from __future__ import annotations

import logging
import logging.handlers
import threading
import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_breaker import SinkCircuitBreakerHandler

_LOGGING_LOCK = threading.Lock()
_QUEUE_LISTENER: logging.handlers.QueueListener | None = None
_MANAGED_OUTPUT_HANDLERS: list[logging.Handler] = []
_SINK_CIRCUIT_BREAKERS: list["SinkCircuitBreakerHandler"] = []
_MANAGED_HANDLERS: weakref.WeakSet[logging.Handler] = weakref.WeakSet()
_ATEXIT_SHUTDOWN_REGISTERED = False
