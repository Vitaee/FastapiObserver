"""OTLP metrics export installation."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from ..config import ObservabilitySettings
from .lifecycle import build_provider_shutdown_callback, register_shutdown_hook
from .resource import (
    build_metric_exporter,
    create_otel_resource,
    has_configured_meter_provider,
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
    has_external_provider = has_configured_meter_provider(metrics_api, meter_provider)
    provider_owned = False

    if not has_external_provider:
        trace_otel = otel_settings or OTelSettings(
            service_name=settings.service,
            service_version=settings.version,
            environment=settings.environment,
        )
        resource = create_otel_resource(settings, trace_otel)
        reader = metrics_export.PeriodicExportingMetricReader(
            build_metric_exporter(otel_metrics_settings),
            export_interval_millis=otel_metrics_settings.export_interval_millis,
        )
        candidate_provider = metrics_sdk.MeterProvider(
            resource=resource,
            metric_readers=[reader],
        )
        try:
            metrics_api.set_meter_provider(candidate_provider)
            meter_provider = candidate_provider
            provider_owned = True
        except Exception:
            meter_provider = metrics_api.get_meter_provider()
            _LOGGER.warning(
                "otel.metrics.provider_already_configured",
                extra={"_skip_enrichers": True},
            )
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

    register_shutdown_hook(
        key=f"otel.meter_provider.{id(meter_provider)}",
        callback=build_provider_shutdown_callback(
            meter_provider,
            logger=_LOGGER,
            component="meter_provider",
            shutdown=provider_owned,
        ),
        app=app,
        logger=_LOGGER,
    )
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
