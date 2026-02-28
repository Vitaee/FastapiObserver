from __future__ import annotations

import logging
import re
import weakref
import typing
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI

from .config import ObservabilitySettings
from .control_plane import RuntimeControlSettings, mount_control_plane
from .logging import setup_logging, shutdown_logging
from .profiles import apply_profile_context
from .db_tracing import uninstrument_sqlalchemy
from .metrics import (
    MetricsBackend,
    build_metrics_backend,
    collapse_dynamic_segments,
    mount_backend_metrics_endpoint,
)
from .middleware import RequestLoggingMiddleware
from .otel import (
    OTelLogsSettings,
    OTelMetricsSettings,
    OTelSettings,
    install_otel,
    install_otel_logs,
    install_otel_metrics,
)
from .security import SecurityPolicy, TrustedProxyPolicy

_LOGGER = logging.getLogger("fastapiobserver.fastapi")
_REGISTERED_APPS: weakref.WeakSet[FastAPI] = weakref.WeakSet()
_ROUTE_PARAM_PATTERN = re.compile(r"\{[^{}]+\}")


@dataclass
class ObservabilityRuntimeState:
    settings: ObservabilitySettings
    teardown_done: bool = False
    excluded_urls: frozenset[str] = frozenset()


def install_observability(
    app: FastAPI,
    settings: ObservabilitySettings | None = None,
    *,
    security_policy: SecurityPolicy | None = None,
    trusted_proxy_policy: TrustedProxyPolicy | None = None,
    metrics_enabled: bool | None = None,
    otel_settings: OTelSettings | None = None,
    otel_logs_settings: OTelLogsSettings | None = None,
    otel_metrics_settings: OTelMetricsSettings | None = None,
    runtime_control_settings: RuntimeControlSettings | None = None,
    audit_key_provider: object | None = None,
    db_engine: Any | Sequence[Any] | None = None,
    db_commenter_enabled: bool = True,
    db_commenter_options: dict[str, bool] | None = None,
) -> None:
    """One-call entry point that wires up all observability subsystems.

    Follows the **Facade** pattern: hides the complexity of log setup,
    metrics backend selection, OTel installation, and middleware ordering
    behind a single function.
    """
    # --- 0. Zero-Glue Profile Application ---
    # Apply profile defaults via context manager so configure objects
    # pick them up, but environment reverts instantly after.
    with apply_profile_context():
        # Auto-instantiate any missing configuration objects
        settings = settings or ObservabilitySettings.from_env()
        security_policy = security_policy or SecurityPolicy.from_env()
        trusted_proxy_policy = trusted_proxy_policy or TrustedProxyPolicy.from_env()
        otel_settings = otel_settings or OTelSettings.from_env(settings)
        otel_logs_settings = otel_logs_settings or OTelLogsSettings.from_env()
        otel_metrics_settings = otel_metrics_settings or OTelMetricsSettings.from_env()
        runtime_control_settings = runtime_control_settings or RuntimeControlSettings.from_env()

    # Legacy fallback guard
    app.state._observability_teardown_done = False

    # Mark a fresh install cycle so shutdown executes once per lifecycle.
    # Ensure settings are stored on state for lifespan to access
    app.state._observability_state = ObservabilityRuntimeState(settings=settings)

    # --- 1 & 2. Structured logging + OTLP Logs ---
    _setup_structured_logging(
        app=app,
        settings=settings,
        otel_settings=otel_settings,
        otel_logs_settings=otel_logs_settings,
        security_policy=security_policy,
        audit_key_provider=audit_key_provider,
    )

    # --- 3. Metrics backend ---
    metrics_backend = _setup_metrics_backend(app, settings, metrics_enabled)

    # --- 4. OTel tracing ---
    if otel_settings and otel_settings.enabled:
        install_otel(app, settings, otel_settings)

    # --- 4b. Database tracing + SQLCommenter ---
    if db_engine is not None and otel_settings and otel_settings.enabled:
        _setup_database_tracing(db_engine, db_commenter_enabled, db_commenter_options)

    # --- 5. OTel metrics ---
    if otel_metrics_settings and otel_metrics_settings.enabled:
        install_otel_metrics(
            settings,
            otel_metrics_settings,
            app=app,
            otel_settings=otel_settings,
        )

    # --- 6. Middleware & Security Setup ---
    _setup_request_middleware(
        app,
        settings,
        security_policy,
        trusted_proxy_policy,
        metrics_backend,
    )

    # --- 8. Runtime control plane ---
    if runtime_control_settings and runtime_control_settings.enabled:
        mount_control_plane(app, runtime_control_settings)


def _setup_structured_logging(
    app: FastAPI,
    settings: ObservabilitySettings,
    otel_settings: OTelSettings | None,
    otel_logs_settings: OTelLogsSettings | None,
    security_policy: SecurityPolicy,
    audit_key_provider: object | None,
) -> None:
    otel_log_handler = None
    logs_mode: Literal["local_json", "otlp", "both"] = "local_json"
    if otel_logs_settings and otel_logs_settings.enabled:
        logs_mode = otel_logs_settings.logs_mode
        otel_log_handler = install_otel_logs(
            settings,
            otel_logs_settings,
            app=app,
            otel_settings=otel_settings,
            security_policy=security_policy,
        )
        if logs_mode == "otlp" and otel_log_handler is None:
            raise RuntimeError(
                "OTLP log mode requires a configured OTLP log handler. "
                "Install `fastapi-observer[otel]` and verify OTLP settings, "
                "or use logs_mode='both' for local fallback."
            )

    extra_handlers = [otel_log_handler] if otel_log_handler else None
    setup_logging(
        settings,
        security_policy=security_policy,
        logs_mode=logs_mode,
        extra_handlers=extra_handlers,
        audit_key_provider=audit_key_provider,
    )
    _register_logging_shutdown_hook(app)


