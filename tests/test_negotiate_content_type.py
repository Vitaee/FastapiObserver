from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapiobserver.metrics import mount_metrics_endpoint


_OPENMETRICS = "application/openmetrics-text; version=1.0.0"


@pytest.mark.parametrize(
    ("metrics_format", "accept", "content_type"),
    [
        ("negotiate", _OPENMETRICS, "application/openmetrics-text"),
        ("negotiate", "text/plain", "text/plain"),
        ("negotiate", None, "text/plain"),
        ("openmetrics", "text/plain", "application/openmetrics-text"),
        ("prometheus", _OPENMETRICS, "text/plain"),
    ],
)
def test_metrics_content_type_follows_the_format_and_accept_header(
    metrics_format: str, accept: str | None, content_type: str
) -> None:
    app = FastAPI()
    mount_metrics_endpoint(app, "/metrics", metrics_format=metrics_format)
    headers = {"Accept": accept} if accept else {}

    response = TestClient(app).get("/metrics", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)


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


def test_metrics_gzip_and_name_filter() -> None:
    app = FastAPI()
    mount_metrics_endpoint(app, "/metrics", metrics_format="negotiate")
    client = TestClient(app)

    gzipped = client.get("/metrics", headers={"Accept-Encoding": "gzip"})
    assert gzipped.headers["content-encoding"] == "gzip"

    filtered = client.get("/metrics", params={"name[]": "python_info"})
    assert "python_info" in filtered.text
    assert "process_cpu_seconds_total" not in filtered.text
