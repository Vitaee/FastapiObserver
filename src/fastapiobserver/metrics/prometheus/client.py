from __future__ import annotations

from typing import Any

from ...utils import lazy_import


def _import_prometheus_client() -> Any:
    try:
        return lazy_import(
            "prometheus_client",
            package_hint="fastapi-observer[prometheus]",
        )
    except (RuntimeError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "Prometheus support requires `pip install fastapi-observer[prometheus]`"
        ) from exc

__all__ = ["_import_prometheus_client"]
