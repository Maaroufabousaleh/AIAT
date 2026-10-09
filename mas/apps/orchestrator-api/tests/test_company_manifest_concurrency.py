"""Company manifest validation and apply must use optimistic concurrency."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

OPERATOR_HEADERS = {"X-API-Key": "test-operator-key"}


def _install_storage(storage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


@pytest.mark.anyio
@pytest.mark.parametrize(("manifest_row", "expected_version"), [(None, 0), ({"manifest_version": 7}, 7)])
async def test_validation_returns_active_manifest_version(client, monkeypatch, manifest_row, expected_version):
    import orchestrator_api.main as main

    company_id = uuid4()
    storage = MagicMock()
    storage.get_company = AsyncMock(return_value={"id": company_id})
    storage.get_company_read_model = AsyncMock(return_value={"manifest": manifest_row})
    _install_storage(storage)
    compiled = MagicMock()
    monkeypatch.setattr(
        main,
        "compile_company_manifest",
        lambda _manifest: (compiled, "validated-digest", {"slug": "team"}),
    )

    response = await client.post(
        f"/companies/{company_id}/manifest/validate",
        json={"manifest": {"slug": "team"}, "source": "dashboard_operator"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["current_manifest_version"] == expected_version


@pytest.mark.anyio
async def test_apply_passes_expected_version_and_returns_stable_conflict(client, monkeypatch):
    import orchestrator_api.main as main

    company_id = uuid4()
    storage = MagicMock()
    storage.apply_company_manifest = AsyncMock(
        side_effect=ValueError("COMPANY_MANIFEST_VERSION_CONFLICT: expected 2, current 3")
    )
    _install_storage(storage)
    compiled = MagicMock()
    canonical = {"slug": "team"}
    monkeypatch.setattr(
        main,
        "compile_company_manifest",
        lambda _manifest: (compiled, "validated-digest", canonical),
    )

    response = await client.post(
        f"/companies/{company_id}/manifest/apply",
        headers=OPERATOR_HEADERS,
        json={
            "manifest": {"slug": "team"},
            "source": "dashboard_operator",
            "expected_manifest_version": 2,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "COMPANY_MANIFEST_VERSION_CONFLICT"
    storage.apply_company_manifest.assert_awaited_once_with(
        company_id=company_id,
        manifest=compiled,
        digest="validated-digest",
        canonical=canonical,
        source="dashboard_operator",
        actor="operator",
        expected_manifest_version=2,
    )


@pytest.mark.anyio
async def test_apply_requires_expected_manifest_version(client):
    company_id = uuid4()
    response = await client.post(
        f"/companies/{company_id}/manifest/apply",
        headers=OPERATOR_HEADERS,
        json={"manifest": {"slug": "team"}, "source": "dashboard_operator"},
    )
    assert response.status_code == 422
