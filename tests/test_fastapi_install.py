from __future__ import annotations

from fastapi import FastAPI

from observabilityfastapi import ObservabilitySettings, install_observability
from observabilityfastapi.middleware import RequestLoggingMiddleware


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
        assert "observabilityfastapi[prometheus]" in str(exc)
    else:
        route_paths = [getattr(route, "path", None) for route in app.routes]
        assert settings.metrics_path in route_paths
