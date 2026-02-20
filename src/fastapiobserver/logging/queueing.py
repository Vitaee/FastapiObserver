from __future__ import annotations

import logging
import logging.handlers
import queue
import threading
from dataclasses import dataclass
from typing import cast

from ..config import LogQueueOverflowPolicy


@dataclass(frozen=True)
class LogQueueStatsSnapshot:
    queue_size: int
    queue_capacity: int
    overflow_policy: LogQueueOverflowPolicy
    enqueued_total: int
    dropped_total: int
    dropped_oldest_total: int
    dropped_newest_total: int
    blocked_total: int
    block_timeout_total: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "queue_size": self.queue_size,
            "queue_capacity": self.queue_capacity,
            "overflow_policy": self.overflow_policy,
            "enqueued_total": self.enqueued_total,
            "dropped_total": self.dropped_total,
            "dropped_oldest_total": self.dropped_oldest_total,
            "dropped_newest_total": self.dropped_newest_total,
            "blocked_total": self.blocked_total,
            "block_timeout_total": self.block_timeout_total,
        }


class LogQueueTelemetry:
    """Thread-safe in-memory counters for queue pressure visibility."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset(
            log_queue=None,
            queue_capacity=0,
            overflow_policy="drop_oldest",
        )

    def reset(
        self,
        *,
        log_queue: queue.Queue[logging.LogRecord] | None,
        queue_capacity: int,
        overflow_policy: LogQueueOverflowPolicy,
    ) -> None:
        with self._lock:
            self._queue = log_queue
            self._queue_capacity = queue_capacity
            self._overflow_policy = overflow_policy
            self._enqueued_total = 0
            self._dropped_total = 0
            self._dropped_oldest_total = 0
            self._dropped_newest_total = 0
            self._blocked_total = 0
            self._block_timeout_total = 0

    def record_enqueued(self) -> None:
        with self._lock:
            self._enqueued_total += 1

    def record_drop_oldest(self) -> None:
        with self._lock:
            self._dropped_total += 1
            self._dropped_oldest_total += 1

    def record_drop_newest(self) -> None:
        with self._lock:
            self._dropped_total += 1
            self._dropped_newest_total += 1

    def record_blocked(self) -> None:
        with self._lock:
            self._blocked_total += 1

    def record_block_timeout(self) -> None:
        with self._lock:
            self._block_timeout_total += 1

    def snapshot(self) -> LogQueueStatsSnapshot:
        with self._lock:
            queue_ref = self._queue
            queue_capacity = self._queue_capacity
            overflow_policy = self._overflow_policy
            enqueued_total = self._enqueued_total
            dropped_total = self._dropped_total
            dropped_oldest_total = self._dropped_oldest_total
            dropped_newest_total = self._dropped_newest_total
            blocked_total = self._blocked_total
            block_timeout_total = self._block_timeout_total

        queue_size = _safe_queue_size(queue_ref)
        if queue_ref is not None:
            queue_capacity = queue_ref.maxsize

        return LogQueueStatsSnapshot(
            queue_size=queue_size,
            queue_capacity=queue_capacity,
            overflow_policy=overflow_policy,
            enqueued_total=enqueued_total,
            dropped_total=dropped_total,
            dropped_oldest_total=dropped_oldest_total,
            dropped_newest_total=dropped_newest_total,
            blocked_total=blocked_total,
            block_timeout_total=block_timeout_total,
        )


_LOG_QUEUE_TELEMETRY = LogQueueTelemetry()


class OverflowPolicyQueueHandler(logging.handlers.QueueHandler):
    """Queue handler with explicit overflow policy and queue telemetry."""

    def __init__(
        self,
        log_queue: queue.Queue[logging.LogRecord],
        *,
        overflow_policy: LogQueueOverflowPolicy,
        block_timeout_seconds: float,
        telemetry: LogQueueTelemetry,
    ) -> None:
        super().__init__(log_queue)
        self.overflow_policy = overflow_policy
        self.block_timeout_seconds = block_timeout_seconds
        self.telemetry = telemetry

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
            self.telemetry.record_enqueued()
            return
        except queue.Full:
            pass

        if self.overflow_policy == "drop_newest":
            self.telemetry.record_drop_newest()
            return

        if self.overflow_policy == "drop_oldest":
            self._drop_oldest_then_enqueue(record)
            return

        self._block_then_enqueue(record)

    def _drop_oldest_then_enqueue(self, record: logging.LogRecord) -> None:
        log_queue = cast(queue.Queue[logging.LogRecord], self.queue)
        try:
            log_queue.get_nowait()
            self.telemetry.record_drop_oldest()
        except queue.Empty:
            pass

        try:
            log_queue.put_nowait(record)
            self.telemetry.record_enqueued()
        except queue.Full:
            # Another producer can win the free slot; newest record is dropped.
            self.telemetry.record_drop_newest()

    def _block_then_enqueue(self, record: logging.LogRecord) -> None:
        log_queue = cast(queue.Queue[logging.LogRecord], self.queue)
        self.telemetry.record_blocked()
        try:
            log_queue.put(
                record,
                block=True,
                timeout=self.block_timeout_seconds,
            )
            self.telemetry.record_enqueued()
        except queue.Full:
            self.telemetry.record_block_timeout()
            self.telemetry.record_drop_newest()


def get_log_queue_stats() -> dict[str, int | str]:
    """Return a snapshot of queue pressure counters for diagnostics/metrics."""
    return _LOG_QUEUE_TELEMETRY.snapshot().as_dict()


def _safe_queue_size(log_queue: queue.Queue[logging.LogRecord] | None) -> int:
    if log_queue is None:
        return 0
    try:
        return log_queue.qsize()
    except Exception:
        return 0
