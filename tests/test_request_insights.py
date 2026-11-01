from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from fastapiobserver import (
    ObservabilitySettings,
    OTelSettings,
    install_observability,
    set_user_context,
)
from fastapiobserver.logging import (
    get_log_bytes_by_route,
    get_log_level,
    set_log_level,
    setup_logging,
    shutdown_logging,
)
import fastapiobserver.otel.tracing as otel_tracing_module


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    @property
    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.lines]


def _capture_logs(settings: ObservabilitySettings, *extra: logging.Handler) -> _Collector:
    """Send the app's logs, after the queue and formatter, to a collector."""
    collector = _Collector()
    setup_logging(settings, logs_mode="otlp", extra_handlers=[collector, *extra])
    return collector


def test_failed_request_writes_its_held_debug_logs() -> None:
    settings = ObservabilitySettings(log_level="INFO", log_level_on_error="DEBUG")
    app = FastAPI()
    log = logging.getLogger("tests.held")

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        log.debug("ok detail")
        return {}

    @app.get("/fail")
    def fail() -> dict[str, str]:  # sync: runs in the thread pool
        log.debug("fail detail")
        raise RuntimeError("boom")

    install_observability(app, settings, metrics_enabled=False)
    collector = _capture_logs(settings)
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/ok")
    client.get("/fail")
    log.debug("outside a request")
    shutdown_logging()

    messages = [payload["message"] for payload in collector.payloads]
    assert "ok detail" not in messages
    assert "outside a request" not in messages
    assert messages.index("fail detail") < messages.index("request.failed")
    held = collector.payloads[messages.index("fail detail")]
    failed = collector.payloads[messages.index("request.failed")]
    assert held["level"] == "DEBUG"
    assert held["request_id"] == failed["request_id"]


def test_user_context_set_in_a_sync_endpoint_reaches_the_request_line() -> None:
    settings = ObservabilitySettings()
    app = FastAPI()

    @app.get("/me")
    def me() -> dict[str, str]:  # sync: runs in the thread pool with a copy of the context
        set_user_context({"user_id": "42"})
        return {}

    @app.get("/anonymous")
    def anonymous() -> dict[str, str]:
        return {}

    install_observability(app, settings, metrics_enabled=False)

    @app.middleware("http")  # added after install_observability, so it runs outside it
    async def set_tenant(request: Any, call_next: Any) -> Any:
        if request.url.path == "/tenant":
            set_user_context({"tenant": "acme"})
        return await call_next(request)

    @app.get("/tenant")
    def tenant() -> dict[str, str]:
        return {}

    collector = _capture_logs(settings)
    client = TestClient(app)
    client.get("/me")
    client.get("/anonymous")
    client.get("/tenant")
    shutdown_logging()

    me_line, anonymous_line, tenant_line = [
        p for p in collector.payloads if p["message"] == "request.completed"
    ]
    assert me_line["user_context"] == {"user_id": "42"}
    assert "user_context" not in anonymous_line
    assert tenant_line["user_context"] == {"tenant": "acme"}


def test_unsampled_requests_write_only_warnings_unless_they_fail_or_are_slow() -> None:
    settings = ObservabilitySettings(log_sample_rate=0.0, log_slow_request_ms=50)
    app = FastAPI()
    log = logging.getLogger("tests.sampling")

    @app.get("/fast")
    async def fast() -> dict[str, str]:
        log.info("fast detail")
        log.warning("fast warning")
        return {}

    @app.get("/slow")
    async def slow() -> dict[str, str]:
        log.info("slow detail")
        await asyncio.sleep(0.06)
        return {}

    @app.get("/fail")
    def fail() -> dict[str, str]:
        log.info("fail detail")
        raise RuntimeError("boom")

    install_observability(app, settings, metrics_enabled=False)
    collector = _capture_logs(settings)
    client = TestClient(app, raise_server_exceptions=False)
    for path in ("/fast", "/slow", "/fail"):
        client.get(path)
    log.info("outside a request")
    shutdown_logging()

    messages = [payload["message"] for payload in collector.payloads]
    assert "fast detail" not in messages
    assert "fast warning" in messages
    assert {"slow detail", "fail detail", "request.failed", "outside a request"} <= set(messages)
    [completed] = [p for p in collector.payloads if p["message"] == "request.completed"]
    assert completed["event"]["path"] == "/slow"


