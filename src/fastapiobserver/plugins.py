from __future__ import annotations

import logging
import threading
from typing import Callable, Mapping

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("fastapiobserver.plugins")

LogEnricher = Callable[[dict[str, object]], dict[str, object]]
MetricHook = Callable[[Request, Response, float], None]

_LOCK = threading.RLock()
_LOG_ENRICHERS: dict[str, LogEnricher] = {}
_METRIC_HOOKS: dict[str, MetricHook] = {}


def register_log_enricher(name: str, fn: LogEnricher) -> None:
    with _LOCK:
        _LOG_ENRICHERS[name] = fn


def register_metric_hook(name: str, fn: MetricHook) -> None:
    with _LOCK:
        _METRIC_HOOKS[name] = fn


def clear_plugins() -> None:
    with _LOCK:
        _LOG_ENRICHERS.clear()
        _METRIC_HOOKS.clear()


def apply_log_enrichers(event: dict[str, object]) -> dict[str, object]:
    with _LOCK:
        enrichers = list(_LOG_ENRICHERS.items())

    enriched = dict(event)
    for name, enricher in enrichers:
        try:
            candidate = enricher(dict(enriched))
            if isinstance(candidate, Mapping):
                enriched = dict(candidate)
        except Exception:
            logger.exception(
                "log enricher failed",
                extra={"event": {"enricher": name}, "_skip_enrichers": True},
            )
    return enriched


def emit_metric_hooks(request: Request, response: Response, duration_seconds: float) -> None:
    with _LOCK:
        metric_hooks = list(_METRIC_HOOKS.items())

    for name, metric_hook in metric_hooks:
        try:
            metric_hook(request, response, duration_seconds)
        except Exception:
            logger.exception(
                "metric hook failed",
                extra={"event": {"hook": name}, "_skip_enrichers": True},
            )
