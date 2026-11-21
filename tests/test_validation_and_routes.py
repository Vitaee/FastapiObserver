"""Tests for 422 validation error diagnostics and startup route checks."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel
import pytest

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.middleware import validation as validation_module


class _Item(BaseModel):
    price: float


class _Order(BaseModel):
    secret_token: str
    items: list[_Item]


def test_validation_errors_log_loc_and_type_without_values_and_increment_metric(
    caplog: pytest.LogCaptureFixture,
) -> None:
    prometheus_client = pytest.importorskip("prometheus_client")
    app = FastAPI()
    router = APIRouter(prefix="/v1")

    @router.post("/orders/{order_id}")
    def create_order(order_id: int, order: _Order, limit: int = 10) -> dict[str, Any]:
        return {"order_id": order_id, "limit": limit}

    app.include_router(router)
    install_observability(app, ObservabilitySettings(), metrics_enabled=True)

    def metric_value(field: str) -> float:
        return (
            prometheus_client.REGISTRY.get_sample_value(
                "fastapiobserver_http_validation_errors_total",
                {"route": "/v1/orders/{order_id}", "field": field},
            )
            or 0.0
        )

    before_price = metric_value("body.items[].price")
    before_token = metric_value("body.secret_token")
    before_limit = metric_value("query.limit")

    with caplog.at_level(logging.WARNING, logger="fastapiobserver.middleware"):
        response = TestClient(app).post(
            "/v1/orders/42?limit=not_an_int",
            json={
                " items": [],
                "items": [{"price": "SENSITIVE_INPUT_VALUE"}, {}],
            },
        )

    assert response.status_code == 422
    [record] = [r for r in caplog.records if r.message == "request.client_error"]
    event = record.event  # type: ignore[attr-defined]
    assert event["status_code"] == 422
    assert event["http.route"] == "/v1/orders/{order_id}"
    assert event["validation_errors"] == [
        {"loc": "query.limit", "type": "int_parsing"},
        {"loc": "body.secret_token", "type": "missing"},
        {"loc": "body.items[0].price", "type": "float_parsing"},
        {"loc": "body.items[1].price", "type": "missing"},
    ]
    assert "SENSITIVE_INPUT_VALUE" not in str(event)
    assert metric_value("query.limit") - before_limit == 1
    assert metric_value("body.secret_token") - before_token == 1
    # Both array items collapse to the same `body.items[].price` metric label.
    assert metric_value("body.items[].price") - before_price == 2


def test_custom_validation_exception_handler_registered_after_install_is_preserved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    install_observability(app, ObservabilitySettings(), metrics_enabled=False)

    @app.exception_handler(RequestValidationError)
    def custom_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse({"custom_error": True}, status_code=422)

    @app.get("/items/{item_id}")
    def get_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    with caplog.at_level(logging.WARNING, logger="fastapiobserver.middleware"):
        response = TestClient(app).get("/items/not-a-number")

    assert response.status_code == 422
    assert response.json() == {"custom_error": True}
    [record] = [r for r in caplog.records if r.message == "request.client_error"]
    assert record.event["validation_errors"] == [  # type: ignore[attr-defined]
        {"loc": "path.item_id", "type": "int_parsing"}
    ]


def test_validation_field_label_cardinality_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validation_module, "_known_fields", set())
    for i in range(validation_module._MAX_VALIDATION_FIELDS):
        assert validation_module._bounded_field_label(f"body.field_{i}") == f"body.field_{i}"
    assert validation_module._bounded_field_label("body.field_overflow") == "<other>"
    assert validation_module._bounded_field_label("body.field_0") == "body.field_0"


def test_shadowed_and_duplicate_routes_warn_once_at_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()

    # 1. Shadowed: /items/{id} registered before /items/me
    @app.get("/items/{id}")
    def item_by_id(id: str) -> dict[str, str]:
        return {"id": id}

    @app.get("/items/me")
    def item_me() -> dict[str, str]:
        return {"id": "me"}

    # 2. Not shadowed: typed /typed/{id:int} before /typed/me, and static before param
    @app.get("/typed/{id:int}")
    def typed_by_id(id: int) -> dict[str, int]:
        return {"id": id}

    @app.get("/typed/me")
    def typed_me() -> dict[str, str]:
        return {"id": "me"}

    @app.get("/typed/1")
    def typed_one() -> dict[str, int]:
        return {"id": 1}

    # 3. Duplicate method + path in an included router registered after install
    router = APIRouter(prefix="/v1")

    @router.post("/users")
    def create_user_v1() -> dict[str, str]:
        return {}

    @router.post("/users")
    def create_user_v1_duplicate() -> dict[str, str]:
        return {}

    with caplog.at_level(logging.WARNING, logger="fastapiobserver.routes"):
        install_observability(app, ObservabilitySettings(), metrics_enabled=False)
        app.include_router(router)
        with TestClient(app):
            pass

    warnings = [(r.message, r.event) for r in caplog.records]  # type: ignore[attr-defined]
    assert warnings == [
        (
            "route.shadowed",
            {
                "path": "/items/me",
                "shadowed_by": "/items/{id}",
                "methods": ["GET"],
            },
        ),
        (
            "route.shadowed",
            {
                "path": "/typed/1",
                "shadowed_by": "/typed/{id:int}",
                "methods": ["GET"],
            },
        ),
        (
            "route.duplicate",
            {
                "path": "/v1/users",
                "methods": ["POST"],
            },
        ),
    ]


def test_db_connection_held_during_http_warns_and_tracks_conn_held_ms(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import time
    import httpx
    from sqlalchemy import create_engine, event, text

    from fastapiobserver import instrument_httpx_client

    prometheus_client = pytest.importorskip("prometheus_client")
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _register_sleep(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.create_function("slow_sql", 0, lambda: (time.sleep(0.005), 1)[1])

    def _mock_http(req: httpx.Request) -> httpx.Response:
        if "slow-http" in str(req.url):
            time.sleep(0.002)
        return httpx.Response(200, json={"ok": True})

    http_client = httpx.Client(transport=httpx.MockTransport(_mock_http), timeout=2.0)
    instrument_httpx_client(http_client)
    app = FastAPI()

    @app.get("/query-dominant/{item_id}")
    def query_dominant_endpoint(item_id: int) -> dict[str, int]:
        # Connection held ~5.1ms, query takes ~5.0ms (< 10x ratio) -> no warning
        with engine.connect() as conn:
            conn.execute(text("SELECT slow_sql()"))
            http_client.get(f"https://api.example.test/v1/orders/{item_id}")
        return {"item_id": item_id}

    @app.get("/bad/{item_id}")
    def bad_endpoint(item_id: int) -> dict[str, int]:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            http_client.get(f"https://api.example.test/v1/slow-http/{item_id}")
        return {"item_id": item_id}

    install_observability(
        app, ObservabilitySettings(), metrics_enabled=True, db_engine=engine
    )

    def held_metric(route: str) -> float:
        return (
            prometheus_client.REGISTRY.get_sample_value(
                "fastapiobserver_db_connection_held_during_http_total",
                {"route": route},
            )
            or 0.0
        )

    before_dom = held_metric("/query-dominant/{item_id}")
    before_bad = held_metric("/bad/{item_id}")

    try:
        with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
            with TestClient(app) as client:
                assert client.get("/query-dominant/1").status_code == 200
                assert client.get("/bad/1").status_code == 200
                assert client.get("/bad/2").status_code == 200
    finally:
        http_client.close()
        engine.dispose()

    dom_event, bad_event_1, bad_event_2 = [
        r.event  # type: ignore[attr-defined]
        for r in caplog.records
        if r.message == "request.completed"
    ]
    assert "diagnostics" not in dom_event
    assert dom_event["db"]["sqlalchemy"]["conn_held_ms"] >= dom_event["db"]["ms"]
    assert dom_event["response_bytes"] > 0

    assert bad_event_1["diagnostics"] == ["db.connection_held_during_http"]
    assert bad_event_2["diagnostics"] == ["db.connection_held_during_http"]
    assert bad_event_1["db"]["sqlalchemy"]["conn_held_ms"] > 0

    [held_warning] = [
        r.event  # type: ignore[attr-defined]
        for r in caplog.records
        if r.message == "db.connection_held_during_http"
    ]
    assert held_warning["method"] == "GET"
    assert held_warning["route"] == "/bad/{item_id}"
    assert held_warning["target"] == "GET api.example.test"
    assert held_warning["code.file.path"] == __file__
    assert held_warning["code.function.name"].endswith("bad_endpoint")

    assert held_metric("/query-dominant/{item_id}") - before_dom == 0
    assert held_metric("/bad/{item_id}") - before_bad == 2


def test_after_disconnect_calls_and_request_diagnostics(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace

    from fastapiobserver import pools
    from fastapiobserver.loop_watchdog import EventLoopWatchdog
    from fastapiobserver.middleware.request_logging import RequestLoggingMiddleware
    from fastapiobserver.request_context import get_request_state

    async def disconnected_app(scope: Any, receive: Any, send: Any) -> None:
        state = get_request_state()
        assert state is not None
        state.add_call("db", 0.001)  # before disconnect
        msg = await receive()
        assert msg["type"] == "http.disconnect"
        state.add_call("db", 0.002)
        state.add_call("db", 0.003)
        state.add_call("http", 0.005)

    async def noop_send(message: Any) -> None:
        pass

    middleware = RequestLoggingMiddleware(disconnected_app, ObservabilitySettings())
    scope = {"type": "http", "method": "POST", "path": "/stream", "headers": []}
    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        asyncio.run(
            middleware(scope, lambda: asyncio.sleep(0, {"type": "http.disconnect"}), noop_send)
        )

    [disc_record] = [
        r for r in caplog.records if r.message == "request.client_disconnected"
    ]
    disc_event = disc_record.event  # type: ignore[attr-defined]
    assert disc_event["client_disconnected"] is True
    assert disc_event["after_disconnect_calls"] == {"db": 2, "http": 1}
    assert disc_event["db"]["count"] == 3
    assert disc_event["http"]["count"] == 1
    assert disc_event["response_bytes"] == 0

    caplog.clear()
    monkeypatch.setattr(pools, "_last_warned", {})
    monkeypatch.setattr(
        pools,
        "_thread_limiter",
        SimpleNamespace(
            borrowed_tokens=4,
            total_tokens=4,
            statistics=lambda: SimpleNamespace(
                borrowed_tokens=4, total_tokens=4, tasks_waiting=1
            ),
        ),
    )
    monkeypatch.setattr(pools, "db_pool_usage", lambda: [("sqlite://", 5, 5)])

    async def slow_victim_app(scope: Any, receive: Any, send: Any) -> None:
        state = get_request_state()
        assert state is not None
        for _ in range(10):
            state.add_call("db", 0.001, statement="SELECT 1")
            state.add_call("http", 0.001, statement="GET api.example.test/items/:id")
        watchdog = EventLoopWatchdog(0.05)
        watchdog._report(0.06)
        watchdog._answer()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"hello ", "more_body": True})
        await send({"type": "http.response.body", "body": b"world!", "more_body": False})

    victim_mw = RequestLoggingMiddleware(slow_victim_app, ObservabilitySettings())
    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        asyncio.run(
            victim_mw(
                {"type": "http", "method": "GET", "path": "/victim", "headers": []},
                lambda: asyncio.sleep(0, {"type": "http.request", "body": b""}),
                noop_send,
            )
        )

    [record] = [r for r in caplog.records if r.message == "request.completed"]
    assert record.event["response_bytes"] == 12  # type: ignore[attr-defined]
    assert record.event["diagnostics"] == [  # type: ignore[attr-defined]
        "db.n_plus_one",
        "db.pool_exhausted",
        "event_loop.blocked",
        "http.n_plus_one",
        "threadpool.saturated",
    ]


def test_websocket_coverage_and_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite://")
    app = FastAPI()

    @app.websocket("/ws/{room}")
    async def chat_room(websocket: WebSocket, room: str) -> None:
        await websocket.accept()
        text_msg = await websocket.receive_text()
        bytes_msg = await websocket.receive_bytes()
        with engine.connect() as conn:
            for _ in range(10):
                conn.execute(text("SELECT 1"))
        await websocket.send_text(f"ack:{room}:{len(text_msg)}")
        await websocket.send_bytes(bytes_msg)
        await websocket.close(code=1000)

    @app.websocket("/ws-reject")
    async def reject_room(websocket: WebSocket) -> None:
        await websocket.close(code=4401)

    @app.websocket("/ws-fail")
    async def fail_room(websocket: WebSocket) -> None:
        await websocket.accept()
        raise RuntimeError("ws handler boom")

    install_observability(
        app, ObservabilitySettings(), metrics_enabled=False, db_engine=engine
    )

    try:
        with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
            with TestClient(app) as client:
                with client.websocket_connect("/ws/general") as ws:
                    ws.send_text("PRIVATE_WS_PAYLOAD")
                    ws.send_bytes(b"binary-data")
                    assert ws.receive_text() == "ack:general:18"
                    assert ws.receive_bytes() == b"binary-data"

                with pytest.raises(WebSocketDisconnect):
                    with client.websocket_connect("/ws-reject"):
                        pass

                with pytest.raises(RuntimeError, match="ws handler boom"):
                    with client.websocket_connect("/ws-fail") as ws:
                        ws.receive_text()
    finally:
        engine.dispose()

    closed = next(r for r in caplog.records if r.message == "websocket.closed")
    closed_event = closed.event  # type: ignore[attr-defined]
    assert closed_event["path"] == "/ws/general"
    assert closed_event["http.route"] == "/ws/{room}"
    assert closed_event["accepted"] is True
    assert closed_event["close_code"] == 1000
    assert closed_event["messages_received"] == 2
    assert closed_event["messages_sent"] == 2
    assert closed_event["bytes_received"] == len("PRIVATE_WS_PAYLOAD") + len(b"binary-data")
    assert closed_event["bytes_sent"] == len("ack:general:18") + len(b"binary-data")
    assert closed_event["db"]["count"] == 10
    assert closed_event["diagnostics"] == ["db.n_plus_one"]
    assert "PRIVATE_WS_PAYLOAD" not in str(closed_event)

    rejected = next(r for r in caplog.records if r.message == "websocket.rejected")
    rejected_event = rejected.event  # type: ignore[attr-defined]
    assert rejected_event["accepted"] is False
    assert rejected_event["close_code"] == 4401

    failed = next(r for r in caplog.records if r.message == "websocket.failed")
    failed_event = failed.event  # type: ignore[attr-defined]
    assert failed_event["accepted"] is True
    assert failed_event["close_code"] == 1006
    assert failed.exc_info is not None


def test_client_closing_a_websocket_is_logged_as_closed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()

    @app.websocket("/echo")
    async def echo(websocket: WebSocket) -> None:
        await websocket.accept()
        while True:  # the usual loop: WebSocketDisconnect ends it, uncaught
            await websocket.send_text(await websocket.receive_text())

    install_observability(app, ObservabilitySettings(), metrics_enabled=False)

    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        with TestClient(app) as client:
            # Still raised to the server, as without this library.
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/echo") as ws:
                    ws.send_text("hello")
                    assert ws.receive_text() == "hello"

    [session] = [r for r in caplog.records if r.message.startswith("websocket.")]
    assert session.message == "websocket.closed"
    assert session.levelno == logging.INFO
    assert session.exc_info is None
    assert session.event["close_code"] == 1000  # type: ignore[attr-defined]


def test_websocket_queries_spread_over_messages_are_not_n_plus_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite://")
    app = FastAPI()

    @app.websocket("/chat")
    async def chat(websocket: WebSocket) -> None:  # one lookup per message received
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_text()
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                await websocket.send_text(message)
        except WebSocketDisconnect:
            pass

    @app.websocket("/ticker")
    async def ticker(websocket: WebSocket) -> None:  # one lookup per message sent
        await websocket.accept()
        for _ in range(12):
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            await websocket.send_text("tick")
        await websocket.close()

    install_observability(
        app, ObservabilitySettings(), metrics_enabled=False, db_engine=engine
    )

    try:
        with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
            with TestClient(app) as client:
                with client.websocket_connect("/chat") as ws:
                    for number in range(12):
                        ws.send_text(str(number))
                        ws.receive_text()
                with client.websocket_connect("/ticker") as ws:
                    for _ in range(12):
                        ws.receive_text()
    finally:
        engine.dispose()

    assert not [r for r in caplog.records if r.message == "db.n_plus_one"]
    sessions = [
        r.event  # type: ignore[attr-defined]
        for r in caplog.records
        if r.message == "websocket.closed"
    ]
    assert [event["db"]["count"] for event in sessions] == [12, 12]
    assert all("diagnostics" not in event for event in sessions)


def test_websocket_sessions_skip_gc_pause_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import time

    from fastapiobserver import gc_pauses
    from fastapiobserver.middleware import request_logging

    prometheus_client = pytest.importorskip("prometheus_client")
    app = FastAPI()

    @app.websocket("/chat")
    async def chat(websocket: WebSocket) -> None:
        await websocket.accept()
        gc_pauses._on_gc("start", {})
        time.sleep(gc_pauses.GC_PAUSE_SECONDS + 0.02)
        gc_pauses._on_gc("stop", {})
        await websocket.close()

    @app.websocket("/chat-fail")
    async def chat_fail(websocket: WebSocket) -> None:
        await websocket.accept()
        gc_pauses._on_gc("start", {})
        time.sleep(gc_pauses.GC_PAUSE_SECONDS + 0.02)
        gc_pauses._on_gc("stop", {})
        raise RuntimeError("ws gc boom")

    install_observability(app, ObservabilitySettings(), metrics_enabled=True)
    before = prometheus_client.REGISTRY.get_sample_value("fastapiobserver_gc_pause_total") or 0.0
    now = time.perf_counter()

    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        with TestClient(app) as client:
            with monkeypatch.context() as mp:
                mp.setattr(request_logging, "last_loop_block_window", lambda: (now, now + 10.0))
                with client.websocket_connect("/chat"):
                    pass
            with pytest.raises(RuntimeError, match="ws gc boom"):
                with client.websocket_connect("/chat-fail") as ws:
                    ws.receive_text()

    [closed] = [r.event for r in caplog.records if r.message == "websocket.closed"]  # type: ignore[attr-defined]
    assert closed["diagnostics"] == ["event_loop.blocked"]
    assert "gc_pause_ms" not in closed

    [failed] = [r.event for r in caplog.records if r.message == "websocket.failed"]  # type: ignore[attr-defined]
    assert "diagnostics" not in failed
    assert "gc_pause_ms" not in failed

    after = prometheus_client.REGISTRY.get_sample_value("fastapiobserver_gc_pause_total") or 0.0
    assert after == before
