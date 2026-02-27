"""
Conftest for tool-service tests.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Ensure the tool-service package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
async def client():
    """Async httpx test client against the FastAPI app.

    We use the lifespan context manager to ensure app.state is populated
    (registry, cache, etc.) before running requests.
    """
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")

    from tool_service.main import app

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as ac:
            yield ac


@pytest.fixture
def make_registry():
    """Factory that builds a ToolRegistry with no Redis (cache=None)."""
    from tool_service.config import Settings
    from tool_service.rate_limiter import RateLimiterPool
    from tool_service.registry import ToolRegistry
    from tool_service.tools.all_tools import get_all_tools

    def _factory(*, cache=None):
        settings = Settings()
        registry = ToolRegistry(settings, cache=cache, rate_limiter=RateLimiterPool())
        registry.register_all(get_all_tools())
        return registry

    return _factory

