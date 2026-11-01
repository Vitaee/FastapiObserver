from __future__ import annotations

import atexit
import logging
import logging.handlers
import queue
import threading
import time
from typing import Literal, cast

from ..config import ObservabilitySettings
from ..security import SecurityPolicy
from ..sinks import build_sink_handlers
from .circuit_breaker import SinkCircuitBreakerHandler
from .filters import PluginLogFilter, RequestIdFilter, TraceContextFilter
from .formatter import StructuredJsonFormatter
from .held import HeldLogFilter
from .queueing import OverflowPolicyQueueHandler, _LOG_QUEUE_TELEMETRY

_LOGGER = logging.getLogger("fastapiobserver.logging")
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


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
        _teardown_handlers_locked(suppress_errors=True)
        _LOG_QUEUE_TELEMETRY.reset(
            log_queue=None,
            queue_capacity=0,
            overflow_policy="drop_oldest",
        )


def flush_logging(timeout_seconds: float = 5.0) -> None:
    """Wait until queued records are written, then flush the outputs.

    Logging keeps running, for other apps in the process and for this one if it
    starts again. ``atexit`` stops it when the process exits.
    """
    import fastapiobserver.logging.state as state

    listener = state._QUEUE_LISTENER
    if listener is None:
        return
    log_queue = cast(queue.Queue[logging.LogRecord], listener.queue)
    deadline = time.monotonic() + timeout_seconds
    # The listener marks each record done once its handlers have run.
    while log_queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.005)
    for handler in list(state._MANAGED_OUTPUT_HANDLERS):
        try:
            handler.flush()
        except Exception:
            _LOGGER.debug(
                "logging.output_handler.flush_failed",
                exc_info=True,
                extra={"_skip_enrichers": True},
            )


def _teardown_handlers_locked(suppress_errors: bool = True) -> None:
    """Safely stop listeners, unregister managed handlers, and close outputs."""
    import fastapiobserver.logging.state as state

    errors: list[Exception] = []

    listener = state._QUEUE_LISTENER
    state._QUEUE_LISTENER = None
    if listener is not None:
        try:
            listener.stop()
        except Exception as exc:
            if not suppress_errors:
                errors.append(exc)
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
        except Exception as exc:
            if not suppress_errors:
                errors.append(exc)
            _LOGGER.debug(
                "logging.managed_handler.close_failed",
                exc_info=True,
                extra={"_skip_enrichers": True},
            )

    for output_handler in state._MANAGED_OUTPUT_HANDLERS:
        try:
            output_handler.close()
        except Exception as exc:
            if not suppress_errors:
                errors.append(exc)
            _LOGGER.debug(
                "logging.output_handler.close_failed",
                exc_info=True,
                extra={"_skip_enrichers": True},
            )
            
    state._MANAGED_OUTPUT_HANDLERS = []
    state._SINK_CIRCUIT_BREAKERS = []
    state._HELD_LOG_FILTER = None
    state._MANAGED_HANDLERS.clear()

    if errors and not suppress_errors:
        if len(errors) == 1:
            raise errors[0]
        error_details = "\n".join(f"  - {type(e).__name__}: {str(e)}" for e in errors)
        raise RuntimeError(
            f"Multiple errors during logging teardown:\n{error_details}"
        ) from errors[0]


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

        _teardown_handlers_locked(suppress_errors=False)
        _LOG_QUEUE_TELEMETRY.reset(
            log_queue=None,
            queue_capacity=settings.log_queue_max_size,
            overflow_policy=settings.log_queue_overflow_policy,
        )

        formatter = StructuredJsonFormatter(settings=settings, security_policy=security_policy)

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
        level = logging.getLevelName(settings.log_level)
        lowest_level = level
        if settings.log_level_on_error is not None:
            lowest_level = min(level, logging.getLevelName(settings.log_level_on_error))
        if lowest_level < level or settings.log_sample_rate < 1:
            held_filter = HeldLogFilter(
                queue_handler, level, lowest_level, settings.log_sample_rate
            )
            queue_handler.addFilter(held_filter)
            state._HELD_LOG_FILTER = held_filter
        set_log_level(level)
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
        _ensure_fork_reinitialization_locked()

        for name in _UVICORN_LOGGERS:
            uvicorn_logger = logging.getLogger(name)
            uvicorn_logger.handlers = []
            uvicorn_logger.propagate = True


def set_log_level(level: int) -> None:
    """Set the log level, at startup and from the runtime control plane.

    With ``OBS_LOG_LEVEL_ON_ERROR`` below *level*, the root logger stays at that
    lower level so the records are created, and the held-log filter applies
    *level* instead.
    """
    import fastapiobserver.logging.state as state

    root_level = level
    held_filter = state._HELD_LOG_FILTER
    if held_filter is not None:
        held_filter.level = level
        root_level = min(level, held_filter.lowest_level)
    logging.getLogger().setLevel(root_level)
    for name in _UVICORN_LOGGERS:
        logging.getLogger(name).setLevel(level)


def get_log_level() -> str:
    """The level records are written at, as set by :func:`set_log_level`."""
    import fastapiobserver.logging.state as state

    held_filter = state._HELD_LOG_FILTER
    level = (
        held_filter.level
        if held_filter is not None
        else logging.getLogger().getEffectiveLevel()
    )
    return logging.getLevelName(level)


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


def _ensure_fork_reinitialization_locked() -> None:
    import os
    import fastapiobserver.logging.state as state
    if getattr(state, "_FORK_HOOK_REGISTERED", False):
        return
    if hasattr(os, "register_at_fork"):
        os.register_at_fork(after_in_child=_restart_queue_listener_in_child)
    setattr(state, "_FORK_HOOK_REGISTERED", True)


def _restart_queue_listener_in_child() -> None:
    import fastapiobserver.logging.state as state

    listener = state._QUEUE_LISTENER
    if listener is not None:
        # Another thread of the parent may have held any lock at the moment of
        # the fork, and no thread in the child will release it. So don't touch
        # the inherited queue, not even to drain it: its records are the
        # parent's to write. Give the child fresh locks and a fresh queue.
        _LOG_QUEUE_TELEMETRY._lock = threading.Lock()
        for breaker in state._SINK_CIRCUIT_BREAKERS:
            breaker._lock = threading.Lock()
        old_queue = cast(queue.Queue[logging.LogRecord], listener.queue)
        new_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=old_queue.maxsize)

        # Re-attach the new queue to the OverflowPolicyQueueHandler if present
        for handler in state._MANAGED_HANDLERS:
            from .queueing import OverflowPolicyQueueHandler

            if (
                isinstance(handler, OverflowPolicyQueueHandler)
                and getattr(handler, "queue", None) is old_queue
            ):
                handler.queue = new_queue

        new_listener = logging.handlers.QueueListener(
            new_queue,
            *listener.handlers,
            respect_handler_level=listener.respect_handler_level,
        )
        state._QUEUE_LISTENER = new_listener
        new_listener.start()


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