def _setup_metrics_backend(
    app: FastAPI,
    settings: ObservabilitySettings,
    metrics_enabled: bool | None,
) -> MetricsBackend:
    enable_metrics = (
        settings.metrics_enabled if metrics_enabled is None else metrics_enabled
    )
    metrics_backend = build_metrics_backend(
        enable_metrics,
        service=settings.service,
        environment=settings.environment,
        exemplars_enabled=settings.exemplars_enabled,
        backend=settings.metrics_backend,
    )

    mount_backend_metrics_endpoint(
        app,
        metrics_backend,
        path=settings.metrics_path,
        metrics_format=settings.metrics_format,
    )
    return metrics_backend


def _setup_database_tracing(
    db_engine: Any | Sequence[Any],
    db_commenter_enabled: bool,
    db_commenter_options: dict[str, bool] | None,
) -> None:
    from .db_tracing import (
        instrument_sqlalchemy,
        instrument_sqlalchemy_async,
    )

    if isinstance(db_engine, (str, bytes, bytearray)):
        raise TypeError(
            f"db_engine must be an SQLAlchemy Engine or sequence of Engines, "
            f"not {type(db_engine).__name__}"
        )

    engines = db_engine if isinstance(db_engine, Sequence) else [db_engine]
    for engine in engines:
        if hasattr(engine, "sync_engine"):
            instrument_sqlalchemy_async(
                engine,
                enable_commenter=db_commenter_enabled,
                commenter_options=db_commenter_options,
            )
        else:
            instrument_sqlalchemy(
                engine,
                enable_commenter=db_commenter_enabled,
                commenter_options=db_commenter_options,
            )


def _setup_request_middleware(
    app: FastAPI,
    settings: ObservabilitySettings,
    security_policy: SecurityPolicy,
    trusted_proxy_policy: TrustedProxyPolicy,
    metrics_backend: MetricsBackend,
) -> None:
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

    for middleware in app.user_middleware:
        if middleware.cls is RequestLoggingMiddleware:
            return

    app.add_middleware(
        RequestLoggingMiddleware,
        settings=settings,
        security_policy=security_policy,
        trusted_proxy_policy=trusted_proxy_policy,
        metrics_backend=metrics_backend,
    )

def _get_runtime_state(app: FastAPI) -> ObservabilityRuntimeState | None:
    return getattr(app.state, "_observability_state", None)

def _auto_discover_excluded_routes(app: FastAPI, settings: ObservabilitySettings) -> None:
    """Auto-discover routes like /docs, /openapi.json, or include_in_schema=False."""
    # Build a set of paths to exclude
    excluded = set(settings.metrics_exclude_paths)
    if settings.metrics_path:
        excluded.update(_build_exclude_path_variants(settings.metrics_path))

    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        # Exclude standard utility routes and hidden specs
        if path in {"/docs", "/redoc", "/openapi.json"}:
            excluded.update(_build_exclude_path_variants(path))
        # Exclude implicitly hidden routes (if it has include_in_schema attr)
        elif hasattr(route, "include_in_schema") and not route.include_in_schema:
            excluded.update(_build_exclude_path_variants(path))
            
    # Save into runtime state, do not mutate settings
    state = _get_runtime_state(app)
    if state:
        state.excluded_urls = frozenset(excluded)

    # If OTel middleware is present, dynamically update its excluded URLs
    try:
        from .otel.exclusions import update_otel_middleware_exclusions

        update_otel_middleware_exclusions(app, excluded)
    except ImportError:
        pass
    except Exception:
        _LOGGER.warning(
            "observability.fastapi.otel_exclusion_update_failed",
            exc_info=True,
            extra={"_skip_enrichers": True},
        )


def _build_exclude_path_variants(path: str) -> set[str]:
    template_normalized = _ROUTE_PARAM_PATTERN.sub(":id", path)
    return {
        path,
        collapse_dynamic_segments(path),
        template_normalized,
        collapse_dynamic_segments(template_normalized),
    }


@asynccontextmanager
async def observability_lifespan(app: FastAPI) -> typing.AsyncGenerator[typing.Any, None]:
    """A native FastAPI lifespan context manager for graceful observability teardown.
    
    Developers can yield from this manager directly in their app's lifespan:
    
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with observability_lifespan(app):
            yield
    """
    state = _get_runtime_state(app)
    if state:
        _auto_discover_excluded_routes(app, state.settings)

    try:
        yield {}
    finally:
        _teardown_observability(app)


def _register_logging_shutdown_hook(app: FastAPI) -> None:
    if app in _REGISTERED_APPS:
        return

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def auto_observability_lifespan(
        app_inst: FastAPI,
    ) -> typing.AsyncGenerator[typing.Any, None]:
        async with observability_lifespan(app_inst):
            if original_lifespan:
                # If the user is explicitly using observability_lifespan inside their
                # original_lifespan, this will safely no-op during cleanup because
                # _teardown_observability is idempotent.
                async with original_lifespan(app_inst) as state:
                    yield state
            else:
                yield {}

    app.router.lifespan_context = auto_observability_lifespan
    _REGISTERED_APPS.add(app)


def _teardown_observability(app: FastAPI) -> None:
    state = _get_runtime_state(app)
    if state:
        if state.teardown_done:
            return
    elif getattr(app.state, "_observability_teardown_done", False):
        return
    
    shutdown_logging()
    uninstrument_sqlalchemy()
    
    if state:
        state.teardown_done = True
    else:
        app.state._observability_teardown_done = True

__all__ = ["install_observability", "observability_lifespan"]
