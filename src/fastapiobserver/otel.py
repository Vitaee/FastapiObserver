from __future__ import annotations

import importlib
import logging
import threading
from typing import Any, Callable, Literal, Mapping
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .config import ObservabilitySettings
from .security import SecurityPolicy, sanitize_event

_RUNTIME_LOCK = threading.RLock()
_RUNTIME_TRACE_SAMPLING_RATIO = 1.0
OTEL_PROTOCOLS = {"grpc", "http/protobuf"}
_LOGGER = logging.getLogger("fastapiobserver.otel")
_OTLP_LOG_PROCESSOR_KEYS_ATTR = "_fastapiobserver_otlp_log_processor_keys"
_STANDARD_LOG_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)


class OTelSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    service_name: str = "api"
    service_version: str = "0.0.0"
    environment: str = "development"
    otlp_endpoint: str | None = None
    protocol: Literal["grpc", "http/protobuf"] = "grpc"
    trace_sampling_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    extra_resource_attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize_protocol(cls, value: object) -> str:
        normalized_protocol = str(value).strip().lower()
        if normalized_protocol not in OTEL_PROTOCOLS:
            raise ValueError(f"Invalid OTel protocol: {value}")
        return normalized_protocol

    @field_validator("extra_resource_attributes", mode="before")
    @classmethod
    def _parse_resource_attributes(cls, value: object) -> dict[str, str]:
        return _parse_resource_attributes(value)

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
            extra_resource_attributes=_parse_resource_attributes(
                env_settings.extra_resource_attributes
            ),
        )


