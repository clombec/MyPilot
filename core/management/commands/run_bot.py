"""
run_bot.py

`manage.py run_bot`: starts the Discord bot, its scheduler and the agent
backend (Claude Code). Prepares the agent workspace first.
"""

import logging

from django.core.management.base import BaseCommand, CommandError

from agent.backend import ClaudeCodeBackend, ClaudeCodeConfig
from agent.service import AgentService
from agent.workspace import resolve_workspace
from bot.client import PilotBot
from bot.config import BotConfig, ConfigError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Run the Discord bot (long-running process)."""

    help = "Lance le bot Discord, le planificateur et l'agent Claude Code."

    def handle(self, *args, **options) -> None:
        try:
            config = BotConfig.from_env()
        except ConfigError as exc:
            raise CommandError(f"Configuration Discord incomplète : {exc}") from exc

        agent_config = ClaudeCodeConfig.from_env()
        try:
            agent_config.workdir, agent_config.mcp_config = resolve_workspace(agent_config.workdir)
        except OSError as exc:
            raise CommandError(f"Dossier de travail de l'agent invalide : {exc}") from exc

        logger.info(
            "Starting bot (agent auth: %s, model: %s, workdir: %s)",
            agent_config.auth,
            agent_config.model or "default",
            agent_config.workdir,
        )
        bot = PilotBot(config, AgentService(ClaudeCodeBackend(agent_config)))
        bot.run(config.token, log_handler=None)
