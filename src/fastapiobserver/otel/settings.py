"""OTel signal settings models with env-based loading."""

from __future__ import annotations

import threading
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..config import ObservabilitySettings
from ..utils import EnvLoadable, normalize_protocol

OTEL_PROTOCOLS = {"grpc", "http/protobuf"}
_RUNTIME_LOCK = threading.RLock()
_RUNTIME_TRACE_SAMPLING_RATIO = 1.0


class OTelSettings(EnvLoadable, BaseModel):
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
    def from_env(
        cls,
        settings: ObservabilitySettings | None = None,
    ) -> "OTelSettings":
        default_service_name = settings.service if settings else "api"
        default_service_version = settings.version if settings else "0.0.0"
        default_environment = settings.environment if settings else "development"
        env_settings = cls._env_values()

        return cls(
            enabled=bool(env_settings["enabled"]),
            service_name=env_settings["service_name"] or default_service_name,
            service_version=env_settings["service_version"] or default_service_version,
            environment=env_settings["environment"] or default_environment,
            otlp_endpoint=env_settings["otlp_endpoint"],
            protocol=env_settings["protocol"],
            trace_sampling_ratio=float(env_settings["trace_sampling_ratio"]),
            extra_resource_attributes=parse_resource_attributes(
                env_settings["extra_resource_attributes"]
            ),
        )

    @classmethod
    def _env_settings_class(cls) -> type[BaseSettings]:
        return _OTelEnvSettings


class OTelLogsSettings(EnvLoadable, BaseModel):
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
        return normalize_protocol(
            value,
            allowed=OTEL_PROTOCOLS,
            default="grpc",
            strict=False,
            label="OTel protocol",
        )

    @classmethod
    def _env_settings_class(cls) -> type[BaseSettings]:
        return _OTelLogsEnvSettings


class OTelMetricsSettings(EnvLoadable, BaseModel):
    """Configuration for OTLP metrics export."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    otlp_endpoint: str | None = None
    protocol: Literal["grpc", "http/protobuf"] = "grpc"
    export_interval_millis: int = Field(default=60_000, ge=1_000)

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize_protocol(cls, value: object) -> str:
        return normalize_protocol(
            value,
            allowed=OTEL_PROTOCOLS,
            default="grpc",
            strict=False,
            label="OTel protocol",
        )

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


# ---------------------------------------------------------------------------
# Env-based settings wrappers
# ---------------------------------------------------------------------------


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
        return normalize_protocol(
            value,
            allowed=OTEL_PROTOCOLS,
            default="grpc",
            strict=False,
            label="OTel protocol",
        )


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
        return normalize_protocol(
            value,
            allowed=OTEL_PROTOCOLS,
            default="grpc",
            strict=False,
            label="OTel protocol",
        )

    @field_validator("logs_mode", mode="before")
    @classmethod
    def _normalize_logs_mode(cls, value: object) -> str:
        normalized = str(value).strip().lower()
        if normalized not in ("local_json", "otlp", "both"):
            return "local_json"
        return normalized


class _OTelMetricsEnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    enabled: bool = Field(default=False, validation_alias="OTEL_METRICS_ENABLED")
    otlp_endpoint: str | None = Field(
        default=None, validation_alias="OTEL_METRICS_ENDPOINT"
    )
    protocol: Literal["grpc", "http/protobuf"] = Field(
        default="grpc", validation_alias="OTEL_METRICS_PROTOCOL"
    )
    export_interval_millis: int = Field(
        default=60_000,
        ge=1_000,
        validation_alias="OTEL_METRICS_EXPORT_INTERVAL_MILLIS",
    )

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize_protocol_env(cls, value: object) -> str:
        return normalize_protocol(
            value,
            allowed=OTEL_PROTOCOLS,
            default="grpc",
            strict=False,
            label="OTel protocol",
        )


# ---------------------------------------------------------------------------
# Resource attribute parsing helper
# ---------------------------------------------------------------------------


def parse_resource_attributes(value: object) -> dict[str, str]:
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
