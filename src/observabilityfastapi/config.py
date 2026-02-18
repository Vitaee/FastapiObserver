from __future__ import annotations

import logging
import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .utils import normalize_path, parse_csv_tuple

_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")
DEFAULT_METRICS_EXCLUDE_PATHS = (
    "/metrics",
    "/health",
    "/healthz",
    "/docs",
    "/openapi.json",
)


class ObservabilitySettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_name: str = Field(default="app", validation_alias="APP_NAME")
    service: str = Field(default="api", validation_alias="SERVICE_NAME")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    version: str = Field(default="0.0.0", validation_alias="APP_VERSION")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_dir: str | None = Field(default=None, validation_alias="LOG_DIR")
    request_id_header: str = Field(
        default="x-request-id", validation_alias="REQUEST_ID_HEADER"
    )
    response_request_id_header: str = Field(
        default="x-request-id", validation_alias="RESPONSE_REQUEST_ID_HEADER"
    )
    metrics_enabled: bool = Field(default=False, validation_alias="METRICS_ENABLED")
    metrics_path: str = Field(default="/metrics", validation_alias="METRICS_PATH")
    metrics_exclude_paths: tuple[str, ...] = Field(
        default=DEFAULT_METRICS_EXCLUDE_PATHS,
        validation_alias="METRICS_EXCLUDE_PATHS",
    )

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized_log_level = value.upper().strip()
        level = logging.getLevelName(normalized_log_level)
        if not isinstance(level, int):
            raise ValueError(f"Invalid log_level: {value}")
        return normalized_log_level

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

    @field_validator("metrics_exclude_paths", mode="before")
    @classmethod
    def _parse_metrics_exclude_paths(cls, value: object) -> tuple[str, ...]:
        return parse_csv_tuple(value, DEFAULT_METRICS_EXCLUDE_PATHS)

    @field_validator("metrics_exclude_paths")
    @classmethod
    def _normalize_metrics_exclude_paths(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return tuple(normalize_path(path, default="/") for path in value)

    @classmethod
    def from_env(cls) -> "ObservabilitySettings":
        return cls()


__all__ = ["ObservabilitySettings", "DEFAULT_METRICS_EXCLUDE_PATHS"]
