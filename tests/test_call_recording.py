"""Manual adapters share the native recorder and explicit call ownership."""

from concurrent.futures import ThreadPoolExecutor
import importlib
import logging
import math
import os
import weakref

import pytest

from fastapiobserver.experimental import observe_operation
from fastapiobserver.request_context import RequestState, get_request_state


def event(caplog, name):
    return next(r.event for r in caplog.records
                if r.msg.startswith("operation.") and r.event["name"] == name)


def test_manual_completion_updates_its_explicit_operation(caplog):
    caplog.set_level(logging.INFO)
    with observe_operation("origin") as operation:
        assert operation.record_call("db", 0.005)
        with observe_operation("other"):
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(operation.record_call, "http", 0.002,
                                       error="read_timeout").result()
    assert event(caplog, "origin")["db"] == {"count": 1, "ms": 5.0}
    assert event(caplog, "origin")["http"] == {
        "count": 1, "ms": 2.0, "errors": {"read_timeout": 1},
    }
    assert "http" not in event(caplog, "other")
    assert not operation.record_call("http", 0.001)


def test_call_handle_completes_once_even_across_threads(caplog):
    caplog.set_level(logging.INFO)
    with observe_operation("origin") as operation:
        handle = operation.start_call("http")
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: handle.finish(), range(32)))
        assert results.count(True) == 1
    assert event(caplog, "origin")["http"]["count"] == 1


def test_call_handle_rejects_late_completion_and_does_not_retain_state():
    with observe_operation("origin") as operation:
        handle = operation.start_call("db")
        state = get_request_state()
        reference = weakref.ref(state)
    assert not handle.finish()
    del operation, state
    assert reference() is None


@pytest.mark.parametrize(("kind", "seconds", "error"), [
    ("arbitrary", 0.1, None), ("db", -1.0, None), ("db", math.inf, None),
    ("db", math.nan, None), ("db", True, None), ("db", "0.1", None),
    ("db", 1e308, None), ("db", 10**400, None),
    ("http", 0.1, "x" * 65), ("http", 0.1, "secret\nvalue"),
])
def test_invalid_manual_metadata_is_rejected_without_recording(kind, seconds, error):
    with observe_operation("origin") as operation:
        with pytest.raises((TypeError, ValueError)):
            operation.record_call(kind, seconds, error=error)
        assert get_request_state().calls == {}


def test_error_detail_is_bounded_without_losing_totals(caplog):
    caplog.set_level(logging.INFO)
    with observe_operation("origin") as operation:
        for number in range(1000):
            assert operation.record_call("http", 0.001, error=f"error_{number}")
    summary = event(caplog, "origin")["http"]
    assert summary["count"] == sum(summary["errors"].values()) == 1000
    assert len(summary["errors"]) <= 33
    assert summary["errors"]["other"] > 0


def test_recording_requires_an_entered_operation():
    operation = observe_operation("not.started")
    with pytest.raises(RuntimeError):
        operation.record_call("db", 0.1)
    with pytest.raises(RuntimeError):
        operation.start_call("db")


def test_span_owner_tracking_is_bounded_weak_and_never_falls_back():
    recording = importlib.import_module("fastapiobserver.core.recording")
    owners = recording.SpanOwners(limit=2)
    first, second, third = RequestState({}), RequestState({}), RequestState({})
    assert owners.capture((1, 1), first)
    assert owners.capture((1, 2), second)
    assert not owners.capture((1, 3), third)
    assert owners.dropped == 1
    first.close()
    assert owners.pop((1, 1)) is None
    assert owners.pop((1, 3)) is None
    assert owners.pop((1, 2)) is second
    assert len(owners) == 0
    assert owners.capture((2, 1), third)
    reference = weakref.ref(third)
    del third
    assert reference() is None and len(owners) == 0


def test_closing_a_retained_owner_immediately_releases_tracking_capacity():
    recording = importlib.import_module("fastapiobserver.core.recording")
    owners = recording.SpanOwners(limit=1)
    first, second = RequestState({}), RequestState({})
    assert owners.capture((1, 1), first)
    first.close()
    assert len(owners) == 0
    assert owners.capture((1, 2), second)
    assert owners.dropped == 0


def test_sql_completion_retains_the_owner_from_before_execution(caplog):
    from types import SimpleNamespace
    from fastapiobserver.db_queries import _before, _after

    caplog.set_level(logging.INFO)
    context = SimpleNamespace()
    with observe_operation("origin"):
        _before(None, None, "SELECT 1", None, context, False)
        with observe_operation("other"):
            _after(None, None, "SELECT 1", None, context, False)
    assert event(caplog, "origin")["db"]["count"] == 1
    assert "db" not in event(caplog, "other")


def test_request_cleanup_expires_handles_and_copied_context():
    from contextvars import copy_context
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapiobserver import install_observability
    from fastapiobserver.core.recording import CallHandle

    app = FastAPI()
    captured = []

    @app.get("/work")
    async def work():
        state = get_request_state()
        captured.append((CallHandle(state, "http"), copy_context()))
        return {"ok": True}

    install_observability(app, metrics_enabled=False, configure_logging=False)
    with TestClient(app) as client:
        assert client.get("/work").status_code == 200
    handle, context = captured[0]
    with observe_operation("later"):
        assert not handle.finish()
        assert context.run(get_request_state) is None
        assert get_request_state().calls == {}


def test_aggregate_overflow_cannot_corrupt_existing_totals():
    with observe_operation("origin") as operation:
        assert operation.record_call("http", 1e305)
        assert not operation.record_call("http", 1e305)
        assert get_request_state().calls["http"] == [1, 1e305]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork contract")
def test_inherited_handle_rejects_completion_before_touching_its_parent_lock():
    import subprocess
    import sys
    from pathlib import Path
    import fastapiobserver

    # Fork in a fresh single-threaded process, retaining a locked parent handle.
    script = """
import os
from fastapiobserver.experimental import observe_operation
with observe_operation('parent') as operation:
    handle = operation.start_call('db')
    handle._lock.acquire()
    pid = os.fork()
    if pid == 0:
        os._exit(0 if handle.finish() is False else 1)
    handle._lock.release()
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert handle.finish()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=10,
        env={**os.environ, "PYTHONPATH": str(Path(fastapiobserver.__file__).resolve().parents[1])},
    )
    assert result.returncode == 0, result.stderr
