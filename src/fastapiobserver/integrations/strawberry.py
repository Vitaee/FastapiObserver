"""Strawberry GraphQL integration for fastapi-observer.

This module provides a `StrawberryObservabilityExtension` that automatically
extracts the `operationName` from incoming GraphQL requests and injects it
into the active `fastapiobserver` telemetry context, as well as setting the
OpenTelemetry span name if active.

It does NOT require `strawberry-graphql` as a hard dependency. It uses Duck Typing.
"""

from __future__ import annotations

import logging
from typing import Any

from ..request_context import get_user_context, set_user_context

_LOGGER = logging.getLogger("fastapiobserver.integrations.strawberry")


class StrawberryObservabilityExtension:
    """Strawberry SchemaExtension for injecting operation names into logs/traces.

    Usage:
        import strawberry
        from fastapiobserver.integrations.strawberry import StrawberryObservabilityExtension

        schema = strawberry.Schema(
            query=Query,
            extensions=[StrawberryObservabilityExtension],
        )
    """

    def __init__(self, *, execution_context: Any = None) -> None:
        self.execution_context = execution_context

    async def on_operation(self) -> Any:
        # Note: In Strawberry extensions, `on_operation` is an async generator.
        # We intercept before the operation runs, yield to allow execution, then clean up.

        op_name = "AnonymousOperation"

        try:
            if self.execution_context:
                op_name = self.execution_context.operation_name or op_name
        except Exception:
            _LOGGER.debug(
                "strawberry_extension.parse_failed",
                exc_info=True,
                extra={"_skip_enrichers": True},
            )

        # 1. Update the logging context so StructuredJsonFormatter sees it
        context = get_user_context() or {}
        graphql_data = context.get("graphql", {})
        if isinstance(graphql_data, dict):
            graphql_data["operation_name"] = op_name
            context["graphql"] = graphql_data
            set_user_context(context)

        # 2. Update OpenTelemetry Trace if active
        try:
            from opentelemetry import trace as otel_trace  # type: ignore

            span = otel_trace.get_current_span()
            if span and span.is_recording():
                span.update_name(f"graphql.operation.{op_name}")
                span.set_attribute("graphql.operation.name", op_name)
        except ImportError:
            pass  # OTel not installed
        except Exception:
            _LOGGER.debug(
                "strawberry_extension.otel_failed",
                exc_info=True,
                extra={"_skip_enrichers": True},
            )

        yield  # Allow Strawberry to execute the actual GraphQL resolvers
