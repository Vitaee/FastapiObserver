from __future__ import annotations

import asyncio
import importlib
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from fastapiobserver import (
    ObservabilitySettings,
    OTelSettings,
    install_observability,
    instrument_httpx_client,
    instrument_requests_session,
)
from fastapiobserver.propagation import (
    uninstrument_httpx_client,
    uninstrument_requests_session,
)
from fastapiobserver.request_context import get_request_state
import fastapiobserver.otel.tracing as tracing


@pytest.fixture(autouse=True)
def restore_clients():
    yield
    uninstrument_httpx_client()
    uninstrument_requests_session()


def _call(app, caplog, *, traced=False, sampling_ratio=1.0):
    install_observability(
        app,
        ObservabilitySettings(),
        metrics_enabled=False,
        otel_settings=OTelSettings(enabled=traced, trace_sampling_ratio=sampling_ratio),
    )
    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        response = TestClient(app).get("/call")
    assert response.status_code == 200
    [record] = [r for r in caplog.records if r.message == "request.completed"]
    return record.event


@pytest.mark.parametrize("library", ["httpx", "httpx2"])
@pytest.mark.parametrize("global_install", [False, True])
def test_httpx_sync_calls_count_once_without_tracing(library, global_install, caplog):
    http = pytest.importorskip(library)
    app = FastAPI()

    def respond(request):
        assert request.headers["x-request-id"]
        return http.Response(200, json={"ok": True})

    with http.Client(transport=http.MockTransport(respond)) as client:
        instrument_httpx_client(None if global_install else client)
        instrument_httpx_client(None if global_install else client)

        @app.get("/call")
        def call():
            for _ in range(3):
                assert client.get("https://downstream.test/items").status_code == 200
            return {}

        event = _call(app, caplog)
    assert event["http"]["count"] == 3
    assert event["http"]["ms"] >= 0
    assert "errors" not in event["http"]


@pytest.mark.parametrize("library", ["httpx", "httpx2"])
@pytest.mark.parametrize(
    "error_name, category",
    [
        ("ConnectTimeout", "connect_timeout"),
        ("ReadTimeout", "read_timeout"),
        ("WriteTimeout", "write_timeout"),
        ("PoolTimeout", "pool_timeout"),
        ("ConnectError", "network_error"),
        ("RemoteProtocolError", "network_error"),
    ],
)
def test_failed_httpx_calls_are_counted_and_classified(library, error_name, category, caplog):
    http = pytest.importorskip(library)
    app = FastAPI()

    def fail(request):
        raise getattr(http, error_name)("https://user:secret@downstream.test/?token=secret")

    with http.Client(transport=http.MockTransport(fail)) as client:
        instrument_httpx_client(client)

        @app.get("/call")
        def call():
            with pytest.raises(getattr(http, error_name)):
                client.get("https://downstream.test/")
            return {}

        event = _call(app, caplog)
    assert event["http"]["count"] == 1
    assert event["http"]["errors"] == {category: 1}
    assert "secret" not in str(event)


