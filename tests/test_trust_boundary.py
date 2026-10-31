from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.middleware import _resolve_request_id
from fastapiobserver.request_context import get_request_id
from fastapiobserver.security import TrustedProxyPolicy, resolve_client_ip


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


def test_forwarded_for_uses_rightmost_hop_outside_trusted_proxies() -> None:
    policy = TrustedProxyPolicy(honor_forwarded_headers=True)
    proxy = "10.0.0.5"

    def resolve(*values: bytes) -> str | None:
        return resolve_client_ip(proxy, [(b"x-forwarded-for", v) for v in values], policy)

    assert resolve(b"203.0.113.7") == "203.0.113.7"
    # Proxies append to whatever the client sent, so leftmost entries are forgeable.
    assert resolve(b"6.6.6.6, 203.0.113.7") == "203.0.113.7"
    assert resolve(b"6.6.6.6", b"203.0.113.7") == "203.0.113.7"
    assert resolve(b"203.0.113.7, 10.1.1.1") == "203.0.113.7"
    # Every hop is a trusted proxy: fall back to the leftmost one.
    assert resolve(b"10.2.2.2, 10.1.1.1") == "10.2.2.2"
    assert resolve() == proxy
    # Untrusted peers never get their forwarded headers honored.
    assert resolve_client_ip("198.51.100.1", [(b"x-forwarded-for", b"6.6.6.6")], policy) == (
        "198.51.100.1"
    )
