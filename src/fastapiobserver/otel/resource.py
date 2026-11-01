"""OTel resource creation and endpoint normalization helpers."""

from __future__ import annotations

from types import ModuleType
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

from ..config import ObservabilitySettings
from ..utils import lazy_import
from .settings import OTelLogsSettings, OTelMetricsSettings, OTelSettings


def create_otel_resource(
    settings: ObservabilitySettings,
    otel_settings: OTelSettings,
) -> Any:
    resources_module = import_otel_module("opentelemetry.sdk.resources")
    resource_attrs = {
        "service.name": otel_settings.service_name or settings.service,
        "service.version": otel_settings.service_version or settings.version,
        "deployment.environment.name": otel_settings.environment or settings.environment,
        "service.namespace": settings.app_name,
    }
    resource_attrs.update(otel_settings.extra_resource_attributes)
    return resources_module.Resource.create(resource_attrs)


_OTLP_EXPORTERS = {
    "traces": ("trace_exporter", "OTLPSpanExporter"),
    "metrics": ("metric_exporter", "OTLPMetricExporter"),
    "logs": ("_log_exporter", "OTLPLogExporter"),
}


def build_otlp_exporter(
    signal: Literal["traces", "metrics", "logs"],
    settings: OTelSettings | OTelLogsSettings | OTelMetricsSettings,
) -> Any:
    """Build the OTLP exporter for ``signal`` over the configured protocol."""
    module_name, class_name = _OTLP_EXPORTERS[signal]
    transport = "http" if settings.protocol == "http/protobuf" else "grpc"
    exporter_cls = getattr(
        import_otel_module(f"opentelemetry.exporter.otlp.proto.{transport}.{module_name}"),
        class_name,
    )
    endpoint = _normalize_otlp_endpoint_for_signal(
        settings.otlp_endpoint, settings.protocol, http_path=f"/v1/{signal}"
    )
    return exporter_cls(endpoint=endpoint) if endpoint else exporter_cls()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def import_otel_module(name: str) -> ModuleType:
    try:
        module: ModuleType = lazy_import(name, package_hint="fastapi-observer[otel]")
        return module
    except RuntimeError as exc:
        raise RuntimeError(
            "OpenTelemetry support requires `pip install fastapi-observer[otel]`"
        ) from exc


def has_configured_provider(provider: Any) -> bool:
    """False while a global OTel provider is still the API's proxy/no-op default."""
    return not any(
        cls.__name__.lstrip("_").startswith(("Proxy", "NoOp"))
        for cls in type(provider).__mro__
    )


def normalize_otlp_endpoint(
    endpoint: str | None,
    protocol: Literal["grpc", "http/protobuf"],
) -> str | None:
    return _normalize_otlp_endpoint_for_signal(endpoint, protocol, http_path="/v1/traces")


def normalize_otlp_metrics_endpoint(
    endpoint: str | None,
    protocol: Literal["grpc", "http/protobuf"],
) -> str | None:
    return _normalize_otlp_endpoint_for_signal(endpoint, protocol, http_path="/v1/metrics")


def _normalize_otlp_endpoint_for_signal(
    endpoint: str | None,
    protocol: Literal["grpc", "http/protobuf"],
    *,
    http_path: str,
) -> str | None:
    if endpoint is None:
        return None
    normalized_endpoint = endpoint.strip()
    if not normalized_endpoint:
        return None
    if protocol == "grpc":
        parsed = urlparse(normalized_endpoint)
        grpc_path = parsed.path.rstrip("/")
        if grpc_path.endswith(http_path):
            raise ValueError(
                f"gRPC OTLP endpoint must not include '{http_path}'. "
                f"Use protocol='http/protobuf' for '{http_path}' endpoints, "
                "or remove the path for gRPC."
            )
        return normalized_endpoint

    parsed = urlparse(normalized_endpoint)
    if parsed.path and parsed.path != "/":
        return normalized_endpoint
    return urlunparse(parsed._replace(path=http_path))
