"""Operation behavior through the existing logging and client seams."""

import asyncio
from contextvars import copy_context
import importlib
import logging
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import fastapiobserver
from sqlalchemy import create_engine, text

from fastapiobserver import get_request_id, set_user_context
from fastapiobserver.db_queries import count_queries
from fastapiobserver.propagation import instrument_httpx_client
from fastapiobserver.request_context import (
    RequestState, get_request_state, get_user_context, set_request_id, set_request_state,
)
from fastapiobserver.logging import flush_logging, setup_logging
from fastapiobserver import ObservabilitySettings


def observe(name, **kwargs):
    return importlib.import_module("fastapiobserver.experimental").observe_operation(name, **kwargs)


def terminals(caplog):
    return [r for r in caplog.records if r.msg.startswith("operation.")]


def test_operation_reuses_native_sql_http_and_correlation(caplog):
    caplog.set_level(logging.INFO)
    engine = create_engine("sqlite://")
    count_queries(engine)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            instrument_httpx_client(client)
            with observe("import.orders") as operation:
                request_id = get_request_id()
                with engine.connect() as connection:
                    connection.execute(text("select 1"))
                assert client.get("https://example.test/orders").status_code == 200
                set_user_context({"tenant": "example"})
                logging.getLogger("application").info("import.finished")
        [record] = terminals(caplog)
        assert record.msg == "operation.completed"
        assert record.request_id == request_id == requests[0].headers["x-request-id"]
        assert record.user_context == {"tenant": "example"}
        assert record.event["name"] == "import.orders"
        assert record.event["operation_id"] == operation.operation_id
        assert record.event["outcome"] == "success"
        assert record.event["db"]["count"] == record.event["http"]["count"] == 1
        assert record.event["duration_ms"] >= 0
        assert get_request_state() is None and get_request_id() is None
    finally:
        engine.dispose()


def test_nested_operation_restores_outer_request_and_owns_its_counts(caplog):
    caplog.set_level(logging.INFO)
    outer = RequestState({"type": "http"})
    set_request_state(outer)
    set_request_id("request-42")
    set_user_context({"tenant": "outer"})
    try:
        with observe("parent") as parent:
            parent_state = get_request_state()
            parent_state.add_call("db", 0.001)
            with observe("child"):
                get_request_state().add_call("db", 0.002)
                set_user_context({"tenant": "child"})
            assert get_request_state() is parent_state
            assert get_user_context() == {"tenant": "outer"}
        child_record, parent_record = terminals(caplog)
        assert child_record.event["parent_operation_id"] == parent.operation_id
        assert child_record.event["db"]["count"] == parent_record.event["db"]["count"] == 1
        assert get_request_state() is outer
        assert get_request_id() == "request-42"
        assert get_user_context() == {"tenant": "outer"}
        assert outer.calls == {}
    finally:
        set_request_state(None)


def test_async_operations_isolate_concurrent_tasks_and_copied_threads(caplog):
    caplog.set_level(logging.INFO)

    async def work(index):
        async with observe(f"job.{index}"):
            await asyncio.sleep(0)
            context = copy_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(
                    context.run, lambda: get_request_state().add_call("db", 0.01)
                ).result()
            assert get_request_state().calls["db"][0] == 1
            return get_request_id()

    async def run():
        return await asyncio.gather(*(work(i) for i in range(4)))

    request_ids = asyncio.run(run())
    assert len(set(request_ids)) == 4
    assert len(terminals(caplog)) == 4
    assert {r.request_id for r in terminals(caplog)} == set(request_ids)


@pytest.mark.parametrize("error", [ValueError("private detail"), asyncio.CancelledError()])
def test_operation_preserves_exception_and_cancellation(error, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(type(error)) as caught:
        with observe("failed.job"):
            raise error
    assert caught.value is error
    [record] = terminals(caplog)
    expected = "cancelled" if isinstance(error, asyncio.CancelledError) else "failure"
    assert record.event["outcome"] == expected
    assert "private detail" not in str(record.event)
    assert record.exc_info is None
    assert get_request_state() is None and get_request_id() is None


def test_logging_failure_preserves_application_result_and_handlers():
    class Broken(logging.Handler):
        def emit(self, record):
            raise RuntimeError("sink unavailable")

    logger = logging.getLogger("operation.broken")
    handler = Broken()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        with observe("successful.job", logger=logger):
            result = 42
        assert result == 42
        failure = ValueError("application failure")
        with pytest.raises(ValueError) as caught:
            with observe("failed.job", logger=logger):
                raise failure
        assert caught.value is failure
        assert logger.handlers == [handler]
    finally:
        logger.removeHandler(handler)


def test_closed_operation_releases_state_and_rejects_late_stream_completion(caplog):
    caplog.set_level(logging.INFO)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text="ok"))
    with httpx.Client(transport=transport) as client:
        instrument_httpx_client(client)
        with observe("stream.job"):
            state = get_request_state()
            context = copy_context()
            response = client.send(client.build_request("GET", "https://example.test"), stream=True)
            state.add_call("db", 0.001, "select 'private'")
            set_user_context({"tenant": "temporary"})
        context.run(response.close)
        state.add_call("http", 1)
        assert state.calls == state.call_errors == state.statements == {}
        assert state.http_span_ids == set()
        assert state.held_logs is None and state.user_context is None
        assert context.run(get_request_state) is None
        assert context.run(get_request_id) is None
        assert len(terminals(caplog)) == 1


