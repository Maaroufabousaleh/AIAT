"""
Root conftest for mas-core tests.
Provides shared fixtures: fake Redis, async event loop, and log capture.
"""
import asyncio
import sys
from pathlib import Path

import fakeredis.aioredis as fakeredis
import pytest

# Ensure workspace packages are importable without editable install.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "mas-core"))
sys.path.insert(0, str(ROOT / "packages" / "mas-tools-sdk"))


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
