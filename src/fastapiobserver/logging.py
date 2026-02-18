from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._version import __version__
from .config import ObservabilitySettings
from .plugins import apply_log_enrichers
from .request_context import get_request_id, get_span_id, get_trace_id, get_user_context
from .security import SecurityPolicy, sanitize_event

orjson: Any
try:
    import orjson  # type: ignore[no-redef]
except ModuleNotFoundError:  # pragma: no cover - fallback for environments without orjson
    orjson = None

_LOGGING_LOCK = threading.Lock()
LOG_SCHEMA_VERSION = "1.0.0"


class StructuredJsonFormatter(logging.Formatter):
    def __init__(
        self,
        settings: ObservabilitySettings,
        security_policy: SecurityPolicy | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.security_policy = security_policy or SecurityPolicy()

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "app_name": self.settings.app_name,
            "service": self.settings.service,
            "environment": self.settings.environment,
            "version": self.settings.version,
            "log_schema_version": LOG_SCHEMA_VERSION,
            "library": "fastapiobserver",
            "library_version": __version__,
        }

        request_id = getattr(record, "request_id", None) or get_request_id()
        if request_id:
            payload["request_id"] = request_id

        trace_id = getattr(record, "trace_id", None) or get_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id

        span_id = getattr(record, "span_id", None) or get_span_id()
        if span_id:
            payload["span_id"] = span_id

        user_context = get_user_context()
        if user_context:
            payload["user_context"] = user_context

        event = getattr(record, "event", None)
        if isinstance(event, dict):
            payload["event"] = event

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        if getattr(record, "_skip_enrichers", False):
            enriched_payload = payload
        else:
            enriched_payload = apply_log_enrichers(payload)
        sanitized_payload = sanitize_event(enriched_payload, self.security_policy)
        return _json_dumps(sanitized_payload)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = get_request_id()
        return True


class TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = get_trace_id()
        span_id = get_span_id()

        try:
            from opentelemetry import trace as otel_trace  # type: ignore

            span = otel_trace.get_current_span()
            span_context = span.get_span_context()
            if span_context and span_context.is_valid:
                trace_id = f"{span_context.trace_id:032x}"
                span_id = f"{span_context.span_id:016x}"
        except Exception:
            pass

        if trace_id:
            record.trace_id = trace_id
        if span_id:
            record.span_id = span_id
        return True


def setup_logging(
    settings: ObservabilitySettings,
    *,
    force: bool = True,
    security_policy: SecurityPolicy | None = None,
) -> None:
    with _LOGGING_LOCK:
        root_logger = logging.getLogger()
        managed_handlers = [
            handler
            for handler in root_logger.handlers
            if getattr(handler, "_fastapiobserver_managed", False)
        ]

        if managed_handlers and not force:
            return

        for handler in managed_handlers:
            root_logger.removeHandler(handler)
            handler.close()

        formatter = StructuredJsonFormatter(
            settings=settings, security_policy=security_policy
        )
        root_logger.setLevel(settings.log_level.upper())

        stream_handler = logging.StreamHandler(sys.stdout)
        _configure_handler(stream_handler, formatter)
        root_logger.addHandler(stream_handler)

        if settings.log_dir:
            log_dir = Path(settings.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            file_name = f"{settings.service or settings.app_name}.log"
            file_handler = logging.handlers.RotatingFileHandler(
                os.fspath(log_dir / file_name),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            _configure_handler(file_handler, formatter)
            root_logger.addHandler(file_handler)

        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.handlers = []
            uvicorn_logger.propagate = True
            uvicorn_logger.setLevel(settings.log_level.upper())


def _configure_handler(
    handler: logging.Handler,
    formatter: logging.Formatter,
) -> None:
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())
    handler.addFilter(TraceContextFilter())
    setattr(handler, "_fastapiobserver_managed", True)


def _json_dumps(payload: dict[str, Any]) -> str:
    if orjson is not None:
        return orjson.dumps(payload).decode("utf-8")
    return json.dumps(payload, ensure_ascii=True, default=str)
