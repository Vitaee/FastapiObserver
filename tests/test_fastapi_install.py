from __future__ import annotations

import asyncio
import io
import json
import sys

import typing
from typing import Any
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import fastapiobserver.fastapi as fastapi_module
from fastapiobserver import (
    OTelMetricsSettings,
    ObservabilitySettings,
    install_observability,
    observability_lifespan,
)
from fastapiobserver.metrics import register_metrics_backend, unregister_metrics_backend
from fastapiobserver.middleware import RequestLoggingMiddleware
from tests.helpers import run_lifespan


def test_install_observability_drops_duplicate_uvicorn_access_log() -> None:
    install_observability(
        FastAPI(),
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        metrics_enabled=False,
    )

    # Uvicorn writes access lines only while this logger has somewhere to send them.
    assert not logging.getLogger("uvicorn.access").hasHandlers()
    assert logging.getLogger("uvicorn.error").hasHandlers()


def test_install_observability_with_metrics_enabled_requires_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    monkeypatch.setitem(sys.modules, "prometheus_client", None)
    settings = ObservabilitySettings(
        app_name="test",
        service="svc",
        environment="test",
        metrics_enabled=True,
    )

    with pytest.raises(RuntimeError, match=r"fastapi-observer\[prometheus\]"):
        install_observability(FastAPI(), settings)


def test_install_observability_uses_registered_metrics_backend_from_settings() -> None:
    app = FastAPI()
    settings = ObservabilitySettings(
        app_name="test",
        service="svc",
        environment="test",
        metrics_enabled=True,
        metrics_backend="custom_backend",
        metrics_path="/custom-metrics",
    )

    class _CustomBackend:
        def observe(
            self,
            method: str,
            path: str,
            status_code: int,
            duration_seconds: float,
        ) -> None:
            return None

        def mount_endpoint(
            self,
            app: FastAPI,
            *,
            path: str = "/metrics",
            metrics_format: str = "negotiate",
        ) -> None:
            app.state.custom_metrics_path = path
            app.state.custom_metrics_format = metrics_format

    def _custom_factory(
        *,
        service: str,
        environment: str,
        exemplars_enabled: bool,
    ) -> _CustomBackend:
        assert service == "svc"
        assert environment == "test"
        assert exemplars_enabled is False
        return _CustomBackend()

    register_metrics_backend("custom_backend", _custom_factory)
    try:
        install_observability(app, settings)
    finally:
        unregister_metrics_backend("custom_backend")

    assert app.state.custom_metrics_path == "/custom-metrics"
    assert app.state.custom_metrics_format == "negotiate"


def test_install_observability_installs_otel_metrics_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics_api = pytest.importorskip("opentelemetry.metrics")
    sdk_metrics = pytest.importorskip("opentelemetry.sdk.metrics")
    sdk_export = pytest.importorskip("opentelemetry.sdk.metrics.export")
    import fastapiobserver.otel.metrics as otel_metrics_module

    monkeypatch.setattr(
        otel_metrics_module,
        "build_otlp_exporter",
        lambda *_: sdk_export.ConsoleMetricExporter(out=io.StringIO()),
    )
    install_observability(
        FastAPI(),
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        metrics_enabled=False,
        otel_metrics_settings=OTelMetricsSettings(
            enabled=True, otlp_endpoint="http://collector:4317", protocol="grpc"
        ),
    )

    assert isinstance(metrics_api.get_meter_provider(), sdk_metrics.MeterProvider)