class OTelLogsSettings(BaseModel):
    """Configuration for OTLP log export.

    ``logs_mode`` controls where logs are sent:

    * ``"local_json"`` — current behaviour, no OTLP export (default)
    * ``"otlp"`` — export via OTLP only
    * ``"both"`` — local JSON **and** OTLP export (useful during migration)
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    logs_mode: Literal["local_json", "otlp", "both"] = "local_json"
    otlp_endpoint: str | None = None
    protocol: Literal["grpc", "http/protobuf"] = "grpc"

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize_protocol(cls, value: object) -> str:
        normalized = str(value).strip().lower()
        return normalized if normalized in OTEL_PROTOCOLS else "grpc"

    @classmethod
    def from_env(cls) -> "OTelLogsSettings":
        """Load OTLP log settings from environment variables.

        Env vars
        --------
        ``OTEL_LOGS_ENABLED``   — bool, default ``False``
        ``OTEL_LOGS_MODE``      — ``local_json`` | ``otlp`` | ``both``
        ``OTEL_LOGS_ENDPOINT``  — OTLP endpoint URL
        ``OTEL_LOGS_PROTOCOL``  — ``grpc`` | ``http/protobuf``
        """
        env = _OTelLogsEnvSettings()
        return cls(
            enabled=env.enabled,
            logs_mode=env.logs_mode,
            otlp_endpoint=env.otlp_endpoint,
            protocol=env.protocol,
        )


class _SanitizingOTLPLogHandler(logging.Handler):
    """Wrap OTLP LoggingHandler and sanitize custom record attributes.

    This ensures OTLP-exported log attributes follow the same security policy
    as structured JSON sink output.
    """

    def __init__(
        self,
        delegate: logging.Handler,
        *,
        security_policy: SecurityPolicy,
    ) -> None:
        super().__init__(level=delegate.level)
        self._delegate = delegate
        self._security_policy = security_policy

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sanitized_record = logging.makeLogRecord(record.__dict__.copy())
            _sanitize_record_custom_attributes(
                sanitized_record,
                self._security_policy,
            )
            self._delegate.emit(sanitized_record)
        except Exception:
            self.handleError(record)

    def setFormatter(self, fmt: logging.Formatter | None) -> None:  # noqa: N802
        super().setFormatter(fmt)
        self._delegate.setFormatter(fmt)

    def flush(self) -> None:
        self._delegate.flush()

    def close(self) -> None:
        try:
            self._delegate.close()
        finally:
            super().close()

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
    extra_resource_attributes: str | None = Field(
        default=None,
        validation_alias="OTEL_EXTRA_RESOURCE_ATTRIBUTES",
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


class _OTelLogsEnvSettings(BaseSettings):
    """Env-based settings for OTLP log export (12-factor parity)."""

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    enabled: bool = Field(default=False, validation_alias="OTEL_LOGS_ENABLED")
    logs_mode: Literal["local_json", "otlp", "both"] = Field(
        default="local_json", validation_alias="OTEL_LOGS_MODE"
    )
    otlp_endpoint: str | None = Field(
        default=None, validation_alias="OTEL_LOGS_ENDPOINT"
    )
    protocol: Literal["grpc", "http/protobuf"] = Field(
        default="grpc", validation_alias="OTEL_LOGS_PROTOCOL"
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

    @field_validator("logs_mode", mode="before")
    @classmethod
    def _normalize_logs_mode(cls, value: object) -> str:
        normalized = str(value).strip().lower()
        if normalized not in ("local_json", "otlp", "both"):
            return "local_json"
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
    resource_attrs.update(otel_settings.extra_resource_attributes)
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
    current_provider = trace_api.get_tracer_provider()
    has_external_provider = _has_configured_tracer_provider(trace_api, current_provider)

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

    tracer_provider = current_provider
    if not has_external_provider:
        sampler = sampling.ParentBased(DynamicTraceIdRatioSampler())
        resource = create_otel_resource(settings, otel_settings)
        tracer_provider = trace_sdk.TracerProvider(resource=resource, sampler=sampler)

        exporter = _build_span_exporter(otel_settings)
        tracer_provider.add_span_processor(trace_export.BatchSpanProcessor(exporter))

        try:
            trace_api.set_tracer_provider(tracer_provider)
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
    excluded_urls_str = _build_excluded_urls_csv(settings)
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

    app.state._fastapiobserver_otel_installed = True


# ---------------------------------------------------------------------------
# OTLP Log Export (WS3) — reuses same resource attrs as traces (DRY)
# ---------------------------------------------------------------------------


def install_otel_logs(
    settings: ObservabilitySettings,
    otel_logs_settings: OTelLogsSettings,
    *,
    otel_settings: OTelSettings | None = None,
    security_policy: SecurityPolicy | None = None,
) -> logging.Handler | None:
    """Configure ``LoggerProvider`` with OTLP export for structured logs.

    Uses the same OTel resource attributes as traces to ensure
    consistent service identity across all signals.

    Returns the ``LoggingHandler`` so that the caller can route it
    through the existing ``QueueListener`` pipeline, ensuring filters
    (request ID, trace context) and sanitization are applied
    consistently to both local JSON and OTLP logs.
    """
    if otel_logs_settings.logs_mode == "local_json":
        return None

    try:
        otel_logs_sdk = _import_otel_module("opentelemetry.sdk._logs")
        otel_logs_export = _import_otel_module("opentelemetry.sdk._logs.export")
        otel_log_api = _import_otel_module("opentelemetry._logs")
    except RuntimeError:
        _LOGGER.warning(
            "otel.logs.sdk_unavailable",
            extra={
                "event": {
                    "message": "OTLP log export requires opentelemetry-sdk.",
                },
                "_skip_enrichers": True,
            },
        )
        return None

    policy = security_policy or SecurityPolicy()

    # Re-use resource from trace settings when available.
    trace_otel = otel_settings or OTelSettings(
        service_name=settings.service,
        service_version=settings.version,
        environment=settings.environment,
    )
    resource = create_otel_resource(settings, trace_otel)
    processor_key = (
        otel_logs_settings.protocol,
        otel_logs_settings.otlp_endpoint or "__default__",
    )

    logger_provider = otel_log_api.get_logger_provider()
    has_external_provider = _has_configured_logger_provider(
        otel_log_api,
        logger_provider,
    )
    if not has_external_provider:
        candidate_provider = otel_logs_sdk.LoggerProvider(resource=resource)
        attached = _attach_log_processor_once(
            candidate_provider,
            processor_key,
            lambda: otel_logs_export.BatchLogRecordProcessor(
                _build_log_exporter(otel_logs_settings),
            ),
        )
        if not attached:
            _LOGGER.warning(
                "otel.logs.processor_attach.failed",
                extra={"_skip_enrichers": True},
            )
            return None
        try:
            otel_log_api.set_logger_provider(candidate_provider)
            logger_provider = candidate_provider
        except Exception:
            logger_provider = otel_log_api.get_logger_provider()
            attached = _attach_log_processor_once(
                logger_provider,
                processor_key,
                lambda: otel_logs_export.BatchLogRecordProcessor(
                    _build_log_exporter(otel_logs_settings),
                ),
            )
            if not attached:
                _LOGGER.warning(
                    "otel.logs.provider_already_configured",
                    extra={"_skip_enrichers": True},
                )
                return None
    elif otel_logs_settings.otlp_endpoint:
        attached = _attach_log_processor_once(
            logger_provider,
            processor_key,
            lambda: otel_logs_export.BatchLogRecordProcessor(
                _build_log_exporter(otel_logs_settings),
            ),
        )
        if not attached:
            _LOGGER.warning(
                "otel.logs.external_provider_without_processor_hook",
                extra={
                    "event": {
                        "provider_class": logger_provider.__class__.__name__,
                    },
                    "_skip_enrichers": True,
                },
            )

    # Return handler for routing through QueueListener instead of attaching
    # directly to root. This keeps a single logging pipeline.
    try:
        raw_handler = otel_logs_sdk.LoggingHandler(
            level=logging.NOTSET,
            logger_provider=logger_provider,
        )
    except Exception:
        _LOGGER.debug(
            "otel.logs.handler_create.failed",
            exc_info=True,
            extra={"_skip_enrichers": True},
        )
        return None

    otel_handler = _SanitizingOTLPLogHandler(
        raw_handler,
        security_policy=policy,
    )
    _LOGGER.info(
        "otel.logs.installed",
        extra={
            "event": {
                "logs_mode": otel_logs_settings.logs_mode,
                "endpoint": otel_logs_settings.otlp_endpoint or "default",
                "provider_class": logger_provider.__class__.__name__,
            },
            "_skip_enrichers": True,
        },
    )
    return otel_handler


def _build_log_exporter(otel_logs_settings: OTelLogsSettings) -> Any:
    """Build the OTLP log exporter based on protocol."""
    endpoint = otel_logs_settings.otlp_endpoint
    if otel_logs_settings.protocol == "http/protobuf":
        exporter_module = _import_otel_module(
            "opentelemetry.exporter.otlp.proto.http._log_exporter"
        )
    else:
        exporter_module = _import_otel_module(
            "opentelemetry.exporter.otlp.proto.grpc._log_exporter"
        )
    if endpoint:
        return exporter_module.OTLPLogExporter(endpoint=endpoint)
    return exporter_module.OTLPLogExporter()


# ---------------------------------------------------------------------------
# Excluded URLs — auto-derived from settings (WS4)
# ---------------------------------------------------------------------------


def _build_excluded_urls_csv(settings: ObservabilitySettings) -> str | None:
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
    import os

    if os.environ.get("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS") is not None or os.environ.get(
        "OTEL_PYTHON_EXCLUDED_URLS"
    ) is not None:
        return None

    # --- Tier 3: safe package defaults ---
    urls: set[str] = set(settings.metrics_exclude_paths)
    urls.add(settings.metrics_path)
    urls.add("/_observability/control")  # RuntimeControlSettings.path default
    return ",".join(sorted(urls)) or None


def _build_span_exporter(otel_settings: OTelSettings) -> Any:
    endpoint = _normalize_otlp_endpoint(
        otel_settings.otlp_endpoint,
        otel_settings.protocol,
    )

    if otel_settings.protocol == "http/protobuf":
        exporter_module = _import_otel_module(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter"
        )
    else:
        exporter_module = _import_otel_module(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
        )

    if endpoint:
        return exporter_module.OTLPSpanExporter(endpoint=endpoint)
    return exporter_module.OTLPSpanExporter()


def _import_otel_module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenTelemetry support requires `pip install fastapi-observer[otel]`"
        ) from exc


def _has_configured_tracer_provider(trace_api: Any, provider: Any) -> bool:
    proxy_provider = getattr(trace_api, "ProxyTracerProvider", None)
    if proxy_provider is not None and isinstance(provider, proxy_provider):
        return False
    if provider.__class__.__name__ in {"ProxyTracerProvider", "NoOpTracerProvider"}:
        return False
    return hasattr(provider, "add_span_processor")


def _has_configured_logger_provider(log_api: Any, provider: Any) -> bool:
    proxy_provider = getattr(log_api, "ProxyLoggerProvider", None)
    if proxy_provider is not None and isinstance(provider, proxy_provider):
        return False
    if provider.__class__.__name__ in {"ProxyLoggerProvider", "NoOpLoggerProvider"}:
        return False
    return hasattr(provider, "get_logger")


def _attach_log_processor_once(
    logger_provider: Any,
    key: tuple[str, str],
    build_processor: Callable[[], Any],
) -> bool:
    if not hasattr(logger_provider, "add_log_record_processor"):
        return False

    existing_keys: set[tuple[str, str]]
    existing_keys = getattr(logger_provider, _OTLP_LOG_PROCESSOR_KEYS_ATTR, set())
    if not isinstance(existing_keys, set):
        try:
            existing_keys = set(existing_keys)
        except TypeError:
            existing_keys = set()
    if key in existing_keys:
        return True

    try:
        logger_provider.add_log_record_processor(build_processor())
    except Exception:
        return False

    existing_keys.add(key)
    try:
        setattr(logger_provider, _OTLP_LOG_PROCESSOR_KEYS_ATTR, existing_keys)
    except Exception:
        # Some provider implementations may not allow custom attributes.
        pass
    return True


def _sanitize_record_custom_attributes(
    record: logging.LogRecord,
    policy: SecurityPolicy,
) -> None:
    custom_attributes = {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_LOG_RECORD_ATTRS and not key.startswith("_")
    }
    if not custom_attributes:
        return

    sanitized_attributes = sanitize_event(custom_attributes, policy)
    for key in custom_attributes:
        record.__dict__.pop(key, None)
    for key, value in sanitized_attributes.items():
        record.__dict__[key] = value


def _parse_resource_attributes(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key).strip(): str(val).strip() for key, val in value.items() if str(key).strip()}
    if isinstance(value, str):
        attributes: dict[str, str] = {}
        for item in value.split(","):
            candidate = item.strip()
            if not candidate:
                continue
            if "=" not in candidate:
                raise ValueError(
                    "Invalid OTEL extra resource attribute format; expected key=value"
                )
            key, raw_value = candidate.split("=", 1)
            normalized_key = key.strip()
            if not normalized_key:
                raise ValueError(
                    "Invalid OTEL extra resource attribute format; key cannot be empty"
                )
            attributes[normalized_key] = raw_value.strip()
        return attributes
    raise ValueError("extra_resource_attributes must be a mapping or key=value CSV string")


def _normalize_otlp_endpoint(
    endpoint: str | None,
    protocol: Literal["grpc", "http/protobuf"],
) -> str | None:
    if endpoint is None:
        return None
    normalized_endpoint = endpoint.strip()
    if not normalized_endpoint:
        return None
    if protocol == "grpc":
        parsed = urlparse(normalized_endpoint)
        grpc_path = parsed.path.rstrip("/")
        if grpc_path.endswith("/v1/traces"):
            raise ValueError(
                "gRPC OTLP endpoint must not include '/v1/traces'. "
                "Use protocol='http/protobuf' for '/v1/traces' endpoints, "
                "or remove the path for gRPC."
            )
        return normalized_endpoint

    parsed = urlparse(normalized_endpoint)
    if parsed.path and parsed.path != "/":
        return normalized_endpoint
    return urlunparse(parsed._replace(path="/v1/traces"))
