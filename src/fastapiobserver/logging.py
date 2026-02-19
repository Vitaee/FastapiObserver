from __future__ import annotations

import atexit
import hashlib
import json
import logging
import logging.handlers
import queue
import re
import threading
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Mapping, cast

from ._version import __version__
from .config import LogQueueOverflowPolicy, ObservabilitySettings
from .plugins import apply_log_enrichers, apply_log_filters
from .request_context import get_request_id, get_span_id, get_trace_id, get_user_context
from .security import SecurityPolicy, sanitize_event
from .sinks import build_sink_handlers
from .utils import lazy_import

orjson: Any
try:
    orjson = lazy_import("orjson")
except ModuleNotFoundError:  # pragma: no cover - fallback for environments without orjson
    orjson = None

_LOGGING_LOCK = threading.Lock()
_QUEUE_LISTENER: logging.handlers.QueueListener | None = None
_MANAGED_OUTPUT_HANDLERS: list[logging.Handler] = []
_SINK_CIRCUIT_BREAKERS: list["SinkCircuitBreakerHandler"] = []
_MANAGED_HANDLERS: weakref.WeakSet[logging.Handler] = weakref.WeakSet()
_ATEXIT_SHUTDOWN_REGISTERED = False
_LOGGER = logging.getLogger("fastapiobserver.logging")
LOG_SCHEMA_VERSION = "1.0.0"

CircuitBreakerState = Literal["closed", "open", "half_open"]


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


@dataclass(frozen=True)
class SinkCircuitBreakerSnapshot:
    sink_name: str
    state: CircuitBreakerState
    failure_threshold: int
    recovery_timeout_seconds: float
    consecutive_failures: int
    handled_total: int
    failures_total: int
    skipped_total: int
    opens_total: int
    half_open_total: int
    closes_total: int

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "sink_name": self.sink_name,
            "state": self.state,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout_seconds": self.recovery_timeout_seconds,
            "consecutive_failures": self.consecutive_failures,
            "handled_total": self.handled_total,
            "failures_total": self.failures_total,
            "skipped_total": self.skipped_total,
            "opens_total": self.opens_total,
            "half_open_total": self.half_open_total,
            "closes_total": self.closes_total,
        }


