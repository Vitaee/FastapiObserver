from __future__ import annotations

import asyncio

import typing
from typing import Any
import logging

import pytest
from fastapi import FastAPI

import fastapiobserver.fastapi as fastapi_module
from fastapiobserver import (
    OTelMetricsSettings,
    ObservabilitySettings,
    install_observability,
    observability_lifespan,
    register_metrics_backend,
    unregister_metrics_backend,
)
from fastapiobserver.middleware import RequestLoggingMiddleware


def test_install_observability_adds_request_logging_middleware_once() -> None:
    app = FastAPI()
    settings = ObservabilitySettings(app_name="test", service="svc", environment="test")

    install_observability(app, settings, metrics_enabled=False)
    install_observability(app, settings, metrics_enabled=False)

    count = sum(1 for item in app.user_middleware if item.cls is RequestLoggingMiddleware)
    assert count == 1


def test_install_observability_with_metrics_enabled_requires_extra() -> None:
    app = FastAPI()
    settings = ObservabilitySettings(
        app_name="test",
        service="svc",
        environment="test",
        metrics_enabled=True,
    )

    try:
        install_observability(app, settings)
    except RuntimeError as exc:
        assert "fastapi-observer[prometheus]" in str(exc)
    else:
        route_paths = [getattr(route, "path", None) for route in app.routes]
        assert settings.metrics_path in route_paths


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
    app = FastAPI()
    settings = ObservabilitySettings(app_name="test", service="svc", environment="test")
    calls: dict[str, object] = {}

    def fake_install_otel_metrics(
        settings_arg: ObservabilitySettings,
        otel_metrics_settings_arg: OTelMetricsSettings,
        *,
        app: FastAPI | None = None,
        otel_settings: object | None = None,
    ) -> object:
        calls["settings"] = settings_arg
        calls["otel_metrics_settings"] = otel_metrics_settings_arg
        calls["app"] = app
        calls["otel_settings"] = otel_settings
        return object()

    monkeypatch.setattr(fastapi_module, "install_otel_metrics", fake_install_otel_metrics)

    otel_metrics_settings = OTelMetricsSettings(
        enabled=True,
        otlp_endpoint="http://collector:4317",
        protocol="grpc",
    )
    install_observability(
        app,
        settings,
        metrics_enabled=False,
        otel_metrics_settings=otel_metrics_settings,
    )

    assert calls["settings"] is settings
    assert calls["otel_metrics_settings"] == otel_metrics_settings
    assert calls["app"] is app
    assert isinstance(calls["otel_settings"], fastapi_module.OTelSettings)
    assert not calls["otel_settings"].enabled


def test_install_observability_registers_logging_shutdown_hook_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    settings = ObservabilitySettings(app_name="test", service="svc", environment="test")

    mock_calls = []

    def mock_shutdown() -> None:
        mock_calls.append(1)

    monkeypatch.setattr(fastapi_module, "shutdown_logging", mock_shutdown)

    install_observability(app, settings, metrics_enabled=False)
    install_observability(app, settings, metrics_enabled=False)

    assert app in fastapi_module._REGISTERED_APPS
    
    # Verify the shutdown hook actively works when application teardown runs
    async def _run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(_run_lifespan())
        
    assert len(mock_calls) == 1


def test_custom_observability_lifespan_cleans_up_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    
    # We will track calls to shutdown_logging
    mock_calls = []

    def mock_shutdown() -> None:
        mock_calls.append(1)

    monkeypatch.setattr(fastapi_module, "shutdown_logging", mock_shutdown)
    
    async def _run_explicit_lifespan() -> None:
        async with observability_lifespan(app):
            pass

    asyncio.run(_run_explicit_lifespan())
    assert len(mock_calls) == 1


def test_cleanup_runs_again_after_reinstall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    settings = ObservabilitySettings(app_name="test", service="svc", environment="test")
    mock_calls: list[int] = []

    def mock_shutdown() -> None:
        mock_calls.append(1)

    monkeypatch.setattr(fastapi_module, "shutdown_logging", mock_shutdown)

    async def _run_lifespan_cycle() -> None:
        async with app.router.lifespan_context(app):
            pass

    install_observability(app, settings, metrics_enabled=False)
    asyncio.run(_run_lifespan_cycle())

    install_observability(app, settings, metrics_enabled=False)
    asyncio.run(_run_lifespan_cycle())

    assert len(mock_calls) == 2


def test_auto_discover_excluded_routes() -> None:
    app = FastAPI()
    
    @app.get("/visible")
    def visible():
        pass
        
    @app.get("/hidden", include_in_schema=False)
    def hidden():
        pass
        
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
    
    # Check that /hidden and /docs were dynamically added to the exclude tuple
    excluded_urls = app.state._observability_state.excluded_urls
    
    assert "/metrics" in excluded_urls
    assert "/hidden" in excluded_urls
    assert "/docs" in excluded_urls
    assert "/visible" not in excluded_urls


def test_auto_discover_excluded_routes_normalizes_parameterized_paths() -> None:
    app = FastAPI()

    @app.get("/hidden/{item_id}", include_in_schema=False)
    def hidden(item_id: int):
        return item_id

    settings = ObservabilitySettings(
        app_name="test",
        service="svc",
        metrics_exclude_paths=("/metrics",),
    )

    fastapi_module.install_observability(app, settings, metrics_enabled=False)
    fastapi_module._auto_discover_excluded_routes(app, settings)
    excluded_urls = app.state._observability_state.excluded_urls
    assert "/hidden/{item_id}" in excluded_urls
    assert "/hidden/:id" in excluded_urls


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
    caplog: pytest.LogCaptureFixture, 
    monkeypatch: pytest.MonkeyPatch
) -> None:
    app = FastAPI()
    settings = ObservabilitySettings(app_name="test", service="svc")
    
    try:
        from fastapiobserver.otel import exclusions
    except ImportError:
        pytest.skip("OTel packages not installed")

    def mock_update(*args: Any, **kwargs: Any) -> None:
        raise ValueError("Simulated update failure")
        
    monkeypatch.setattr(exclusions, "update_otel_middleware_exclusions", mock_update)
    
    # We patch sys.modules to pretend 'fastapiobserver.otel.exclusions' is loaded 
    # and has our mocked function since it's imported dynamically inside the function
    import sys
    monkeypatch.setitem(sys.modules, "fastapiobserver.otel.exclusions", exclusions)

    with caplog.at_level(logging.WARNING):
        fastapi_module._auto_discover_excluded_routes(app, settings)
        
    assert "observability.fastapi.otel_exclusion_update_failed" in caplog.text
