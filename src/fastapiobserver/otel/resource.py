"""OTel resource creation, URL exclusion, and endpoint normalization helpers."""

from __future__ import annotations

import logging
import os
from types import ModuleType
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

from ..config import ObservabilitySettings
from ..utils import lazy_import
from .settings import OTelLogsSettings, OTelMetricsSettings, OTelSettings
from .types import (
    LogExporterLike,
    MetricExporterLike,
    OTelResourceLike,
    SpanExporterLike,
)

_LOGGER = logging.getLogger("fastapiobserver.otel")


def create_otel_resource(
    settings: ObservabilitySettings,
    otel_settings: OTelSettings,
) -> OTelResourceLike:
    resources_module = import_otel_module("opentelemetry.sdk.resources")
    resource_attrs = {
        "service.name": otel_settings.service_name or settings.service,
        "service.version": otel_settings.service_version or settings.version,
        "deployment.environment.name": otel_settings.environment or settings.environment,
        "service.namespace": settings.app_name,
    }
    resource_attrs.update(otel_settings.extra_resource_attributes)
    return resources_module.Resource.create(resource_attrs)


# ---------------------------------------------------------------------------
# Excluded URLs — auto-derived from settings (WS4)
# ---------------------------------------------------------------------------


def build_excluded_urls_csv(settings: ObservabilitySettings) -> str | None:
    """Build a comma-separated string of excluded URLs for OTel tracing.

    Precedence (highest to lowest):

    1. **Explicit config** — ``otel_excluded_urls`` set in
       ``ObservabilitySettings``.
    2. **OTel env vars** — ``OTEL_PYTHON_FASTAPI_EXCLUDED_URLS`` or
       ``OTEL_PYTHON_EXCLUDED_URLS`` set in the environment.  We return
       ``None`` so the OTel SDK handles them natively.
    3. **Package defaults** — auto-derived from ``metrics_path``,
       ``metrics_exclude_paths``, and the control-plane default path.
    """
    # --- Tier 1: explicit config (includes explicit empty = 'trace everything') ---
    if settings.otel_excluded_urls is not None:
        return ",".join(settings.otel_excluded_urls)  # "" means no exclusions

    # --- Tier 2: OTel env vars — let SDK handle natively ---
    if os.environ.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS") is not None or os.environ.get(
        "OTEL_PYTHON_EXCLUDED_URLS"
    ) is not None:
        return None

    # --- Tier 3: safe package defaults ---
    urls: set[str] = set(settings.metrics_exclude_paths)
    urls.add(settings.metrics_path)
    urls.add("/_observability/control")  # RuntimeControlSettings.path default
    return ",".join(sorted(urls)) or None


# ---------------------------------------------------------------------------
# Span exporter builder
# ---------------------------------------------------------------------------


def build_span_exporter(otel_settings: OTelSettings) -> SpanExporterLike:
    endpoint = normalize_otlp_endpoint(
        otel_settings.otlp_endpoint,
        otel_settings.protocol,
    )

    if otel_settings.protocol == "http/protobuf":
        exporter_module = import_otel_module(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
    else:
        exporter_module = import_otel_module(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
        )

    if endpoint:
        return exporter_module.OTLPSpanExporter(endpoint=endpoint)
    return exporter_module.OTLPSpanExporter()


# ---------------------------------------------------------------------------
# Log exporter builder
# ---------------------------------------------------------------------------


def build_log_exporter(otel_logs_settings: OTelLogsSettings) -> LogExporterLike:
    """Build the OTLP log exporter based on protocol."""
    endpoint = otel_logs_settings.otlp_endpoint
    if otel_logs_settings.protocol == "http/protobuf":
        exporter_module = import_otel_module(
            "opentelemetry.exporter.otlp.proto.http._log_exporter"
        )
    else:
        exporter_module = import_otel_module(
            "opentelemetry.exporter.otlp.proto.grpc._log_exporter"
        )
    if endpoint:
        return exporter_module.OTLPLogExporter(endpoint=endpoint)
    return exporter_module.OTLPLogExporter()


# ---------------------------------------------------------------------------
# Metric exporter builder
# ---------------------------------------------------------------------------


def build_metric_exporter(
    otel_metrics_settings: OTelMetricsSettings,
) -> MetricExporterLike:
    endpoint = normalize_otlp_metrics_endpoint(
        otel_metrics_settings.otlp_endpoint,
        otel_metrics_settings.protocol,
    )
    if otel_metrics_settings.protocol == "http/protobuf":
        exporter_module = import_otel_module(
            "opentelemetry.exporter.otlp.proto.http.metric_exporter"
        )
    else:
        exporter_module = import_otel_module(
            "opentelemetry.exporter.otlp.proto.grpc.metric_exporter"
        )

    if endpoint:
        return exporter_module.OTLPMetricExporter(endpoint=endpoint)
    return exporter_module.OTLPMetricExporter()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def import_otel_module(name: str) -> ModuleType:
    try:
        return lazy_import(name, package_hint="fastapi-observer[otel]")
    except RuntimeError as exc:
        raise RuntimeError(
            "OpenTelemetry support requires `pip install fastapi-observer[otel]`"
        ) from exc


def has_configured_tracer_provider(trace_api: Any, provider: Any) -> bool:
    proxy_provider = getattr(trace_api, "ProxyTracerProvider", None)
    if proxy_provider is not None and isinstance(provider, proxy_provider):
        return False
    if provider.__class__.__name__ in {"ProxyTracerProvider", "NoOpTracerProvider"}:
        return False
    return hasattr(provider, "add_span_processor")


def has_configured_logger_provider(log_api: Any, provider: Any) -> bool:
    proxy_provider = getattr(log_api, "ProxyLoggerProvider", None)
    if proxy_provider is not None and isinstance(provider, proxy_provider):
        return False
    if provider.__class__.__name__ in {"ProxyLoggerProvider", "NoOpLoggerProvider"}:
        return False
    return hasattr(provider, "get_logger")


def has_configured_meter_provider(metrics_api: Any, provider: Any) -> bool:
    proxy_provider = getattr(metrics_api, "ProxyMeterProvider", None)
    if proxy_provider is not None and isinstance(provider, proxy_provider):
        return False
    if provider.__class__.__name__ in {"ProxyMeterProvider", "NoOpMeterProvider"}:
        return False
    return hasattr(provider, "get_meter")


def normalize_otlp_endpoint(
    endpoint: str | None,
    protocol: Literal["grpc", "http/protobuf"],
) -> str | None:
    return _normalize_otlp_endpoint_for_signal(
        endpoint,
        protocol,
        http_path="/v1/traces",
    )


def normalize_otlp_metrics_endpoint(
    endpoint: str | None,
    protocol: Literal["grpc", "http/protobuf"],
) -> str | None:
    return _normalize_otlp_endpoint_for_signal(
        endpoint,
        protocol,
        http_path="/v1/metrics",
    )


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
