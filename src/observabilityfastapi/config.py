from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or default


@dataclass(frozen=True)
class ObservabilitySettings:
    app_name: str = "app"
    service: str = "api"
    environment: str = "development"
    version: str = "0.0.0"
    log_level: str = "INFO"
    log_dir: str | None = None
    request_id_header: str = "x-request-id"
    response_request_id_header: str = "x-request-id"
    metrics_enabled: bool = False
    metrics_path: str = "/metrics"
    metrics_exclude_paths: tuple[str, ...] = (
        "/metrics",
        "/health",
        "/healthz",
        "/docs",
        "/openapi.json",
    )

    @classmethod
    def from_env(cls) -> "ObservabilitySettings":
        return cls(
            app_name=os.getenv("APP_NAME", "app"),
            service=os.getenv("SERVICE_NAME", "api"),
            environment=os.getenv("ENVIRONMENT", "development"),
            version=os.getenv("APP_VERSION", "0.0.0"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_dir=os.getenv("LOG_DIR"),
            request_id_header=os.getenv("REQUEST_ID_HEADER", "x-request-id"),
            response_request_id_header=os.getenv(
                "RESPONSE_REQUEST_ID_HEADER", "x-request-id"
            ),
            metrics_enabled=_env_bool("METRICS_ENABLED", False),
            metrics_path=os.getenv("METRICS_PATH", "/metrics"),
            metrics_exclude_paths=_env_tuple(
                "METRICS_EXCLUDE_PATHS",
                ("/metrics", "/health", "/healthz", "/docs", "/openapi.json"),
            ),
        )
