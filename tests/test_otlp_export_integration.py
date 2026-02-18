from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from fastapiobserver import OTelSettings, ObservabilitySettings, install_observability

from tests.conftest_otlp import OtlpCollector


def test_spans_are_exported_to_otlp_collector(otlp_collector: OtlpCollector) -> None:
    pytest.importorskip("opentelemetry.trace")
    trace_api = pytest.importorskip("opentelemetry.trace")
    trace_export = pytest.importorskip("opentelemetry.sdk.trace.export")
    otlp_http_exporter = pytest.importorskip(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter"
    )

    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    install_observability(
        app,
        ObservabilitySettings(app_name="orders-api", service="orders", environment="test"),
        metrics_enabled=False,
        otel_settings=OTelSettings(
            enabled=True,
            service_name="orders",
            service_version="1.0.0",
            environment="test",
            protocol="http/protobuf",
            otlp_endpoint=otlp_collector.endpoint,
        ),
    )

    provider = trace_api.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        pytest.skip("Active tracer provider does not support span processors")

    exporter = otlp_http_exporter.OTLPSpanExporter(endpoint=otlp_collector.endpoint)
    provider.add_span_processor(trace_export.SimpleSpanProcessor(exporter))

    response = TestClient(app).get("/ping")
    assert response.status_code == 200

    if hasattr(provider, "force_flush"):
        provider.force_flush()

    deadline = time.time() + 3.0
    while time.time() < deadline and otlp_collector.span_count == 0:
        time.sleep(0.05)

    assert otlp_collector.span_count > 0
    assert any(path.endswith("/v1/traces") for path in otlp_collector.paths)
    assert any("ping" in span.name.lower() for span in otlp_collector.get_spans())
