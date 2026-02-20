from __future__ import annotations

import os
from pathlib import Path
from .client import _import_prometheus_client

def mark_prometheus_process_dead(pid: int) -> None:
    if not _is_prometheus_multiprocess_enabled():
        return
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

__all__ = [
    "mark_prometheus_process_dead",
    "_is_prometheus_multiprocess_enabled",
    "_validate_prometheus_multiprocess_dir",
]
