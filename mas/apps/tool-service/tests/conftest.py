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

    async def fake_orch_get(path, params=None, *, context=None):
        """Return plausible stub data for GET requests."""
        return {"id": str(uuid4()), "status": "ok", "state": "INTAKE", "items": []}

    async def fake_orch_post(path, body=None, *, context=None, principal="service"):
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

    try:
        import tool_service.tools.flow as flow_mod

        monkeypatch.setattr(flow_mod, "orch_get", fake_orch_get)
        monkeypatch.setattr(flow_mod, "orch_post", fake_orch_post)
    except (ImportError, ModuleNotFoundError):
        pass

    try:
        import tool_service.tools.capability as capability_mod

        async def fake_capability_get(path, params=None):
            return [
                {
                    "id": str(uuid4()),
                    "name": "stub-worker",
                    "status": params.get("status", "ACTIVE") if params else "ACTIVE",
                    "capability_names": ["stub.capability"],
                    "required_tools": ["web_search"],
                }
            ]

        async def fake_capability_post(path, body=None):
            if path == "/capabilities/search":
                return [
                    {
                        "id": str(uuid4()),
                        "name": "stub-worker",
                        "status": "ACTIVE",
                        "capability_names": [body.get("name", "stub.capability")],
                        "required_tools": ["web_search"],
                    }
                ]
            return {
                "id": str(uuid4()),
                "name": (body or {}).get("name", "stub-worker"),
                "status": "ACTIVE",
                "capability_names": (body or {}).get("capability_names", []),
                "required_tools": (body or {}).get("required_tools", []),
            }

        async def fake_capability_delete(path):
            return {"status": "deregistered"}

        monkeypatch.setattr(capability_mod, "orch_get", fake_capability_get)
        monkeypatch.setattr(capability_mod, "orch_post", fake_capability_post)
        monkeypatch.setattr(capability_mod, "orch_delete", fake_capability_delete)
    except (ImportError, ModuleNotFoundError):
        pass

    try:
        import tool_service.tools.web as web_mod

        async def fake_web_search_execute(self, **kwargs):
            return {
                "results": [{"title": kwargs.get("query", "test"), "url": "https://example.com"}]
            }

        async def fake_web_fetch_execute(self, **kwargs):
            return {"content": "ok", "url": kwargs.get("url", "https://example.com")}

        monkeypatch.setattr(web_mod.WebSearchTool, "execute", fake_web_search_execute)
        monkeypatch.setattr(web_mod.WebFetchTool, "execute", fake_web_fetch_execute)
    except (ImportError, ModuleNotFoundError):
        pass

    try:
        import tool_service.tools.file as file_mod

        async def fake_file_read_execute(self, **kwargs):
            return {"path": kwargs.get("path", "test.txt"), "content": "stub"}

        async def fake_file_write_execute(self, **kwargs):
            return {"path": kwargs.get("path", "test.txt"), "written": True}

        monkeypatch.setattr(file_mod.FileReadTool, "execute", fake_file_read_execute)
        monkeypatch.setattr(file_mod.FileWriteTool, "execute", fake_file_write_execute)
    except (ImportError, ModuleNotFoundError):
        pass

    try:
        import tool_service.tools.infra as infra_mod

        async def fake_blob_download_execute(self, **kwargs):
            return {
                "bucket": kwargs.get("bucket", "test"),
                "key": kwargs.get("key", "file.txt"),
                "content": "stub",
            }

        async def fake_blob_upload_execute(self, **kwargs):
            return {
                "bucket": kwargs.get("bucket", "test"),
                "key": kwargs.get("key", "file.txt"),
                "uploaded": True,
            }

        async def fake_blob_list_execute(self, **kwargs):
            return {"items": []}

        monkeypatch.setattr(infra_mod.BlobDownloadTool, "execute", fake_blob_download_execute)
        monkeypatch.setattr(infra_mod.BlobUploadTool, "execute", fake_blob_upload_execute)
        monkeypatch.setattr(infra_mod.BlobListTool, "execute", fake_blob_list_execute)
    except (ImportError, ModuleNotFoundError):
        pass


@pytest.fixture
async def client(monkeypatch):
    """Async httpx test client against the FastAPI app.

    We use the lifespan context manager to ensure app.state is populated
    (registry, cache, etc.) before running requests.
    """
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")

    from tool_service.config import get_settings
    from tool_service.main import app

    monkeypatch.setenv("TOOL_SECRET", "test-tool-secret")
    get_settings.cache_clear()
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.tool_secret}"}

    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
                headers=headers,
            ) as ac,
        ):
            yield ac
    finally:
        get_settings.cache_clear()


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
