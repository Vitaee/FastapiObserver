from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .utils import normalize_path, parse_csv

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


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # --- identity ---
    app_name: str = Field(default="app", validation_alias="APP_NAME")
    service: str = Field(default="api", validation_alias="SERVICE_NAME")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    version: str = Field(default="0.0.0", validation_alias="APP_VERSION")

    # --- logging ---
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_dir: str | None = Field(default=None, validation_alias="LOG_DIR")
    log_queue_max_size: int = Field(default=10_000, gt=0, validation_alias="LOG_QUEUE_MAX_SIZE")
    log_queue_overflow_policy: LogQueueOverflowPolicy = Field(
        default="drop_oldest",
        validation_alias="LOG_QUEUE_OVERFLOW_POLICY",
    )
    log_queue_block_timeout_seconds: float = Field(
        default=1.0,
        ge=0.0,
        validation_alias="LOG_QUEUE_BLOCK_TIMEOUT_SECONDS",
    )
    sink_circuit_breaker_enabled: bool = Field(
        default=True,
        validation_alias="LOG_SINK_CIRCUIT_BREAKER_ENABLED",
    )
    sink_circuit_breaker_failure_threshold: int = Field(
        default=5,
        gt=0,
        validation_alias="LOG_SINK_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
    )
    sink_circuit_breaker_recovery_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        validation_alias="LOG_SINK_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS",
    )

    # --- request ---
    request_id_header: str = Field(
        default="x-request-id", validation_alias="REQUEST_ID_HEADER"
    )
    response_request_id_header: str = Field(
        default="x-request-id", validation_alias="RESPONSE_REQUEST_ID_HEADER"
    )

    # --- metrics ---
    metrics_enabled: bool = Field(default=False, validation_alias="METRICS_ENABLED")
    metrics_backend: str = Field(default="prometheus", validation_alias="METRICS_BACKEND")
    metrics_path: str = Field(default="/metrics", validation_alias="METRICS_PATH")
    metrics_exclude_paths: tuple[str, ...] = Field(
        default=DEFAULT_METRICS_EXCLUDE_PATHS,
        validation_alias="METRICS_EXCLUDE_PATHS",
    )
    exemplars_enabled: bool = Field(
        default=False, validation_alias="METRICS_EXEMPLARS_ENABLED"
    )
    metrics_format: MetricsFormat = Field(
        default="negotiate", validation_alias="METRICS_FORMAT"
    )

    # --- otel trace noise control ---
    otel_excluded_urls: tuple[str, ...] | None = Field(
        default=None, validation_alias="OTEL_EXCLUDED_URLS"
    )

    # --- logtail (Better Stack) ---
    logtail_enabled: bool = Field(default=False, validation_alias="LOGTAIL_ENABLED")
    logtail_source_token: str | None = Field(
        default=None, validation_alias="LOGTAIL_SOURCE_TOKEN"
    )
    logtail_batch_size: int = Field(
        default=50, gt=0, validation_alias="LOGTAIL_BATCH_SIZE"
    )
    logtail_flush_interval: float = Field(
        default=2.0, gt=0.0, validation_alias="LOGTAIL_FLUSH_INTERVAL"
    )

    # --- logtail DLQ ---
    logtail_dlq_enabled: bool = Field(
        default=False, validation_alias="LOGTAIL_DLQ_ENABLED"
    )
    logtail_dlq_dir: str = Field(
        default=".dlq/logtail", validation_alias="LOGTAIL_DLQ_DIR"
    )
    logtail_dlq_filename: str = Field(
        default="logtail_dlq.ndjson",
        validation_alias="LOGTAIL_DLQ_FILENAME",
    )
    logtail_dlq_max_bytes: int = Field(
        default=50 * 1024 * 1024,
        validation_alias="LOGTAIL_DLQ_MAX_BYTES",
    )
    logtail_dlq_backup_count: int = Field(
        default=10, validation_alias="LOGTAIL_DLQ_BACKUP_COUNT"
    )
    logtail_dlq_compress: bool = Field(
        default=True, validation_alias="LOGTAIL_DLQ_COMPRESS"
    )

    # --- tamper-evident audit logging ---
    audit_logging_enabled: bool = Field(
        default=False, validation_alias="OBS_AUDIT_LOGGING_ENABLED"
    )
    audit_key_env_var: str = Field(
        default="OBS_AUDIT_SECRET_KEY", validation_alias="OBS_AUDIT_KEY_ENV_VAR"
    )

    # ---------- validators ----------

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
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
        parsed = parse_csv(
            value,
            default=DEFAULT_METRICS_EXCLUDE_PATHS,
            optional=False,
        )
        if isinstance(parsed, tuple):
            return parsed
        return DEFAULT_METRICS_EXCLUDE_PATHS

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


__all__ = [
    "ObservabilitySettings",
    "DEFAULT_METRICS_EXCLUDE_PATHS",
    "MetricsFormat",
    "LogQueueOverflowPolicy",
]
