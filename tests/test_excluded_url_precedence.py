"""Tests for the 3-tier excluded URL precedence in _build_excluded_urls_csv."""

from __future__ import annotations

import pytest

from fastapiobserver.config import ObservabilitySettings
from fastapiobserver.otel import _build_excluded_urls_csv  # type: ignore[attr-defined]


def test_tier1_explicit_config_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier 1: explicit otel_excluded_urls overrides everything."""
    monkeypatch.setenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "/should-be-ignored")
    settings = ObservabilitySettings(
        otel_excluded_urls=("/custom/path", "/another"),
    )
    result = _build_excluded_urls_csv(settings)
    assert result is not None
    assert "/custom/path" in result
    assert "/another" in result


def test_tier2_otel_env_vars_return_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier 2: when OTel env vars are set (but no explicit config),
    return None so the SDK handles them natively."""
    monkeypatch.setenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "/health,/ready")
    settings = ObservabilitySettings()
    assert settings.otel_excluded_urls is None  # not explicitly set
    result = _build_excluded_urls_csv(settings)
    assert result is None


def test_tier2_generic_otel_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier 2: generic OTEL_PYTHON_EXCLUDED_URLS also defers to SDK."""
    monkeypatch.setenv("OTEL_PYTHON_EXCLUDED_URLS", "/ping")
    monkeypatch.delenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", raising=False)
    settings = ObservabilitySettings()
    result = _build_excluded_urls_csv(settings)
    assert result is None


def test_tier2_empty_string_env_var_still_defers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting OTEL_PYTHON_FASTAPI_EXCLUDED_URLS='' still counts as 'set'
    and defers to SDK rather than falling to package defaults."""
    monkeypatch.setenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "")
    settings = ObservabilitySettings()
    result = _build_excluded_urls_csv(settings)
    assert result is None


def test_tier3_package_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier 3: no explicit config, no OTel env vars → auto-derive safe defaults."""
    monkeypatch.delenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", raising=False)
    monkeypatch.delenv("OTEL_PYTHON_EXCLUDED_URLS", raising=False)
    settings = ObservabilitySettings()
    result = _build_excluded_urls_csv(settings)
    assert result is not None
    urls = result.split(",")
    assert "/metrics" in urls
    assert "/_observability/control" in urls


def test_explicit_empty_exclusion_returns_empty_string() -> None:
    """Operators can set OTEL_EXCLUDED_URLS='' to mean 'no exclusions at all'."""
    settings = ObservabilitySettings(otel_excluded_urls="")  # type: ignore[arg-type]
    assert settings.otel_excluded_urls == ()  # validator converts "" → empty tuple
    result = _build_excluded_urls_csv(settings)
    assert result == ""  # empty string = trace everything, no exclusions


def test_install_otel_passes_empty_excluded_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: install_otel actually passes excluded_urls='' to instrument_app
    when otel_excluded_urls is explicitly set to empty."""
    pytest.importorskip("opentelemetry.instrumentation.fastapi")

    from fastapi import FastAPI
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    from fastapiobserver.otel import OTelSettings, install_otel

    captured_kwargs: dict = {}

    def spy_instrument_app(app, **kwargs):
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(FastAPIInstrumentor, "instrument_app", spy_instrument_app)

    app = FastAPI()
    settings = ObservabilitySettings(otel_excluded_urls="")  # type: ignore[arg-type]
    otel_settings = OTelSettings(enabled=True, service_name="test-svc")

    try:
        install_otel(app, settings, otel_settings)
    except Exception:
        pass  # partial install is fine; we only care about the kwargs

    assert "excluded_urls" in captured_kwargs, (
        "install_otel must pass excluded_urls to instrument_app"
    )
    assert captured_kwargs["excluded_urls"] == ""
