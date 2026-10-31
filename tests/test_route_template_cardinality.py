"""Tests for route template extraction to prevent metric cardinality explosion."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.middleware import _extract_route_template  # type: ignore[attr-defined]
from fastapiobserver.middleware.events import UNMATCHED_ROUTE


class _FakeRoute:
    """Minimal route stub matching Starlette's Route interface."""

    def __init__(self, path: str) -> None:
        self.path = path


def test_route_template_preferred_over_raw_path() -> None:
    """When scope has a matched route, use its template."""
    scope = {"route": _FakeRoute("/users/{user_id}")}
    result = _extract_route_template(scope, "/users/alice", 200)
    assert result == "/users/{user_id}"


def test_unmatched_404_gets_the_shared_label() -> None:
    """No route matched: a 404 must not put its raw path in the label."""
    scope: dict = {}  # no "route" key
    assert _extract_route_template(scope, "/nonexistent/path", 404) == UNMATCHED_ROUTE


def test_plain_route_without_template_keeps_raw_path() -> None:
    """FastAPI records no route for plain Starlette routes, such as `/metrics`."""
    scope: dict = {}
    assert _extract_route_template(scope, "/metrics", 200) == "/metrics"


def test_fallback_when_route_has_no_path_attr() -> None:
    """Mounted sub-apps may have routes without .path attribute."""

    class _MountedRoute:
        pass

    scope = {"route": _MountedRoute()}
    assert _extract_route_template(scope, "/mounted/raw", 200) == "/mounted/raw"


def test_route_is_none() -> None:
    """scope["route"] explicitly set to None."""
    scope = {"route": None}
    assert _extract_route_template(scope, "/somewhere", 404) == UNMATCHED_ROUTE


def test_nested_route_template() -> None:
    """Deeply nested route with multiple parameters."""
    scope = {"route": _FakeRoute("/orgs/{org_id}/teams/{team_id}/members")}
    result = _extract_route_template(scope, "/orgs/acme-corp/teams/eng/members", 200)
    assert result == "/orgs/{org_id}/teams/{team_id}/members"


def test_scanner_404s_share_one_metrics_label() -> None:
    app = FastAPI()

    @app.get("/orders/{order_id}")
    def order(order_id: int) -> dict[str, int]:
        return {"order_id": order_id}

    install_observability(app, ObservabilitySettings(metrics_enabled=True))
    client = TestClient(app)  # no lifespan: exclusions must still apply

    def unmatched_requests() -> float:
        return sum(
            float(line.rsplit(" ", 1)[1])
            for line in client.get("/metrics").text.splitlines()
            if line.startswith("http_requests_total{") and f'path="{UNMATCHED_ROUTE}"' in line
        )

    before = unmatched_requests()
    probes = [f"/probe-{uuid.uuid4().hex[:8]}.php" for _ in range(20)]
    for path in probes:
        assert client.get(path).status_code == 404
    after_probes = unmatched_requests()
    client.get("/healthz")  # excluded by default, though no route serves it

    assert after_probes - before == 20
    assert unmatched_requests() == after_probes
    assert not any(path in client.get("/metrics").text for path in probes)


def test_routers_included_with_a_prefix_keep_it_in_the_label() -> None:
    # Since FastAPI 0.137, scope["route"].path leaves out prefixes given to
    # include_router() and those of outer routers (fastapi/fastapi#16051).
    app = FastAPI()
    v1, v2, inner = APIRouter(), APIRouter(), APIRouter()

    @v1.get("/items/{item_id}")
    def v1_item(item_id: str) -> dict[str, str]:
        return {}

    @v2.get("/items/{item_id}")
    def v2_item(item_id: str) -> dict[str, str]:
        return {}

    @inner.get("/ping")
    def ping() -> dict[str, str]:
        return {}

    v2.include_router(inner, prefix="/admin")
    app.include_router(v1, prefix="/v1")
    app.include_router(v2, prefix="/v2")
    install_observability(app, ObservabilitySettings(metrics_enabled=True))
    client = TestClient(app)
    for path in ("/v1/items/1", "/v2/items/1", "/v2/admin/ping"):
        assert client.get(path).status_code == 200

    labels = {
        line.split('path="', 1)[1].split('"', 1)[0]
        for line in client.get("/metrics").text.splitlines()
        if line.startswith("http_requests_total{")
    }
    assert {"/v1/items/{item_id}", "/v2/items/{item_id}", "/v2/admin/ping"} <= labels
    assert "/items/{item_id}" not in labels
