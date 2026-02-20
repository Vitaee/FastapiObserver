from __future__ import annotations

import atexit
import logging
import logging.handlers
import queue
from typing import Literal

from ..config import ObservabilitySettings
from ..security import SecurityPolicy
from ..sinks import build_sink_handlers
from .circuit_breaker import SinkCircuitBreakerHandler
from .filters import PluginLogFilter, RequestIdFilter, TraceContextFilter
from .formatter import StructuredJsonFormatter
from .queueing import OverflowPolicyQueueHandler, _LOG_QUEUE_TELEMETRY

_LOGGER = logging.getLogger("fastapiobserver.logging")


def get_managed_output_handlers() -> list[logging.Handler]:
    """Return the currently managed output handlers."""
    import fastapiobserver.logging.state as state

    with state._LOGGING_LOCK:
        return list(state._MANAGED_OUTPUT_HANDLERS)


def shutdown_logging() -> None:
    """Flush and stop managed logging components.

    Safe to call multiple times. Used by FastAPI shutdown hooks and ``atexit``.
    """
    import fastapiobserver.logging.state as state

    with state._LOGGING_LOCK:
        listener = state._QUEUE_LISTENER
        state._QUEUE_LISTENER = None
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
            if handler in state._MANAGED_HANDLERS
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

        for output_handler in state._MANAGED_OUTPUT_HANDLERS:
            try:
                output_handler.close()
            except Exception:
                _LOGGER.debug(
                    "logging.output_handler.close_failed",
                    exc_info=True,
                    extra={"_skip_enrichers": True},
                )
        state._MANAGED_OUTPUT_HANDLERS = []
        state._SINK_CIRCUIT_BREAKERS = []
        state._MANAGED_HANDLERS.clear()
        _LOG_QUEUE_TELEMETRY.reset(
            log_queue=None,
            queue_capacity=0,
            overflow_policy="drop_oldest",
        )


def setup_logging(
    settings: ObservabilitySettings,
    *,
    force: bool = True,
    security_policy: SecurityPolicy | None = None,
    logs_mode: Literal["local_json", "otlp", "both"] = "local_json",
    extra_handlers: list[logging.Handler] | None = None,
) -> None:
    """Configure the root logger with structured JSON output."""
    import fastapiobserver.logging.state as state

    with state._LOGGING_LOCK:
        root_logger = logging.getLogger()
        managed_handlers = [
            handler
            for handler in root_logger.handlers
            if handler in state._MANAGED_HANDLERS
        ]

        if managed_handlers and not force:
            return

        for handler in managed_handlers:
            root_logger.removeHandler(handler)
            handler.close()

        if state._QUEUE_LISTENER is not None:
            state._QUEUE_LISTENER.stop()
            state._QUEUE_LISTENER = None

        for output_handler in state._MANAGED_OUTPUT_HANDLERS:
            output_handler.close()
        state._MANAGED_OUTPUT_HANDLERS = []
        state._SINK_CIRCUIT_BREAKERS = []
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
        state._SINK_CIRCUIT_BREAKERS = sink_breakers

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
        state._QUEUE_LISTENER = listener
        state._MANAGED_OUTPUT_HANDLERS = managed_output_handlers
        _ensure_atexit_shutdown_hook_locked()

        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.handlers = []
            uvicorn_logger.propagate = True
            uvicorn_logger.setLevel(settings.log_level.upper())


def _configure_queue_handler(handler: logging.Handler) -> None:
    import fastapiobserver.logging.state as state
    handler.addFilter(RequestIdFilter())
    handler.addFilter(TraceContextFilter())
    handler.addFilter(PluginLogFilter())
    state._MANAGED_HANDLERS.add(handler)


def _ensure_atexit_shutdown_hook_locked() -> None:
    import fastapiobserver.logging.state as state
    if state._ATEXIT_SHUTDOWN_REGISTERED:
        return
    atexit.register(shutdown_logging)
    state._ATEXIT_SHUTDOWN_REGISTERED = True


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
    seen_names: dict[str, int] = {}

    for delegate, base_name in handlers_with_names:
        normalized_name = base_name.strip().lower()
        if normalized_name in seen_names:
            seen_names[normalized_name] += 1
            sink_name = f"{normalized_name}_{seen_names[normalized_name]}"
        else:
            seen_names[normalized_name] = 1
            sink_name = normalized_name

        breaker = SinkCircuitBreakerHandler(
            sink_name=sink_name,
            delegate=delegate,
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=recovery_timeout_seconds,
        )
        wrapped_handlers.append(breaker)
        breakers.append(breaker)

    return wrapped_handlers, breakers
