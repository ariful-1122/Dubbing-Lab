"""Structured logging configuration."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings

_CONFIGURED = False


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON for file output."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        if hasattr(record, "extra_fields") and record.extra_fields:
            payload.update(record.extra_fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Human-readable console formatter."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


class StructuredLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that attaches structured fields to records."""

    def process(
        self,
        msg: str,
        kwargs: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        extra_fields = dict(self.extra)
        event = kwargs.pop("event", None)
        if event:
            extra_fields["event"] = event
        log_extra = kwargs.pop("log_extra", None)
        if log_extra:
            extra_fields.update(log_extra)
        extra["extra_fields"] = extra_fields
        if event:
            extra["event"] = event
        return msg, kwargs


def setup_logging(settings: Settings | None = None) -> None:
    """Configure application-wide logging (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = settings or get_settings()
    settings.log_path.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("app")
    root.setLevel(settings.log_level)
    root.handlers.clear()
    root.propagate = False

    log_file = settings.log_path / settings.log_file
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    file_handler.setLevel(settings.log_level)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(HumanFormatter())
    console_handler.setLevel(settings.log_level)
    root.addHandler(console_handler)

    _CONFIGURED = True


def get_logger(name: str, **context: Any) -> StructuredLoggerAdapter:
    """Return a structured logger for the given module name."""
    setup_logging()
    base = logging.getLogger(f"app.{name}")
    return StructuredLoggerAdapter(base, context)


def log_event(
    logger: StructuredLoggerAdapter,
    level: int,
    event: str,
    message: str,
    **fields: Any,
) -> None:
    """Emit a structured log event."""
    logger.log(level, message, event=event, log_extra=fields)
