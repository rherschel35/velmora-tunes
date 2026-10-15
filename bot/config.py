"""Environment configuration. Secret values are never logged."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


class ConfigError(RuntimeError):
    """Raised when required environment variables are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    token: str
    guild_id: int
    lavalink_uri: str
    lavalink_password: str

    @property
    def lavalink_host(self) -> str:
        """Host and port only, safe to write to logs."""

        parts = urlsplit(self.lavalink_uri)
        host = parts.hostname or "unknown"
        if parts.port:
            return f"{host}:{parts.port}"
        return host


_REQUIRED = ("DISCORD_TOKEN", "GUILD_ID", "LAVALINK_URI", "LAVALINK_PASSWORD")


def load_settings() -> Settings:
    missing = [name for name in _REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        joined = ", ".join(missing)
        raise ConfigError(f"Missing required environment variables: {joined}")

    guild_raw = os.environ["GUILD_ID"].strip()
    try:
        guild_id = int(guild_raw)
    except ValueError as exc:
        raise ConfigError("GUILD_ID must be a numeric Discord guild id") from exc
    if guild_id <= 0:
        raise ConfigError("GUILD_ID must be a numeric Discord guild id")

    uri = os.environ["LAVALINK_URI"].strip().rstrip("/")
    if not uri.startswith(("http://", "https://")):
        raise ConfigError("LAVALINK_URI must start with http:// or https://")

    return Settings(
        token=os.environ["DISCORD_TOKEN"].strip(),
        guild_id=guild_id,
        lavalink_uri=uri,
        lavalink_password=os.environ["LAVALINK_PASSWORD"].strip(),
    )
