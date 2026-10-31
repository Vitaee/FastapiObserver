"""Full pools: requests wait, latency climbs, CPU stays low.

Two pools make requests wait without using CPU: AnyIO's thread pool, which runs
sync endpoints and dependencies, and SQLAlchemy connection pools. Neither AnyIO
nor SQLAlchemy has a hook for how long one call waited, so this reports when a
pool is full: a warning at most once a minute per pool, checked as each request
starts, and gauges on ``/metrics``. Requests still start when every thread and
connection is stuck, so a deadlock is reported too.
"""

from __future__ import annotations

import logging
import math
import time
import weakref
from typing import Any

_LOGGER = logging.getLogger("fastapiobserver.pools")
# ponytail: fixed; make it a setting if once a minute is too noisy or too quiet.
_WARN_INTERVAL_SECONDS = 60.0

_thread_limiter: Any = None  # AnyIO's default limiter, captured on the event loop
_db_pools: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()  # engine: label
_last_warned: dict[str, float] = {}


def watch_thread_pool() -> None:
    """Remember AnyIO's default thread limiter. Call it on the event loop."""
    global _thread_limiter
    from anyio.to_thread import current_default_thread_limiter

    _thread_limiter = current_default_thread_limiter()


def watch_db_pool(engine: Any) -> None:
    """Watch a sync SQLAlchemy engine's pool, if it has a limit (``QueuePool``)."""
    if callable(getattr(engine.pool, "checkedout", None)):
        _db_pools[engine] = engine.url.render_as_string(hide_password=True)


def thread_pool_usage() -> tuple[int, float, int] | None:
    """Busy threads, the limit and calls waiting; None until the app has started."""
    if _thread_limiter is None:
        return None
    stats = _thread_limiter.statistics()
    return stats.borrowed_tokens, stats.total_tokens, stats.tasks_waiting


def db_pool_usage() -> list[tuple[str, int, float]]:
    """Each watched engine's label, checked-out connections and limit."""
    return [
        (label, engine.pool.checkedout(), _db_pool_limit(engine.pool))
        for engine, label in list(_db_pools.items())
    ]


def check_pools() -> None:
    """Warn about full pools. Called as each request starts."""
    limiter = _thread_limiter
    # Cheap test first; statistics() copies the borrower list.
    if limiter is not None and limiter.borrowed_tokens >= limiter.total_tokens:
        busy, limit, waiting = thread_pool_usage() or (0, 0, 0)
        if waiting:
            _warn(
                "threadpool.saturated",
                "threadpool",
                {"busy": busy, "limit": limit, "waiting": waiting},
            )
    for label, checked_out, limit in db_pool_usage():
        if checked_out >= limit:
            _warn(
                "db.pool_exhausted",
                label,
                {"engine": label, "checked_out": checked_out, "limit": limit},
            )


def _db_pool_limit(pool: Any) -> float:
    # max_overflow has no public accessor; without it, or at -1, there's no limit.
    max_overflow = getattr(pool, "_max_overflow", -1)
    return math.inf if max_overflow < 0 else pool.size() + max_overflow


def _warn(message: str, pool: str, event: dict[str, Any]) -> None:
    now = time.monotonic()
    if now - _last_warned.get(pool, -math.inf) < _WARN_INTERVAL_SECONDS:
        return
    _last_warned[pool] = now
    _LOGGER.warning(message, extra={"event": event, "_skip_enrichers": True})


__all__ = [
    "check_pools",
    "db_pool_usage",
    "thread_pool_usage",
    "watch_db_pool",
    "watch_thread_pool",
]
