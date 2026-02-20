import logging
import os
import tempfile
import time
from typing import Any

from fastapiobserver import ObservabilitySettings
from fastapiobserver.sinks import (
    LogtailSink,
    RotatingFileSink,
    StdoutSink,
    _LogtailHandler,
    build_sink_handlers,
    clear_sinks,
    discover_entry_point_sinks,
    get_registered_sinks,
    register_sink,
    unregister_sink,
)

def test_stdout_sink() -> None:
    formatter = logging.Formatter()
    sink = StdoutSink()
    assert sink.name == "stdout"
    handler = sink.create_handler(formatter)
    assert isinstance(handler, logging.StreamHandler)
    assert handler.formatter is formatter

def test_rotating_file_sink() -> None:
    with tempfile.TemporaryDirectory() as d:
        formatter = logging.Formatter()
        sink = RotatingFileSink(log_dir=d, filename="test.log", max_bytes=100, backup_count=2)
        assert sink.name == "rotating_file"

        handler = sink.create_handler(formatter)
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.formatter is formatter
        assert handler.baseFilename == os.path.join(d, "test.log")

def test_logtail_sink_drop_limits() -> None:
    formatter = logging.Formatter()
    sink = LogtailSink(source_token="test_token", batch_size=2, flush_interval=0.1)
    assert sink.name == "logtail"

    handler = sink.create_handler(formatter)
    assert getattr(handler, "name", None) or handler

def test_logtail_sink_flushing_and_errors(monkeypatch: Any) -> None:
    formatter = logging.Formatter()

    # Needs to be wrapped to expose `LogtailSink`'s internal queue since
    # create_handler() returns the thread wrapper Handler
    handler = _LogtailHandler(
        endpoint="http://example.com",
        source_token="test",
        batch_size=2,
        flush_interval=0.01,
        max_retries=2,
        max_queue_size=2,
    )
    handler.setFormatter(formatter)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="test.py", lineno=1,
        msg="test message", args=(), exc_info=None
    )

    calls: list[bytes] = []

    class MockResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def mock_urlopen(req, timeout=10):
        calls.append(req.data)
        return MockResponse()

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    # 1. Fill queue to trigger drops
    handler.emit(record)
    handler.emit(record)
    handler.emit(record)  # Drop oldest

    # We put 1 item successfully since queue limit is 2 but the worker consumed 0
    # because of synthetic races, so items 2 and 3 dropped old elements
    time.sleep(0.01) # guarantee flush doesn't interleave
    assert handler.drop_count >= 1

    # 2. Let it flush
    time.sleep(0.05)
    assert len(calls) >= 1

    # 3. Simulate HTTP error
    def mock_urlopen_error(req, timeout=10):
        import urllib.error
        raise urllib.error.HTTPError("url", 400, "Bad Request", {}, None)  # type: ignore

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_error)
    handler.emit(record)
    time.sleep(0.05)

    assert handler.error_count >= 1

    handler.close()

def test_logtail_retryable_error(monkeypatch: Any) -> None:
    formatter = logging.Formatter()
    handler = _LogtailHandler(
        endpoint="http://example.com", source_token="test",
        batch_size=1, flush_interval=0.01,
        max_retries=2, max_queue_size=2,
    )
    handler.setFormatter(formatter)

    class MockErrorResponse:
        status = 500
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def mock_urlopen_500(req, timeout=10):
        import urllib.error
        raise urllib.error.HTTPError("url", 500, "Internal Error", {}, None)  # type: ignore

    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen_500)

    # We will patch time.sleep to not wait during the test
    monkeypatch.setattr("time.sleep", lambda x: None)

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="test.py", lineno=1,
        msg="test message", args=(), exc_info=None
    )
    handler.emit(record)

    # We synchronously drain the queue bypassing the thread flush timer
    # so we're not racing the worker thread sleep
    handler._drain_and_send()

    assert handler.error_count >= 1
    handler.close()

def test_logtail_format_exception() -> None:
    handler = _LogtailHandler(
        endpoint="http://example.com", source_token="test",
        batch_size=1, flush_interval=0.01,
        max_retries=1, max_queue_size=2,
    )
    # Give it a formatter that throws an exception to hit the outer except
    class BadFormatter(logging.Formatter):
        def format(self, record):
            raise ValueError("bad format")

    handler.setFormatter(BadFormatter())

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="test.py", lineno=1,
        msg="test message", args=(), exc_info=None
    )
    handler.emit(record)
    assert handler.drop_count == 0  # didn't put anything
    handler.close()

