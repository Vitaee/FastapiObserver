"""Check separation in a fresh interpreter, including forbidden import attempts."""

import json
from pathlib import Path

import pytest

from tests.helpers import RUN_EXAMPLE, run_python

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_experimental_operation_never_imports_frameworks_or_optional_clients():
    result = run_python(
        """
import logging, threading
handlers = list(logging.getLogger().handlers)
threads = set(threading.enumerate())
from fastapiobserver.experimental import observe_operation
from fastapiobserver import ObservabilitySettings, StructuredJsonFormatter
with observe_operation('plain.python'):
    pass
assert handlers == logging.getLogger().handlers
assert threads == set(threading.enumerate())
""",
        forbidden=("fastapi", "starlette", "sqlalchemy", "redis", "pymongo", "opentelemetry",
                   "httpx", "requests"),
    )
    assert result.returncode == 0, result.stderr


def test_python_operation_example_uses_real_sql_and_loopback_http_without_frameworks():
    result = run_python(
        RUN_EXAMPLE, str(EXAMPLES / "python_operation.py"),
        forbidden=("fastapi", "starlette"), OTEL_SDK_DISABLED="true",
    )
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    [terminal] = [r for r in records if r["message"] == "operation.completed"]
    assert terminal["event"]["db"]["count"] == 2
    assert terminal["event"]["http"]["count"] == 1
    [application] = [r for r in records if r["message"] == "catalog.imported"]
    assert application["request_id"] == terminal["request_id"]


def test_custom_client_callback_example_without_optional_integrations():
    result = run_python(
        RUN_EXAMPLE, str(EXAMPLES / "callback_operation.py"),
        forbidden=("fastapi", "starlette", "sqlalchemy", "opentelemetry"),
    )
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    [terminal] = [r for r in records if r["message"] == "operation.completed"]
    assert terminal["event"]["db"]["count"] == 1
    assert terminal["event"]["db"]["ms"] >= 0
    [application] = [r for r in records if r["message"] == "catalog.read"]
    assert application["event"]["value"] == 42


def test_unknown_package_attribute_raises_attribute_error():
    import fastapiobserver

    with pytest.raises(AttributeError, match="no attribute 'not_an_export'"):
        fastapiobserver.not_an_export
