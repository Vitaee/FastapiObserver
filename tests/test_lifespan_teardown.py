from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapiobserver import install_observability, ObservabilitySettings, observability_lifespan
import fastapiobserver.fastapi as fastapi_module
import asyncio

def test_auto_lifespan_teardown_runs_on_exception(monkeypatch):
    """Test that observability_lifespan teardown runs even if the underlying app lifespan fails."""
    app = FastAPI()
    settings = ObservabilitySettings()
    
    calls = []
    def mock_teardown(app_inst):
        calls.append(1)
        
    monkeypatch.setattr(fastapi_module, "_teardown_observability", mock_teardown)
    
    @asynccontextmanager
    async def failing_lifespan(app_inst):
        raise RuntimeError("Something failed during startup!")
        yield
        
    app.router.lifespan_context = failing_lifespan
    install_observability(app, settings, metrics_enabled=False)
    
    async def _run():
        try:
            async with app.router.lifespan_context(app):
                pass
        except RuntimeError:
            pass
            
    asyncio.run(_run())
    assert len(calls) == 1

def test_manual_lifespan_teardown_runs_on_exception(monkeypatch):
    app = FastAPI()
    
    calls = []
    def mock_teardown(app_inst):
        calls.append(1)
        
    monkeypatch.setattr(fastapi_module, "_teardown_observability", mock_teardown)
    
    async def _run():
        try:
            async with observability_lifespan(app):
                raise RuntimeError("Failed explicitly")
        except RuntimeError:
            pass
            
    asyncio.run(_run())
    assert len(calls) == 1


def test_teardown_handlers_chained_errors() -> None:
    from fastapiobserver.logging import setup as logging_setup
    import fastapiobserver.logging.state as logging_state
    import pytest

    class BrokenListener:
        def stop(self) -> None:
            raise ValueError("Listener stop broke")

    class BrokenHandler:
        def close(self) -> None:
            raise OSError("Handler close broke")

    orig_listener = logging_state._QUEUE_LISTENER
    orig_handlers = list(logging_state._MANAGED_OUTPUT_HANDLERS)

    try:
        logging_state._QUEUE_LISTENER = BrokenListener()  # type: ignore[assignment]
        logging_state._MANAGED_OUTPUT_HANDLERS[:] = [BrokenHandler(), BrokenHandler()]  # type: ignore[list-item]

        with pytest.raises(RuntimeError) as exc_info:
            logging_setup._teardown_handlers_locked(suppress_errors=False)

        assert "Multiple errors" in str(exc_info.value)
        assert "ValueError" in str(exc_info.value)
        assert "OSError" in str(exc_info.value)
        assert exc_info.value.__cause__ is not None
    finally:
        logging_state._QUEUE_LISTENER = orig_listener
        logging_state._MANAGED_OUTPUT_HANDLERS[:] = orig_handlers


def test_teardown_handlers_single_error() -> None:
    from fastapiobserver.logging import setup as logging_setup
    import fastapiobserver.logging.state as logging_state
    import pytest

    class BrokenListener:
        def stop(self) -> None:
            raise ValueError("Listener stop broke exclusively")

    orig_listener = logging_state._QUEUE_LISTENER
    orig_handlers = list(logging_state._MANAGED_OUTPUT_HANDLERS)

    try:
        logging_state._QUEUE_LISTENER = BrokenListener()  # type: ignore[assignment]
        logging_state._MANAGED_OUTPUT_HANDLERS[:] = []

        with pytest.raises(ValueError, match="Listener stop broke exclusively"):
            logging_setup._teardown_handlers_locked(suppress_errors=False)

    finally:
        logging_state._QUEUE_LISTENER = orig_listener
        logging_state._MANAGED_OUTPUT_HANDLERS[:] = orig_handlers
