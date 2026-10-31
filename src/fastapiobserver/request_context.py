from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

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


@dataclass(eq=False, slots=True)
class RequestState:
    """What code running for a request adds to it, read when the request ends.

    The middleware creates one per request. Sync endpoints and dependencies run
    in the thread pool with a copy of the context, which still holds this object,
    so what they add reaches the middleware.
    """

    scope: Mapping[str, Any]
    user_context: dict[str, Any] | None = None
    sampled: bool | None = None  # decided at its first record that needs it
    held_logs: deque[logging.LogRecord] | None = None
    calls: dict[str, list[float]] = field(default_factory=dict)  # kind -> [count, seconds]
    statements: dict[str, int] = field(default_factory=dict)

    def add_call(self, kind: str, seconds: float, statement: str | None = None) -> None:
        with _CALLS_LOCK:
            totals = self.calls.setdefault(kind, [0, 0.0])
            totals[0] += 1
            totals[1] += seconds
            if statement:
                self.statements[statement] = self.statements.get(statement, 0) + 1


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
    return _request_state_var.get()


def set_request_id(value: str | None) -> None:
    _request_id_var.set(value)


def get_request_id() -> str | None:
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
    _user_context_var.set(value)
    request_state = _request_state_var.get()
    if request_state is not None:
        # Also on the request, which the middleware sees even when a sync
        # endpoint sets it from the thread pool.
        request_state.user_context = value


def get_user_context() -> dict[str, Any] | None:
    request_state = _request_state_var.get()
    if request_state is not None and request_state.user_context is not None:
        return request_state.user_context
    # Set outside the request, for example by middleware added after ours.
    return _user_context_var.get()


def clear_user_context() -> None:
    set_user_context(None)
