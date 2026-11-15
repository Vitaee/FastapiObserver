"""Startup checks for duplicate and shadowed HTTP routes."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
import logging
import re
from typing import Any, Pattern

from starlette.convertors import (
    FloatConvertor,
    IntegerConvertor,
    PathConvertor,
    StringConvertor,
    UUIDConvertor,
)

_LOGGER = logging.getLogger("fastapiobserver.routes")
_PARAM_RE = re.compile(r"\{([^{}:]+)(?::[^{}]+)?\}")
_DEFAULT_CONVERTOR = StringConvertor()
_SAMPLES: dict[type[Any], tuple[str, str]] = {
    StringConvertor: ("a", "b"),
    IntegerConvertor: ("1", "2"),
    FloatConvertor: ("1.5", "2.5"),
    UUIDConvertor: (
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ),
    PathConvertor: ("a/b", "c/d"),
}


def _iter_routes(routes: Sequence[Any]) -> Iterator[Any]:
    for route in routes:
        effective = getattr(route, "effective_route_contexts", None)
        if callable(effective):
            yield from effective()
        else:
            yield route


def _sample_paths(path_format: str, convertors: dict[str, Any]) -> tuple[str, str] | None:
    """Two concrete paths matching *path_format* so static segments cannot match both."""
    try:
        return (
            _PARAM_RE.sub(
                lambda m: _SAMPLES[type(convertors.get(m.group(1), _DEFAULT_CONVERTOR))][0],
                path_format,
            ),
            _PARAM_RE.sub(
                lambda m: _SAMPLES[type(convertors.get(m.group(1), _DEFAULT_CONVERTOR))][1],
                path_format,
            ),
        )
    except KeyError:
        return None


def check_routes(
    app: Any,
    reported: set[tuple[str, str, str, tuple[str, ...]]] | None = None,
) -> None:
    """Warn about duplicate ``method + path`` pairs and routes shadowed by earlier routes."""
    seen: list[tuple[str, Pattern[str], frozenset[str]]] = []
    for route in _iter_routes(getattr(app, "routes", ())):
        path = getattr(route, "path", None)
        path_regex = getattr(route, "path_regex", None)
        raw_methods = getattr(route, "methods", None)
        if not (isinstance(path, str) and path and path_regex and raw_methods):
            continue
        methods = frozenset(str(m).upper() for m in raw_methods)
        path_format = getattr(route, "path_format", None) or path
        convertors = getattr(route, "param_convertors", None)
        samples = _sample_paths(
            path_format, convertors if isinstance(convertors, dict) else {}
        )
        remaining = set(methods)
        for earlier_path, earlier_regex, earlier_methods in seen:
            shared = tuple(sorted(earlier_methods & remaining))
            if not shared:
                continue
            issue: str | None = None
            if earlier_path == path:
                issue = "route.duplicate"
            elif samples is not None and all(
                earlier_regex.match(sample) is not None for sample in samples
            ):
                issue = "route.shadowed"
            if issue is not None:
                key = (issue, path, earlier_path, shared)
                if reported is None or key not in reported:
                    if reported is not None:
                        reported.add(key)
                    event: dict[str, Any] = {"path": path, "methods": list(shared)}
                    if issue == "route.shadowed":
                        event["shadowed_by"] = earlier_path
                    _LOGGER.warning(issue, extra={"event": event, "_skip_enrichers": True})
                remaining.difference_update(shared)
            if not remaining:
                break
        seen.append((path, path_regex, methods))


__all__ = ["check_routes"]
