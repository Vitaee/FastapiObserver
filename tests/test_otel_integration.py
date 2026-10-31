from __future__ import annotations

from fastapi import FastAPI
import pytest

from fastapiobserver.config import ObservabilitySettings
from fastapiobserver.otel import (
    OTelMetricsSettings,
    OTelSettings,
    create_otel_resource,
    install_otel_metrics,
    install_otel,
    set_trace_sampling_ratio,
)
import fastapiobserver.otel as otel_module
import fastapiobserver.otel.resource as otel_resource_module


def test_install_otel_missing_dependency_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    original_lazy_import = otel_resource_module.lazy_import

    def fake_lazy_import(
        module_path: str,
        attr: str | None = None,
        *,
        package_hint: str | None = None,
    ):
        if module_path.startswith("opentelemetry"):
            raise RuntimeError("missing dependency")
        return original_lazy_import(module_path, attr=attr, package_hint=package_hint)

    monkeypatch.setattr(otel_resource_module, "lazy_import", fake_lazy_import)

    with pytest.raises(RuntimeError, match=r"pip install fastapi-observer\[otel\]"):
        install_otel(
            FastAPI(),
            ObservabilitySettings(),
            OTelSettings(enabled=True, service_name="svc"),
        )


def test_install_otel_metrics_missing_dependency_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_lazy_import = otel_resource_module.lazy_import

    def fake_lazy_import(
        module_path: str,
        attr: str | None = None,
        *,
        package_hint: str | None = None,
    ):
        if module_path.startswith("opentelemetry"):
            raise RuntimeError("missing dependency")
        return original_lazy_import(module_path, attr=attr, package_hint=package_hint)

    monkeypatch.setattr(otel_resource_module, "lazy_import", fake_lazy_import)

    handler = install_otel_metrics(
        ObservabilitySettings(),
        OTelMetricsSettings(enabled=True, protocol="grpc"),
    )
    assert handler is None


def test_trace_sampling_ratio_is_clamped() -> None:
    assert set_trace_sampling_ratio(1.2) == 1.0
    assert set_trace_sampling_ratio(-0.5) == 0.0
    assert set_trace_sampling_ratio(0.33) == 0.33


def test_create_otel_resource_when_dependency_available() -> None:
    pytest.importorskip("opentelemetry.sdk.resources")

    resource = create_otel_resource(
        ObservabilitySettings(app_name="orders", service="orders", version="2.0.0"),
        OTelSettings(
            enabled=True,
            service_name="orders-api",
            service_version="2.0.0",
            environment="production",
        ),
    )

    assert resource.attributes["service.name"] == "orders-api"
    assert resource.attributes["service.version"] == "2.0.0"


def test_create_otel_resource_merges_extra_attributes() -> None:
    pytest.importorskip("opentelemetry.sdk.resources")

    resource = create_otel_resource(
        ObservabilitySettings(app_name="orders", service="orders", version="2.0.0"),
        OTelSettings(
            enabled=True,
            service_name="orders-api",
            service_version="2.0.0",
            environment="production",
            extra_resource_attributes={
                "k8s.namespace": "prod",
                "service.namespace": "platform",
            },
        ),
    )

    assert resource.attributes["k8s.namespace"] == "prod"
    assert resource.attributes["service.namespace"] == "platform"


def test_otel_settings_parse_extra_resource_attributes_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OTEL_EXTRA_RESOURCE_ATTRIBUTES",
        "k8s.namespace=prod,custom.team=platform",
    )

    settings = OTelSettings.from_env()

    assert settings.extra_resource_attributes == {
        "k8s.namespace": "prod",
        "custom.team": "platform",
    }


def test_normalize_http_otlp_endpoint_adds_default_trace_path() -> None:
    normalized = otel_module.normalize_otlp_endpoint(
        "http://127.0.0.1:4318",
        "http/protobuf",
    )
    assert normalized == "http://127.0.0.1:4318/v1/traces"


