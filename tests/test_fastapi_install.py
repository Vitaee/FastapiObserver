from __future__ import annotations

from fastapi import FastAPI
import pytest

import fastapiobserver.fastapi as fastapi_module
from fastapiobserver import (
    OTelMetricsSettings,
    ObservabilitySettings,
    install_observability,
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
    assert calls["otel_settings"] is None


def test_install_observability_registers_logging_shutdown_hook_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    settings = ObservabilitySettings(app_name="test", service="svc", environment="test")
    calls = {"count": 0}

    def fake_shutdown_logging() -> None:
        calls["count"] += 1

    monkeypatch.setattr(fastapi_module, "shutdown_logging", fake_shutdown_logging)

    install_observability(app, settings, metrics_enabled=False)
    install_observability(app, settings, metrics_enabled=False)

    shutdown_handlers = [
        handler
        for handler in app.router.on_shutdown
        if handler is fake_shutdown_logging
    ]
    assert len(shutdown_handlers) == 1
