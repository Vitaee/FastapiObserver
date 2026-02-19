"""Tests for OTelMetricsSettings.from_env()."""

from __future__ import annotations

import pytest

from fastapiobserver.otel import OTelMetricsSettings


def test_otel_metrics_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_METRICS_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_METRICS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_METRICS_PROTOCOL", raising=False)
    monkeypatch.delenv("OTEL_METRICS_EXPORT_INTERVAL_MILLIS", raising=False)

    settings = OTelMetricsSettings.from_env()

    assert settings.enabled is False
    assert settings.otlp_endpoint is None
    assert settings.protocol == "grpc"
    assert settings.export_interval_millis == 60_000


def test_otel_metrics_from_env_reads_all_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_METRICS_ENABLED", "true")
    monkeypatch.setenv("OTEL_METRICS_ENDPOINT", "http://collector:4318")
    monkeypatch.setenv("OTEL_METRICS_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_METRICS_EXPORT_INTERVAL_MILLIS", "5000")

    settings = OTelMetricsSettings.from_env()

    assert settings.enabled is True
    assert settings.otlp_endpoint == "http://collector:4318"
    assert settings.protocol == "http/protobuf"
    assert settings.export_interval_millis == 5000
