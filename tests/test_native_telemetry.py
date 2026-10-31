"""FastAPI 0.142+ traces requests itself; fastapi-observer uses that and keeps
FastAPI's own log export out of the way of redaction."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from fastapiobserver import (
    ObservabilitySettings,
    OTelLogsSettings,
    OTelSettings,
    install_observability,
)
from fastapiobserver.logging import setup_logging, shutdown_logging
import fastapiobserver.otel.logs as otel_logs_module
import fastapiobserver.otel.tracing as otel_tracing_module

pytest.importorskip("fastapi.telemetry")
trace_api = pytest.importorskip("opentelemetry.trace")
in_memory_spans = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.payloads: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.payloads.append(json.loads(self.format(record)))


def _server_spans(exporter: Any) -> list[Any]:
    trace_api.get_tracer_provider().force_flush()
    return [s for s in exporter.get_finished_spans() if s.kind == trace_api.SpanKind.SERVER]


def test_requests_are_traced_by_fastapi_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = in_memory_spans.InMemorySpanExporter()
    monkeypatch.setattr(otel_tracing_module, "build_otlp_exporter", lambda *_: exporter)
    settings = ObservabilitySettings()
    app = FastAPI()

    @app.get("/orders/{order_id}")
    def order(order_id: str) -> dict[str, str]:
        return {}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {}

    @app.get("/internal", include_in_schema=False)
    def internal() -> dict[str, str]:
        return {}

    install_observability(app, settings, otel_settings=OTelSettings(enabled=True))
    collector = _Collector()
    setup_logging(settings, logs_mode="otlp", extra_handlers=[collector])
    with TestClient(app) as client:  # the lifespan adds hidden routes to the exclusions
        for path in ("/orders/1", "/healthz", "/internal"):
            client.get(path)
    shutdown_logging()

    [span] = _server_spans(exporter)  # excluded and hidden routes aren't traced
    assert span.instrumentation_scope.name == "fastapi"
    assert span.name == "GET /orders/{order_id}"
    [completed] = [
        p
        for p in collector.payloads
        if p["message"] == "request.completed" and p["event"]["path"] == "/orders/1"
    ]
    assert completed["trace_id"] == f"{span.context.trace_id:032x}"


def test_instrumentation_traces_when_fastapi_tracing_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = in_memory_spans.InMemorySpanExporter()
    monkeypatch.setattr(otel_tracing_module, "build_otlp_exporter", lambda *_: exporter)
    app = FastAPI(telemetry={"tracing": False})  # as on FastAPI before 0.142

    @app.get("/orders/{order_id}")
    def order(order_id: str) -> dict[str, str]:
        return {}

    install_observability(app, ObservabilitySettings(), otel_settings=OTelSettings(enabled=True))
    TestClient(app).get("/orders/1")
    shutdown_logging()

    [span] = _server_spans(exporter)
    assert span.instrumentation_scope.name == "opentelemetry.instrumentation.fastapi"


@pytest.mark.parametrize("tracing", [False, True])
def test_fastapi_does_not_export_its_own_logs_past_redaction(
    monkeypatch: pytest.MonkeyPatch, tracing: bool
) -> None:
    in_memory_logs = pytest.importorskip("opentelemetry.sdk._logs.export")
    logs_api = pytest.importorskip("opentelemetry._logs")
    exported = in_memory_logs.InMemoryLogRecordExporter()
    monkeypatch.setattr(otel_logs_module, "build_otlp_exporter", lambda *_: exported)
    monkeypatch.setattr(
        otel_tracing_module,
        "build_otlp_exporter",
        lambda *_: in_memory_spans.InMemorySpanExporter(),
    )
    app = FastAPI()

    @app.get("/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("upstream rejected token=sk_live_secret")

    install_observability(
        app,
        ObservabilitySettings(),
        otel_settings=OTelSettings(enabled=tracing),
        otel_logs_settings=OTelLogsSettings(enabled=True, logs_mode="otlp"),
    )
    TestClient(app, raise_server_exceptions=False).get("/boom")
    shutdown_logging()
    logs_api.get_logger_provider().force_flush()

    scopes = {record.instrumentation_scope.name for record in exported.get_finished_logs()}
    assert "fastapiobserver.middleware" in scopes  # ours, redacted, still exported
    assert "fastapi" not in scopes
