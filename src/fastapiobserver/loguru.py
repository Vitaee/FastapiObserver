from __future__ import annotations

import logging
from types import TracebackType
from typing import Any, Callable

from .utils import lazy_import

_LOGGER = logging.getLogger("fastapiobserver.loguru")
_BRIDGE_MARKER = "_fastapiobserver_loguru_bridge"
_STANDARD_LOG_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)


def build_loguru_to_stdlib_sink(
    *,
    stdlib_logger_name: str | None = None,
    include_extra: bool = True,
) -> Callable[[Any], None]:
    """Build a Loguru sink that forwards records into stdlib logging.

    This lets existing Loguru users keep ``logger.bind(...).info(...)`` calls
    while routing output through fastapi-observer's queue/sanitization pipeline.
    """

    def _sink(message: Any) -> None:
        record = getattr(message, "record", None)
        if not isinstance(record, dict):
            return

        raw_extra = record.get("extra")
        extra: dict[str, Any]
        if isinstance(raw_extra, dict):
            extra = dict(raw_extra)
        else:
            extra = {}

        # Guard against accidental recursive bridges in mixed logging setups.
        if bool(extra.get(_BRIDGE_MARKER)):
            return

        record_logger_name = str(record.get("name") or "")
        logger_name = _resolve_logger_name(stdlib_logger_name, record_logger_name)
        stdlib_logger = logging.getLogger(logger_name)
        stdlib_extra = _build_stdlib_extra(extra, include_extra=include_extra)
        exc_info = _extract_exc_info(record.get("exception"))
        level = _extract_level_number(record.get("level"))
        message_text = str(record.get("message") or "")

        stdlib_logger.log(
            level,
            message_text,
            exc_info=exc_info,
            extra=stdlib_extra,
        )

    return _sink


def install_loguru_bridge(
    *,
    stdlib_logger_name: str | None = None,
    level: str | int = 0,
    include_extra: bool = True,
    enqueue: bool = False,
    catch: bool = True,
) -> int:
    """Install a Loguru -> stdlib bridge and return the Loguru handler id."""
    loguru_logger = lazy_import(
        "loguru",
        "logger",
        package_hint="fastapi-observer[loguru]",
    )
    sink = build_loguru_to_stdlib_sink(
        stdlib_logger_name=stdlib_logger_name,
        include_extra=include_extra,
    )
    handler_id = int(
        loguru_logger.add(
            sink,
            level=level,
            enqueue=enqueue,
            catch=catch,
        )
    )
    _LOGGER.info(
        "loguru.bridge.installed",
        extra={
            "event": {
                "handler_id": handler_id,
                "target_logger": stdlib_logger_name or "<record.name>",
                "include_extra": include_extra,
                "enqueue": enqueue,
            },
            "_skip_enrichers": True,
        },
    )
    return handler_id


def remove_loguru_bridge(handler_id: int) -> None:
    """Remove a previously installed Loguru bridge handler."""
    loguru_logger = lazy_import(
        "loguru",
        "logger",
        package_hint="fastapi-observer[loguru]",
    )
    loguru_logger.remove(int(handler_id))


def _resolve_logger_name(configured_name: str | None, record_name: str) -> str:
    if configured_name is not None:
        candidate = configured_name.strip()
        if candidate:
            return candidate
    candidate = record_name.strip()
    if candidate:
        return candidate
    return "loguru"


def _build_stdlib_extra(
    extra: dict[str, Any],
    *,
    include_extra: bool,
) -> dict[str, Any]:
    forwarded_extra: dict[str, Any] = {_BRIDGE_MARKER: True}
    if not include_extra:
        return forwarded_extra

    for key, value in extra.items():
        normalized_key = str(key)
        if normalized_key == _BRIDGE_MARKER:
            continue
        if normalized_key in _STANDARD_LOG_RECORD_ATTRS:
            continue
        forwarded_extra[normalized_key] = value
    return forwarded_extra


def _extract_level_number(level: Any) -> int:
    level_number = getattr(level, "no", None)
    if isinstance(level_number, int):
        return level_number
    return logging.INFO


def _extract_exc_info(
    exception: Any,
) -> tuple[type[BaseException], BaseException, TracebackType | None] | None:
    if exception is None:
        return None

    exc_type = getattr(exception, "type", None)
    exc_value = getattr(exception, "value", None)
    exc_traceback = getattr(exception, "traceback", None)
    if not isinstance(exc_type, type):
        return None
    if not issubclass(exc_type, BaseException):
        return None
    if not isinstance(exc_value, BaseException):
        return None
    if exc_traceback is not None and not isinstance(exc_traceback, TracebackType):
        return None
    return exc_type, exc_value, exc_traceback


__all__ = [
    "build_loguru_to_stdlib_sink",
    "install_loguru_bridge",
    "remove_loguru_bridge",
]
