from __future__ import annotations

import logging
import threading
from typing import Callable, Mapping, Protocol

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("fastapiobserver.plugins")

LogEnricher = Callable[[dict[str, object]], dict[str, object]]
MetricHook = Callable[[Request, Response, float], None]
LogFilterFn = Callable[[logging.LogRecord], bool]


class LogFilter(Protocol):
    """Log filter extension point for record-level allow/deny decisions."""

    def should_log(self, record: logging.LogRecord) -> bool: ...

_LOCK = threading.RLock()
_LOG_ENRICHERS: dict[str, LogEnricher] = {}
_METRIC_HOOKS: dict[str, MetricHook] = {}
_LOG_FILTERS: dict[str, LogFilterFn] = {}


def register_log_enricher(name: str, fn: LogEnricher) -> None:
    with _LOCK:
        _LOG_ENRICHERS[name] = fn


def register_metric_hook(name: str, fn: MetricHook) -> None:
    with _LOCK:
        _METRIC_HOOKS[name] = fn


def register_log_filter(name: str, fn: LogFilter | LogFilterFn) -> None:
    with _LOCK:
        if hasattr(fn, "should_log"):
            _LOG_FILTERS[name] = fn.should_log
            return
        if callable(fn):
            _LOG_FILTERS[name] = fn
            return
        raise TypeError("log filter must be callable or implement should_log(record)")


def unregister_log_filter(name: str) -> None:
    with _LOCK:
        _LOG_FILTERS.pop(name, None)


def clear_plugins() -> None:
    with _LOCK:
        _LOG_ENRICHERS.clear()
        _METRIC_HOOKS.clear()
        _LOG_FILTERS.clear()


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


def apply_log_filters(record: logging.LogRecord) -> bool:
    if getattr(record, "_skip_log_filters", False):
        return True

    with _LOCK:
        log_filters = list(_LOG_FILTERS.items())

    for name, log_filter in log_filters:
        try:
            if not log_filter(record):
                return False
        except Exception:
            logger.exception(
                "log filter failed",
                extra={
                    "event": {"filter": name},
                    "_skip_enrichers": True,
                    "_skip_log_filters": True,
                },
            )
    return True


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
