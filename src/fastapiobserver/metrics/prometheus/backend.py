from __future__ import annotations

import threading
import logging
from typing import Any

from fastapi import FastAPI
from ..contracts import MetricsBackend, MetricsFormat
from .client import _import_prometheus_client
from .exemplars import _get_trace_exemplar
from .multiprocess import _is_prometheus_multiprocess_enabled, _validate_prometheus_multiprocess_dir
from .collector import _register_log_queue_metrics_collector

_LOGGER = logging.getLogger("fastapiobserver.metrics")

class PrometheusMetricsBackend:
    """Prometheus backend with optional exemplar support.

    When *exemplars_enabled* is ``True``, each counter increment and
    histogram observation attaches the current OTel trace ID as an
    exemplar label.  This enables the **metrics → traces** jump in
    Grafana.

    .. warning::

       Prometheus exemplars are **not compatible** with the multiprocess
       collector (``PROMETHEUS_MULTIPROC_DIR``).  If multiprocess mode
       is detected, exemplars are silently disabled with a warning log.
    """

    _LOCK = threading.Lock()
    _REQUEST_COUNT: Any = None
    _REQUEST_LATENCY: Any = None

    def __init__(
        self,
        *,
        service: str,
        environment: str,
        exemplars_enabled: bool = False,
    ) -> None:
        self.service = service
        self.environment = environment
        self._exemplars_enabled = exemplars_enabled

        if self._exemplars_enabled and _is_prometheus_multiprocess_enabled():
            _LOGGER.warning(
                "metrics.exemplars.disabled_in_multiprocess",
                extra={
                    "event": {
                        "message": (
                            "Prometheus exemplars are not compatible with "
                            "multiprocess mode. Exemplars have been disabled."
                        ),
                    },
                    "_skip_enrichers": True,
                },
            )
            self._exemplars_enabled = False

        prometheus_client = _import_prometheus_client()
        with self.__class__._LOCK:
            if self.__class__._REQUEST_COUNT is None:
                self.__class__._REQUEST_COUNT = prometheus_client.Counter(
                    "http_requests_total",
                    "Total count of HTTP requests",
                    ("service", "environment", "method", "path", "status_code"),
                )
                self.__class__._REQUEST_LATENCY = prometheus_client.Histogram(
                    "http_request_duration_seconds",
                    "HTTP request latency",
                    ("service", "environment", "method", "path", "status_code"),
                    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.3, 0.5, 1, 3, 5, 10),
                )

    def observe(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        labels = {
            "service": self.service,
            "environment": self.environment,
            "method": method,
            "path": path,
            "status_code": str(status_code),
        }
        if self._exemplars_enabled:
            exemplar = _get_trace_exemplar()
            self.__class__._REQUEST_COUNT.labels(**labels).inc(exemplar=exemplar)
            self.__class__._REQUEST_LATENCY.labels(**labels).observe(
                duration_seconds, exemplar=exemplar
            )
        else:
            self.__class__._REQUEST_COUNT.labels(**labels).inc()
            self.__class__._REQUEST_LATENCY.labels(**labels).observe(duration_seconds)

    def mount_endpoint(
        self,
        app: FastAPI,
        *,
        path: str = "/metrics",
        metrics_format: "MetricsFormat" = "negotiate",
    ) -> None:
        from ...metrics import mount_metrics_endpoint
        mount_metrics_endpoint(app, path=path, metrics_format=metrics_format)


def _build_prometheus_metrics_backend(
    *,
    service: str,
    environment: str,
    exemplars_enabled: bool,
) -> MetricsBackend:
    _validate_prometheus_multiprocess_dir()
    backend = PrometheusMetricsBackend(
        service=service,
        environment=environment,
        exemplars_enabled=exemplars_enabled,
    )
    _register_log_queue_metrics_collector()
    return backend

__all__ = ["PrometheusMetricsBackend", "_build_prometheus_metrics_backend"]
