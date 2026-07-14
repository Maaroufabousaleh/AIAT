"""
Tests for dead-letter queue endpoints:
  GET  /dead-letters
  GET  /dead-letters/{id}
  POST /dead-letters/{id}/replay
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────

PROJECT_ID = uuid.uuid4()
NOW_ISO = "2026-01-01T00:00:00+00:00"


def _fake_dead_letter(letter_id: int = 1) -> dict:
    message_id = str(uuid.uuid4())
    return {
        "id": letter_id,
        "message_id": message_id,
        "project_id": PROJECT_ID,
        "recipient_team": "exec_ceo",
        "envelope_json": {
            "message_id": message_id,
            "retry_count": 3,
            "timestamp": "2025-12-31T00:00:00+00:00",
            "msg_type": "DIRECTIVE",
            "sender_id": "orchestrator",
            "recipient_team": "exec_ceo",
            "project_id": str(PROJECT_ID),
            "payload": {"action": "RESUME"},
        },
        "created_at": NOW_ISO,
        "failure_reason": "max_attempts_exceeded",
    }


def _make_storage(letters=None):
    storage = MagicMock()
    storage.list_dead_letters = AsyncMock(return_value=letters if letters is not None else [])
    return storage


def _make_storage_with_conn(row=None):
    """Storage that supports engine.connect() for raw SQL queries."""
    storage = _make_storage()

    mock_conn = MagicMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = row

    mock_conn.execute = AsyncMock(return_value=result)

    mock_connect_cm = MagicMock()
    mock_connect_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_connect_cm.__aexit__ = AsyncMock(return_value=False)

    storage.engine = MagicMock()
    storage.engine.connect = MagicMock(return_value=mock_connect_cm)
    storage.create_task_log = AsyncMock()

    return storage


def _patch_state(storage, controller=None):
    """Directly set app.state attributes."""
    from orchestrator_api.main import app

    app.state.storage = storage
    if controller is not None:
        app.state.controller = controller


# ── GET /dead-letters ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_dead_letters_empty(client):
    """GET /dead-letters returns empty list when queue is empty."""
    _patch_state(_make_storage(letters=[]))

    resp = await client.get("/dead-letters")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_list_dead_letters_with_results(client):
    """GET /dead-letters returns all dead letters."""
    letters = [_fake_dead_letter(1), _fake_dead_letter(2)]
    _patch_state(_make_storage(letters=letters))

    resp = await client.get("/dead-letters")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.anyio
async def test_list_dead_letters_with_project_filter(client):
    """GET /dead-letters?project_id=... filters by project_id."""
    storage = _make_storage(letters=[_fake_dead_letter(1)])
    _patch_state(storage)

    resp = await client.get(f"/dead-letters?project_id={PROJECT_ID}")
    assert resp.status_code == 200
    storage.list_dead_letters.assert_awaited_once_with(
        project_id=PROJECT_ID,
        recipient_team=None,
        limit=100,
    )


@pytest.mark.anyio
async def test_list_dead_letters_with_team_filter(client):
    """GET /dead-letters?recipient_team=exec_ceo filters by team."""
    storage = _make_storage(letters=[])
    _patch_state(storage)

    resp = await client.get("/dead-letters?recipient_team=exec_ceo")
    assert resp.status_code == 200
    storage.list_dead_letters.assert_awaited_once_with(
        project_id=None,
        recipient_team="exec_ceo",
        limit=100,
    )


@pytest.mark.anyio
async def test_list_dead_letters_no_storage_returns_503(client):
    """GET /dead-letters returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.get("/dead-letters")
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_list_dead_letters_invalid_limit(client):
    """GET /dead-letters returns 422 for limit=0."""
    resp = await client.get("/dead-letters?limit=0")
    assert resp.status_code == 422


# ── GET /dead-letters/{id} ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_dead_letter_found(client):
    """GET /dead-letters/{id} returns the dead letter when found."""
    row = _fake_dead_letter(1)
    _patch_state(_make_storage_with_conn(row=row))

    resp = await client.get("/dead-letters/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 1
    assert data["recipient_team"] == "exec_ceo"


@pytest.mark.anyio
async def test_get_dead_letter_not_found(client):
    """GET /dead-letters/{id} returns 404 when dead letter missing."""
    _patch_state(_make_storage_with_conn(row=None))

    resp = await client.get("/dead-letters/9999")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_dead_letter_no_storage_returns_503(client):
    """GET /dead-letters/{id} returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.get("/dead-letters/1")
    assert resp.status_code == 503


# ── POST /dead-letters/{id}/replay ────────────────────────────────────────────


@pytest.mark.anyio
async def test_replay_dead_letter_happy_path(client):
    """POST /dead-letters/{id}/replay successfully replays to router."""
    row = _fake_dead_letter(1)
    storage = _make_storage_with_conn(row=row)
    _patch_state(storage)

    with patch("httpx.AsyncClient") as mock_http:
        mock_response = MagicMock(status_code=201)
        mock_response.json.return_value = {"entry_id": "123-0", "deduplicated": False}
        mock_client_instance = MagicMock(post=AsyncMock(return_value=mock_response))
        mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post("/dead-letters/1/replay")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "replayed"
    assert "new_message_id" in data
    assert data["entry_id"] == "123-0"
    storage.create_task_log.assert_awaited_once()
    published = mock_client_instance.post.await_args.kwargs["json"]
    assert published["retry_count"] == 0
    assert published["timestamp"] != "2025-12-31T00:00:00+00:00"


@pytest.mark.anyio
async def test_replay_dead_letter_not_found(client):
    """POST /dead-letters/{id}/replay returns 404 when dead letter missing."""
    _patch_state(_make_storage_with_conn(row=None))

    resp = await client.post("/dead-letters/9999/replay")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_replay_dead_letter_router_error_returns_502(client):
    """POST /dead-letters/{id}/replay returns 502 when router returns error."""
    row = _fake_dead_letter(1)
    _patch_state(_make_storage_with_conn(row=row))

    with patch("httpx.AsyncClient") as mock_http:
        mock_response = MagicMock(status_code=500)
        mock_client_instance = MagicMock(post=AsyncMock(return_value=mock_response))
        mock_http.return_value.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = await client.post("/dead-letters/1/replay")

    assert resp.status_code == 502


@pytest.mark.anyio
async def test_replay_dead_letter_no_storage_returns_503(client):
    """POST /dead-letters/{id}/replay returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.post("/dead-letters/1/replay")
    assert resp.status_code == 503
