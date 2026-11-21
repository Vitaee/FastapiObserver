from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import linecache
import logging
import time
from typing import Any

from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
import pytest

from fastapiobserver import (
    ObservabilitySettings,
    OTelSettings,
    install_observability,
    set_user_context,
)
from fastapiobserver.logging import (
    get_log_bytes_by_route,
    get_log_level,
    set_log_level,
    setup_logging,
    shutdown_logging,
)
from tests.helpers import Collector, capture_logs


def test_failed_request_writes_its_held_debug_logs() -> None:
    settings = ObservabilitySettings(log_level="INFO", log_level_on_error="DEBUG")
    app = FastAPI()
    log = logging.getLogger("tests.held")

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        log.debug("ok detail")
        return {}

    @app.get("/fail")
    def fail() -> dict[str, str]:  # sync: runs in the thread pool
        log.debug("fail detail")
        raise RuntimeError("boom")

    install_observability(app, settings, metrics_enabled=False)
    collector = capture_logs(settings)
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/ok")
    client.get("/fail")
    log.debug("outside a request")
    shutdown_logging()

    messages = [payload["message"] for payload in collector.payloads]
    assert "ok detail" not in messages
    assert "outside a request" not in messages
    assert messages.index("fail detail") < messages.index("request.failed")
    held = collector.payloads[messages.index("fail detail")]
    failed = collector.payloads[messages.index("request.failed")]
    assert held["level"] == "DEBUG"
    assert held["request_id"] == failed["request_id"]


def test_long_messages_are_cut_before_they_are_held_or_queued() -> None:
    from fastapiobserver.logging.queueing import MAX_MESSAGE_CHARS

    settings = ObservabilitySettings(log_level="INFO", log_level_on_error="DEBUG")
    app = FastAPI()
    log = logging.getLogger("tests.long")
    payload = "x" * (MAX_MESSAGE_CHARS + 100)

    @app.get("/fail")
    def fail() -> dict[str, str]:
        log.debug("held %s", payload)
        raise RuntimeError("boom")

    install_observability(app, settings, metrics_enabled=False)
    collector = capture_logs(settings)
    TestClient(app, raise_server_exceptions=False).get("/fail")
    log.info(payload)
    shutdown_logging()

    messages = [p["message"] for p in collector.payloads]
    long_lines = {message[:4]: message for message in messages if len(message) > 1000}
    assert set(long_lines) == {"held", "xxxx"}
    for line in long_lines.values():
        assert len(line) <= MAX_MESSAGE_CHARS and line.endswith(" characters cut]")


def test_user_context_set_in_a_sync_endpoint_reaches_the_request_line() -> None:
    settings = ObservabilitySettings()
    app = FastAPI()

    @app.get("/me")
    def me() -> dict[str, str]:  # sync: runs in the thread pool with a copy of the context
        set_user_context({"user_id": "42"})
        return {}

    @app.get("/anonymous")
    def anonymous() -> dict[str, str]:
        return {}

    install_observability(app, settings, metrics_enabled=False)

    @app.middleware("http")  # added after install_observability, so it runs outside it
    async def set_tenant(request: Any, call_next: Any) -> Any:
        if request.url.path == "/tenant":
            set_user_context({"tenant": "acme"})
        return await call_next(request)

    @app.get("/tenant")
    def tenant() -> dict[str, str]:
        return {}

    collector = capture_logs(settings)
    client = TestClient(app)
    client.get("/me")
    client.get("/anonymous")
    client.get("/tenant")
    shutdown_logging()

    me_line, anonymous_line, tenant_line = [
        p for p in collector.payloads if p["message"] == "request.completed"
    ]
    assert me_line["user_context"] == {"user_id": "42"}
    assert "user_context" not in anonymous_line
    assert tenant_line["user_context"] == {"tenant": "acme"}


