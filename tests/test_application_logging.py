"""Applications can keep their logging pipeline while installing diagnostics."""

from __future__ import annotations

import json
import logging
import queue
from contextlib import asynccontextmanager
from logging.handlers import QueueHandler, QueueListener

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import fastapiobserver.fastapi as installer
from fastapiobserver import (
    OTelLogsSettings,
    OTelMetricsSettings,
    OTelSettings,
    ObservabilitySettings,
    RuntimeControlSettings,
    install_observability,
    set_user_context,
)


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"message": record.getMessage(), **{
            key: getattr(record, key)
            for key in ("event", "request_id", "trace_id", "span_id", "user_context")
            if hasattr(record, key)
        }})


class _Collector(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.setFormatter(_JsonFormatter())
        self.payloads = []

    def emit(self, record):
        self.payloads.append(json.loads(self.format(record)))


def _logging_state(logger):
    return (
        logger.level,
        logger.propagate,
        logger.disabled,
        tuple(logger.filters),
        tuple((h, h.level, h.formatter, tuple(h.filters)) for h in logger.handlers),
    )


@pytest.fixture
def application_logging(monkeypatch):
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "filters", [logging.Filter()])
    root.setLevel(logging.INFO)
    yield root


def test_application_logging_is_preserved_and_request_context_survives_queue(
    application_logging, monkeypatch,
):
    class ApprovedRedaction(logging.Filter):
        def filter(self, record):
            record.msg = record.getMessage().replace("private@example.test", "[redacted]")
            record.args = ()
            if hasattr(record, "user_context"):
                record.user_context = {
                    **(record.user_context or {}), "email": "[redacted]",
                }
            return True

    direct, queued = _Collector(), _Collector()
    direct.addFilter(ApprovedRedaction())
    queue_handler = QueueHandler(queue.Queue())
    queue_handler.setLevel(logging.INFO)
    queue_handler.addFilter(ApprovedRedaction())
    application_logging.handlers = [direct, queue_handler]
    listener = QueueListener(queue_handler.queue, queued)
    listener.start()

    access = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access, "handlers", [_Collector()])
    monkeypatch.setattr(access, "filters", [logging.Filter()])
    monkeypatch.setattr(access, "level", logging.WARNING)
    monkeypatch.setattr(access, "propagate", False)
    before = _logging_state(application_logging), _logging_state(access)
    flushes = []
    monkeypatch.setattr(installer, "flush_logging", lambda: flushes.append(True))

    @asynccontextmanager
    async def lifespan(_):
        logging.getLogger("orders").info("application.started")
        yield
        logging.getLogger("orders").info("application.stopped")

    app = FastAPI(lifespan=lifespan)

    @app.get("/orders/{order_id}")
    async def order(order_id: int):
        set_user_context({"tenant_slug": "example", "email": "private@example.test"})
        logging.getLogger("orders").info("order.created private@example.test")
        return {"order_id": order_id}

    try:
        install_observability(
            app,
            # These controls belong to the library pipeline and must have no
            # effect on the application's existing levels or handlers.
            ObservabilitySettings(log_level="CRITICAL", log_sample_rate=0),
            configure_logging=False,
            metrics_enabled=False,
            otel_settings=OTelSettings(enabled=False),
            otel_logs_settings=OTelLogsSettings(enabled=True, logs_mode="otlp"),
        )
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            response = client.get("/orders/42", headers={
                "traceparent": "00-" + "1" * 32 + "-" + "2" * 16 + "-01",
            })
        assert before == (_logging_state(application_logging), _logging_state(access))
        assert not flushes
        assert response.status_code == 200
    finally:
        listener.stop()

    for collector in (direct, queued):
        messages = [p["message"] for p in collector.payloads]
        assert messages.count("application.started") == 1
        assert messages.count("application.stopped") == 1
        assert messages.count("order.created [redacted]") == 1
        [diagnostic] = [p for p in collector.payloads if p["message"] == "request.completed"]
        assert diagnostic["request_id"] == response.headers["x-request-id"]
        assert diagnostic["trace_id"] == "1" * 32
        assert diagnostic["span_id"] == "2" * 16
        assert diagnostic["user_context"] == {"tenant_slug": "example", "email": "[redacted]"}
        assert diagnostic["event"]["path"] == "/orders/42"
        assert diagnostic["event"]["status_code"] == 200
        assert diagnostic["event"]["app_duration_ms"] >= diagnostic["event"]["duration_ms"]
        assert "private@example.test" not in json.dumps(collector.payloads)


def test_existing_record_factory_can_set_request_correlation(application_logging, monkeypatch):
    factory = logging.getLogRecordFactory()

    def application_record_factory(*args, **kwargs):
        record = factory(*args, **kwargs)
        record.request_id = None
        record.user_context = None
        return record

    monkeypatch.setattr(logging, "_logRecordFactory", application_record_factory)

    class ApprovedRedaction(logging.Filter):
        def filter(self, record):
            if record.user_context:
                record.user_context = {**record.user_context, "email": "[redacted]"}
            return True

    diagnostic_logger = logging.getLogger("fastapiobserver.middleware")
    redaction = ApprovedRedaction()
    monkeypatch.setattr(diagnostic_logger, "filters", [redaction])
    collector = _Collector()
    application_logging.handlers = [collector]
    app = FastAPI()

    @app.get("/")
    async def index():
        set_user_context({"email": "private@example.test"})
        return {}

    install_observability(app, configure_logging=False, metrics_enabled=False)
    with TestClient(app) as client:
        response = client.get("/")
    [diagnostic] = [p for p in collector.payloads if p["message"] == "request.completed"]
    assert diagnostic["request_id"] == response.headers["x-request-id"]
    assert diagnostic["user_context"] == {"email": "[redacted]"}
    assert diagnostic_logger.filters[-1] is redaction
    assert logging.getLogRecordFactory() is application_record_factory


