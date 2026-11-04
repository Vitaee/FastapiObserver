"""Each request's SQLAlchemy queries, counted from SQLAlchemy's own events.

The request line's ``db`` totals and the ``db.n_plus_one`` warning then work
without tracing, and see every request instead of only sampled ones. Queries
other clients make, such as Redis or a driver used directly, are counted from
their OpenTelemetry spans when tracing is on.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable
from contextvars import ContextVar

from .core.recording import CallHandle
from .request_context import get_request_state

# SQLAlchemy's instrumentation for OpenTelemetry; its spans would count twice.
SQLALCHEMY_INSTRUMENTATION = "opentelemetry.instrumentation.sqlalchemy"
_STARTED = "_fastapiobserver_call"
_QUERY_OBSERVERS: ContextVar[tuple[Callable[[Any], None], ...]] = ContextVar(
    "native_query_observers", default=(),
)


def _has_query_hooks(engine: Any) -> bool:
    from sqlalchemy import event
    return all(event.contains(engine, name, callback) for name, callback in _QUERY_HOOKS)


def count_queries(engine: Any) -> None:
    """Add each query a sync SQLAlchemy ``Engine`` runs to its request's ``db`` totals."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    if isinstance(engine, Engine):
        for name, callback in _QUERY_HOOKS:
            if not event.contains(engine, name, callback):
                event.listen(engine, name, callback)


def _before(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    for observer in _QUERY_OBSERVERS.get():
        observer(conn.engine)
    if context is not None:
        state = get_request_state()
        setattr(context, _STARTED,
                CallHandle(state, "db", source="sqlalchemy") if state is not None else None)


def _after(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    call = getattr(context, _STARTED, None)
    if call is not None:
        call.finish(statement=statement)


def _error(context: Any) -> None:
    """Observe failed cursor attempts without changing SQLAlchemy's exception chain."""
    call = getattr(context.execution_context, _STARTED, None)
    if call is not None:
        from sqlalchemy.exc import IntegrityError
        category = ("integrity_error" if isinstance(context.sqlalchemy_exception, IntegrityError)
                    else "disconnect" if context.is_disconnect else "execution_error")
        call.finish(error=category)


_QUERY_HOOKS = (("before_cursor_execute", _before), ("after_cursor_execute", _after),
                ("handle_error", _error))


__all__ = ["SQLALCHEMY_INSTRUMENTATION", "count_queries"]
