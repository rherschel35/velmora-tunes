"""Run Velmora House Tunes. Secrets from the environment are never printed."""

from __future__ import annotations

import logging
import sys

from bot.client import VelmoraBot
from bot.config import ConfigError, load_settings


class _RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [secret for secret in secrets if len(secret) >= 4]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        rendered = record.getMessage()
        redacted = rendered
        for secret in self._secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, "[redacted]")
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True


def _configure_logging(token: str, password: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(_RedactingFilter([token, password]))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    _configure_logging(settings.token, settings.lavalink_password)
    bot = VelmoraBot(settings)
    bot.run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
