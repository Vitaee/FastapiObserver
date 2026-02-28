from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def update_otel_middleware_exclusions(
    app: FastAPI, new_exclusions: frozenset[str] | set[str]
) -> None:
    """Safely mutate OTel middleware exclusions to include dynamically generated routes.

    Checks the FastAPI middleware stack for the `OpenTelemetryMiddleware`
    and updates its `_excluded_urls` to merge with `new_exclusions`.
    """
    if not hasattr(app, "middleware_stack") or not app.middleware_stack:
        return

    current = app.middleware_stack
    while hasattr(current, "app"):
        if current.__class__.__name__ == "OpenTelemetryMiddleware":
            try:
                from opentelemetry.util.http import parse_excluded_urls
            except ImportError:
                break

            current_excluded: set[str] = set()
            for attr_name in ("excluded_urls", "_excluded_urls"):
                existing_exclusions = getattr(current, attr_name, None)
                existing_patterns = getattr(existing_exclusions, "_excluded_urls", None)
                if existing_patterns:
                    current_excluded.update(
                        str(pattern) for pattern in existing_patterns
                    )

            current_excluded.update(new_exclusions)

            merged = parse_excluded_urls(",".join(sorted(current_excluded)))
            if hasattr(current, "excluded_urls"):
                setattr(current, "excluded_urls", merged)
            else:
                setattr(current, "_excluded_urls", merged)
            break
        current = current.app
