"""Each request's SQLAlchemy queries, counted from SQLAlchemy's own events.

The request line's ``db`` totals and the ``db.n_plus_one`` warning then work
without tracing, and see every request instead of only sampled ones. Queries
other clients make, such as Redis or a driver used directly, are counted from
their OpenTelemetry spans when tracing is on.
"""

from __future__ import annotations

import time
from typing import Any

from .request_context import get_request_state

# SQLAlchemy's instrumentation for OpenTelemetry; its spans would count twice.
SQLALCHEMY_INSTRUMENTATION = "opentelemetry.instrumentation.sqlalchemy"
_STARTED = "_fastapiobserver_started"


def count_queries(engine: Any) -> None:
    """Add each query a sync SQLAlchemy ``Engine`` runs to its request's ``db`` totals."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    if isinstance(engine, Engine) and not event.contains(engine, "after_cursor_execute", _after):
        event.listen(engine, "before_cursor_execute", _before)
        event.listen(engine, "after_cursor_execute", _after)


def _before(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    if context is not None:
        setattr(context, _STARTED, time.perf_counter())


def _after(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    state = get_request_state()
    if state is None:
        return
    started = getattr(context, _STARTED, None)
    state.add_call("db", time.perf_counter() - started if started is not None else 0.0, statement)


__all__ = ["SQLALCHEMY_INSTRUMENTATION", "count_queries"]
