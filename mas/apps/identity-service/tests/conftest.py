"""Identity-service test runtime configuration."""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Exercise the asyncio runtime used by FastAPI, asyncpg, and production."""
    return "asyncio"
