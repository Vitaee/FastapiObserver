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
