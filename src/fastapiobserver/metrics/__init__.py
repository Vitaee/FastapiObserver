from __future__ import annotations

from typing import Callable, Protocol

from fastapi import FastAPI

from ..config import MetricsFormat
from ..utils import collapse_dynamic_segments
from .prometheus import (
    PrometheusMetricsBackend,
    _build_prometheus_metrics_backend,
    mark_prometheus_process_dead,
    mount_metrics_endpoint,
)


class MetricsBackend(Protocol):
    """Interface for metrics recording backends."""

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
        metrics_format: MetricsFormat = "negotiate",
    ) -> None: ...


MetricsBackendFactory = Callable[..., MetricsBackend]


class NoopMetricsBackend:
    """No-op backend that silently discards all observations."""

    def observe(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        return None


_BACKEND_FACTORIES: dict[str, MetricsBackendFactory] = {}


def register_metrics_backend(name: str, factory: MetricsBackendFactory) -> None:
    normalized_name = name.strip().lower()
    if not normalized_name:
        raise ValueError("Metrics backend name cannot be empty")
    _BACKEND_FACTORIES[normalized_name] = factory


def unregister_metrics_backend(name: str) -> None:
    _BACKEND_FACTORIES.pop(name.strip().lower(), None)


def get_registered_metrics_backends() -> dict[str, MetricsBackendFactory]:
    return dict(_BACKEND_FACTORIES)


def build_metrics_backend(
    enabled: bool,
    *,
    service: str = "api",
    environment: str = "development",
    exemplars_enabled: bool = False,
    backend: str = "prometheus",
) -> MetricsBackend:
    """Factory for selecting the appropriate metrics backend."""
    if not enabled:
        return NoopMetricsBackend()
    factory = _BACKEND_FACTORIES.get(backend.strip().lower())
    if factory is None:
        available_csv = ", ".join(sorted(_BACKEND_FACTORIES)) or "(none)"
        raise ValueError(
            f"Unknown metrics backend: {backend!r}. Available backends: {available_csv}"
        )
    return factory(
        service=service,
        environment=environment,
        exemplars_enabled=exemplars_enabled,
    )


def mount_backend_metrics_endpoint(
    app: FastAPI,
    backend: MetricsBackend,
    *,
    path: str = "/metrics",
    metrics_format: MetricsFormat = "negotiate",
) -> bool:
    mount_endpoint = getattr(backend, "mount_endpoint", None)
    if not callable(mount_endpoint):
        return False
    mount_endpoint(app, path=path, metrics_format=metrics_format)
    return True


register_metrics_backend("prometheus", _build_prometheus_metrics_backend)

__all__ = [
    "MetricsBackend",
    "MountableMetricsBackend",
    "MetricsBackendFactory",
    "MetricsFormat",
    "NoopMetricsBackend",
    "collapse_dynamic_segments",
    "register_metrics_backend",
    "unregister_metrics_backend",
    "get_registered_metrics_backends",
    "build_metrics_backend",
    "mount_backend_metrics_endpoint",
    "PrometheusMetricsBackend",
    "mark_prometheus_process_dead",
    "mount_metrics_endpoint",
]