def test_sampling_follows_the_trace_id_as_opentelemetry_does() -> None:
    settings = ObservabilitySettings(log_sample_rate=0.5)
    app = FastAPI()

    @app.get("/")
    async def root() -> dict[str, str]:
        return {}

    install_observability(app, settings, metrics_enabled=False)
    collector = _capture_logs(settings)
    client = TestClient(app, client=("127.0.0.1", 50000))  # trusted, so traceparent is read
    kept, dropped = "0af7651916cd43dd" + "0" * 16, "0af7651916cd43dd" + "f" * 16
    for trace_id in (kept, dropped):
        client.get("/", headers={"traceparent": f"00-{trace_id}-b7ad6b7169203331-01"})
    shutdown_logging()

    completed = [p for p in collector.payloads if p["message"] == "request.completed"]
    assert [payload["trace_id"] for payload in completed] == [kept]
    sampling = pytest.importorskip("opentelemetry.sdk.trace.sampling")
    sampler = sampling.TraceIdRatioBased(0.5)
    decisions = [
        sampler.should_sample(None, int(trace_id, 16), "GET /").decision.is_sampled()
        for trace_id in (kept, dropped)
    ]
    assert decisions == [True, False]


def test_runtime_log_level_keeps_holding_lower_records() -> None:
    settings = ObservabilitySettings(log_level="INFO", log_level_on_error="DEBUG")
    setup_logging(settings, logs_mode="otlp", extra_handlers=[_Collector()])
    try:
        assert get_log_level() == "INFO"
        set_log_level(logging.WARNING)
        assert get_log_level() == "WARNING"
        # Still low enough to create the records that are held.
        assert logging.getLogger().level == logging.DEBUG
    finally:
        shutdown_logging()


def test_request_line_has_db_and_http_totals_and_n_plus_one_is_reported_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_memory = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    trace_api = pytest.importorskip("opentelemetry.trace")
    monkeypatch.setattr(
        otel_tracing_module, "build_otlp_exporter", lambda *_: in_memory.InMemorySpanExporter()
    )
    settings = ObservabilitySettings()
    app = FastAPI()
    statement = (
        "SELECT * FROM items WHERE order_id = %(order_id)s AND status = 'it''s paid' LIMIT 50"
    )

    @app.get("/orders")
    def list_orders() -> dict[str, str]:
        tracer = trace_api.get_tracer("tests")
        client = trace_api.SpanKind.CLIENT
        for _ in range(12):
            with tracer.start_as_current_span(
                "SELECT",
                kind=client,
                attributes={"db.system": "postgresql", "db.statement": statement},
            ):
                pass
        with tracer.start_as_current_span(
            "GET", kind=client, attributes={"http.request.method": "GET"}
        ):
            pass
        return {}

    install_observability(
        app, settings, metrics_enabled=False, otel_settings=OTelSettings(enabled=True)
    )
    collector = _capture_logs(settings)
    client = TestClient(app)
    client.get("/orders")
    client.get("/orders")
    shutdown_logging()

    [first, _] = [p["event"] for p in collector.payloads if p["message"] == "request.completed"]
    assert first["db"]["count"] == 12
    assert first["http"]["count"] == 1
    assert first["db"]["ms"] >= 0
    [warning] = [p for p in collector.payloads if p["message"] == "db.n_plus_one"]
    assert warning["level"] == "WARNING"
    assert warning["event"] == {
        "method": "GET",
        "route": "/orders",
        "statement": "SELECT * FROM items WHERE order_id = %(order_id)s AND status = ? LIMIT ?",
        "count": 12,
    }


