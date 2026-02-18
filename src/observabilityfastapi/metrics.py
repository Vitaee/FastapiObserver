from __future__ import annotations

import importlib
import re
import threading
from typing import Any, Protocol

from fastapi import FastAPI

_UUID_RE = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)
_NUMBER_RE = re.compile(r"/\d+")
_HEX_RE = re.compile(r"/[0-9a-fA-F]{16,}")


class MetricsBackend(Protocol):
    def observe(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        ...


class NoopMetricsBackend:
    def observe(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        return None


class PrometheusMetricsBackend:
    _LOCK = threading.Lock()
    _REQUEST_COUNT: Any = None
    _REQUEST_LATENCY: Any = None

    def __init__(self) -> None:
        prometheus_client = _import_prometheus_client()
        with self.__class__._LOCK:
            if self.__class__._REQUEST_COUNT is None:
                self.__class__._REQUEST_COUNT = prometheus_client.Counter(
                    "http_requests_total",
                    "Total count of HTTP requests",
                    ("method", "path", "status_code"),
                )
                self.__class__._REQUEST_LATENCY = prometheus_client.Histogram(
                    "http_request_duration_seconds",
                    "HTTP request latency",
                    ("method", "path", "status_code"),
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
            "method": method,
            "path": path,
            "status_code": str(status_code),
        }
        self.__class__._REQUEST_COUNT.labels(**labels).inc()
        self.__class__._REQUEST_LATENCY.labels(**labels).observe(duration_seconds)


def _import_prometheus_client() -> Any:
    try:
        return importlib.import_module("prometheus_client")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Prometheus support requires `pip install observabilityfastapi[prometheus]`"
        ) from exc


def build_metrics_backend(enabled: bool) -> MetricsBackend:
    if not enabled:
        return NoopMetricsBackend()
    return PrometheusMetricsBackend()


def mount_metrics_endpoint(app: FastAPI, path: str = "/metrics") -> None:
    if not path.startswith("/"):
        path = f"/{path}"
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return
    prometheus_client = _import_prometheus_client()
    app.mount(path, prometheus_client.make_asgi_app())


def normalize_path(path: str) -> str:
    normalized = _UUID_RE.sub("/:id", path)
    normalized = _HEX_RE.sub("/:id", normalized)
    normalized = _NUMBER_RE.sub("/:id", normalized)
    return normalized
