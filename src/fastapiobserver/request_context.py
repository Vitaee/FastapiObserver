from __future__ import annotations

import logging
import math
import threading
from hashlib import sha256
from collections import deque
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .core.recording import CallOwner
__all__ = [
    "UNMATCHED_ROUTE",
    "RequestState",
    "get_request_state",
    "route_template",
    "set_request_state",
    "set_request_id",
    "get_request_id",
    "clear_request_id",
    "set_trace_id",
    "get_trace_id",
    "clear_trace_id",
    "set_span_id",
    "get_span_id",
    "clear_span_id",
    "set_user_context",
    "get_user_context",
    "clear_user_context",
]

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
_span_id_var: ContextVar[str | None] = ContextVar("span_id", default=None)
_user_context_var: ContextVar[dict[str, Any] | None] = ContextVar(
    "user_context", default=None
)
_request_state_var: ContextVar[RequestState | None] = ContextVar(
    "request_state", default=None
)
_CALLS_LOCK = threading.Lock()

# Requests that matched no route share this label, so scanners can't add one per URL.
UNMATCHED_ROUTE = "<unmatched>"
_MAX_REQUEST_STATEMENTS = 128
_MAX_ERROR_CATEGORIES = 32


def _add_error(errors: dict[str, int], error: str) -> None:
    if error not in errors and len(errors) >= _MAX_ERROR_CATEGORIES:
        error = "other"
    errors[error] = errors.get(error, 0) + 1


@dataclass(slots=True)
class SQLExecutions:
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    failed_seconds: float = 0.0
    errors: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {"attempted": self.attempted, "succeeded": self.succeeded,
                "failed": self.failed,
                "in_flight": self.attempted - self.succeeded - self.failed,
                "failed_ms": round(self.failed_seconds * 1000, 3),
                "errors": dict(self.errors)}


