from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def update_otel_middleware_exclusions(
    app: FastAPI, new_exclusions: frozenset[str] | set[str]
) -> None:
    """Merge ``new_exclusions`` into the built OTel middleware's excluded URLs.

    ``FastAPIInstrumentor.instrument_app(excluded_urls=...)`` only takes
    exclusions up front, but routes are usually added after
    ``install_observability()`` and OTel offers no supported way to extend
    them later, so this updates the middleware in place.
    """
    current = getattr(app, "middleware_stack", None)
    while current is not None:
        # Match by name so apps without OTel never import the ASGI instrumentation.
        if type(current).__name__ == "OpenTelemetryMiddleware":
            from opentelemetry.util.http import parse_excluded_urls

            existing = getattr(current.excluded_urls, "_excluded_urls", None) or ()
            merged = sorted({*existing, *new_exclusions})
            current.excluded_urls = parse_excluded_urls(",".join(merged))
            return
        current = getattr(current, "app", None)
