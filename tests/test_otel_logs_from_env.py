"""Tests for OTelLogsSettings.from_env() — 12-factor parity."""

from __future__ import annotations

import pytest

from fastapiobserver.otel import OTelLogsSettings


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no env vars are set, from_env returns defaults."""
    monkeypatch.delenv("OTEL_LOGS_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_LOGS_MODE", raising=False)
    monkeypatch.delenv("OTEL_LOGS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_LOGS_PROTOCOL", raising=False)

    settings = OTelLogsSettings.from_env()

    assert settings.enabled is False
    assert settings.logs_mode == "local_json"
    assert settings.otlp_endpoint is None
    assert settings.protocol == "grpc"


def test_from_env_reads_all_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """All env vars are correctly read."""
    monkeypatch.setenv("OTEL_LOGS_ENABLED", "true")
    monkeypatch.setenv("OTEL_LOGS_MODE", "both")
    monkeypatch.setenv("OTEL_LOGS_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_LOGS_PROTOCOL", "grpc")

    settings = OTelLogsSettings.from_env()

    assert settings.enabled is True
    assert settings.logs_mode == "both"
    assert settings.otlp_endpoint == "http://collector:4317"
    assert settings.protocol == "grpc"


def test_from_env_http_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    """http/protobuf protocol is correctly parsed."""
    monkeypatch.setenv("OTEL_LOGS_ENABLED", "true")
    monkeypatch.setenv("OTEL_LOGS_PROTOCOL", "http/protobuf")
    monkeypatch.delenv("OTEL_LOGS_MODE", raising=False)
    monkeypatch.delenv("OTEL_LOGS_ENDPOINT", raising=False)

    settings = OTelLogsSettings.from_env()

    assert settings.protocol == "http/protobuf"


def test_from_env_invalid_mode_defaults_to_local_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid logs_mode falls back to local_json."""
    monkeypatch.setenv("OTEL_LOGS_MODE", "invalid_mode")
    monkeypatch.delenv("OTEL_LOGS_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_LOGS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_LOGS_PROTOCOL", raising=False)

    settings = OTelLogsSettings.from_env()

    assert settings.logs_mode == "local_json"


def test_from_env_otlp_only_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """logs_mode='otlp' is correctly parsed."""
    monkeypatch.setenv("OTEL_LOGS_ENABLED", "true")
    monkeypatch.setenv("OTEL_LOGS_MODE", "otlp")
    monkeypatch.setenv("OTEL_LOGS_ENDPOINT", "http://localhost:4317")
    monkeypatch.delenv("OTEL_LOGS_PROTOCOL", raising=False)

    settings = OTelLogsSettings.from_env()

    assert settings.enabled is True
    assert settings.logs_mode == "otlp"
    assert settings.otlp_endpoint == "http://localhost:4317"
