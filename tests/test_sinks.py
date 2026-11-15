import logging
import os
import tempfile
from contextlib import closing
from typing import Any

from fastapiobserver import ObservabilitySettings
from fastapiobserver.sinks import (
    RotatingFileSink,
    StdoutSink,
    build_sink_handlers,
    get_registered_sinks,
    register_sink,
    unregister_sink,
)

def test_stdout_sink() -> None:
    formatter = logging.Formatter()
    sink = StdoutSink()
    assert sink.name == "stdout"
    handler = sink.create_handler(formatter)
    assert isinstance(handler, logging.StreamHandler)
    assert handler.formatter is formatter

def test_rotating_file_sink() -> None:
    with tempfile.TemporaryDirectory() as d:
        formatter = logging.Formatter()
        sink = RotatingFileSink(log_dir=d, filename="test.log", max_bytes=100, backup_count=2)
        assert sink.name == "rotating_file"

        with closing(sink.create_handler(formatter)) as handler:
            assert isinstance(handler, logging.handlers.RotatingFileHandler)
            assert handler.formatter is formatter
            assert handler.baseFilename == os.path.join(d, "test.log")

def test_sink_registry_is_keyed_by_name() -> None:
    sink = StdoutSink()
    register_sink(sink)
    register_sink(StdoutSink())

    assert list(get_registered_sinks()) == ["stdout"]
    unregister_sink("stdout")
    assert get_registered_sinks() == {}


def test_build_sink_handlers_adds_registered_sinks_to_the_configured_ones(tmp_path) -> None:
    formatter = logging.Formatter()
    settings = ObservabilitySettings(app_name="app", service="svc", environment="env")

    [(handler, name)] = build_sink_handlers(settings, formatter)
    assert isinstance(handler, logging.StreamHandler)
    assert name == "stdout"

    register_sink(StdoutSink())
    assert [n for _, n in build_sink_handlers(settings, formatter)] == ["stdout", "stdout"]

    with_files = ObservabilitySettings(app_name="app", service="svc", log_dir=str(tmp_path))
    handlers = build_sink_handlers(with_files, formatter)
    try:
        assert "rotating_file" in [n for _, n in handlers]
    finally:
        for handler, _ in handlers:
            handler.close()


def test_retired_settings_are_reported_with_their_replacements(
    monkeypatch: Any, caplog: Any
) -> None:
    from fastapi import FastAPI

    from fastapiobserver import install_observability

    monkeypatch.setenv("OBS_LOG_SINKS", "acme")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("METRICS_ENABLED", "true")
    monkeypatch.setenv("OBS_METRICS_ENABLED", "true")  # replacement set: not reported
    with caplog.at_level(logging.WARNING, logger="fastapiobserver.fastapi"):
        install_observability(FastAPI(), ObservabilitySettings(), metrics_enabled=False)

    [record] = [r for r in caplog.records if r.getMessage() == "observability.settings_retired"]
    assert record.event["replacements"] == {
        "OBS_LOG_SINKS": "register_sink() in code",
        "LOG_LEVEL": "OBS_LOG_LEVEL",
    }
