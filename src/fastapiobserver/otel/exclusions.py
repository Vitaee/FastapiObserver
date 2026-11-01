from __future__ import annotations

import os
import re
from collections.abc import Iterable, MutableMapping
from typing import TYPE_CHECKING, Any

from .native import native_telemetry_config

if TYPE_CHECKING:
    from fastapi import FastAPI

    from ..config import ObservabilitySettings

_OTEL_EXCLUDED_URLS_ENV_VARS = ("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "OTEL_PYTHON_EXCLUDED_URLS")
_CONTROL_PLANE_PATH = "/_observability/control"  # RuntimeControlSettings.path default
# A route template's parameter, after re.escape(): {name} or {name:path}.
_ESCAPED_PARAM = re.compile(r"\\\{[^}]*\\\}")


def path_pattern(path: str) -> str:
    """An exclusion pattern matching exactly *path*, a path or route template.

    OpenTelemetry searches its patterns anywhere in what it checks: the path for
    FastAPI's own tracing, the full URL for the instrumentation. So anchor both
    ends, or ``/health`` would also exclude ``/healthcare``.
    """
    escaped = _ESCAPED_PARAM.sub(
        lambda param: ".+" if param.group().endswith(r":path\}") else "[^/]+",
        re.escape(path),
    )
    return rf"^(?:\w+://[^/]*)?{escaped}$"


def uses_default_trace_exclusions(settings: ObservabilitySettings) -> bool:
    """True unless trace exclusions were set, in settings or OpenTelemetry's variables."""
    return settings.otel_excluded_urls is None and not any(
        name in os.environ for name in _OTEL_EXCLUDED_URLS_ENV_VARS
    )


def build_excluded_urls_csv(settings: ObservabilitySettings) -> str | None:
    """The trace exclusions, as OpenTelemetry's comma-separated patterns.

    In order of precedence:

    1. ``otel_excluded_urls``, as given; empty means trace everything.
    2. OpenTelemetry's ``OTEL_PYTHON_FASTAPI_EXCLUDED_URLS`` or
       ``OTEL_PYTHON_EXCLUDED_URLS``: None, so OpenTelemetry reads them itself.
    3. The metrics path, ``metrics_exclude_paths`` and the control plane's path.
    """
    if settings.otel_excluded_urls is not None:
        return ",".join(settings.otel_excluded_urls)
    if not uses_default_trace_exclusions(settings):
        return None
    paths = {*settings.metrics_exclude_paths, settings.metrics_path, _CONTROL_PLANE_PATH}
    return ",".join(sorted(map(path_pattern, paths)))


class NativeExclusions:
    """FastAPI's native ``exclude`` hook, with the URL patterns OpenTelemetry's
    instrumentation uses: *csv*, or its environment variables when *csv* is None.
    """

    def __init__(self, csv: str | None) -> None:
        from opentelemetry.util.http import get_excluded_urls, parse_excluded_urls

        self.excluded_urls = (
            parse_excluded_urls(csv) if csv is not None else get_excluded_urls("FASTAPI")
        )

    def __call__(self, scope: MutableMapping[str, Any]) -> bool:
        return bool(self.excluded_urls.url_disabled(scope.get("path", "")))


def update_otel_middleware_exclusions(
    app: FastAPI, new_exclusions: frozenset[str] | set[str]
) -> None:
    """Add the paths ``new_exclusions`` to the excluded URLs of whichever tracing is active.

    Both FastAPI's native tracing and ``FastAPIInstrumentor.instrument_app()`` take
    exclusions up front, but routes are usually added after
    ``install_observability()``, so this updates them in place.
    """
    native_exclude = (native_telemetry_config(app) or {}).get("exclude")
    if isinstance(native_exclude, NativeExclusions):
        native_exclude.excluded_urls = _merged(native_exclude.excluded_urls, new_exclusions)

    current = getattr(app, "middleware_stack", None)
    while current is not None:
        # Match by name so apps without OTel never import the ASGI instrumentation.
        if type(current).__name__ == "OpenTelemetryMiddleware":
            current.excluded_urls = _merged(current.excluded_urls, new_exclusions)
            return
        current = getattr(current, "app", None)


def _merged(excluded_urls: Any, new_paths: Iterable[str]) -> Any:
    from opentelemetry.util.http import parse_excluded_urls

    existing = getattr(excluded_urls, "_excluded_urls", None) or ()
    return parse_excluded_urls(",".join(sorted({*existing, *map(path_pattern, new_paths)})))
