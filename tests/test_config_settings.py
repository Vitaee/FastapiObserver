from __future__ import annotations

import pytest

from observabilityfastapi.config import ObservabilitySettings
from observabilityfastapi.control_plane import RuntimeControlSettings
from observabilityfastapi.otel import OTelSettings
from observabilityfastapi.security import SecurityPolicy, TrustedProxyPolicy


def test_observability_settings_normalize_values() -> None:
    settings = ObservabilitySettings(
        log_level="debug",
        request_id_header="X-Request-ID",
        response_request_id_header="X-Request-ID",
        metrics_path="metrics/",
        metrics_exclude_paths=("health", "/docs/", "/openapi.json"),
    )

    assert settings.log_level == "DEBUG"
    assert settings.request_id_header == "x-request-id"
    assert settings.response_request_id_header == "x-request-id"
    assert settings.metrics_path == "/metrics"
    assert settings.metrics_exclude_paths == ("/health", "/docs", "/openapi.json")


def test_observability_settings_reject_invalid_header() -> None:
    with pytest.raises(ValueError, match="Invalid request_id_header"):
        ObservabilitySettings(request_id_header="invalid header")


def test_security_policy_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_REDACTION_MODE", "hash")
    monkeypatch.setenv("OBS_LOG_REQUEST_BODY", "true")
    monkeypatch.setenv("OBS_MAX_BODY_LENGTH", "512")

    policy = SecurityPolicy.from_env()

    assert policy.redaction_mode == "hash"
    assert policy.log_request_body is True
    assert policy.max_body_length == 512


def test_security_policy_rejects_invalid_max_body_length() -> None:
    with pytest.raises(ValueError, match="max_body_length"):
        SecurityPolicy(max_body_length=0)


def test_trusted_proxy_policy_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_TRUSTED_PROXY_ENABLED", "false")
    monkeypatch.setenv("OBS_TRUSTED_CIDRS", "10.0.0.0/8, 127.0.0.1/32")
    monkeypatch.setenv("OBS_HONOR_FORWARDED_HEADERS", "true")

    policy = TrustedProxyPolicy.from_env()

    assert policy.enabled is False
    assert policy.trusted_cidrs == ("10.0.0.0/8", "127.0.0.1/32")
    assert policy.honor_forwarded_headers is True


def test_otel_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "payments-api")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_TRACE_SAMPLING_RATIO", "0.25")

    settings = OTelSettings.from_env()

    assert settings.enabled is True
    assert settings.service_name == "payments-api"
    assert settings.protocol == "http/protobuf"
    assert settings.trace_sampling_ratio == 0.25


def test_otel_settings_reject_invalid_protocol() -> None:
    with pytest.raises(ValueError, match="Invalid OTel protocol"):
        OTelSettings(enabled=True, protocol="udp")  # type: ignore[arg-type]


def test_runtime_control_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_RUNTIME_CONTROL_ENABLED", "true")
    monkeypatch.setenv("OBS_RUNTIME_CONTROL_PATH", "control/")
    monkeypatch.setenv("OBS_RUNTIME_CONTROL_TOKEN_ENV_VAR", "CONTROL_TOKEN")

    settings = RuntimeControlSettings.from_env()

    assert settings.enabled is True
    assert settings.path == "/control"
    assert settings.token_env_var == "CONTROL_TOKEN"
