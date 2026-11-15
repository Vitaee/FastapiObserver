from __future__ import annotations

import sys

import pytest

from fastapiobserver.config import ObservabilitySettings
from fastapiobserver.logging import setup_logging
from fastapiobserver.metrics import NoopMetricsBackend, build_metrics_backend


def test_metrics_backend_disabled_returns_noop() -> None:
    assert isinstance(build_metrics_backend(False), NoopMetricsBackend)


def test_metrics_backend_missing_dependency_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    monkeypatch.setitem(sys.modules, "prometheus_client", None)
    with pytest.raises(
        RuntimeError, match=r"pip install fastapi-observer\[prometheus\]"
    ):
        build_metrics_backend(True)


def test_metrics_backend_labels_include_service_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prometheus_client = pytest.importorskip("prometheus_client")
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    backend = build_metrics_backend(True, service="billing", environment="prod")
    backend.observe(method="GET", path="/labels", status_code=200, duration_seconds=0.01)

    labels = prometheus_client.REGISTRY.get_sample_value(
        "http_requests_total",
        {"method": "GET", "path": "/labels", "status_code": "200",
         "service": "billing", "environment": "prod"},
    )
    assert labels is not None and labels >= 1


def test_metrics_backend_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="Unknown metrics backend"):
        build_metrics_backend(True, backend="custom")


def test_metrics_backend_rejects_a_missing_multiprocess_dir(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="PROMETHEUS_MULTIPROC_DIR"):
        build_metrics_backend(True)


def test_log_queue_metrics_are_registered_for_prometheus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prometheus_client = pytest.importorskip("prometheus_client")
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    setup_logging(
        ObservabilitySettings(app_name="test", service="billing", environment="prod"),
        force=True,
    )
    build_metrics_backend(True, service="billing", environment="prod")

    metrics_text = prometheus_client.generate_latest(prometheus_client.REGISTRY).decode()

    assert "fastapiobserver_log_queue_size" in metrics_text
    assert "fastapiobserver_log_queue_capacity" in metrics_text
    assert "fastapiobserver_log_queue_dropped_total" in metrics_text
    assert "fastapiobserver_sink_circuit_breaker_state_info" in metrics_text
    assert "fastapiobserver_sink_circuit_breaker_failures_total" in metrics_text
