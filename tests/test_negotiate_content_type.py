"""Tests for true Accept-based content negotiation on /metrics."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapiobserver.metrics import mount_metrics_endpoint


@pytest.fixture
def app_negotiate() -> FastAPI:
    """App with negotiate metrics format."""
    app = FastAPI()
    mount_metrics_endpoint(app, "/metrics", metrics_format="negotiate")
    return app


@pytest.fixture
def app_openmetrics() -> FastAPI:
    """App with openmetrics-only format."""
    app = FastAPI()
    mount_metrics_endpoint(app, "/metrics", metrics_format="openmetrics")
    return app


@pytest.fixture
def app_prometheus() -> FastAPI:
    """App with classic prometheus format."""
    app = FastAPI()
    mount_metrics_endpoint(app, "/metrics", metrics_format="prometheus")
    return app


def test_negotiate_serves_openmetrics_when_accepted(app_negotiate: FastAPI) -> None:
    """When Accept includes openmetrics, serve OpenMetrics."""
    client = TestClient(app_negotiate)
    resp = client.get(
        "/metrics",
        headers={"Accept": "application/openmetrics-text; version=1.0.0"},
    )
    assert resp.status_code == 200
    assert "application/openmetrics-text" in resp.headers["content-type"]


def test_negotiate_serves_prometheus_when_no_openmetrics_accept(
    app_negotiate: FastAPI,
) -> None:
    """When Accept doesn't include openmetrics, serve classic Prometheus."""
    client = TestClient(app_negotiate)
    resp = client.get("/metrics", headers={"Accept": "text/plain"})
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


def test_negotiate_serves_prometheus_by_default(app_negotiate: FastAPI) -> None:
    """No Accept header → classic Prometheus text."""
    client = TestClient(app_negotiate)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # Should NOT be openmetrics since no Accept header
    content_type = resp.headers["content-type"]
    assert "text/plain" in content_type or "text/plain" in content_type


def test_openmetrics_always_serves_openmetrics(app_openmetrics: FastAPI) -> None:
    """openmetrics mode always serves OpenMetrics regardless of Accept."""
    client = TestClient(app_openmetrics)
    resp = client.get("/metrics", headers={"Accept": "text/plain"})
    assert resp.status_code == 200
    assert "openmetrics" in resp.headers["content-type"]


def test_prometheus_always_serves_prometheus(app_prometheus: FastAPI) -> None:
    """prometheus mode always serves classic format."""
    client = TestClient(app_prometheus)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # Prometheus ASGI app serves text/plain; the content type must NOT be openmetrics
    assert "openmetrics" not in resp.headers.get("content-type", "").lower()


def test_negotiate_rejects_openmetrics_with_q_zero(app_negotiate: FastAPI) -> None:
    """q=0 for openmetrics means 'not acceptable' — serve Prometheus instead."""
    client = TestClient(app_negotiate)
    resp = client.get(
        "/metrics",
        headers={"Accept": "application/openmetrics-text; q=0, text/plain"},
    )
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "openmetrics" not in resp.headers["content-type"]
