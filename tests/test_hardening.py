"""Regression proofs for diagnostic privacy and bounded state."""

import io
import json
import logging
from contextlib import redirect_stderr

import pytest
from fastapi import FastAPI

from fastapiobserver import ObservabilitySettings, OTelSettings, SecurityPolicy
from fastapiobserver.logging.circuit_breaker import SinkCircuitBreakerHandler
from fastapiobserver.logging.formatter import StructuredJsonFormatter
from fastapiobserver.logging.queueing import LogQueueTelemetry, OverflowPolicyQueueHandler
from fastapiobserver.middleware import RequestLoggingMiddleware
from fastapiobserver.otel.tracing import install_otel
from fastapiobserver.request_context import RequestState


@pytest.mark.parametrize("preset", [None, "strict"])
def test_existing_provider_warning_removes_endpoint_secrets(preset, caplog):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    trace.set_tracer_provider(TracerProvider())
    with caplog.at_level(logging.WARNING):
        install_otel(
            FastAPI(), ObservabilitySettings(),
            OTelSettings(enabled=True, protocol="http/protobuf",
                         otlp_endpoint="https://user:SECRET@collector.test/v1/traces?token=QUERY#FRAG"),
        )
    policy = SecurityPolicy.from_preset(preset) if preset else SecurityPolicy()
    formatter = StructuredJsonFormatter(ObservabilitySettings(), policy)
    record = next(r for r in caplog.records if r.msg == "otel.external_provider.detected")
    payload = json.loads(formatter.format(record))
    assert payload["event"]["otlp_endpoint"] == "https://collector.test/v1/traces"


def test_sink_failure_never_dumps_original_message_or_arguments():
    class BrokenStream:
        def write(self, value):
            raise OSError("sink unavailable")

        def flush(self):
            pass

    sink = logging.StreamHandler(BrokenStream())
    sink.setFormatter(StructuredJsonFormatter(
        ObservabilitySettings(), SecurityPolicy(redacted_fields=("message",)),
    ))
    handler = SinkCircuitBreakerHandler(
        sink_name="broken", delegate=sink, failure_threshold=1, recovery_timeout_seconds=30,
    )
    record = logging.LogRecord("probe", logging.INFO, __file__, 1, "%s", ("SECRET",), None)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        handler.handle(record)
    assert "SECRET" not in stderr.getvalue()
    assert handler.snapshot().failures_total == 1


def test_query_diagnostics_retain_only_bounded_fingerprints(caplog):
    state = RequestState({})
    sensitive = "SELECT $$api_secret=SQL_SECRET$$"
    for _ in range(12):
        state.add_call("db", 0.001, sensitive)
    middleware = RequestLoggingMiddleware(FastAPI(), ObservabilitySettings())
    with caplog.at_level(logging.WARNING):
        middleware._report_repeated_statement("GET", "/report", state)
    record = next(r for r in caplog.records if r.msg == "db.n_plus_one")
    assert "SQL_SECRET" not in repr(record.event)
    assert "statement" not in record.event
    assert record.event["count"] == 12
    assert len(record.event["statement_fingerprint"]) == 64
    assert "SQL_SECRET" not in repr(state.statements)

    for number in range(5_000):
        state.add_call("db", 0.001, f"SELECT {number}")
    assert state.calls["db"][0] == 5_012  # Counts are never capped.
    assert len(state.statements) <= 128

    with caplog.at_level(logging.CRITICAL):
        for number in range(5_000):
            middleware._report_repeated_statement(
                "GET", "/report", RequestState({}, statements={f"{number:064x}": 12})
            )
    assert len(middleware._reported_statements) <= 1_024


def test_queue_preparation_failure_does_not_dump_original_arguments():
    import queue

    class BrokenMessage:
        def __str__(self):
            raise ValueError("SECRET")

        def __repr__(self):
            return "SECRET"

    handler = OverflowPolicyQueueHandler(
        queue.Queue(1), overflow_policy="drop_oldest", block_timeout_seconds=0,
        telemetry=LogQueueTelemetry(),
    )
    record = logging.LogRecord("probe", logging.INFO, __file__, 1, "%s", (BrokenMessage(),), None)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        handler.handle(record)
    assert "SECRET" not in stderr.getvalue()
