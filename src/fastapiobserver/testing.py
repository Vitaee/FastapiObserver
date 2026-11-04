"""Small native-SQL assertions, independent of pytest, logging and trace sampling."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import Token
import os
import threading
from types import TracebackType
from typing import Any
from weakref import ref

from .db_queries import _QUERY_OBSERVERS, _has_query_hooks

__all__ = ["assert_query_budget"]


class QueryBudget:
    """Single-use, engine-specific cursor-entry budget; nested scopes are included."""

    def __init__(self, engine: Any, max_queries: int) -> None:
        if type(max_queries) is not int:
            raise TypeError("max_queries must be an integer")
        if max_queries < 0:
            raise ValueError("max_queries must be nonnegative")
        from sqlalchemy.engine import Engine
        engine = getattr(engine, "sync_engine", engine)
        if not isinstance(engine, Engine):
            raise TypeError("A SQLAlchemy Engine or AsyncEngine is required")
        self._engine = ref(engine)
        self._maximum = max_queries
        self._count = 0
        self._closed = True
        self._used = False
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._token: Token[tuple[Callable[[Any], None], ...]] | None = None

    def __enter__(self) -> QueryBudget:
        if self._used or self._pid != os.getpid():
            raise RuntimeError("A query budget is single-use and process-local")
        engine = self._engine()
        if engine is None or not _has_query_hooks(engine):
            raise RuntimeError("Attach native counting with count_queries(engine) first")
        self._used, self._closed = True, False
        self._token = _QUERY_OBSERVERS.set((*_QUERY_OBSERVERS.get(), self._record))
        return self

    def _record(self, engine: Any) -> None:
        if self._pid != os.getpid() or engine is not self._engine():
            return
        with self._lock:
            if not self._closed:
                self._count += 1

    @property
    def count(self) -> int:
        if self._pid != os.getpid():
            raise RuntimeError("A query budget cannot be checked across a process fork")
        with self._lock:
            return self._count

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._token is None:
            return
        _QUERY_OBSERVERS.reset(self._token)
        self._token = None
        if self._pid != os.getpid():
            self._closed = True
            if exc_type is None:
                raise RuntimeError("A query budget cannot be checked across a process fork")
            return
        with self._lock:
            self._closed = True
            count = self._count
        engine = self._engine()
        if exc_type is None and (engine is None or not _has_query_hooks(engine)):
            raise RuntimeError("Native query counting changed during the budget scope")
        if exc_type is None and count > self._maximum:
            raise AssertionError(
                f"Expected at most {self._maximum} SQLAlchemy cursor-execution attempts; "
                f"observed {count}"
            )


def assert_query_budget(engine: Any, max_queries: int) -> QueryBudget:
    """Assert a maximum number of native cursor-execution attempts on one engine.

    Requires existing native counting through ``count_queries(engine)`` or the
    FastAPI installer. Counts failed attempts and queries in nested operations;
    excludes connection acquisition, compilation and fetch/transaction hooks.
    Join work before leaving; new threads require an explicit copied context.
    Application exceptions propagate unchanged, even when over budget.
    """
    return QueryBudget(engine, max_queries)
