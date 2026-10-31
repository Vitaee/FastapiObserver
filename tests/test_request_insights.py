from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from fastapiobserver import ObservabilitySettings, OTelSettings, install_observability
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
