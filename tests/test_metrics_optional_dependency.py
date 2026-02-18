from __future__ import annotations

import os
import tempfile

import observabilityfastapi.metrics as metrics_module
from observabilityfastapi.metrics import (
    NoopMetricsBackend,
    PrometheusMetricsBackend,
    build_metrics_backend,
    mark_prometheus_process_dead,
)
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


def test_metrics_backend_labels_include_service_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    backend = build_metrics_backend(True, service="billing", environment="prod")
    assert isinstance(backend, PrometheusMetricsBackend)
    label_names = set(backend.__class__._REQUEST_COUNT._labelnames)
    assert {"service", "environment", "method", "path", "status_code"} <= label_names


def test_metrics_backend_multiprocess_dir_validation_raises() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        bad_path = os.path.join(temp_dir, "missing")
        old_value = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = bad_path
        try:
            with pytest.raises(RuntimeError, match="PROMETHEUS_MULTIPROC_DIR"):
                build_metrics_backend(True)
        finally:
            if old_value is None:
                os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
            else:
                os.environ["PROMETHEUS_MULTIPROC_DIR"] = old_value


def test_mark_process_dead_noop_without_multiprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    mark_prometheus_process_dead(1234)
