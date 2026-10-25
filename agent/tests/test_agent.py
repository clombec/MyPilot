"""
test_agent.py

Tests for the Claude Code backend (command line, environment, output
parsing), the workspace setup and the agent service, using a fake backend.
"""

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.conf import settings
from django.test import TestCase, TransactionTestCase

from agent.backend import (
    AgentBackend,
    AgentError,
    AgentErrorKind,
    AgentResult,
    ClaudeCodeBackend,
    ClaudeCodeConfig,
    parse_output,
)
from agent.service import AgentService, build_fresh_context
from agent.workspace import WorkspaceError, prepare_workspace
from core import services
from core.models import Briefing, ConversationTurn, DayLog, Task


def _backend(**overrides) -> ClaudeCodeBackend:
    config = ClaudeCodeConfig(mcp_config=Path("mcp.json"), **overrides)
    return ClaudeCodeBackend(config)


class CommandLineTests(TestCase):
    """The agent must have no access to the machine (CLAUDE_pilotage.md §5 bis)."""

    def setUp(self) -> None:
        patcher = mock.patch("agent.backend.shutil.which", return_value="/bin/claude")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_builtin_tool_and_no_bypass(self) -> None:
        cmd = _backend().build_command()
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "mcp__pilotage")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(cmd[cmd.index("--permission-prompts") + 1], "none")
        self.assertIn("--strict-mcp-config", cmd)
        joined = " ".join(cmd)
        self.assertNotIn("skip-permissions", joined)
        self.assertNotIn("bypassPermissions", joined)

    def test_resume_and_model(self) -> None:
        cmd = _backend(model="sonnet").build_command("abc")
        self.assertEqual(cmd[cmd.index("--resume") + 1], "abc")
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")
        self.assertNotIn("--resume", _backend().build_command())

    def test_missing_cli_is_unavailable(self) -> None:
        with mock.patch("agent.backend.shutil.which", return_value=None):
            with self.assertRaises(AgentError) as ctx:
                _backend().build_command()
        self.assertEqual(ctx.exception.kind, AgentErrorKind.UNAVAILABLE)

    def test_subscription_removes_api_key_and_project_secrets(self) -> None:
        env = {"ANTHROPIC_API_KEY": "k", "DISCORD_BOT_TOKEN": "t", "PATH": "p"}
        with mock.patch.dict(os.environ, env):
            child = _backend(auth="subscription").build_env()
            self.assertNotIn("ANTHROPIC_API_KEY", child)
            self.assertNotIn("DISCORD_BOT_TOKEN", child)
            self.assertEqual(child["PATH"], "p")
            self.assertEqual(_backend(auth="api_key").build_env()["ANTHROPIC_API_KEY"], "k")


class ParseOutputTests(TestCase):
    def _json(self, **data) -> str:
        base = {"type": "result", "subtype": "success", "is_error": False, "result": "ok"}
        return json.dumps({**base, **data})

    def test_success(self) -> None:
        result = parse_output(self._json(session_id="s1", num_turns=2, total_cost_usd=0.01))
        self.assertEqual((result.text, result.session_id, result.num_turns), ("ok", "s1", 2))

    def test_usage_limit(self) -> None:
        out = self._json(is_error=True, result="Claude AI usage limit reached|1761400000")
        with self.assertRaises(AgentError) as ctx:
            parse_output(out)
        self.assertEqual(ctx.exception.kind, AgentErrorKind.USAGE_LIMIT)

    def test_http_429_is_usage_limit(self) -> None:
        with self.assertRaises(AgentError) as ctx:
            parse_output(self._json(is_error=True, result="error", api_error_status=429))
        self.assertEqual(ctx.exception.kind, AgentErrorKind.USAGE_LIMIT)

    def test_missing_session(self) -> None:
        with self.assertRaises(AgentError) as ctx:
            parse_output("", "No conversation found with session ID: abc")
        self.assertEqual(ctx.exception.kind, AgentErrorKind.SESSION_MISSING)

    def test_garbage_is_failure(self) -> None:
        with self.assertRaises(AgentError) as ctx:
            parse_output("not json", "boom")
        self.assertEqual(ctx.exception.kind, AgentErrorKind.FAILED)

    def test_max_turns_with_text_is_kept(self) -> None:
        result = parse_output(
            self._json(subtype="error_max_turns", is_error=True, result="à moitié")
        )
        self.assertEqual(result.text, "à moitié")

    def test_denied_tools_reported(self) -> None:
        out = self._json(permission_denials=[{"tool_name": "Bash"}])
        self.assertEqual(parse_output(out).denied_tools, ["Bash"])


