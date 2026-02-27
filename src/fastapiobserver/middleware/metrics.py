from __future__ import annotations

import logging

from ..config import ObservabilitySettings
from ..metrics import MetricsBackend, collapse_dynamic_segments
from ..plugins import emit_metric_hooks
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Scope

class _MetricsRecorder:
    def __init__(
        self,
        *,
        settings: ObservabilitySettings,
        metrics_backend: MetricsBackend,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.metrics_backend = metrics_backend
        self.logger = logger

    def observe(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
        scope: Scope | None = None,
    ) -> None:
        collapsed_path = collapse_dynamic_segments(path)
        excluded_urls = self.settings.metrics_exclude_paths
        
        if scope and "app" in scope:
            app = scope["app"]
            if hasattr(app.state, "_observability_excluded_urls"):
                excluded_urls = app.state._observability_excluded_urls

        if (
            path in excluded_urls
            or collapsed_path in excluded_urls
        ):
            return
        try:
            self.metrics_backend.observe(
                method=method,
                path=collapsed_path,
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
        except Exception:
            self.logger.exception(
                "metrics.observe.failed",
                extra={
                    "event": {"method": method, "path": path},
                    "_skip_enrichers": True,
                },
            )

    def emit_hooks(
        self,
        *,
        scope: Scope,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        request = Request(scope)
        response = Response(status_code=status_code)
        emit_metric_hooks(request, response, duration_seconds)

__all__ = ["_MetricsRecorder"]
