from __future__ import annotations

import logging

import pytest

from fastapiobserver.plugins import clear_plugins
from fastapiobserver.request_context import (
    clear_request_id,
    clear_span_id,
    clear_trace_id,
    clear_user_context,
)


@pytest.fixture(autouse=True)
def reset_global_state() -> None:
    root = logging.getLogger()
    original_level = root.level

    clear_plugins()
    clear_request_id()
    clear_trace_id()
    clear_span_id()
    clear_user_context()
    yield
    clear_plugins()
    clear_request_id()
    clear_trace_id()
    clear_span_id()
    clear_user_context()
    root.setLevel(original_level)
