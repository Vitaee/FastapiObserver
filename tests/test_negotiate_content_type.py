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
    """prometheus mode serves classic text even when OpenMetrics is preferred."""
    client = TestClient(app_prometheus)
    resp = client.get(
        "/metrics",
        headers={"Accept": "application/openmetrics-text; version=1.0.0"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


def test_metrics_render_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rendering can take hundreds of ms on large apps; it must not block the loop."""
    import asyncio

    from prometheus_client import exposition

    loops_seen: list[bool] = []
    choose_encoder = exposition.choose_encoder

    def recording_choose_encoder(accept_header: str):  # type: ignore[no-untyped-def]
        try:
            asyncio.get_running_loop()
            loops_seen.append(True)
        except RuntimeError:
            loops_seen.append(False)
        return choose_encoder(accept_header)

    monkeypatch.setattr(exposition, "choose_encoder", recording_choose_encoder)
    app = FastAPI()
    mount_metrics_endpoint(app, "/metrics", metrics_format="negotiate")
    resp = TestClient(app).get("/metrics")

    assert resp.status_code == 200
    assert loops_seen == [False]


def test_metrics_gzip_and_name_filter(app_negotiate: FastAPI) -> None:
    client = TestClient(app_negotiate)

    gzipped = client.get("/metrics", headers={"Accept-Encoding": "gzip"})
    assert gzipped.headers["content-encoding"] == "gzip"

    filtered = client.get("/metrics", params={"name[]": "python_info"})
    assert "python_info" in filtered.text
    assert "process_cpu_seconds_total" not in filtered.text
