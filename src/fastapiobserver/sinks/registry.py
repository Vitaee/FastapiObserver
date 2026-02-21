from __future__ import annotations


import threading

from .protocol import LogSink

_SINK_REGISTRY: dict[str, LogSink] = {}
_SINK_REGISTRY_LOCK = threading.RLock()

def register_sink(sink: LogSink) -> None:
    """Register a custom log sink.  Sinks are keyed by ``sink.name``."""
    with _SINK_REGISTRY_LOCK:
        _SINK_REGISTRY[sink.name] = sink

def unregister_sink(name: str) -> None:
    """Remove a previously registered sink."""
    with _SINK_REGISTRY_LOCK:
        _SINK_REGISTRY.pop(name, None)

def get_registered_sinks() -> dict[str, LogSink]:
    """Return a snapshot of currently registered sinks."""
    with _SINK_REGISTRY_LOCK:
        return dict(_SINK_REGISTRY)

def clear_sinks() -> None:
    """Remove all registered sinks (useful for testing)."""
    from .discovery import _set_discovered
    _set_discovered(False)
    with _SINK_REGISTRY_LOCK:
        _SINK_REGISTRY.clear()

__all__ = ["register_sink", "unregister_sink", "get_registered_sinks", "clear_sinks"]
