"""Records below the log level, held per request and written only if it fails.

With ``LOG_LEVEL=INFO`` and ``LOG_LEVEL_ON_ERROR=DEBUG``, a request's ``DEBUG``
records stay in memory while it runs. If it ends in a 5xx or an unhandled
exception they are written before its ``request.failed`` line; otherwise they
are dropped.
"""

from __future__ import annotations

import logging
from collections import deque

from ..request_context import get_request_state
from . import state

# ponytail: fixed cap per request, newest kept; make it a setting if 200 isn't enough.
_MAX_HELD_RECORDS = 200


class HeldLogFilter(logging.Filter):
    """Holds records below *level* for the current request instead of passing them on.

    It is the queue handler's last filter, so held records already carry their
    request ID and trace context. Outside a request they are dropped, as the log
    level would have dropped them. *lowest_level* is ``LOG_LEVEL_ON_ERROR``.
    """

    def __init__(self, handler: logging.Handler, level: int, lowest_level: int) -> None:
        super().__init__()
        self.handler = handler
        self.level = level
        self.lowest_level = lowest_level

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= self.level:
            return True
        request_state = get_request_state()
        if request_state is not None:
            if request_state.held_logs is None:
                request_state.held_logs = deque(maxlen=_MAX_HELD_RECORDS)
            request_state.held_logs.append(record)
        return False


def write_held_logs() -> None:
    """Write the current request's held records. Called when the request failed."""
    held_filter = state._HELD_LOG_FILTER
    request_state = get_request_state()
    if held_filter is None or request_state is None or not request_state.held_logs:
        return
    held, request_state.held_logs = request_state.held_logs, None
    for record in held:
        # emit(), not handle(): the filters already ran when the record was held.
        held_filter.handler.emit(record)


__all__ = ["HeldLogFilter", "write_held_logs"]
