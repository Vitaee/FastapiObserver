"""Tests for route template extraction to prevent metric cardinality explosion."""

from __future__ import annotations

from fastapiobserver.middleware import _extract_route_template  # type: ignore[attr-defined]


class _FakeRoute:
    """Minimal route stub matching Starlette's Route interface."""

    def __init__(self, path: str) -> None:
        self.path = path


def test_route_template_preferred_over_raw_path() -> None:
    """When scope has a matched route, use its template."""
    scope = {"route": _FakeRoute("/users/{user_id}")}
    result = _extract_route_template(scope, "/users/alice")
    assert result == "/users/{user_id}"


def test_fallback_to_raw_path_when_no_route() -> None:
    """404 / unmatched — no route in scope → raw path used."""
    scope: dict = {}  # no "route" key
    result = _extract_route_template(scope, "/nonexistent/path")
    assert result == "/nonexistent/path"


def test_fallback_when_route_has_no_path_attr() -> None:
    """Mounted sub-apps may have routes without .path attribute."""

    class _MountedRoute:
        pass

    scope = {"route": _MountedRoute()}
    result = _extract_route_template(scope, "/mounted/raw")
    assert result == "/mounted/raw"


def test_route_is_none() -> None:
    """scope["route"] explicitly set to None."""
    scope = {"route": None}
    result = _extract_route_template(scope, "/somewhere")
    assert result == "/somewhere"


def test_nested_route_template() -> None:
    """Deeply nested route with multiple parameters."""
    scope = {"route": _FakeRoute("/orgs/{org_id}/teams/{team_id}/members")}
    result = _extract_route_template(scope, "/orgs/acme-corp/teams/eng/members")
    assert result == "/orgs/{org_id}/teams/{team_id}/members"
