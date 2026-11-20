from __future__ import annotations

import logging
import math
import os
import sys
import sysconfig
import threading
from hashlib import sha256
from types import FrameType
from collections import deque
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .gc_pauses import gc_seconds
from .core.recording import CallKind, CallOwner
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
_DIAGNOSTICS = frozenset({"db.n_plus_one", "threadpool.saturated", "db.pool_exhausted"})
# The same statement this many times in one request is usually a query per item
# in a loop (N+1), which one query with a join or IN would replace.
# ponytail: fixed thresholds; make them settings if they're too noisy or quiet for someone.
_N_PLUS_ONE_MIN_REPEATS = 10
# One statement this slow is worth a look on its own, as in Sentry's slow-query default.
_SLOW_QUERY_SECONDS = 1.0
# A connection held this much longer than its queries ran sat idle, out of the pool.
_IDLE_CONNECTION_SECONDS = 0.1
_LIBRARY_DIRS = (
    "<",  # frozen modules and code compiled from strings
    os.path.dirname(os.path.abspath(__file__)),
    sysconfig.get_path("stdlib"),
    sysconfig.get_path("platstdlib"),
)
# Past the router or the event loop, application frames wrap or serve the request,
# such as an ASGI middleware or the script that started the server. Queries with
# no application code inside, such as relationships lazy-loaded while the response
# is serialized, get no location rather than one of those.
_REQUEST_BOUNDARIES = tuple(os.sep + os.path.join(*parts) for parts in (
    ("fastapi", "routing.py"), ("starlette", "routing.py"),
    ("asyncio", "base_events.py"), ("asyncio", "runners.py"),
))


