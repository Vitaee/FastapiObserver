from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from fastapiobserver import ObservabilitySettings
from fastapiobserver.middleware.request_logging import RequestLoggingMiddleware
import fastapiobserver.middleware.request_logging as request_logging
from fastapiobserver.request_context import get_request_state


class _Metrics:
    def __init__(self) -> None:
        self.observations: list[dict] = []

    def observe(self, **values) -> None:
        self.observations.append(values)


def _run(app, monkeypatch, caplog, *, send_error: bool = False):
    clock = [1.0]
    monkeypatch.setattr(request_logging, "time", SimpleNamespace(perf_counter=lambda: clock[0]))
    metrics = _Metrics()
    middleware = RequestLoggingMiddleware(
        app(clock, metrics), ObservabilitySettings(), metrics_backend=metrics
    )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if send_error and message["type"] == "http.response.body":
            raise OSError("connection closed")

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/timing",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "query_string": b"",
    }
    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        try:
            asyncio.run(middleware(scope, receive, send))
        except (RuntimeError, OSError, asyncio.CancelledError):
            pass
    records = [r for r in caplog.records if r.name == "fastapiobserver.middleware"]
    return metrics.observations, records


def test_http_metrics_finish_before_background_work(monkeypatch, caplog) -> None:
    def app(clock, metrics):
        async def serve(scope, receive, send):
            clock[0] = 2.0
            await send({"type": "http.response.start", "status": 200, "headers": []})
            clock[0] = 3.0
            await send({"type": "http.response.body", "body": b"ok"})
            assert metrics.observations == [
                {
                    "method": "GET",
                    "path": "<unmatched>",
                    "status_code": 200,
                    "duration_seconds": 2.0,
                }
            ]
            assert get_request_state() is not None  # background work keeps correlation
            clock[0] = 11.0

        return serve

    observations, records = _run(app, monkeypatch, caplog)

    assert len(observations) == len(records) == 1
    assert records[0].message == "request.completed"
    assert records[0].event["duration_ms"] == 2000
    assert records[0].event["app_duration_ms"] == 10000
    assert records[0].event["response_completed"] is True


@pytest.mark.parametrize("cancelled", [False, True])
def test_failure_after_response_preserves_sent_status(monkeypatch, caplog, cancelled) -> None:
    def app(clock, metrics):
        async def serve(scope, receive, send):
            await send({"type": "http.response.start", "status": 202, "headers": []})
            clock[0] = 2.0
            await send({"type": "http.response.body", "body": b"accepted"})
            clock[0] = 5.0
            if cancelled:
                raise asyncio.CancelledError()
            raise RuntimeError("background work failed")

        return serve

    observations, records = _run(app, monkeypatch, caplog)

    assert len(observations) == len(records) == 1
    assert observations[0]["status_code"] == 202
    assert observations[0]["duration_seconds"] == 1.0
    assert records[0].message == ("request.cancelled" if cancelled else "request.failed")
    assert records[0].event["status_code"] == 202
    assert records[0].event["duration_ms"] == 1000
    assert records[0].event["app_duration_ms"] == 4000
    assert records[0].event["response_completed"] is True


@pytest.mark.parametrize("send_error", [False, True])
def test_incomplete_response_records_interruption_once(monkeypatch, caplog, send_error) -> None:
    def app(clock, metrics):
        async def serve(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            clock[0] = 4.0
            await send({"type": "http.response.body", "body": b"partial", "more_body": True})
            raise RuntimeError("stream failed")

        return serve

    observations, records = _run(app, monkeypatch, caplog, send_error=send_error)

    assert len(observations) == len(records) == 1
    assert observations[0]["status_code"] == 200
    assert observations[0]["duration_seconds"] == 3.0
    assert records[0].message == "request.failed"
    assert records[0].event["status_code"] == 200
    assert records[0].event["response_completed"] is False


def test_response_duration_includes_stream_and_trailers(monkeypatch, caplog) -> None:
    def app(clock, metrics):
        async def serve(scope, receive, send):
            await send(
                {"type": "http.response.start", "status": 200, "headers": [], "trailers": True}
            )
            clock[0] = 2.0
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            clock[0] = 3.0
            await send({"type": "http.response.body", "body": b"last"})
            assert not metrics.observations
            clock[0] = 4.0
            await send({"type": "http.response.trailers", "headers": []})
            clock[0] = 9.0

        return serve

    observations, records = _run(app, monkeypatch, caplog)

    assert len(observations) == 1
    assert observations[0]["duration_seconds"] == 3.0
    assert records[0].event["duration_ms"] == 3000
    assert records[0].event["app_duration_ms"] == 8000


def test_observed_disconnect_is_distinct_from_cancellation(caplog):
    async def app(scope, receive, send):
        message = await receive()
        assert message["type"] == "http.disconnect"
        await asyncio.sleep(0.02)  # The app keeps working after observing it.

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    middleware = RequestLoggingMiddleware(app, ObservabilitySettings())
    scope = {"type": "http", "method": "GET", "path": "/left", "headers": []}
    with caplog.at_level(logging.INFO):
        asyncio.run(middleware(scope, receive, send))
    [record] = [r for r in caplog.records if r.name == "fastapiobserver.middleware"]
    assert record.message == "request.client_disconnected"
    assert record.event["after_disconnect_ms"] >= 15
    assert record.event["cancelled"] is False
    assert record.event["status_code"] == 499


def test_disconnect_received_after_response_is_not_an_abandoned_request(caplog):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})
        await receive()  # ASGI can return disconnect because the response has ended.

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    scope = {"type": "http", "method": "GET", "path": "/done", "headers": []}
    with caplog.at_level(logging.INFO):
        asyncio.run(RequestLoggingMiddleware(app, ObservabilitySettings())(scope, receive, send))
    [record] = [r for r in caplog.records if r.name == "fastapiobserver.middleware"]
    assert record.message == "request.completed"
    assert record.event["client_disconnected"] is False


def test_disconnect_while_start_send_waits_preserves_successfully_sent_status(caplog):
    wrapped_receive = None

    async def app(scope, receive, send):
        nonlocal wrapped_receive
        wrapped_receive = receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            await wrapped_receive()

    scope = {"type": "http", "method": "GET", "path": "/race", "headers": []}
    with caplog.at_level(logging.INFO):
        asyncio.run(RequestLoggingMiddleware(app, ObservabilitySettings())(scope, receive, send))
    [record] = [r for r in caplog.records if r.name == "fastapiobserver.middleware"]
    assert record.event["status_code"] == 200
    assert record.event["client_disconnected"] is True
