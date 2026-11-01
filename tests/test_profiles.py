"""Tests for zero-glue environment profile auto-configuration."""
from __future__ import annotations

import os
import typing

import pytest
from fastapi import FastAPI

from fastapiobserver import install_observability
from fastapiobserver.config import ObservabilitySettings
from fastapiobserver.fastapi import _REGISTERED_APPS
from fastapiobserver.security import SecurityPolicy


@pytest.fixture(autouse=True)
def clean_env() -> typing.Generator[None, None, None]:
    """Ensure a clean environment before and after each test."""
    original_env = dict(os.environ)
    # Clear out any observational env vars that might pollute
    for key in list(os.environ.keys()):
        if (
            key.startswith("OBS_")
            or key.startswith("OTEL_")
        ):
            del os.environ[key]
            
    yield
    
    os.environ.clear()
    os.environ.update(original_env)
    _REGISTERED_APPS.clear()


def test_zero_glue_install_works_with_no_args() -> None:
    """Test that install_observability requires only the FastAPI app."""
    app = FastAPI()
    
    # This should not raise any TypeError about missing requires positional arguments
    install_observability(app)
    
    # Verify it completed its most basic setup task (registering lifespan)
    assert app in _REGISTERED_APPS


def test_observability_settings_read_from_env_automatically() -> None:
    """Test zero-glue picks up standard env vars."""
    os.environ["OBS_APP_NAME"] = "zero-glue-test"
    os.environ["OBS_SERVICE_NAME"] = "magic-service"
    
    app = FastAPI()
    install_observability(app)
    
    # We can fetch the configured settings instance by checking the middleware
    # because the middleware intercepts and stores the final settings object.
    from fastapiobserver.middleware.request_logging import RequestLoggingMiddleware
    
    middleware = next(m for m in app.user_middleware if m.cls is RequestLoggingMiddleware)
    settings = typing.cast(ObservabilitySettings, middleware.kwargs["settings"])
    
    assert settings.app_name == "zero-glue-test"
    assert settings.service == "magic-service"


def test_development_profile_overrides() -> None:
    """Test OBS_PROFILE=development behavior."""
    os.environ["OBS_PROFILE"] = "development"
    os.environ["OBS_APP_NAME"] = "dev-api"
    os.environ["OBS_OTEL_ENABLED"] = "true"
    
    app = FastAPI()
    install_observability(app)
    
    from fastapiobserver.middleware.request_logging import RequestLoggingMiddleware
    middleware = next(m for m in app.user_middleware if m.cls is RequestLoggingMiddleware)
    settings = typing.cast(ObservabilitySettings, middleware.kwargs["settings"])
    
    from fastapiobserver.otel.settings import OTelSettings
    otel_settings = OTelSettings.from_env()

    assert settings.log_level == "DEBUG"
    # os.environ modification during profile context allows user envs to override securely
    assert otel_settings.enabled is True
    # The environment shouldn't bleed out.
    assert "OBS_LOG_LEVEL" not in os.environ


def test_development_profile_pure_defaults() -> None:
    """Test OBS_PROFILE=development when no user vars exist."""
    os.environ["OBS_PROFILE"] = "development"
    
    app = FastAPI()
    install_observability(app)
    
    from fastapiobserver.middleware.request_logging import RequestLoggingMiddleware
    middleware = next(m for m in app.user_middleware if m.cls is RequestLoggingMiddleware)
    settings = typing.cast(ObservabilitySettings, middleware.kwargs["settings"])

    assert settings.log_level == "DEBUG"


def test_production_profile_enforces_strict() -> None:
    """Test OBS_PROFILE=production behavior."""
    os.environ["OBS_PROFILE"] = "production"
    
    app = FastAPI()
    install_observability(app)
    
    from fastapiobserver.middleware.request_logging import RequestLoggingMiddleware
    middleware = next(m for m in app.user_middleware if m.cls is RequestLoggingMiddleware)
    settings = typing.cast(ObservabilitySettings, middleware.kwargs["settings"])
    security_policy = typing.cast(SecurityPolicy, middleware.kwargs["security_policy"])
    
    assert settings.log_level == "INFO"
    assert settings.log_queue_max_size == 20000
    assert security_policy.redaction_mode == "drop"
    assert security_policy.log_request_body is False

    # Asserts that local os environ variables do not bleed out after the end
    assert "OBS_LOG_LEVEL" not in os.environ
    assert "OBS_LOG_QUEUE_MAX_SIZE" not in os.environ
    assert "OBS_REDACTION_PRESET" not in os.environ
