from __future__ import annotations

import logging
from typing import Any
from .protocol import LogSink
from .registry import register_sink

_LOGGER = logging.getLogger("fastapiobserver.sinks")
_DISCOVERED: bool = False

def _set_discovered(val: bool) -> None:
    global _DISCOVERED
    _DISCOVERED = val

def discover_entry_point_sinks() -> None:
    """Auto-discover sinks from the ``fastapiobserver.log_sinks`` entry-point group."""
    global _DISCOVERED
    if _DISCOVERED:
        return
    try:
        from importlib.metadata import entry_points

        sinks_group: Any
        try:
            sinks_group = entry_points(group="fastapiobserver.log_sinks")
        except TypeError:
            eps = entry_points()
            if hasattr(eps, "select"):
                sinks_group = eps.select(group="fastapiobserver.log_sinks")
            else:
                sinks_group = ()
        for ep in sinks_group:
            try:
                sink_factory = ep.load()
                sink = sink_factory()
                if isinstance(sink, LogSink):
                    register_sink(sink)
                    _LOGGER.debug(
                        "sinks.entry_point.loaded",
                        extra={
                            "event": {"sink_name": sink.name, "entry_point": ep.name},
                            "_skip_enrichers": True,
                        },
                    )
            except Exception:
                _LOGGER.warning(
                    "sinks.entry_point.failed",
                    exc_info=True,
                    extra={
                        "event": {"entry_point": ep.name},
                        "_skip_enrichers": True,
                    },
                )
        _DISCOVERED = True
    except Exception:
        _LOGGER.debug(
            "sinks.entry_point.discover_failed",
            exc_info=True,
            extra={"_skip_enrichers": True},
        )

__all__ = ["discover_entry_point_sinks"]
