"""Tests for OTelLogsSettings.from_env() — 12-factor parity."""

from __future__ import annotations

import pytest

from fastapiobserver.otel import OTelLogsSettings

_VARS = (
    "OBS_OTEL_LOGS_ENABLED",
    "OBS_OTEL_LOGS_MODE",
    "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_SDK_DISABLED",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_from_env_defaults() -> None:
    settings = OTelLogsSettings.from_env()

    assert settings.enabled is False
    assert settings.logs_mode == "local_json"
    assert settings.otlp_endpoint is None  # the exporter reads the endpoint variables
    assert settings.protocol == "grpc"


def test_from_env_reads_all_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_OTEL_LOGS_ENABLED", "true")
    monkeypatch.setenv("OBS_OTEL_LOGS_MODE", "both")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", "http/protobuf")

    settings = OTelLogsSettings.from_env()

    assert settings.enabled is True
    assert settings.logs_mode == "both"
    assert settings.protocol == "http/protobuf"


def test_from_env_falls_back_to_the_shared_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

    assert OTelLogsSettings.from_env().protocol == "http/protobuf"


def test_from_env_signal_protocol_wins_over_the_shared_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL", "grpc")

    assert OTelLogsSettings.from_env().protocol == "grpc"


def test_from_env_invalid_mode_defaults_to_local_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBS_OTEL_LOGS_MODE", "invalid_mode")

    assert OTelLogsSettings.from_env().logs_mode == "local_json"


def test_sdk_disabled_turns_log_export_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_OTEL_LOGS_ENABLED", "true")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    assert OTelLogsSettings.from_env().enabled is False
