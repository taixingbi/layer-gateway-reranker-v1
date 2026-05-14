"""Gateway JSON log events and uvicorn logging configuration."""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_JSON_CONTEXT_KEYS = ("request_id", "session_id", "method", "path", "status")
_JSON_FIXED_KEYS = frozenset({"ts", "level", "logger", *_JSON_CONTEXT_KEYS, "message", "error"})
_GATEWAY_OPTIONAL_STRINGS = ("trace_id", "request_id", "session_id", "path", "backend", "conversation_id")


def _load_log_timezone(name: str) -> ZoneInfo:
    """Resolve `LOG_TIMEZONE` / formatter timezone (falls back to New York)."""
    raw = (name or "EST").strip()
    if raw.upper() in ("EST", "EDT") or raw == "US/Eastern":
        raw = "America/New_York"
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/New_York")


def _gateway_env() -> str:
    """Return `GATEWAY_ENV` or `ENV` (default `dev`) for log payloads."""
    return os.environ.get("GATEWAY_ENV") or os.environ.get("ENV", "dev")


def log_gateway_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    path: str | None = None,
    backend: str | None = None,
    conversation_id: str | None = None,
    is_new_conversation: bool | None = None,
    latency_ms: float | None = None,
    queue_wait_ms: float | None = None,
    status_code: int | None = None,
    error: Mapping[str, Any] | None = None,
    gateway_meta: Mapping[str, Any] | None = None,
) -> None:
    """Emit a gateway log record; extras become JSON fields in `JsonLogFormatter`."""
    extra: dict[str, Any] = {"event": event, "service": "gateway", "env": _gateway_env()}
    if request_id is not None:
        extra["request_id"] = request_id
    if trace_id is not None:
        extra["trace_id"] = trace_id
    if session_id is not None:
        extra["session_id"] = session_id
    if path is not None:
        extra["path"] = path
    if backend is not None:
        extra["backend"] = backend
    if conversation_id is not None:
        extra["conversation_id"] = conversation_id
    if is_new_conversation is not None:
        extra["is_new_conversation"] = is_new_conversation
    if latency_ms is not None:
        extra["latency_ms"] = latency_ms
    if queue_wait_ms is not None:
        extra["queue_wait_ms"] = queue_wait_ms
    if status_code is not None:
        extra["status_code"] = status_code
    if error is not None:
        extra["structured_error"] = dict(error)
    if gateway_meta is not None:
        extra["gateway_meta"] = dict(gateway_meta)
    logger.log(level, event, extra=extra)


class JsonLogFormatter(logging.Formatter):
    """Format `log_gateway_event` records as JSON; other records use a legacy JSON shape."""

    def __init__(self, *, timezone: str = "America/New_York", extra_fields: Sequence[str] = ()) -> None:
        super().__init__()
        self._tz = _load_log_timezone(timezone)
        self._extras = tuple(extra_fields)

    def format(self, record: logging.LogRecord) -> str:
        """Return a single JSON object per line."""
        if getattr(record, "event", None):
            return self._format_gateway(record)
        return self._format_legacy(record)

    @staticmethod
    def _gateway_level(levelname: str) -> str:
        if levelname == "WARNING":
            return "WARN"
        return levelname

    def _format_gateway(self, record: logging.LogRecord) -> str:
        """Serialize gateway `extra` fields into the stable gateway JSON schema."""
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=self._tz).isoformat(),
            "level": self._gateway_level(record.levelname),
            "event": record.event,
            "service": getattr(record, "service", "gateway"),
            "env": getattr(record, "env", "-"),
        }
        for key in _GATEWAY_OPTIONAL_STRINGS:
            val = getattr(record, key, None)
            payload[key] = val if val not in (None, "") else "-"
        if hasattr(record, "is_new_conversation"):
            payload["is_new_conversation"] = bool(record.is_new_conversation)
        if getattr(record, "latency_ms", None) is not None:
            payload["latency_ms"] = record.latency_ms
        if getattr(record, "queue_wait_ms", None) is not None:
            payload["queue_wait_ms"] = record.queue_wait_ms
        if getattr(record, "status_code", None) is not None:
            payload["status_code"] = record.status_code
        err = getattr(record, "structured_error", None)
        if err is not None:
            payload["error"] = err
        if getattr(record, "gateway_meta", None) is not None:
            payload["gateway_meta"] = record.gateway_meta
        for key in self._extras:
            if key in payload or key in {"ts", "level", "event", "service", "env", "error"}:
                continue
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)

    def _format_legacy(self, record: logging.LogRecord) -> str:
        """Serialize non-gateway log records as compact JSON."""
        err = self.formatException(record.exc_info) if record.exc_info else None
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=self._tz).isoformat(),
            "level": record.levelname,
            "logger": record.name,
        }
        for key in _JSON_CONTEXT_KEYS:
            payload[key] = getattr(record, key, "-")
        payload["message"] = record.getMessage()
        if err is not None:
            payload["error"] = err
        for key in self._extras:
            if key in _JSON_FIXED_KEYS:
                continue
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


def build_logging_config(*, level_name: str, json_logs: bool) -> dict[str, Any]:
    """Build a `dictConfig` for uvicorn/root logging to stdout."""
    formatter_name = "json" if json_logs else "standard"
    formatters: dict[str, Any] = {
        "json": {
            "()": f"{JsonLogFormatter.__module__}.{JsonLogFormatter.__qualname__}",
            "timezone": os.environ.get("LOG_TIMEZONE", "America/New_York"),
            "extra_fields": ("backend",),
        },
        "standard": {"format": "%(levelname)s %(name)s %(message)s"},
    }
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {formatter_name: formatters[formatter_name]},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": formatter_name,
                "stream": sys.stdout,
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": level_name, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": level_name, "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": level_name, "propagate": False},
        },
        "root": {"handlers": ["default"], "level": level_name},
    }


def new_request_id() -> str:
    """Return a new UUID string for `X-Request-Id`."""
    return str(uuid.uuid4())
