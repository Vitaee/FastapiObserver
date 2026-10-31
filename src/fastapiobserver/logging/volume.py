"""JSON log volume by route, exported as ``fastapiobserver_log_bytes_total``."""

from __future__ import annotations

import logging
import threading

from ..request_context import UNMATCHED_ROUTE, get_request_state, route_template

# Label for lines written outside a request, such as startup and background work.
NO_REQUEST = "<none>"

_LOCK = threading.Lock()
_BYTES_BY_ROUTE: dict[str, int] = {}
# Off until the Prometheus collector that exports the counts is registered.
COUNTING = False


def start_counting() -> None:
    global COUNTING
    COUNTING = True


def current_route() -> str:
    """The route label for a record created now, read on the caller's thread."""
    request_state = get_request_state()
    if request_state is None:
        return NO_REQUEST
    return route_template(request_state.scope) or UNMATCHED_ROUTE


def count_log_bytes(record: logging.LogRecord, line: str) -> None:
    route = getattr(record, "_obs_route", None)
    # Not counting, or every sink formats the same record and it's counted once.
    if route is None or getattr(record, "_obs_bytes_counted", False):
        return
    record._obs_bytes_counted = True
    size = len(line) if line.isascii() else len(line.encode())
    with _LOCK:
        _BYTES_BY_ROUTE[route] = _BYTES_BY_ROUTE.get(route, 0) + size


def get_log_bytes_by_route() -> dict[str, int]:
    """Bytes of JSON log lines written so far, by the route that wrote them."""
    with _LOCK:
        return dict(_BYTES_BY_ROUTE)


__all__ = [
    "NO_REQUEST",
    "count_log_bytes",
    "current_route",
    "get_log_bytes_by_route",
    "start_counting",
]
