from __future__ import annotations

import pytest
from pydantic import ValidationError

from fastapiobserver.config import ObservabilitySettings
from fastapiobserver.control_plane import RuntimeControlSettings
from fastapiobserver.otel import OTelSettings
from fastapiobserver.security import SecurityPolicy, TrustedProxyPolicy


def test_observability_settings_normalize_values() -> None:
    settings = ObservabilitySettings(
        log_level="debug",
        log_queue_overflow_policy="DROP_NEWEST",  # type: ignore[arg-type]
        sink_circuit_breaker_enabled=True,
        sink_circuit_breaker_failure_threshold=3,
        sink_circuit_breaker_recovery_timeout_seconds=15.0,
        request_id_header="X-Request-ID",
        response_request_id_header="X-Request-ID",
        metrics_backend=" CUSTOM_BACKEND ",
        metrics_path="metrics/",
        metrics_exclude_paths=("health", "/docs/", "/openapi.json"),
    )

    assert settings.log_level == "DEBUG"
    assert settings.log_queue_overflow_policy == "drop_newest"
    assert settings.sink_circuit_breaker_enabled is True
    assert settings.sink_circuit_breaker_failure_threshold == 3
    assert settings.sink_circuit_breaker_recovery_timeout_seconds == 15.0
    assert settings.request_id_header == "x-request-id"
    assert settings.response_request_id_header == "x-request-id"
    assert settings.metrics_backend == "custom_backend"
    assert settings.metrics_path == "/metrics"
    assert settings.metrics_exclude_paths == ("/health", "/docs", "/openapi.json")


def test_observability_settings_reject_invalid_header() -> None:
    with pytest.raises(ValidationError, match="Invalid header value"):
        ObservabilitySettings(request_id_header="invalid header")


def test_observability_settings_reject_invalid_log_queue_policy() -> None:
    with pytest.raises(ValidationError, match="log_queue_overflow_policy"):
        ObservabilitySettings(log_queue_overflow_policy="evict_random")  # type: ignore[arg-type]


def test_observability_settings_reject_empty_metrics_backend() -> None:
    with pytest.raises(ValidationError, match="metrics_backend"):
        ObservabilitySettings(metrics_backend="   ")


def test_observability_settings_reject_invalid_sink_circuit_breaker_threshold() -> None:
    with pytest.raises(ValidationError, match="sink_circuit_breaker_failure_threshold"):
        ObservabilitySettings(sink_circuit_breaker_failure_threshold=0)


def test_security_policy_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_REDACTION_MODE", "hash")
    monkeypatch.setenv("OBS_LOG_REQUEST_BODY", "true")
    monkeypatch.setenv("OBS_MAX_BODY_LENGTH", "512")

    policy = SecurityPolicy.from_env()

    assert policy.redaction_mode == "hash"
    assert policy.log_request_body is True
    assert policy.max_body_length == 512


def test_security_policy_rejects_invalid_max_body_length() -> None:
    with pytest.raises(ValidationError, match="max_body_length"):
        SecurityPolicy(max_body_length=0)


def test_security_policy_can_unset_preset_optional_fields_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBS_REDACTION_PRESET", "strict")
    monkeypatch.setenv("OBS_HEADER_ALLOWLIST", "none")

    policy = SecurityPolicy.from_env()

    assert policy.header_allowlist is None


def test_trusted_proxy_policy_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_TRUSTED_PROXY_ENABLED", "false")
    monkeypatch.setenv("OBS_TRUSTED_CIDRS", "10.0.0.0/8, 127.0.0.1/32")
    monkeypatch.setenv("OBS_HONOR_FORWARDED_HEADERS", "true")

    policy = TrustedProxyPolicy.from_env()

    assert policy.enabled is False
    assert policy.trusted_cidrs == ("10.0.0.0/8", "127.0.0.1/32")
    assert policy.honor_forwarded_headers is True


def test_otel_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.25")

    settings = OTelSettings.from_env()

    assert settings.enabled is True
    assert settings.protocol == "http/protobuf"
    assert settings.trace_sampling_ratio == 0.25


def test_otel_service_name_names_the_service_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OBS_SERVICE_NAME", raising=False)
    monkeypatch.setenv("OTEL_SERVICE_NAME", "payments-api")
    assert ObservabilitySettings().service == "payments-api"

    monkeypatch.setenv("OBS_SERVICE_NAME", "orders-api")
    assert ObservabilitySettings().service == "orders-api"


def test_sdk_disabled_turns_tracing_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    assert OTelSettings.from_env().enabled is False


def test_otel_settings_reject_invalid_protocol() -> None:
    with pytest.raises(ValidationError, match="Invalid OTel protocol"):
        OTelSettings(enabled=True, protocol="udp")  # type: ignore[arg-type]


def test_runtime_control_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_RUNTIME_CONTROL_ENABLED", "true")
    monkeypatch.setenv("OBS_RUNTIME_CONTROL_PATH", "control/")
    monkeypatch.setenv("OBS_RUNTIME_CONTROL_TOKEN_ENV_VAR", "CONTROL_TOKEN")

    settings = RuntimeControlSettings.from_env()

    assert settings.enabled is True
    assert settings.path == "/control"
    assert settings.token_env_var == "CONTROL_TOKEN"


@pytest.mark.parametrize("value", ["/health,/ready", '["/health", "/ready"]'])
def test_list_settings_read_csv_or_json_from_env(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OBS_METRICS_EXCLUDE_PATHS", value)

    assert ObservabilitySettings().metrics_exclude_paths == ("/health", "/ready")


@pytest.mark.parametrize("value", ["", "[]"])
def test_empty_metrics_exclude_paths_excludes_nothing(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OBS_METRICS_EXCLUDE_PATHS", value)
    assert ObservabilitySettings().metrics_exclude_paths == ()
    assert ObservabilitySettings(metrics_exclude_paths=()).metrics_exclude_paths == ()


def test_settings_ignore_variables_named_like_their_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "ENABLED": "true",
        "PATH": "/usr/bin",
        "PROTOCOL": "http/protobuf",
        "LOG_LEVEL": "DEBUG",
        "METRICS_ENABLED": "true",
    }.items():
        monkeypatch.setenv(name, value)

    control = RuntimeControlSettings.from_env()
    assert control.enabled is False
    assert control.path == "/_observability/control"
    assert OTelSettings.from_env().enabled is False
    assert OTelSettings.from_env().protocol == "grpc"
    settings = ObservabilitySettings()
    assert settings.log_level == "INFO"
    assert settings.metrics_enabled is False
    # Field names still work in code.
    assert ObservabilitySettings(log_level="DEBUG").log_level == "DEBUG"
