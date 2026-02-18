from __future__ import annotations

import importlib
import json
import logging
import logging.handlers
import queue
import threading
from datetime import datetime, timezone
from typing import Any, Literal

from ._version import __version__
from .config import ObservabilitySettings
from .plugins import apply_log_enrichers
from .request_context import get_request_id, get_span_id, get_trace_id, get_user_context
from .security import SecurityPolicy, sanitize_event
from .sinks import build_sink_handlers

orjson: Any
try:
    orjson = importlib.import_module("orjson")
except ModuleNotFoundError:  # pragma: no cover - fallback for environments without orjson
    orjson = None

_LOGGING_LOCK = threading.Lock()
_QUEUE_LISTENER: logging.handlers.QueueListener | None = None
_MANAGED_OUTPUT_HANDLERS: list[logging.Handler] = []
LOG_SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Formatter — Single Responsibility: formats *what* each log record contains
# ---------------------------------------------------------------------------


class StructuredJsonFormatter(logging.Formatter):
    """Serialize log records into structured JSON.

    Fields include service identity, request ID, trace/span IDs for
    correlation, and user context.  Plugin enrichers are applied unless
    the record has ``_skip_enrichers`` set.
    """

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


# ---------------------------------------------------------------------------
# Filters — separate concerns: request ID and trace context injection
# ---------------------------------------------------------------------------


class RequestIdFilter(logging.Filter):
    """Attach request ID from context vars to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = get_request_id()
        return True


class TraceContextFilter(logging.Filter):
    """Attach OTel trace_id / span_id to every log record."""

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
            record.trace_id = trace_id  # type: ignore[attr-defined]
        if span_id:
            record.span_id = span_id  # type: ignore[attr-defined]
        return True


# ---------------------------------------------------------------------------
# Setup — orchestrates formatter, queue-based pipeline, and sink handlers
# ---------------------------------------------------------------------------


def setup_logging(
    settings: ObservabilitySettings,
    *,
    force: bool = True,
    security_policy: SecurityPolicy | None = None,
    logs_mode: Literal["local_json", "otlp", "both"] = "local_json",
    extra_handlers: list[logging.Handler] | None = None,
) -> None:
    """Configure the root logger with structured JSON output.

    Uses a ``QueueHandler`` → ``QueueListener`` pipeline so that I/O
    happens in a background thread, keeping request handlers non-blocking.

    Parameters
    ----------
    logs_mode:
        ``"local_json"`` — normal local sinks only (default).
        ``"otlp"`` — only OTLP handler (local sinks suppressed).
        ``"both"`` — local sinks **and** OTLP handler.
    extra_handlers:
        Additional output handlers (e.g. OTel ``LoggingHandler``) to route
        through the ``QueueListener`` pipeline alongside local sinks.
    """
    global _QUEUE_LISTENER
    global _MANAGED_OUTPUT_HANDLERS

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

        if _QUEUE_LISTENER is not None:
            _QUEUE_LISTENER.stop()
            _QUEUE_LISTENER = None

        for output_handler in _MANAGED_OUTPUT_HANDLERS:
            output_handler.close()
        _MANAGED_OUTPUT_HANDLERS = []

        formatter = StructuredJsonFormatter(
            settings=settings, security_policy=security_policy
        )
        root_logger.setLevel(settings.log_level.upper())

        # Build output handlers based on logs_mode
        output_handlers: list[logging.Handler] = []
        if logs_mode in ("local_json", "both"):
            output_handlers = build_sink_handlers(settings, formatter)
        if extra_handlers:
            output_handlers.extend(extra_handlers)
        if not output_handlers:
            raise RuntimeError(
                "Logging setup resolved zero output handlers. "
                "Configure at least one local sink or OTLP handler."
            )
        for output_handler in output_handlers:
            if output_handler.formatter is None:
                output_handler.setFormatter(formatter)

        log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
        queue_handler = logging.handlers.QueueHandler(log_queue)
        _configure_queue_handler(queue_handler)
        root_logger.addHandler(queue_handler)

        listener = logging.handlers.QueueListener(
            log_queue,
            *output_handlers,
            respect_handler_level=True,
        )
        listener.start()
        _QUEUE_LISTENER = listener
        _MANAGED_OUTPUT_HANDLERS = output_handlers

        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.handlers = []
            uvicorn_logger.propagate = True
            uvicorn_logger.setLevel(settings.log_level.upper())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _configure_queue_handler(handler: logging.Handler) -> None:
    handler.addFilter(RequestIdFilter())
    handler.addFilter(TraceContextFilter())
    setattr(handler, "_fastapiobserver_managed", True)


def _json_dumps(payload: dict[str, Any]) -> str:
    if orjson is not None:
        return orjson.dumps(payload).decode("utf-8")
    return json.dumps(payload, ensure_ascii=True, default=str)