@dataclass(eq=False, slots=True, weakref_slot=True)
class RequestState:
    """What code running for a request adds to it, read when the request ends.

    The middleware creates one per request. Sync endpoints and dependencies run
    in the thread pool with a copy of the context, which still holds this object,
    so what they add reaches the middleware.
    """

    scope: Mapping[str, Any]
    # The recording span current when the request reached the middleware: its
    # server span, since tracing wraps the whole app. Spans opened inside, such as
    # FastAPI's endpoint span, would hide it from code that runs later.
    span: Any = None
    user_context: dict[str, Any] | None = None
    sampled: bool | None = None  # decided at its first record that needs it
    sample_probability: float | None = None
    sample_reason: str | None = None
    held_logs: deque[logging.LogRecord] | None = None
    calls: dict[str, list[float]] = field(default_factory=dict)  # kind -> [count, seconds]
    call_errors: dict[str, dict[str, int]] = field(default_factory=dict)
    http_span_ids: set[int | None] = field(default_factory=set)
    statements: dict[str, int] = field(default_factory=dict)
    operation_id: str | None = None
    closed: bool = False
    resolved_route: str | None = None
    sql: SQLExecutions | None = None
    call_sources: dict[str, set[str]] = field(default_factory=dict)
    pending_calls: dict[str, int] = field(default_factory=dict)
    dropped_spans: int = 0
    observer_logging: bool = False
    retention_reason: str | None = None
    _call_owner_token: CallOwner | None = field(default=None, repr=False)

    def _call_owner(self, source: str | None = None, kind: str | None = None) -> CallOwner | None:
        with _CALLS_LOCK:
            if self.closed:
                return None
            if self._call_owner_token is None:
                self._call_owner_token = CallOwner(self)
            if kind is not None and source is not None:
                self.call_sources.setdefault(kind, set()).add(source)
                self.pending_calls[kind] = self.pending_calls.get(kind, 0) + 1
            if source == "sqlalchemy":
                if self.sql is None:
                    self.sql = SQLExecutions()
                self.sql.attempted += 1
            return self._call_owner_token

    def add_call(
        self, kind: str, seconds: float, statement: str | None = None, *, error: str | None = None
    ) -> None:
        self._record_call(kind, seconds, statement, error=error)

    def _record_call(
        self, kind: str, seconds: float, statement: str | None = None, *,
        error: str | None = None, source: str = "manual", started: bool = False,
    ) -> bool:
        with _CALLS_LOCK:
            if self.closed:
                return False
            if source == "sqlalchemy" and self.sql is not None and error is not None:
                total = self.sql.failed_seconds + seconds
                if not math.isfinite(total * 1000):
                    return False
                self.sql.failed += 1
                self.sql.failed_seconds = total
                _add_error(self.sql.errors, error)
                self._complete_call(kind, source, started)
                return True
            previous = self.calls.get(kind)
            total_seconds = (previous[1] if previous is not None else 0.0) + seconds
            if not math.isfinite(total_seconds * 1000):
                return False
            totals = self.calls.setdefault(kind, [0, 0.0])
            totals[0] += 1
            totals[1] = total_seconds
            if error is not None:
                errors = self.call_errors.setdefault(kind, {})
                _add_error(errors, error)
            if source == "sqlalchemy" and self.sql is not None:
                self.sql.succeeded += 1
            self._complete_call(kind, source, started)
            if statement:
                # SQL is never retained in request state or emitted by N+1 warnings.
                fingerprint = sha256(statement.encode("utf-8", errors="replace")).hexdigest()
                if fingerprint in self.statements or len(self.statements) < _MAX_REQUEST_STATEMENTS:
                    self.statements[fingerprint] = self.statements.get(fingerprint, 0) + 1
            return True

    def _complete_call(self, kind: str, source: str, started: bool) -> None:
        self.call_sources.setdefault(kind, set()).add(source)
        if started:
            self.pending_calls[kind] = max(0, self.pending_calls.get(kind, 0) - 1)

    def drop_span(self) -> None:
        with _CALLS_LOCK:
            if not self.closed:
                self.dropped_spans += 1

    def _observation(self) -> dict[str, Any]:
        if not self.call_sources and not self.dropped_spans:
            return {"coverage": "unknown"}
        sources = set().union(*self.call_sources.values())
        pending = {kind: count for kind, count in self.pending_calls.items() if count}
        if "sampled_span" in sources or pending or self.dropped_spans or (
            "manual" in sources and len(sources) > 1
        ):
            coverage = "partial"
        else:
            coverage = "caller_reported" if sources == {"manual"} else "instrumented_clients"
        result: dict[str, Any] = {"coverage": coverage}
        if self.call_sources:
            result["sources"] = {kind: sorted(values) for kind, values in self.call_sources.items()}
        if pending:
            result["in_flight"] = pending
        if self.dropped_spans:
            result["dropped_spans"] = self.dropped_spans
        return result

    def summarize_calls(self, *, close: bool = False) -> dict[str, Any]:
        """Copy diagnostic fields atomically; optionally freeze the operation."""
        with _CALLS_LOCK:
            result: dict[str, Any] = {"observation": self._observation()}
            for kind, (count, seconds) in self.calls.items():
                result[kind] = {"count": int(count), "ms": round(seconds * 1000, 3)}
                if kind in self.call_errors:
                    result[kind]["errors"] = dict(self.call_errors[kind])
            if self.sql is not None:
                result.setdefault("db", {"count": 0, "ms": 0.0})["sqlalchemy"] = self.sql.summary()
            if close:
                self._release()
            return result

    def _release(self) -> None:
        # Caller holds _CALLS_LOCK, shared with admission and snapshots.
        self.closed = True
        self.calls, self.call_errors = {}, {}
        self.statements.clear()
        self.http_span_ids.clear()
        self.held_logs = None
        self.user_context = None
        self._call_owner_token = None
        self.sql = None
        self.call_sources.clear()
        self.pending_calls.clear()

    def close(self) -> tuple[dict[str, list[float]], dict[str, dict[str, int]]]:
        """Freeze experimental totals and release state retained by copied contexts."""
        with _CALLS_LOCK:
            calls, errors = self.calls, self.call_errors
            self._release()
            return calls, errors

    def hold_log(self, record: logging.LogRecord, limit: int) -> None:
        with _CALLS_LOCK:
            if not self.closed:
                if self.held_logs is None:
                    self.held_logs = deque(maxlen=limit)
                self.held_logs.append(record)

    def mark_http_span(self, span_id: int) -> None:
        with _CALLS_LOCK:
            if not self.closed:
                self.http_span_ids.add(span_id)


