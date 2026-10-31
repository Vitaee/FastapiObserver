"""Prometheus backend, ``/metrics`` endpoint and multiprocess helpers.

``prometheus_client`` is an optional dependency, so it is imported lazily.
"""

from __future__ import annotations

import gzip
import logging
import os
import threading
from typing import Any, Callable, Iterator

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from ..config import MetricsFormat
from ..utils import normalize_path

_LOGGER = logging.getLogger("fastapiobserver.metrics")
_HTTP_LABELS = ("service", "environment", "method", "path", "status_code")
# Accept header pinned for the non-negotiating formats; prometheus_client picks the encoder.
_FORCED_ACCEPT = {
    "openmetrics": "application/openmetrics-text",
    "prometheus": "text/plain",
}
_SINK_COUNTERS = {
    "failures_total": "Total sink handler failures observed by circuit breakers.",
    "skipped_total": "Total records skipped while sink circuit breaker was open.",
    "opens_total": "Total transitions into open state for sink circuit breakers.",
    "half_open_total": "Total transitions into half-open state for sink circuit breakers.",
    "closes_total": "Total transitions back to closed state for sink circuit breakers.",
}


def _import_prometheus_client() -> Any:
    try:
        import prometheus_client
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Prometheus support requires `pip install fastapi-observer[prometheus]`"
        ) from exc
    return prometheus_client


def _multiprocess_dir() -> str:
    return os.getenv("PROMETHEUS_MULTIPROC_DIR", "").strip()


def _validate_multiprocess_dir() -> None:
    path = _multiprocess_dir()
    if path and not (os.path.isdir(path) and os.access(path, os.W_OK)):
        raise RuntimeError(
            f"PROMETHEUS_MULTIPROC_DIR={path!r} must be an existing, writable directory. "
            "Create it before starting workers."
        )


def mark_prometheus_process_dead(pid: int) -> None:
    """Clean up a dead worker's files in multiprocess mode (gunicorn ``child_exit``)."""
    if not _multiprocess_dir():
        return
    _import_prometheus_client()
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(pid)


def _get_trace_exemplar() -> dict[str, str] | None:
    """Current OTel trace ID as a Prometheus exemplar, or ``None`` without a valid span."""
    try:
        from opentelemetry import trace as otel_trace

        ctx = otel_trace.get_current_span().get_span_context()
        if ctx and ctx.is_valid:
            return {"TraceID": f"{ctx.trace_id:032x}"}
    except Exception:
        pass
    return None


class _LogQueueMetricsCollector:
    """Exposes log-queue, Logtail DLQ and sink circuit-breaker stats at scrape time."""

    def collect(self) -> Iterator[Any]:
        from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

        from ..logging import get_log_queue_stats, get_sink_circuit_breaker_stats
        from ..sinks import get_logtail_dlq_stats

        queue = get_log_queue_stats()
        yield GaugeMetricFamily(
            "fastapiobserver_log_queue_size",
            "Current number of log records waiting in the core queue.",
            value=float(queue["queue_size"]),
        )
        yield GaugeMetricFamily(
            "fastapiobserver_log_queue_capacity",
            "Configured capacity of the core logging queue.",
            value=float(queue["queue_capacity"]),
        )
        policy = GaugeMetricFamily(
            "fastapiobserver_log_queue_overflow_policy_info",
            "Current overflow policy for the core logging queue.",
            labels=["policy"],
        )
        policy.add_metric([str(queue["overflow_policy"])], 1.0)
        yield policy
        yield CounterMetricFamily(
            "fastapiobserver_log_queue_enqueued_total",
            "Total log records accepted into the core queue.",
            value=float(queue["enqueued_total"]),
        )
        dropped = CounterMetricFamily(
            "fastapiobserver_log_queue_dropped_total",
            "Total log records dropped due to queue pressure.",
            labels=["reason"],
        )
        dropped.add_metric(["drop_oldest"], float(queue["dropped_oldest_total"]))
        dropped.add_metric(["drop_newest"], float(queue["dropped_newest_total"]))
        yield dropped
        yield CounterMetricFamily(
            "fastapiobserver_log_queue_blocked_total",
            "Total times producers entered blocking mode while queue was full.",
            value=float(queue["blocked_total"]),
        )
        yield CounterMetricFamily(
            "fastapiobserver_log_queue_block_timeouts_total",
            "Total blocking enqueue attempts that timed out and dropped the newest record.",
            value=float(queue["block_timeout_total"]),
        )

        dlq = get_logtail_dlq_stats()
        written = CounterMetricFamily(
            "fastapiobserver_dlq_written_total",
            "Total payloads written to local DLQ.",
            labels=["reason"],
        )
        written.add_metric(["queue_overflow"], float(dlq["written_queue_overflow"]))
        written.add_metric(["send_failed"], float(dlq["written_send_failed"]))
        yield written
        yield CounterMetricFamily(
            "fastapiobserver_dlq_write_failures_total",
            "Total failures when attempting to write payloads to the DLQ disk.",
            value=float(dlq["failures"]),
        )
        yield CounterMetricFamily(
            "fastapiobserver_dlq_bytes_total",
            "Total bytes written to the DLQ disk.",
            value=float(dlq["bytes"]),
        )

        sinks = sorted(get_sink_circuit_breaker_stats().items())
        if not sinks:
            return
        state = GaugeMetricFamily(
            "fastapiobserver_sink_circuit_breaker_state_info",
            "Current sink circuit-breaker state.",
            labels=["sink", "state"],
        )
        for sink_name, sink in sinks:
            state.add_metric([sink_name, str(sink["state"])], 1.0)
        yield state
        for key, documentation in _SINK_COUNTERS.items():
            counter = CounterMetricFamily(
                f"fastapiobserver_sink_circuit_breaker_{key}", documentation, labels=["sink"]
            )
            for sink_name, sink in sinks:
                counter.add_metric([sink_name], float(sink[key]))
            yield counter


