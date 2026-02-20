from __future__ import annotations

import logging
import queue
import threading
import atexit
import urllib.request
import urllib.error
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .dlq import LogtailDLQ

_LOGGER = logging.getLogger("fastapiobserver.sinks")

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
        from .dlq import LogtailDLQ
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

__all__ = ["_LogtailHandler"]
