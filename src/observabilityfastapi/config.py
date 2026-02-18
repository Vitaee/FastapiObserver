from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")


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

    def __post_init__(self) -> None:
        normalized_log_level = self.log_level.upper().strip()
        level = logging.getLevelName(normalized_log_level)
        if not isinstance(level, int):
            raise ValueError(f"Invalid log_level: {self.log_level}")
        object.__setattr__(self, "log_level", normalized_log_level)

        request_header = self.request_id_header.strip().lower()
        response_header = self.response_request_id_header.strip().lower()
        if not _HEADER_NAME_RE.fullmatch(request_header):
            raise ValueError(f"Invalid request_id_header: {self.request_id_header}")
        if not _HEADER_NAME_RE.fullmatch(response_header):
            raise ValueError(
                f"Invalid response_request_id_header: {self.response_request_id_header}"
            )
        object.__setattr__(self, "request_id_header", request_header)
        object.__setattr__(self, "response_request_id_header", response_header)

        object.__setattr__(self, "metrics_path", _normalize_path(self.metrics_path))
        normalized_exclude_paths = tuple(
            _normalize_path(path) for path in self.metrics_exclude_paths
        )
        object.__setattr__(self, "metrics_exclude_paths", normalized_exclude_paths)

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


def _normalize_path(path: str) -> str:
    candidate = path.strip() or "/"
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    if len(candidate) > 1:
        candidate = candidate.rstrip("/")
    return candidate
