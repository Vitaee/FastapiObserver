"""Flush OTel providers when the FastAPI app shuts down.

Shutting them down is left to process exit: SDK providers register their own
``atexit`` shutdown (``shutdown_on_exit=True``). A provider is global, so one
app's lifespan ending mustn't stop it for other apps, or for this one if it
starts again.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

_LOGGER = logging.getLogger("fastapiobserver.otel")
_HOOKED: weakref.WeakKeyDictionary[FastAPI, set[int]] = weakref.WeakKeyDictionary()


def flush_on_app_shutdown(app: FastAPI | None, provider: Any) -> None:
    """Each time ``app``'s lifespan ends, export what ``provider`` holds."""
    if app is None:
        return
    hooked = _HOOKED.setdefault(app, set())
    if id(provider) in hooked:
        return
    hooked.add(id(provider))

    # Wrap the lifespan: Starlette 1.0 removed add_event_handler/on_shutdown.
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app_inst: FastAPI) -> AsyncGenerator[Any, None]:
        try:
            async with original_lifespan(app_inst) as state:
                yield state
        finally:
            try:
                provider.force_flush()
            except Exception:
                _LOGGER.debug(
                    "otel.provider.lifecycle.failed",
                    exc_info=True,
                    extra={"_skip_enrichers": True},
                )

    app.router.lifespan_context = lifespan
