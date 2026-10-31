"""OTLP metrics export installation."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from ..config import ObservabilitySettings
from .lifecycle import close_on_app_shutdown
from .resource import (
    build_otlp_exporter,
    create_otel_resource,
    has_configured_provider,
    import_otel_module,
)
from .settings import OTelMetricsSettings, OTelSettings

_LOGGER = logging.getLogger("fastapiobserver.otel")


def install_otel_metrics(
    settings: ObservabilitySettings,
    otel_metrics_settings: OTelMetricsSettings,
    *,
    app: FastAPI | None = None,
    otel_settings: OTelSettings | None = None,
) -> Any | None:
    """Configure OTel metrics export via OTLP."""
    if not otel_metrics_settings.enabled:
        return None

    try:
        metrics_api = import_otel_module("opentelemetry.metrics")
        metrics_sdk = import_otel_module("opentelemetry.sdk.metrics")
        metrics_export = import_otel_module("opentelemetry.sdk.metrics.export")
    except RuntimeError:
        _LOGGER.warning(
            "otel.metrics.sdk_unavailable",
            extra={
                "event": {"message": "OTLP metrics export requires opentelemetry-sdk."},
                "_skip_enrichers": True,
            },
        )
        return None

    meter_provider = metrics_api.get_meter_provider()
    provider_owned = not has_configured_provider(meter_provider)

    if provider_owned:
        trace_otel = otel_settings or OTelSettings(
            service_name=settings.service,
            service_version=settings.version,
            environment=settings.environment,
        )
        reader = metrics_export.PeriodicExportingMetricReader(
            build_otlp_exporter("metrics", otel_metrics_settings),
            export_interval_millis=otel_metrics_settings.export_interval_millis,
        )
        meter_provider = metrics_sdk.MeterProvider(
            resource=create_otel_resource(settings, trace_otel),
            metric_readers=[reader],
        )
        metrics_api.set_meter_provider(meter_provider)
    elif otel_metrics_settings.otlp_endpoint:
        _LOGGER.warning(
            "otel.metrics.external_provider.detected",
            extra={
                "event": {
                    "provider_class": meter_provider.__class__.__name__,
                    "otlp_endpoint": otel_metrics_settings.otlp_endpoint,
                },
                "_skip_enrichers": True,
            },
        )

    close_on_app_shutdown(app, meter_provider, shutdown=provider_owned)
    _LOGGER.info(
        "otel.metrics.installed",
        extra={
            "event": {
                "endpoint": otel_metrics_settings.otlp_endpoint or "default",
                "protocol": otel_metrics_settings.protocol,
                "export_interval_millis": otel_metrics_settings.export_interval_millis,
                "provider_class": meter_provider.__class__.__name__,
            },
            "_skip_enrichers": True,
        },
    )
    return meter_provider
