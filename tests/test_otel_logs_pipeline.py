from __future__ import annotations

import asyncio
import json
import logging
import warnings
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
import pytest
from pydantic import SecretStr

import fastapiobserver.fastapi as fastapi_module
from fastapiobserver.fastapi import install_observability
from fastapiobserver.otel import OTelLogsSettings
from fastapiobserver.otel.logs import _SanitizingOTLPLogHandler
from fastapiobserver.security import SecurityPolicy
from fastapiobserver.config import ObservabilitySettings


class _CollectingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_otlp_mode_requires_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    settings = ObservabilitySettings()

    monkeypatch.setattr(fastapi_module, "install_otel_logs", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="OTLP log mode requires"):
        install_observability(
            app,
            settings,
            otel_logs_settings=OTelLogsSettings(enabled=True, logs_mode="otlp"),
        )


def test_sanitizing_otlp_handler_sanitizes_custom_attributes() -> None:
    collector = _CollectingHandler()
    policy = SecurityPolicy()
    handler = _SanitizingOTLPLogHandler(
        collector,
        security_policy=policy,
    )

    record = logging.makeLogRecord(
        {
            "name": "demo",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "msg": "hello",
            "password": "secret",
            "event": {
                "password": "secret",
                "headers": {"authorization": "Bearer abc"},
            },
        }
    )

    handler.emit(record)

    assert collector.records
    emitted = collector.records[0]
    assert getattr(emitted, "password") == "***"
    event = getattr(emitted, "event")
    assert event["password"] == "***"
    assert event["headers"]["authorization"] == "***"

    # Wrapper emits a cloned record so sibling handlers see the original.
    assert getattr(record, "password") == "secret"
    record_event = getattr(record, "event")
    assert record_event["password"] == "secret"


@pytest.mark.parametrize(
    ("body", "media_type", "mode", "expected"),
    [
        (r'{"pass\u0077ord":"body-secret"}', "application/json", "mask", '{"password":"***"}'),
        ("pass%77ord=body-secret", "application/x-www-form-urlencoded", "mask", "password=***"),
        (r'{"pass\u0077ord":"body-secret', "application/json", "mask", "***"),
        (
            r'{"pass\u0077ord":"\ud800"}',
            "application/json",
            "hash",
            '{"password":"hmac-sha256:'
            '6c82905373e79e4d72bbc6f42eccc81b2c71eb2842e8190053fa24670d66dfc3"}',
        ),
    ],
)
def test_captured_bodies_are_redacted_in_json_and_otlp_logs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    body: str,
    media_type: str,
    mode: str,
    expected: str,
) -> None:
    from fastapiobserver.logging import shutdown_logging
    import fastapiobserver.otel.logs as otel_logs_module

    export = pytest.importorskip("opentelemetry.sdk._logs.export")
    exporter = export.InMemoryLogRecordExporter()
    monkeypatch.setattr(otel_logs_module, "build_otlp_exporter", lambda *_: exporter)
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> Response:
        return Response(await request.body(), media_type=media_type)

    install_observability(
        app,
        ObservabilitySettings(),
        metrics_enabled=False,
        security_policy=SecurityPolicy(
            log_request_body=True,
            log_response_body=True,
            redaction_mode=mode,
            hash_key=SecretStr("test-key"),
        ),
        otel_logs_settings=OTelLogsSettings(enabled=True, logs_mode="both"),
    )
    try:
        with TestClient(app) as client:
            response = client.post("/echo", content=body, headers={"content-type": media_type})
        assert response.content == body.encode()  # Capture must not change application traffic.
        local = [
            json.loads(line) for line in capsys.readouterr().out.splitlines() if line[:1] == "{"
        ]
        otlp = [json.loads(item.log_record.body) for item in exporter.get_finished_logs()]
        for records in (local, otlp):
            [completed] = [row for row in records if row.get("message") == "request.completed"]
            assert completed["event"]["request_body"] == expected
            assert completed["event"]["response_body"] == expected
            assert "body-secret" not in json.dumps(completed)
    finally:
        shutdown_logging()


def _install_otlp_logs_app(monkeypatch: pytest.MonkeyPatch, app: FastAPI) -> Any:
    in_memory = pytest.importorskip("opentelemetry.sdk._logs.export")
    import fastapiobserver.otel.logs as otel_logs_module

    exporter = in_memory.InMemoryLogRecordExporter()
    monkeypatch.setattr(otel_logs_module, "build_otlp_exporter", lambda *_: exporter)
    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        metrics_enabled=False,
        otel_logs_settings=OTelLogsSettings(enabled=True, logs_mode="otlp"),
    )
    return exporter


def _run_lifespan(app: FastAPI) -> None:
    async def _cycle() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(_cycle())


def test_otlp_logs_are_redacted_without_deprecated_or_root_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        exporter = _install_otlp_logs_app(monkeypatch, app)

    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]
    # Only our queue handler may sit on root; an OTel root handler would skip redaction.
    assert not any(
        type(handler).__module__.startswith("opentelemetry")
        for handler in logging.getLogger().handlers
    )

    logging.getLogger("demo").warning("login", extra={"password": "secret"})
    _run_lifespan(app)

    [exported] = [
        item
        for item in exporter.get_finished_logs()
        if item.log_record.attributes.get("password")
    ]
    assert exported.log_record.attributes["password"] == "***"


def test_otlp_logs_strip_credentials_from_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    exporter = _install_otlp_logs_app(monkeypatch, app)

    try:
        raise ConnectionError("could not connect to postgresql://admin:hunter2@db/app")
    except ConnectionError:
        logging.getLogger("demo").exception("db.failed")
    _run_lifespan(app)

    [exported] = [
        item.log_record
        for item in exporter.get_finished_logs()
        if "db.failed" in str(item.log_record.body)
    ]
    attributes = dict(exported.attributes or {})
    assert attributes["exception.type"] == "ConnectionError"
    assert attributes["exception.message"] == (
        "could not connect to postgresql://***:***@db/app"
    )
    assert "ConnectionError" in attributes["exception.stacktrace"]
    assert '"error"' in str(exported.body)
    assert "hunter2" not in str(exported.body) + str(attributes)


def test_logs_from_app_shutdown_reach_otlp_before_provider_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        yield
        logging.getLogger("demo").warning("app.stopping")

    app = FastAPI(lifespan=lifespan)
    exporter = _install_otlp_logs_app(monkeypatch, app)

    _run_lifespan(app)

    bodies = [str(item.log_record.body) for item in exporter.get_finished_logs()]
    assert any("app.stopping" in body for body in bodies)


def test_otlp_logs_carry_the_trace_of_the_span_they_were_written_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    app = FastAPI()
    exporter = _install_otlp_logs_app(monkeypatch, app)

    tracer = sdk_trace.TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("work") as span:
        logging.getLogger("demo").warning("inside.span")
    _run_lifespan(app)

    [exported] = [
        item.log_record
        for item in exporter.get_finished_logs()
        if "inside.span" in str(item.log_record.body)
    ]
    assert exported.trace_id == span.get_span_context().trace_id
    assert exported.span_id == span.get_span_context().span_id
    assert "_otel_span_context" not in (exported.attributes or {})
