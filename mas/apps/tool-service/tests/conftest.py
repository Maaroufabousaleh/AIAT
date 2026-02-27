"""
Conftest for tool-service tests.
"""
import pytest
from httpx import AsyncClient, ASGITransport

from tool_service.main import app


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