# ponytail: an application installed into site-packages gets no location; add a
# setting naming its packages if someone deploys that way.
def _app_code_location() -> dict[str, str | int] | None:
    """The innermost running line outside this package, the standard library and
    installed packages: for a query, the application line that ran it."""
    try:
        frame: FrameType | None = sys._getframe(1)
        greenlet = sys.modules.get("greenlet")
        current = greenlet.getcurrent() if greenlet is not None else None
        while True:
            while frame is not None:
                code = frame.f_code
                filename = code.co_filename
                if filename.endswith(_REQUEST_BOUNDARIES):
                    return None
                if not (filename.startswith(_LIBRARY_DIRS) or "site-packages" in filename
                        or "dist-packages" in filename):
                    module = frame.f_globals.get("__name__")
                    return {
                        "code.file.path": filename,
                        "code.line.number": frame.f_lineno,
                        "code.function.name": (
                            f"{module}.{code.co_qualname}" if module else code.co_qualname
                        ),
                    }
                frame = frame.f_back
            # SQLAlchemy's async engines run the driver in a greenlet whose own
            # frames end inside SQLAlchemy; the awaiting code is on its parent's.
            current = getattr(current, "parent", None)
            if current is None:
                return None
            frame = current.gr_frame
    except Exception:
        # A diagnostic must never fail the query it describes.
        return None


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
    conn_held_seconds: float = 0.0
    errors: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "attempted": self.attempted, "succeeded": self.succeeded,
            "failed": self.failed,
            "in_flight": self.attempted - self.succeeded - self.failed,
            "failed_ms": round(self.failed_seconds * 1000, 3),
            "errors": dict(self.errors),
        }
        if self.conn_held_seconds > 0:
            result["conn_held_ms"] = round(self.conn_held_seconds * 1000, 3)
        return result


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
    # fingerprint -> the application code and ORM relationship that ran it, for
    # statements repeated enough
    statement_origins: dict[str, dict[str, str | int]] = field(default_factory=dict)
    # The slowest statement at or over _SLOW_QUERY_SECONDS: fingerprint, seconds, origin.
    slow_query: tuple[str, float, dict[str, str | int]] | None = None
    http_targets: dict[str, int] = field(default_factory=dict)
    validation_errors: list[dict[str, str]] | None = None
    active_db_connections: int = 0
    conn_held_during_http: dict[str, str | int] | None = None
    gc_seconds_at_start: float = field(default_factory=gc_seconds)  # read as it starts
    gc_pause_seconds: float | None = None
    observed_diagnostics: set[str] | None = None
    disconnected: bool = False
    after_disconnect_calls: dict[str, int] | None = None
    _diagnostics: set[str] | None = field(default=None, repr=False)
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
        relationship: str | None = None,
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
            repeated = slow = None
            targets = self.statements if kind == "db" else self.http_targets
            if statement:
                # SQL is never retained in request state or emitted by diagnostics.
                fingerprint = sha256(statement.encode("utf-8", errors="replace")).hexdigest()
                if fingerprint in targets or len(targets) < _MAX_REQUEST_STATEMENTS:
                    count = targets[fingerprint] = targets.get(fingerprint, 0) + 1
                    if count == _N_PLUS_ONE_MIN_REPEATS:
                        repeated = fingerprint
                        self._observe(f"{kind}.n_plus_one")
                if kind == "db" and seconds >= _SLOW_QUERY_SECONDS and self._slowest(seconds):
                    slow = fingerprint
                    self._observe("db.slow_query")
        if repeated is None and slow is None:
            return True
        # Walk the stack only for a statement that is repeated or slow, so other
        # queries pay nothing; outside the lock every request shares.
        origin = _app_code_location() or {}
        if relationship is not None:
            origin["relationship" if kind == "db" else "target"] = relationship
        with _CALLS_LOCK:
            if not self.closed:
                if repeated is not None and origin:
                    self.statement_origins[repeated] = origin
                if slow is not None and self._slowest(seconds):
                    self.slow_query = (slow, seconds, origin)
        return True

    def _observe(self, diagnostic: str) -> None:
        # Caller holds _CALLS_LOCK.
        if self.observed_diagnostics is None:
            self.observed_diagnostics = set()
        self.observed_diagnostics.add(diagnostic)

    def _slowest(self, seconds: float) -> bool:
        # Caller holds _CALLS_LOCK.
        return self.slow_query is None or seconds > self.slow_query[1]

    def _complete_call(self, kind: str, source: str, started: bool) -> None:
        self.call_sources.setdefault(kind, set()).add(source)
        if started:
            self.pending_calls[kind] = max(0, self.pending_calls.get(kind, 0) - 1)
        if self.disconnected:
            if self.after_disconnect_calls is None:
                self.after_disconnect_calls = {}
            self.after_disconnect_calls[kind] = self.after_disconnect_calls.get(kind, 0) + 1

    def forget_unrepeated_calls(self) -> None:
        """Drop the counts of statements and targets that haven't reached the N+1
        threshold.

        A WebSocket session calls it at each message. The same query once per
        message isn't a query per item, however long the session lasts, while a
        burst within one message still counts and stays recorded.
        """
        if not self.statements and not self.http_targets:
            return
        with _CALLS_LOCK:
            for counts in (self.statements, self.http_targets):
                for fingerprint in [
                    f for f, count in counts.items() if count < _N_PLUS_ONE_MIN_REPEATS
                ]:
                    del counts[fingerprint]

    def record_diagnostic_fact(self, name: str) -> None:
        with _CALLS_LOCK:
            if not self.closed:
                self._observe(name)

    def record_gc_pause(self, seconds: float) -> None:
        with _CALLS_LOCK:
            if not self.closed:
                self.gc_pause_seconds = seconds
                self._observe("gc.pause")

    def on_db_checkout(self) -> None:
        with _CALLS_LOCK:
            if not self.closed:
                if self.sql is None:
                    self.sql = SQLExecutions()
                self.active_db_connections += 1

    def on_db_checkin(self, held_seconds: float) -> None:
        with _CALLS_LOCK:
            if self.closed:
                return
            self.active_db_connections = max(0, self.active_db_connections - 1)
            if self.sql is not None:
                total = self.sql.conn_held_seconds + max(0.0, held_seconds)
                if math.isfinite(total * 1000):
                    self.sql.conn_held_seconds = total

    def on_http_start(self, target: str | None) -> None:
        if self.active_db_connections <= 0 or self.conn_held_during_http is not None:
            return
        with _CALLS_LOCK:
            if (
                self.closed
                or self.active_db_connections <= 0
                or self.conn_held_during_http is not None
            ):
                return
            self.conn_held_during_http = {
                "target": target[:200] if target else "<unknown>"
            }
        origin = _app_code_location()
        if origin:
            with _CALLS_LOCK:
                if not self.closed and self.conn_held_during_http is not None:
                    self.conn_held_during_http.update(origin)

    def check_held_connection(self) -> tuple[str, dict[str, str | int]] | None:
        """The diagnostic for connections this request kept out of the pool unused.

        Both need connections held at least ten times as long as their queries ran.
        ``db.connection_held_during_http``: one was out during an outbound HTTP call,
        or is still out. ``db.connection_held_idle``: otherwise, held
        ``_IDLE_CONNECTION_SECONDS`` or more beyond the queries' time.
        """
        if self.sql is None:  # no SQLAlchemy work, as on most requests: skip the lock
            return None
        with _CALLS_LOCK:
            if self.closed or self.sql is None:
                return None
            held = self.sql.conn_held_seconds
            db_seconds = self.calls.get("db", (0.0, 0.0))[1]
            long_hold = held >= db_seconds * 10.0
            details: dict[str, str | int]
            if self.conn_held_during_http is not None and (
                long_hold or self.active_db_connections > 0
            ):
                name, details = "db.connection_held_during_http", self.conn_held_during_http
            elif long_hold and held - db_seconds >= _IDLE_CONNECTION_SECONDS:
                name = "db.connection_held_idle"
                details = {"conn_held_ms": round(held * 1000), "db_ms": round(db_seconds * 1000)}
            else:
                return None
            self._observe(name)
            return name, details

    def drop_span(self) -> None:
        with _CALLS_LOCK:
            if not self.closed:
                self.dropped_spans += 1

    def add_diagnostic(self, name: str, attributes: dict[str, str | int | float]) -> None:
        """Add at most one of each fixed diagnostic to the borrowed server span.

        No provider or span is created. Attribute values come from diagnostic
        code, never from arbitrary log events or engine URLs.
        """
        span = self.span
        if self.closed or span is None or name not in _DIAGNOSTICS:
            return
        try:
            if (getattr(getattr(span, "kind", None), "name", None) != "SERVER"
                    or not span.is_recording()):
                return
            with _CALLS_LOCK:
                if self.closed:
                    return
                if self._diagnostics is None:
                    self._diagnostics = set()
                if name in self._diagnostics:
                    return
                self._diagnostics.add(name)
            # Application SDK code must run outside the aggregation lock.
            span.add_event(f"fastapiobserver.{name}", attributes=attributes)
        except Exception:
            # Diagnostics must never replace an application result or exception.
            pass

    def most_repeated_statement(
        self, kind: CallKind = "db"
    ) -> tuple[str, int, dict[str, str | int]] | None:
        """Its fingerprint, count and, once repeated enough, what is known of its origin:
        the application code location and the ORM relationship or HTTP target."""
        with _CALLS_LOCK:
            targets = self.statements if kind == "db" else self.http_targets
            top = max(targets.items(), key=lambda item: item[1], default=None)
            return None if top is None else (*top, self.statement_origins.get(top[0], {}))

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
        self.statement_origins.clear()
        self.slow_query = None
        self.http_targets.clear()
        self.validation_errors = None
        self.active_db_connections = 0
        self.conn_held_during_http = None
        self.gc_pause_seconds = None
        self.observed_diagnostics = None
        self.disconnected = False
        self.after_disconnect_calls = None
        self.http_span_ids.clear()
        self.held_logs = None
        self.user_context = None
        self._call_owner_token = None
        self.sql = None
        self.call_sources.clear()
        self.pending_calls.clear()
        self._diagnostics = None

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
