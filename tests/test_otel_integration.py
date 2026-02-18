from __future__ import annotations

from fastapi import FastAPI
import pytest

from fastapiobserver.config import ObservabilitySettings
from fastapiobserver.otel import (
    OTelSettings,
    create_otel_resource,
    install_otel,
    set_trace_sampling_ratio,
)
import fastapiobserver.otel as otel_module


def test_install_otel_missing_dependency_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = otel_module.importlib.import_module

    def fake_import(name: str):
        if name.startswith("opentelemetry"):
            raise ModuleNotFoundError(name)
        return original_import(name)

    monkeypatch.setattr(otel_module.importlib, "import_module", fake_import)

    with pytest.raises(RuntimeError, match=r"pip install fastapi-observer\[otel\]"):
        install_otel(
            FastAPI(),
            ObservabilitySettings(),
            OTelSettings(enabled=True, service_name="svc"),
        )


def test_trace_sampling_ratio_is_clamped() -> None:
    assert set_trace_sampling_ratio(1.2) == 1.0
    assert set_trace_sampling_ratio(-0.5) == 0.0
    assert set_trace_sampling_ratio(0.33) == 0.33


def test_create_otel_resource_when_dependency_available() -> None:
    pytest.importorskip("opentelemetry.sdk.resources")

    resource = create_otel_resource(
        ObservabilitySettings(app_name="orders", service="orders", version="2.0.0"),
        OTelSettings(
            enabled=True,
            service_name="orders-api",
            service_version="2.0.0",
            environment="production",
        ),
    )

    assert resource.attributes["service.name"] == "orders-api"
    assert resource.attributes["service.version"] == "2.0.0"
