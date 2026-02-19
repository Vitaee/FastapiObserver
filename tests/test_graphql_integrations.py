from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from fastapiobserver.integrations.strawberry import StrawberryObservabilityExtension
from fastapiobserver.request_context import get_user_context, set_user_context, clear_user_context


@pytest.fixture(autouse=True)
def clean_context() -> None:
    clear_user_context()


@pytest.mark.asyncio
async def test_strawberry_extension_injects_operation_name_into_log_context() -> None:
    # 1. Setup simulated execution context (Duck Typing Strawberry)
    mock_context = MagicMock()
    mock_context.operation_name = "GetUsersQuery"
    mock_context.query = "{ users { id name } }"

    # 2. Add an existing user context to ensure it merges safely
    set_user_context({"tenant": "acme"})

    # 3. Init our extension
    extension = StrawberryObservabilityExtension(execution_context=mock_context)

    # 4. Run the async generator hook
    generator = extension.on_operation()
    await generator.__anext__()

    # 5. Assert the observability context updated correctly
    final_context = get_user_context()
    assert final_context is not None
    assert final_context["tenant"] == "acme"
    assert "graphql" in final_context
    assert final_context["graphql"]["operation_name"] == "GetUsersQuery"


@pytest.mark.asyncio
async def test_strawberry_extension_handles_missing_operation_name() -> None:
    mock_context = MagicMock()
    # E.g. a malformed query where Strawberry couldn't parse the operation
    mock_context.operation_name = None 

    extension = StrawberryObservabilityExtension(execution_context=mock_context)
    generator = extension.on_operation()
    await generator.__anext__()

    final_context = get_user_context()
    assert final_context is not None
    assert final_context["graphql"]["operation_name"] == "AnonymousOperation"


@pytest.mark.asyncio
@patch("fastapiobserver.integrations.strawberry.get_user_context")
async def test_strawberry_extension_swallows_parsing_exceptions(mock_get: MagicMock) -> None:
    mock_context = MagicMock()
    # Force an exception on property access
    type(mock_context).operation_name = property(
        lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    
    extension = StrawberryObservabilityExtension(execution_context=mock_context)
    generator = extension.on_operation()
    await generator.__anext__()
    
    # It shouldn't crash. It should just gracefully yield and mark it anonymous.
    mock_get.assert_called_once()
