"""
service.py

Orchestration of agent calls for the bot: daily session, fresh context
header, conversation history and briefings. Database work is synchronous
(Django ORM) and wrapped with `sync_to_async`; the agent call itself is async.

Continuity rules (CLAUDE_clabpilot.md §5 bis):
- one Claude Code session per day, stored in DayLog.agent_session_id and
  resumed with --resume; a new day starts a new session;
- the morning briefing always starts a fresh session, which then becomes
  the session of the day;
- every message gets a short "fresh context" header, because the database
  may have changed since the last exchange (web, other commands).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from asgiref.sync import sync_to_async
from django.utils import timezone

from core import services
from core.models import Briefing, ConversationTurn, DayLog, Observation, Task
from core.snapshot import WEEKDAYS_FR, build_snapshot

from .backend import AgentBackend, AgentError, AgentErrorKind, AgentResult

logger = logging.getLogger(__name__)

RECENT_CHANGES = 10
HISTORY_ON_NEW_SESSION = 10

BRIEFING_REQUEST = """\
Écris le briefing du matin d'aujourd'hui.
Suis exactement la spec de briefing ci-dessous (sections, ordre, longueur, ton).
Applique les consignes actives qui concernent aujourd'hui. Les données viennent du
snapshot ci-dessous ; utilise les outils de lecture seulement s'il te manque quelque chose.
Réponds uniquement par le texte du briefing, prêt à être posté sur Discord.

