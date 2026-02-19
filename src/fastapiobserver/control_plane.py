from __future__ import annotations

import logging
import os
import secrets
import weakref
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .otel import get_trace_sampling_ratio, set_trace_sampling_ratio
from .utils import EnvLoadable, normalize_path

__all__ = [
    "RuntimeControlSettings",
    "ControlPlanePayload",
    "mount_control_plane",
]

_MOUNTED_CONTROL_PATHS: weakref.WeakKeyDictionary[FastAPI, set[str]] = weakref.WeakKeyDictionary()

class RuntimeControlSettings(EnvLoadable, BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    path: str = "/_observability/control"
    auth_mode: Literal["token"] = "token"
    token_env_var: str = "OBSERVABILITY_CONTROL_TOKEN"

    @field_validator("path")
    @classmethod
    def _normalize_control_path(cls, value: str) -> str:
        return normalize_path(value, default="/_observability/control")

    @field_validator("token_env_var")
    @classmethod
    def _validate_token_env_var(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("token_env_var cannot be empty")
        return normalized

    @classmethod
    def _env_settings_class(cls) -> type[BaseSettings]:
        return _RuntimeControlEnvSettings


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

    normalized_path = settings.path
    mounted_paths = _MOUNTED_CONTROL_PATHS.setdefault(app, set())
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


class _RuntimeControlEnvSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    enabled: bool = Field(
        default=False,
        validation_alias="OBS_RUNTIME_CONTROL_ENABLED",
    )
    path: str = Field(
        default="/_observability/control",
        validation_alias="OBS_RUNTIME_CONTROL_PATH",
    )
    auth_mode: Literal["token"] = "token"
    token_env_var: str = Field(
        default="OBSERVABILITY_CONTROL_TOKEN",
        validation_alias="OBS_RUNTIME_CONTROL_TOKEN_ENV_VAR",
    )
