from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapiobserver.control_plane import RuntimeControlSettings, mount_control_plane


def test_control_plane_requires_token_auth(monkeypatch) -> None:
    monkeypatch.setenv("OBSERVABILITY_CONTROL_TOKEN", "secret-token")
    app = FastAPI()
    mount_control_plane(app, RuntimeControlSettings(enabled=True))
    client = TestClient(app)

    response = client.post(
        "/_observability/control",
        json={"log_level": "DEBUG", "trace_sampling_ratio": 0.25},
    )

    assert response.status_code == 401


def test_control_plane_updates_log_level_and_sampling(monkeypatch) -> None:
    monkeypatch.setenv("OBSERVABILITY_CONTROL_TOKEN", "secret-token")
    app = FastAPI()
    mount_control_plane(app, RuntimeControlSettings(enabled=True))
    client = TestClient(app)

    response = client.post(
        "/_observability/control",
        headers={"Authorization": "Bearer secret-token"},
        json={"log_level": "DEBUG", "trace_sampling_ratio": 0.35},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["log_level"] == "DEBUG"
    assert data["trace_sampling_ratio"] == 0.35
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
