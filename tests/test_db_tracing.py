"""Tests for SQLAlchemy tracing + SQLCommenter integration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import fastapiobserver.db_tracing as db_tracing
from fastapiobserver.db_tracing import (
    _DEFAULT_COMMENTER_OPTIONS,
    instrument_sqlalchemy,
    instrument_sqlalchemy_async,
    uninstrument_sqlalchemy,
)


@pytest.fixture(autouse=True)
def _fresh_engine_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_tracing, "_ENGINES", [])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_engine(url: str = "postgresql://localhost/test") -> MagicMock:
    engine = MagicMock()
    engine.url = url
    # ensure it does NOT have sync_engine → treated as sync engine
    del engine.sync_engine
    return engine


def _make_mock_async_engine(
    url: str = "postgresql+asyncpg://localhost/test",
) -> MagicMock:
    async_engine = MagicMock()
    async_engine.sync_engine = _make_mock_engine(url)
    return async_engine


# ---------------------------------------------------------------------------
# Core instrumentation
# ---------------------------------------------------------------------------


class TestInstrumentSQLAlchemy:
    @patch("fastapiobserver.db_tracing.lazy_import")
    def test_calls_instrumentor_with_defaults(
        self, mock_lazy_import: MagicMock,
    ) -> None:
        mock_module = MagicMock()
        mock_lazy_import.return_value = mock_module
        engine = _make_mock_engine()

        instrument_sqlalchemy(engine)

        mock_module.SQLAlchemyInstrumentor().instrument.assert_called_once_with(
            engines=[engine],
            enable_commenter=True,
            commenter_options=_DEFAULT_COMMENTER_OPTIONS,
        )

    @patch("fastapiobserver.db_tracing.lazy_import")
    def test_custom_commenter_options(
        self, mock_lazy_import: MagicMock,
    ) -> None:
        mock_module = MagicMock()
        mock_lazy_import.return_value = mock_module
        engine = _make_mock_engine()
        custom_opts = {"opentelemetry_values": True, "db_driver": False}

        instrument_sqlalchemy(engine, commenter_options=custom_opts)

        mock_module.SQLAlchemyInstrumentor().instrument.assert_called_once_with(
            engines=[engine],
            enable_commenter=True,
            commenter_options=custom_opts,
        )

    @patch("fastapiobserver.db_tracing.lazy_import")
    def test_commenter_can_be_disabled(
        self, mock_lazy_import: MagicMock,
    ) -> None:
        mock_module = MagicMock()
        mock_lazy_import.return_value = mock_module
        engine = _make_mock_engine()

        instrument_sqlalchemy(engine, enable_commenter=False)

        mock_module.SQLAlchemyInstrumentor().instrument.assert_called_once_with(
            engines=[engine],
            enable_commenter=False,
            commenter_options=_DEFAULT_COMMENTER_OPTIONS,
        )

    @patch("fastapiobserver.db_tracing.lazy_import")
    def test_second_engine_reinstruments_both(
        self, mock_lazy_import: MagicMock,
    ) -> None:
        # The OTel instrumentor is a singleton that ignores a second instrument()
        # call, so the second engine must be instrumented together with the first.
        instrumentor = mock_lazy_import.return_value.SQLAlchemyInstrumentor.return_value
        orders, users = _make_mock_engine("sqlite://orders"), _make_mock_engine("sqlite://users")

        instrument_sqlalchemy(orders)
        instrument_sqlalchemy(users)

        instrumentor.uninstrument.assert_called()
        assert instrumentor.instrument.call_args.kwargs["engines"] == [orders, users]

    @patch("fastapiobserver.db_tracing.lazy_import")
    def test_warns_when_opentelemetry_refuses_to_instrument(
        self, mock_lazy_import: MagicMock, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # e.g. an SQLAlchemy version outside the instrumentation's supported range
        instrumentor = mock_lazy_import.return_value.SQLAlchemyInstrumentor.return_value
        instrumentor.is_instrumented_by_opentelemetry = False

        with caplog.at_level("INFO", logger="fastapiobserver.db_tracing"):
            instrument_sqlalchemy(_make_mock_engine())

        messages = [record.getMessage() for record in caplog.records]
        assert "db_tracing.not_instrumented" in messages
        assert "db_tracing.instrumented" not in messages


# ---------------------------------------------------------------------------
# Async engine
# ---------------------------------------------------------------------------


class TestInstrumentAsyncEngine:
    @patch("fastapiobserver.db_tracing.lazy_import")
    def test_extracts_sync_engine(
        self, mock_lazy_import: MagicMock,
    ) -> None:
        mock_module = MagicMock()
        mock_lazy_import.return_value = mock_module
        async_engine = _make_mock_async_engine()

        instrument_sqlalchemy_async(async_engine)

        mock_module.SQLAlchemyInstrumentor().instrument.assert_called_once_with(
            engines=[async_engine.sync_engine],
            enable_commenter=True,
            commenter_options=_DEFAULT_COMMENTER_OPTIONS,
        )

    def test_non_async_engine_raises_type_error(self) -> None:
        bogus = MagicMock(spec=[])  # no sync_engine attribute
        with pytest.raises(TypeError, match="sync_engine"):
            instrument_sqlalchemy_async(bogus)


# ---------------------------------------------------------------------------
# Uninstrument
# ---------------------------------------------------------------------------


class TestUninstrument:
    @patch("fastapiobserver.db_tracing.lazy_import")
    def test_uninstrument_calls_teardown(
        self, mock_lazy_import: MagicMock,
    ) -> None:
        mock_module = MagicMock()
        mock_lazy_import.return_value = mock_module

        uninstrument_sqlalchemy()

        mock_module.SQLAlchemyInstrumentor().uninstrument.assert_called_once()


# ---------------------------------------------------------------------------
# Missing dependency
# ---------------------------------------------------------------------------


class TestMissingDependency:
    @patch(
        "fastapiobserver.db_tracing.lazy_import",
        side_effect=RuntimeError("Missing optional dependency"),
    )
    def test_missing_dependency_raises_clear_error(
        self, mock_lazy_import: MagicMock,
    ) -> None:
        engine = _make_mock_engine()
        with pytest.raises(RuntimeError, match="otel-sqlalchemy"):
            instrument_sqlalchemy(engine)


# ---------------------------------------------------------------------------
# Default commenter options
# ---------------------------------------------------------------------------


def test_default_commenter_options_include_traceparent() -> None:
    assert _DEFAULT_COMMENTER_OPTIONS["opentelemetry_values"] is True
    assert _DEFAULT_COMMENTER_OPTIONS["db_driver"] is True
    assert _DEFAULT_COMMENTER_OPTIONS["db_framework"] is False
