from __future__ import annotations

import importlib
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Literal, Protocol

from fastapi import FastAPI

from .utils import normalize_path as normalize_route_path

_UUID_RE = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)
_NUMBER_RE = re.compile(r"/\d+")
_HEX_RE = re.compile(r"/[0-9a-fA-F]{16,}")

_LOGGER = logging.getLogger("fastapiobserver.metrics")
_LOG_QUEUE_COLLECTOR_LOCK = threading.Lock()
_LOG_QUEUE_COLLECTOR_REGISTERED = False


# ---------------------------------------------------------------------------
# Protocol — Single Responsibility: separates "what" from "how"
# ---------------------------------------------------------------------------


class MetricsBackend(Protocol):
    """Interface for metrics recording backends (Interface Segregation)."""

    def observe(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None: ...


# ---------------------------------------------------------------------------
# Noop implementation — always safe, zero overhead
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Exemplar extraction — DRY: shared by Counter and Histogram
# ---------------------------------------------------------------------------


def _get_trace_exemplar() -> dict[str, str] | None:
    """Extract TraceID from the current OTel span for Prometheus exemplar.

    Returns ``None`` when OTel is not installed or no valid span exists.
    """
    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return {"TraceID": f"{ctx.trace_id:032x}"}
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Prometheus implementation — supports exemplars + OpenMetrics exposition
# ---------------------------------------------------------------------------


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


class _LogQueueMetricsCollector:
    """Custom collector for logging queue pressure counters."""

    def collect(self) -> Any:
        from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

        from .logging import get_log_queue_stats

        stats = get_log_queue_stats()

        queue_size = GaugeMetricFamily(
            "fastapiobserver_log_queue_size",
            "Current number of log records waiting in the core queue.",
        )
        queue_size.add_metric([], float(stats["queue_size"]))
        yield queue_size

        queue_capacity = GaugeMetricFamily(
            "fastapiobserver_log_queue_capacity",
            "Configured capacity of the core logging queue.",
        )
        queue_capacity.add_metric([], float(stats["queue_capacity"]))
        yield queue_capacity

        queue_policy = GaugeMetricFamily(
            "fastapiobserver_log_queue_overflow_policy_info",
            "Current overflow policy for the core logging queue.",
            labels=["policy"],
        )
        queue_policy.add_metric([str(stats["overflow_policy"])], 1.0)
        yield queue_policy

        enqueued_total = CounterMetricFamily(
            "fastapiobserver_log_queue_enqueued_total",
            "Total log records accepted into the core queue.",
        )
        enqueued_total.add_metric([], float(stats["enqueued_total"]))
        yield enqueued_total

        dropped_total = CounterMetricFamily(
            "fastapiobserver_log_queue_dropped_total",
            "Total log records dropped due to queue pressure.",
            labels=["reason"],
        )
        dropped_total.add_metric(["drop_oldest"], float(stats["dropped_oldest_total"]))
        dropped_total.add_metric(["drop_newest"], float(stats["dropped_newest_total"]))
        yield dropped_total

        blocked_total = CounterMetricFamily(
            "fastapiobserver_log_queue_blocked_total",
            "Total times producers entered blocking mode while queue was full.",
        )
        blocked_total.add_metric([], float(stats["blocked_total"]))
        yield blocked_total

        block_timeouts_total = CounterMetricFamily(
            "fastapiobserver_log_queue_block_timeouts_total",
            "Total blocking enqueue attempts that timed out and dropped the newest record.",
        )
        block_timeouts_total.add_metric([], float(stats["block_timeout_total"]))
        yield block_timeouts_total


# ---------------------------------------------------------------------------
# Builder — Open/Closed: add new backends without modifying existing code
# ---------------------------------------------------------------------------


def build_metrics_backend(
    enabled: bool,
    *,
    service: str = "api",
    environment: str = "development",
    exemplars_enabled: bool = False,
) -> MetricsBackend:
    """Factory for selecting the appropriate metrics backend."""
    if not enabled:
        return NoopMetricsBackend()
    _validate_prometheus_multiprocess_dir()
    backend = PrometheusMetricsBackend(
        service=service,
        environment=environment,
        exemplars_enabled=exemplars_enabled,
    )
    _register_log_queue_metrics_collector()
    return backend


# ---------------------------------------------------------------------------
# /metrics endpoint — supports both classic Prometheus and OpenMetrics format
# ---------------------------------------------------------------------------

MetricsFormat = Literal["prometheus", "openmetrics", "negotiate"]


def _accepts_openmetrics(accept_header: str) -> bool:
    """Return True if *accept_header* includes ``application/openmetrics-text``
    with a quality factor > 0.

    Handles comma-separated media types, optional params (``; q=...``),
    and case-insensitive matching.  ``q=0`` explicitly means "not acceptable".
    """
    if not accept_header:
        return False
    for media_range in accept_header.split(","):
        parts = [p.strip() for p in media_range.strip().split(";")]
        media_type = parts[0].lower()
        if media_type != "application/openmetrics-text":
            continue
        # Check quality factor — defaults to 1.0 if absent
        quality = 1.0
        for param in parts[1:]:
            key_val = param.split("=", 1)
            if len(key_val) == 2 and key_val[0].strip().lower() == "q":
                try:
                    quality = float(key_val[1].strip().strip("\"'"))
                except ValueError:
                    quality = 1.0
        if quality > 0:
            return True
    return False


def mount_metrics_endpoint(
    app: FastAPI,
    path: str = "/metrics",
    *,
    metrics_format: MetricsFormat = "negotiate",
) -> None:
    """Mount the ``/metrics`` endpoint on *app*.

    * ``"openmetrics"`` — always serves OpenMetrics (exemplars visible).
    * ``"prometheus"``  — always serves classic Prometheus text format.
    * ``"negotiate"``   — inspects ``Accept`` header; serves OpenMetrics
      when ``application/openmetrics-text`` is accepted, otherwise
      classic Prometheus format (true content negotiation).
    """
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

    if metrics_format == "openmetrics":
        _mount_openmetrics_endpoint(app, path, prometheus_client)
        return

    if metrics_format == "negotiate":
        _mount_negotiating_endpoint(app, path, prometheus_client)
        return

    # Default: classic Prometheus text format
    app.mount(path, prometheus_client.make_asgi_app())


def _mount_negotiating_endpoint(
    app: FastAPI, path: str, prometheus_client: Any
) -> None:
    """Mount endpoint with true Accept-based content negotiation."""
    try:
        from prometheus_client.openmetrics.exposition import (
            CONTENT_TYPE_LATEST as OPENMETRICS_CONTENT_TYPE,
        )
        from prometheus_client.openmetrics.exposition import (
            generate_latest as generate_openmetrics,
        )
    except ImportError:
        _LOGGER.warning(
            "metrics.negotiate.openmetrics_unavailable",
            extra={
                "event": {
                    "message": (
                        "OpenMetrics not available; negotiate falls back "
                        "to Prometheus text format."
                    ),
                },
                "_skip_enrichers": True,
            },
        )
        app.mount(path, prometheus_client.make_asgi_app())
        return

    from prometheus_client import generate_latest as generate_prometheus
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route

    PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
    registry = prometheus_client.REGISTRY

    def negotiating_metrics_endpoint(request: Request) -> Response:
        accept = request.headers.get("accept", "")
        if _accepts_openmetrics(accept):
            return Response(
                generate_openmetrics(registry),
                media_type=OPENMETRICS_CONTENT_TYPE,
            )
        return Response(
            generate_prometheus(registry),
            media_type=PROMETHEUS_CONTENT_TYPE,
        )

    app.routes.insert(
        0, Route(path, endpoint=negotiating_metrics_endpoint, methods=["GET"])
    )


def _mount_openmetrics_endpoint(
    app: FastAPI, path: str, prometheus_client: Any
) -> None:
    """Mount endpoint that always exposes OpenMetrics format with exemplars."""
    try:
        from prometheus_client.openmetrics.exposition import (
            CONTENT_TYPE_LATEST,
            generate_latest,
        )
    except ImportError:
        _LOGGER.warning(
            "metrics.openmetrics.unavailable",
            extra={
                "event": {
                    "message": (
                        "OpenMetrics exposition not available in this "
                        "prometheus_client version; falling back to default."
                    ),
                },
                "_skip_enrichers": True,
            },
        )
        app.mount(path, prometheus_client.make_asgi_app())
        return

    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.routing import Route

    registry = prometheus_client.REGISTRY

    def metrics_endpoint(_request: Request) -> Response:
        return Response(
            generate_latest(registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    app.routes.insert(0, Route(path, endpoint=metrics_endpoint, methods=["GET"]))


# ---------------------------------------------------------------------------
# Path normalization — DRY: reused by middleware and tests
# ---------------------------------------------------------------------------


def normalize_path(path: str) -> str:
    """Replace dynamic path segments with ``/:id`` to control label cardinality."""
    normalized = _UUID_RE.sub("/:id", path)
    normalized = _HEX_RE.sub("/:id", normalized)
    normalized = _NUMBER_RE.sub("/:id", normalized)
    return normalized


# ---------------------------------------------------------------------------
# Multiprocess helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Internal import helper
# ---------------------------------------------------------------------------


def _import_prometheus_client() -> Any:
    try:
        return importlib.import_module("prometheus_client")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Prometheus support requires `pip install fastapi-observer[prometheus]`"
        ) from exc


def _register_log_queue_metrics_collector() -> None:
    global _LOG_QUEUE_COLLECTOR_REGISTERED

    if _is_prometheus_multiprocess_enabled():
        return

    with _LOG_QUEUE_COLLECTOR_LOCK:
        if _LOG_QUEUE_COLLECTOR_REGISTERED:
            return
        prometheus_client = _import_prometheus_client()
        collector = _LogQueueMetricsCollector()
        try:
            prometheus_client.REGISTRY.register(collector)
        except ValueError:
            # Collector already registered by another backend init in-process.
            pass
        _LOG_QUEUE_COLLECTOR_REGISTERED = True
