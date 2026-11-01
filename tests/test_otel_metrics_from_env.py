"""Tests for OTelMetricsSettings.from_env()."""

from __future__ import annotations

import pytest

from fastapiobserver.otel import OTelMetricsSettings

_VARS = (
    "OBS_OTEL_METRICS_ENABLED",
    "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_METRIC_EXPORT_INTERVAL",
    "OTEL_SDK_DISABLED",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_otel_metrics_from_env_defaults() -> None:
    settings = OTelMetricsSettings.from_env()

    assert settings.enabled is False
    assert settings.otlp_endpoint is None  # the exporter reads the endpoint variables
    assert settings.protocol == "grpc"
    assert settings.export_interval_millis == 60_000


def test_otel_metrics_from_env_reads_all_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_OTEL_METRICS_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_METRIC_EXPORT_INTERVAL", "5000")

    settings = OTelMetricsSettings.from_env()

    assert settings.enabled is True
    assert settings.protocol == "http/protobuf"
    assert settings.export_interval_millis == 5000


def test_otel_metrics_fall_back_to_the_shared_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

    assert OTelMetricsSettings.from_env().protocol == "http/protobuf"


def test_sdk_disabled_turns_metric_export_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_OTEL_METRICS_ENABLED", "true")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    assert OTelMetricsSettings.from_env().enabled is False
