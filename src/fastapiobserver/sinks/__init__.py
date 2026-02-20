from __future__ import annotations

from .builtin import RotatingFileSink, StdoutSink
from .discovery import discover_entry_point_sinks
from .factory import build_sink_handlers
from .logtail import LogtailSink, _LogtailHandler, LogtailDLQ
from .protocol import LogSink
from .registry import clear_sinks, get_registered_sinks, register_sink, unregister_sink
from .stats import get_logtail_dlq_stats

__all__ = [
    "LogSink",
    "LogtailDLQ",
    "LogtailSink",
    "RotatingFileSink",
    "StdoutSink",
    "_LogtailHandler",
    "build_sink_handlers",
    "clear_sinks",
    "discover_entry_point_sinks",
    "get_logtail_dlq_stats",
    "get_registered_sinks",
    "register_sink",
    "unregister_sink",
]
