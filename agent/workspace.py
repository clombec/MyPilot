"""
workspace.py

Preparation of the agent's working directory.

The versioned source is `agent_workspace/CLAUDE.md` in this repository. At
runtime it is copied to AGENT_WORKDIR, a dedicated folder OUTSIDE the
repository: Claude Code reads every CLAUDE.md of the parent folders, and the
development rules of this repository must not reach the agent.
The MCP declaration (`mcp_config.json`) is generated next to it, because it
contains machine-specific absolute paths.
"""

import json
import logging
import shutil
import sys
from pathlib import Path

from django.conf import settings

from .backend import DEFAULT_WORKDIR

logger = logging.getLogger(__name__)

SOURCE_DIR = settings.BASE_DIR / "agent_workspace"
PROMPT_FILE = "CLAUDE.md"
MCP_CONFIG_FILE = "mcp_config.json"
MCP_SERVER_SCRIPT = settings.BASE_DIR / "agent" / "mcp_server.py"


class WorkspaceError(Exception):
    """The agent workspace cannot be prepared safely."""


def build_mcp_config() -> dict:
    """Return the MCP declaration of the `pilotage` server for this machine."""
    return {
        "mcpServers": {
            "pilotage": {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(MCP_SERVER_SCRIPT)],
                "env": {"DJANGO_SETTINGS_MODULE": "config.settings"},
            }
        }
    }


def prepare_workspace(workdir: Path) -> Path:
    """Copy the agent prompt and write the MCP configuration into `workdir`.

    Args:
        workdir: Target folder (created if missing). Must be outside the repository.

    Returns:
        Path of the generated `mcp_config.json`.

    Raises:
        WorkspaceError: If `workdir` is inside the repository.
        OSError: If the files cannot be written.
    """
    workdir = workdir.expanduser().resolve()
    repo = settings.BASE_DIR.resolve()
    if workdir == repo or repo in workdir.parents:
        raise WorkspaceError(
            f"AGENT_WORKDIR ({workdir}) is inside the repository: the development "
            "CLAUDE.md would be loaded by the agent. Choose a folder outside it."
        )
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE_DIR / PROMPT_FILE, workdir / PROMPT_FILE)
    config_path = workdir / MCP_CONFIG_FILE
    config_path.write_text(json.dumps(build_mcp_config(), indent=2), encoding="utf-8")
    logger.info("Agent workspace ready: %s", workdir)
    return config_path


def resolve_workspace(workdir: Path) -> tuple[Path, Path]:
    """Prepare the workspace, falling back to the default folder if needed.

    `.env.example` once suggested `./agent_workspace` (inside the repository):
    such a value is replaced by DEFAULT_WORKDIR, with a warning.

    Args:
        workdir: Folder configured by AGENT_WORKDIR.

    Returns:
        (folder actually used, path of mcp_config.json).

    Raises:
        OSError: If the files cannot be written.
    """
    try:
        return workdir, prepare_workspace(workdir)
    except WorkspaceError as exc:
        logger.warning("%s Using %s instead.", exc, DEFAULT_WORKDIR)
        return DEFAULT_WORKDIR, prepare_workspace(DEFAULT_WORKDIR)
