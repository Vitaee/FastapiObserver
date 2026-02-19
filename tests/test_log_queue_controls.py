from __future__ import annotations

import logging
import queue

from fastapiobserver.logging import LogQueueTelemetry, OverflowPolicyQueueHandler


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="tests.queue",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_drop_newest_policy_drops_latest_record_when_full() -> None:
    telemetry = LogQueueTelemetry()
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=1)
    telemetry.reset(
        log_queue=log_queue,
        queue_capacity=1,
        overflow_policy="drop_newest",
    )
    handler = OverflowPolicyQueueHandler(
        log_queue,
        overflow_policy="drop_newest",
        block_timeout_seconds=0.01,
        telemetry=telemetry,
    )

    handler.enqueue(_record("first"))
    handler.enqueue(_record("second"))

    kept = log_queue.get_nowait()
    assert kept.msg == "first"
    snapshot = telemetry.snapshot()
    assert snapshot.enqueued_total == 1
    assert snapshot.dropped_total == 1
    assert snapshot.dropped_oldest_total == 0
    assert snapshot.dropped_newest_total == 1
    assert snapshot.blocked_total == 0


def test_drop_oldest_policy_keeps_latest_record_when_full() -> None:
    telemetry = LogQueueTelemetry()
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=1)
    telemetry.reset(
        log_queue=log_queue,
        queue_capacity=1,
        overflow_policy="drop_oldest",
    )
    handler = OverflowPolicyQueueHandler(
        log_queue,
        overflow_policy="drop_oldest",
        block_timeout_seconds=0.01,
        telemetry=telemetry,
    )

    handler.enqueue(_record("first"))
    handler.enqueue(_record("second"))

    kept = log_queue.get_nowait()
    assert kept.msg == "second"
    snapshot = telemetry.snapshot()
    assert snapshot.enqueued_total == 2
    assert snapshot.dropped_total == 1
    assert snapshot.dropped_oldest_total == 1
    assert snapshot.dropped_newest_total == 0
    assert snapshot.blocked_total == 0


def test_block_policy_times_out_and_drops_newest_when_queue_stays_full() -> None:
    telemetry = LogQueueTelemetry()
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=1)
    telemetry.reset(
        log_queue=log_queue,
        queue_capacity=1,
        overflow_policy="block",
    )
    handler = OverflowPolicyQueueHandler(
        log_queue,
        overflow_policy="block",
        block_timeout_seconds=0.01,
        telemetry=telemetry,
    )

    handler.enqueue(_record("first"))
    handler.enqueue(_record("second"))

    kept = log_queue.get_nowait()
    assert kept.msg == "first"
    snapshot = telemetry.snapshot()
    assert snapshot.enqueued_total == 1
    assert snapshot.dropped_total == 1
    assert snapshot.dropped_oldest_total == 0
    assert snapshot.dropped_newest_total == 1
    assert snapshot.blocked_total == 1
    assert snapshot.block_timeout_total == 1
