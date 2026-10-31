from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from typing import TYPE_CHECKING, Any

from .native import native_telemetry_config

if TYPE_CHECKING:
    from fastapi import FastAPI


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
    """Merge ``new_exclusions`` into the excluded URLs of whichever tracing is active.

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


def _merged(excluded_urls: Any, new_exclusions: Iterable[str]) -> Any:
    from opentelemetry.util.http import parse_excluded_urls

    existing = getattr(excluded_urls, "_excluded_urls", None) or ()
    return parse_excluded_urls(",".join(sorted({*existing, *new_exclusions})))
