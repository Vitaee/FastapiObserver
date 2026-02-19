"""OTel tracing installation for FastAPI applications."""

from __future__ import annotations

import logging
import weakref
from typing import Any

from fastapi import FastAPI

from ..config import ObservabilitySettings
from .lifecycle import build_provider_shutdown_callback, register_shutdown_hook
from .resource import (
    build_excluded_urls_csv,
    build_span_exporter,
    create_otel_resource,
    has_configured_tracer_provider,
    import_otel_module,
)
from .settings import OTelSettings, get_trace_sampling_ratio, set_trace_sampling_ratio

_LOGGER = logging.getLogger("fastapiobserver.otel")
_OTEL_INSTALLED_APPS: weakref.WeakSet[FastAPI] = weakref.WeakSet()


def install_otel(
    app: FastAPI,
    settings: ObservabilitySettings,
    otel_settings: OTelSettings,
) -> None:
    if not otel_settings.enabled:
        return
    if app in _OTEL_INSTALLED_APPS:
        return

    trace_api = import_otel_module("opentelemetry.trace")
    trace_sdk = import_otel_module("opentelemetry.sdk.trace")
    trace_export = import_otel_module("opentelemetry.sdk.trace.export")
    sampling = import_otel_module("opentelemetry.sdk.trace.sampling")
    fastapi_instrumentor_module = import_otel_module(
        "opentelemetry.instrumentation.fastapi"
    )
    logging_instrumentor_module = import_otel_module(
        "opentelemetry.instrumentation.logging"
    )

    set_trace_sampling_ratio(otel_settings.trace_sampling_ratio)
    current_provider = trace_api.get_tracer_provider()
    has_external_provider = has_configured_tracer_provider(trace_api, current_provider)
    provider_owned = False

    class DynamicTraceIdRatioSampler(sampling.Sampler): # type: ignore
        def should_sample(
            self,
            parent_context: Any,
            trace_id: int,
            name: str,
            kind: Any | None = None,
            attributes: dict[str, Any] | None = None,
            links: list[Any] | None = None,
            trace_state: Any | None = None,
        ) -> Any:
            delegate = sampling.TraceIdRatioBased(get_trace_sampling_ratio())
            return delegate.should_sample(
                parent_context,
                trace_id,
                name,
                kind=kind,
                attributes=attributes,
                links=links,
                trace_state=trace_state,
            )

        def get_description(self) -> str:
            return "DynamicTraceIdRatioSampler"

    tracer_provider = current_provider
    if not has_external_provider:
        sampler = sampling.ParentBased(DynamicTraceIdRatioSampler())
        resource = create_otel_resource(settings, otel_settings)
        tracer_provider = trace_sdk.TracerProvider(resource=resource, sampler=sampler)

        exporter = build_span_exporter(otel_settings)
        tracer_provider.add_span_processor(trace_export.BatchSpanProcessor(exporter))

        try:
            trace_api.set_tracer_provider(tracer_provider)
            provider_owned = True
        except Exception:
            tracer_provider = trace_api.get_tracer_provider()
            _LOGGER.warning(
                "otel.tracer_provider.already_configured",
                extra={
                    "event": {
                        "provider_class": tracer_provider.__class__.__name__,
                    },
                    "_skip_enrichers": True,
                },
            )
            provider_owned = False
    elif otel_settings.otlp_endpoint:
        _LOGGER.warning(
            "otel.external_provider.detected",
            extra={
                "event": {
                    "provider_class": tracer_provider.__class__.__name__,
                    "otlp_endpoint": otel_settings.otlp_endpoint,
                },
                "_skip_enrichers": True,
            },
        )

    # --- Wire excluded URLs for noise control ---
    excluded_urls_str = build_excluded_urls_csv(settings)
    instrument_kwargs: dict[str, Any] = {
        "tracer_provider": tracer_provider,
    }
    if excluded_urls_str is not None:
        instrument_kwargs["excluded_urls"] = excluded_urls_str

    fastapi_instrumentor_module.FastAPIInstrumentor.instrument_app(
        app, **instrument_kwargs
    )

    try:
        logging_instrumentor_module.LoggingInstrumentor().instrument(
            set_logging_format=False
        )
    except Exception:
        _LOGGER.debug(
            "otel.logging_instrumentor.failed",
            exc_info=True,
            extra={"_skip_enrichers": True},
        )

    if provider_owned:
        register_shutdown_hook(
            key=f"otel.tracer_provider.{id(tracer_provider)}",
            callback=build_provider_shutdown_callback(
                tracer_provider,
                logger=_LOGGER,
                component="tracer_provider",
                shutdown=True,
            ),
            app=app,
            logger=_LOGGER,
        )

    _OTEL_INSTALLED_APPS.add(app)