def test_log_bytes_are_counted_once_per_line_by_route_and_exported() -> None:
    prometheus_client = pytest.importorskip("prometheus_client")
    settings = ObservabilitySettings()
    app = FastAPI()
    route = "/bytes/{item_id}"

    @app.get(route)
    async def get_item(item_id: str) -> dict[str, str]:
        logging.getLogger("tests.bytes").info("item read")
        return {}

    install_observability(app, settings, metrics_enabled=True)
    before = get_log_bytes_by_route().get(route, 0)
    collector = _capture_logs(settings, _Collector())  # two sinks, one count
    request_id = TestClient(app).get("/bytes/42").headers["x-request-id"]
    shutdown_logging()

    request_lines = [
        line for line in collector.lines if json.loads(line).get("request_id") == request_id
    ]
    assert len(request_lines) == 2
    assert get_log_bytes_by_route()[route] - before == sum(len(line) for line in request_lines)
    output = prometheus_client.generate_latest().decode()
    assert f'fastapiobserver_log_bytes_total{{route="{route}"}}' in output


def _n_plus_one_app(engine: Any, *, async_engine: bool = False) -> FastAPI:
    from sqlalchemy import text

    app = FastAPI()
    if async_engine:

        @app.get("/orders")
        async def list_orders_async() -> dict[str, str]:
            async with engine.connect() as conn:
                for order_id in range(12):
                    await conn.execute(text("SELECT :order_id"), {"order_id": order_id})
            return {}

    else:

        @app.get("/orders")
        def list_orders() -> dict[str, str]:
            with engine.connect() as conn:
                for order_id in range(12):
                    conn.execute(text("SELECT :order_id"), {"order_id": order_id})
            return {}

    return app


def _request_db_totals_and_warnings(
    app: FastAPI, settings: ObservabilitySettings, **install: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    install_observability(app, settings, metrics_enabled=False, **install)
    collector = _capture_logs(settings)
    client = TestClient(app)
    client.get("/orders")
    client.get("/orders")
    shutdown_logging()
    [first, _] = [p["event"] for p in collector.payloads if p["message"] == "request.completed"]
    warnings = [p["event"] for p in collector.payloads if p["message"] == "db.n_plus_one"]
    return first, warnings


def test_sqlalchemy_queries_are_counted_and_n_plus_one_reported_without_tracing() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    engine = sqlalchemy.create_engine("sqlite://")
    settings = ObservabilitySettings()

    first, warnings = _request_db_totals_and_warnings(
        _n_plus_one_app(engine), settings, db_engine=engine
    )

    assert first["db"]["count"] == 12
    assert first["db"]["ms"] > 0
    assert warnings == [{"method": "GET", "route": "/orders", "statement": "SELECT ?", "count": 12}]


def test_async_sqlalchemy_queries_are_counted_without_tracing() -> None:
    pytest.importorskip("aiosqlite")
    asyncio_ext = pytest.importorskip("sqlalchemy.ext.asyncio")
    from sqlalchemy.pool import NullPool

    engine = asyncio_ext.create_async_engine("sqlite+aiosqlite://", poolclass=NullPool)
    settings = ObservabilitySettings()

    first, warnings = _request_db_totals_and_warnings(
        _n_plus_one_app(engine, async_engine=True), settings, db_engine=engine
    )

    assert first["db"]["count"] == 12
    assert [w["count"] for w in warnings] == [12]


def test_sqlalchemy_queries_count_once_with_tracing_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    in_memory = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    trace_api = pytest.importorskip("opentelemetry.trace")
    from fastapiobserver.db_queries import SQLALCHEMY_INSTRUMENTATION, count_queries

    monkeypatch.setattr(
        otel_tracing_module, "build_otlp_exporter", lambda *_: in_memory.InMemorySpanExporter()
    )
    engine = sqlalchemy.create_engine("sqlite://")
    count_queries(engine)
    settings = ObservabilitySettings()
    app = FastAPI()

    @app.get("/orders")
    def list_orders() -> dict[str, str]:
        # Each query as SQLAlchemy's instrumentation would trace it, where it can.
        tracer = trace_api.get_tracer(SQLALCHEMY_INSTRUMENTATION)
        with engine.connect() as conn:
            for order_id in range(12):
                with tracer.start_as_current_span(
                    "SELECT",
                    kind=trace_api.SpanKind.CLIENT,
                    attributes={"db.system": "sqlite", "db.statement": "SELECT ?"},
                ):
                    conn.execute(sqlalchemy.text("SELECT :order_id"), {"order_id": order_id})
        return {}

    first, _ = _request_db_totals_and_warnings(
        app, settings, otel_settings=OTelSettings(enabled=True)
    )

    assert first["db"]["count"] == 12
