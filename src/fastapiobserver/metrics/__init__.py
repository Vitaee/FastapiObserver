from __future__ import annotations

from .builder import build_metrics_backend, mount_backend_metrics_endpoint
from .contracts import (
    MetricsBackend,
    MetricsBackendFactory,
    MetricsFormat,
    MountableMetricsBackend,
)
from .endpoint import mount_metrics_endpoint
from .noop import NoopMetricsBackend
from .pathing import collapse_dynamic_segments
from .prometheus import (
    PrometheusMetricsBackend,
    _build_prometheus_metrics_backend,
    mark_prometheus_process_dead,
)
from .registry import (
    get_registered_metrics_backends,
    register_metrics_backend,
    unregister_metrics_backend,
)

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

register_metrics_backend("prometheus", _build_prometheus_metrics_backend)
