from __future__ import annotations

import json
import logging

import pytest
import strawberry
from fastapi import FastAPI
from fastapi.testclient import TestClient
from strawberry.fastapi import GraphQLRouter

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.integrations.strawberry import StrawberryObservabilityExtension
from fastapiobserver.logging import flush_logging
from fastapiobserver.request_context import clear_user_context, get_user_context


@strawberry.type
class Query:
    @strawberry.field
    def hello(self) -> str:
        logging.getLogger("tests.graphql").info("resolving.hello")
        return "world"


schema = strawberry.Schema(query=Query, extensions=[StrawberryObservabilityExtension])


@pytest.fixture(autouse=True)
def clean_context() -> None:
    clear_user_context()


def test_operation_name_reaches_every_log_line_of_the_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = FastAPI()
    app.include_router(GraphQLRouter(schema), prefix="/graphql")
    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        metrics_enabled=False,
    )

    # Named in the query only: the name is found after parsing.
    response = TestClient(app).post("/graphql", json={"query": "query Greet { hello }"})
    flush_logging()

    assert response.status_code == 200
    assert response.json() == {"data": {"hello": "world"}}
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line[:1] == "{"]
    by_message = {line["message"]: line for line in lines}
    for message in ("resolving.hello", "request.completed"):
        assert by_message[message]["user_context"] == {"graphql": {"operation_name": "Greet"}}


def test_sync_execution_and_anonymous_operations_work() -> None:
    result = schema.execute_sync("{ hello }")

    assert result.errors is None
    assert get_user_context() == {"graphql": {"operation_name": "AnonymousOperation"}}


def test_the_current_span_is_named_after_the_operation() -> None:
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    tracer = sdk_trace.TracerProvider().get_tracer("test")

    with tracer.start_as_current_span("POST /graphql") as span:
        result = schema.execute_sync("query Greet { hello }")

    assert result.errors is None
    assert span.name == "graphql.operation.Greet"
    assert span.attributes["graphql.operation.name"] == "Greet"


@pytest.mark.parametrize(
    "telemetry",
    [{}, {"tracing": False}],  # FastAPI's own tracing, and the instrumentation before 0.142
    ids=["fastapi-tracing", "instrumentation"],
)
def test_the_request_span_is_named_after_the_operation(
    monkeypatch: pytest.MonkeyPatch, telemetry: dict[str, bool]
) -> None:
    trace_api = pytest.importorskip("opentelemetry.trace")
    in_memory = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    import fastapiobserver.otel.tracing as otel_tracing_module
    from fastapiobserver import OTelSettings
    from fastapiobserver.otel.native import native_telemetry_config

    exporter = in_memory.InMemorySpanExporter()
    monkeypatch.setattr(otel_tracing_module, "build_otlp_exporter", lambda *_: exporter)
    app = FastAPI(telemetry=telemetry)  # type: ignore[arg-type]
    if telemetry.get("tracing", True) and native_telemetry_config(app) is None:
        pytest.skip("FastAPI traces requests itself from 0.142")
    app.include_router(GraphQLRouter(schema), prefix="/graphql")
    install_observability(
        app,
        ObservabilitySettings(),
        metrics_enabled=False,
        otel_settings=OTelSettings(enabled=True),
    )

    response = TestClient(app).post("/graphql", json={"query": "query Greet { hello }"})
    trace_api.get_tracer_provider().force_flush()
    flush_logging()

    assert response.status_code == 200
    [server] = [s for s in exporter.get_finished_spans() if s.kind == trace_api.SpanKind.SERVER]
    assert server.name == "graphql.operation.Greet"
    assert server.attributes["graphql.operation.name"] == "Greet"
