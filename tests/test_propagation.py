from __future__ import annotations

import pytest

from fastapiobserver.propagation import inject_trace_headers


def test_inject_trace_headers_propagates_baggage_when_present() -> None:
    baggage = pytest.importorskip("opentelemetry.baggage")
    context = pytest.importorskip("opentelemetry.context")

    token = context.attach(baggage.set_baggage("tenant_id", "acme"))
    try:
        headers = inject_trace_headers({})
    finally:
        context.detach(token)

    assert headers.get("baggage") == "tenant_id=acme"
