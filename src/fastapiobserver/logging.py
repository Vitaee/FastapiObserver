from __future__ import annotations

import importlib
import json
import logging
import logging.handlers
import queue
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast

from ._version import __version__
from .config import LogQueueOverflowPolicy, ObservabilitySettings
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
# Queue telemetry — bounded queue visibility + overflow counters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogQueueStatsSnapshot:
    queue_size: int
    queue_capacity: int
    overflow_policy: LogQueueOverflowPolicy
    enqueued_total: int
    dropped_total: int
    dropped_oldest_total: int
    dropped_newest_total: int
    blocked_total: int
    block_timeout_total: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "queue_size": self.queue_size,
            "queue_capacity": self.queue_capacity,
            "overflow_policy": self.overflow_policy,
            "enqueued_total": self.enqueued_total,
            "dropped_total": self.dropped_total,
            "dropped_oldest_total": self.dropped_oldest_total,
            "dropped_newest_total": self.dropped_newest_total,
            "blocked_total": self.blocked_total,
            "block_timeout_total": self.block_timeout_total,
        }


class LogQueueTelemetry:
    """Thread-safe in-memory counters for queue pressure visibility."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset(
            log_queue=None,
            queue_capacity=0,
            overflow_policy="drop_oldest",
        )

    def reset(
        self,
        *,
        log_queue: queue.Queue[logging.LogRecord] | None,
        queue_capacity: int,
        overflow_policy: LogQueueOverflowPolicy,
    ) -> None:
        with self._lock:
            self._queue = log_queue
            self._queue_capacity = queue_capacity
            self._overflow_policy = overflow_policy
            self._enqueued_total = 0
            self._dropped_total = 0
            self._dropped_oldest_total = 0
            self._dropped_newest_total = 0
            self._blocked_total = 0
            self._block_timeout_total = 0

    def record_enqueued(self) -> None:
        with self._lock:
            self._enqueued_total += 1

    def record_drop_oldest(self) -> None:
        with self._lock:
            self._dropped_total += 1
            self._dropped_oldest_total += 1

    def record_drop_newest(self) -> None:
        with self._lock:
            self._dropped_total += 1
            self._dropped_newest_total += 1

    def record_blocked(self) -> None:
        with self._lock:
            self._blocked_total += 1

    def record_block_timeout(self) -> None:
        with self._lock:
            self._block_timeout_total += 1

    def snapshot(self) -> LogQueueStatsSnapshot:
        with self._lock:
            queue_ref = self._queue
            queue_capacity = self._queue_capacity
            overflow_policy = self._overflow_policy
            enqueued_total = self._enqueued_total
            dropped_total = self._dropped_total
            dropped_oldest_total = self._dropped_oldest_total
            dropped_newest_total = self._dropped_newest_total
            blocked_total = self._blocked_total
            block_timeout_total = self._block_timeout_total

        queue_size = _safe_queue_size(queue_ref)
        if queue_ref is not None:
            queue_capacity = queue_ref.maxsize

        return LogQueueStatsSnapshot(
            queue_size=queue_size,
            queue_capacity=queue_capacity,
            overflow_policy=overflow_policy,
            enqueued_total=enqueued_total,
            dropped_total=dropped_total,
            dropped_oldest_total=dropped_oldest_total,
            dropped_newest_total=dropped_newest_total,
            blocked_total=blocked_total,
            block_timeout_total=block_timeout_total,
        )


_LOG_QUEUE_TELEMETRY = LogQueueTelemetry()


class OverflowPolicyQueueHandler(logging.handlers.QueueHandler):
    """Queue handler with explicit overflow policy and queue telemetry."""

    def __init__(
        self,
        log_queue: queue.Queue[logging.LogRecord],
        *,
        overflow_policy: LogQueueOverflowPolicy,
        block_timeout_seconds: float,
        telemetry: LogQueueTelemetry,
    ) -> None:
        super().__init__(log_queue)
        self.overflow_policy = overflow_policy
        self.block_timeout_seconds = block_timeout_seconds
        self.telemetry = telemetry

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
            self.telemetry.record_enqueued()
            return
        except queue.Full:
            pass

        if self.overflow_policy == "drop_newest":
            self.telemetry.record_drop_newest()
            return

        if self.overflow_policy == "drop_oldest":
            self._drop_oldest_then_enqueue(record)
            return

        self._block_then_enqueue(record)

    def _drop_oldest_then_enqueue(self, record: logging.LogRecord) -> None:
        log_queue = cast(queue.Queue[logging.LogRecord], self.queue)
        try:
            log_queue.get_nowait()
            self.telemetry.record_drop_oldest()
        except queue.Empty:
            pass

        try:
            log_queue.put_nowait(record)
            self.telemetry.record_enqueued()
        except queue.Full:
            # Another producer can win the free slot; newest record is dropped.
            self.telemetry.record_drop_newest()

    def _block_then_enqueue(self, record: logging.LogRecord) -> None:
        log_queue = cast(queue.Queue[logging.LogRecord], self.queue)
        self.telemetry.record_blocked()
        try:
            log_queue.put(
                record,
                block=True,
                timeout=self.block_timeout_seconds,
            )
            self.telemetry.record_enqueued()
        except queue.Full:
            self.telemetry.record_block_timeout()
            self.telemetry.record_drop_newest()


def get_log_queue_stats() -> dict[str, int | str]:
    """Return a snapshot of queue pressure counters for diagnostics/metrics."""
    return _LOG_QUEUE_TELEMETRY.snapshot().as_dict()


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
        _LOG_QUEUE_TELEMETRY.reset(
            log_queue=None,
            queue_capacity=settings.log_queue_max_size,
            overflow_policy=settings.log_queue_overflow_policy,
        )

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

        log_queue: queue.Queue[logging.LogRecord] = queue.Queue(
            maxsize=settings.log_queue_max_size
        )
        _LOG_QUEUE_TELEMETRY.reset(
            log_queue=log_queue,
            queue_capacity=settings.log_queue_max_size,
            overflow_policy=settings.log_queue_overflow_policy,
        )
        queue_handler = OverflowPolicyQueueHandler(
            log_queue,
            overflow_policy=settings.log_queue_overflow_policy,
            block_timeout_seconds=settings.log_queue_block_timeout_seconds,
            telemetry=_LOG_QUEUE_TELEMETRY,
        )
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


def _safe_queue_size(log_queue: queue.Queue[logging.LogRecord] | None) -> int:
    if log_queue is None:
        return 0
    try:
        return log_queue.qsize()
    except Exception:
        return 0


def _json_dumps(payload: dict[str, Any]) -> str:
    if orjson is not None:
        return orjson.dumps(payload).decode("utf-8")
    return json.dumps(payload, ensure_ascii=True, default=str)
