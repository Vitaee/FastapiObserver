from __future__ import annotations

import types
from unittest import mock

import pytest


def test_metrics_registry_contract() -> None:
    from fastapiobserver.metrics import (
        get_registered_metrics_backends,
        register_metrics_backend,
        unregister_metrics_backend,
    )

    original_backends = get_registered_metrics_backends()

    try:

        def dummy_factory(**kwargs: object) -> str:
            return "dummy"

        register_metrics_backend("dummy_test", dummy_factory)
        current = get_registered_metrics_backends()
        assert "dummy_test" in current
        assert current["dummy_test"] is dummy_factory

        unregister_metrics_backend("dummy_test")
        current = get_registered_metrics_backends()
        assert "dummy_test" not in current

    finally:
        for name in list(get_registered_metrics_backends().keys()):
            if name not in original_backends:
                unregister_metrics_backend(name)


def test_metrics_collapse_dynamic_segments_contract() -> None:
    from fastapiobserver.metrics import collapse_dynamic_segments

    path1 = "/users/123e4567-e89b-12d3-a456-426614174000/profile"
    assert collapse_dynamic_segments(path1) == "/users/:id/profile"
    assert collapse_dynamic_segments("/api/v1/items/42/details") == "/api/v1/items/:id/details"
    assert collapse_dynamic_segments("/objects/0123456789abcdef012345/meta") == "/objects/:id/meta"


def test_metrics_accepts_openmetrics_contract() -> None:
    from fastapiobserver.metrics.endpoint import _accepts_openmetrics

    assert _accepts_openmetrics("application/openmetrics-text") is True
    assert _accepts_openmetrics("application/json, application/openmetrics-text; q=0.5") is True
    assert _accepts_openmetrics("application/openmetrics-text; q=0") is False
    assert _accepts_openmetrics("text/html") is False
    assert _accepts_openmetrics("") is False


def test_metrics_build_backend_contract() -> None:
    from fastapiobserver.metrics import (
        NoopMetricsBackend,
        PrometheusMetricsBackend,
        build_metrics_backend,
    )

    noop = build_metrics_backend(enabled=False)
    assert isinstance(noop, NoopMetricsBackend)

    prom = build_metrics_backend(enabled=True, backend="prometheus")
    assert isinstance(prom, PrometheusMetricsBackend)
    assert prom.service == "api"
    assert prom.environment == "development"

    with pytest.raises(ValueError, match="Unknown metrics backend"):
        build_metrics_backend(enabled=True, backend="nonexistent")


@mock.patch(
    "fastapiobserver.metrics.prometheus.multiprocess."
    "_is_prometheus_multiprocess_enabled",
    return_value=True,
)
@mock.patch("os.access", return_value=True)
@mock.patch("pathlib.Path.is_dir", return_value=True)
@mock.patch("pathlib.Path.exists", return_value=True)
@mock.patch("os.getenv", return_value="/tmp/multiproc")
def test_prometheus_multiprocess_validation_contract(
    mock_getenv: mock.Mock,
    mock_exists: mock.Mock,
    mock_isdir: mock.Mock,
    mock_access: mock.Mock,
    mock_enabled: mock.Mock,
) -> None:
    from fastapiobserver.metrics.prometheus.multiprocess import (
        _validate_prometheus_multiprocess_dir,
    )

    _ = (mock_getenv, mock_enabled)

    _validate_prometheus_multiprocess_dir()

    mock_access.return_value = False
    with pytest.raises(RuntimeError, match="must be writable"):
        _validate_prometheus_multiprocess_dir()
    mock_access.return_value = True

    mock_isdir.return_value = False
    with pytest.raises(RuntimeError, match="must point to a directory"):
        _validate_prometheus_multiprocess_dir()
    mock_isdir.return_value = True

    mock_exists.return_value = False
    with pytest.raises(RuntimeError, match="set but does not exist"):
        _validate_prometheus_multiprocess_dir()


def test_prepare_prometheus_multiprocess_attaches_submodule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapiobserver.metrics.prometheus import multiprocess as multiprocess_module

    class _PromClient:
        pass

    client = _PromClient()
    fake_multiprocess = types.SimpleNamespace()

    def _fake_import_module(module_name: str) -> object:
        if module_name != "prometheus_client.multiprocess":
            raise AssertionError(f"Unexpected import: {module_name}")
        return fake_multiprocess

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "/tmp/prom")
    monkeypatch.setattr(
        multiprocess_module,
        "_import_prometheus_client",
        lambda: client,
    )
    monkeypatch.setattr(
        multiprocess_module.importlib,
        "import_module",
        _fake_import_module,
    )

    multiprocess_module._prepare_prometheus_multiprocess()

    assert hasattr(client, "multiprocess")
    assert client.multiprocess is fake_multiprocess


def test_mark_prometheus_process_dead_prepares_missing_submodule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapiobserver.metrics.prometheus import multiprocess as multiprocess_module

    class _PromClient:
        pass

    marked: list[int] = []
    client = _PromClient()
    fake_multiprocess = types.SimpleNamespace(
        mark_process_dead=lambda pid: marked.append(pid),
    )

    def _fake_import_module(module_name: str) -> object:
        if module_name != "prometheus_client.multiprocess":
            raise AssertionError(f"Unexpected import: {module_name}")
        return fake_multiprocess

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "/tmp/prom")
    monkeypatch.setattr(
        multiprocess_module,
        "_import_prometheus_client",
        lambda: client,
    )
    monkeypatch.setattr(
        multiprocess_module.importlib,
        "import_module",
        _fake_import_module,
    )

    multiprocess_module.mark_prometheus_process_dead(4321)

    assert marked == [4321]