class SinkCircuitBreakerHandler(logging.Handler):
    """Protect sink handlers with a basic open/half-open/closed breaker."""

    def __init__(
        self,
        *,
        sink_name: str,
        delegate: logging.Handler,
        failure_threshold: int,
        recovery_timeout_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(level=delegate.level)
        self.sink_name = sink_name
        self._delegate = delegate
        self._failure_threshold = max(1, int(failure_threshold))
        self._recovery_timeout_seconds = max(0.001, float(recovery_timeout_seconds))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()

        self._state: CircuitBreakerState = "closed"
        self._opened_until = 0.0
        self._consecutive_failures = 0
        self._handled_total = 0
        self._failures_total = 0
        self._skipped_total = 0
        self._opens_total = 0
        self._half_open_total = 0
        self._closes_total = 0

    def emit(self, record: logging.LogRecord) -> None:
        if self._should_skip():
            return

        try:
            self._delegate.handle(record)
        except Exception:
            self._record_failure()
            self.handleError(record)
            return

        self._record_success()

    def setFormatter(self, fmt: logging.Formatter | None) -> None:  # noqa: N802
        super().setFormatter(fmt)
        self._delegate.setFormatter(fmt)

    def flush(self) -> None:
        self._delegate.flush()

    def close(self) -> None:
        try:
            self._delegate.close()
        finally:
            super().close()

    def snapshot(self) -> SinkCircuitBreakerSnapshot:
        with self._lock:
            return SinkCircuitBreakerSnapshot(
                sink_name=self.sink_name,
                state=self._state,
                failure_threshold=self._failure_threshold,
                recovery_timeout_seconds=self._recovery_timeout_seconds,
                consecutive_failures=self._consecutive_failures,
                handled_total=self._handled_total,
                failures_total=self._failures_total,
                skipped_total=self._skipped_total,
                opens_total=self._opens_total,
                half_open_total=self._half_open_total,
                closes_total=self._closes_total,
            )

    def _should_skip(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._state != "open":
                return False
            if now >= self._opened_until:
                self._state = "half_open"
                self._half_open_total += 1
                return False
            self._skipped_total += 1
            return True

    def _record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            self._failures_total += 1
            self._consecutive_failures += 1

            should_open = (
                self._state == "half_open"
                or self._consecutive_failures >= self._failure_threshold
            )
            if should_open:
                self._state = "open"
                self._opens_total += 1
                self._opened_until = now + self._recovery_timeout_seconds
                self._consecutive_failures = 0

    def _record_success(self) -> None:
        with self._lock:
            self._handled_total += 1
            self._consecutive_failures = 0
            if self._state == "half_open":
                self._state = "closed"
                self._closes_total += 1
                self._opened_until = 0.0


def get_sink_circuit_breaker_stats() -> dict[str, dict[str, int | float | str]]:
    """Return per-sink circuit-breaker snapshots."""
    with _LOGGING_LOCK:
        breakers = list(_SINK_CIRCUIT_BREAKERS)
    return {breaker.sink_name: breaker.snapshot().as_dict() for breaker in breakers}


def get_managed_output_handlers() -> list[logging.Handler]:
    """Return the currently managed output handlers."""
    with _LOGGING_LOCK:
        return list(_MANAGED_OUTPUT_HANDLERS)


def shutdown_logging() -> None:
    """Flush and stop managed logging components.

    Safe to call multiple times. Used by FastAPI shutdown hooks and ``atexit``.
    """

    global _QUEUE_LISTENER
    global _MANAGED_OUTPUT_HANDLERS
    global _SINK_CIRCUIT_BREAKERS

    with _LOGGING_LOCK:
        listener = _QUEUE_LISTENER
        _QUEUE_LISTENER = None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                _LOGGER.debug(
                    "logging.queue_listener.stop_failed",
                    exc_info=True,
                    extra={"_skip_enrichers": True},
                )

        root_logger = logging.getLogger()
        managed_handlers = [
            handler
            for handler in root_logger.handlers
            if handler in _MANAGED_HANDLERS
        ]
        for handler in managed_handlers:
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                _LOGGER.debug(
                    "logging.managed_handler.close_failed",
                    exc_info=True,
                    extra={"_skip_enrichers": True},
                )

        for output_handler in _MANAGED_OUTPUT_HANDLERS:
            try:
                output_handler.close()
            except Exception:
                _LOGGER.debug(
                    "logging.output_handler.close_failed",
                    exc_info=True,
                    extra={"_skip_enrichers": True},
                )
        _MANAGED_OUTPUT_HANDLERS = []
        _SINK_CIRCUIT_BREAKERS = []
        _MANAGED_HANDLERS.clear()
        _LOG_QUEUE_TELEMETRY.reset(
            log_queue=None,
            queue_capacity=0,
            overflow_policy="drop_oldest",
        )


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
        *,
        enrich_event: (
            Callable[[dict[str, object]], Mapping[str, object] | dict[str, object]] | None
        ) = None,
        sanitize_payload: (
            Callable[[dict[str, Any], SecurityPolicy], Mapping[str, Any] | dict[str, Any]]
            | None
        ) = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.security_policy = security_policy or SecurityPolicy()
        self._enrich_event = enrich_event or apply_log_enrichers
        self._sanitize_payload = sanitize_payload or sanitize_event

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
            error_payload = _build_structured_error(record, self)
            payload["error"] = error_payload
            # Backward-compatible field retained for existing dashboards.
            payload["exc_info"] = error_payload["stacktrace"]

        if getattr(record, "_skip_enrichers", False):
            enriched_payload = payload
        else:
            candidate_payload = self._enrich_event(dict(payload))
            if isinstance(candidate_payload, Mapping):
                enriched_payload = dict(candidate_payload)
            else:
                enriched_payload = payload
        sanitized_candidate = self._sanitize_payload(enriched_payload, self.security_policy)
        if isinstance(sanitized_candidate, Mapping):
            sanitized_payload = dict(sanitized_candidate)
        else:
            sanitized_payload = enriched_payload
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
            _LOGGER.debug(
                "trace_context_filter.otel_lookup_failed",
                exc_info=True,
                extra={"_skip_enrichers": True},
            )

        if trace_id:
            record.trace_id = trace_id  # type: ignore[attr-defined]
        if span_id:
            record.span_id = span_id  # type: ignore[attr-defined]
        return True


class PluginLogFilter(logging.Filter):
    """Apply user-registered log filters with fault isolation."""

    def filter(self, record: logging.LogRecord) -> bool:
        return apply_log_filters(record)


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
    global _SINK_CIRCUIT_BREAKERS

    with _LOGGING_LOCK:
        root_logger = logging.getLogger()
        managed_handlers = [
            handler
            for handler in root_logger.handlers
            if handler in _MANAGED_HANDLERS
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
        _SINK_CIRCUIT_BREAKERS = []
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
        output_handlers: list[tuple[logging.Handler, str]] = []
        if logs_mode in ("local_json", "both"):
            output_handlers = build_sink_handlers(settings, formatter)
            
        extra_output_handlers: list[tuple[logging.Handler, str]] = []
        if extra_handlers:
            for h in extra_handlers:
                extra_output_handlers.append((h, h.__class__.__name__.lower()))
                
        all_output_handlers = output_handlers + extra_output_handlers
        if not all_output_handlers:
            raise RuntimeError(
                "Logging setup resolved zero output handlers. "
                "Configure at least one local sink or OTLP handler."
            )
        for output_handler, _ in all_output_handlers:
            if output_handler.formatter is None:
                output_handler.setFormatter(formatter)

        managed_output_handlers, sink_breakers = _wrap_sink_handlers(
            all_output_handlers,
            failure_threshold=settings.sink_circuit_breaker_failure_threshold,
            recovery_timeout_seconds=settings.sink_circuit_breaker_recovery_timeout_seconds,
            enabled=settings.sink_circuit_breaker_enabled,
        )
        _SINK_CIRCUIT_BREAKERS = sink_breakers

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
            *managed_output_handlers,
            respect_handler_level=True,
        )
        listener.start()
        _QUEUE_LISTENER = listener
        _MANAGED_OUTPUT_HANDLERS = managed_output_handlers
        _ensure_atexit_shutdown_hook_locked()

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
    handler.addFilter(PluginLogFilter())
    _MANAGED_HANDLERS.add(handler)


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


def _ensure_atexit_shutdown_hook_locked() -> None:
    global _ATEXIT_SHUTDOWN_REGISTERED
    if _ATEXIT_SHUTDOWN_REGISTERED:
        return
    atexit.register(shutdown_logging)
    _ATEXIT_SHUTDOWN_REGISTERED = True


def _build_structured_error(
    record: logging.LogRecord,
    formatter: logging.Formatter,
) -> dict[str, str]:
    exc_info = record.exc_info
    if not exc_info:
        return {"type": "", "message": "", "stacktrace": ""}

    if not isinstance(exc_info, tuple) or len(exc_info) != 3:
        return {
            "type": "Exception",
            "message": "",
            "stacktrace": "",
        }

    exc_type, exc_value, _exc_tb = exc_info
    error_type = exc_type.__name__ if exc_type is not None else "Exception"
    error_message = str(exc_value) if exc_value is not None else ""
    stacktrace = formatter.formatException(exc_info)
    fingerprint = _generate_error_fingerprint(error_type, stacktrace)

    return {
        "type": error_type,
        "message": error_message,
        "stacktrace": stacktrace,
        "fingerprint": fingerprint,
    }


def _generate_error_fingerprint(error_type: str, stacktrace: str) -> str:
    """Hash stack trace after stripping out environment-specific noise.
    
    Removes transient values like:
    1. Memory addresses (e.g. 0x10a2b3c4d)
    2. Exact line numbers (e.g. line 42)
    This guarantees refactor-safe grouping of identical underlying errors.
    """
    if not stacktrace:
        return hashlib.md5(error_type.encode("utf-8")).hexdigest()

    # Strip hexadecimal memory addresses
    sanitized = re.sub(r"0x[0-9a-fA-F]+", "0x<ptr>", stacktrace)
    # Strip exact line numbers to survive minor file refactoring
    sanitized = re.sub(r"line \d+", "line <N>", sanitized)
    
    # Combine error type and sanitized trace to ensure distinct groupings
    payload = f"{error_type}:{sanitized}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _wrap_sink_handlers(
    handlers_with_names: list[tuple[logging.Handler, str]],
    *,
    failure_threshold: int,
    recovery_timeout_seconds: float,
    enabled: bool,
) -> tuple[list[logging.Handler], list[SinkCircuitBreakerHandler]]:
    if not enabled:
        return [h for h, _ in handlers_with_names], []

    wrapped_handlers: list[logging.Handler] = []
    breakers: list[SinkCircuitBreakerHandler] = []
    name_counts: dict[str, int] = {}
    for handler, base_name in handlers_with_names:
        sink_name = _resolve_sink_name(base_name, name_counts)
        breaker = SinkCircuitBreakerHandler(
            sink_name=sink_name,
            delegate=handler,
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=recovery_timeout_seconds,
        )
        wrapped_handlers.append(breaker)
        breakers.append(breaker)
    return wrapped_handlers, breakers


def _resolve_sink_name(
    base_name: str,
    name_counts: dict[str, int],
) -> str:
    normalized_name = base_name.strip().lower()
    count = name_counts.get(normalized_name, 0) + 1
    name_counts[normalized_name] = count
    if count == 1:
        return normalized_name
    return f"{normalized_name}_{count}"
