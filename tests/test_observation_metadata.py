"""Coverage follows the actual observing layer, never the presence of a provider."""

import logging
import json
import pytest
from types import SimpleNamespace

from fastapiobserver.experimental import observe_operation
from fastapiobserver.request_context import get_request_state


def terminal(caplog):
    return next(r.event for r in caplog.records if r.msg == "operation.completed")


def test_no_observations_are_unknown_and_manual_calls_are_caller_reported(caplog):
    caplog.set_level(logging.INFO)
    with observe_operation("empty"):
        pass
    assert terminal(caplog)["observation"] == {"coverage": "unknown"}
    caplog.clear()
    with observe_operation("manual") as operation:
        operation.record_call("db", .001)
    assert terminal(caplog)["observation"] == {
        "coverage": "caller_reported", "sources": {"db": ["manual"]},
    }


def test_unfinished_native_calls_are_explicitly_partial(caplog):
    from fastapiobserver.core.recording import CallHandle
    caplog.set_level(logging.INFO)
    with observe_operation("pending"):
        call = CallHandle(get_request_state(), "http", source="httpx")
    assert terminal(caplog)["observation"] == {
        "coverage": "partial", "sources": {"http": ["httpx"]},
        "in_flight": {"http": 1},
    }
    assert not call.finish()


def test_native_sql_and_manual_calls_do_not_claim_whole_application_coverage(caplog):
    from fastapiobserver.db_queries import _before, _after
    caplog.set_level(logging.INFO)
    with observe_operation("mixed") as operation:
        context = SimpleNamespace()
        _before(None, None, "SELECT 1", None, context, False)
        _after(None, None, "SELECT 1", None, context, False)
        operation.record_call("db", .001)
    assert terminal(caplog)["observation"] == {
        "coverage": "partial", "sources": {"db": ["manual", "sqlalchemy"]},
    }


def test_sampling_is_unknown_for_a_borrowed_logger(caplog):
    caplog.set_level(logging.INFO)
    with observe_operation("borrowed", logger=logging.getLogger("application")):
        pass
    assert terminal(caplog)["log_sampling"] == {
        "owner": "application", "inclusion_probability": None, "reason": "unknown",
    }


def test_managed_sampling_records_selection_and_failure_promotion():
    from fastapiobserver import ObservabilitySettings, setup_logging
    from fastapiobserver.request_context import set_trace_id
    from fastapiobserver.logging import flush_logging
    payloads = []

    class Capture(logging.Handler):
        def emit(self, record):
            payloads.append(json.loads(self.format(record)))

    setup_logging(ObservabilitySettings(log_sample_rate=.5), logs_mode="otlp",
                  extra_handlers=[Capture()])
    set_trace_id("0" * 31 + "1")
    with observe_operation("selected"):
        pass
    try:
        with observe_operation("failed"):
            raise ValueError("private")
    except ValueError:
        pass
    assert flush_logging()
    selected, failed = [p["event"] for p in payloads if p["message"].startswith("operation.")]
    assert selected["log_sampling"] == {
        "owner": "observer", "inclusion_probability": .5, "reason": "sampled",
    }
    assert failed["log_sampling"] == {
        "owner": "observer", "inclusion_probability": 1.0, "reason": "failure",
    }


def test_native_http_is_limited_to_instrumented_clients(caplog):
    import httpx
    from fastapiobserver import instrument_httpx_client
    caplog.set_level(logging.INFO)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        instrument_httpx_client(client)
        with observe_operation("native.http"):
            client.get("https://example.test/")
    assert terminal(caplog)["observation"] == {
        "coverage": "instrumented_clients", "sources": {"http": ["httpx"]},
    }


def test_sampled_fallback_and_overflow_remain_partial(caplog):
    from opentelemetry import trace
    from opentelemetry.sdk import trace as sdk
    from fastapiobserver.otel.tracing import _request_call_totals
    from fastapiobserver.core.recording import SpanOwners
    caplog.set_level(logging.INFO)
    provider = sdk.TracerProvider()
    processor = _request_call_totals(trace, sdk)
    processor._owners = SpanOwners(limit=1)
    provider.add_span_processor(processor)
    try:
        with observe_operation("sampled"):
            tracer = provider.get_tracer("custom.client")
            first = tracer.start_span("first", kind=trace.SpanKind.CLIENT,
                                      attributes={"db.system.name": "redis"})
            dropped = tracer.start_span("overflow", kind=trace.SpanKind.CLIENT,
                                        attributes={"db.system.name": "redis"})
            first.end()
            dropped.end()
        assert terminal(caplog)["db"]["count"] == 1
        assert terminal(caplog)["observation"] == {
            "coverage": "partial", "sources": {"db": ["sampled_span"]}, "dropped_spans": 1,
        }
    finally:
        provider.shutdown()


def test_sampling_probability_tracks_the_cached_decision_across_reconfiguration():
    from fastapiobserver import ObservabilitySettings, setup_logging
    from fastapiobserver.request_context import set_trace_id
    from fastapiobserver.logging import flush_logging
    payloads = []

    class Capture(logging.Handler):
        def emit(self, record):
            payloads.append(json.loads(self.format(record)))

    setup_logging(ObservabilitySettings(log_sample_rate=.5), logs_mode="otlp",
                  extra_handlers=[Capture()])
    set_trace_id("0" * 31 + "1")
    with observe_operation("reconfigured"):
        logging.getLogger("application").info("first.sampling.decision")
        setup_logging(ObservabilitySettings(log_sample_rate=.25), logs_mode="otlp",
                      extra_handlers=[Capture()])
    assert flush_logging()
    [event] = [p["event"] for p in payloads if p["message"] == "operation.completed"]
    assert event["log_sampling"]["inclusion_probability"] == .5


@pytest.mark.parametrize(("path", "slow_ms", "reason"), [
    ("/ok", .001, "slow"), ("/missing", 1e9, "warning"),
])
def test_retained_request_records_have_probability_one(path, slow_ms, reason):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapiobserver import ObservabilitySettings, install_observability, setup_logging
    payloads = []
    class Capture(logging.Handler):
        def emit(self, record):
            payloads.append(json.loads(self.format(record)))
    app = FastAPI()
    @app.get("/ok")
    async def ok():
        return {"ok": True}
    settings = ObservabilitySettings(log_sample_rate=0, log_slow_request_ms=slow_ms)
    install_observability(app, settings, metrics_enabled=False)
    setup_logging(settings, logs_mode="otlp", extra_handlers=[Capture()])
    with TestClient(app) as client:
        client.get(path)
    [event] = [p["event"] for p in payloads if p["message"].startswith("request.")]
    assert event["log_sampling"] == {
        "owner": "observer", "inclusion_probability": 1.0, "reason": reason,
    }
