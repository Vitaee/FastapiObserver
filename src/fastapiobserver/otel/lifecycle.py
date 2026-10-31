"""Flush OTel providers when the FastAPI app shuts down.

Process exit needs nothing from us: SDK providers register their own
``atexit`` shutdown (``shutdown_on_exit=True``).
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


def close_on_app_shutdown(app: FastAPI | None, provider: Any, *, shutdown: bool) -> None:
    """Once ``app``'s lifespan ends, shut ``provider`` down (``shutdown=True``,
    for providers we created) or just flush it (providers owned by someone else).
    """
    if app is None:
        return
    hooked = _HOOKED.setdefault(app, set())
    if id(provider) in hooked:
        return
    hooked.add(id(provider))

    # Wrap the lifespan: Starlette 1.0 removed add_event_handler/on_shutdown.
    original_lifespan = app.router.lifespan_context
    done = False

    @asynccontextmanager
    async def lifespan(app_inst: FastAPI) -> AsyncGenerator[Any, None]:
        nonlocal done
        try:
            async with original_lifespan(app_inst) as state:
                yield state
        finally:
            if not done:
                done = True
                try:
                    if shutdown:  # SDK shutdown() exports pending data first
                        provider.shutdown()
                    else:
                        provider.force_flush()
                except Exception:
                    _LOGGER.debug(
                        "otel.provider.lifecycle.failed",
                        exc_info=True,
                        extra={"_skip_enrichers": True},
                    )

    app.router.lifespan_context = lifespan
