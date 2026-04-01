"""
Conftest for message-router tests.
"""
import sys
from pathlib import Path

import pytest


@pytest.fixture
async def client():
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from message_router.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
