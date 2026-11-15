"""Each request's SQLAlchemy queries, counted from SQLAlchemy's own events.

The request line's ``db`` totals and the ``db.n_plus_one`` warning then work
without tracing, and see every request instead of only sampled ones. Queries
other clients make, such as Redis or a driver used directly, are counted from
their OpenTelemetry spans when tracing is on.
"""

from __future__ import annotations

import time
import weakref
from typing import Any
from collections.abc import Callable
from contextvars import ContextVar

from .core.recording import CallHandle
from .request_context import get_request_state

# SQLAlchemy's instrumentation for OpenTelemetry; its spans would count twice.
SQLALCHEMY_INSTRUMENTATION = "opentelemetry.instrumentation.sqlalchemy"
_STARTED = "_fastapiobserver_call"
_CHECKOUT = "_fastapiobserver_checkout"
_RELATIONSHIP = "fastapiobserver_relationship"
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
        pool = getattr(engine, "pool", None)
        if pool is not None:
            for pool_event, pool_callback in _POOL_HOOKS:
                if not event.contains(pool, pool_event, pool_callback):
                    event.listen(pool, pool_event, pool_callback)
        from sqlalchemy.orm import Session

        # Once for every session in the process: the ORM event has no engine.
        if not event.contains(Session, "do_orm_execute", _label_relationship_load):
            event.listen(Session, "do_orm_execute", _label_relationship_load)


def _label_relationship_load(orm_execute_state: Any) -> None:
    """Carry a relationship's name, such as ``Order.items``, to its query, so an
    N+1 warning can name a lazy load even when no application code is running."""
    try:
        if orm_execute_state.is_relationship_load:
            orm_execute_state.update_execution_options(
                **{_RELATIONSHIP: str(orm_execute_state.loader_strategy_path[-1])[:200]}
            )
    except Exception:
        # A diagnostic must never fail the query it describes.
        pass


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
        options = getattr(context, "execution_options", None) or {}
        call.finish(statement=statement, relationship=options.get(_RELATIONSHIP))


def _error(context: Any) -> None:
    """Observe failed cursor attempts without changing SQLAlchemy's exception chain."""
    call = getattr(context.execution_context, _STARTED, None)
    if call is not None:
        from sqlalchemy.exc import IntegrityError
        category = ("integrity_error" if isinstance(context.sqlalchemy_exception, IntegrityError)
                    else "disconnect" if context.is_disconnect else "execution_error")
        call.finish(error=category)


def _checkout(dbapi_conn: Any, connection_record: Any, connection_proxy: Any) -> None:
    state = get_request_state()
    if state is not None:
        info = getattr(connection_record, "info", None)
        if isinstance(info, dict):
            info[_CHECKOUT] = (weakref.ref(state), time.perf_counter())
        state.on_db_checkout()


def _checkin(dbapi_conn: Any, connection_record: Any) -> None:
    info = getattr(connection_record, "info", None)
    checkout = info.pop(_CHECKOUT, None) if isinstance(info, dict) else None
    if checkout is not None:
        state_ref, started = checkout
        state = state_ref()
        if state is not None:
            state.on_db_checkin(max(0.0, time.perf_counter() - started))


_QUERY_HOOKS = (("before_cursor_execute", _before), ("after_cursor_execute", _after),
                ("handle_error", _error))
_POOL_HOOKS = (("checkout", _checkout), ("checkin", _checkin))


__all__ = ["SQLALCHEMY_INSTRUMENTATION", "count_queries"]