@pytest.mark.parametrize("library", ["httpx", "httpx2"])
def test_async_httpx_stream_errors_count_once(library, caplog):
    http = pytest.importorskip(library)
    app = FastAPI()

    class Stream(http.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            raise http.ReadTimeout("body timed out")

    async def respond(request):
        return http.Response(200, stream=Stream())

    @app.get("/call")
    async def call():
        async with http.AsyncClient(transport=http.MockTransport(respond)) as client:
            instrument_httpx_client(client)
            with pytest.raises(http.ReadTimeout):
                async with client.stream("GET", "https://downstream.test/") as response:
                    assert "http" not in get_request_state().calls
                    async for _ in response.aiter_bytes():
                        pass
        return {}

    event = _call(app, caplog)
    assert event["http"]["count"] == 1
    assert event["http"]["errors"] == {"read_timeout": 1}


@pytest.mark.parametrize("library", ["httpx", "httpx2"])
@pytest.mark.parametrize("sampling_ratio", [0.0, 1.0])
def test_async_calls_and_http_errors_count_with_tracing(
    library, caplog, monkeypatch, sampling_ratio
):
    http = pytest.importorskip(library)
    exporter = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    monkeypatch.setattr(tracing, "build_otlp_exporter", lambda *_: exporter.InMemorySpanExporter())
    app = FastAPI()

    async def respond(request):
        await asyncio.sleep(0)
        return http.Response(503 if request.url.path == "/fail" else 200, content=b"ok")

    @app.get("/call")
    async def call():
        async with http.AsyncClient(transport=http.MockTransport(respond)) as client:
            instrument_httpx_client(client)
            await asyncio.gather(
                *[client.get(f"https://downstream.test/{path}") for path in ("ok", "ok", "fail")]
            )
        return {}

    event = _call(app, caplog, traced=True, sampling_ratio=sampling_ratio)
    assert event["http"]["count"] == 3
    assert event["http"]["errors"] == {"http_5xx": 1}


@pytest.mark.parametrize("library", ["httpx", "httpx2"])
@pytest.mark.parametrize("fail", [False, True])
def test_sync_streams_complete_or_fail_once(library, fail, caplog):
    http = pytest.importorskip(library)
    app = FastAPI()

    class Stream(http.SyncByteStream):
        def __iter__(self):
            yield b"first"
            if fail:
                raise http.ReadTimeout("stream timed out")
            yield b"last"

    with http.Client(
        transport=http.MockTransport(lambda _: http.Response(200, stream=Stream()))
    ) as client:
        instrument_httpx_client(client)

        @app.get("/call")
        def call():
            with client.stream("GET", "https://downstream.test/") as response:
                assert "http" not in get_request_state().calls
                if fail:
                    with pytest.raises(http.ReadTimeout):
                        response.read()
                else:
                    assert response.read() == b"firstlast"
                response.close()
            return {}

        event = _call(app, caplog)
    assert event["http"]["count"] == 1
    assert event["http"].get("errors", {}) == ({"read_timeout": 1} if fail else {})


def test_cancelled_async_call_propagates_and_is_counted(caplog):
    http = pytest.importorskip("httpx2")
    app = FastAPI()

    async def respond(request):
        raise asyncio.CancelledError()

    instrument_httpx_client()

    @app.get("/call")
    async def call():
        async with http.AsyncClient(transport=http.MockTransport(respond)) as client:
            instrument_httpx_client(client)  # global and per-client setup must not count twice
            with pytest.raises(asyncio.CancelledError):
                await client.get("https://downstream.test/")
        return {}

    event = _call(app, caplog)
    assert event["http"]["count"] == 1
    assert event["http"]["errors"] == {"cancelled": 1}


def test_manually_traced_call_counts_once(caplog, monkeypatch):
    http = pytest.importorskip("httpx2")
    trace = pytest.importorskip("opentelemetry.trace")
    exporter = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    monkeypatch.setattr(tracing, "build_otlp_exporter", lambda *_: exporter.InMemorySpanExporter())
    app = FastAPI()
    with http.Client(transport=http.MockTransport(lambda _: http.Response(200))) as client:
        instrument_httpx_client(client)

        @app.get("/call")
        def call():
            with trace.get_tracer("tests").start_as_current_span(
                "downstream", kind=trace.SpanKind.CLIENT, attributes={"http.method": "GET"}
            ):
                client.get("https://downstream.test/")
            return {}

        event = _call(app, caplog, traced=True)
    assert event["http"]["count"] == 1


@pytest.mark.parametrize("fail", [False, True])
def test_requests_streams_count_consumption_and_read_timeouts(fail, caplog):
    import io

    requests = pytest.importorskip("requests")
    urllib3 = pytest.importorskip("urllib3")
    app = FastAPI()

    class Body(io.BytesIO):
        def read(self, *args):
            if fail:
                raise urllib3.exceptions.ReadTimeoutError(None, "/", "read timed out")
            return super().read(*args)

    class Adapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            response = requests.Response()
            response.request, response.status_code = request, 200
            response.raw = urllib3.response.HTTPResponse(Body(b"stream"), preload_content=False)
            return response

        def close(self):
            pass

    instrument_requests_session()
    with requests.Session() as session:
        session.mount("https://", Adapter())

        @app.get("/call")
        def call():
            with session.get("https://downstream.test/", stream=True) as response:
                assert "http" not in get_request_state().calls
                if fail:
                    with pytest.raises(requests.ConnectionError):
                        list(response.iter_content())
                else:
                    assert b"".join(response.iter_content()) == b"stream"
            return {}

        event = _call(app, caplog)
    assert event["http"]["count"] == 1
    assert event["http"].get("errors", {}) == ({"read_timeout": 1} if fail else {})


@pytest.mark.parametrize("traced", [False, True])
def test_requests_redirects_and_failures_count_once(traced, caplog, monkeypatch):
    requests = pytest.importorskip("requests")
    exporter = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    monkeypatch.setattr(tracing, "build_otlp_exporter", lambda *_: exporter.InMemorySpanExporter())
    app = FastAPI()

    class Adapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            if request.url.endswith("/timeout"):
                raise requests.ReadTimeout("read timed out")
            response = requests.Response()
            response.request = request
            response.url = request.url
            response.status_code = 302 if request.url.endswith("/redirect") else 404
            response._content = b"not found"
            response._content_consumed = True
            if response.status_code == 302:
                response.headers["Location"] = "/missing"
            return response

        def close(self):
            pass

    instrument_requests_session()
    instrument_requests_session()
    with requests.Session() as session:
        session.mount("https://", Adapter())

        @app.get("/call")
        def call():
            assert session.get("https://downstream.test/redirect").status_code == 404
            with pytest.raises(requests.ReadTimeout):
                session.get("https://downstream.test/timeout")
            return {}

        event = _call(app, caplog, traced=traced)
    assert event["http"]["count"] == 2
    assert event["http"]["errors"] == {"http_4xx": 1, "read_timeout": 1}


def test_http_counting_does_not_require_opentelemetry_instrumentation(monkeypatch, caplog):
    http = pytest.importorskip("httpx2")
    original_import = importlib.import_module

    def import_module(name, *args, **kwargs):
        if name.startswith("opentelemetry.instrumentation"):
            raise ImportError("optional instrumentation not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", import_module)
    app = FastAPI()
    with http.Client(transport=http.MockTransport(lambda _: http.Response(200))) as client:
        instrument_httpx_client(client)

        @app.get("/call")
        def call():
            client.get("https://downstream.test/")
            return {}

        event = _call(app, caplog)
    assert event["http"]["count"] == 1
