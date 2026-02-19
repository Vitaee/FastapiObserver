from __future__ import annotations

import os
import tempfile
import importlib

import fastapiobserver.metrics as metrics_module
from fastapi import FastAPI
from fastapiobserver.metrics import (
    NoopMetricsBackend,
    PrometheusMetricsBackend,
    build_metrics_backend,
    mount_backend_metrics_endpoint,
    mark_prometheus_process_dead,
    register_metrics_backend,
    unregister_metrics_backend,
)
from fastapiobserver.config import ObservabilitySettings
from fastapiobserver.logging import setup_logging
import pytest


def test_metrics_backend_disabled_returns_noop() -> None:
    backend = build_metrics_backend(False)
    assert isinstance(backend, NoopMetricsBackend)


def test_metrics_backend_missing_dependency_raises() -> None:
    original_lazy_import = metrics_module.lazy_import

    def fake_lazy_import(
        module_path: str,
        attr: str | None = None,
        *,
        package_hint: str | None = None,
    ):
        if module_path == "prometheus_client":
            raise ModuleNotFoundError("No module named 'prometheus_client'")
        return original_lazy_import(module_path, attr=attr, package_hint=package_hint)

    metrics_module.lazy_import = fake_lazy_import
    try:
        with pytest.raises(
            RuntimeError, match=r"pip install fastapi-observer\[prometheus\]"
        ):
            build_metrics_backend(True)
    finally:
        metrics_module.lazy_import = original_lazy_import


def test_metrics_backend_labels_include_service_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    backend = build_metrics_backend(True, service="billing", environment="prod")
    assert isinstance(backend, PrometheusMetricsBackend)
    label_names = set(backend.__class__._REQUEST_COUNT._labelnames)
    assert {"service", "environment", "method", "path", "status_code"} <= label_names


def test_metrics_backend_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="Unknown metrics backend"):
        build_metrics_backend(True, backend="custom")


def test_metrics_backend_custom_registry_factory() -> None:
    class _CustomBackend:
        def observe(
            self,
            method: str,
            path: str,
            status_code: int,
            duration_seconds: float,
        ) -> None:
            return None

    def _factory(
        *,
        service: str,
        environment: str,
        exemplars_enabled: bool,
    ) -> _CustomBackend:
        assert service == "billing"
        assert environment == "prod"
        assert exemplars_enabled is True
        return _CustomBackend()

    register_metrics_backend("custom", _factory)
    try:
        backend = build_metrics_backend(
            True,
            backend="custom",
            service="billing",
            environment="prod",
            exemplars_enabled=True,
        )
    finally:
        unregister_metrics_backend("custom")

    assert isinstance(backend, _CustomBackend)


def test_mount_backend_metrics_endpoint_uses_backend_extension() -> None:
    app = FastAPI()

    class _MountableBackend:
        def observe(
            self,
            method: str,
            path: str,
            status_code: int,
            duration_seconds: float,
        ) -> None:
            return None

        def mount_endpoint(
            self,
            app: FastAPI,
            *,
            path: str = "/metrics",
            metrics_format: str = "negotiate",
        ) -> None:
            app.state.mounted_path = path
            app.state.metrics_format = metrics_format

    backend = _MountableBackend()
    mounted = mount_backend_metrics_endpoint(
        app,
        backend,
        path="/custom-metrics",
        metrics_format="openmetrics",
    )

    assert mounted is True
    assert app.state.mounted_path == "/custom-metrics"
    assert app.state.metrics_format == "openmetrics"


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


def test_log_queue_metrics_are_registered_for_prometheus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    setup_logging(
        ObservabilitySettings(app_name="test", service="billing", environment="prod"),
        force=True,
    )
    build_metrics_backend(True, service="billing", environment="prod")

    prometheus_client = importlib.import_module("prometheus_client")
    metrics_text = prometheus_client.generate_latest(prometheus_client.REGISTRY).decode(
        "utf-8"
    )

    assert "fastapiobserver_log_queue_size" in metrics_text
    assert "fastapiobserver_log_queue_capacity" in metrics_text
    assert "fastapiobserver_log_queue_dropped_total" in metrics_text
    assert "fastapiobserver_sink_circuit_breaker_state_info" in metrics_text
    assert "fastapiobserver_sink_circuit_breaker_failures_total" in metrics_text