def test_normalize_http_otlp_endpoint_keeps_explicit_path() -> None:
    normalized = otel_module.normalize_otlp_endpoint(
        "http://127.0.0.1:4318/custom/path",
        "http/protobuf",
    )
    assert normalized == "http://127.0.0.1:4318/custom/path"


def test_normalize_grpc_otlp_endpoint_rejects_http_trace_path() -> None:
    with pytest.raises(ValueError, match=r"must not include '/v1/traces'"):
        otel_module.normalize_otlp_endpoint(
            "http://127.0.0.1:4317/v1/traces",
            "grpc",
        )


def test_normalize_grpc_otlp_endpoint_keeps_valid_endpoint() -> None:
    normalized = otel_module.normalize_otlp_endpoint(
        "http://127.0.0.1:4317",
        "grpc",
    )
    assert normalized == "http://127.0.0.1:4317"


def test_normalize_http_otlp_metrics_endpoint_adds_default_metrics_path() -> None:
    normalized = otel_module.normalize_otlp_metrics_endpoint(
        "http://127.0.0.1:4318",
        "http/protobuf",
    )
    assert normalized == "http://127.0.0.1:4318/v1/metrics"


def test_normalize_grpc_otlp_metrics_endpoint_rejects_http_metrics_path() -> None:
    with pytest.raises(ValueError, match=r"must not include '/v1/metrics'"):
        otel_module.normalize_otlp_metrics_endpoint(
            "http://127.0.0.1:4317/v1/metrics",
            "grpc",
        )


def test_http_logs_endpoint_gets_default_logs_path() -> None:
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http._log_exporter")
    from fastapiobserver.otel import OTelLogsSettings
    from fastapiobserver.otel.resource import build_otlp_exporter

    exporter = build_otlp_exporter(
        "logs",
        OTelLogsSettings(protocol="http/protobuf", otlp_endpoint="http://127.0.0.1:4318"),
    )
    assert exporter._endpoint == "http://127.0.0.1:4318/v1/logs"


def test_install_otel_metrics_replaces_default_proxy_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics_api = pytest.importorskip("opentelemetry.metrics")
    sdk_metrics = pytest.importorskip("opentelemetry.sdk.metrics")
    sdk_export = pytest.importorskip("opentelemetry.sdk.metrics.export")
    import io

    import fastapiobserver.otel.metrics as otel_metrics_module

    monkeypatch.setattr(
        otel_metrics_module,
        "build_otlp_exporter",
        lambda *_: sdk_export.ConsoleMetricExporter(out=io.StringIO()),
    )

    provider = install_otel_metrics(
        ObservabilitySettings(),
        OTelMetricsSettings(enabled=True, otlp_endpoint="http://127.0.0.1:4317"),
    )

    assert isinstance(provider, sdk_metrics.MeterProvider)
    assert metrics_api.get_meter_provider() is provider


def test_hidden_routes_added_after_install_are_not_traced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    in_memory = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    from fastapi.testclient import TestClient
    from opentelemetry import trace as trace_api

    from fastapiobserver import install_observability
    import fastapiobserver.otel.tracing as otel_tracing_module

    exporter = in_memory.InMemorySpanExporter()
    monkeypatch.setattr(otel_tracing_module, "build_otlp_exporter", lambda *_: exporter)

    app = FastAPI()
    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc"),
        metrics_enabled=False,
        otel_settings=OTelSettings(enabled=True),
    )

    @app.get("/visible")
    def visible() -> None:
        return None

    @app.get("/internal", include_in_schema=False)
    def internal() -> None:
        return None

    with TestClient(app) as client:  # runs the lifespan, which discovers routes
        assert client.get("/visible").status_code == 200
        assert client.get("/internal").status_code == 200
        trace_api.get_tracer_provider().force_flush()

    server_spans = [s.name for s in exporter.get_finished_spans() if s.kind.name == "SERVER"]
    assert server_spans == ["GET /visible"]
