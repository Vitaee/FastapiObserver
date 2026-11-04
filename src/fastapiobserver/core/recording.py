"""Shared completed-call admission and process-local ownership handles."""

from __future__ import annotations

import math
import os
import re
import sys
import threading
import time
from typing import TYPE_CHECKING, Literal
from weakref import WeakValueDictionary, ref

if TYPE_CHECKING:
    from ..request_context import RequestState

CallKind = Literal["db", "http"]
SpanKey = tuple[int, int]
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SOURCES = frozenset({"manual", "sqlalchemy", "httpx", "httpx2", "requests", "sampled_span"})


def validate_call(kind: str, seconds: float, error: str | None = None) -> None:
    if kind not in ("db", "http"):
        raise ValueError("Call kind must be 'db' or 'http'")
    if type(seconds) not in (int, float):
        raise TypeError("Call duration must be a number of seconds")
    if seconds < 0 or seconds > sys.float_info.max / 1000 or not math.isfinite(seconds):
        raise ValueError("Call duration must be finite and nonnegative")
    if error is not None and (not isinstance(error, str) or not _ERROR_CODE.fullmatch(error)):
        raise ValueError("Error category must be 1–64 lowercase letters, digits or underscores")


def record_completed_call(
    owner: RequestState | None, kind: CallKind, seconds: float,
    statement: str | None = None, *, error: str | None = None,
    source: str = "manual", started: bool = False,
) -> bool:
    """Reject malformed adapter input without changing application outcomes."""
    if owner is None or not isinstance(source, str) or source not in _SOURCES:
        return False
    if statement is not None and not isinstance(statement, str):
        return False
    try:
        validate_call(kind, seconds, error)
    except (TypeError, ValueError, OverflowError):
        return False
    return owner._record_call(kind, seconds, statement, error=error, source=source, started=started)


class CallOwner:
    """A state-owned lifetime token, borrowed weakly by calls and span tracking."""

    __slots__ = ("_state", "_pid", "__weakref__")

    def __init__(self, owner: RequestState) -> None:
        self._state = ref(owner)
        self._pid = os.getpid()

    def get(self) -> RequestState | None:
        if os.getpid() != self._pid:
            return None
        owner = self._state()
        return owner if owner is not None and not owner.closed else None


class CallHandle:
    """One completion, attached to its start owner without retaining its payloads."""

    def __init__(self, owner: RequestState, kind: CallKind, *, source: str = "manual") -> None:
        validate_call(kind, 0.0)
        if source not in _SOURCES:
            raise ValueError("Unsupported observation source")
        token = owner._call_owner(source, kind)
        self._owner = ref(token) if token is not None else None
        self._kind = kind
        self._source = source
        self._started = time.perf_counter()
        self._lock = threading.Lock()
        self._finished = False

    def finish(self, *, error: str | None = None, statement: str | None = None) -> bool:
        # Check before locking: an inherited lock may belong to a vanished
        # parent thread. A parent-process call cannot complete in the child.
        token = self._owner() if self._owner is not None else None
        owner = token.get() if token is not None else None
        if owner is None:
            return False
        with self._lock:
            if self._finished:
                return False
            self._finished = True
        return record_completed_call(
            owner, self._kind, max(0.0, time.perf_counter() - self._started),
            statement, error=error, source=self._source, started=True,
        )


class SpanOwners:
    """Bounded weak ownership for sampled spans; missing ownership stays unknown."""

    def __init__(self, limit: int = 4096) -> None:
        self.limit = limit
        self.dropped = 0
        self._owners: WeakValueDictionary[SpanKey, CallOwner] = WeakValueDictionary()
        self._lock = threading.Lock()
        self._pid = os.getpid()

    def _after_fork(self) -> None:
        if os.getpid() != self._pid:
            self._lock = threading.Lock()
            self._owners = WeakValueDictionary()
            self.dropped = 0
            self._pid = os.getpid()

    def capture(self, key: SpanKey, owner: RequestState) -> bool:
        self._after_fork()
        token = owner._call_owner()
        if token is None:
            return False
        with self._lock:
            if key not in self._owners and len(self._owners) >= self.limit:
                self.dropped += 1
                return False
            self._owners[key] = token
            return True

    def pop(self, key: SpanKey) -> RequestState | None:
        self._after_fork()
        with self._lock:
            token = self._owners.pop(key, None)
        return token.get() if token is not None else None

    def __len__(self) -> int:
        self._after_fork()
        with self._lock:
            return len(self._owners)
