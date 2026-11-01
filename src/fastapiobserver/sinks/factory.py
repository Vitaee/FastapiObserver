from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import ObservabilitySettings

from .builtin import RotatingFileSink, StdoutSink
from .registry import get_registered_sinks

_LOGGER = logging.getLogger("fastapiobserver.sinks")

def build_sink_handlers(
    settings: "ObservabilitySettings",
    formatter: logging.Formatter,
) -> list[tuple[logging.Handler, str]]:
    """Build the list of output handlers from settings + registered sinks.

    Called by ``logging.setup_logging()``.  This is the single point of
    assembly (Dependency Inversion: high-level logging depends on this
    abstraction, not on concrete handler constructors).
    """
    handlers: list[tuple[logging.Handler, str]] = []

    # Always add stdout
    handlers.append((StdoutSink().create_handler(formatter), "stdout"))

    # Add rotating file if configured
    if settings.log_dir:
        handlers.append(
            (RotatingFileSink(log_dir=settings.log_dir).create_handler(formatter), "rotating_file")
        )

    # Add sinks registered with register_sink()
    for sink in get_registered_sinks().values():
        try:
            handlers.append((sink.create_handler(formatter), sink.name))
        except Exception:
            _LOGGER.warning(
                "sinks.create_handler.failed",
                exc_info=True,
                extra={
                    "event": {"sink_name": sink.name},
                    "_skip_enrichers": True,
                },
            )

    return handlers

__all__ = ["build_sink_handlers"]
