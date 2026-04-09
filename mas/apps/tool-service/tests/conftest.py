"""
Conftest for tool-service tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the tool-service package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Restrict anyio to asyncio backend (aiolimiter is asyncio-only) ────────────
@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


# ── Auto-mock orchestrator HTTP helpers so tools don't hit the network ────────
@pytest.fixture(autouse=True)
def _mock_orchestrator_http(monkeypatch):
    """Patch _orch_get and _orch_post in tool modules so no real HTTP occurs.

    Returns a canned success dict that satisfies the tool assertions.
    Only patches if the tool modules are importable (they depend on mas_tools_sdk
    which may not be on sys.path when running tests in isolation).
    """
    from uuid import uuid4

    async def fake_orch_get(path, params=None):
        """Return plausible stub data for GET requests."""
        return {"id": str(uuid4()), "status": "ok", "state": "INTAKE", "items": []}

    async def fake_orch_post(path, body=None):
        """Return plausible stub data for POST requests."""
        return {
            "id": str(uuid4()),
            "status": "created",
            "state": "INTAKE",
            "task_id": str(uuid4()),
        }

    # Patch both tool modules that make orchestrator HTTP calls.
    # Guard with try/except so tests still run if the tool modules
    # cannot be imported (e.g. when mas_tools_sdk is not on sys.path).
    try:
        import tool_service.tools.project as proj_mod

        monkeypatch.setattr(proj_mod, "orch_get", fake_orch_get)
        monkeypatch.setattr(proj_mod, "orch_post", fake_orch_post)
    except (ImportError, ModuleNotFoundError):
        pass

    try:
        import tool_service.tools.sprint_kpi as sprint_mod

        monkeypatch.setattr(sprint_mod, "orch_get", fake_orch_get)
        monkeypatch.setattr(sprint_mod, "orch_post", fake_orch_post)
    except (ImportError, ModuleNotFoundError):
        pass


@pytest.fixture
async def client():
    """Async httpx test client against the FastAPI app.

    We use the lifespan context manager to ensure app.state is populated
    (registry, cache, etc.) before running requests.
    """
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")

    from tool_service.main import app

    async with app.router.lifespan_context(app), httpx.AsyncClient(
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
