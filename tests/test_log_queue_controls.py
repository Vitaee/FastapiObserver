from __future__ import annotations

import logging
import queue

import pytest

from fastapiobserver.logging import LogQueueTelemetry, OverflowPolicyQueueHandler


@pytest.mark.parametrize(
    ("policy", "kept", "counts"),
    [
        ("drop_newest", "first", {"enqueued_total": 1, "dropped_newest_total": 1}),
        ("drop_oldest", "second", {"enqueued_total": 2, "dropped_oldest_total": 1}),
        (
            "block",
            "first",
            {"enqueued_total": 1, "dropped_newest_total": 1, "blocked_total": 1,
             "block_timeout_total": 1},
        ),
    ],
)
def test_overflow_policy_decides_which_record_a_full_queue_keeps(
    policy: str, kept: str, counts: dict[str, int]
) -> None:
    telemetry = LogQueueTelemetry()
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=1)
    telemetry.reset(log_queue=log_queue, queue_capacity=1, overflow_policy=policy)
    handler = OverflowPolicyQueueHandler(
        log_queue, overflow_policy=policy, block_timeout_seconds=0.01, telemetry=telemetry,
    )

    handler.enqueue(logging.makeLogRecord({"msg": "first"}))
    handler.enqueue(logging.makeLogRecord({"msg": "second"}))

    assert log_queue.get_nowait().msg == kept
    snapshot = telemetry.snapshot()
    assert snapshot.dropped_total == 1
    for name in (
        "enqueued_total", "dropped_oldest_total", "dropped_newest_total", "blocked_total",
    ):
        assert getattr(snapshot, name) == counts.get(name, 0), name
    if "block_timeout_total" in counts:
        assert snapshot.block_timeout_total == counts["block_timeout_total"]


def test_prepare_freezes_arguments_and_passes_other_records_through() -> None:
    handler = OverflowPolicyQueueHandler(
        queue.Queue(),
        overflow_policy="drop_oldest",
        block_timeout_seconds=1.0,
        telemetry=LogQueueTelemetry(),
    )
    plain = logging.makeLogRecord({"msg": "request.completed"})
    items = ["a"]
    with_args = logging.makeLogRecord({"msg": "items %s"})
    with_args.args = (items,)

    assert handler.prepare(plain) is plain  # nothing to freeze, so not copied
    prepared = handler.prepare(with_args)
    items.append("b")  # changed after the call, before the listener formats it
    assert prepared is not with_args
    assert (prepared.getMessage(), prepared.args) == ("items ['a']", None)


@pytest.mark.parametrize(
    ("msg", "arg", "message_end"),
    [
        ("%(body)s", {"body": "x" * 40_000}, " characters cut]"),  # mapping argument
        ("%.5s", "x" * 40_000, "xxxxx"),  # long argument, short message: frozen, not cut
    ],
)
def test_cap_message_measures_arguments_and_formats_only_long_ones(
    msg: str, arg: object, message_end: str
) -> None:
    from fastapiobserver.logging.queueing import MAX_MESSAGE_CHARS, cap_message

    record = logging.LogRecord("tests", logging.INFO, __file__, 1, msg, (arg,), None)
    capped = cap_message(record)

    assert capped is not record and capped.args is None
    assert capped.getMessage().endswith(message_end)
    assert len(capped.getMessage()) <= MAX_MESSAGE_CHARS
    assert cap_message(capped) is capped
