from __future__ import annotations

import threading

from .contracts import MetricsBackendFactory

_METRICS_BACKEND_FACTORIES: dict[str, MetricsBackendFactory] = {}
_METRICS_BACKEND_LOCK = threading.RLock()


def register_metrics_backend(name: str, factory: MetricsBackendFactory) -> None:
    normalized_name = name.strip().lower()
    if not normalized_name:
        raise ValueError("Metrics backend name cannot be empty")
    with _METRICS_BACKEND_LOCK:
        _METRICS_BACKEND_FACTORIES[normalized_name] = factory


def unregister_metrics_backend(name: str) -> None:
    normalized_name = name.strip().lower()
    if not normalized_name:
        return
    with _METRICS_BACKEND_LOCK:
        _METRICS_BACKEND_FACTORIES.pop(normalized_name, None)


def get_registered_metrics_backends() -> dict[str, MetricsBackendFactory]:
    with _METRICS_BACKEND_LOCK:
        return dict(_METRICS_BACKEND_FACTORIES)

__all__ = [
    "register_metrics_backend",
    "unregister_metrics_backend",
    "get_registered_metrics_backends",
    "_METRICS_BACKEND_FACTORIES",
    "_METRICS_BACKEND_LOCK",
]
