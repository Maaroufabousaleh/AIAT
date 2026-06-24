"""
Conftest for orchestrator-api tests.

Shared constants and helpers used across multiple test modules.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

# Ensure this tests directory is importable so ``from conftest import …``
# works under ``--import-mode=importlib``.
_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

# ── shared test constants ─────────────────────────────────────────────────────

PROJECT_ID: uuid.UUID = uuid.UUID("00000000-0000-4000-a000-000000000001")
NOW_ISO: str = "2026-01-01T00:00:00+00:00"


def _fake_project(
    state: str,
    *,
    project_id: uuid.UUID | None = None,
    name: str = "Test Project",
    description: str = "desc",
    failed_from_state: str | None = None,
    failure_reason: str | None = None,
) -> dict:
    """Return a minimal project dict suitable for use in mock storage."""
    return {
        "id": project_id if project_id is not None else PROJECT_ID,
        "name": name,
        "description": description,
        "state": state,
        "created_by": "human",
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
        "failed_from_state": failed_from_state,
        "failure_reason": failure_reason,
    }


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch):
    """Ensure orchestrator-api can start without real Postgres/Redis."""
    monkeypatch.setenv("PGBOUNCER_DSN", "postgresql+asyncpg://fake:fake@localhost:6432/fake")
    monkeypatch.setenv("ROUTER_URL", "http://localhost:9999")
    monkeypatch.setenv("MAS_API_KEY", "test-mas-key")
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)


@pytest.fixture
async def client(monkeypatch):
    """ASGI test client — no real server is started.

    We patch AgentStorage.connect to avoid needing a real DB,
    and set storage=None so endpoints that need DB raise 503.
    """
    pytest.importorskip("fastapi")
    httpx = pytest.importorskip("httpx")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    # Patch storage.connect to be a no-op (will leave storage as None
    # since the except block handles connection failures gracefully)
    from orchestrator_api.main import app

    # Reset cached system state to avoid 503 leaks between tests
    app.state._cached_system_state = "RUNNING"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
