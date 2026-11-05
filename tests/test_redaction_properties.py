"""Generated cases check the configured policy across managed output paths."""

from contextlib import redirect_stderr
import io
import json
import logging
import queue
from types import MappingProxyType
from urllib.parse import quote

from hypothesis import given, settings, strategies as st
from pydantic import SecretStr
import pytest

from fastapiobserver import ObservabilitySettings, StructuredJsonFormatter
from fastapiobserver.middleware.body_capture import _BodyCapture
from fastapiobserver.middleware.events import _RequestEventBuilder
from fastapiobserver.security import SecurityPolicy
from fastapiobserver.security.redaction import sanitize_record


@given(values=st.dictionaries(st.one_of(st.text(max_size=30), st.integers()),
                             st.one_of(st.none(), st.booleans(), st.integers(), st.text()),
                             max_size=15))
def test_nested_readonly_mappings_preserve_key_conversion_and_redaction(values):
    source = MappingProxyType({**values, "PaSsWoRd": "private-mapping-value"})
    result = sanitize_record({"event": {"nested": source}}, SecurityPolicy())
    expected = sanitize_record({"event": {"nested": dict(source)}}, SecurityPolicy())
    assert result == expected
    assert all(isinstance(key, str) for key in result["event"]["nested"])
    assert "private-mapping-value" not in json.dumps(result)
    assert source["PaSsWoRd"] == "private-mapping-value"


@st.composite
def captured_secrets(draw):
    secret = "private-value-" + draw(st.text(alphabet="abcdef0123456789", min_size=8, max_size=40))
    spelling = draw(st.sampled_from(["password", "client_secret", "api-key"]))
    upper = draw(st.lists(st.booleans(), min_size=len(spelling), max_size=len(spelling)))
    key = "".join(c.upper() if flag else c for c, flag in zip(spelling, upper))
    escaped = draw(st.lists(st.booleans(), min_size=len(key), max_size=len(key)))
    form = draw(st.booleans())
    if form:
        encoded = "".join(f"%{ord(c):02x}" if flag else c for c, flag in zip(key, escaped))
        body = f"{encoded}={quote(secret)}&visible=ok&{encoded}={quote(secret)}"
    else:
        value = {key: secret, "visible": "ok"}
        for wrapper in draw(st.lists(st.sampled_from(["object", "array"]), max_size=24)):
            value = {"node": value} if wrapper == "object" else [None, value]
        body = json.dumps(value)
        encoded = '"' + "".join(f"\\u{ord(c):04x}" if flag else c
                                  for c, flag in zip(key, escaped)) + '"'
        body = body.replace(json.dumps(key), encoded)
        if draw(st.booleans()):
            body += "invalid-json-tail"
    encoded_body = body.encode()
    limit = draw(st.integers(min_value=0, max_value=len(encoded_body) + 16))
    split = draw(st.integers(min_value=0, max_value=len(encoded_body)))
    capture = _BodyCapture(enabled=True, max_length=limit)
    for chunk in (encoded_body[:split], encoded_body[split:]):
        capture.capture_from_message({"type": "http.request", "body": chunk}, "http.request")
    return capture.value, secret


def record_for(body, secret, policy):
    event = _RequestEventBuilder(policy).build(
        method="POST", path="/private", status_code=200, duration_seconds=.1,
        client_ip=None, request_body=body, response_body=body, error_type="ok", exception=None,
    )
    return logging.makeLogRecord({
        "name": "redaction.property", "levelno": logging.INFO, "levelname": "INFO",
        "msg": "request.completed", "event": event, "password": secret,
        "user_context": {"nested": [{"password": secret}]},
    })


@pytest.mark.parametrize("mode", ["mask", "hash", "drop"])
@settings(max_examples=60, deadline=None)
@given(case=captured_secrets())
def test_generated_bodies_and_context_are_redacted_in_json(mode, case):
    body, secret = case
    policy = SecurityPolicy(redaction_mode=mode, hash_key=SecretStr("property-test-key"))
    record = record_for(body, secret, policy)
    payload = json.loads(StructuredJsonFormatter(ObservabilitySettings(), policy).format(record))
    assert secret not in json.dumps(payload)
    assert "private-value-" not in json.dumps(payload)
    assert payload["event"]["path"] == "/private"
    # The caller's object remains available to sibling, application-owned handlers.
    assert record.user_context["nested"][0]["password"] == secret


@pytest.mark.parametrize("mode", ["mask", "hash", "drop"])
@settings(max_examples=40, deadline=None)
@given(case=captured_secrets())
def test_generated_bodies_and_context_are_redacted_in_real_otlp_records(mode, case):
    sdk = pytest.importorskip("opentelemetry.sdk._logs")
    export = pytest.importorskip("opentelemetry.sdk._logs.export")
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from fastapiobserver.otel.logs import _SanitizingOTLPLogHandler
    body, secret = case
    policy = SecurityPolicy(redaction_mode=mode, hash_key=SecretStr("property-test-key"))
    provider = sdk.LoggerProvider(shutdown_on_exit=False)
    exporter = export.InMemoryLogRecordExporter()
    provider.add_log_record_processor(export.SimpleLogRecordProcessor(exporter))
    handler = _SanitizingOTLPLogHandler(LoggingHandler(logger_provider=provider),
                                        security_policy=policy)
    handler.setFormatter(StructuredJsonFormatter(ObservabilitySettings(), policy))
    try:
        handler.emit(record_for(body, secret, policy))
        [emitted] = exporter.get_finished_logs()
        output = str(emitted.log_record.body) + json.dumps(dict(emitted.log_record.attributes))
        assert secret not in output
        assert "private-value-" not in output
        assert json.loads(emitted.log_record.body)["message"] == "request.completed"
    finally:
        handler.close()
        provider.shutdown()


@settings(max_examples=60, deadline=None)
@given(secret=st.text(alphabet="abcdef0123456789", min_size=8, max_size=80))
def test_failing_outputs_never_dump_source_records_to_stderr(secret):
    from fastapiobserver.logging.circuit_breaker import SinkCircuitBreakerHandler
    from fastapiobserver.logging.queueing import LogQueueTelemetry, OverflowPolicyQueueHandler
    secret = "private-value-" + secret

    class Broken(logging.Handler):
        def emit(self, record):
            raise RuntimeError(secret)

    sink = SinkCircuitBreakerHandler(sink_name="property", delegate=Broken(),
                                     failure_threshold=1, recovery_timeout_seconds=1)
    queued = OverflowPolicyQueueHandler(queue.Queue(maxsize=1), overflow_policy="drop_newest",
                                        block_timeout_seconds=0, telemetry=LogQueueTelemetry())
    record = logging.makeLogRecord({"msg": secret, "password": secret, "args": ()})
    output = io.StringIO()
    with redirect_stderr(output):
        sink.emit(record)
        queued.handleError(record)
    assert "log_sink.failed" in output.getvalue()
    assert secret not in output.getvalue()
    sink.close()
    queued.close()
