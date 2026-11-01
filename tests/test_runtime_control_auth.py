from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from fastapiobserver.control_plane import RuntimeControlSettings, mount_control_plane
import fastapiobserver.otel.settings as otel_settings
from fastapiobserver.security import TrustedProxyPolicy

OPERATOR = ("127.0.0.1", 50000)
AUTH = {"Authorization": "Bearer secret-token"}


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


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("OBSERVABILITY_CONTROL_TOKEN", "secret-token")
    app = FastAPI()
    mount_control_plane(app, RuntimeControlSettings(enabled=True))
    return TestClient(app, client=OPERATOR)


def test_control_plane_updates_log_level_and_sampling(monkeypatch) -> None:
    monkeypatch.setattr(otel_settings, "_RUNTIME_SAMPLER_INSTALLED", True)

    response = _client(monkeypatch).post(
        "/_observability/control",
        headers=AUTH,
        json={"log_level": "DEBUG", "trace_sampling_ratio": 0.35},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["log_level"] == "DEBUG"
    assert data["trace_sampling_ratio"] == 0.35
    assert data["process_id"] == os.getpid()
    assert logging.getLogger().getEffectiveLevel() == logging.DEBUG


def test_sampling_changes_are_refused_when_this_library_does_not_sample(monkeypatch) -> None:
    # Tracing off, or the app's own tracer provider decides sampling.
    monkeypatch.setattr(otel_settings, "_RUNTIME_SAMPLER_INSTALLED", False)
    client = _client(monkeypatch)

    response = client.post(
        "/_observability/control", headers=AUTH, json={"trace_sampling_ratio": 0.35}
    )

    assert response.status_code == 409
    current = client.get("/_observability/control", headers=AUTH).json()
    assert current["trace_sampling_ratio"] is None


def test_a_non_ascii_token_is_refused_not_a_server_error(monkeypatch) -> None:
    non_ascii = "Bearer s\u00e9cret".encode("latin-1")  # raw header bytes, as a client may send
    response = _client(monkeypatch).get(
        "/_observability/control", headers={"Authorization": non_ascii}
    )

    assert response.status_code == 401


def test_each_change_is_logged_with_what_it_replaced(monkeypatch, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="fastapiobserver.control_plane"):
        _client(monkeypatch).post(
            "/_observability/control", headers=AUTH, json={"log_level": "DEBUG"}
        )

    [record] = [r for r in caplog.records if r.getMessage() == "control_plane.settings_changed"]
    assert record.event["requested"] == {"log_level": "DEBUG"}
    assert record.event["previous"]["process_id"] == os.getpid()
    assert record.event["client_ip"] == OPERATOR[0]


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