def test_unsampled_requests_write_only_warnings_unless_they_fail_or_are_slow() -> None:
    settings = ObservabilitySettings(log_sample_rate=0.0, log_slow_request_ms=50)
    app = FastAPI()
    log = logging.getLogger("tests.sampling")

    @app.get("/fast")
    async def fast() -> dict[str, str]:
        log.info("fast detail")
        log.warning("fast warning")
        return {}

    @app.get("/slow")
    async def slow() -> dict[str, str]:
        log.info("slow detail")
        await asyncio.sleep(0.06)
        return {}

    @app.get("/fail")
    def fail() -> dict[str, str]:
        log.info("fail detail")
        raise RuntimeError("boom")

    install_observability(app, settings, metrics_enabled=False)
    collector = capture_logs(settings)
    client = TestClient(app, raise_server_exceptions=False)
    for path in ("/fast", "/slow", "/fail"):
        client.get(path)
    log.info("outside a request")
    shutdown_logging()

    messages = [payload["message"] for payload in collector.payloads]
    assert "fast detail" not in messages
    assert "fast warning" in messages
    assert {"slow detail", "fail detail", "request.failed", "outside a request"} <= set(messages)
    [completed] = [p for p in collector.payloads if p["message"] == "request.completed"]
    assert completed["event"]["path"] == "/slow"


def test_sampling_follows_the_trace_id_as_opentelemetry_does() -> None:
    settings = ObservabilitySettings(log_sample_rate=0.5)
    app = FastAPI()

    @app.get("/")
    async def root() -> dict[str, str]:
        return {}

    install_observability(app, settings, metrics_enabled=False)
    collector = capture_logs(settings)
    client = TestClient(app, client=("127.0.0.1", 50000))  # trusted, so traceparent is read
    kept, dropped = "0af7651916cd43dd" + "0" * 16, "0af7651916cd43dd" + "f" * 16
    for trace_id in (kept, dropped):
        client.get("/", headers={"traceparent": f"00-{trace_id}-b7ad6b7169203331-01"})
    shutdown_logging()

    completed = [p for p in collector.payloads if p["message"] == "request.completed"]
    assert [payload["trace_id"] for payload in completed] == [kept]
    sampling = pytest.importorskip("opentelemetry.sdk.trace.sampling")
    sampler = sampling.TraceIdRatioBased(0.5)
    decisions = [
        sampler.should_sample(None, int(trace_id, 16), "GET /").decision.is_sampled()
        for trace_id in (kept, dropped)
    ]
    assert decisions == [True, False]


def _request_id_hashed_as(*, kept: bool) -> str:
    """A request ID whose hash would keep, or drop, its request at a 0.5 sample rate."""
    for number in itertools.count():
        digest = hashlib.blake2b(f"req-{number}".encode(), digest_size=8).digest()
        if (int.from_bytes(digest, "big") < 1 << 63) is kept:
            break
    return f"req-{number}"


def test_sampling_follows_a_trace_this_service_started() -> None:
    # No traceparent arrives, so the trace ID is only on the current span. Each
    # request ID is chosen so that hashing it would decide the other way.
    trace = pytest.importorskip("opentelemetry.trace")
    settings = ObservabilitySettings(log_sample_rate=0.5)
    app = FastAPI()

    @app.get("/")
    async def root() -> dict[str, str]:
        return {}

    install_observability(app, settings, metrics_enabled=False)
    kept, dropped = "0af7651916cd43dd" + "0" * 16, "0af7651916cd43dd" + "f" * 16
    trace_ids = iter((kept, dropped))

    class ServerSpan:  # where OpenTelemetry's middleware sits: outside this library's
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            context = trace.SpanContext(int(next(trace_ids), 16), 1, is_remote=False)
            with trace.use_span(trace.NonRecordingSpan(context)):
                await self.inner(scope, receive, send)

    app.add_middleware(ServerSpan)
    collector = capture_logs(settings)
    client = TestClient(app, client=("127.0.0.1", 50000))  # trusted, so X-Request-ID is read
    for hashed_as_kept in (False, True):
        client.get("/", headers={"x-request-id": _request_id_hashed_as(kept=hashed_as_kept)})
    shutdown_logging()

    completed = [p for p in collector.payloads if p["message"] == "request.completed"]
    assert [payload["trace_id"] for payload in completed] == [kept]


