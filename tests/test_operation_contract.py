"""Structural JSON vectors complement the real recording lifecycle checks."""

import asyncio
import json
import logging
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from fastapiobserver.experimental import observe_operation

ROOT = Path(__file__).resolve().parents[1]


def validator():
    schema = json.loads((ROOT / "spec/operation-event.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_operation_schema_accepts_positive_and_rejects_negative_vectors():
    vectors = json.loads((ROOT / "spec/fixtures/operation-events.json").read_text())
    check = validator()
    for event in vectors["valid"]:
        check.validate(json.loads(json.dumps(event, allow_nan=False)))
    for event in vectors["invalid"]:
        assert list(check.iter_errors(event)), event


@pytest.mark.parametrize("error", [None, ValueError("private"), asyncio.CancelledError()])
def test_emitted_operation_events_match_the_draft_contract(error, caplog):
    caplog.set_level(logging.INFO)
    try:
        with observe_operation("catalog.import") as operation:
            operation.record_call("db", 0.001)
            operation.record_call("http", 0.002, error="read_timeout")
            if error is not None:
                raise error
    except BaseException as caught:
        assert caught is error
    [record] = [r for r in caplog.records if r.msg.startswith("operation.")]
    event = json.loads(json.dumps(record.event, allow_nan=False))
    validator().validate(event)
    assert event["db"]["count"] == event["http"]["count"] == 1
    assert "private" not in json.dumps(event)
