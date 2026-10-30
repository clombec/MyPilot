"""
agent_check.py

`manage.py agent_check`: one real call to the agent to check the whole chain
(claude CLI, login, MCP server `clabpilot`, database). Read-only.
"""

import asyncio
import logging

from django.core.management.base import BaseCommand, CommandError

from agent.backend import AgentError, ClaudeCodeBackend, ClaudeCodeConfig
from agent.workspace import resolve_workspace

logger = logging.getLogger(__name__)

CHECK_PROMPT = (
    "Test technique : appelle l'outil list_projects, puis réponds en une ligne avec le "
    "nombre de projets et leurs slugs. N'écris rien en base."
)


class Command(BaseCommand):
    """Check that the agent can be called and reaches its tools."""

    help = "Vérifie la chaîne complète de l'agent (Claude Code + outils clabpilot), sans écrire."

    def handle(self, *args, **options) -> None:
        config = ClaudeCodeConfig.from_env()
        config.workdir, config.mcp_config = resolve_workspace(config.workdir)
        backend = ClaudeCodeBackend(config)
        self.stdout.write(f"Dossier de l'agent : {config.workdir}")
        self.stdout.write(f"Commande : {' '.join(backend.build_command())}")
        try:
            result = asyncio.run(backend.run(CHECK_PROMPT))
        except AgentError as exc:
            raise CommandError(
                f"Échec de l'appel à l'agent ({exc.kind.value}) : {exc.detail}"
            ) from exc
        self.stdout.write(self.style.SUCCESS(f"Réponse : {result.text}"))
        self.stdout.write(
            f"{result.num_turns} tours, {result.duration_ms} ms, outils refusés : "
            f"{result.denied_tools or 'aucun'}"
        )