@pytest.mark.parametrize("fail", [False, True])
def test_observability_lifespan_cleans_up_even_when_the_app_fails(
    monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    flushes: list[int] = []
    monkeypatch.setattr(fastapi_module, "flush_logging", lambda: flushes.append(1))

    async def run() -> None:
        async with observability_lifespan(FastAPI()):
            if fail:
                raise RuntimeError("Failed explicitly")

    if fail:
        with pytest.raises(RuntimeError, match="Failed explicitly"):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert flushes == [1]


def test_cleanup_runs_once_per_lifespan_cycle_even_when_installed_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    settings = ObservabilitySettings(app_name="test", service="svc", environment="test")
    mock_calls: list[int] = []
    monkeypatch.setattr(fastapi_module, "flush_logging", lambda: mock_calls.append(1))

    install_observability(app, settings, metrics_enabled=False)
    install_observability(app, settings, metrics_enabled=False)
    run_lifespan(app)
    assert len(mock_calls) == 1

    install_observability(app, settings, metrics_enabled=False)
    run_lifespan(app)
    assert len(mock_calls) == 2


def test_auto_discover_excluded_routes_and_parameterized_paths() -> None:
    app = FastAPI()

    @app.get("/visible")
    def visible():
        pass

    @app.get("/hidden", include_in_schema=False)
    def hidden():
        pass

    @app.get("/hidden/{item_id}", include_in_schema=False)
    def hidden_item(item_id: int):
        return item_id

    @app.get("/docs")
    def docs():
        pass

    settings = ObservabilitySettings(
        app_name="test",
        service="svc",
        metrics_exclude_paths=("/metrics",),
    )

    fastapi_module.install_observability(app, settings, metrics_enabled=False)
    fastapi_module._auto_discover_excluded_routes(app, settings)

    excluded_urls = app.state._observability_state.excluded_urls
    assert "/metrics" in excluded_urls
    assert "/hidden" in excluded_urls
    assert "/hidden/{item_id}" in excluded_urls
    assert "/hidden/:id" in excluded_urls
    assert "/docs" in excluded_urls
    assert "/visible" not in excluded_urls


def test_auto_discover_updates_active_otel_exclusion_list() -> None:
    pytest.importorskip("opentelemetry.util.http")
    from opentelemetry.util.http import parse_excluded_urls

    app = FastAPI()

    @app.get("/hidden", include_in_schema=False)
    def hidden() -> None:
        return None

    settings = ObservabilitySettings(
        app_name="test",
        service="svc",
        metrics_exclude_paths=("/metrics",),
    )

    class OpenTelemetryMiddleware:
        def __init__(self) -> None:
            self.excluded_urls = parse_excluded_urls("/existing")
            self.app = object()

    app.middleware_stack = OpenTelemetryMiddleware()  # type: ignore[assignment]

    app.state._observability_state = fastapi_module.ObservabilityRuntimeState(settings=settings)
    fastapi_module._auto_discover_excluded_routes(app, settings)

    otel_middleware = typing.cast(Any, app.middleware_stack)
    assert otel_middleware.excluded_urls.url_disabled("/existing")
    assert otel_middleware.excluded_urls.url_disabled("/hidden")


def test_install_observability_forbids_string_db_engine() -> None:
    app = FastAPI()
    settings = ObservabilitySettings(app_name="test", service="svc")
    with pytest.raises(TypeError, match="must be an SQLAlchemy Engine or sequence"):
        fastapi_module.install_observability(
            app, 
            settings, 
            db_engine="sqlite:///:memory:", 
            otel_settings=fastapi_module.OTelSettings(enabled=True)
        )


def test_auto_discover_catches_and_logs_otel_exclusion_errors(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapiobserver.otel import exclusions

    def mock_update(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Simulated update failure")

    monkeypatch.setattr(exclusions, "update_otel_middleware_exclusions", mock_update)

    with caplog.at_level(logging.WARNING):
        fastapi_module._auto_discover_excluded_routes(
            FastAPI(), ObservabilitySettings(app_name="test", service="svc")
        )

    assert "observability.fastapi.otel_exclusion_update_failed" in caplog.text


def test_install_logs_the_settings_that_took_effect(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OBS_PROFILE", "production")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "https://user:s3cret@collector.internal:4318/v1/traces?api_key=abc",
    )

    with caplog.at_level(logging.INFO, logger="fastapiobserver.fastapi"):
        install_observability(FastAPI(), metrics_enabled=False)

    [record] = [r for r in caplog.records if r.getMessage() == "observability.configured"]
    event = record.event  # type: ignore[attr-defined]
    assert event["profile"] == "production"
    assert event["redaction_mode"] == "drop"  # from the production profile's strict preset
    assert event["metrics_path"] is None
    assert event["otlp_endpoint"] == "https://collector.internal:4318/v1/traces"


def test_one_apps_shutdown_leaves_logging_running_for_others_and_restarts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import fastapiobserver.logging as logging_module

    settings = ObservabilitySettings(app_name="test", service="svc", environment="test")
    first, second = FastAPI(), FastAPI()

    @second.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    install_observability(first, settings, metrics_enabled=False)
    install_observability(second, settings, metrics_enabled=False)
    with TestClient(first):
        pass

    client = TestClient(second)
    for _ in range(2):  # a reused TestClient runs the lifespan twice
        with client:
            client.get("/ping")
    logging_module.flush_logging()

    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]
    # Distinct request IDs: a handler another test left behind may repeat lines.
    request_ids = {
        line["request_id"]
        for line in lines
        if line["message"] == "request.completed" and line["event"]["path"] == "/ping"
    }
    assert len(request_ids) == 2


def test_second_install_changes_nothing(caplog: pytest.LogCaptureFixture) -> None:
    app = FastAPI()
    first = ObservabilitySettings(app_name="first", service="svc")
    install_observability(app, first, metrics_enabled=False)
    routes = list(app.routes)

    with caplog.at_level(logging.WARNING, logger="fastapiobserver.fastapi"):
        install_observability(
            app, ObservabilitySettings(app_name="second", service="svc"), metrics_enabled=True
        )

    assert "observability.already_installed" in caplog.messages
    assert app.state._observability_state.settings is first
    assert app.routes == routes  # no second /metrics
    assert sum(1 for item in app.user_middleware if item.cls is RequestLoggingMiddleware) == 1
