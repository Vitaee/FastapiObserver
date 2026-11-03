"""Run the documented requests in fresh processes so global telemetry stays isolated."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("module", "requests"),
    [
        ("basic_app", [["GET", "/items/42", None, 200, {"item_id": 42}]]),
        ("allowlist_app", [["GET", "/users/42", None, 200, {"user_id": 42}]]),
        ("security_presets_app", [["POST", "/payments", {"amount": 100}, 200, {"amount": "100"}]]),
        ("otel_app", [["GET", "/orders/42", None, 200, {"order_id": 42}]]),
        ("graphql_app", [["POST", "/graphql", {"query": "{ hello }"}, 200, None]]),
        (
            "db_tracing_app",
            [
                ["POST", "/users/ada", None, 200, {"id": 1, "username": "ada"}],
                ["POST", "/users/ada", None, 409, {"detail": "Username already exists"}],
                ["GET", "/users", None, 200, [{"id": 1, "username": "ada"}]],
            ],
        ),
    ],
)
def test_standalone_example_requests(module: str, requests: list) -> None:
    script = r"""
import importlib, json, sys
from fastapi.testclient import TestClient
from fastapiobserver.logging import shutdown_logging

app = importlib.import_module("examples." + sys.argv[1]).app
with TestClient(app) as client:
    for method, path, payload, expected_status, expected in json.loads(sys.argv[2]):
        response = client.request(method, path, json=payload)
        assert response.status_code == expected_status, response.text
        data = response.json()
        if isinstance(expected, dict):
            assert expected.items() <= data.items(), data
        elif expected is not None:
            assert data == expected, data
        else:
            assert data.get("data", {}).get("hello"), data
shutdown_logging()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, module, json.dumps(requests)],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "OBS_OTEL_ENABLED": "false",
            "OBS_OTEL_LOGS_ENABLED": "false",
            "OBS_OTEL_METRICS_ENABLED": "false",
            "OBS_RUNTIME_CONTROL_ENABLED": "false",
            "OTEL_SDK_DISABLED": "true",
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    # SQL echo and other plain-text handlers must not bypass the JSON pipeline.
    for line in result.stdout.splitlines():
        if line.strip():
            assert isinstance(json.loads(line), dict), line
