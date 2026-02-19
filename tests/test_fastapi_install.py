from __future__ import annotations

from fastapi import FastAPI

from fastapiobserver import (
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