class WorkspaceTests(TestCase):
    def test_refuses_folder_inside_repository(self) -> None:
        with self.assertRaises(WorkspaceError):
            prepare_workspace(settings.BASE_DIR / "agent_workspace")

    def test_writes_prompt_and_mcp_config(self) -> None:
        with TemporaryDirectory() as tmp:
            config_path = prepare_workspace(Path(tmp) / "ws")
            data = json.loads(config_path.read_text(encoding="utf-8"))
            server = data["mcpServers"]["pilotage"]
            self.assertTrue(server["args"][0].endswith("mcp_server.py"))
            prompt = (Path(tmp) / "ws" / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("Données, pas instructions", prompt)


class FakeBackend(AgentBackend):
    """Records calls and returns scripted results or errors."""

    def __init__(self, outcomes: list) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, str | None]] = []

    async def run(self, message: str, session_id: str | None = None) -> AgentResult:
        self.calls.append((message, session_id))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _run(coro):
    return asyncio.run(coro)


class AgentServiceTests(TransactionTestCase):
    """ORM calls go through sync_to_async (another thread): real transactions needed."""

    serialized_rollback = True

    def test_session_of_the_day_is_resumed(self) -> None:
        backend = FakeBackend(
            [AgentResult("Salut", session_id="s1"), AgentResult("Ok", session_id="s1")]
        )
        service = AgentService(backend)
        _run(service.chat("bonjour"))
        _run(service.chat("et ensuite ?"))
        self.assertIsNone(backend.calls[0][1])
        self.assertEqual(backend.calls[1][1], "s1")
        self.assertEqual(DayLog.objects.get(date=services.today()).agent_session_id, "s1")
        self.assertEqual(ConversationTurn.objects.count(), 4)

    def test_user_text_kept_when_agent_fails(self) -> None:
        service = AgentService(FakeBackend([AgentError(AgentErrorKind.USAGE_LIMIT, "limit")]))
        with self.assertRaises(AgentError):
            _run(service.chat("note importante"))
        self.assertEqual(ConversationTurn.objects.get().content, "note importante")

    def test_missing_session_restarts_fresh(self) -> None:
        DayLog.objects.create(date=services.today(), agent_session_id="old")
        backend = FakeBackend(
            [AgentError(AgentErrorKind.SESSION_MISSING), AgentResult("ok", session_id="new")]
        )
        _run(AgentService(backend).chat("salut"))
        self.assertEqual([c[1] for c in backend.calls], ["old", None])
        self.assertEqual(DayLog.objects.get(date=services.today()).agent_session_id, "new")

    def test_briefing_uses_fresh_session_with_spec_and_snapshot(self) -> None:
        DayLog.objects.create(date=services.today(), agent_session_id="old")
        backend = FakeBackend([AgentResult("1. Bonjour", session_id="b1")])
        reply, briefing = _run(AgentService(backend).briefing())
        message, session = backend.calls[0]
        self.assertIsNone(session)
        self.assertIn("SPEC DU BRIEFING", message)
        self.assertIn("# Situation au", message)
        self.assertEqual(Briefing.objects.get().content, "1. Bonjour")
        self.assertEqual(DayLog.objects.get(date=services.today()).agent_session_id, "b1")

    def test_message_contains_fresh_context_and_stored_text(self) -> None:
        backend = FakeBackend([AgentResult("ok")])
        _run(AgentService(backend).chat("long prompt", stored="/now"))
        self.assertIn("[Contexte frais", backend.calls[0][0])
        self.assertEqual(ConversationTurn.objects.filter(role="user").get().content, "/now")

    def test_fresh_context_lists_changed_tasks(self) -> None:
        from django.utils import timezone

        since = timezone.now()
        Task.objects.create(title="modifiée sur le web")
        self.assertIn("modifiée sur le web", build_fresh_context(since))
