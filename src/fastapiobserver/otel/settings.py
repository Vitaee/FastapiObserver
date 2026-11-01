"""OTel signal settings models with env-based loading."""

from __future__ import annotations

import threading
from typing import Any, Literal, Mapping, Self
from urllib.parse import unquote

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from ..utils import EnvLoadable, InternalSettingsBase, normalize_protocol

OTEL_PROTOCOLS = {"grpc", "http/protobuf"}
_RUNTIME_LOCK = threading.RLock()
_RUNTIME_TRACE_SAMPLING_RATIO = 1.0
_RUNTIME_SAMPLER_INSTALLED = False


class _BaseOTelSettings(EnvLoadable, BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    otlp_endpoint: str | None = None
    protocol: Literal["grpc", "http/protobuf"] = "grpc"

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize_protocol_base(cls, value: object) -> str:
        return normalize_protocol(
            value,
            allowed=OTEL_PROTOCOLS,
            default="grpc",
            strict=False,
            label="OTel protocol",
        )


class OTelSettings(_BaseOTelSettings):

    enabled: bool = False
    # None: ObservabilitySettings' service, version and environment, so traces
    # and metrics name the service the same way.
    service_name: str | None = None
    service_version: str | None = None
    environment: str | None = None
    trace_sampling_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    extra_resource_attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize_protocol(cls, value: object) -> str:
        return normalize_protocol(
            value,
            allowed=OTEL_PROTOCOLS,
            strict=True,
            label="OTel protocol",
        )

    @field_validator("extra_resource_attributes", mode="before")
    @classmethod
    def _parse_resource_attributes(cls, value: object) -> dict[str, str]:
        return parse_resource_attributes(value)

    @classmethod
    def _env_settings_class(cls) -> type[BaseSettings]:
        return _OTelEnvSettings


class OTelLogsSettings(_BaseOTelSettings):
    """Configuration for OTLP log export.

    ``logs_mode`` controls where logs are sent:

    * ``"local_json"`` — current behaviour, no OTLP export (default)
    * ``"otlp"`` — export via OTLP only
    * ``"both"`` — local JSON **and** OTLP export (useful during migration)
    """

    logs_mode: Literal["local_json", "otlp", "both"] = "local_json"

    @classmethod
    def _env_settings_class(cls) -> type[BaseSettings]:
        return _OTelLogsEnvSettings


class OTelMetricsSettings(_BaseOTelSettings):
    """Configuration for OTLP metrics export."""

    export_interval_millis: int = Field(default=60_000, ge=1_000)

    @classmethod
    def _env_settings_class(cls) -> type[BaseSettings]:
        return _OTelMetricsEnvSettings


# ---------------------------------------------------------------------------
# Runtime trace sampling ratio — shared mutable state
# ---------------------------------------------------------------------------


def get_trace_sampling_ratio() -> float:
    with _RUNTIME_LOCK:
        return _RUNTIME_TRACE_SAMPLING_RATIO


def set_trace_sampling_ratio(ratio: float) -> float:
    value = max(0.0, min(1.0, float(ratio)))
    with _RUNTIME_LOCK:
        global _RUNTIME_TRACE_SAMPLING_RATIO
        _RUNTIME_TRACE_SAMPLING_RATIO = value
    return value


def mark_runtime_sampler_installed() -> None:
    global _RUNTIME_SAMPLER_INSTALLED
    _RUNTIME_SAMPLER_INSTALLED = True


def runtime_sampler_installed() -> bool:
    """Whether traces are sampled at the ratio set here.

    False with no tracing, or with a tracer provider the app set up itself,
    whose own sampler decides.
    """
    return _RUNTIME_SAMPLER_INSTALLED


# ---------------------------------------------------------------------------
# Env-based settings wrappers
# ---------------------------------------------------------------------------


class _BaseOTelEnvSettings(InternalSettingsBase):
    """Endpoints aren't read here: the OTLP exporters read the specification's
    ``OTEL_EXPORTER_OTLP_*ENDPOINT`` variables themselves."""

    enabled: bool = False
    # The specification's switch for the whole SDK: it overrides every enable flag.
    sdk_disabled: bool = Field(default=False, validation_alias="OTEL_SDK_DISABLED", exclude=True)

    @field_validator("protocol", mode="before", check_fields=False)
    @classmethod
    def _normalize_protocol_env_base(cls, value: object) -> str:
        return normalize_protocol(
            value,
            allowed=OTEL_PROTOCOLS,
            default="grpc",
            strict=False,
            label="OTel protocol",
        )

    @model_validator(mode="after")
    def _apply_sdk_disabled(self) -> Self:
        if self.sdk_disabled:
            self.enabled = False
        return self


def _protocol_field(signal: str) -> Any:
    """The signal's OTLP protocol: its own variable, then the shared one."""
    return Field(
        default="grpc",
        validation_alias=AliasChoices(
            f"OTEL_EXPORTER_OTLP_{signal}_PROTOCOL", "OTEL_EXPORTER_OTLP_PROTOCOL"
        ),
    )


class _OTelEnvSettings(_BaseOTelEnvSettings):

    enabled: bool = Field(default=False, validation_alias="OBS_OTEL_ENABLED")
    protocol: Literal["grpc", "http/protobuf"] = _protocol_field("TRACES")
    trace_sampling_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        validation_alias="OTEL_TRACES_SAMPLER_ARG",
    )
    extra_resource_attributes: str | None = Field(
        default=None,
        validation_alias="OTEL_RESOURCE_ATTRIBUTES",
    )


class _OTelLogsEnvSettings(_BaseOTelEnvSettings):

    enabled: bool = Field(default=False, validation_alias="OBS_OTEL_LOGS_ENABLED")
    logs_mode: Literal["local_json", "otlp", "both"] = Field(
        default="local_json", validation_alias="OBS_OTEL_LOGS_MODE"
    )
    protocol: Literal["grpc", "http/protobuf"] = _protocol_field("LOGS")

    @field_validator("logs_mode", mode="before")
    @classmethod
    def _normalize_logs_mode(cls, value: object) -> str:
        normalized = str(value).strip().lower()
        if normalized not in ("local_json", "otlp", "both"):
            return "local_json"
        return normalized


class _OTelMetricsEnvSettings(_BaseOTelEnvSettings):

    enabled: bool = Field(default=False, validation_alias="OBS_OTEL_METRICS_ENABLED")
    protocol: Literal["grpc", "http/protobuf"] = _protocol_field("METRICS")
    export_interval_millis: int = Field(
        default=60_000,
        ge=1_000,
        validation_alias="OTEL_METRIC_EXPORT_INTERVAL",
    )


# ---------------------------------------------------------------------------
# Resource attribute parsing helper
# ---------------------------------------------------------------------------


def parse_resource_attributes(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {
            str(key).strip(): str(val).strip()
            for key, val in value.items()
            if str(key).strip()
        }
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
            attributes[normalized_key] = unquote(raw_value.strip())  # percent-encoded per spec
        return attributes
    raise ValueError("extra_resource_attributes must be a mapping or key=value CSV string")
