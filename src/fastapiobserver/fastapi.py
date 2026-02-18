from __future__ import annotations

import logging
from typing import Literal

from fastapi import FastAPI

from .config import ObservabilitySettings
from .control_plane import RuntimeControlSettings, mount_control_plane
from .logging import setup_logging
from .metrics import (
    PrometheusMetricsBackend,
    build_metrics_backend,
    mount_metrics_endpoint,
)
from .middleware import RequestLoggingMiddleware
from .otel import OTelLogsSettings, OTelSettings, install_otel, install_otel_logs
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
    otel_logs_settings: OTelLogsSettings | None = None,
    runtime_control_settings: RuntimeControlSettings | None = None,
) -> None:
    """One-call entry point that wires up all observability subsystems.

    Follows the **Facade** pattern: hides the complexity of log setup,
    metrics backend selection, OTel installation, and middleware ordering
    behind a single function.
    """
    security_policy = security_policy or SecurityPolicy()
    trusted_proxy_policy = trusted_proxy_policy or TrustedProxyPolicy()

    # --- 1. Resolve OTLP log handler (before logging setup) ---
    # We get the handler first so it can be routed through the
    # QueueListener pipeline, ensuring filters and sanitization
    # apply uniformly to both local sinks and OTLP output.
    otel_log_handler = None
    logs_mode: Literal["local_json", "otlp", "both"] = "local_json"
    if otel_logs_settings and otel_logs_settings.enabled:
        logs_mode = otel_logs_settings.logs_mode
        otel_log_handler = install_otel_logs(
            settings,
            otel_logs_settings,
            otel_settings=otel_settings,
            security_policy=security_policy,
        )
        if logs_mode == "otlp" and otel_log_handler is None:
            raise RuntimeError(
                "OTLP log mode requires a configured OTLP log handler. "
                "Install `fastapi-observer[otel]` and verify OTLP settings, "
                "or use logs_mode='both' for local fallback."
            )

    # --- 2. Structured logging ---
    extra_handlers = [otel_log_handler] if otel_log_handler else None
    setup_logging(
        settings,
        security_policy=security_policy,
        logs_mode=logs_mode,
        extra_handlers=extra_handlers,
    )

    # --- 3. Metrics backend ---
    enable_metrics = (
        settings.metrics_enabled if metrics_enabled is None else metrics_enabled
    )
    metrics_backend = build_metrics_backend(
        enable_metrics,
        service=settings.service,
        environment=settings.environment,
        exemplars_enabled=settings.exemplars_enabled,
    )

    if isinstance(metrics_backend, PrometheusMetricsBackend):
        mount_metrics_endpoint(
            app,
            settings.metrics_path,
            metrics_format=settings.metrics_format,
        )

    # --- 4. OTel tracing ---
    if otel_settings and otel_settings.enabled:
        install_otel(app, settings, otel_settings)

    # --- 5. Middleware ordering check ---
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

    # --- 6. Request logging middleware ---
    if not _has_request_logging_middleware(app):
        app.add_middleware(
            RequestLoggingMiddleware,
            settings=settings,
            security_policy=security_policy,
            trusted_proxy_policy=trusted_proxy_policy,
            metrics_backend=metrics_backend,
        )

    # --- 7. Runtime control plane ---
    if runtime_control_settings and runtime_control_settings.enabled:
        mount_control_plane(app, runtime_control_settings)


def _has_request_logging_middleware(app: FastAPI) -> bool:
    for middleware in app.user_middleware:
        if middleware.cls is RequestLoggingMiddleware:
            return True
    return False
