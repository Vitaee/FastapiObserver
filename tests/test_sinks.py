import logging
import os
import tempfile
from typing import Any

from fastapiobserver import ObservabilitySettings
from fastapiobserver.sinks import (
    RotatingFileSink,
    StdoutSink,
    build_sink_handlers,
    clear_sinks,
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

        handler = sink.create_handler(formatter)
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.formatter is formatter
        assert handler.baseFilename == os.path.join(d, "test.log")

def test_sink_registry() -> None:
    clear_sinks()

    class DummySink:
        @property
        def name(self) -> str:
            return "dummy"

        def create_handler(self, formatter: logging.Formatter) -> logging.Handler:
            return logging.NullHandler()

    sink = DummySink()
    register_sink(sink)  # type: ignore

    sinks = get_registered_sinks()
    assert "dummy" in sinks
    assert sinks["dummy"] is sink

    unregister_sink("dummy")
    assert "dummy" not in get_registered_sinks()

def test_build_sink_handlers_creates_configured_sinks() -> None:
    clear_sinks()

    with tempfile.TemporaryDirectory() as d:
        settings = ObservabilitySettings(
            app_name="test",
            service="test",
            environment="test",
            log_dir=d,
        )
        formatter = logging.Formatter()

        handlers_with_names = build_sink_handlers(settings, formatter)

        names = [n for _, n in handlers_with_names]
        assert "stdout" in names
        assert "rotating_file" in names

def test_sinks_registry_contract() -> None:
    from fastapiobserver.sinks import register_sink, get_registered_sinks, clear_sinks, StdoutSink

    clear_sinks()
    assert len(get_registered_sinks()) == 0

    register_sink(StdoutSink())
    assert len(get_registered_sinks()) == 1
    assert "stdout" in get_registered_sinks()

    # idempotent
    register_sink(StdoutSink())
    assert len(get_registered_sinks()) == 1

def test_sinks_build_handlers_contract() -> None:
    from fastapiobserver.sinks import (
        StdoutSink,
        build_sink_handlers,
        clear_sinks,
        register_sink,
    )
    from fastapiobserver.config import ObservabilitySettings
    import logging

    settings = ObservabilitySettings(app_name="app", service="svc", environment="env")
    formatter = logging.Formatter()

    clear_sinks()
    handlers = build_sink_handlers(settings, formatter)
    assert len(handlers) == 1
    assert isinstance(handlers[0][0], logging.StreamHandler)
    assert handlers[0][1] == "stdout"

    clear_sinks()
    register_sink(StdoutSink())
    handlers = build_sink_handlers(settings, formatter)
    # The factory merges registered sinks with builtin sinks.
    # Both StdoutSink (builtin) and registered StdoutSink will be in the list
    assert len(handlers) == 2
    assert handlers[0][1] == "stdout"
    assert handlers[1][1] == "stdout"

    assert handlers[0][1] == "stdout"


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
