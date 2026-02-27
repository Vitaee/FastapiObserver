from __future__ import annotations

import importlib
import os
from pathlib import Path
from .client import _import_prometheus_client


def _prepare_prometheus_multiprocess() -> None:
    """Ensure ``prometheus_client.multiprocess`` is available when enabled.

    Some ``prometheus-client`` builds expose the multiprocess helpers only
    after explicitly importing ``prometheus_client.multiprocess``.
    """
    if not _is_prometheus_multiprocess_enabled():
        return

    prometheus_client = _import_prometheus_client()
    if hasattr(prometheus_client, "multiprocess"):
        return

    try:
        prometheus_multiprocess = importlib.import_module("prometheus_client.multiprocess")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PROMETHEUS_MULTIPROC_DIR is set, but prometheus_client.multiprocess "
            "could not be imported. Install a prometheus-client build with "
            "multiprocess support."
        ) from exc

    prometheus_client.multiprocess = prometheus_multiprocess  # type: ignore[attr-defined]


def mark_prometheus_process_dead(pid: int) -> None:
    if not _is_prometheus_multiprocess_enabled():
        return
    _prepare_prometheus_multiprocess()
    prometheus_client = _import_prometheus_client()
    prometheus_client.multiprocess.mark_process_dead(pid)


def _is_prometheus_multiprocess_enabled() -> bool:
    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR", "").strip()
    return bool(multiproc_dir)


def _validate_prometheus_multiprocess_dir() -> None:
    if not _is_prometheus_multiprocess_enabled():
        return
    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR", "").strip()
    path = Path(multiproc_dir)
    if not path.exists():
        raise RuntimeError(
            "PROMETHEUS_MULTIPROC_DIR is set but does not exist. "
            "Create a writable directory before starting workers."
        )
    if not path.is_dir():
        raise RuntimeError("PROMETHEUS_MULTIPROC_DIR must point to a directory.")
    if not os.access(path, os.W_OK):
        raise RuntimeError("PROMETHEUS_MULTIPROC_DIR must be writable.")
    _prepare_prometheus_multiprocess()

__all__ = [
    "mark_prometheus_process_dead",
    "_is_prometheus_multiprocess_enabled",
    "_validate_prometheus_multiprocess_dir",
    "_prepare_prometheus_multiprocess",
]
