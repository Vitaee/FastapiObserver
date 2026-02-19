from __future__ import annotations

import logging

from fastapiobserver.logging import SinkCircuitBreakerHandler


class _ScriptedHandler(logging.Handler):
    def __init__(self, outcomes: list[bool]) -> None:
        super().__init__()
        self._outcomes = outcomes
        self.calls = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.calls += 1
        index = min(self.calls - 1, len(self._outcomes) - 1)
        if not self._outcomes[index]:
            raise RuntimeError("sink failed")


def _record(msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="tests.breaker",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_sink_circuit_breaker_opens_and_skips_while_open() -> None:
    now = [0.0]
    delegate = _ScriptedHandler([False, False, True])
    breaker = SinkCircuitBreakerHandler(
        sink_name="test-sink",
        delegate=delegate,
        failure_threshold=2,
        recovery_timeout_seconds=30.0,
        clock=lambda: now[0],
    )

    breaker.emit(_record("a"))
    breaker.emit(_record("b"))
    breaker.emit(_record("c"))

    snapshot = breaker.snapshot()
    assert delegate.calls == 2
    assert snapshot.state == "open"
    assert snapshot.failures_total == 2
    assert snapshot.skipped_total == 1
    assert snapshot.opens_total == 1
    assert snapshot.half_open_total == 0
    assert snapshot.closes_total == 0


def test_sink_circuit_breaker_half_open_success_closes_breaker() -> None:
    now = [0.0]
    delegate = _ScriptedHandler([False, True])
    breaker = SinkCircuitBreakerHandler(
        sink_name="test-sink",
        delegate=delegate,
        failure_threshold=1,
        recovery_timeout_seconds=5.0,
        clock=lambda: now[0],
    )

    breaker.emit(_record("first"))
    now[0] = 6.0
    breaker.emit(_record("second"))

    snapshot = breaker.snapshot()
    assert delegate.calls == 2
    assert snapshot.state == "closed"
    assert snapshot.failures_total == 1
    assert snapshot.handled_total == 1
    assert snapshot.opens_total == 1
    assert snapshot.half_open_total == 1
    assert snapshot.closes_total == 1
