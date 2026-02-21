"""SQLAlchemy tracing with SQLCommenter support.

Instruments SQLAlchemy engines so that every SQL query gets:

1. An OTel span for latency tracking.
2. A SQL comment containing ``traceparent``, enabling DBAs to correlate
   slow Postgres/MySQL queries back to the originating HTTP request.

Usage::

    from fastapiobserver.db_tracing import instrument_sqlalchemy

    engine = create_engine("postgresql://...")
    instrument_sqlalchemy(engine)
    # SELECT * FROM users /*traceparent='00-abc123...',route='/api/users'*/

Or for async engines::

    from fastapiobserver.db_tracing import instrument_sqlalchemy_async

    async_engine = create_async_engine("postgresql+asyncpg://...")
    instrument_sqlalchemy_async(async_engine)
"""

from __future__ import annotations

import logging
from typing import Any

from .utils import lazy_import

_LOGGER = logging.getLogger("fastapiobserver.db_tracing")

_DEFAULT_COMMENTER_OPTIONS: dict[str, bool] = {
    "opentelemetry_values": True,  # injects traceparent
    "db_driver": True,
    "db_framework": False,  # noisy — omit by default
    "route": True,
}

_PACKAGE_HINT = "fastapi-observer[otel-sqlalchemy]"


def instrument_sqlalchemy(
    engine: Any,
    *,
    enable_commenter: bool = True,
    commenter_options: dict[str, bool] | None = None,
) -> None:
    """Instrument a SQLAlchemy ``Engine`` for tracing and SQLCommenter.

    Parameters
    ----------
    engine:
        A synchronous ``sqlalchemy.engine.Engine`` instance.
    enable_commenter:
        When ``True`` (default), appends a SQL comment with trace
        context to every query.
    commenter_options:
        Key-value pairs controlling which fields appear in the SQL
        comment.  Defaults to ``traceparent``, ``db_driver``, and
        ``route``.
    """
    try:
        instrumentor_module = lazy_import(
            "opentelemetry.instrumentation.sqlalchemy",
            package_hint=_PACKAGE_HINT,
        )
        instrumentor = instrumentor_module.SQLAlchemyInstrumentor()

        options = commenter_options or _DEFAULT_COMMENTER_OPTIONS

        instrumentor.instrument(
            engine=engine,
            enable_commenter=enable_commenter,
            commenter_options=options,
        )
        _LOGGER.info(
            "db_tracing.instrumented",
            extra={
                "event": {
                    "engine_url": str(engine.url),
                    "commenter_enabled": enable_commenter,
                },
                "_skip_enrichers": True,
            },
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "SQLAlchemy instrumentation requires "
            f"`pip install {_PACKAGE_HINT}`"
        ) from exc


def instrument_sqlalchemy_async(
    async_engine: Any,
    *,
    enable_commenter: bool = True,
    commenter_options: dict[str, bool] | None = None,
) -> None:
    """Instrument an async SQLAlchemy ``AsyncEngine``.

    Extracts the underlying ``sync_engine`` and delegates to
    :func:`instrument_sqlalchemy`.

    Parameters
    ----------
    async_engine:
        A ``sqlalchemy.ext.asyncio.AsyncEngine`` instance.
    enable_commenter:
        See :func:`instrument_sqlalchemy`.
    commenter_options:
        See :func:`instrument_sqlalchemy`.
    """
    sync_engine = getattr(async_engine, "sync_engine", None)
    if sync_engine is None:
        raise TypeError(
            "Expected an AsyncEngine with a `sync_engine` attribute, "
            f"got {type(async_engine).__name__}"
        )
    instrument_sqlalchemy(
        sync_engine,
        enable_commenter=enable_commenter,
        commenter_options=commenter_options,
    )


def uninstrument_sqlalchemy() -> None:
    """Reverse global SQLAlchemy instrumentation."""
    try:
        instrumentor_module = lazy_import(
            "opentelemetry.instrumentation.sqlalchemy",
        )
        instrumentor_module.SQLAlchemyInstrumentor().uninstrument()
    except (ModuleNotFoundError, RuntimeError):
        pass


__all__ = [
    "instrument_sqlalchemy",
    "instrument_sqlalchemy_async",
    "uninstrument_sqlalchemy",
]