def test_operation_cannot_be_reentered_and_name_is_bounded(caplog):
    caplog.set_level(logging.INFO)
    operation = observe("once")
    with operation:
        with pytest.raises(RuntimeError):
            with operation:
                pass
    with pytest.raises(RuntimeError):
        with operation:
            pass
    assert len(terminals(caplog)) == 1
    for invalid in ("", " ", "x" * 129, "line\nbreak"):
        with pytest.raises(ValueError):
            observe(invalid)


def test_early_state_close_still_restores_the_surrounding_context():
    outer = RequestState({"type": "http"})
    set_request_state(outer)
    set_request_id("outer")
    operation = observe("closed.early")
    try:
        with operation:
            get_request_state().close()
        assert get_request_state() is outer
        assert get_request_id() == "outer"
    finally:
        set_request_state(None)


def test_sampling_and_failure_retention_use_the_existing_pipeline():
    payloads = []

    class Capture(logging.Handler):
        def emit(self, record):
            payloads.append(json.loads(self.format(record)))

    setup_logging(
        ObservabilitySettings(log_sample_rate=0, log_level_on_error="DEBUG"),
        logs_mode="otlp", extra_handlers=[Capture()],
    )
    with observe("sampled.out"):
        logging.getLogger("application").info("successful.detail")
    with pytest.raises(ValueError):
        with observe("failed.job"):
            logging.getLogger("application").debug("held.detail")
            raise ValueError("application failure")
    assert flush_logging()
    messages = [p["message"] for p in payloads]
    assert messages == ["held.detail", "operation.failed"]


def test_copied_context_cannot_retain_or_replace_closed_user_context():
    with observe("temporary"):
        set_user_context({"tenant": "temporary"})
        context = copy_context()
        state = get_request_state()
    context.run(set_user_context, {"tenant": "late"})
    assert state.user_context is None
    assert context.run(get_user_context) is None


def test_stale_logging_and_tracing_callbacks_cannot_retain_closed_details(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import SpanKind
    from fastapiobserver.http_calls import _mark_current_span
    from fastapiobserver.logging.held import HeldLogFilter
    import fastapiobserver.logging.held as held

    with observe("temporary"):
        state = get_request_state()
    # Model a callback which fetched the state before another thread closed it.
    monkeypatch.setattr(held, "get_request_state", lambda: state)
    held_filter = HeldLogFilter(logging.NullHandler(), logging.INFO, logging.DEBUG)
    record = logging.LogRecord("application", logging.DEBUG, __file__, 0, "detail", (), None)
    held_filter.filter(record)
    assert state.held_logs is None
    provider = TracerProvider()
    try:
        with provider.get_tracer("application").start_as_current_span("late", kind=SpanKind.CLIENT):
            _mark_current_span(state)
        assert state.http_span_ids == set()
    finally:
        provider.shutdown()


def test_actual_async_task_cancellation_preserves_cancelled_outcome(caplog):
    caplog.set_level(logging.INFO)

    async def run():
        entered = asyncio.Event()

        async def work():
            async with observe("cancelled.job"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(work())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert get_request_state() is None

    asyncio.run(run())
    [record] = terminals(caplog)
    assert record.msg == "operation.cancelled"


def test_async_sqlalchemy_queries_work_without_an_application(caplog):
    from sqlalchemy.ext.asyncio import create_async_engine

    caplog.set_level(logging.INFO)

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://")
        count_queries(engine.sync_engine)
        try:
            async with observe("async.import"):
                async with engine.connect() as connection:
                    await connection.execute(text("select 1"))
        finally:
            await engine.dispose()

    asyncio.run(run())
    [record] = terminals(caplog)
    assert record.event["db"]["count"] == 1


@pytest.mark.parametrize("ratio", [0.0, 0.1, 1.0])
def test_existing_tracing_bridges_do_not_change_native_operation_totals(ratio):
    # Global providers/instrumentors need the same fresh-process isolation as
    # the standalone application examples, especially under randomized ordering.
    result = subprocess.run(
        [sys.executable, "-c",
         "import runpy,sys; runpy.run_path(sys.argv[1])['_check_trace_totals'](float(sys.argv[2]))",
         str(Path(__file__).resolve()), str(ratio)],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(fastapiobserver.__file__).resolve().parents[1])},
    )
    assert result.returncode == 0, result.stderr


def _check_trace_totals(ratio):
    from opentelemetry import trace
    import opentelemetry.sdk.trace as trace_sdk
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    from fastapiobserver.db_tracing import instrument_sqlalchemy, uninstrument_sqlalchemy
    from fastapiobserver.otel.tracing import _request_call_totals

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("fastapiobserver.operations")
    logger.setLevel(logging.INFO)
    logger.addHandler(Capture())
    provider = trace_sdk.TracerProvider(sampler=TraceIdRatioBased(ratio))
    provider.add_span_processor(_request_call_totals(trace, trace_sdk))
    trace.set_tracer_provider(provider)
    engine = create_engine("sqlite://")
    instrument_sqlalchemy(engine, enable_commenter=False)
    try:
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
            instrument_httpx_client(client)
            with provider.get_tracer("application").start_as_current_span("job"):
                with observe("traced.job"):
                    with engine.connect() as connection:
                        connection.execute(text("select 1"))
                    client.get("https://example.test")
        [record] = records
        assert record.event["db"]["count"] == record.event["http"]["count"] == 1
    finally:
        uninstrument_sqlalchemy()
        engine.dispose()
        provider.shutdown()
