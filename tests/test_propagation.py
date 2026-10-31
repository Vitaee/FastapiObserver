from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.propagation import inject_trace_headers, instrument_httpx_client
from fastapiobserver.request_context import get_request_id, set_request_id


def test_inject_trace_headers_propagates_baggage_when_present() -> None:
    baggage = pytest.importorskip("opentelemetry.baggage")
    context = pytest.importorskip("opentelemetry.context")

    token = context.attach(baggage.set_baggage("tenant_id", "acme"))
    try:
        headers = inject_trace_headers({})
    finally:
        context.detach(token)

    assert headers.get("baggage") == "tenant_id=acme"


def test_inject_trace_headers_forwards_request_id_without_overwriting() -> None:
    set_request_id("req-1")

    assert inject_trace_headers({})["x-request-id"] == "req-1"
    assert inject_trace_headers({"X-Request-ID": "mine"}) == {"X-Request-ID": "mine"}


def test_request_id_reaches_the_next_service() -> None:
    propagate = pytest.importorskip("opentelemetry.propagate")
    settings = ObservabilitySettings(app_name="test", service="svc", environment="test")

    downstream = FastAPI()

    @downstream.get("/whoami")
    def whoami() -> dict[str, str | None]:
        return {"request_id": get_request_id()}

    install_observability(downstream, settings, metrics_enabled=False)

    upstream = FastAPI()

    @upstream.get("/call")
    def call() -> dict[str, str | None]:
        headers: dict[str, str] = {}
        propagate.inject(headers)  # what OpenTelemetry's httpx and requests clients call
        # A pod-network address, so the downstream service trusts the forwarded header.
        client = TestClient(downstream, client=("10.0.0.7", 50000))
        return client.get("/whoami", headers=headers).json()

    install_observability(upstream, settings, metrics_enabled=False)

    response = TestClient(upstream).get("/call")

    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_instrument_httpx_client_fails_loudly_without_httpx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *args: None if name == "httpx" else find_spec(name, *args),
    )

    with pytest.raises(RuntimeError, match="request hook"):
        instrument_httpx_client()
