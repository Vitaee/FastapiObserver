"""Experimental operation boundaries using the existing logging/client seams.

This module configures no logging, providers, framework or client hooks. Its
interface and event schema can change before promotion to the stable API.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
import logging
import time
from types import TracebackType
from uuid import uuid4

from .core.recording import CallHandle, CallKind, record_completed_call, validate_call
from .logging.filters import RequestIdFilter, TraceContextFilter
from .logging.held import keep_request_logs
from .logging import state as logging_state
from .request_context import (
    RequestState, _operation_context, get_request_id, get_request_state, get_user_context,
)

__all__ = ["observe_operation"]


class Operation:
    """A single-use sync/async scope; child calls belong to the child only."""

    def __init__(self, name: str, logger: logging.Logger) -> None:
        self.operation_id = uuid4().hex
        self._name = name
        self._logger = logger
        self._started: float | None = None
        self._state: RequestState | None = None
        self._context: AbstractContextManager[None] | None = None
        self._parent_id: str | None = None
        self._exited = False

    def __enter__(self) -> Operation:
        if self._started is not None:
            raise RuntimeError("An operation can only be entered once")
        parent = get_request_state()
        self._parent_id = parent.operation_id if parent is not None else None
        user_context = get_user_context()
        state = RequestState({}, user_context=dict(user_context) if user_context else None,
                             operation_id=self.operation_id)
        self._context = _operation_context(state, get_request_id() or self.operation_id)
        self._context.__enter__()
        self._state = state
        self._started = time.perf_counter()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._state is None or self._context is None or self._exited:
            return
        self._exited = True
        try:
            self._finish(exc)
        finally:
            if not self._state.closed:
                self._state.close()
            self._context.__exit__(exc_type, exc, traceback)

    async def __aenter__(self) -> Operation:
        return self.__enter__()

    def record_call(self, kind: CallKind, seconds: float, *, error: str | None = None) -> bool:
        """Record one completed custom-client call against this operation.

        Validate manual metadata before mutation. Returns false after closure;
        it never selects a different operation from the caller's current context.
        """
        if self._state is None:
            raise RuntimeError("Enter the operation before recording calls")
        validate_call(kind, seconds, error)
        return record_completed_call(self._state, kind, seconds, error=error)

    def start_call(self, kind: CallKind) -> CallHandle:
        """Capture a monotonic start and owner for a callback which finishes later."""
        if self._state is None or self._state.closed:
            raise RuntimeError("A new call needs an active operation")
        return CallHandle(self._state, kind)

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.__exit__(exc_type, exc, traceback)

    def _finish(self, exc: BaseException | None) -> None:
        assert self._state is not None and self._started is not None
        if exc is None:
            outcome, level, message = "success", logging.INFO, "operation.completed"
        elif isinstance(exc, asyncio.CancelledError):
            outcome, level, message = "cancelled", logging.WARNING, "operation.cancelled"
        else:
            outcome, level, message = "failure", logging.ERROR, "operation.failed"
        event: dict[str, object] = {
            "schema_version": "0.1.0",
            "name": self._name, "operation_id": self.operation_id,
            "outcome": outcome,
            "duration_ms": round(max(0.0, time.perf_counter() - self._started) * 1000, 3),
        }
        if self._parent_id is not None:
            event["parent_operation_id"] = self._parent_id
        try:
            if exc is not None:
                keep_request_logs(failed=True)
            record = self._logger.makeRecord(
                self._logger.name, level, __file__, 0, message, (), None, extra={"event": event},
            )
            RequestIdFilter().filter(record)
            TraceContextFilter().filter(record)
            held_filter = logging_state._HELD_LOG_FILTER
            admitted = held_filter is None or held_filter.filter(record)
            calls, errors = self._state.close()
            for kind, (count, seconds) in calls.items():
                summary: dict[str, object] = {"count": int(count), "ms": round(seconds * 1000, 3)}
                if kind in errors:
                    summary["errors"] = errors[kind]
                event[kind] = summary
            if admitted and self._logger.isEnabledFor(level):
                self._logger.handle(record)
        except Exception:
            # A borrowed application's handler may raise. Telemetry must not
            # replace the result, exception or cancellation of the operation.
            pass


def observe_operation(name: str, *, logger: logging.Logger | None = None) -> Operation:
    """Observe one Python operation without configuring its application.

    Use ``with`` or ``async with``. Explicitly attach existing SQLAlchemy/HTTP
    helpers to count dependencies. Pass a logger to retain application routing;
    otherwise terminal records use ``fastapiobserver.operations``. The caller
    owns logging configuration, sanitization, flushing and client lifecycle.
    """
    if not isinstance(name, str) or not name.strip() or len(name) > 128 or not name.isprintable():
        raise ValueError("Operation name must contain 1–128 printable characters")
    return Operation(name, logger if logger is not None else logging.getLogger(
        "fastapiobserver.operations"
    ))