def route_template(scope: Mapping[str, Any]) -> str | None:
    """The matched route's template, such as ``/users/{user_id}``, once routing has run.

    Since FastAPI 0.137, ``scope["route"].path`` leaves out prefixes given to
    ``include_router()`` and those of outer routers. FastAPI keeps the full
    template in its effective route context, set for routes in included routers.
    """
    fastapi_scope = scope.get("fastapi")
    context = (
        fastapi_scope.get("effective_route_context") if isinstance(fastapi_scope, dict) else None
    )
    template = getattr(context, "path", None) or getattr(scope.get("route"), "path", None)
    return template if isinstance(template, str) and template else None


def set_request_state(value: RequestState | None) -> None:
    _request_state_var.set(value)


def get_request_state() -> RequestState | None:
    state = _request_state_var.get()
    return state if state is not None and not state.closed else None


def set_request_id(value: str | None) -> None:
    _request_id_var.set(value)


def get_request_id() -> str | None:
    state = _request_state_var.get()
    if state is not None and state.closed:
        return None
    return _request_id_var.get()


def clear_request_id() -> None:
    _request_id_var.set(None)


def set_trace_id(value: str | None) -> None:
    _trace_id_var.set(value)


def get_trace_id() -> str | None:
    return _trace_id_var.get()


def clear_trace_id() -> None:
    _trace_id_var.set(None)


def set_span_id(value: str | None) -> None:
    _span_id_var.set(value)


def get_span_id() -> str | None:
    return _span_id_var.get()


def clear_span_id() -> None:
    _span_id_var.set(None)


def set_user_context(value: dict[str, Any] | None) -> None:
    request_state = _request_state_var.get()
    # Experimental contexts retain user data only on their shared, closeable
    # state. A copied ContextVar must not keep it alive after closure.
    if request_state is not None and request_state.operation_id is not None:
        with _CALLS_LOCK:
            if not request_state.closed:
                request_state.user_context = value
        return
    _user_context_var.set(value)
    if request_state is not None:
        # Also on the request, which the middleware sees even when a sync
        # endpoint sets it from the thread pool.
        request_state.user_context = value


def get_user_context() -> dict[str, Any] | None:
    request_state = _request_state_var.get()
    if request_state is not None and request_state.closed:
        return None
    if request_state is not None and request_state.user_context is not None:
        return request_state.user_context
    # Set outside the request, for example by middleware added after ours.
    return _user_context_var.get()


def clear_user_context() -> None:
    set_user_context(None)


@contextmanager
def _operation_context(state: RequestState, request_id: str) -> Iterator[None]:
    """Bind the existing correlation seam without clearing an outer request."""
    state_token = _request_state_var.set(state)
    id_token = _request_id_var.set(request_id)
    user_token = _user_context_var.set(None)
    trace_token = _trace_id_var.set(_trace_id_var.get())
    span_token = _span_id_var.set(_span_id_var.get())
    try:
        yield
    finally:
        _span_id_var.reset(span_token)
        _trace_id_var.reset(trace_token)
        _user_context_var.reset(user_token)
        _request_id_var.reset(id_token)
        _request_state_var.reset(state_token)