def test_sampling_does_not_hold_lines_written_in_a_websocket_session() -> None:
    # A session can stay open for hours; before 2.4.0 its lines were never held.
    settings = ObservabilitySettings(log_sample_rate=0.0)
    app = FastAPI()
    log = logging.getLogger("tests.chat")

    @app.websocket("/chat")
    async def chat(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.receive_text()
        log.info("chat.message")
        await websocket.close()

    install_observability(app, settings, metrics_enabled=False)
    collector = capture_logs(settings)
    with TestClient(app).websocket_connect("/chat") as ws:
        ws.send_text("hello")
    shutdown_logging()

    messages = [payload["message"] for payload in collector.payloads]
    assert [m for m in messages if m in ("chat.message", "websocket.closed")] == [
        "chat.message", "websocket.closed",
    ]


def test_runtime_log_level_keeps_holding_lower_records() -> None:
    settings = ObservabilitySettings(log_level="INFO", log_level_on_error="DEBUG")
    setup_logging(settings, logs_mode="otlp", extra_handlers=[Collector()])
    try:
        assert get_log_level() == "INFO"
        set_log_level(logging.WARNING)
        assert get_log_level() == "WARNING"
        # Still low enough to create the records that are held.
        assert logging.getLogger().level == logging.DEBUG
    finally:
        shutdown_logging()


def test_request_line_has_db_and_http_totals_and_n_plus_one_is_reported_once(
    span_exporter: Any,
) -> None:
    trace_api = pytest.importorskip("opentelemetry.trace")
    settings = ObservabilitySettings()
    app = FastAPI()
    statement = (
        "SELECT * FROM items WHERE order_id = %(order_id)s AND status = 'it''s paid' LIMIT 50"
    )

    @app.get("/orders")
    def list_orders() -> dict[str, str]:
        tracer = trace_api.get_tracer("tests")
        client = trace_api.SpanKind.CLIENT
        for _ in range(12):
            with tracer.start_as_current_span(
                "SELECT",
                kind=client,
                attributes={"db.system": "postgresql", "db.statement": statement},
            ):
                pass
        with tracer.start_as_current_span(
            "GET", kind=client, attributes={"http.request.method": "GET"}
        ):
            pass
        return {}

    install_observability(
        app, settings, metrics_enabled=False, otel_settings=OTelSettings(enabled=True)
    )
    collector = capture_logs(settings)
    client = TestClient(app)
    client.get("/orders")
    client.get("/orders")
    shutdown_logging()

    [first, _] = [p["event"] for p in collector.payloads if p["message"] == "request.completed"]
    assert first["db"]["count"] == 12
    assert first["http"]["count"] == 1
    assert first["db"]["ms"] >= 0
    [warning] = [p for p in collector.payloads if p["message"] == "db.n_plus_one"]
    assert warning["level"] == "WARNING"
    assert _pop_location(warning["event"])[0] == "list_orders"
    assert warning["event"] == {
        "method": "GET",
        "route": "/orders",
        "statement_fingerprint": "3c57e6bd96ffab1a306d453be8db6bce9dfb7418e861eace63b49b08e45d43be",
        "count": 12,
    }


def test_log_bytes_are_counted_once_per_line_by_route_and_exported() -> None:
    prometheus_client = pytest.importorskip("prometheus_client")
    settings = ObservabilitySettings()
    app = FastAPI()
    route = "/bytes/{item_id}"

    @app.get(route)
    async def get_item(item_id: str) -> dict[str, str]:
        logging.getLogger("tests.bytes").info("item read")
        return {}

    install_observability(app, settings, metrics_enabled=True)
    before = get_log_bytes_by_route().get(route, 0)
    collector = capture_logs(settings, Collector())  # two sinks, one count
    request_id = TestClient(app).get("/bytes/42").headers["x-request-id"]
    shutdown_logging()

    request_lines = [
        line for line in collector.lines if json.loads(line).get("request_id") == request_id
    ]
    assert len(request_lines) == 2
    assert get_log_bytes_by_route()[route] - before == sum(len(line) for line in request_lines)
    output = prometheus_client.generate_latest().decode()
    assert f'fastapiobserver_log_bytes_total{{route="{route}"}}' in output


def _pop_location(event: dict[str, Any]) -> tuple[str, str]:
    """Remove the code location; return its function and the source line it names."""
    path, line = event.pop("code.file.path"), event.pop("code.line.number")
    function = event.pop("code.function.name").rsplit(".", 1)[-1]
    return function, linecache.getline(path, line).strip()


def _n_plus_one_app(engine: Any, *, async_engine: bool = False) -> FastAPI:
    from sqlalchemy import text

    app = FastAPI()
    if async_engine:

        @app.get("/orders")
        async def list_orders_async() -> dict[str, str]:
            async with engine.connect() as conn:
                for order_id in range(12):
                    await conn.execute(text("SELECT :order_id"), {"order_id": order_id})
            return {}

    else:

        @app.get("/orders")
        def list_orders() -> dict[str, str]:
            with engine.connect() as conn:
                for order_id in range(12):
                    conn.execute(text("SELECT :order_id"), {"order_id": order_id})
            return {}

    return app


def _request_db_totals_and_warnings(
    app: FastAPI, settings: ObservabilitySettings, **install: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    install_observability(app, settings, metrics_enabled=False, **install)
    collector = capture_logs(settings)
    client = TestClient(app)
    client.get("/orders")
    client.get("/orders")
    shutdown_logging()
    [first, _] = [p["event"] for p in collector.payloads if p["message"] == "request.completed"]
    warnings = [p["event"] for p in collector.payloads if p["message"] == "db.n_plus_one"]
    return first, warnings


def _counter(name: str, **labels: str) -> float:
    prometheus_client = pytest.importorskip("prometheus_client")
    return prometheus_client.REGISTRY.get_sample_value(name, labels) or 0.0


def test_slowest_query_is_reported_once_with_the_line_that_ran_it() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    prometheus_client = pytest.importorskip("prometheus_client")
    from hashlib import sha256

    engine = sqlalchemy.create_engine("sqlite://")
    sqlalchemy.event.listen(engine, "connect", lambda dbapi_conn, _: dbapi_conn.create_function(
        "sleep_ms", 1, lambda ms: time.sleep(ms / 1000) or 0))
    slow = "SELECT sleep_ms(20)"
    app = FastAPI()

    @app.get("/report")
    def report() -> dict[str, str]:
        with engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
            conn.execute(sqlalchemy.text(slow))
        return {}

    settings = ObservabilitySettings(slow_query_ms=10)
    install_observability(app, settings, metrics_enabled=True, db_engine=engine)
    before = _counter("fastapiobserver_db_slow_query_total", route="/report")
    collector = capture_logs(settings)
    client = TestClient(app)
    client.get("/report")
    client.get("/report")
    shutdown_logging()

    lines = [p["event"] for p in collector.payloads if p["message"] == "request.completed"]
    assert all("db.slow_query" in line["diagnostics"] for line in lines)
    [warning] = [p["event"] for p in collector.payloads if p["message"] == "db.slow_query"]
    assert _pop_location(warning) == ("report", "conn.execute(sqlalchemy.text(slow))")
    assert warning.pop("duration_ms") >= 20
    assert warning == {
        "method": "GET",
        "route": "/report",
        "statement_fingerprint": sha256(slow.encode()).hexdigest(),
    }
    output = prometheus_client.generate_latest().decode()
    assert f'fastapiobserver_db_slow_query_total{{route="/report"}} {before + 2}' in output


def test_slow_query_threshold_follows_obs_slow_query_ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hashlib import sha256

    sqlalchemy = pytest.importorskip("sqlalchemy")
    engine = sqlalchemy.create_engine("sqlite://")

    def _register_sqlite_helpers(dbapi_conn: Any, _: Any) -> None:
        def fail_after_ms(ms: float) -> int:
            time.sleep(ms / 1000)
            raise RuntimeError("boom")

        dbapi_conn.create_function("sleep_ms", 1, lambda ms: time.sleep(ms / 1000) or 0)
        dbapi_conn.create_function("fail_after_ms", 1, fail_after_ms)

    sqlalchemy.event.listen(engine, "connect", _register_sqlite_helpers)
    slowest_sql = "SELECT sleep_ms(40)"

    try:
        for threshold_ms, expect_slow in (("500", False), ("10", True)):
            monkeypatch.setenv("OBS_SLOW_QUERY_MS", threshold_ms)
            settings = ObservabilitySettings.from_env()
            app = FastAPI()

            @app.get("/slow-threshold")
            def report() -> dict[str, str]:
                with engine.connect() as conn:
                    conn.execute(sqlalchemy.text("SELECT sleep_ms(15)"))
                    conn.execute(sqlalchemy.text(slowest_sql))
                return {}

            @app.get("/slow-failing")
            def failing() -> dict[str, str]:
                with engine.connect() as conn:
                    try:
                        conn.execute(sqlalchemy.text("SELECT fail_after_ms(25)"))
                    except sqlalchemy.exc.OperationalError:
                        pass
                return {}

            install_observability(app, settings, metrics_enabled=False, db_engine=engine)
            collector = capture_logs(settings)
            client = TestClient(app)
            client.get("/slow-threshold")
            client.get("/slow-failing")
            shutdown_logging()

            completed = [
                p["event"] for p in collector.payloads if p["message"] == "request.completed"
            ]
            warnings = [p["event"] for p in collector.payloads if p["message"] == "db.slow_query"]
            if expect_slow:
                assert completed[0]["diagnostics"] == ["db.slow_query"]
                assert len(warnings) == 1
                assert warnings[0]["statement_fingerprint"] == sha256(
                    slowest_sql.encode()
                ).hexdigest()
                assert warnings[0]["duration_ms"] >= 40
                assert "diagnostics" not in completed[1]
            else:
                assert all("diagnostics" not in item for item in completed)
                assert not warnings
    finally:
        engine.dispose()


def test_a_long_garbage_collection_pause_is_shown_on_the_request_it_stalled() -> None:
    from fastapiobserver import gc_pauses

    app = FastAPI()

    @app.get("/stalled")
    def stalled() -> dict[str, str]:
        gc_pauses._on_gc("start", {})  # a collection as long as the threshold, simulated
        time.sleep(gc_pauses.GC_PAUSE_SECONDS + 0.02)
        gc_pauses._on_gc("stop", {})
        return {}

    @app.get("/fine")
    def fine() -> dict[str, str]:
        return {}

    settings = ObservabilitySettings()
    install_observability(app, settings, metrics_enabled=True)
    before = _counter("fastapiobserver_gc_pause_total")
    collector = capture_logs(settings)
    client = TestClient(app)
    client.get("/stalled")
    client.get("/fine")
    shutdown_logging()

    stalled_line, fine_line = [
        p["event"] for p in collector.payloads if p["message"] == "request.completed"
    ]
    assert stalled_line["diagnostics"] == ["gc.pause"]
    assert stalled_line["gc_pause_ms"] >= gc_pauses.GC_PAUSE_SECONDS * 1000
    assert "gc_pause_ms" not in fine_line and "diagnostics" not in fine_line
    assert _counter("fastapiobserver_gc_pause_total") - before == 1


def test_watch_gc_times_real_collections_once() -> None:
    import gc

    from fastapiobserver import gc_pauses

    try:
        gc_pauses.watch_gc()
        gc_pauses.watch_gc()
        before = gc_pauses.gc_seconds()
        gc.collect()
        assert gc.callbacks.count(gc_pauses._on_gc) == 1
        assert gc_pauses.gc_seconds() > before
    finally:
        gc.callbacks.remove(gc_pauses._on_gc)


def test_a_connection_held_idle_is_reported_by_route() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from sqlalchemy.orm import Session

    engine = sqlalchemy.create_engine("sqlite://")
    app = FastAPI()

    def get_session() -> Any:
        with Session(engine) as session:
            yield session

    @app.get("/report")
    def report(session: Session = Depends(get_session)) -> dict[str, str]:
        session.execute(sqlalchemy.text("SELECT 1"))
        time.sleep(0.12)  # slow work while the session keeps its connection
        return {}

    @app.get("/quick")
    def quick(session: Session = Depends(get_session)) -> dict[str, str]:
        session.execute(sqlalchemy.text("SELECT 1"))
        return {}

    settings = ObservabilitySettings()
    install_observability(app, settings, metrics_enabled=True, db_engine=engine)
    before = _counter("fastapiobserver_db_connection_held_idle_total", route="/report")
    collector = capture_logs(settings)
    client = TestClient(app)
    client.get("/report")
    client.get("/report")
    client.get("/quick")
    shutdown_logging()

    lines = [p["event"] for p in collector.payloads if p["message"] == "request.completed"]
    assert [line.get("diagnostics") for line in lines] == [
        ["db.connection_held_idle"], ["db.connection_held_idle"], None,
    ]
    warnings = [p for p in collector.payloads if p["message"] == "db.connection_held_idle"]
    [warning] = [p["event"] for p in warnings]
    assert warning["route"] == "/report" and warning["conn_held_ms"] >= 120
    assert warning["db_ms"] * 10 <= warning["conn_held_ms"]
    assert _counter("fastapiobserver_db_connection_held_idle_total", route="/report") - before == 2


def test_sqlalchemy_queries_are_counted_and_n_plus_one_reported_without_tracing() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    engine = sqlalchemy.create_engine("sqlite://")
    settings = ObservabilitySettings()

    first, warnings = _request_db_totals_and_warnings(
        _n_plus_one_app(engine), settings, db_engine=engine
    )

    assert first["db"]["count"] == 12
    assert first["db"]["ms"] > 0
    assert _pop_location(warnings[0]) == (
        "list_orders", 'conn.execute(text("SELECT :order_id"), {"order_id": order_id})'
    )
    assert warnings == [{
        "method": "GET", "route": "/orders", "count": 12,
        "statement_fingerprint": "66cbb3a40d4bbd150b75825ad291a6545399f3098fc1079e4d8b5bb061a6a481",
    }]


def test_requests_with_an_n_plus_one_are_counted_by_route() -> None:
    prometheus_client = pytest.importorskip("prometheus_client")
    sqlalchemy = pytest.importorskip("sqlalchemy")
    engine = sqlalchemy.create_engine("sqlite://")
    app = _n_plus_one_app(engine)
    install_observability(app, ObservabilitySettings(), metrics_enabled=True, db_engine=engine)

    def counted() -> float:
        return prometheus_client.REGISTRY.get_sample_value(
            "fastapiobserver_db_n_plus_one_total", {"route": "/orders"}
        ) or 0.0

    before = counted()
    client = TestClient(app)
    client.get("/orders")
    client.get("/orders")
    shutdown_logging()

    # The warning is written once per statement; the counter has each request.
    assert counted() - before == 2


def test_async_sqlalchemy_queries_are_counted_without_tracing() -> None:
    pytest.importorskip("aiosqlite")
    asyncio_ext = pytest.importorskip("sqlalchemy.ext.asyncio")
    from sqlalchemy.pool import NullPool

    engine = asyncio_ext.create_async_engine("sqlite+aiosqlite://", poolclass=NullPool)
    settings = ObservabilitySettings()

    first, warnings = _request_db_totals_and_warnings(
        _n_plus_one_app(engine, async_engine=True), settings, db_engine=engine
    )

    assert first["db"]["count"] == 12
    assert [w["count"] for w in warnings] == [12]
    # The driver runs in a greenlet; the location is the awaiting application line.
    assert _pop_location(warnings[0]) == (
        "list_orders_async",
        'await conn.execute(text("SELECT :order_id"), {"order_id": order_id})',
    )


def test_n_plus_one_from_response_serialization_names_the_relationship_and_no_wrapper() -> None:
    """Lazy loads run from FastAPI's serializer, with no application code inside the
    route: the warning names the relationship. An application middleware around the
    request is not where they ran."""
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from pydantic import BaseModel, ConfigDict
    from sqlalchemy import orm
    from sqlalchemy.pool import StaticPool

    Base = orm.declarative_base()

    class Order(Base):
        __tablename__ = "orders"
        id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        items = orm.relationship("Item")

    class Item(Base):
        __tablename__ = "items"
        id = sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        order_id = sqlalchemy.Column(sqlalchemy.ForeignKey("orders.id"))

    class ItemOut(BaseModel):
        model_config = ConfigDict(from_attributes=True)
        id: int

    class OrderOut(BaseModel):
        model_config = ConfigDict(from_attributes=True)
        items: list[ItemOut]

    engine = sqlalchemy.create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with orm.Session(engine) as session:
        session.add_all([Order(items=[Item()]) for _ in range(12)])
        session.commit()

    class Wrapper:
        def __init__(self, app: Any) -> None:
            self.app = app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            await self.app(scope, receive, send)

    app = FastAPI()
    app.add_middleware(Wrapper)

    def get_session() -> Any:
        with orm.Session(engine) as session:
            yield session  # Open until the response is sent, as lazy loads need.

    @app.get("/orders", response_model=list[OrderOut])
    async def list_orders(session: Any = Depends(get_session)) -> Any:
        return session.scalars(sqlalchemy.select(Order)).all()

    first, warnings = _request_db_totals_and_warnings(
        app, ObservabilitySettings(), db_engine=engine
    )

    assert first["db"]["count"] == 13
    assert warnings == [{
        "method": "GET", "route": "/orders", "count": 12, "relationship": "Order.items",
        "statement_fingerprint": warnings[0]["statement_fingerprint"],
    }]


def test_sqlalchemy_queries_count_once_with_tracing_on(span_exporter: Any) -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    trace_api = pytest.importorskip("opentelemetry.trace")
    from fastapiobserver.db_queries import SQLALCHEMY_INSTRUMENTATION, count_queries

    engine = sqlalchemy.create_engine("sqlite://")
    count_queries(engine)
    settings = ObservabilitySettings()
    app = FastAPI()

    @app.get("/orders")
    def list_orders() -> dict[str, str]:
        # Each query as SQLAlchemy's instrumentation would trace it, where it can.
        tracer = trace_api.get_tracer(SQLALCHEMY_INSTRUMENTATION)
        with engine.connect() as conn:
            for order_id in range(12):
                with tracer.start_as_current_span(
                    "SELECT",
                    kind=trace_api.SpanKind.CLIENT,
                    attributes={"db.system": "sqlite", "db.statement": "SELECT ?"},
                ):
                    conn.execute(sqlalchemy.text("SELECT :order_id"), {"order_id": order_id})
        return {}

    first, _ = _request_db_totals_and_warnings(
        app, settings, otel_settings=OTelSettings(enabled=True)
    )

    assert first["db"]["count"] == 12
