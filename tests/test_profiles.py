from __future__ import annotations

import os
import typing

import pytest
from fastapi import FastAPI

from fastapiobserver import install_observability
from fastapiobserver.middleware.request_logging import RequestLoggingMiddleware
from fastapiobserver.otel.settings import OTelSettings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(("OBS_", "OTEL_")):
            monkeypatch.delenv(key)


def _installed(monkeypatch: pytest.MonkeyPatch, **env: str) -> dict[str, typing.Any]:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    app = FastAPI()
    install_observability(app)
    return next(m for m in app.user_middleware if m.cls is RequestLoggingMiddleware).kwargs


def test_observability_settings_read_from_env_automatically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _installed(
        monkeypatch, OBS_APP_NAME="zero-glue-test", OBS_SERVICE_NAME="magic-service"
    )["settings"]

    assert settings.app_name == "zero-glue-test"
    assert settings.service == "magic-service"


def test_development_profile_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _installed(
        monkeypatch, OBS_PROFILE="development", OBS_APP_NAME="dev-api", OBS_OTEL_ENABLED="true"
    )["settings"]

    assert settings.log_level == "DEBUG"
    assert OTelSettings.from_env().enabled is True
    assert "OBS_LOG_LEVEL" not in os.environ


def test_production_profile_enforces_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    installed = _installed(monkeypatch, OBS_PROFILE="production")
    settings, security_policy = installed["settings"], installed["security_policy"]

    assert settings.log_level == "INFO"
    assert settings.log_queue_max_size == 20000
    assert settings.loop_block_threshold_ms == 250  # the event-loop watchdog is on
    assert settings.log_level_on_error is None  # held DEBUG logs stay opt-in
    assert security_policy.redaction_mode == "drop"
    assert security_policy.log_request_body is False
    for key in (
        "OBS_LOG_LEVEL", "OBS_LOG_QUEUE_MAX_SIZE", "OBS_LOOP_BLOCK_THRESHOLD_MS",
        "OBS_REDACTION_PRESET",
    ):
        assert key not in os.environ
