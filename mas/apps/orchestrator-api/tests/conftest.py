"""
Conftest for orchestrator-api tests.
"""
from pathlib import Path
import sys

import pytest


@pytest.fixture
async def client():
    """ASGI test client — no real server is started."""
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from orchestrator_api.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
