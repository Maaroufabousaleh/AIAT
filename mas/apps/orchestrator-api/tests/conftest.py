"""
Conftest for orchestrator-api tests.
"""
import pytest
from httpx import AsyncClient, ASGITransport

from orchestrator_api.main import app


@pytest.fixture
async def client():
    """ASGI test client — no real server is started."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
