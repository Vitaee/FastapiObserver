from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.middleware import _resolve_request_id
from fastapiobserver.request_context import get_request_id
from fastapiobserver.security import TrustedProxyPolicy


def _build_app(trusted_proxy_policy: TrustedProxyPolicy) -> FastAPI:
    app = FastAPI()

    @app.get("/request-id")
    def request_id() -> dict[str, str | None]:
        return {"request_id": get_request_id()}

    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="test", environment="test"),
        trusted_proxy_policy=trusted_proxy_policy,
        metrics_enabled=False,
    )
    return app


def test_spoofed_request_id_is_ignored_for_untrusted_source() -> None:
    app = _build_app(TrustedProxyPolicy(enabled=True, trusted_cidrs=("127.0.0.1/32",)))
    client = TestClient(app)

    response = client.get("/request-id", headers={"x-request-id": "spoofed-id"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] != "spoofed-id"
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_request_id_is_honored_when_trust_boundary_disabled() -> None:
    app = _build_app(TrustedProxyPolicy(enabled=False))
    client = TestClient(app)

    response = client.get("/request-id", headers={"x-request-id": "trusted-id"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "trusted-id"
    assert response.json()["request_id"] == "trusted-id"


def test_resolve_request_id_accepts_only_trusted_valid_values() -> None:
    assert _resolve_request_id("request_123", True) == "request_123"
    assert _resolve_request_id("bad id with spaces", True) != "bad id with spaces"
    assert _resolve_request_id("request_123", False) != "request_123"
