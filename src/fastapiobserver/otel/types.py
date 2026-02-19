"""Typing protocols for optional OpenTelemetry integrations.

These protocols intentionally cover only the methods this package relies on,
so we can provide useful typing without hard-importing OTel runtime classes.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any, Protocol, Sequence


class OTelResourceLike(Protocol):
    """Minimal resource shape accepted by OTel SDK providers."""

    attributes: Any


class SpanExporterLike(Protocol):
    """Minimal span exporter interface used by OTel processors."""

    def export(self, spans: Sequence[Any]) -> Any: ...

    def shutdown(self) -> None: ...


class LogExporterLike(Protocol):
    """Minimal log exporter interface used by OTel log processors."""

    def export(self, batch: Sequence[Any]) -> Any: ...

    def shutdown(self) -> None: ...


class MetricExporterLike(Protocol):
    """Minimal metric exporter interface used by OTel metric readers."""

    def export(self, metrics_data: Any, timeout_millis: float = ...) -> Any: ...

    def shutdown(self, timeout_millis: float = ...) -> None: ...


__all__ = [
    "ModuleType",
    "OTelResourceLike",
    "SpanExporterLike",
    "LogExporterLike",
    "MetricExporterLike",
]
