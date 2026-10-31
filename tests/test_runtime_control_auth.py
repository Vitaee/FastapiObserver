from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from fastapiobserver.control_plane import RuntimeControlSettings, mount_control_plane
from fastapiobserver.security import TrustedProxyPolicy

OPERATOR = ("127.0.0.1", 50000)


def test_control_plane_requires_token_auth(monkeypatch) -> None:
    monkeypatch.setenv("OBSERVABILITY_CONTROL_TOKEN", "secret-token")
    app = FastAPI()
    mount_control_plane(app, RuntimeControlSettings(enabled=True))
    client = TestClient(app, client=OPERATOR)

    response = client.post(
        "/_observability/control",
        json={"log_level": "DEBUG", "trace_sampling_ratio": 0.25},
    )

    assert response.status_code == 401


def test_control_plane_updates_log_level_and_sampling(monkeypatch) -> None:
    monkeypatch.setenv("OBSERVABILITY_CONTROL_TOKEN", "secret-token")
    app = FastAPI()
    mount_control_plane(app, RuntimeControlSettings(enabled=True))
    client = TestClient(app, client=OPERATOR)

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


@pytest.mark.parametrize(
    ("caller", "forwarded_for", "honor_forwarded_headers", "expected_status"),
    [
        ("203.0.113.9", None, False, 403),  # public address
        ("10.0.0.5", "203.0.113.9", False, 403),  # public caller through an ingress
        ("10.0.0.5", "203.0.113.9", True, 403),  # same, with forwarded headers honoured
        ("10.0.0.5", "10.0.0.9", True, 200),  # in-cluster caller through a proxy
    ],
)
def test_control_plane_only_accepts_trusted_callers(
    monkeypatch, caller, forwarded_for, honor_forwarded_headers, expected_status
) -> None:
    monkeypatch.setenv("OBSERVABILITY_CONTROL_TOKEN", "secret-token")
    app = FastAPI()
    mount_control_plane(
        app,
        RuntimeControlSettings(enabled=True),
        TrustedProxyPolicy(honor_forwarded_headers=honor_forwarded_headers),
    )
    headers = {"Authorization": "Bearer secret-token"}
    if forwarded_for:
        headers["X-Forwarded-For"] = forwarded_for

    response = TestClient(app, client=(caller, 50000)).get(
        "/_observability/control", headers=headers
    )

    assert response.status_code == expected_status
