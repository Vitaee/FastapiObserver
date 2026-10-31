"""OTel tracing installation for FastAPI applications."""

from __future__ import annotations

import logging
import weakref
from typing import Any

from fastapi import FastAPI

from ..config import ObservabilitySettings
from ..request_context import get_request_state
from .lifecycle import close_on_app_shutdown
from .native import trace_natively
from .resource import (
    build_excluded_urls_csv,
    build_otlp_exporter,
    create_otel_resource,
    has_configured_provider,
    import_otel_module,
)
from .settings import OTelSettings, get_trace_sampling_ratio, set_trace_sampling_ratio

_LOGGER = logging.getLogger("fastapiobserver.otel")
_OTEL_INSTALLED_APPS: weakref.WeakSet[FastAPI] = weakref.WeakSet()
_CALL_TOTALS_PROVIDERS: weakref.WeakSet[Any] = weakref.WeakSet()


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

    set_trace_sampling_ratio(otel_settings.trace_sampling_ratio)
    tracer_provider = trace_api.get_tracer_provider()
    provider_owned = not has_configured_provider(tracer_provider)

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

    if provider_owned:
        tracer_provider = trace_sdk.TracerProvider(
            resource=create_otel_resource(settings, otel_settings),
            sampler=sampling.ParentBased(DynamicTraceIdRatioSampler()),
        )
        tracer_provider.add_span_processor(
            trace_export.BatchSpanProcessor(build_otlp_exporter("traces", otel_settings))
        )
        trace_api.set_tracer_provider(tracer_provider)
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

    if (
        hasattr(tracer_provider, "add_span_processor")
        and tracer_provider not in _CALL_TOTALS_PROVIDERS
    ):
        tracer_provider.add_span_processor(_request_call_totals(trace_api, trace_sdk))
        _CALL_TOTALS_PROVIDERS.add(tracer_provider)

    # FastAPI 0.142+ traces requests itself, with spans for dependencies, the
    # endpoint and serialization; older versions need OpenTelemetry's instrumentation.
    excluded_urls_str = build_excluded_urls_csv(settings)
    if not trace_natively(app, excluded_urls_str):
        instrument_kwargs: dict[str, Any] = {"tracer_provider": tracer_provider}
        if excluded_urls_str is not None:
            instrument_kwargs["excluded_urls"] = excluded_urls_str
        import_otel_module(
            "opentelemetry.instrumentation.fastapi"
        ).FastAPIInstrumentor.instrument_app(app, **instrument_kwargs)

    if provider_owned:
        close_on_app_shutdown(app, tracer_provider, shutdown=True)

    _OTEL_INSTALLED_APPS.add(app)


def _request_call_totals(trace_api: Any, trace_sdk: Any) -> Any:
    """A span processor that adds each finished client span, a database query or
    an outbound HTTP call, to its request's ``db`` or ``http`` totals.

    It sees only spans that are sampled, from clients that are instrumented.
    """
    client_kind = trace_api.SpanKind.CLIENT

    class RequestCallTotals(trace_sdk.SpanProcessor):  # type: ignore
        def on_end(self, span: Any) -> None:
            request_state = get_request_state()
            if request_state is None or span.kind != client_kind or span.end_time is None:
                return
            attributes = span.attributes or {}
            seconds = (span.end_time - span.start_time) / 1e9
            # Old and new semantic-convention names; instrumentations emit either.
            if "db.system" in attributes or "db.system.name" in attributes:
                statement = attributes.get("db.query.text") or attributes.get("db.statement")
                request_state.add_call(
                    "db", seconds, statement if isinstance(statement, str) else None
                )
            elif "http.request.method" in attributes or "http.method" in attributes:
                request_state.add_call("http", seconds)

    return RequestCallTotals()