=== SPEC DU BRIEFING (version #{spec_id}) ===
{spec}

=== SNAPSHOT ===
{snapshot}
"""

WEEKLY_REQUEST = """\
Fais la revue de la semaine (commande /semaine).
Compare les jours déclarés par projet aux cibles du profil, repère les tâches qui
stagnent, résume ce qui a été fait, puis une ou deux suggestions au plus.

=== SNAPSHOT ===
{snapshot}
"""


@dataclass
class AgentReply:
    """What the bot needs after an agent call."""

    text: str
    result: AgentResult


def _now() -> datetime:
    return timezone.localtime()


def build_fresh_context(since: datetime | None) -> str:
    """Short header prepended to each message: time and recent changes.

    Args:
        since: Time of the previous exchange, or None.

    Returns:
        The header text.
    """
    now = _now()
    lines = [f"[Contexte frais : {WEEKDAYS_FR[now.weekday()]} {now:%d/%m/%Y %H:%M}]"]
    day = DayLog.objects.filter(date=now.date()).first()
    if day and day.declared_focus:
        lines.append(
            f"Journée déclarée : {day.declared_focus}"
            + (f", {day.planned_hours} h" if day.planned_hours is not None else "")
            + (f", lieu {day.location}" if day.location else "")
        )
    else:
        lines.append("Journée pas encore déclarée.")
    if since:
        changed = Task.objects.filter(updated_at__gte=since).order_by("-updated_at")
        count = changed.count()
        if count:
            items = ", ".join(
                f"#{t.pk} {t.title[:40]} [{t.status}]" for t in changed[:RECENT_CHANGES]
            )
            lines.append(f"Tâches modifiées depuis le dernier échange ({count}) : {items}")
        new_obs = Observation.objects.filter(created_at__gte=since)
        if new_obs.exists():
            lines.append(
                "Nouvelles observations : " + "; ".join(f"#{o.pk} {o.text[:60]}" for o in new_obs)
            )
    return "\n".join(lines)


def _today_log() -> DayLog:
    """Return today's DayLog, creating an empty one if needed (stores the session)."""
    log, _ = DayLog.objects.get_or_create(date=services.today())
    return log


def _last_turn_time() -> datetime | None:
    turn = ConversationTurn.objects.first()  # ordered newest first
    return turn.created_at if turn else None


def _history_block() -> str:
    """Recent turns, given to a brand-new session so it keeps the thread."""
    since = timezone.now() - timedelta(hours=24)
    turns = list(ConversationTurn.objects.filter(created_at__gte=since)[:HISTORY_ON_NEW_SESSION])
    if not turns:
        return ""
    lines = ["[Échanges récents, du plus ancien au plus récent]"]
    for turn in reversed(turns):
        who = "Lui" if turn.role == ConversationTurn.Role.USER else "Toi"
        lines.append(f"{who} : {turn.content[:400]}")
    return "\n".join(lines)


def prepare_chat(text: str, channel: str, stored: str | None = None) -> tuple[str, str | None]:
    """Record the user's message and build the full message for the agent.

    The user's text is stored first, so nothing is lost if the agent fails.

    Args:
        text: Text sent to the agent.
        channel: Discord channel name (or command) it comes from.
        stored: Shorter text kept in the history instead of `text` (e.g. "/semaine").

    Returns:
        (message for the agent, session id to resume or None).
    """
    since = _last_turn_time()
    ConversationTurn.objects.create(
        role=ConversationTurn.Role.USER, content=stored or text, channel=channel
    )
    session_id = _today_log().agent_session_id or None
    parts = [build_fresh_context(since)]
    if not session_id:
        history = _history_block()
        if history:
            parts.append(history)
    parts.append(f"[Message de l'utilisateur, salon #{channel}]\n{text}")
    return "\n\n".join(parts), session_id


def finish_chat(result: AgentResult, channel: str) -> None:
    """Store the agent's answer and keep the session of the day."""
    ConversationTurn.objects.create(
        role=ConversationTurn.Role.AGENT, content=result.text, channel=channel
    )
    if result.session_id:
        log = _today_log()
        if log.agent_session_id != result.session_id:
            log.agent_session_id = result.session_id
            log.save(update_fields=["agent_session_id", "updated_at"])


def forget_session() -> None:
    """Drop today's session id (it could not be resumed)."""
    DayLog.objects.filter(date=services.today()).update(agent_session_id="")


def prepare_briefing() -> str:
    """Build the briefing request: active spec + full snapshot."""
    spec = services.get_active_briefing_spec()
    return BRIEFING_REQUEST.format(
        spec_id=spec.pk if spec else "?",
        spec=spec.content if spec else "(aucune spec : fais court et utile)",
        snapshot=build_snapshot(),
    )


def store_briefing(result: AgentResult) -> Briefing:
    """Save the briefing and make its session the session of the day."""
    briefing = Briefing.objects.create(date=services.today(), content=result.text)
    ConversationTurn.objects.create(
        role=ConversationTurn.Role.AGENT, content=result.text, channel="briefing"
    )
    if result.session_id:
        log = _today_log()
        log.agent_session_id = result.session_id
        log.save(update_fields=["agent_session_id", "updated_at"])
    return briefing


class AgentService:
    """High-level agent operations used by the bot.

    Attributes:
        backend: The agent backend (Claude Code).
    """

    def __init__(self, backend: AgentBackend) -> None:
        self.backend = backend

    async def chat(
        self, text: str, channel: str = "agent", stored: str | None = None
    ) -> AgentReply:
        """Send a user message (free text or slash command prompt) to the agent.

        Args:
            text: Text for the agent.
            channel: Where it comes from.
            stored: Shorter version kept in the conversation history.

        Raises:
            AgentError: If the agent call fails (the user's text is already saved).
        """
        message, session_id = await sync_to_async(prepare_chat)(text, channel, stored)
        try:
            result = await self.backend.run(message, session_id)
        except AgentError as exc:
            if exc.kind != AgentErrorKind.SESSION_MISSING or not session_id:
                raise
            logger.warning("Session could not be resumed, starting a new one")
            await sync_to_async(forget_session)()
            result = await self.backend.run(message, None)
        await sync_to_async(finish_chat)(result, channel)
        return AgentReply(text=result.text, result=result)

    async def briefing(self) -> tuple[AgentReply, Briefing]:
        """Write the morning briefing in a fresh session and store it."""
        message = await sync_to_async(prepare_briefing)()
        result = await self.backend.run(message, None)
        briefing = await sync_to_async(store_briefing)(result)
        return AgentReply(text=result.text, result=result), briefing

    async def weekly_review(self) -> AgentReply:
        """Weekly review, inside the session of the day."""
        snapshot = await sync_to_async(build_snapshot)()
        return await self.chat(
            WEEKLY_REQUEST.format(snapshot=snapshot), channel="agent", stored="/semaine"
        )
