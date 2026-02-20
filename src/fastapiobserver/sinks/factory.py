from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import ObservabilitySettings

from .builtin import RotatingFileSink, StdoutSink
from .discovery import discover_entry_point_sinks
from .logtail.sink import LogtailSink
from .registry import _SINK_REGISTRY

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

    # Add Logtail if configured
    if settings.logtail_enabled and settings.logtail_source_token:
        handlers.append(
            (
                LogtailSink(
                    source_token=settings.logtail_source_token,
                    batch_size=settings.logtail_batch_size,
                    flush_interval=settings.logtail_flush_interval,
                    dlq_enabled=settings.logtail_dlq_enabled,
                    dlq_dir=settings.logtail_dlq_dir,
                    dlq_filename=settings.logtail_dlq_filename,
                    dlq_max_bytes=settings.logtail_dlq_max_bytes,
                    dlq_backup_count=settings.logtail_dlq_backup_count,
                    dlq_compress=settings.logtail_dlq_compress,
                ).create_handler(formatter),
                "logtail",
            )
        )

    # Add entry-point discovered sinks
    discover_entry_point_sinks()
    for sink in _SINK_REGISTRY.values():
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
