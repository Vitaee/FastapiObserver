from __future__ import annotations

import os
import gzip
import json
import logging
import threading
from typing import Literal, Any
from datetime import datetime, timezone
import shutil

def _gzip_rotator(source: str, dest: str) -> None:
    try:
        with open(source, "rb") as f_in:
            with gzip.open(dest, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(source)
    except Exception:
        pass


def _gzip_namer(default_name: str) -> str:
    return default_name + ".gz"


class LogtailDLQ:
    """Best-effort local durability for Logtail dropped messages.
    
    Provides thread-safe persistence using a RotatingFileHandler configured to output NDJSON.
    Supports gzip compression during rotation.
    """

    def __init__(
        self,
        directory: str,
        filename: str,
        max_bytes: int,
        backup_count: int,
        compress: bool,
    ) -> None:
        import logging.handlers

        self.directory = directory
        self.filename = filename
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.compress = compress
        
        self.written_overflow = 0
        self.written_failed = 0
        self.write_failures_total = 0
        self.bytes_total = 0
        # Re-entrant because handler error callbacks can be invoked while submit() holds the lock.
        self._lock = threading.RLock()

        os.makedirs(self.directory, exist_ok=True)
        self.filepath = os.path.join(self.directory, self.filename)

        self._handler = logging.handlers.RotatingFileHandler(
            self.filepath,
            maxBytes=self.max_bytes,
            backupCount=self.backup_count,
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        
        def _handle_error(record: logging.LogRecord) -> None:
            with self._lock:
                self.write_failures_total += 1
            
        self._handler.handleError = _handle_error  # type: ignore[method-assign]
        
        if self.compress:
            self._handler.rotator = _gzip_rotator  # type: ignore[assignment]
            self._handler.namer = _gzip_namer  # type: ignore[assignment]

    def submit(self, payload: str, reason: Literal["queue_overflow", "send_failed"]) -> None:
        with self._lock:
            try:
                parsed_payload: Any
                try:
                    parsed_payload = json.loads(payload)
                except Exception:
                    parsed_payload = payload

                envelope = json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "reason": reason,
                        "payload": parsed_payload,
                    }
                )
                
                # Leverage the standard emit to ensure maxBytes size checking
                # correctly triggers the rotator and namer overrides.
                record = logging.LogRecord(
                    name="dlq",
                    level=logging.INFO,
                    pathname="",
                    lineno=0,
                    msg=envelope,
                    args=(),
                    exc_info=None,
                )
                self._handler.emit(record)
                
                if reason == "queue_overflow":
                    self.written_overflow += 1
                else:
                    self.written_failed += 1
                self.bytes_total += len(envelope.encode("utf-8"))
            except Exception:
                self.write_failures_total += 1

    def get_stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "written_overflow": self.written_overflow,
                "written_failed": self.written_failed,
                "failures": self.write_failures_total,
                "bytes": self.bytes_total,
            }

    def close(self) -> None:
        with self._lock:
            try:
                self._handler.close()
            except Exception:
                pass

__all__ = ["LogtailDLQ"]
