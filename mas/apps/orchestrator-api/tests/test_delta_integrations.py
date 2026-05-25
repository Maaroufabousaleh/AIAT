from unittest.mock import AsyncMock, MagicMock, patch

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
    assert by_id["docling_ingestion"]["policy"]["artifact_contract"].startswith("large extraction")
    assert by_id["github_rest"]["status"] == "intake_visible"
    assert "named credential reference" in by_id["github_rest"]["required_gates"]
    assert by_id["github_rest"]["policy"]["write_actions"] == "approval_required"
    assert by_id["defensive_scanners"]["status"] == "wired_optional"
    assert data["scanner_visibility"]["trufflehog"]["status"] in {
        "AVAILABLE",
        "SKIPPED_TOOL_UNAVAILABLE",
    }
    assert by_id["n8n_edge_automation"]["status"] == "deferred"
    assert data["summary"]["total"] == 4


@pytest.mark.anyio
async def test_docling_certification_check_blocks_until_worker_is_approved(client):
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[])
    _patch_state(storage)

    resp = await client.post(
        "/integrations/docling/certification-check",
        json={"source_name": "requirements.pdf", "mime_type": "application/pdf"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "blocked"
    assert "adapter contract" in data["missing_gates"]
    assert data["artifact_contract"]["mode"] == "artifact_reference"
    assert data["artifact_contract"]["content_inline_allowed"] is False
    assert data["sandbox"]["required_profile"] == "gvisor"


@pytest.mark.anyio
async def test_github_metadata_dry_run_resolves_named_credential_without_leaking_it(client):
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[])
    _patch_state(storage)
    manager = MagicMock()
    manager.resolve = AsyncMock(return_value="ghp_real_secret")

    with patch("orchestrator_api.main._credentials_manager", return_value=manager):
        resp = await client.post(
            "/integrations/github/repository-metadata",
            json={
                "repo_url": "https://github.com/example/repo",
                "credential_name": "GITHUB_TOKEN",
                "requester": "operator",
                "dry_run": True,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"]["owner"] == "example"
    assert data["mode"] == "dry_run"
    assert data["credential_ref"] == "<GITHUB_TOKEN>"
    assert data["credential_audit"] == "resolved_server_side"
    assert "ghp_real_secret" not in resp.text
    manager.resolve.assert_awaited_once_with(
        "GITHUB_TOKEN",
        requester="operator",
        context="github.metadata.read",
    )


@pytest.mark.anyio
async def test_n8n_policy_allows_edge_https_and_rejects_control_plane(client):
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[])
    _patch_state(storage)

    allowed = await client.post(
        "/integrations/n8n/edge-policy",
        json={
            "webhook_url": "https://n8n.example.test/webhook/aiat",
            "credential_name": "N8N_WEBHOOK_TOKEN",
        },
    )
    rejected = await client.post(
        "/integrations/n8n/edge-policy",
        json={
            "webhook_url": "https://n8n.example.test/webhook/aiat",
            "allow_control_plane": True,
        },
    )

    assert allowed.status_code == 200
    assert allowed.json()["status"] == "allowed_edge_adapter"
    assert allowed.json()["credential_ref"] == "<N8N_WEBHOOK_TOKEN>"
    assert allowed.json()["control_plane_allowed"] is False
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert "control-plane" in rejected.json()["reasons"][0]
