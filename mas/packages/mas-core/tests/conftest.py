"""
Root conftest for mas-core tests.
Provides shared fixtures: fake Redis, async event loop, and log capture.
"""
import asyncio
import pytest
import fakeredis.aioredis as fakeredis


@pytest.fixture
def event_loop():
    """Single asyncio event loop per test session (required by asyncio_mode=auto)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def redis_client():
    """In-memory Redis replacement — no real Redis needed in unit tests."""
    client = await fakeredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()
