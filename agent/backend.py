"""
backend.py

Agent backends. The bot talks to an `AgentBackend`; the only implementation
for now is `ClaudeCodeBackend`, which runs the local `claude` CLI in
non-interactive mode (CLAUDE_pilotage.md §5 bis).

Safety model: the agent gets no built-in tool at all (`--tools ""`), only the
`pilotage` MCP tools, and anything not pre-approved is denied
(`--permission-mode dontAsk`, `--permission-prompts none`). Permission
bypass modes are never used.
"""

import asyncio
import json
import logging
import os
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

MCP_TOOL_PATTERN = "mcp__pilotage"
DEFAULT_WORKDIR = Path.home() / ".pilotage" / "agent_workspace"
USAGE_LIMIT_MARKERS = ("usage limit", "rate limit", "limit reached", "limit will reset")
SESSION_MISSING_MARKERS = ("no conversation found", "session not found")
KILL_GRACE_SECONDS = 5


class AgentErrorKind(Enum):
    """Why an agent call failed."""

    TIMEOUT = "timeout"
    USAGE_LIMIT = "usage_limit"
    UNAVAILABLE = "unavailable"
    SESSION_MISSING = "session_missing"
    FAILED = "failed"


class AgentError(Exception):
    """An agent call failed. `kind` drives the message shown to the user."""

    def __init__(self, kind: AgentErrorKind, detail: str = "") -> None:
        super().__init__(f"{kind.value}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class AgentResult:
    """Outcome of one agent call."""

    text: str
    session_id: str | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None
    denied_tools: list[str] = field(default_factory=list)


class AgentBackend(ABC):
    """Interface used by the bot. Implementations must be safe to call concurrently
    (they queue calls internally)."""

    @abstractmethod
    async def run(self, message: str, session_id: str | None = None) -> AgentResult:
        """Send a message to the agent, optionally resuming a session.

        Args:
            message: Full message (context + user text).
            session_id: Session to resume, or None for a fresh session.

        Returns:
            The agent's answer.

        Raises:
            AgentError: On timeout, usage limit, missing CLI or any failure.
        """


@dataclass
class ClaudeCodeConfig:
    """Settings of the Claude Code backend (from the environment)."""

    claude_bin: str = "claude"
    workdir: Path = DEFAULT_WORKDIR
    mcp_config: Path | None = None
    model: str = ""
    max_turns: int = 8
    timeout: float = 120.0
    auth: str = "subscription"  # subscription | api_key

    @classmethod
    def from_env(cls, mcp_config: Path | None = None) -> "ClaudeCodeConfig":
        """Build the configuration from environment variables.

        Args:
            mcp_config: Path of the generated MCP configuration file.

        Returns:
            A ClaudeCodeConfig.
        """
        env = os.environ
        workdir = env.get("AGENT_WORKDIR", "").strip()
        return cls(
            claude_bin=env.get("CLAUDE_BIN", "").strip() or "claude",
            workdir=Path(workdir).expanduser() if workdir else DEFAULT_WORKDIR,
            mcp_config=mcp_config,
            model=env.get("AGENT_MODEL", "").strip(),
            max_turns=int(env.get("AGENT_MAX_TURNS", "").strip() or 8),
            timeout=float(env.get("AGENT_TIMEOUT", "").strip() or 120),
            auth=env.get("AGENT_AUTH", "").strip() or "subscription",
        )


class ClaudeCodeBackend(AgentBackend):
    """Runs `claude -p` locally, one process at a time.

    Attributes:
        config: Backend settings.
    """

    def __init__(self, config: ClaudeCodeConfig) -> None:
        self.config = config
        self._lock = asyncio.Lock()

    def build_command(self, session_id: str | None = None) -> list[str]:
        """Return the CLI arguments. The message itself is sent on stdin.

        Args:
            session_id: Session to resume, if any.

        Returns:
            The argument list, starting with the resolved `claude` executable.

        Raises:
            AgentError: If the executable or the MCP configuration is missing.
        """
        exe = shutil.which(self.config.claude_bin)
        if not exe:
            raise AgentError(AgentErrorKind.UNAVAILABLE, f"{self.config.claude_bin} not found")
        if not self.config.mcp_config:
            raise AgentError(AgentErrorKind.UNAVAILABLE, "MCP configuration missing")
        cmd = [
            exe,
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--mcp-config",
            str(self.config.mcp_config),
            "--strict-mcp-config",
            "--allowedTools",
            MCP_TOOL_PATTERN,
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--setting-sources",
            "project",
            "--disable-slash-commands",
            "--no-chrome",
            "--max-turns",
            str(self.config.max_turns),
        ]
        if self.config.model:
            cmd += ["--model", self.config.model]
        if session_id:
            cmd += ["--resume", session_id]
        return cmd

    def build_env(self) -> dict[str, str]:
        """Return the child process environment.

        With `AGENT_AUTH=subscription`, ANTHROPIC_API_KEY is removed so the
        subscription is used (otherwise the key would be billed per use).
        """
        env = dict(os.environ)
        if self.config.auth != "api_key":
            env.pop("ANTHROPIC_API_KEY", None)
        # Secrets of this project are useless to the agent: never pass them on.
        for name in ("DISCORD_BOT_TOKEN", "API_TOKEN", "DJANGO_SECRET_KEY"):
            env.pop(name, None)
        return env

    async def run(self, message: str, session_id: str | None = None) -> AgentResult:
        """Run the agent, with one retry on transient failures.

        A missing session is not retried here: the caller decides to start a
        fresh one (AgentErrorKind.SESSION_MISSING).
        """
        async with self._lock:
            try:
                return await self._run_once(message, session_id)
            except AgentError as exc:
                if exc.kind not in (AgentErrorKind.FAILED, AgentErrorKind.TIMEOUT):
                    raise
                logger.warning("Agent call failed (%s), retrying once", exc.kind.value)
                return await self._run_once(message, session_id)

    async def _run_once(self, message: str, session_id: str | None) -> AgentResult:
        """Start one `claude` process and parse its JSON output."""
        cmd = self.build_command(session_id)
        self.config.workdir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.config.workdir,
                env=self.build_env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise AgentError(AgentErrorKind.UNAVAILABLE, str(exc)) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(message.encode("utf-8")), timeout=self.config.timeout
            )
        except TimeoutError as exc:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
            except TimeoutError:
                logger.error("claude process did not exit after kill")
            raise AgentError(AgentErrorKind.TIMEOUT, f"> {self.config.timeout:.0f} s") from exc

        elapsed = int((time.monotonic() - started) * 1000)
        result = parse_output(stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"))
        logger.info(
            "Agent call: %d ms, %s turns, cost %s, denied %s",
            result.duration_ms or elapsed,
            result.num_turns,
            result.cost_usd,
            result.denied_tools or "none",
        )
        return result


def _classify(text: str) -> AgentErrorKind:
    """Map an error text to an error kind."""
    low = text.lower()
    if any(m in low for m in SESSION_MISSING_MARKERS):
        return AgentErrorKind.SESSION_MISSING
    if any(m in low for m in USAGE_LIMIT_MARKERS):
        return AgentErrorKind.USAGE_LIMIT
    return AgentErrorKind.FAILED


def parse_output(stdout: str, stderr: str = "") -> AgentResult:
    """Parse the JSON printed by `claude -p --output-format json`.

    Args:
        stdout: Standard output of the process.
        stderr: Standard error of the process (used for diagnostics only).

    Returns:
        The parsed AgentResult.

    Raises:
        AgentError: If the output is not valid JSON or reports an error.
    """
    try:
        data = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else None
    except (json.JSONDecodeError, IndexError):
        data = None
    if not isinstance(data, dict):
        detail = (stderr or stdout).strip()[-500:]
        raise AgentError(_classify(detail), detail or "empty output")

    text = str(data.get("result") or "").strip()
    if data.get("is_error") or data.get("subtype") not in (None, "success"):
        detail = text or str(data.get("subtype")) or stderr.strip()[-500:]
        kind = _classify(f"{detail} {stderr}")
        if data.get("api_error_status") == 429:
            kind = AgentErrorKind.USAGE_LIMIT
        if data.get("subtype") == "error_max_turns" and text:
            # Out of turns but an answer exists: keep it.
            kind = None
        if kind is not None:
            raise AgentError(kind, detail[:500])

    denied = [d.get("tool_name", "?") for d in data.get("permission_denials") or []]
    return AgentResult(
        text=text,
        session_id=data.get("session_id"),
        num_turns=data.get("num_turns"),
        duration_ms=data.get("duration_ms"),
        cost_usd=data.get("total_cost_usd"),
        denied_tools=denied,
    )
