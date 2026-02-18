from __future__ import annotations

import importlib
import threading
from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .config import ObservabilitySettings

_RUNTIME_LOCK = threading.RLock()
_RUNTIME_TRACE_SAMPLING_RATIO = 1.0
OTEL_PROTOCOLS = {"grpc", "http/protobuf"}


class OTelSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    service_name: str = "api"
    service_version: str = "0.0.0"
    environment: str = "development"
    otlp_endpoint: str | None = None
    protocol: Literal["grpc", "http/protobuf"] = "grpc"
    trace_sampling_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize_protocol(cls, value: object) -> str:
        normalized_protocol = str(value).strip().lower()
        if normalized_protocol not in OTEL_PROTOCOLS:
            raise ValueError(f"Invalid OTel protocol: {value}")
        return normalized_protocol

    @classmethod
    def from_env(
        cls,
        settings: ObservabilitySettings | None = None,
    ) -> "OTelSettings":
        default_service_name = settings.service if settings else "api"
        default_service_version = settings.version if settings else "0.0.0"
        default_environment = settings.environment if settings else "development"
        env_settings = _OTelEnvSettings()

        return cls(
            enabled=env_settings.enabled,
            service_name=env_settings.service_name or default_service_name,
            service_version=env_settings.service_version or default_service_version,
            environment=env_settings.environment or default_environment,
            otlp_endpoint=env_settings.otlp_endpoint,
            protocol=env_settings.protocol,
            trace_sampling_ratio=env_settings.trace_sampling_ratio,
        )


class _OTelEnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    enabled: bool = Field(default=False, validation_alias="OTEL_ENABLED")
    service_name: str | None = Field(default=None, validation_alias="OTEL_SERVICE_NAME")
    service_version: str | None = Field(
        default=None,
        validation_alias="OTEL_SERVICE_VERSION",
    )
    environment: str | None = Field(default=None, validation_alias="OTEL_ENVIRONMENT")
    otlp_endpoint: str | None = Field(
        default=None,
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )
    protocol: Literal["grpc", "http/protobuf"] = Field(
        default="grpc",
        validation_alias="OTEL_EXPORTER_OTLP_PROTOCOL",
    )
    trace_sampling_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        validation_alias="OTEL_TRACE_SAMPLING_RATIO",
    )

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize_protocol_env(cls, value: object) -> str:
        if value is None:
            return "grpc"
        normalized = str(value).strip().lower()
        if normalized not in OTEL_PROTOCOLS:
            return "grpc"
        return normalized


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
    if getattr(app.state, "_fastapiobserver_otel_installed", False):
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

    app.state._fastapiobserver_otel_installed = True


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
            "OpenTelemetry support requires `pip install fastapi-observer[otel]`"
        ) from exc
