from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from fastapiobserver import (
    OTelSettings,
    ObservabilitySettings,
    RequestIdFilter,
    StructuredJsonFormatter,
    install_observability,
)
import fastapiobserver.otel.tracing as otel_tracing_module
from fastapiobserver.logging import TraceContextFilter


class _CollectingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def _span_pairs(spans: list[Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for span in spans:
        context = getattr(span, "context", None)
        if context is None:
            continue
        pairs.add((f"{context.trace_id:032x}", f"{context.span_id:016x}"))
    return pairs


def test_trace_and_span_ids_are_correlated_in_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.trace")
    in_memory_export = pytest.importorskip(
        "opentelemetry.sdk.trace.export.in_memory_span_exporter"
    )
    trace_api = pytest.importorskip("opentelemetry.trace")
    exporter = in_memory_export.InMemorySpanExporter()
    monkeypatch.setattr(otel_tracing_module, "build_span_exporter", lambda _: exporter)
    monkeypatch.setattr(otel_tracing_module, "has_configured_tracer_provider", lambda *_: False)

    # Force a clean provider so this test does not depend on global OTel state.
    if hasattr(trace_api, "_TRACER_PROVIDER"):
        trace_api._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    set_once = getattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", None)
    if set_once is not None and hasattr(set_once, "_done"):
        set_once._done = False  # type: ignore[attr-defined]

    app = FastAPI()

    @app.get("/orders/{order_id}")
    def get_order(order_id: str) -> dict[str, str]:
        return {"order_id": order_id}

    settings = ObservabilitySettings(
        app_name="orders-api",
        service="orders",
        environment="test",
    )
    install_observability(
        app,
        settings,
        metrics_enabled=False,
        otel_settings=OTelSettings(
            enabled=True,
            service_name="orders",
            service_version="1.0.0",
            environment="test",
        ),
    )

    provider = trace_api.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        pytest.skip("Active tracer provider does not support span processors")

    handler = _CollectingHandler()
    handler.setFormatter(StructuredJsonFormatter(settings))
    handler.addFilter(RequestIdFilter())
    handler.addFilter(TraceContextFilter())
    logging.getLogger().addHandler(handler)

    try:
        response = TestClient(app).get("/orders/123")
        assert response.status_code == 200
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()

    if hasattr(provider, "force_flush"):
        provider.force_flush()

    spans = exporter.get_finished_spans()
    assert spans, "Expected at least one finished span from instrumented request"

    payloads = [json.loads(message) for message in handler.messages]
    correlated_logs = [
        payload
        for payload in payloads
        if payload.get("logger") == "fastapiobserver.middleware"
        and payload.get("trace_id")
        and payload.get("span_id")
    ]
    assert correlated_logs, "Expected middleware logs to include trace/span IDs"

    span_pairs = _span_pairs(spans)
    assert any(
        (payload["trace_id"], payload["span_id"]) in span_pairs
        for payload in correlated_logs
    )
