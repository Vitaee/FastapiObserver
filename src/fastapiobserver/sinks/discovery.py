from __future__ import annotations

import importlib.metadata
import logging
import threading
from collections.abc import Collection

from .protocol import LogSink
from .registry import register_sink

_LOGGER = logging.getLogger("fastapiobserver.sinks")
_LOADED: set[str] = set()
_LOADED_LOCK = threading.Lock()


def _forget_loaded() -> None:
    with _LOADED_LOCK:
        _LOADED.clear()


def discover_entry_point_sinks(enabled: Collection[str] = ()) -> None:
    """Register the ``fastapiobserver.log_sinks`` entry points named in *enabled*.

    Other entry points are never imported, so an installed package can't
    receive your logs unless you list it in ``OBS_LOG_SINKS``.
    """
    with _LOADED_LOCK:
        try:
            group = importlib.metadata.entry_points(group="fastapiobserver.log_sinks")
        except Exception:
            _LOGGER.debug(
                "sinks.entry_point.discover_failed",
                exc_info=True,
                extra={"_skip_enrichers": True},
            )
            return

        names = {ep.name for ep in group}
        for message, skipped in (
            ("sinks.entry_point.not_enabled", names - set(enabled)),
            ("sinks.entry_point.missing", set(enabled) - names),
        ):
            if skipped:
                _LOGGER.warning(
                    message,
                    extra={
                        "event": {"entry_points": sorted(skipped), "setting": "OBS_LOG_SINKS"},
                        "_skip_enrichers": True,
                    },
                )

        for ep in group:
            if ep.name not in enabled or ep.name in _LOADED:
                continue
            try:
                sink = ep.load()()
                if isinstance(sink, LogSink):
                    register_sink(sink)
                    _LOADED.add(ep.name)
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

__all__ = ["discover_entry_point_sinks"]
