from __future__ import annotations

import pytest

from fastapiobserver.utils import lazy_import, normalize_protocol, parse_csv


def test_parse_csv_returns_default_when_value_missing() -> None:
    assert parse_csv(None, default=("a", "b")) == ("a", "b")


def test_parse_csv_optional_supports_nullish_markers() -> None:
    assert parse_csv("none", optional=True) is None


def test_parse_csv_optional_keeps_explicit_empty_tuple() -> None:
    assert parse_csv("", optional=True) == ()


def test_normalize_protocol_strict_raises_for_invalid_value() -> None:
    with pytest.raises(ValueError, match="Invalid protocol"):
        normalize_protocol("udp", allowed={"grpc", "http/protobuf"}, strict=True)


def test_normalize_protocol_non_strict_uses_default() -> None:
    assert (
        normalize_protocol(
            "udp",
            allowed={"grpc", "http/protobuf"},
            default="grpc",
            strict=False,
        )
        == "grpc"
    )


def test_lazy_import_missing_dependency_raises_runtime_error_with_hint() -> None:
    with pytest.raises(RuntimeError, match=r"fastapi-observer\[otel\]"):
        lazy_import(
            "this_module_does_not_exist_for_fastapiobserver_tests",
            package_hint="fastapi-observer[otel]",
        )
