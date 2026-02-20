from __future__ import annotations

from typing import Callable, Literal, Protocol
from fastapi import FastAPI

class MetricsBackend(Protocol):
    """Interface for metrics recording backends (Interface Segregation)."""

    def observe(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None: ...


class MountableMetricsBackend(Protocol):
    """Optional extension for backends that can mount their endpoint."""

    def mount_endpoint(
        self,
        app: FastAPI,
        *,
        path: str = "/metrics",
        metrics_format: "MetricsFormat" = "negotiate",
    ) -> None: ...


MetricsBackendFactory = Callable[..., MetricsBackend]

MetricsFormat = Literal["prometheus", "openmetrics", "negotiate"]

__all__ = [
    "MetricsBackend",
    "MountableMetricsBackend",
    "MetricsBackendFactory",
    "MetricsFormat",
]