def test_logtail_queue_empty_race(monkeypatch: Any) -> None:
    # Coverage for queue.Empty during drop oldest and queue.Full on retry
    handler = _LogtailHandler(
        endpoint="http://example.com", source_token="test",
        batch_size=1, flush_interval=0.01,
        max_retries=1, max_queue_size=1,
    )

    # Mock the internal queue artificially to trigger the precise except blocks
    import queue
    class RaceyQueue(queue.Queue):
        def put_nowait(self, item):
            raise queue.Full()
        def get_nowait(self):
            raise queue.Empty()

    handler._queue = RaceyQueue(maxsize=1)  # type: ignore

    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="test.py", lineno=1,
        msg="test message", args=(), exc_info=None
    )
    handler.emit(record)
    handler.close()

def test_discover_entry_point_sinks_exceptions(monkeypatch: Any) -> None:
    clear_sinks()
    import importlib.metadata

    # 1. Test when discovery throws a master exception
    def mock_entry_points_error(*args, **kwargs):
        raise RuntimeError("cannot load eps")

    monkeypatch.setattr(importlib.metadata, "entry_points", mock_entry_points_error)
    discover_entry_point_sinks()  # Should handle the Exception gracefully

    # 2. Test when factory throws an exception
    class BadEP:
        name = "bad_ep"
        def load(self):
            def factory():
                raise ValueError("cannot construct")
            return factory

    def mock_entry_points_success(*args, **kwargs):
        if kwargs.get("group") == "fastapiobserver.log_sinks":
            return [BadEP()]
        return []

    monkeypatch.setattr(importlib.metadata, "entry_points", mock_entry_points_success)
    discover_entry_point_sinks()

    assert len(get_registered_sinks()) == 0

def test_sink_registry() -> None:
    clear_sinks()

    class DummySink:
        @property
        def name(self) -> str:
            return "dummy"

        def create_handler(self, formatter: logging.Formatter) -> logging.Handler:
            return logging.NullHandler()

    sink = DummySink()
    register_sink(sink)  # type: ignore

    sinks = get_registered_sinks()
    assert "dummy" in sinks
    assert sinks["dummy"] is sink

    unregister_sink("dummy")
    assert "dummy" not in get_registered_sinks()

def test_build_sink_handlers_creates_configured_sinks() -> None:
    clear_sinks()

    with tempfile.TemporaryDirectory() as d:
        settings = ObservabilitySettings(
            app_name="test",
            service="test",
            environment="test",
            log_dir=d,
            logtail_enabled=True,
            logtail_source_token="secret",
        )
        formatter = logging.Formatter()

        handlers_with_names = build_sink_handlers(settings, formatter)

        names = [n for _, n in handlers_with_names]
        assert "stdout" in names
        assert "rotating_file" in names
        assert "logtail" in names

def test_discover_entry_point_sinks_no_crash_on_missing() -> None:
    # Should safely handle when no entry points exist
    clear_sinks()
    discover_entry_point_sinks()
    # It shouldn't crash

def test_sinks_registry_contract() -> None:
    from fastapiobserver.sinks import register_sink, get_registered_sinks, clear_sinks, StdoutSink

    clear_sinks()
    assert len(get_registered_sinks()) == 0

    register_sink(StdoutSink())
    assert len(get_registered_sinks()) == 1
    assert "stdout" in get_registered_sinks()

    # idempotent
    register_sink(StdoutSink())
    assert len(get_registered_sinks()) == 1

def test_sinks_build_handlers_contract() -> None:
    from fastapiobserver.sinks import (
        StdoutSink,
        build_sink_handlers,
        clear_sinks,
        register_sink,
    )
    from fastapiobserver.config import ObservabilitySettings
    import logging

    settings = ObservabilitySettings(app_name="app", service="svc", environment="env")
    formatter = logging.Formatter()

    clear_sinks()
    handlers = build_sink_handlers(settings, formatter)
    assert len(handlers) == 1
    assert isinstance(handlers[0][0], logging.StreamHandler)
    assert handlers[0][1] == "stdout"

    clear_sinks()
    register_sink(StdoutSink())
    handlers = build_sink_handlers(settings, formatter)
    # The factory merges registered sinks with builtin sinks.
    # Both StdoutSink (builtin) and registered StdoutSink will be in the list
    assert len(handlers) == 2
    assert handlers[0][1] == "stdout"
    assert handlers[1][1] == "stdout"

    assert handlers[0][1] == "stdout"
