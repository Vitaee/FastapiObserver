from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

@runtime_checkable
class LogSink(Protocol):
    """Minimal interface every log sink must satisfy."""

    @property
    def name(self) -> str: ...

    def create_handler(self, formatter: logging.Formatter) -> logging.Handler: ...

__all__ = ["LogSink"]
