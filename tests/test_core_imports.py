"""Check separation in a fresh interpreter, including forbidden import attempts."""

import os
import json
from pathlib import Path
import subprocess
import sys

import fastapiobserver


def test_experimental_operation_never_imports_frameworks_or_optional_clients():
    script = """
import importlib.abc, logging, sys, threading
class Forbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fastapi', 'starlette', 'sqlalchemy', 'redis',
                                      'pymongo', 'opentelemetry', 'httpx', 'requests'}:
            raise AssertionError('Unexpected import: ' + fullname)
sys.meta_path.insert(0, Forbidden())
handlers = list(logging.getLogger().handlers)
threads = set(threading.enumerate())
from fastapiobserver.experimental import observe_operation
from fastapiobserver import ObservabilitySettings, StructuredJsonFormatter
with observe_operation('plain.python'):
    pass
assert handlers == logging.getLogger().handlers
assert threads == set(threading.enumerate())
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(fastapiobserver.__file__).resolve().parents[1])},
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_python_operation_example_uses_real_sql_and_loopback_http_without_frameworks():
    root = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc, runpy, sys
class Forbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fastapi', 'starlette'}:
            raise AssertionError('Unexpected import: ' + fullname)
sys.meta_path.insert(0, Forbidden())
runpy.run_path(sys.argv[1], run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root / "examples/python_operation.py")],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(fastapiobserver.__file__).resolve().parents[1]),
             "OTEL_SDK_DISABLED": "true"},
    )
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    [terminal] = [r for r in records if r["message"] == "operation.completed"]
    assert terminal["event"]["db"]["count"] == 2
    assert terminal["event"]["http"]["count"] == 1
    [application] = [r for r in records if r["message"] == "catalog.imported"]
    assert application["request_id"] == terminal["request_id"]


def test_custom_client_callback_example_without_optional_integrations():
    root = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc, runpy, sys
class Forbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fastapi','starlette','sqlalchemy','opentelemetry'}:
            raise AssertionError('Unexpected import: ' + fullname)
sys.meta_path.insert(0, Forbidden())
runpy.run_path(sys.argv[1], run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root / "examples/callback_operation.py")],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(fastapiobserver.__file__).resolve().parents[1])},
    )
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    [terminal] = [r for r in records if r["message"] == "operation.completed"]
    assert terminal["event"]["db"]["count"] == 1
    assert terminal["event"]["db"]["ms"] >= 0
    [application] = [r for r in records if r["message"] == "catalog.read"]
    assert application["event"]["value"] == 42
