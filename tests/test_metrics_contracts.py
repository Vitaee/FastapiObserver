from __future__ import annotations

import os
from pathlib import Path

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

        register_metrics_backend("dummy_test", dummy_factory)  # type: ignore[arg-type]
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


@pytest.mark.parametrize("path", ["/auth/2fa", "/v2beta/users", "/files/1password"])
def test_collapse_keeps_segments_that_only_start_with_digits(path: str) -> None:
    from fastapiobserver.metrics import collapse_dynamic_segments

    assert collapse_dynamic_segments(path) == path


@pytest.mark.parametrize(("sampled", "has_exemplar"), [(True, True), (False, False)])
def test_exemplar_only_for_sampled_traces(sampled: bool, has_exemplar: bool) -> None:
    trace_api = pytest.importorskip("opentelemetry.trace")
    from fastapiobserver.metrics.prometheus import _get_trace_exemplar

    context = trace_api.SpanContext(
        trace_id=0x1234,
        span_id=0x5678,
        is_remote=False,
        trace_flags=trace_api.TraceFlags(trace_api.TraceFlags.SAMPLED if sampled else 0),
    )
    with trace_api.use_span(trace_api.NonRecordingSpan(context)):
        assert (_get_trace_exemplar() is not None) is has_exemplar


def test_multiprocess_mode_warns_that_process_stats_are_not_exported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pytest.importorskip("prometheus_client")
    from fastapiobserver.metrics.prometheus import _register_collectors

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    with caplog.at_level("WARNING", logger="fastapiobserver.metrics"):
        _register_collectors()

    assert "metrics.multiprocess.process_stats_unavailable" in caplog.messages


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


def test_prometheus_multiprocess_validation_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapiobserver.metrics.prometheus import _validate_multiprocess_dir

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    _validate_multiprocess_dir()

    (tmp_path / "file.txt").write_text("")
    for bad_dir in (tmp_path / "missing", tmp_path / "file.txt"):
        monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(bad_dir))
        with pytest.raises(RuntimeError, match="existing, writable directory"):
            _validate_multiprocess_dir()

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    monkeypatch.setattr(os, "access", lambda *_args: False)
    with pytest.raises(RuntimeError, match="existing, writable directory"):
        _validate_multiprocess_dir()


def test_mark_prometheus_process_dead_removes_live_gauge_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapiobserver import mark_prometheus_process_dead

    dead_gauge = tmp_path / "gauge_liveall_4321.db"
    dead_gauge.write_bytes(b"")
    other_worker = tmp_path / "gauge_liveall_9999.db"
    other_worker.write_bytes(b"")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

    mark_prometheus_process_dead(4321)

    assert not dead_gauge.exists()
    assert other_worker.exists()
