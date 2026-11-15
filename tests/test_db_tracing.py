from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import fastapiobserver.db_tracing as db_tracing
from fastapiobserver.db_tracing import instrument_sqlalchemy, instrument_sqlalchemy_async


@pytest.fixture(autouse=True)
def _fresh_engine_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_tracing, "_ENGINES", [])


def _engine() -> MagicMock:
    engine = MagicMock(url="postgresql://localhost/test")
    del engine.sync_engine
    return engine


@patch("fastapiobserver.db_tracing.lazy_import")
def test_commenter_adds_the_trace_context_by_default(mock_lazy_import: MagicMock) -> None:
    instrumentor = mock_lazy_import.return_value.SQLAlchemyInstrumentor.return_value
    engine = _engine()

    instrument_sqlalchemy(engine)

    kwargs = instrumentor.instrument.call_args.kwargs
    assert kwargs["engines"] == [engine]
    assert kwargs["enable_commenter"] is True
    assert kwargs["commenter_options"]["opentelemetry_values"] is True


@patch("fastapiobserver.db_tracing.lazy_import")
def test_warns_when_opentelemetry_refuses_to_instrument(
    mock_lazy_import: MagicMock, caplog: pytest.LogCaptureFixture,
) -> None:
    # e.g. an SQLAlchemy version outside the instrumentation's supported range
    instrumentor = mock_lazy_import.return_value.SQLAlchemyInstrumentor.return_value
    instrumentor.is_instrumented_by_opentelemetry = False

    with caplog.at_level("INFO", logger="fastapiobserver.db_tracing"):
        instrument_sqlalchemy(_engine())

    messages = [record.getMessage() for record in caplog.records]
    assert "db_tracing.not_instrumented" in messages
    assert "db_tracing.instrumented" not in messages


def test_non_async_engine_raises_type_error() -> None:
    with pytest.raises(TypeError, match="sync_engine"):
        instrument_sqlalchemy_async(MagicMock(spec=[]))


@patch(
    "fastapiobserver.db_tracing.lazy_import",
    side_effect=RuntimeError("Missing optional dependency"),
)
def test_missing_dependency_raises_clear_error(mock_lazy_import: MagicMock) -> None:
    with pytest.raises(RuntimeError, match="otel-sqlalchemy"):
        instrument_sqlalchemy(_engine())
