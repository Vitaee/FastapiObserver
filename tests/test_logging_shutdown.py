from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time

import pytest

from fastapiobserver import ObservabilitySettings
from fastapiobserver.logging import (
    flush_logging, get_log_queue_stats, setup_logging, shutdown_logging,
)
from fastapiobserver.logging.circuit_breaker import get_sink_circuit_breaker_stats
import fastapiobserver.logging.state as state


class _BlockingSink(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.allow_write = threading.Event()
        self.closed = threading.Event()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.started.set()
        if not self.allow_write.wait(5):
            raise TimeoutError("Test sink was not released")
        self.messages.append(record.getMessage())

    def close(self) -> None:
        self.closed.set()
        super().close()


def test_flush_deadline_does_not_wait_for_a_blocked_sink():
    sink = _BlockingSink()
    setup_logging(ObservabilitySettings(), logs_mode="otlp", extra_handlers=[sink])
    try:
        logging.getLogger("test.deadline").warning("in flight")
        assert sink.started.wait(1)
        started = time.monotonic()
        assert flush_logging(timeout_seconds=0.05) is False
        assert time.monotonic() - started < 0.5
        assert not sink.closed.is_set()
    finally:
        sink.allow_write.set()
        shutdown_logging()


def test_stop_deadline_keeps_resources_for_retry():
    sink = _BlockingSink()
    setup_logging(ObservabilitySettings(), logs_mode="otlp", extra_handlers=[sink])
    listener = state._QUEUE_LISTENER
    try:
        logging.getLogger("test.deadline").warning("in flight")
        assert sink.started.wait(1)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            listener.stop(timeout_seconds=0.05)
        assert time.monotonic() - started < 0.5
        assert not sink.closed.is_set()
        assert listener._thread.is_alive()
    finally:
        sink.allow_write.set()
        shutdown_logging()


def test_failed_flush_is_not_reported_as_success(capsys):
    class FailedFlush(logging.Handler):
        def emit(self, record):
            pass

        def flush(self):
            raise OSError("SECRET")

    setup_logging(ObservabilitySettings(), logs_mode="otlp", extra_handlers=[FailedFlush()])
    assert flush_logging(timeout_seconds=0.5) is False
    assert get_sink_circuit_breaker_stats()["failedflush"]["failures_total"] >= 1
    assert "SECRET" not in capsys.readouterr().err


@pytest.mark.parametrize("action", ["shutdown", "reconfigure"])
@pytest.mark.parametrize("overflow_policy", ["drop_oldest", "drop_newest", "block"])
def test_logging_teardown_drains_a_full_queue_before_closing_sinks(
    action: str, overflow_policy: str,
) -> None:
    sink = _BlockingSink()
    setup_logging(
        ObservabilitySettings(log_queue_max_size=1, log_queue_overflow_policy=overflow_policy),
        logs_mode="otlp",
        extra_handlers=[sink],
    )
    listener = state._QUEUE_LISTENER
    assert listener is not None
    listener_thread = listener._thread
    producer = next(h for h in logging.getLogger().handlers if h in state._MANAGED_HANDLERS)
    errors: list[Exception] = []
    entered = threading.Event()

    def teardown() -> None:
        entered.set()
        try:
            if action == "shutdown":
                shutdown_logging()
            else:
                setup_logging(
                    ObservabilitySettings(),
                    logs_mode="otlp",
                    extra_handlers=[logging.NullHandler()],
                )
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=teardown, daemon=True)
    try:
        logging.getLogger("tests.shutdown").info("first")
        assert sink.started.wait(1)
        logging.getLogger("tests.shutdown").info("second")
        assert listener.queue.full()
        worker.start()
        assert entered.wait(1)
        assert not sink.closed.wait(0.1), "Sink closed while its listener was still blocked"
        sink.allow_write.set()
        worker.join(timeout=2)

        assert not worker.is_alive()
        assert not errors
        assert listener_thread is not None and not listener_thread.is_alive()
        assert sink.closed.is_set()
        assert sink.messages == ["first", "second"]
        # A producer already referenced by a logging call must not refill a stopped queue.
        producer.emit(logging.makeLogRecord({"msg": "late", "levelno": logging.INFO}))
        assert listener.queue.empty()
        assert listener.queue.unfinished_tasks == 0
    finally:
        sink.allow_write.set()
        if worker.ident is not None:
            worker.join(timeout=2)
        if (
            listener_thread is not None
            and listener_thread.is_alive()
            and state._QUEUE_LISTENER is not listener
        ):
            listener.queue.join()
            listener.stop()
        shutdown_logging()


def test_shutdown_keeps_listener_and_sinks_when_stopping_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = _BlockingSink()
    sink.allow_write.set()
    setup_logging(ObservabilitySettings(), logs_mode="otlp", extra_handlers=[sink])
    listener = state._QUEUE_LISTENER
    assert listener is not None
    original_stats = get_log_queue_stats()

    def fail_stop() -> None:
        raise RuntimeError("could not stop listener")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(listener, "stop", fail_stop)
            shutdown_logging()

            assert state._QUEUE_LISTENER is listener
            assert state._MANAGED_OUTPUT_HANDLERS
            assert not sink.closed.is_set()
            assert get_log_queue_stats() == original_stats
    finally:
        if state._QUEUE_LISTENER is not listener:
            listener.stop()
        shutdown_logging()


@pytest.mark.parametrize("action", ["shutdown", "reconfigure"])
@pytest.mark.parametrize("failure_time", ["before_stop", "during_stop"])
def test_full_queue_teardown_does_not_wait_for_a_dead_listener(
    action: str, failure_time: str,
) -> None:
    script = r'''
import logging
import sys
import threading
from fastapiobserver import ObservabilitySettings
from fastapiobserver.logging import setup_logging, shutdown_logging
import fastapiobserver.logging.state as state

threading.excepthook = lambda args: None  # The sink failure is intentional.
entered = threading.Event()
allow_failure = threading.Event()

class BrokenSink(logging.Handler):
    def emit(self, record):
        entered.set()
        allow_failure.wait()
        raise RuntimeError("sink failed")

sink = BrokenSink()
setup_logging(
    ObservabilitySettings(log_queue_max_size=1, sink_circuit_breaker_enabled=False),
    logs_mode="otlp", extra_handlers=[sink],
)
listener = state._QUEUE_LISTENER
thread = listener._thread
if sys.argv[2] == "before_stop":
    allow_failure.set()
logging.getLogger("test.dead").error("crash listener")
assert entered.wait(1)
if sys.argv[2] == "before_stop":
    thread.join(timeout=1)
    assert not thread.is_alive()
logging.getLogger("test.dead").error("pending")
assert listener.queue.full()
if sys.argv[2] == "during_stop":
    threading.Timer(0.05, allow_failure.set).start()
if sys.argv[1] == "shutdown":
    shutdown_logging()
else:
    setup_logging(ObservabilitySettings(), logs_mode="otlp", extra_handlers=[logging.NullHandler()])
assert state._QUEUE_LISTENER is not listener
assert sink._closed
shutdown_logging()
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, action, failure_time],
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert completed.returncode == 0, completed.stderr
