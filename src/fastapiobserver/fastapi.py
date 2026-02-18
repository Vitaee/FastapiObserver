from __future__ import annotations

import logging

from fastapi import FastAPI

from .config import ObservabilitySettings
from .control_plane import RuntimeControlSettings, mount_control_plane
from .logging import setup_logging
from .metrics import PrometheusMetricsBackend, build_metrics_backend, mount_metrics_endpoint
from .middleware import RequestLoggingMiddleware
from .otel import OTelSettings, install_otel
from .security import SecurityPolicy, TrustedProxyPolicy

_LOGGER = logging.getLogger("fastapiobserver.fastapi")


def install_observability(
    app: FastAPI,
    settings: ObservabilitySettings,
    *,
    security_policy: SecurityPolicy | None = None,
    trusted_proxy_policy: TrustedProxyPolicy | None = None,
    metrics_enabled: bool | None = None,
    otel_settings: OTelSettings | None = None,
    runtime_control_settings: RuntimeControlSettings | None = None,
) -> None:
    security_policy = security_policy or SecurityPolicy()
    trusted_proxy_policy = trusted_proxy_policy or TrustedProxyPolicy()

    setup_logging(settings, security_policy=security_policy)

    enable_metrics = settings.metrics_enabled if metrics_enabled is None else metrics_enabled
    metrics_backend = build_metrics_backend(
        enable_metrics,
        service=settings.service,
        environment=settings.environment,
    )

    if isinstance(metrics_backend, PrometheusMetricsBackend):
        mount_metrics_endpoint(app, settings.metrics_path)

    if otel_settings and otel_settings.enabled:
        install_otel(app, settings, otel_settings)

    if (
        security_policy.log_request_body or security_policy.log_response_body
    ) and app.user_middleware:
        _LOGGER.warning(
            "observability.body_capture.middleware_order",
            extra={
                "event": {
                    "message": (
                        "Body capture works best when observability middleware is the "
                        "outermost middleware."
                    ),
                    "existing_middleware": [
                        getattr(middleware.cls, "__name__", str(middleware.cls))
                        for middleware in app.user_middleware
                    ],
                },
                "_skip_enrichers": True,
            },
        )

    if not _has_request_logging_middleware(app):
        app.add_middleware(
            RequestLoggingMiddleware,
            settings=settings,
            security_policy=security_policy,
            trusted_proxy_policy=trusted_proxy_policy,
            metrics_backend=metrics_backend,
        )

    if runtime_control_settings and runtime_control_settings.enabled:
        mount_control_plane(app, runtime_control_settings)


def _has_request_logging_middleware(app: FastAPI) -> bool:
    for middleware in app.user_middleware:
        if middleware.cls is RequestLoggingMiddleware:
            return True
    return False
