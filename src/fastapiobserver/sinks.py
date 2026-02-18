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
import logging
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Protocol, runtime_checkable

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
    _SINK_REGISTRY.clear()


def discover_entry_point_sinks() -> None:
    """Auto-discover sinks from the ``fastapiobserver.log_sinks`` entry-point group."""
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
    except Exception:
        pass


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
        self._error_count = 0
        self._drop_count = 0

        self._worker = threading.Thread(
            target=self._flush_worker,
            daemon=True,
            name="logtail-flush-worker",
        )
        self._worker.start()
        atexit.register(self._shutdown_flush)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            formatted = self.format(record)
            try:
                self._queue.put_nowait(formatted)
            except queue.Full:
                # Drop oldest to make room (bounded memory)
                try:
                    self._queue.get_nowait()
                    self._drop_count += 1
                except queue.Empty:
                    pass
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
                    self._error_count += 1
                    return  # Don't retry client errors
            except Exception:
                pass

            # Exponential backoff
            if attempt < self._max_retries - 1:
                time.sleep(min(2**attempt * 0.5, 10))

        self._error_count += 1

    def _shutdown_flush(self) -> None:
        self._shutdown.set()
        self._worker.join(timeout=5)
        self._drain_and_send()

    @property
    def error_count(self) -> int:
        return self._error_count

    @property
    def drop_count(self) -> int:
        return self._drop_count

    def close(self) -> None:
        self._shutdown_flush()
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
    ) -> None:
        self._source_token = source_token
        self._endpoint = endpoint or self.ENDPOINT
        self._batch_size = batch_size
        self._flush_interval = flush_interval

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
        handler.setFormatter(formatter)
        return handler


# =====================================================================
# Helpers for building handler list from settings
# =====================================================================


def build_sink_handlers(
    settings: Any,
    formatter: logging.Formatter,
) -> list[logging.Handler]:
    """Build the list of output handlers from settings + registered sinks.

    Called by ``logging.setup_logging()``.  This is the single point of
    assembly (Dependency Inversion: high-level logging depends on this
    abstraction, not on concrete handler constructors).
    """
    handlers: list[logging.Handler] = []

    # Always add stdout
    handlers.append(StdoutSink().create_handler(formatter))

    # Add rotating file if configured
    if settings.log_dir:
        handlers.append(
            RotatingFileSink(log_dir=settings.log_dir).create_handler(formatter)
        )

    # Add Logtail if configured
    if settings.logtail_enabled and settings.logtail_source_token:
        handlers.append(
            LogtailSink(
                source_token=settings.logtail_source_token,
                batch_size=settings.logtail_batch_size,
                flush_interval=settings.logtail_flush_interval,
            ).create_handler(formatter)
        )

    # Add entry-point discovered sinks
    discover_entry_point_sinks()
    for sink in _SINK_REGISTRY.values():
        try:
            handlers.append(sink.create_handler(formatter))
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


__all__ = [
    "LogSink",
    "LogtailSink",
    "RotatingFileSink",
    "StdoutSink",
    "build_sink_handlers",
    "clear_sinks",
    "discover_entry_point_sinks",
    "get_registered_sinks",
    "register_sink",
    "unregister_sink",
]
