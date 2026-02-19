"""Lifecycle helpers for OTel provider flush/shutdown hooks."""

from __future__ import annotations

import atexit
import logging
import threading
from typing import Any, Callable

from fastapi import FastAPI

_LOGGER = logging.getLogger("fastapiobserver.otel")
_ATEXIT_LOCK = threading.Lock()
_ATEXIT_KEYS: set[str] = set()
_APP_HOOK_KEYS_ATTR = "_fastapiobserver_otel_shutdown_hook_keys"


def register_shutdown_hook(
    key: str,
    callback: Callable[[], None],
    *,
    app: FastAPI | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Register an idempotent shutdown hook on app shutdown and atexit."""
    normalized_key = key.strip()
    if not normalized_key:
        raise ValueError("Shutdown hook key cannot be empty")

    log = logger or _LOGGER
    run_once = _build_run_once(callback, log, normalized_key)

    with _ATEXIT_LOCK:
        if normalized_key not in _ATEXIT_KEYS:
            atexit.register(run_once)
            _ATEXIT_KEYS.add(normalized_key)

    if app is None:
        return

    app_hook_keys = getattr(app.state, _APP_HOOK_KEYS_ATTR, None)
    if not isinstance(app_hook_keys, set):
        app_hook_keys = set()
        setattr(app.state, _APP_HOOK_KEYS_ATTR, app_hook_keys)
    if normalized_key in app_hook_keys:
        return

    app.add_event_handler("shutdown", run_once)
    app_hook_keys.add(normalized_key)


def build_provider_shutdown_callback(
    provider: Any,
    *,
    logger: logging.Logger | None = None,
    component: str,
    shutdown: bool,
) -> Callable[[], None]:
    """Build callback that flushes provider and optionally shuts it down."""

    log = logger or _LOGGER
    normalized_component = component.strip() or "provider"

    def _callback() -> None:
        _invoke_provider_method(
            provider,
            method_name="force_flush",
            logger=log,
            component=normalized_component,
        )
        if shutdown:
            _invoke_provider_method(
                provider,
                method_name="shutdown",
                logger=log,
                component=normalized_component,
            )

    return _callback


def _build_run_once(
    callback: Callable[[], None],
    logger: logging.Logger,
    key: str,
) -> Callable[[], None]:
    lock = threading.Lock()
    ran = False

    def _run_once() -> None:
        nonlocal ran
        with lock:
            if ran:
                return
            ran = True
        try:
            callback()
        except Exception:
            logger.debug(
                "otel.shutdown_hook.failed",
                exc_info=True,
                extra={
                    "event": {"hook_key": key},
                    "_skip_enrichers": True,
                },
            )

    return _run_once


def _invoke_provider_method(
    provider: Any,
    *,
    method_name: str,
    logger: logging.Logger,
    component: str,
) -> None:
    method = getattr(provider, method_name, None)
    if not callable(method):
        return
    try:
        method()
    except TypeError:
        # Some OTel providers accept timeout_millis for force_flush.
        if method_name != "force_flush":
            raise
        try:
            method(timeout_millis=30_000)
        except Exception:
            logger.debug(
                "otel.provider.lifecycle.failed",
                exc_info=True,
                extra={
                    "event": {"component": component, "method": method_name},
                    "_skip_enrichers": True,
                },
            )
    except Exception:
        logger.debug(
            "otel.provider.lifecycle.failed",
            exc_info=True,
            extra={
                "event": {"component": component, "method": method_name},
                "_skip_enrichers": True,
            },
        )
