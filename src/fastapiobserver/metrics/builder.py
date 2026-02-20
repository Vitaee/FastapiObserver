from __future__ import annotations

from fastapi import FastAPI
from .contracts import MetricsBackend, MetricsFormat
from .noop import NoopMetricsBackend
from .registry import _METRICS_BACKEND_FACTORIES, _METRICS_BACKEND_LOCK


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
    normalized_backend = backend.strip().lower()
    with _METRICS_BACKEND_LOCK:
        backend_factory = _METRICS_BACKEND_FACTORIES.get(normalized_backend)
        available_backends = tuple(sorted(_METRICS_BACKEND_FACTORIES))
    if backend_factory is None:
        available_csv = ", ".join(available_backends) or "(none)"
        raise ValueError(
            f"Unknown metrics backend: {backend!r}. Available backends: {available_csv}"
        )
    return backend_factory(
        service=service,
        environment=environment,
        exemplars_enabled=exemplars_enabled,
    )


def mount_backend_metrics_endpoint(
    app: FastAPI,
    backend: MetricsBackend,
    *,
    path: str = "/metrics",
    metrics_format: "MetricsFormat" = "negotiate",
) -> bool:
    mount_endpoint = getattr(backend, "mount_endpoint", None)
    if not callable(mount_endpoint):
        return False
    mount_endpoint(app, path=path, metrics_format=metrics_format)
    return True

__all__ = ["build_metrics_backend", "mount_backend_metrics_endpoint"]
