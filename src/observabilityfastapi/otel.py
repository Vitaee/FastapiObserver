from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI

from .config import ObservabilitySettings

_RUNTIME_LOCK = threading.RLock()
_RUNTIME_TRACE_SAMPLING_RATIO = 1.0


@dataclass(frozen=True)
class OTelSettings:
    enabled: bool = False
    service_name: str = "api"
    service_version: str = "0.0.0"
    environment: str = "development"
    otlp_endpoint: str | None = None
    protocol: Literal["grpc", "http/protobuf"] = "grpc"
    trace_sampling_ratio: float = 1.0


def get_trace_sampling_ratio() -> float:
    with _RUNTIME_LOCK:
        return _RUNTIME_TRACE_SAMPLING_RATIO


def set_trace_sampling_ratio(ratio: float) -> float:
    value = max(0.0, min(1.0, float(ratio)))
    with _RUNTIME_LOCK:
        global _RUNTIME_TRACE_SAMPLING_RATIO
        _RUNTIME_TRACE_SAMPLING_RATIO = value
    return value


def create_otel_resource(settings: ObservabilitySettings, otel_settings: OTelSettings) -> Any:
    resources_module = _import_otel_module("opentelemetry.sdk.resources")
    resource_attrs = {
        "service.name": otel_settings.service_name or settings.service,
        "service.version": otel_settings.service_version or settings.version,
        "deployment.environment.name": otel_settings.environment or settings.environment,
        "service.namespace": settings.app_name,
    }
    return resources_module.Resource.create(resource_attrs)


def install_otel(
    app: FastAPI,
    settings: ObservabilitySettings,
    otel_settings: OTelSettings,
) -> None:
    if not otel_settings.enabled:
        return
    if getattr(app.state, "_observabilityfastapi_otel_installed", False):
        return

    trace_api = _import_otel_module("opentelemetry.trace")
    trace_sdk = _import_otel_module("opentelemetry.sdk.trace")
    trace_export = _import_otel_module("opentelemetry.sdk.trace.export")
    sampling = _import_otel_module("opentelemetry.sdk.trace.sampling")
    fastapi_instrumentor_module = _import_otel_module(
        "opentelemetry.instrumentation.fastapi"
    )
    logging_instrumentor_module = _import_otel_module(
        "opentelemetry.instrumentation.logging"
    )

    set_trace_sampling_ratio(otel_settings.trace_sampling_ratio)

    class DynamicTraceIdRatioSampler:
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

    sampler = sampling.ParentBased(DynamicTraceIdRatioSampler())
    resource = create_otel_resource(settings, otel_settings)
    tracer_provider = trace_sdk.TracerProvider(resource=resource, sampler=sampler)

    exporter = _build_span_exporter(otel_settings)
    tracer_provider.add_span_processor(trace_export.BatchSpanProcessor(exporter))

    try:
        trace_api.set_tracer_provider(tracer_provider)
    except Exception:
        # The process may already have a global provider set by the host app.
        pass

    fastapi_instrumentor_module.FastAPIInstrumentor.instrument_app(
        app, tracer_provider=tracer_provider
    )

    try:
        logging_instrumentor_module.LoggingInstrumentor().instrument(
            set_logging_format=False
        )
    except Exception:
        # Logging instrumentation is best-effort for compatibility.
        pass

    app.state._observabilityfastapi_otel_installed = True


def _build_span_exporter(otel_settings: OTelSettings) -> Any:
    if otel_settings.protocol == "http/protobuf":
        exporter_module = _import_otel_module(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
    else:
        exporter_module = _import_otel_module(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
        )

    if otel_settings.otlp_endpoint:
        return exporter_module.OTLPSpanExporter(endpoint=otel_settings.otlp_endpoint)
    return exporter_module.OTLPSpanExporter()


def _import_otel_module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenTelemetry support requires `pip install observabilityfastapi[otel]`"
        ) from exc
