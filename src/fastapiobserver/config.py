from __future__ import annotations

import logging
import os
import re
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import NoDecode, SettingsConfigDict

from .utils import InternalSettingsBase, normalize_path, parse_csv

_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")
DEFAULT_METRICS_EXCLUDE_PATHS = (
    "/metrics",
    "/health",
    "/healthz",
    "/docs",
    "/openapi.json",
)

MetricsFormat = Literal["prometheus", "openmetrics", "negotiate"]
LogQueueOverflowPolicy = Literal["drop_oldest", "drop_newest", "block"]


class ObservabilitySettings(InternalSettingsBase):
    # Built from field names in code too. The prefix keeps the environment from
    # matching them bare: `log_level` is read from OBS_LOG_LEVEL, never LOG_LEVEL.
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, populate_by_name=True, env_prefix="OBS_"
    )

    # --- identity ---
    app_name: str = Field(default="app", validation_alias="OBS_APP_NAME")
    service: str = Field(
        default="api", validation_alias=AliasChoices("OBS_SERVICE_NAME", "OTEL_SERVICE_NAME")
    )
    environment: str = Field(default="development", validation_alias="OBS_ENVIRONMENT")
    version: str = Field(default="0.0.0", validation_alias="OBS_APP_VERSION")

    # --- logging ---
    log_level: str = Field(default="INFO", validation_alias="OBS_LOG_LEVEL")
    # Records from this level up to log_level are held per request and written
    # only if the request fails.
    log_level_on_error: str | None = Field(
        default=None, validation_alias="OBS_LOG_LEVEL_ON_ERROR"
    )
    # Share of requests whose INFO and lower lines are written, chosen by trace ID.
    log_sample_rate: float = Field(
        default=1.0, ge=0.0, le=1.0, validation_alias="OBS_LOG_SAMPLE_RATE"
    )
    # Requests at least this slow are written in full, sampled or not.
    log_slow_request_ms: float = Field(
        default=1000.0, gt=0, validation_alias="OBS_LOG_SLOW_REQUEST_MS"
    )
    log_dir: str | None = Field(default=None, validation_alias="OBS_LOG_DIR")
    log_queue_max_size: int = Field(
        default=10_000, gt=0, validation_alias="OBS_LOG_QUEUE_MAX_SIZE"
    )
    log_queue_overflow_policy: LogQueueOverflowPolicy = Field(
        default="drop_oldest",
        validation_alias="OBS_LOG_QUEUE_OVERFLOW_POLICY",
    )
    log_queue_block_timeout_seconds: float = Field(
        default=1.0,
        ge=0.0,
        validation_alias="OBS_LOG_QUEUE_BLOCK_TIMEOUT_SECONDS",
    )
    sink_circuit_breaker_enabled: bool = Field(
        default=True,
        validation_alias="OBS_LOG_SINK_CIRCUIT_BREAKER_ENABLED",
    )
    sink_circuit_breaker_failure_threshold: int = Field(
        default=5,
        gt=0,
        validation_alias="OBS_LOG_SINK_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
    )
    sink_circuit_breaker_recovery_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        validation_alias="OBS_LOG_SINK_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS",
    )

    # --- request ---
    request_id_header: str = Field(
        default="x-request-id", validation_alias="OBS_REQUEST_ID_HEADER"
    )
    response_request_id_header: str = Field(
        default="x-request-id", validation_alias="OBS_RESPONSE_REQUEST_ID_HEADER"
    )

    # --- metrics ---
    metrics_enabled: bool = Field(default=False, validation_alias="OBS_METRICS_ENABLED")
    metrics_backend: str = Field(default="prometheus", validation_alias="OBS_METRICS_BACKEND")
    metrics_path: str = Field(default="/metrics", validation_alias="OBS_METRICS_PATH")
    metrics_exclude_paths: Annotated[tuple[str, ...], NoDecode] = Field(
        default=DEFAULT_METRICS_EXCLUDE_PATHS,
        validation_alias="OBS_METRICS_EXCLUDE_PATHS",
    )
    exemplars_enabled: bool = Field(
        default=False, validation_alias="OBS_METRICS_EXEMPLARS_ENABLED"
    )
    metrics_format: MetricsFormat = Field(
        default="negotiate", validation_alias="OBS_METRICS_FORMAT"
    )

    # --- event loop ---
    # Log the blocking call's stack when the loop doesn't run for this long.
    loop_block_threshold_ms: float | None = Field(
        default=None, gt=0, validation_alias="OBS_LOOP_BLOCK_THRESHOLD_MS"
    )

    # --- otel trace noise control ---
    otel_excluded_urls: tuple[str, ...] | None = Field(
        default=None, validation_alias="OBS_OTEL_EXCLUDED_URLS"
    )

    # ---------- validators ----------

    @field_validator("log_level", "log_level_on_error")
    @classmethod
    def _validate_log_level(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized_log_level = value.upper().strip()
        level = logging.getLevelName(normalized_log_level)
        if not isinstance(level, int):
            raise ValueError(f"Invalid log_level: {value}")
        return normalized_log_level

    @field_validator("log_queue_overflow_policy", mode="before")
    @classmethod
    def _normalize_log_queue_policy(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("request_id_header", "response_request_id_header")
    @classmethod
    def _normalize_header_name(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _HEADER_NAME_RE.fullmatch(normalized):
            raise ValueError(f"Invalid header value: {value}")
        return normalized

    @field_validator("metrics_path")
    @classmethod
    def _normalize_metrics_path(cls, value: str) -> str:
        return normalize_path(value, default="/metrics")

    @field_validator("metrics_backend")
    @classmethod
    def _normalize_metrics_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("metrics_backend cannot be empty")
        return normalized

    @field_validator("metrics_exclude_paths", mode="before")
    @classmethod
    def _parse_metrics_exclude_paths(cls, value: object) -> tuple[str, ...]:
        # Empty means exclude nothing; only an unusable value falls back to the defaults.
        parsed = parse_csv(value, optional=True, nullish_values=frozenset())
        return DEFAULT_METRICS_EXCLUDE_PATHS if parsed is None else parsed

    @field_validator("metrics_exclude_paths")
    @classmethod
    def _normalize_metrics_exclude_paths(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return tuple(normalize_path(path, default="/") for path in value)

    @field_validator("otel_excluded_urls", mode="before")
    @classmethod
    def _parse_otel_excluded_urls(cls, value: object) -> tuple[str, ...] | None:
        # Preserve previous semantics: empty string means explicit empty tuple,
        # while None means "use package defaults".
        return parse_csv(
            value,
            optional=True,
            nullish_values=frozenset(),
        )

    @classmethod
    def from_env(cls) -> "ObservabilitySettings":
        return cls()


# Variables 1.x read and 2.0 doesn't, each with what replaces it.
_RETIRED_ENV_VARS: dict[str, str] = {
    **{
        name: f"OBS_{name}"
        for name in (
            "APP_NAME",
            "SERVICE_NAME",
            "ENVIRONMENT",
            "APP_VERSION",
            "LOG_LEVEL",
            "LOG_LEVEL_ON_ERROR",
            "LOG_SAMPLE_RATE",
            "LOG_SLOW_REQUEST_MS",
            "LOG_DIR",
            "LOG_QUEUE_MAX_SIZE",
            "LOG_QUEUE_OVERFLOW_POLICY",
            "LOG_QUEUE_BLOCK_TIMEOUT_SECONDS",
            "LOG_SINK_CIRCUIT_BREAKER_ENABLED",
            "LOG_SINK_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
            "LOG_SINK_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS",
            "REQUEST_ID_HEADER",
            "RESPONSE_REQUEST_ID_HEADER",
            "METRICS_ENABLED",
            "METRICS_BACKEND",
            "METRICS_PATH",
            "METRICS_EXCLUDE_PATHS",
            "METRICS_EXEMPLARS_ENABLED",
            "METRICS_FORMAT",
            "OTEL_EXCLUDED_URLS",
            "OTEL_ENABLED",
            "OTEL_LOGS_ENABLED",
            "OTEL_LOGS_MODE",
            "OTEL_METRICS_ENABLED",
        )
    },
    "OTEL_SERVICE_VERSION": "OBS_APP_VERSION",
    "OTEL_ENVIRONMENT": "OBS_ENVIRONMENT",
    "OTEL_TRACE_SAMPLING_RATIO": "OTEL_TRACES_SAMPLER_ARG",
    "OTEL_EXTRA_RESOURCE_ATTRIBUTES": "OTEL_RESOURCE_ATTRIBUTES",
    "OTEL_LOGS_ENDPOINT": "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
    "OTEL_LOGS_PROTOCOL": "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    "OTEL_METRICS_ENDPOINT": "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_METRICS_PROTOCOL": "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    "OTEL_METRICS_EXPORT_INTERVAL_MILLIS": "OTEL_METRIC_EXPORT_INTERVAL",
    "OBS_LOG_SINKS": "register_sink() in code",
    "OBS_AUDIT_LOGGING_ENABLED": "nothing: the audit log was removed",
    "LOGTAIL_ENABLED": "register_sink() with a handler of your own",
}


def retired_env_vars() -> dict[str, str]:
    """The retired variables still set, each with its replacement, unless that's set too.

    An app may set a name like ``LOG_LEVEL`` for itself, so setting the
    replacement is also how to stop the warning.
    """
    return {
        name: replacement
        for name, replacement in _RETIRED_ENV_VARS.items()
        if name in os.environ and replacement not in os.environ
    }


__all__ = [
    "ObservabilitySettings",
    "DEFAULT_METRICS_EXCLUDE_PATHS",
    "MetricsFormat",
    "LogQueueOverflowPolicy",
    "retired_env_vars",
]
