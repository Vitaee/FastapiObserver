"""Pluggable log sink architecture (Open/Closed + Interface Segregation).

This module defines the ``LogSink`` protocol and provides built-in sink
implementations.  Third-party sinks can be registered via the entry-point
group ``fastapiobserver.log_sinks`` or by calling ``register_sink()``
directly.

Design rationale
----------------
* **Protocol-based interface** — sinks only need ``create_handler()`` and
  ``name`` property; no heavyweight ABC to couple with.
* **Per-sink failure isolation** — each sink's handler is independent;
  one failing sink cannot block others.
* **Batching with backpressure** — the ``LogtailSink`` flushes via a
  background thread with bounded queue and configurable drop policy.
"""

from __future__ import annotations

import atexit
import gzip
import json
import logging
import logging.handlers
import os
import queue
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .config import ObservabilitySettings

_LOGGER = logging.getLogger("fastapiobserver.sinks")


# =====================================================================
# Protocol (Interface Segregation)
# =====================================================================


@runtime_checkable
class LogSink(Protocol):
    """Minimal interface every log sink must satisfy."""

    @property
    def name(self) -> str: ...

    def create_handler(self, formatter: logging.Formatter) -> logging.Handler: ...


# =====================================================================
# Sink Registry (Open/Closed — extensible without modifying core)
# =====================================================================

_SINK_REGISTRY: dict[str, LogSink] = {}


def register_sink(sink: LogSink) -> None:
    """Register a custom log sink.  Sinks are keyed by ``sink.name``."""
    _SINK_REGISTRY[sink.name] = sink


def unregister_sink(name: str) -> None:
    """Remove a previously registered sink."""
    _SINK_REGISTRY.pop(name, None)


def get_registered_sinks() -> dict[str, LogSink]:
    """Return a snapshot of currently registered sinks."""
    return dict(_SINK_REGISTRY)


def clear_sinks() -> None:
    """Remove all registered sinks (useful for testing)."""
    global _DISCOVERED
    _DISCOVERED = False
    _SINK_REGISTRY.clear()


_DISCOVERED: bool = False

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


# =====================================================================
# Built-in sinks
# =====================================================================


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


# =====================================================================
# Logtail (Better Stack) sink — batched HTTP shipping
# =====================================================================


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

    def close(self) -> None:
        with self._lock:
            try:
                self._handler.close()
            except Exception:
                pass


