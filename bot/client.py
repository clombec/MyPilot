"""
client.py

Discord layer: slash commands, channel listeners, proposal buttons and the
daily scheduler (briefing, nudge, nightly export). Logic lives in
bot/handlers.py (no Claude) and agent/service.py (Claude); this module only
routes Discord events to them.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands, tasks
from django.conf import settings

from agent.backend import AgentError, AgentErrorKind
from agent.service import AgentReply, AgentService
from core.backup import export_all

from . import handlers
from .config import BotConfig
from .text import AGENT_ERROR_MESSAGES, split_message

logger = logging.getLogger(__name__)

CUSTOM_ID_PREFIX = "pilotage"
CATCH_UP_UNTIL_HOUR = 12


def _error_text(exc: AgentError) -> str:
    """French message for an agent failure."""
    return AGENT_ERROR_MESSAGES.get(exc.kind, AGENT_ERROR_MESSAGES[AgentErrorKind.FAILED])


class ProposalView(discord.ui.View):
    """Accept / Refuse buttons. Clicks are handled in `on_interaction`, so the
    buttons keep working after a restart."""

    def __init__(self, proposal_id: int) -> None:
        super().__init__(timeout=None)
        for action, label, style in (
            ("accept", "Accepter", discord.ButtonStyle.success),
            ("reject", "Refuser", discord.ButtonStyle.danger),
        ):
            self.add_item(
                discord.ui.Button(
                    label=label,
                    style=style,
                    custom_id=f"{CUSTOM_ID_PREFIX}:proposal:{proposal_id}:{action}",
                )
            )


class SpecRestoreView(discord.ui.View):
    """Menu to restore an older briefing format."""

    def __init__(self, versions: list[tuple[int, str]]) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Select(
                custom_id=f"{CUSTOM_ID_PREFIX}:spec:restore",
                placeholder="Revenir à une version…",
                options=[
                    discord.SelectOption(label=label, value=str(pk)) for pk, label in versions
                ],
            )
        )


class PilotBot(commands.Bot):
    """The Pilotage Discord bot.

    Attributes:
        config: Discord identifiers and schedule.
        agent: Agent service (Claude Code).
    """

    def __init__(self, config: BotConfig, agent: AgentService) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.config = config
        self.agent = agent
        self._caught_up = False
        self._loops: list[tasks.Loop] = []

    # --- Lifecycle -----------------------------------------------------------

    async def setup_hook(self) -> None:
        """Register slash commands on the guild and start the scheduler."""
        register_commands(self)
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info("Slash commands synced: %d", len(synced))
        self.tree.interaction_check = self._interaction_check  # type: ignore[method-assign]

        self._loops = [
            tasks.loop(time=self.config.briefing_time)(self._scheduled_briefing),
            tasks.loop(time=self.config.nudge_time)(self._scheduled_nudge),
            tasks.loop(time=self.config.export_time)(self._scheduled_export),
        ]
        for loop in self._loops:
            loop.before_loop(self.wait_until_ready)
            loop.start()

    async def on_ready(self) -> None:
        """Log in, then send a missed briefing if the PC was off at briefing time."""
        logger.info("Bot connected as %s", self.user)
        if self._caught_up:
            return
        self._caught_up = True
        now = datetime.now(self.config.timezone)
        if self.config.briefing_time.replace(tzinfo=None) < now.time() and (
            now.hour < CATCH_UP_UNTIL_HOUR
        ):
            if not await sync_to_async(handlers.briefing_sent_today)():
                logger.info("Missed briefing: sending it now")
                await self.send_briefing()

    async def _interaction_check(self, interaction: discord.Interaction) -> bool:
        """Slash commands: owner only, configured channels only."""
        if handlers.is_authorized(self.config, interaction.user.id, interaction.channel_id):
            return True
        logger.warning("Refused interaction from user %s", interaction.user.id)
        await interaction.response.send_message("Accès refusé.", ephemeral=True)
        return False

    # --- Channels ------------------------------------------------------------

    def channel(self, channel_id: int) -> discord.abc.Messageable | None:
        """Return a configured channel, or None if unreachable."""
        found = self.get_channel(channel_id)
        if found is None:
            logger.error("Channel %s not found (bot invited? id correct?)", channel_id)
        return found  # type: ignore[return-value]

    async def send_chunks(self, target: discord.abc.Messageable, text: str) -> discord.Message:
        """Send a long text in several messages; return the first one."""
        first = None
        for chunk in split_message(text) or ["(réponse vide)"]:
            message = await target.send(chunk)
            first = first or message
        return first  # type: ignore[return-value]

    async def on_message(self, message: discord.Message) -> None:
        """#capture → raw task; #agent and #briefing → agent. Everything else ignored.

        Messages from an allowed webhook in #agent also go to the agent.
        """
        if handlers.is_trusted_webhook(self.config, message.webhook_id, message.channel.id):
            content = message.content.strip()
            if not content:
                logger.info("Ignored webhook %s: message has no text", message.webhook_id)
                return
            logger.info("Webhook message received from %s", message.webhook_id)
            prompt = handlers.webhook_prompt(message.author.display_name, content)
            await self.ask_agent(message.channel, lambda: self.agent.chat(prompt, channel="agent"))
            return
        if message.webhook_id is not None:
            logger.info(
                "Ignored webhook %s in channel %s (allowed: %s in channel %s)",
                message.webhook_id,
                message.channel.id,
                sorted(self.config.agent_webhook_ids) or "none",
                self.config.channel_agent,
            )
            return
        if message.author.bot:
            return
        if not handlers.is_authorized(self.config, message.author.id, message.channel.id):
            logger.debug("Ignored message from %s in %s", message.author.id, message.channel.id)
            return
        content = message.content.strip()
        if not content:
            return
        if message.channel.id == self.config.channel_capture:
            reply = await sync_to_async(handlers.capture)(content)
            await message.add_reaction("✅" if reply.startswith("✅") else "⚠️")
            await message.reply(reply, mention_author=False)
        elif message.channel.id in (self.config.channel_agent, self.config.channel_briefing):
            await self.ask_agent(
                message.channel, lambda: self.agent.chat(content, channel=message.channel.name)
            )

    # --- Agent calls ---------------------------------------------------------

    async def run_agent(self, call: Callable[[], Awaitable[AgentReply]]) -> tuple[str, bool]:
        """Run an agent call; return (text to show, success)."""
        try:
            reply = await call()
        except AgentError as exc:
            logger.error("Agent call failed: %s", exc.kind.value)
            return _error_text(exc), False
        finally:
            await self.post_pending_proposals()
        return reply.text or "(pas de réponse)", True

    async def ask_agent(
        self, target: discord.abc.Messageable, call: Callable[[], Awaitable[AgentReply]]
    ) -> None:
        """Agent call from a channel message, with the typing indicator."""
        async with target.typing():
            text, _ = await self.run_agent(call)
        await self.send_chunks(target, text)

    async def answer_interaction(
        self, interaction: discord.Interaction, call: Callable[[], Awaitable[AgentReply]]
    ) -> None:
        """Agent call from a slash command (deferred, then follow-ups)."""
        await interaction.response.defer(thinking=True)
        text, _ = await self.run_agent(call)
        for chunk in split_message(text) or ["(réponse vide)"]:
            await interaction.followup.send(chunk)

    async def send_briefing(self) -> bool:
        """Write the briefing with the agent and post it in #briefing."""
        target = self.channel(self.config.channel_briefing)
        if target is None:
            return False
        async with target.typing():
            try:
                reply, briefing = await self.agent.briefing()
            except AgentError as exc:
                logger.error("Briefing failed: %s", exc.kind.value)
                await target.send("Briefing impossible ce matin. " + _error_text(exc))
                return False
            finally:
                await self.post_pending_proposals()
        first = await self.send_chunks(target, reply.text)
        await sync_to_async(handlers.mark_briefing_sent)(briefing.pk, first.id)
        return True

    # --- Proposals -----------------------------------------------------------

    async def post_pending_proposals(self) -> None:
        """Post every new proposal in #propositions with its buttons."""
        target = self.channel(self.config.channel_proposals)
        if target is None:
            return
        for proposal_id in await sync_to_async(handlers.pending_unposted_proposals)():
            text = await sync_to_async(handlers.proposal_text)(proposal_id)
            try:
                message = await target.send(text, view=ProposalView(proposal_id))
            except discord.HTTPException:
                logger.exception("Could not post proposal %s", proposal_id)
                continue
            await sync_to_async(handlers.mark_proposal_posted)(proposal_id, message.id)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Handle proposal buttons and the briefing-format menu (survive restarts)."""
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith(f"{CUSTOM_ID_PREFIX}:"):
            return
        if not handlers.is_authorized(self.config, interaction.user.id, interaction.channel_id):
            await interaction.response.send_message("Accès refusé.", ephemeral=True)
            return
        parts = custom_id.split(":")
        if parts[1] == "proposal" and len(parts) == 4:
            verdict = await sync_to_async(handlers.decide)(int(parts[2]), parts[3] == "accept")
            original = interaction.message.content if interaction.message else ""
            await interaction.response.edit_message(
                content=f"{original}\n\n**{verdict}**"[:2000], view=None
            )
        elif parts[1] == "spec" and parts[2] == "restore":
            version_id = int((interaction.data or {}).get("values", ["0"])[0])
            text = await sync_to_async(handlers.restore_briefing_version)(version_id)
            await interaction.response.edit_message(content=text, view=None)

    # --- Scheduler -----------------------------------------------------------

    async def _scheduled_briefing(self) -> None:
        if await sync_to_async(handlers.briefing_sent_today)():
            logger.info("Briefing already sent today, skipping")
            return
        await self.send_briefing()

    async def _scheduled_nudge(self) -> None:
        if not await sync_to_async(handlers.should_nudge)():
            return
        target = self.channel(self.config.channel_agent)
        if target is not None:
            await target.send(handlers.NUDGE_TEXT)

    async def _scheduled_export(self) -> None:
        directories = settings.EXPORT_DIRS or [settings.BASE_DIR / "data" / "exports"]
        try:
            await sync_to_async(export_all)(directories, settings.EXPORT_KEEP_JSON)
        except OSError:
            logger.exception("Nightly export failed")


def register_commands(bot: PilotBot) -> None:
    """Declare the slash commands (French names and descriptions)."""
    tree = bot.tree

    @tree.command(name="note", description="Ajoute une observation (ex. oublié mes clés)")
    @app_commands.describe(texte="Ce qui s'est passé")
    async def note_cmd(interaction: discord.Interaction, texte: str) -> None:
        await interaction.response.send_message(await sync_to_async(handlers.note)(texte))

    @tree.command(name="capture", description="Capture une tâche brute")
    @app_commands.describe(texte="La tâche, telle quelle", projet="Slug du projet (optionnel)")
    async def capture_cmd(
        interaction: discord.Interaction, texte: str, projet: str | None = None
    ) -> None:
        reply = await sync_to_async(handlers.capture)(texte, projet)
        await interaction.response.send_message(reply)

    @tree.command(name="statut", description="Compteurs et prochaines échéances (sans Claude)")
    async def status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await sync_to_async(handlers.status)())

    @tree.command(name="annuler", description="Annule la dernière écriture de l'agent")
    async def undo_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(await sync_to_async(handlers.undo)())

    @tree.command(name="now", description="J'ai du temps : propose 2 ou 3 options")
    @app_commands.describe(duree="Ex. 1h, 30 min", contexte="Lieu, outil…")
    async def now_cmd(
        interaction: discord.Interaction,
        duree: str,
        contexte: str | None = None,
    ) -> None:
        ctx = f", contexte : {contexte}" if contexte else ""
        prompt = (
            f"/now : j'ai {duree} devant moi{ctx}. "
            "Propose 2 ou 3 options, chacune avec une phrase qui explique pourquoi."
        )
        await bot.answer_interaction(
            interaction, lambda: bot.agent.chat(prompt, channel="agent", stored=prompt)
        )

    @tree.command(name="jour", description="Déclare ta journée, l'agent propose un plan")
    @app_commands.describe(focus="Projet(s) du jour, lieu…", heures="Heures dispo")
    async def day_cmd(
        interaction: discord.Interaction,
        focus: str,
        heures: float | None = None,
    ) -> None:
        recorded = await sync_to_async(handlers.declare_day)(focus, heures)
        prompt = (
            f"/jour : {recorded}. Rappelle les consignes qui s'appliquent aujourd'hui, puis "
            "propose un plan du jour en options (pas un ordre)."
        )
        await bot.answer_interaction(
            interaction, lambda: bot.agent.chat(prompt, channel="agent", stored=prompt)
        )

    @tree.command(name="fait", description="Clôt une tâche (numéro ou description)")
    @app_commands.describe(tache="Numéro (#12) ou quelques mots")
    async def done_cmd(interaction: discord.Interaction, tache: str) -> None:
        ref = tache.strip().lstrip("#")
        if ref.isdigit():
            await interaction.response.send_message(
                await sync_to_async(handlers.complete_by_id)(int(ref))
            )
            return
        prompt = (
            f"/fait : j'ai terminé « {tache} ». Retrouve la tâche correspondante et "
            "marque-la faite. Si plusieurs correspondent, demande laquelle."
        )
        await bot.answer_interaction(
            interaction, lambda: bot.agent.chat(prompt, channel="agent", stored=prompt)
        )

    @tree.command(name="clarifie", description="L'agent prend des éléments bruts et questionne")
    @app_commands.describe(nombre="Nombre d'éléments (3 par défaut)")
    async def clarify_cmd(
        interaction: discord.Interaction, nombre: app_commands.Range[int, 1, 5] = 3
    ) -> None:
        prompt = (
            f"/clarifie : prends {nombre} élément(s) brut(s) ou partiel(s) à clarifier "
            "(projet prioritaire, échéance proche, déjà évoqué) et pose tes questions, "
            "3 questions au plus au total."
        )
        await bot.answer_interaction(
            interaction, lambda: bot.agent.chat(prompt, channel="agent", stored=prompt)
        )

    @tree.command(name="semaine", description="Revue de la semaine")
    async def week_cmd(interaction: discord.Interaction) -> None:
        await bot.answer_interaction(interaction, bot.agent.weekly_review)

    @tree.command(name="briefing", description="Regénère le briefing du jour dans #briefing")
    async def briefing_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        ok = await bot.send_briefing()
        await interaction.followup.send(
            "Briefing posté dans #briefing." if ok else "Briefing impossible, voir #briefing.",
            ephemeral=True,
        )

    @tree.command(name="briefing-format", description="Format actuel du briefing et versions")
    async def spec_cmd(interaction: discord.Interaction) -> None:
        overview = await sync_to_async(handlers.briefing_format_overview)()
        view = SpecRestoreView(overview.versions) if overview.versions else discord.utils.MISSING
        await interaction.response.send_message(overview.text[:2000], view=view)
