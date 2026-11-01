from __future__ import annotations

from .builtin import RotatingFileSink, StdoutSink
from .factory import build_sink_handlers
from .protocol import LogSink
from .registry import clear_sinks, get_registered_sinks, register_sink, unregister_sink

__all__ = [
    "LogSink",
    "RotatingFileSink",
    "StdoutSink",
    "build_sink_handlers",
    "clear_sinks",
    "get_registered_sinks",
    "register_sink",
    "unregister_sink",
]
