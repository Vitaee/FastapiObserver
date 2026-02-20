from __future__ import annotations

import logging
import sys

class StdoutSink:
    """Writes JSON logs to stdout (always enabled)."""

    @property
    def name(self) -> str:
        return "stdout"

    def create_handler(self, formatter: logging.Formatter) -> logging.Handler:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        return handler


class RotatingFileSink:
    """Writes JSON logs to a rotating file.

    Parameters match ``logging.handlers.RotatingFileHandler``.
    """

    def __init__(
        self,
        log_dir: str,
        filename: str = "app.log",
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
    ) -> None:
        self._log_dir = log_dir
        self._filename = filename
        self._max_bytes = max_bytes
        self._backup_count = backup_count

    @property
    def name(self) -> str:
        return "rotating_file"

    def create_handler(self, formatter: logging.Formatter) -> logging.Handler:
        import os
        from logging.handlers import RotatingFileHandler

        os.makedirs(self._log_dir, exist_ok=True)
        filepath = os.path.join(self._log_dir, self._filename)
        handler = RotatingFileHandler(
            filepath,
            maxBytes=self._max_bytes,
            backupCount=self._backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(formatter)
        return handler

__all__ = ["StdoutSink", "RotatingFileSink"]
