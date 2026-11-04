"""Completion must retain its start owner instead of using the current context."""

from concurrent.futures import ThreadPoolExecutor
import logging

import pytest

pytest.importorskip("opentelemetry.sdk.trace")

from opentelemetry.sdk.trace import TracerProvider
import opentelemetry.sdk.trace as trace_sdk
from opentelemetry import trace
from opentelemetry.trace import SpanKind

from fastapiobserver.experimental import observe_operation
from fastapiobserver.otel.tracing import _request_call_totals


def provider():
    value = TracerProvider()
    value.add_span_processor(_request_call_totals(trace, trace_sdk))
    return value


def terminal(caplog, name):
    return next(r.event for r in caplog.records
                if r.msg.startswith("operation.") and r.event["name"] == name)


def test_span_started_without_an_owner_cannot_join_a_later_operation(caplog):
    caplog.set_level(logging.INFO)
    sdk = provider()
    try:
        span = sdk.get_tracer("custom.client").start_span(
            "lookup", kind=SpanKind.CLIENT, attributes={"db.system": "redis"},
        )
        with observe_operation("unrelated"):
            span.end()
        assert "db" not in terminal(caplog, "unrelated")
    finally:
        sdk.shutdown()


def test_sampled_span_completed_in_another_operation_keeps_its_owner(caplog):
    caplog.set_level(logging.INFO)
    sdk = provider()
    try:
        with observe_operation("origin"):
            span = sdk.get_tracer("custom.client").start_span(
                "lookup", kind=SpanKind.CLIENT, attributes={"db.system": "redis"},
            )
            with observe_operation("other"):
                span.end()
        assert terminal(caplog, "origin")["db"]["count"] == 1
        assert "db" not in terminal(caplog, "other")
    finally:
        sdk.shutdown()


def test_late_child_span_cannot_be_credited_to_parent(caplog):
    caplog.set_level(logging.INFO)
    sdk = provider()
    try:
        with observe_operation("parent"):
            with observe_operation("child"):
                span = sdk.get_tracer("custom.client").start_span(
                    "lookup", kind=SpanKind.CLIENT, attributes={"db.system": "redis"},
                )
            span.end()
        assert "db" not in terminal(caplog, "parent")
        assert "db" not in terminal(caplog, "child")
    finally:
        sdk.shutdown()


def test_sampled_span_can_complete_on_a_thread_without_copied_context(caplog):
    caplog.set_level(logging.INFO)
    sdk = provider()
    try:
        with observe_operation("origin"):
            span = sdk.get_tracer("custom.client").start_span(
                "lookup", kind=SpanKind.CLIENT, attributes={"db.system": "redis"},
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(span.end).result()
        assert terminal(caplog, "origin")["db"]["count"] == 1
    finally:
        sdk.shutdown()


def test_another_active_http_client_does_not_suppress_the_origin_span(caplog):
    from fastapiobserver.http_calls import _ACTIVE_CLIENT

    caplog.set_level(logging.INFO)
    sdk = provider()
    try:
        with observe_operation("origin"):
            span = sdk.get_tracer("custom.client").start_span(
                "GET", kind=SpanKind.CLIENT, attributes={"http.request.method": "GET"},
            )
            with observe_operation("other"):
                token = _ACTIVE_CLIENT.set(object())
                try:
                    span.end()
                finally:
                    _ACTIVE_CLIENT.reset(token)
        assert terminal(caplog, "origin")["http"]["count"] == 1
        assert "http" not in terminal(caplog, "other")
    finally:
        sdk.shutdown()


def test_native_http_deduplication_retains_start_context_when_span_ends_elsewhere(caplog):
    from fastapiobserver.http_calls import _ACTIVE_CLIENT

    caplog.set_level(logging.INFO)
    sdk = provider()
    try:
        with observe_operation("origin") as operation:
            token = _ACTIVE_CLIENT.set(object())
            try:
                span = sdk.get_tracer("custom.client").start_span(
                    "GET", kind=SpanKind.CLIENT, attributes={"http.request.method": "GET"},
                )
                operation.record_call("http", 0.001)
            finally:
                _ACTIVE_CLIENT.reset(token)
            with observe_operation("other"):
                span.end()
        assert terminal(caplog, "origin")["http"]["count"] == 1
        assert "http" not in terminal(caplog, "other")
    finally:
        sdk.shutdown()