class _LogtailHandler(logging.Handler):
    """Handler that batches and ships JSON logs to Logtail via HTTP.

    Design
    ------
    * A bounded ``queue.SimpleQueue`` collects formatted log strings.
    * A daemon thread flushes batches to Logtail at *flush_interval* or
      when the queue reaches *batch_size*.
    * Retries with exponential backoff on transient HTTP failures (5xx).
    * ``atexit`` hook guarantees remaining logs are flushed on shutdown.
    * If the queue is full, the **oldest** record is dropped (bounded
      memory usage).
    """

    def __init__(
        self,
        source_token: str,
        endpoint: str,
        *,
        batch_size: int = 50,
        flush_interval: float = 2.0,
        max_queue_size: int = 10_000,
        max_retries: int = 3,
    ) -> None:
        super().__init__()
        self._source_token = source_token
        self._endpoint = endpoint
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_retries = max_retries
        self._queue: queue.Queue[str] = queue.Queue(maxsize=max_queue_size)
        self._shutdown = threading.Event()
        self._count_lock = threading.Lock()
        self._error_count = 0
        self._drop_count = 0
        self._dlq: LogtailDLQ | None = None

        self._worker = threading.Thread(
            target=self._flush_worker,
            daemon=True,
            name="logtail-flush-worker",
        )
        self._worker.start()
        atexit.register(self._shutdown_flush)

    def enable_dlq(
        self,
        directory: str,
        filename: str,
        max_bytes: int,
        backup_count: int,
        compress: bool,
    ) -> None:
        self._dlq = LogtailDLQ(
            directory=directory,
            filename=filename,
            max_bytes=max_bytes,
            backup_count=backup_count,
            compress=compress,
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            try:
                self._queue.put_nowait(formatted)
            except queue.Full:
                # Drop oldest to make room (bounded memory)
                dropped = None
                try:
                    dropped = self._queue.get_nowait()
                except queue.Empty:
                    pass
                
                if dropped is not None:
                    with self._count_lock:
                        self._drop_count += 1
                    if self._dlq:
                        self._dlq.submit(dropped, reason="queue_overflow")
                try:
                    self._queue.put_nowait(formatted)
                except queue.Full:
                    pass
        except Exception:
            self.handleError(record)

    def _flush_worker(self) -> None:
        while not self._shutdown.is_set():
            self._shutdown.wait(timeout=self._flush_interval)
            self._drain_and_send()

    def _drain_and_send(self) -> None:
        batch: list[str] = []
        while len(batch) < self._batch_size * 2:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if not batch:
            return

        # Send in batch_size chunks
        for i in range(0, len(batch), self._batch_size):
            chunk = batch[i : i + self._batch_size]
            self._send_batch(chunk)

    def _send_batch(self, batch: list[str]) -> None:
        payload = "[" + ",".join(batch) + "]"
        data = payload.encode("utf-8")

        for attempt in range(self._max_retries):
            try:
                req = urllib.request.Request(
                    self._endpoint,
                    data=data,
                    headers={
                        "Authorization": f"Bearer {self._source_token}",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status < 300:
                        return
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    _LOGGER.debug(
                        "logtail.send.client_error",
                        extra={
                            "event": {"status": exc.code, "batch_size": len(batch)},
                            "_skip_enrichers": True,
                        },
                    )
                    with self._count_lock:
                        self._error_count += 1
                    return  # Don't retry client errors
            except Exception:
                _LOGGER.debug(
                    "logtail.send.retryable_error",
                    exc_info=True,
                    extra={
                        "event": {"attempt": attempt + 1, "batch_size": len(batch)},
                        "_skip_enrichers": True,
                    },
                )

            # Exponential backoff
            if attempt < self._max_retries - 1:
                time.sleep(min(2**attempt * 0.5, 10))

        with self._count_lock:
            self._error_count += 1
            
        if self._dlq:
            for item in batch:
                self._dlq.submit(item, reason="send_failed")

    def _shutdown_flush(self) -> None:
        self._shutdown.set()
        self._worker.join(timeout=5)
        self._drain_and_send()

    @property
    def error_count(self) -> int:
        with self._count_lock:
            return self._error_count

    @property
    def drop_count(self) -> int:
        with self._count_lock:
            return self._drop_count

    def dlq_stats(self) -> dict[str, int]:
        if not self._dlq:
            return {"written_overflow": 0, "written_failed": 0, "failures": 0, "bytes": 0}
        with self._dlq._lock:
            return {
                "written_overflow": self._dlq.written_overflow,
                "written_failed": self._dlq.written_failed,
                "failures": self._dlq.write_failures_total,
                "bytes": self._dlq.bytes_total,
            }

    def close(self) -> None:
        self._shutdown_flush()
        if self._dlq:
            self._dlq.close()
        super().close()


class LogtailSink:
    """Ships structured JSON logs to Logtail (Better Stack) via HTTP.

    Example
    -------
    >>> settings = ObservabilitySettings(
    ...     logtail_enabled=True,
    ...     logtail_source_token="my-token",
    ... )
    """

    ENDPOINT = "https://in.logs.betterstack.com"

    def __init__(
        self,
        source_token: str,
        *,
        endpoint: str | None = None,
        batch_size: int = 50,
        flush_interval: float = 2.0,
        dlq_enabled: bool = False,
        dlq_dir: str = ".dlq/logtail",
        dlq_filename: str = "logtail_dlq.ndjson",
        dlq_max_bytes: int = 50 * 1024 * 1024,
        dlq_backup_count: int = 10,
        dlq_compress: bool = True,
    ) -> None:
        self._source_token = source_token
        self._endpoint = endpoint or self.ENDPOINT
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._dlq_enabled = dlq_enabled
        self._dlq_dir = dlq_dir
        self._dlq_filename = dlq_filename
        self._dlq_max_bytes = dlq_max_bytes
        self._dlq_backup_count = dlq_backup_count
        self._dlq_compress = dlq_compress

    @property
    def name(self) -> str:
        return "logtail"

    def create_handler(self, formatter: logging.Formatter) -> logging.Handler:
        handler = _LogtailHandler(
            source_token=self._source_token,
            endpoint=self._endpoint,
            batch_size=self._batch_size,
            flush_interval=self._flush_interval,
        )
        if self._dlq_enabled:
            handler.enable_dlq(
                directory=self._dlq_dir,
                filename=self._dlq_filename,
                max_bytes=self._dlq_max_bytes,
                backup_count=self._dlq_backup_count,
                compress=self._dlq_compress,
            )
        handler.setFormatter(formatter)
        return handler


# =====================================================================
# Helpers for building handler list from settings
# =====================================================================


def build_sink_handlers(
    settings: "ObservabilitySettings",
    formatter: logging.Formatter,
) -> list[tuple[logging.Handler, str]]:
    """Build the list of output handlers from settings + registered sinks.

    Called by ``logging.setup_logging()``.  This is the single point of
    assembly (Dependency Inversion: high-level logging depends on this
    abstraction, not on concrete handler constructors).
    """
    handlers: list[tuple[logging.Handler, str]] = []

    # Always add stdout
    handlers.append((StdoutSink().create_handler(formatter), "stdout"))

    # Add rotating file if configured
    if settings.log_dir:
        handlers.append(
            (RotatingFileSink(log_dir=settings.log_dir).create_handler(formatter), "rotating_file")
        )

    # Add Logtail if configured
    if settings.logtail_enabled and settings.logtail_source_token:
        handlers.append(
            (
                LogtailSink(
                    source_token=settings.logtail_source_token,
                    batch_size=settings.logtail_batch_size,
                    flush_interval=settings.logtail_flush_interval,
                    dlq_enabled=settings.logtail_dlq_enabled,
                    dlq_dir=settings.logtail_dlq_dir,
                    dlq_filename=settings.logtail_dlq_filename,
                    dlq_max_bytes=settings.logtail_dlq_max_bytes,
                    dlq_backup_count=settings.logtail_dlq_backup_count,
                    dlq_compress=settings.logtail_dlq_compress,
                ).create_handler(formatter),
                "logtail",
            )
        )

    # Add entry-point discovered sinks
    discover_entry_point_sinks()
    for sink in _SINK_REGISTRY.values():
        try:
            handlers.append((sink.create_handler(formatter), sink.name))
        except Exception:
            _LOGGER.warning(
                "sinks.create_handler.failed",
                exc_info=True,
                extra={
                    "event": {"sink_name": sink.name},
                    "_skip_enrichers": True,
                },
            )

    return handlers


def _iter_logtail_handlers(handler: logging.Handler) -> list[_LogtailHandler]:
    stack: list[logging.Handler] = [handler]
    resolved: list[_LogtailHandler] = []
    visited: set[int] = set()
    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)

        if isinstance(current, _LogtailHandler):
            resolved.append(current)
            continue

        delegate = getattr(current, "_delegate", None)
        if isinstance(delegate, logging.Handler):
            stack.append(delegate)

    return resolved


def get_logtail_dlq_stats() -> dict[str, int]:
    """Return an aggregated snapshot of active Logtail DLQ statistics."""
    stats = {
        "written_queue_overflow": 0,
        "written_send_failed": 0,
        "failures": 0,
        "bytes": 0,
    }
    try:
        from .logging import get_managed_output_handlers
    except Exception:
        return stats

    for handler in get_managed_output_handlers():
        for logtail_handler in _iter_logtail_handlers(handler):
            h_stats = logtail_handler.dlq_stats()
            stats["written_queue_overflow"] += h_stats["written_overflow"]
            stats["written_send_failed"] += h_stats["written_failed"]
            stats["failures"] += h_stats["failures"]
            stats["bytes"] += h_stats["bytes"]

    return stats


__all__ = [
    "LogSink",
    "LogtailSink",
    "RotatingFileSink",
    "StdoutSink",
    "build_sink_handlers",
    "clear_sinks",
    "discover_entry_point_sinks",
    "get_logtail_dlq_stats",
    "get_registered_sinks",
    "register_sink",
    "unregister_sink",
]