def _register_log_queue_metrics_collector() -> None:
    # Per-worker queue stats would be meaningless once aggregated across processes.
    if _multiprocess_dir():
        return
    try:
        _import_prometheus_client().REGISTRY.register(_LogQueueMetricsCollector())
    except ValueError:
        pass  # Already registered by an earlier backend build in this process.


class PrometheusMetricsBackend:
    """Prometheus request counter and latency histogram.

    With *exemplars_enabled*, observations carry the current OTel trace ID as an
    exemplar (visible in OpenMetrics output), enabling the metrics -> traces jump
    in Grafana. Exemplars are not supported by the multiprocess collector
    (``PROMETHEUS_MULTIPROC_DIR``), so they are disabled there with a warning.
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

        if self._exemplars_enabled and _multiprocess_dir():
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
        cls = PrometheusMetricsBackend
        with cls._LOCK:
            if cls._REQUEST_COUNT is None:
                cls._REQUEST_COUNT = prometheus_client.Counter(
                    "http_requests_total",
                    "Total count of HTTP requests",
                    _HTTP_LABELS,
                )
                cls._REQUEST_LATENCY = prometheus_client.Histogram(
                    "http_request_duration_seconds",
                    "HTTP request latency",
                    _HTTP_LABELS,
                    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.3, 0.5, 1, 3, 5, 10),
                )

    def observe(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        exemplar = _get_trace_exemplar() if self._exemplars_enabled else None
        labels = (self.service, self.environment, method, path, str(status_code))
        self._REQUEST_COUNT.labels(*labels).inc(exemplar=exemplar)
        self._REQUEST_LATENCY.labels(*labels).observe(duration_seconds, exemplar=exemplar)

    def mount_endpoint(
        self,
        app: FastAPI,
        *,
        path: str = "/metrics",
        metrics_format: MetricsFormat = "negotiate",
    ) -> None:
        mount_metrics_endpoint(app, path=path, metrics_format=metrics_format)


def _build_prometheus_metrics_backend(
    *,
    service: str,
    environment: str,
    exemplars_enabled: bool,
) -> PrometheusMetricsBackend:
    _validate_multiprocess_dir()
    backend = PrometheusMetricsBackend(
        service=service,
        environment=environment,
        exemplars_enabled=exemplars_enabled,
    )
    _register_log_queue_metrics_collector()
    return backend


def _metrics_endpoint(registry: Any, accept: str | None) -> Callable[[Request], Response]:
    """Serve *registry* the way ``prometheus_client``'s own apps do.

    A plain function, so Starlette runs it in its thread pool: rendering takes
    tens to hundreds of milliseconds on large apps and must not block the event
    loop, which ``make_asgi_app`` does. A pinned *accept* overrides negotiation.
    """
    _import_prometheus_client()
    from prometheus_client.exposition import choose_encoder, gzip_accepted

    def metrics(request: Request) -> Response:
        encoder, content_type = choose_encoder(accept or request.headers.get("accept", ""))
        names = request.query_params.getlist("name[]")
        body = encoder(registry.restricted_registry(names) if names else registry)
        headers = {"Content-Type": content_type}
        if gzip_accepted(request.headers.get("accept-encoding", "")):
            body = gzip.compress(body)
            headers["Content-Encoding"] = "gzip"
        return Response(body, headers=headers)

    return metrics


def mount_metrics_endpoint(
    app: FastAPI,
    path: str = "/metrics",
    *,
    metrics_format: MetricsFormat = "negotiate",
) -> None:
    """Serve Prometheus metrics at *path* (GET/HEAD, gzip when accepted).

    * ``"negotiate"``   — ``prometheus_client`` picks OpenMetrics or classic text
      from the ``Accept`` header.
    * ``"openmetrics"`` — always OpenMetrics (exemplars visible).
    * ``"prometheus"``  — always classic Prometheus text format.
    """
    path = normalize_path(path, default="/metrics")
    if any(getattr(route, "path", None) == path for route in app.routes):
        return
    prometheus_client = _import_prometheus_client()
    registry = prometheus_client.REGISTRY
    if _multiprocess_dir():
        from prometheus_client import multiprocess

        registry = prometheus_client.CollectorRegistry(auto_describe=True)
        multiprocess.MultiProcessCollector(registry)
    endpoint = _metrics_endpoint(registry, _FORCED_ACCEPT.get(metrics_format))
    app.routes.insert(0, Route(path, endpoint=endpoint, methods=["GET"]))
