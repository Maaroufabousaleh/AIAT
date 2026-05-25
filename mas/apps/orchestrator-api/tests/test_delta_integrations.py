from unittest.mock import AsyncMock, MagicMock

import pytest


def _patch_state(storage):
    from orchestrator_api.main import app

    app.state.storage = storage


@pytest.mark.anyio
async def test_delta_integration_readiness_catalog_exposes_governed_gates(client):
    storage = MagicMock()
    storage.list_workers = AsyncMock(
        return_value=[
            {
                "id": "docling_ingestion_placeholder",
                "name": "Docling Ingestion Placeholder",
                "status": "INACTIVE",
                "evaluation_status": "pending",
                "source_repo": "local",
                "team_id": "dept_production",
                "capability_ids": ["document.ingest.docling"],
            },
            {
                "id": "github_candidate",
                "name": "GitHub metadata candidate",
                "status": "INACTIVE",
                "evaluation_status": "pending",
                "source_repo": "https://github.com/example/repo",
                "team_id": "dept_infra",
                "capability_ids": [],
            },
        ]
    )
    _patch_state(storage)

    resp = await client.get("/integrations/delta-readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert data["phase"] == "Delta"
    assert data["status"] == "started"
    by_id = {item["id"]: item for item in data["integrations"]}

    assert by_id["docling_ingestion"]["status"] == "placeholder_ready"
    assert "adapter contract" in by_id["docling_ingestion"]["required_gates"]
    assert by_id["github_rest"]["status"] == "intake_visible"
    assert "named credential reference" in by_id["github_rest"]["required_gates"]
    assert by_id["defensive_scanners"]["status"] == "wired_optional"
    assert by_id["n8n_edge_automation"]["status"] == "deferred"
    assert data["summary"]["total"] == 4
