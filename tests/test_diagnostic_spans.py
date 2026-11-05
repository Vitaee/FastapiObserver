"""Bounded diagnostic facts on an application's existing recording server span."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import logging
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from fastapiobserver import ObservabilitySettings, pools
from fastapiobserver.middleware.request_logging import RequestLoggingMiddleware
from fastapiobserver.request_context import RequestState, get_request_state, set_request_state


@pytest.fixture
def tracing():
    provider = TracerProvider(shutdown_on_exit=False)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider.get_tracer(__name__), exporter
    provider.shutdown()


async def receive():
    return {"type": "http.request", "body": b""}


def test_only_three_fixed_event_types_are_admitted_under_concurrent_calls(tracing):
    tracer, exporter = tracing
    names = ["db.n_plus_one", "db.pool_exhausted", "threadpool.saturated", "unknown"]
    with tracer.start_as_current_span("request", kind=trace.SpanKind.SERVER) as span:
        state = RequestState({}, span=span)
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda name: state.add_diagnostic(name, {"count": 12}), names * 20))
    assert {event.name for event in exporter.get_finished_spans()[0].events} == {
        "fastapiobserver." + name for name in names if name != "unknown"
    }
    assert len(exporter.get_finished_spans()[0].events) == 3


@pytest.mark.parametrize("end_on_send", [False, True])
def test_n_plus_one_reaches_each_server_span_before_transport_ends_it(
    tracing, caplog, end_on_send,
):
    tracer, exporter = tracing
    statement = "SELECT private_customer_value FROM private_table"

    async def app(scope, receive, send):
        state = get_request_state()
        for _ in range(12):
            state.add_call("db", .001, statement=statement)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})

    middleware = RequestLoggingMiddleware(app, ObservabilitySettings())
    scope = {"type": "http", "method": "GET", "path": "/orders", "headers": []}

    async def run():
        for _ in range(2):
            with tracer.start_as_current_span(
                "GET /orders", kind=trace.SpanKind.SERVER, end_on_exit=not end_on_send,
            ) as span:
                async def send(message):
                    if end_on_send and message["type"] == "http.response.body":
                        span.end()
                await middleware(dict(scope), receive, send)

    with caplog.at_level(logging.WARNING):
        asyncio.run(run())
    assert len([r for r in caplog.records if r.getMessage() == "db.n_plus_one"]) == 1
    assert len(exporter.get_finished_spans()) == 2  # No diagnostic child spans.
    for span in exporter.get_finished_spans():
        [event] = span.events
        assert event.name == "fastapiobserver.db.n_plus_one"
        assert dict(event.attributes) == {
            "count": 12, "threshold": 10,
            "statement_fingerprint": sha256(statement.encode()).hexdigest(),
        }
        assert "private" not in str(event.attributes)


def test_pool_events_are_bounded_private_and_independent_of_log_rate_limit(tracing, monkeypatch):
    tracer, exporter = tracing
    monkeypatch.setattr(pools, "_last_warned", {})
    monkeypatch.setattr(pools, "_thread_limiter", SimpleNamespace(
        borrowed_tokens=2, total_tokens=2,
        statistics=lambda: SimpleNamespace(borrowed_tokens=2, total_tokens=2, tasks_waiting=3),
    ))
    monkeypatch.setattr(pools, "db_pool_usage", lambda: [
        (f"postgresql://private:secret@host/db?token={n}", 5, 5) for n in range(100)
    ])

    async def app(scope, receive, send):
        for _ in range(5):
            pools.check_pools()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body"})

    async def send(message):
        pass

    async def run():
        for _ in range(2):
            with tracer.start_as_current_span("request", kind=trace.SpanKind.SERVER):
                await RequestLoggingMiddleware(app, ObservabilitySettings())(
                    {"type": "http", "method": "GET", "path": "/", "headers": []}, receive, send,
                )
    asyncio.run(run())
    for span in exporter.get_finished_spans():
        assert [(event.name, dict(event.attributes)) for event in span.events] == [
            ("fastapiobserver.threadpool.saturated", {"busy": 2, "limit": 2, "waiting": 3}),
            ("fastapiobserver.db.pool_exhausted", {"checked_out": 5, "limit": 5}),
        ]
        assert "private" not in str(span.events)


@pytest.mark.parametrize("kind", [trace.SpanKind.INTERNAL, trace.SpanKind.CLIENT])
def test_diagnostics_do_not_attach_to_other_span_kinds(tracing, kind):
    tracer, exporter = tracing
    with tracer.start_as_current_span("other", kind=kind) as span:
        state = RequestState({}, span=span)
        state.add_diagnostic("db.n_plus_one", {"count": 12})
    assert not exporter.get_finished_spans()[0].events


@pytest.mark.parametrize("condition", ["missing", "ended", "broken", "closed"])
def test_unavailable_or_faulty_borrowed_span_cannot_break_application(tracing, condition):
    tracer, exporter = tracing
    with tracer.start_as_current_span("request", kind=trace.SpanKind.SERVER) as span:
        if condition == "ended":
            span.end()
        if condition == "missing":
            span = None
        if condition == "broken":
            def fail(*args, **kwargs):
                raise RuntimeError("application span failed")
            span = SimpleNamespace(kind=trace.SpanKind.SERVER, is_recording=lambda: True,
                                   add_event=fail)
        state = RequestState({}, span=span)
        if condition == "closed":
            state.close()
        set_request_state(state)
        try:
            state.add_diagnostic("db.n_plus_one", {"count": 12})
            assert get_request_state() is (None if condition == "closed" else state)
        finally:
            set_request_state(None)
    assert not exporter.get_finished_spans()[0].events
