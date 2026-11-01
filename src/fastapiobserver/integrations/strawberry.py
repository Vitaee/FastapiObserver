"""Strawberry GraphQL integration for fastapi-observer.

``StrawberryObservabilityExtension`` puts the GraphQL operation name into the
request's user context, so every log line of the request carries it, and names
the request's OpenTelemetry server span after it.

Strawberry stays an optional dependency: it's imported only when this module is.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from strawberry.extensions import SchemaExtension

from ..request_context import get_request_state, get_user_context, set_user_context

_LOGGER = logging.getLogger("fastapiobserver.integrations.strawberry")


class StrawberryObservabilityExtension(SchemaExtension):
    """Strawberry schema extension that adds the operation name to logs and traces.

    Usage:
        import strawberry
        from fastapiobserver.integrations.strawberry import StrawberryObservabilityExtension

        schema = strawberry.Schema(
            query=Query,
            extensions=[StrawberryObservabilityExtension],
        )
    """

    def on_execute(self) -> Iterator[None]:
        # After parsing, so a named operation is found without an operationName.
        op_name = self.execution_context.operation_name or "AnonymousOperation"

        context = get_user_context() or {}
        graphql_data = context.get("graphql", {})
        if isinstance(graphql_data, dict):
            graphql_data["operation_name"] = op_name
            context["graphql"] = graphql_data
            set_user_context(context)

        try:
            from opentelemetry import trace as otel_trace

            # The request's server span, which trace lists show. On FastAPI 0.142+
            # the current span here is FastAPI's endpoint span inside it.
            state = get_request_state()
            span = (
                state.span
                if state is not None and state.span is not None
                else otel_trace.get_current_span()
            )
            if span.is_recording():
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

        yield
