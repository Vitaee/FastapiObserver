from __future__ import annotations

import observabilityfastapi.metrics as metrics_module
from observabilityfastapi.metrics import NoopMetricsBackend, build_metrics_backend
import pytest


def test_metrics_backend_disabled_returns_noop() -> None:
    backend = build_metrics_backend(False)
    assert isinstance(backend, NoopMetricsBackend)


def test_metrics_backend_missing_dependency_raises() -> None:
    original_import = metrics_module.importlib.import_module

    def fake_import(name: str):
        if name == "prometheus_client":
            raise ModuleNotFoundError("No module named 'prometheus_client'")
        return original_import(name)

    metrics_module.importlib.import_module = fake_import
    try:
        with pytest.raises(
            RuntimeError, match=r"pip install observabilityfastapi\[prometheus\]"
        ):
            build_metrics_backend(True)
    finally:
        metrics_module.importlib.import_module = original_import
