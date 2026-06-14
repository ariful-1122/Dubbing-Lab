"""Structured logging configuration."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import contextvars

from app.config import Settings, get_settings

active_job_id: contextvars.ContextVar[str] = contextvars.ContextVar("active_job_id", default="")


class JobFileHandler(logging.Handler):
    """Logging handler that routes logs to a job-specific log file based on active_job_id context."""

    def emit(self, record: logging.LogRecord) -> None:
        job_id = active_job_id.get()
        if not job_id:
            # Fallback to checking extra fields
            extra_fields = getattr(record, "extra_fields", {})
            job_id = extra_fields.get("job_id") or getattr(record, "job_id", None)

        if job_id:
            try:
                settings = get_settings()
                processing_dir = settings.processing_path
                job_dir = None
                if processing_dir.exists():
                    for p in processing_dir.iterdir():
                        if p.is_dir() and (p.name == job_id or p.name.endswith(f"_{job_id}")):
                            job_dir = p
                            break
                if job_dir:
                    log_file = job_dir / "job.log"
                    # Format log message
                    msg = self.format(record)
                    with log_file.open("a", encoding="utf-8") as f:
                        f.write(msg + "\n")
            except Exception:
                pass


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

    job_file_handler = JobFileHandler()
    job_file_handler.setFormatter(HumanFormatter())
    job_file_handler.setLevel(settings.log_level)
    root.addHandler(job_file_handler)

    # Ensure stdout and stderr support UTF-8 to prevent UnicodeEncodeError in Windows consoles
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass

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
