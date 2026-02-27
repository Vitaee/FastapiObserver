from __future__ import annotations

import logging
from typing import Any
from fastapi import FastAPI

from ..utils import normalize_path as normalize_route_path
from .contracts import MetricsFormat
from .prometheus.client import _import_prometheus_client
from .prometheus.multiprocess import (
    _is_prometheus_multiprocess_enabled,
    _prepare_prometheus_multiprocess,
)

_LOGGER = logging.getLogger("fastapiobserver.metrics")

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
        _prepare_prometheus_multiprocess()
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

__all__ = [
    "mount_metrics_endpoint",
    "_accepts_openmetrics",
    "_mount_negotiating_endpoint",
    "_mount_openmetrics_endpoint",
]
