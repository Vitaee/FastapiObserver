from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .logtail.handler import _LogtailHandler

def _iter_logtail_handlers(handler: logging.Handler) -> list[_LogtailHandler]:
    from .logtail.handler import _LogtailHandler
    stack: list[logging.Handler] = [handler]
    resolved: list[_LogtailHandler] = []
    visited: set[int] = set()
    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)

        if isinstance(current, _LogtailHandler):
            resolved.append(current)
            continue

        delegate = getattr(current, "_delegate", None)
        if isinstance(delegate, logging.Handler):
            stack.append(delegate)

    return resolved


def get_logtail_dlq_stats() -> dict[str, int]:
    """Return an aggregated snapshot of active Logtail DLQ statistics."""
    stats = {
        "written_queue_overflow": 0,
        "written_send_failed": 0,
        "failures": 0,
        "bytes": 0,
    }
    try:
        from ..logging import get_managed_output_handlers
    except Exception:
        return stats

    for handler in get_managed_output_handlers():
        for logtail_handler in _iter_logtail_handlers(handler):
            h_stats = logtail_handler.dlq_stats()
            stats["written_queue_overflow"] += h_stats["written_overflow"]
            stats["written_send_failed"] += h_stats["written_failed"]
            stats["failures"] += h_stats["failures"]
            stats["bytes"] += h_stats["bytes"]

    return stats

__all__ = ["get_logtail_dlq_stats"]
