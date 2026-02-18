from __future__ import annotations

import importlib
import os
import re
import threading
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI

from .utils import normalize_path as normalize_route_path

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

    def __init__(self, *, service: str, environment: str) -> None:
        self.service = service
        self.environment = environment
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
        self.__class__._REQUEST_COUNT.labels(**labels).inc()
        self.__class__._REQUEST_LATENCY.labels(**labels).observe(duration_seconds)


def _import_prometheus_client() -> Any:
    try:
        return importlib.import_module("prometheus_client")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Prometheus support requires `pip install observabilityfastapi[prometheus]`"
        ) from exc


def build_metrics_backend(
    enabled: bool,
    *,
    service: str = "api",
    environment: str = "development",
) -> MetricsBackend:
    if not enabled:
        return NoopMetricsBackend()
    _validate_prometheus_multiprocess_dir()
    return PrometheusMetricsBackend(service=service, environment=environment)


def mount_metrics_endpoint(app: FastAPI, path: str = "/metrics") -> None:
    path = normalize_route_path(path, default="/metrics")
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return
    prometheus_client = _import_prometheus_client()

    if _is_prometheus_multiprocess_enabled():
        registry = prometheus_client.CollectorRegistry(auto_describe=True)
        prometheus_client.multiprocess.MultiProcessCollector(registry)
        app.mount(path, prometheus_client.make_asgi_app(registry=registry))
        return

    app.mount(path, prometheus_client.make_asgi_app())


def normalize_path(path: str) -> str:
    normalized = _UUID_RE.sub("/:id", path)
    normalized = _HEX_RE.sub("/:id", normalized)
    normalized = _NUMBER_RE.sub("/:id", normalized)
    return normalized


def mark_prometheus_process_dead(pid: int) -> None:
    if not _is_prometheus_multiprocess_enabled():
        return
    prometheus_client = _import_prometheus_client()
    prometheus_client.multiprocess.mark_process_dead(pid)


def _is_prometheus_multiprocess_enabled() -> bool:
    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR", "").strip()
    return bool(multiproc_dir)


def _validate_prometheus_multiprocess_dir() -> None:
    if not _is_prometheus_multiprocess_enabled():
        return
    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR", "").strip()
    path = Path(multiproc_dir)
    if not path.exists():
        raise RuntimeError(
            "PROMETHEUS_MULTIPROC_DIR is set but does not exist. "
            "Create a writable directory before starting workers."
        )
    if not path.is_dir():
        raise RuntimeError("PROMETHEUS_MULTIPROC_DIR must point to a directory.")
    if not os.access(path, os.W_OK):
        raise RuntimeError("PROMETHEUS_MULTIPROC_DIR must be writable.")

