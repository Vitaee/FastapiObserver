"""How long the garbage collector has stopped the process, so a request can show it.

A collection stops every thread until it ends: on a large heap, an old-generation
collection can take hundreds of milliseconds or more, and Python reports no
duration for it. The callback only adds to a running total. Logging, counting or
locking there could deadlock with whatever code the collection interrupted, so
requests read the total as they start and end, outside any collection.
"""

from __future__ import annotations

import gc
import time
from typing import Any

# ponytail: fixed threshold; make it a setting if it's too noisy or quiet for someone.
GC_PAUSE_SECONDS = 0.1
_total_seconds = 0.0
_started = 0.0


def _on_gc(phase: str, info: dict[str, Any]) -> None:
    # Collections never overlap, so these globals need no lock.
    global _total_seconds, _started
    if phase == "start":
        _started = time.perf_counter()
    else:
        _total_seconds += time.perf_counter() - _started


def gc_seconds() -> float:
    """Seconds spent in garbage collection since ``watch_gc()`` was first called."""
    return _total_seconds


def watch_gc() -> None:
    """Start timing collections, once per process."""
    if _on_gc not in gc.callbacks:
        gc.callbacks.append(_on_gc)


__all__ = ["GC_PAUSE_SECONDS", "gc_seconds", "watch_gc"]
