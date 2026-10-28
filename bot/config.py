"""
config.py

Bot settings, read from the environment (`.env`).
"""

import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo


class ConfigError(Exception):
    """A required bot setting is missing or invalid."""


def _int(name: str) -> int:
    """Read a required integer (Discord id) from the environment."""
    raw = os.environ.get(name, "").strip()
    if not raw.isdigit():
        raise ConfigError(f"{name} must be set to a numeric Discord id")
    return int(raw)


def _int_set(name: str) -> frozenset[int]:
    """Read an optional comma-separated list of Discord ids from the environment."""
    raw = os.environ.get(name, "").strip()
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not all(part.isdigit() for part in ids):
        raise ConfigError(f"{name} must be a comma-separated list of numeric Discord ids")
    return frozenset(int(part) for part in ids)


def _time(name: str, default: str, tz: ZoneInfo) -> time:
    """Read an HH:MM time from the environment."""
    raw = os.environ.get(name, "").strip() or default
    try:
        hours, minutes = (int(x) for x in raw.split(":"))
        return time(hours, minutes, tzinfo=tz)
    except ValueError as exc:
        raise ConfigError(f"{name} must look like HH:MM, got {raw!r}") from exc


@dataclass(frozen=True)
class BotConfig:
    """Discord identifiers and schedule."""

    token: str
    owner_id: int
    guild_id: int
    channel_briefing: int
    channel_agent: int
    channel_capture: int
    channel_proposals: int
    briefing_time: time
    nudge_time: time
    export_time: time
    timezone: ZoneInfo
    agent_webhook_ids: frozenset[int] = frozenset()

    @property
    def channels(self) -> set[int]:
        """All channels the bot listens to."""
        return {
            self.channel_briefing,
            self.channel_agent,
            self.channel_capture,
            self.channel_proposals,
        }

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Build the configuration from environment variables.

        Raises:
            ConfigError: If a required value is missing or invalid.
        """
        token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        if not token:
            raise ConfigError("DISCORD_BOT_TOKEN must be set")
        tz = ZoneInfo(os.environ.get("TIMEZONE", "").strip() or "Europe/Paris")
        return cls(
            token=token,
            owner_id=_int("DISCORD_OWNER_ID"),
            guild_id=_int("DISCORD_GUILD_ID"),
            channel_briefing=_int("DISCORD_CHANNEL_BRIEFING"),
            channel_agent=_int("DISCORD_CHANNEL_AGENT"),
            channel_capture=_int("DISCORD_CHANNEL_CAPTURE"),
            channel_proposals=_int("DISCORD_CHANNEL_PROPOSALS"),
            briefing_time=_time("BRIEFING_TIME", "07:30", tz),
            nudge_time=_time("BRIEFING_NUDGE_TIME", "10:00", tz),
            export_time=_time("EXPORT_TIME", "03:00", tz),
            timezone=tz,
            agent_webhook_ids=_int_set("DISCORD_AGENT_WEBHOOK_IDS"),
        )
