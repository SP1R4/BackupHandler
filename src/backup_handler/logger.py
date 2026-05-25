"""
logger.py - Structured Application Logging

Provides the :class:`AppLogger` façade used throughout the backup pipeline.
Supports three output streams:

  1. Rotating file handler — 50 MB x 30 files (human-readable or JSON).
  2. Console handler — always human-readable.
  3. Audit file handler — security- and compliance-relevant events only,
     written to a separate ``audit.log`` at INFO level.

A correlation ID (``run_id``) is attached to every record via
:mod:`contextvars` so log lines from a single backup run can be traced
across modules. Hostname, PID, and effective user are included by default.

Environment toggles:
    BACKUP_HANDLER_LOG_JSON=1    emit the main log file as JSON lines
    BACKUP_HANDLER_LOG_SYSLOG=1  also emit to the local syslog daemon
"""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from contextvars import ContextVar
from logging import handlers
from pathlib import Path
from typing import Any

_AUDIT_EVENTS = frozenset(
    {
        "audit.restore",
        "audit.config_loaded",
        "audit.encryption_key_loaded",
        "audit.schedule_start",
        "audit.backup_complete",
        "audit.backup_failed",
    }
)

_run_id_var: ContextVar[str] = ContextVar("run_id", default="-")


def new_run_id() -> str:
    """Generate and install a new correlation ID for the current context."""
    rid = uuid.uuid4().hex[:12]
    _run_id_var.set(rid)
    return rid


def current_run_id() -> str:
    """Return the correlation ID attached to the current context."""
    return _run_id_var.get()


class _ContextFilter(logging.Filter):
    """Attach hostname, pid, user, and run_id to every record."""

    def __init__(self) -> None:
        super().__init__()
        self._hostname = socket.gethostname()
        try:
            self._user = os.getlogin()
        except OSError:
            self._user = os.environ.get("USER", "unknown")

    def filter(self, record: logging.LogRecord) -> bool:
        record.hostname = self._hostname
        record.user = self._user
        record.run_id = _run_id_var.get()
        return True


class _AuditFilter(logging.Filter):
    """Pass through only records tagged with a known audit event name."""

    def filter(self, record: logging.LogRecord) -> bool:
        event = getattr(record, "audit_event", None)
        return event in _AUDIT_EVENTS


class JsonFormatter(logging.Formatter):
    """Emit each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
            "pid": record.process,
            "hostname": getattr(record, "hostname", "-"),
            "user": getattr(record, "user", "-"),
            "run_id": getattr(record, "run_id", "-"),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        audit_event = getattr(record, "audit_event", None)
        if audit_event:
            payload["audit_event"] = audit_event
        return json.dumps(payload, ensure_ascii=False)


def _truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


class AppLogger:
    """
    Configure and expose the application logger.

    Backwards-compatible with the previous API: callers continue to use
    ``AppLogger(path, level).logger`` and do not need to change.

    Parameters:
        log_file: Path to the primary rotating log file.
        log_level: Logging level for the main logger. Defaults to INFO.
        audit_file: Optional path for the audit stream. If omitted, an
            ``audit.log`` sibling of ``log_file`` is used.
    """

    def __init__(
        self,
        log_file: str | os.PathLike[str],
        log_level: int = logging.INFO,
        audit_file: str | os.PathLike[str] | None = None,
    ) -> None:
        self.logger = self._setup(Path(log_file), log_level, audit_file)

    def _setup(
        self,
        log_file: Path,
        log_level: int,
        audit_file: str | os.PathLike[str] | None,
    ) -> logging.Logger:
        logger = logging.getLogger("backup_handler")
        logger.setLevel(log_level)
        logger.propagate = False

        if logger.handlers:
            return logger

        log_file.parent.mkdir(parents=True, exist_ok=True)

        plain_fmt = logging.Formatter(
            "%(asctime)s [%(run_id)s] %(levelname)s %(name)s:%(lineno)d - %(message)s"
        )

        context_filter = _ContextFilter()
        use_json = _truthy(os.environ.get("BACKUP_HANDLER_LOG_JSON"))

        file_handler = handlers.RotatingFileHandler(
            log_file,
            maxBytes=50 * 1024 * 1024,
            backupCount=30,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonFormatter() if use_json else plain_fmt)
        file_handler.addFilter(context_filter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(plain_fmt)
        console_handler.addFilter(context_filter)
        logger.addHandler(console_handler)

        audit_path = Path(audit_file) if audit_file else log_file.parent / "audit.log"
        audit_handler = handlers.RotatingFileHandler(
            audit_path,
            maxBytes=20 * 1024 * 1024,
            backupCount=30,
            encoding="utf-8",
        )
        audit_handler.setLevel(logging.INFO)
        audit_handler.setFormatter(JsonFormatter())
        audit_handler.addFilter(context_filter)
        audit_handler.addFilter(_AuditFilter())
        logger.addHandler(audit_handler)

        if _truthy(os.environ.get("BACKUP_HANDLER_LOG_SYSLOG")):
            try:
                syslog_handler = handlers.SysLogHandler(address="/dev/log")
                syslog_handler.setFormatter(plain_fmt)
                syslog_handler.addFilter(context_filter)
                logger.addHandler(syslog_handler)
            except OSError:
                logger.warning("syslog handler requested but /dev/log is unavailable")

        return logger

    def log(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        """Proxy to the underlying logger (backwards compatibility)."""
        self.logger.log(level, msg, *args, **kwargs)


def audit(logger: logging.Logger, event: str, message: str, **fields: Any) -> None:
    """
    Emit an audit event.

    The record is picked up by the audit handler (via ``audit_event``
    tag) and also appears in the main log for operator visibility.

    Parameters:
        logger: Any logger instance (typically the one from AppLogger).
        event: One of the ``audit.*`` constants in :data:`_AUDIT_EVENTS`.
        message: Human-readable summary.
        **fields: Additional structured fields included in the JSON record.
    """
    if event not in _AUDIT_EVENTS:
        logger.warning(f"audit() called with unknown event '{event}'")
    extra = {"audit_event": event}
    extra.update(fields)
    logger.info(message, extra=extra)
