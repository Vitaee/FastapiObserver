from __future__ import annotations

import asyncio
import typing
import pytest
from unittest.mock import MagicMock, patch

from fastapiobserver.integrations.strawberry import StrawberryObservabilityExtension
from fastapiobserver.request_context import get_user_context, set_user_context, clear_user_context


@pytest.fixture(autouse=True)
def clean_context() -> None:
    clear_user_context()


def _drive_on_operation(
    extension: StrawberryObservabilityExtension,
) -> dict[str, object] | None:
    async def _run() -> dict[str, object] | None:
        generator = extension.on_operation()
        await generator.__anext__()
        context = get_user_context()
        await generator.aclose()
        return context

    return asyncio.run(_run())


def test_strawberry_extension_injects_operation_name_into_log_context() -> None:
    # 1. Setup simulated execution context (Duck Typing Strawberry)
    mock_context = MagicMock()
    mock_context.operation_name = "GetUsersQuery"
    mock_context.query = "{ users { id name } }"

    # 2. Add an existing user context to ensure it merges safely
    set_user_context({"tenant": "acme"})

    # 3. Init our extension
    extension = StrawberryObservabilityExtension(execution_context=mock_context)

    # 4. Run the async generator hook
    final_context = _drive_on_operation(extension)

    # 5. Assert the observability context updated correctly
    assert final_context is not None
    assert final_context["tenant"] == "acme"
    assert "graphql" in final_context
    graphql_info = typing.cast(dict[str, str], final_context["graphql"])
    assert graphql_info["operation_name"] == "GetUsersQuery"


def test_strawberry_extension_handles_missing_operation_name() -> None:
    mock_context = MagicMock()
    # E.g. a malformed query where Strawberry couldn't parse the operation
    mock_context.operation_name = None

    extension = StrawberryObservabilityExtension(execution_context=mock_context)
    final_context = _drive_on_operation(extension)

    assert final_context is not None
    graphql_info = typing.cast(dict[str, str], final_context["graphql"])
    assert graphql_info["operation_name"] == "AnonymousOperation"


@patch("fastapiobserver.integrations.strawberry.get_user_context")
def test_strawberry_extension_swallows_parsing_exceptions(mock_get: MagicMock) -> None:
    mock_context = MagicMock()
    # Force an exception on property access
    type(mock_context).operation_name = property(
        lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    extension = StrawberryObservabilityExtension(execution_context=mock_context)
    _drive_on_operation(extension)

    # It shouldn't crash. It should just gracefully yield and mark it anonymous.
    mock_get.assert_called_once()
