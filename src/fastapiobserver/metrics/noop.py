from __future__ import annotations

class NoopMetricsBackend:
    """No-op backend that silently discards all observations."""

    def observe(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        return None

__all__ = ["NoopMetricsBackend"]
