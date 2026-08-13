"""Struktureret logopsætning.

Logger i nøgle=værdi-stil, som er let at læse i terminalen og let at
parse maskinelt:

    INFO import.document.classified source_id=BEK-2023-0001 score=93 maritime=True
"""

from __future__ import annotations

import logging
import sys
from typing import Any

_CONFIGURED = False


class KeyValueFormatter(logging.Formatter):
    """Formatter der tilføjer ekstra felter som key=value."""

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in self._RESERVED and not key.startswith("_")
        }
        if extras:
            rendered = " ".join(f"{k}={_render(v)}" for k, v in extras.items())
            return f"{base} {rendered}"
        return base


def _render(value: Any) -> str:
    text = str(value)
    if " " in text:
        return f'"{text}"'
    return text


def configure_logging(level: str = "INFO") -> None:
    """Konfigurerer rod-loggeren. Idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        KeyValueFormatter(
            fmt="%(asctime)s %(levelname)-5s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Dæmp meget snakkesalige biblioteker.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(logging.INFO)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
