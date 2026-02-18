from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from .otel import get_trace_sampling_ratio, set_trace_sampling_ratio


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeControlSettings:
    enabled: bool = False
    path: str = "/_observability/control"
    auth_mode: Literal["token"] = "token"
    token_env_var: str = "OBSERVABILITY_CONTROL_TOKEN"

    def __post_init__(self) -> None:
        normalized_path = _normalize_path(self.path)
        if not self.token_env_var.strip():
            raise ValueError("token_env_var cannot be empty")
        object.__setattr__(self, "path", normalized_path)

    @classmethod
    def from_env(cls) -> "RuntimeControlSettings":
        return cls(
            enabled=_env_bool("OBS_RUNTIME_CONTROL_ENABLED", False),
            path=os.getenv("OBS_RUNTIME_CONTROL_PATH", "/_observability/control"),
            auth_mode="token",
            token_env_var=os.getenv(
                "OBS_RUNTIME_CONTROL_TOKEN_ENV_VAR", "OBSERVABILITY_CONTROL_TOKEN"
            ),
        )


class ControlPlanePayload(BaseModel):
    log_level: str | None = None
    trace_sampling_ratio: float | None = Field(default=None, ge=0.0, le=1.0)


def mount_control_plane(app: FastAPI, settings: RuntimeControlSettings) -> None:
    if not settings.enabled:
        return
    if settings.auth_mode != "token":
        raise RuntimeError("Only token auth mode is supported")

    token = os.getenv(settings.token_env_var)
    if not token:
        raise RuntimeError(
            f"Runtime control plane requires `{settings.token_env_var}` to be set"
        )

    normalized_path = _normalize_path(settings.path)
    mounted_paths: set[str] = getattr(
        app.state, "_observabilityfastapi_control_paths", set()
    )
    if normalized_path in mounted_paths:
        return

    def authorize(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = f"Bearer {token}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized",
            )

    router = APIRouter(
        prefix=normalized_path,
        tags=["observability-control"],
        dependencies=[Depends(authorize)],
    )

    @router.get("")
    async def get_current_settings() -> dict[str, object]:
        return _current_runtime_settings()

    @router.post("")
    async def update_settings(payload: ControlPlanePayload) -> dict[str, object]:
        if payload.log_level:
            _set_log_level(payload.log_level)
        if payload.trace_sampling_ratio is not None:
            set_trace_sampling_ratio(payload.trace_sampling_ratio)
        return _current_runtime_settings()

    app.include_router(router)
    mounted_paths.add(normalized_path)
    app.state._observabilityfastapi_control_paths = mounted_paths


def _current_runtime_settings() -> dict[str, object]:
    return {
        "log_level": logging.getLevelName(logging.getLogger().getEffectiveLevel()),
        "trace_sampling_ratio": get_trace_sampling_ratio(),
    }


def _set_log_level(value: str) -> None:
    normalized = value.upper().strip()
    level = logging.getLevelName(normalized)
    if not isinstance(level, int):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid log level: {value}",
        )

    logging.getLogger().setLevel(level)
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(logger_name).setLevel(level)


def _normalize_path(path: str) -> str:
    candidate = path.strip() or "/_observability/control"
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    if len(candidate) > 1:
        candidate = candidate.rstrip("/")
    return candidate