def test_control_plane_cannot_change_application_owned_log_levels(
    application_logging, monkeypatch,
):
    monkeypatch.setenv("OBSERVABILITY_CONTROL_TOKEN", "secret-token")
    app = FastAPI()
    install_observability(
        app,
        configure_logging=False,
        metrics_enabled=False,
        runtime_control_settings=RuntimeControlSettings(enabled=True),
    )
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/_observability/control",
            headers={"Authorization": "Bearer secret-token"},
            json={"log_level": "DEBUG"},
        )
    assert response.status_code == 409
    assert application_logging.level == logging.INFO


def test_application_owned_otel_providers_export_each_event_once(
    application_logging, monkeypatch,
):
    trace = pytest.importorskip("opentelemetry.trace")
    trace_sdk = pytest.importorskip("opentelemetry.sdk.trace")
    trace_export = pytest.importorskip("opentelemetry.sdk.trace.export")
    memory_spans = pytest.importorskip(
        "opentelemetry.sdk.trace.export.in_memory_span_exporter"
    )
    logs = pytest.importorskip("opentelemetry._logs")
    logs_sdk = pytest.importorskip("opentelemetry.sdk._logs")
    logs_export = pytest.importorskip("opentelemetry.sdk._logs.export")
    log_handler = pytest.importorskip("opentelemetry.instrumentation.logging.handler")
    metrics = pytest.importorskip("opentelemetry.metrics")
    metrics_sdk = pytest.importorskip("opentelemetry.sdk.metrics")
    metrics_export = pytest.importorskip("opentelemetry.sdk.metrics.export")

    spans = memory_spans.InMemorySpanExporter()
    tracer_provider = trace_sdk.TracerProvider()
    tracer_provider.add_span_processor(trace_export.SimpleSpanProcessor(spans))
    trace.set_tracer_provider(tracer_provider)
    exported_logs = logs_export.InMemoryLogRecordExporter()
    logger_provider = logs_sdk.LoggerProvider()
    processor = logs_export.SimpleLogRecordProcessor(exported_logs)
    logger_provider.add_log_record_processor(processor)
    logs.set_logger_provider(logger_provider)
    reader = metrics_export.InMemoryMetricReader()
    meter_provider = metrics_sdk.MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)
    handler = log_handler.LoggingHandler(logger_provider=logger_provider)
    collector = _Collector()
    application_logging.handlers = [handler, collector]
    before = _logging_state(application_logging)

    def unexpected_exporter(*_args, **_kwargs):
        pytest.fail("The application's providers must keep their existing exporters")

    for module in ("tracing", "logs", "metrics"):
        monkeypatch.setattr(
            f"fastapiobserver.otel.{module}.build_otlp_exporter", unexpected_exporter
        )

    app = FastAPI()

    @app.get("/orders/{order_id}")
    async def order(order_id: int):
        logging.getLogger("orders").info("order.created")
        return {"order_id": order_id}

    install_observability(
        app,
        configure_logging=False,
        metrics_enabled=False,
        otel_settings=OTelSettings(enabled=True),
        otel_logs_settings=OTelLogsSettings(
            enabled=True, logs_mode="both", otlp_endpoint="http://unused:4317",
        ),
        otel_metrics_settings=OTelMetricsSettings(enabled=True),
    )
    # A second installation and a second lifespan must not add exports.
    install_observability(app, configure_logging=False)
    for _ in range(2):
        with TestClient(app) as client:
            assert client.get("/orders/42").status_code == 200

    assert trace.get_tracer_provider() is tracer_provider
    assert logs.get_logger_provider() is logger_provider
    assert metrics.get_meter_provider() is meter_provider
    assert before == _logging_state(application_logging)
    messages = [item.log_record.body for item in exported_logs.get_finished_logs()]
    assert messages.count("order.created") == 2
    assert messages.count("request.completed") == 2
    server_spans = [s for s in spans.get_finished_spans() if s.kind == trace.SpanKind.SERVER]
    assert len(server_spans) == 2
    [first, second] = [p for p in collector.payloads if p["message"] == "request.completed"]
    assert first["request_id"] != second["request_id"]
    assert {(p["trace_id"], p["span_id"]) for p in (first, second)} == {
        (f"{s.context.trace_id:032x}", f"{s.context.span_id:016x}") for s in server_spans
    }

    # App shutdown must leave global providers usable by other applications.
    with trace.get_tracer("other-service").start_as_current_span("after-lifespan"):
        logging.getLogger("orders").info("after-lifespan")
    metrics.get_meter("other-service").create_counter("completed").add(1)
    assert spans.get_finished_spans()[-1].name == "after-lifespan"
    assert exported_logs.get_finished_logs()[-1].log_record.body == "after-lifespan"
    assert reader.get_metrics_data().resource_metrics
