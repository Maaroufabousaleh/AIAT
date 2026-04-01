"""
Tests for project document endpoints:
  GET /projects/{id}/documents
  GET /projects/{id}/documents/{doc_id}
  GET /projects/{id}/feasibility
  GET /projects/{id}/sprints
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from conftest import NOW_ISO, PROJECT_ID

# ── helpers ───────────────────────────────────────────────────────────────────

DOC_ID = uuid.uuid4()


def _fake_document(doc_type: str = "PDR") -> dict:
    return {
        "id": DOC_ID,
        "project_id": PROJECT_ID,
        "doc_type": doc_type,
        "content": {"summary": "test"},
        "created_at": NOW_ISO,
    }


def _fake_sprint() -> dict:
    return {
        "id": uuid.uuid4(),
        "project_id": PROJECT_ID,
        "sprint_number": 1,
        "status": "IN_PROGRESS",
        "created_at": NOW_ISO,
    }


def _make_storage(
    project=None,
    documents=None,
    document=None,
    feasibility=None,
    sprints=None,
):
    storage = MagicMock()
    storage.get_project = AsyncMock(return_value=project)
    storage.list_documents = AsyncMock(return_value=documents if documents is not None else [])
    storage.get_document = AsyncMock(return_value=document)
    storage.get_latest_document = AsyncMock(return_value=feasibility)
    storage.list_sprints = AsyncMock(return_value=sprints if sprints is not None else [])
    return storage


def _patch_state(storage, controller=None):
    """Directly set app.state attributes."""
    from orchestrator_api.main import app

    app.state.storage = storage
    if controller is not None:
        app.state.controller = controller


# ── GET /projects/{id}/documents ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_list_documents_empty(client):
    """GET /projects/{id}/documents returns empty list when no docs exist."""
    _patch_state(_make_storage(documents=[]))
    resp = await client.get(f"/projects/{PROJECT_ID}/documents")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_list_documents_with_results(client):
    """GET /projects/{id}/documents returns all documents for the project."""
    docs = [_fake_document("PDR"), _fake_document("CDR")]
    _patch_state(_make_storage(documents=docs))

    resp = await client.get(f"/projects/{PROJECT_ID}/documents")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.anyio
async def test_list_documents_with_type_filter(client):
    """GET /projects/{id}/documents?doc_type=PDR passes doc_type filter to storage."""
    docs = [_fake_document("PDR")]
    storage = _make_storage(documents=docs)
    _patch_state(storage)

    resp = await client.get(f"/projects/{PROJECT_ID}/documents?doc_type=PDR")
    assert resp.status_code == 200
    storage.list_documents.assert_awaited_once_with(PROJECT_ID, doc_type="PDR")


@pytest.mark.anyio
async def test_list_documents_no_storage_returns_503(client):
    """GET /projects/{id}/documents returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.get(f"/projects/{PROJECT_ID}/documents")
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_list_documents_invalid_uuid_returns_422(client):
    """GET /projects/{id}/documents returns 422 for invalid UUID."""
    resp = await client.get("/projects/not-a-uuid/documents")
    assert resp.status_code == 422


# ── GET /projects/{id}/documents/{doc_id} ────────────────────────────────────


@pytest.mark.anyio
async def test_get_document_found(client):
    """GET /projects/{id}/documents/{doc_id} returns document when found."""
    doc = _fake_document("PDR")
    _patch_state(_make_storage(document=doc))

    resp = await client.get(f"/projects/{PROJECT_ID}/documents/{DOC_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["doc_type"] == "PDR"


@pytest.mark.anyio
async def test_get_document_not_found(client):
    """GET /projects/{id}/documents/{doc_id} returns 404 when document missing."""
    _patch_state(_make_storage(document=None))

    resp = await client.get(f"/projects/{PROJECT_ID}/documents/{DOC_ID}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_document_wrong_project(client):
    """GET /projects/{id}/documents/{doc_id} returns 404 when doc belongs to another project."""
    doc = _fake_document("PDR")
    doc["project_id"] = uuid.uuid4()  # different project
    _patch_state(_make_storage(document=doc))

    resp = await client.get(f"/projects/{PROJECT_ID}/documents/{DOC_ID}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_document_no_storage_returns_503(client):
    """GET /projects/{id}/documents/{doc_id} returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.get(f"/projects/{PROJECT_ID}/documents/{DOC_ID}")
    assert resp.status_code == 503


# ── GET /projects/{id}/feasibility ───────────────────────────────────────────


@pytest.mark.anyio
async def test_get_feasibility_found(client):
    """GET /projects/{id}/feasibility returns feasibility report when it exists."""
    doc = _fake_document("FEASIBILITY_REPORT")
    _patch_state(_make_storage(feasibility=doc))

    resp = await client.get(f"/projects/{PROJECT_ID}/feasibility")
    assert resp.status_code == 200
    data = resp.json()
    assert data["doc_type"] == "FEASIBILITY_REPORT"


@pytest.mark.anyio
async def test_get_feasibility_not_found(client):
    """GET /projects/{id}/feasibility returns 404 when no feasibility report."""
    _patch_state(_make_storage(feasibility=None))

    resp = await client.get(f"/projects/{PROJECT_ID}/feasibility")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_feasibility_no_storage_returns_503(client):
    """GET /projects/{id}/feasibility returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.get(f"/projects/{PROJECT_ID}/feasibility")
    assert resp.status_code == 503


# ── GET /projects/{id}/sprints ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_sprints_empty(client):
    """GET /projects/{id}/sprints returns empty list when no sprints."""
    _patch_state(_make_storage(sprints=[]))

    resp = await client.get(f"/projects/{PROJECT_ID}/sprints")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_get_sprints_with_data(client):
    """GET /projects/{id}/sprints returns all sprints for the project."""
    sprints = [_fake_sprint(), _fake_sprint()]
    _patch_state(_make_storage(sprints=sprints))

    resp = await client.get(f"/projects/{PROJECT_ID}/sprints")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.anyio
async def test_get_sprints_no_storage_returns_503(client):
    """GET /projects/{id}/sprints returns 503 when storage unavailable."""
    _patch_state(None)
    resp = await client.get(f"/projects/{PROJECT_ID}/sprints")
    assert resp.status_code == 503
