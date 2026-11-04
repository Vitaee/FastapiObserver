"""Keep released interfaces and representative FastAPI output unchanged."""

import importlib.util
import json
from pathlib import Path


def test_released_public_interface_and_fastapi_output():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "compatibility_probe", root / "scripts" / "compatibility_probe.py"
    )
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    expected = json.loads((root / "tests/fixtures/compatibility-2.2.1.json").read_text())
    assert probe.snapshot() == expected
