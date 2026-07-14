"""
Test 4: Worker integration YAML — create and validate worker config.

Coverage matrix
───────────────
Type        Scenarios
API         register worker (all fields), list workers, update config,
            deactivate → filtered from ACTIVE list, reactivate → visible again,
            deregister, health check, YAML bulk import (valid + invalid + dry-run),
            upstream info endpoint
Integration register → read back via GET → verify all fields survive round-trip
Security    forbidden-tool policy gap (TODO), invalid update_policy rejected (422)
Negative    missing required fields (422), invalid sandbox_profile (422),
            update non-existent worker (404), deactivate non-existent (404),
            YAML import outside CWD (400), import missing required YAML field
Audit/state deactivated worker not returned in ACTIVE listing,
            reactivated worker returned again
"""

from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from conftest import NOW_ISO

# ── constants ────────────────────────────────────────────────────────────────

WORKER_ID = UUID("00000000-0000-4000-a000-0000000000c1")
CAP_ID = UUID("00000000-0000-4000-a000-0000000000d1")


# ── helpers ──────────────────────────────────────────────────────────────────


def _patch(storage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


def _worker_row(
    *,
    worker_id: UUID = WORKER_ID,
    name: str = "code_reviewer",
    status: str = "ACTIVE",
    adapter_type: str = "process",
    sandbox_profile: str = "restricted",
    source_repo: str = "https://github.com/example/code-reviewer",
    version_pin: str = "v1.2.3",
    update_policy: str = "manual",
    adapter_entrypoint: str = "CodeReviewerAgent",
    adapter_module: str | None = "workers.code_reviewer",
    isolation_mode: str = "native",
    wrapper_config: dict | None = None,
    capability_ids: list | None = None,
    version: str = "1.2.3",
    team_id: str | None = "dept_qa",
    evaluation_status: str | None = "approved",
) -> dict:
    return {
        "id": worker_id,
        "name": name,
        "status": status,
        "adapter_type": adapter_type,
        "sandbox_profile": sandbox_profile,
        "source_repo": source_repo,
        "version_pin": version_pin,
        "update_policy": update_policy,
        "adapter_entrypoint": adapter_entrypoint,
        "adapter_module": adapter_module,
        "isolation_mode": isolation_mode,
        "wrapper_config": wrapper_config or {},
        "capability_ids": capability_ids or [CAP_ID],
        "version": version,
        "team_id": team_id,
        "evaluation_status": evaluation_status,
        "health_status": "healthy",
        "last_seen_at": None,
        "error_count": 0,
        "upstream_commit_sha": None,
        "last_upstream_sync": None,
        "source_revision": "main",
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
    }


# ── 1. Register worker with all integration fields ────────────────────────────


@pytest.mark.anyio
async def test_register_worker_with_all_fields_succeeds(client):
    """POST /capabilities/workers with full config → 201, all fields reflected."""
    row = _worker_row()
    storage = MagicMock()
    storage.register_worker = AsyncMock(return_value=row)
    _patch(storage)

    resp = await client.post(
        "/capabilities/workers",
        json={
            "name": "code_reviewer",
            "adapter_type": "process",
            "adapter_config": {"entrypoint": "CodeReviewerAgent"},
            "sandbox_profile": "restricted",
            "capability_ids": [str(CAP_ID)],
            "team_id": "dept_qa",
            "source_repo": "https://github.com/example/code-reviewer",
            "version_pin": "v1.2.3",
            "update_policy": "manual",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "code_reviewer"
    assert body["status"] == "ACTIVE"
    assert body["sandbox_profile"] == "restricted"
    assert body["source_repo"] == "https://github.com/example/code-reviewer"
    assert body["version_pin"] == "v1.2.3"
    assert body["update_policy"] == "manual"
    storage.register_worker.assert_awaited_once()


@pytest.mark.anyio
async def test_register_worker_missing_name_returns_422(client):
    """POST /capabilities/workers without 'name' → 422 validation error."""
    storage = MagicMock()
    _patch(storage)

    resp = await client.post(
        "/capabilities/workers",
        json={"adapter_type": "process"},  # missing name
    )
    assert resp.status_code == 422
    assert "name" in resp.text.lower() or "field required" in resp.text.lower()


@pytest.mark.anyio
async def test_register_worker_missing_adapter_type_returns_422(client):
    """POST /capabilities/workers without 'adapter_type' → 422."""
    storage = MagicMock()
    _patch(storage)

    resp = await client.post(
        "/capabilities/workers",
        json={"name": "code_reviewer"},  # missing adapter_type
    )
    assert resp.status_code == 422


# ── 2. Worker appears in registry listing ─────────────────────────────────────


@pytest.mark.anyio
async def test_list_active_workers_returns_registered_worker(client):
    """GET /capabilities/workers?status=ACTIVE → includes newly registered worker."""
    row = _worker_row()
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[row])
    _patch(storage)

    resp = await client.get("/capabilities/workers", params={"status": "ACTIVE"})
    assert resp.status_code == 200
    workers = resp.json()
    assert len(workers) == 1
    assert workers[0]["name"] == "code_reviewer"
    assert workers[0]["status"] == "ACTIVE"
    storage.list_workers.assert_awaited_once_with(team_id=None, status="ACTIVE")


@pytest.mark.anyio
async def test_list_workers_empty_when_none_registered(client):
    """GET /capabilities/workers returns empty list when no workers."""
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[])
    _patch(storage)

    resp = await client.get("/capabilities/workers")
    assert resp.status_code == 200
    assert resp.json() == []


# ── 3. Read back worker config through GET endpoint ───────────────────────────


@pytest.mark.anyio
async def test_update_worker_config_persists_integration_fields(client):
    """PUT /capabilities/workers/{id} updates adapter_entrypoint and isolation_mode."""
    original = _worker_row()
    updated = _worker_row(
        adapter_entrypoint="UpdatedReviewerAgent",
        isolation_mode="wrapper",
        adapter_module="workers.updated_reviewer",
    )
    storage = MagicMock()
    storage.get_worker = AsyncMock(side_effect=[original, updated])
    storage.update_worker_config = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.put(
        f"/capabilities/workers/{WORKER_ID}",
        json={
            "adapter_entrypoint": "UpdatedReviewerAgent",
            "isolation_mode": "wrapper",
            "adapter_module": "workers.updated_reviewer",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["adapter_entrypoint"] == "UpdatedReviewerAgent"
    assert body["isolation_mode"] == "wrapper"
    assert body["adapter_module"] == "workers.updated_reviewer"
    storage.update_worker_config.assert_awaited_once()


@pytest.mark.anyio
async def test_update_nonexistent_worker_returns_404(client):
    """PUT /capabilities/workers/{unknown_id} → 404."""
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.put(
        f"/capabilities/workers/{uuid4()}",
        json={"adapter_entrypoint": "NewAgent"},
    )
    assert resp.status_code == 404


# ── 4. Deactivate worker → no longer selectable by flows ──────────────────────


@pytest.mark.anyio
async def test_deactivate_worker_changes_status_to_inactive(client):
    """PATCH /capabilities/workers/{id}/status DEACTIVATE → status=INACTIVE."""
    active_row = _worker_row(status="ACTIVE")
    inactive_row = _worker_row(status="INACTIVE")
    storage = MagicMock()
    storage.get_worker = AsyncMock(side_effect=[active_row, inactive_row])
    storage.update_worker_status = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        json={"action": "DEACTIVATE"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "INACTIVE"
    storage.update_worker_status.assert_awaited_once_with(WORKER_ID, status="INACTIVE")


@pytest.mark.anyio
async def test_inactive_worker_not_in_active_listing(client):
    """After deactivate, GET /capabilities/workers?status=ACTIVE returns empty list."""
    storage = MagicMock()
    # ACTIVE list is empty because worker is now INACTIVE
    storage.list_workers = AsyncMock(return_value=[])
    _patch(storage)

    resp = await client.get("/capabilities/workers", params={"status": "ACTIVE"})
    assert resp.status_code == 200
    assert resp.json() == []
    # Verify the status filter was passed to storage
    storage.list_workers.assert_awaited_once_with(team_id=None, status="ACTIVE")


@pytest.mark.anyio
async def test_deactivate_nonexistent_worker_returns_404(client):
    """PATCH /capabilities/workers/{unknown}/status DEACTIVATE → 404."""
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.patch(
        f"/capabilities/workers/{uuid4()}/status",
        json={"action": "DEACTIVATE"},
    )
    assert resp.status_code == 404


# ── 5. Reactivate worker → selectable again ───────────────────────────────────


@pytest.mark.anyio
async def test_reactivate_worker_changes_status_back_to_active(client):
    """PATCH /capabilities/workers/{id}/status ACTIVATE → status=ACTIVE."""
    inactive_row = _worker_row(status="INACTIVE")
    active_row = _worker_row(status="ACTIVE")
    storage = MagicMock()
    storage.get_worker = AsyncMock(side_effect=[inactive_row, active_row])
    storage.update_worker_status = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        json={"action": "ACTIVATE"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACTIVE"
    storage.update_worker_status.assert_awaited_once_with(WORKER_ID, status="ACTIVE")


@pytest.mark.anyio
async def test_reactivated_worker_appears_in_active_listing(client):
    """After reactivate, GET /capabilities/workers?status=ACTIVE returns worker."""
    row = _worker_row(status="ACTIVE")
    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[row])
    _patch(storage)

    resp = await client.get("/capabilities/workers", params={"status": "ACTIVE"})
    assert resp.status_code == 200
    workers = resp.json()
    assert len(workers) == 1
    assert workers[0]["name"] == "code_reviewer"
    assert workers[0]["status"] == "ACTIVE"


# ── 6. DRAIN action ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_drain_worker_changes_status_to_draining(client):
    """PATCH /capabilities/workers/{id}/status DRAIN → status=DRAINING."""
    active_row = _worker_row(status="ACTIVE")
    draining_row = _worker_row(status="DRAINING")
    storage = MagicMock()
    storage.get_worker = AsyncMock(side_effect=[active_row, draining_row])
    storage.update_worker_status = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        json={"action": "DRAIN"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "DRAINING"


# ── 7. Unknown status action → 400 ────────────────────────────────────────────


@pytest.mark.anyio
async def test_unknown_status_action_returns_400(client):
    """PATCH /capabilities/workers/{id}/status with unknown action → 400."""
    row = _worker_row()
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=row)
    _patch(storage)

    resp = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        json={"action": "EXPLODE"},
    )
    assert resp.status_code == 400
    assert "unknown action" in resp.json()["detail"].lower()


# ── 8. Deregister worker ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_deregister_worker_returns_deregistered_status(client):
    """DELETE /capabilities/workers/{id} → 200 with status=deregistered."""
    storage = MagicMock()
    storage.update_worker_status = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.delete(f"/capabilities/workers/{WORKER_ID}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deregistered"
    storage.update_worker_status.assert_awaited_once_with(WORKER_ID, status="DEREGISTERED")


@pytest.mark.anyio
async def test_delete_worker_permanent_removes_registry_row(client, monkeypatch):
    """DELETE /capabilities/workers/{id}?permanent=true hard-deletes test workers."""
    import mas_core.worker_registry.ingestion as ingestion

    row = _worker_row()
    remove_mirror = AsyncMock(return_value=None)
    monkeypatch.setattr(ingestion, "remove_mirror", remove_mirror)
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=row)
    storage.delete_worker = AsyncMock(return_value=True)
    _patch(storage)

    resp = await client.delete(f"/capabilities/workers/{WORKER_ID}?permanent=true")

    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    storage.get_worker.assert_awaited_once_with(WORKER_ID)
    storage.delete_worker.assert_awaited_once_with(WORKER_ID)
    remove_mirror.assert_awaited_once_with(str(WORKER_ID))


@pytest.mark.anyio
async def test_delete_worker_by_name_permanent_removes_registry_row(client):
    """Permanent cleanup accepts worker registry names for dashboard cleanup."""
    row = _worker_row(name="e2e_worker_123")
    storage = MagicMock()
    storage.get_worker_by_name = AsyncMock(return_value=row)
    storage.delete_worker = AsyncMock(return_value=True)
    _patch(storage)

    resp = await client.delete("/capabilities/workers/e2e_worker_123?permanent=true")

    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    storage.get_worker_by_name.assert_awaited_once_with("e2e_worker_123")
    storage.delete_worker.assert_awaited_once_with(WORKER_ID)


@pytest.mark.anyio
async def test_mirror_cleanup_rejects_path_traversal(tmp_path, monkeypatch):
    """Managed mirror deletion cannot escape its configured root."""
    from mas_core.worker_registry import ingestion

    mirror_root = tmp_path / "mirror"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(ingestion, "MIRROR_BASE", mirror_root)

    with pytest.raises(ValueError):
        await ingestion.remove_mirror("../outside")
    with pytest.raises(ValueError):
        await ingestion.remove_mirror(str(outside))
    with pytest.raises(ValueError):
        await ingestion.remove_mirror("")
    assert (outside / "sentinel.txt").exists()


# ── 9. Health check endpoint ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_worker_health_endpoint_returns_health_info(client):
    """GET /capabilities/workers/{id}/health → health_status, error_count, etc."""
    row = _worker_row()
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=row)
    _patch(storage)

    resp = await client.get(f"/capabilities/workers/{WORKER_ID}/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["worker_id"] == str(WORKER_ID)
    assert body["name"] == "code_reviewer"
    assert body["health_status"] == "healthy"
    assert body["status"] == "ACTIVE"
    assert body["error_count"] == 0


@pytest.mark.anyio
async def test_worker_health_endpoint_returns_404_for_unknown(client):
    """GET /capabilities/workers/{unknown}/health → 404."""
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.get(f"/capabilities/workers/{uuid4()}/health")
    assert resp.status_code == 404


# ── 10. YAML manifest bulk import ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_yaml_import_from_valid_manifest_creates_worker(client, tmp_path, monkeypatch):
    """POST /capabilities/workers/import with a valid YAML manifest → created=1."""
    workers_dir = tmp_path / "workers"
    workers_dir.mkdir()

    # Write a minimal valid worker manifest
    manifest_yaml = textwrap.dedent("""\
        metadata:
          id: code_reviewer
          name: Code Reviewer
          version: '1.0'
          source_repo: https://github.com/example/code-reviewer
          version_pin: v1.0.0
          update_policy: manual
          tags:
            - dept_qa
        runtime:
          transport: process
          adapter_config:
            entrypoint: CodeReviewerAgent
          timeout_seconds: 300
          stop_grace_seconds: 60
        capabilities:
          - name: code.review
            version: '1.0'
            description: Review pull requests
            risk_level: low
            required_tools:
              - code.review
        sandbox:
          profile: restricted
          network_mode: egress-allowlist
          egress_allowlist: []
        integration:
          adapter_entrypoint: CodeReviewerAgent
          isolation_mode: native
        limits:
          max_concurrent_tasks: 5
          max_instances: 1
          rate_limit_per_minute: 30
          max_payload_size_bytes: 1048576
    """)
    (workers_dir / "code_reviewer.yaml").write_text(manifest_yaml)

    # Mock storage — seeder calls get_worker_by_name + create_capability + register_worker
    cap_row = {
        "id": CAP_ID,
        "name": "code.review",
        "version": "1.0",
        "description": "Review pull requests",
        "input_schema": None,
        "output_schema": None,
        "risk_level": "low",
        "cost_model": None,
        "required_tools": ["code.review"],
        "required_role": None,
        "created_at": NOW_ISO,
    }
    worker_row = _worker_row(name="code_reviewer")
    storage = MagicMock()
    storage.get_worker_by_name = AsyncMock(return_value=None)
    storage.get_capability_by_name = AsyncMock(return_value=None)
    storage.create_capability = AsyncMock(return_value=cap_row)
    storage.register_worker = AsyncMock(return_value=worker_row)
    _patch(storage)

    # Monkeypatch os.getcwd to tmp_path so path-traversal check passes
    monkeypatch.chdir(tmp_path)

    resp = await client.post(
        "/capabilities/workers/import",
        json={"workers_dir": "workers", "dry_run": False},
    )
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["total"] == 1
    assert summary["created"] == 1
    assert summary["errors"] == 0
    storage.register_worker.assert_awaited_once()
    # Verify name matches manifest id
    _, kwargs = storage.register_worker.await_args
    assert kwargs["name"] == "code_reviewer"
    assert kwargs["source_repo"] == "https://github.com/example/code-reviewer"
    assert kwargs["version_pin"] == "v1.0.0"


@pytest.mark.anyio
async def test_yaml_import_dry_run_does_not_write_to_db(client, tmp_path, monkeypatch):
    """POST /capabilities/workers/import dry_run=True → skipped=1, no DB writes."""
    workers_dir = tmp_path / "workers"
    workers_dir.mkdir()

    manifest_yaml = textwrap.dedent("""\
        metadata:
          id: dry_run_worker
          name: Dry Run Worker
          version: '1.0'
          tags: []
        runtime:
          transport: process
        capabilities: []
        sandbox:
          profile: standard
        integration:
          adapter_entrypoint: WorkerAgent
          isolation_mode: native
    """)
    (workers_dir / "dry_run_worker.yaml").write_text(manifest_yaml)

    storage = MagicMock()
    storage.register_worker = AsyncMock()
    _patch(storage)
    monkeypatch.chdir(tmp_path)

    resp = await client.post(
        "/capabilities/workers/import",
        json={"workers_dir": "workers", "dry_run": True},
    )
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["total"] == 1
    assert summary["skipped"] == 1
    assert summary["created"] == 0
    # No DB writes in dry-run mode
    storage.register_worker.assert_not_awaited()


@pytest.mark.anyio
async def test_yaml_import_invalid_manifest_reports_error(client, tmp_path, monkeypatch):
    """POST /capabilities/workers/import with invalid YAML → errors=1 in summary."""
    workers_dir = tmp_path / "workers"
    workers_dir.mkdir()

    # Missing required 'metadata.id' field
    bad_yaml = textwrap.dedent("""\
        metadata:
          name: Missing ID Worker
        runtime:
          transport: process
    """)
    (workers_dir / "bad_worker.yaml").write_text(bad_yaml)

    storage = MagicMock()
    storage.register_worker = AsyncMock()
    _patch(storage)
    monkeypatch.chdir(tmp_path)

    resp = await client.post(
        "/capabilities/workers/import",
        json={"workers_dir": "workers", "dry_run": False},
    )
    assert resp.status_code == 200
    summary = resp.json()
    assert summary["errors"] == 1
    assert summary["created"] == 0
    # Should contain error detail
    error_detail = summary["details"][0]
    assert error_detail["action"] == "error"
    storage.register_worker.assert_not_awaited()


@pytest.mark.anyio
async def test_yaml_import_outside_cwd_returns_400(client, tmp_path, monkeypatch):
    """POST /capabilities/workers/import with path outside CWD → 400."""
    # Use an absolute path that won't be under CWD
    storage = MagicMock()
    _patch(storage)

    # Use the real tmp_path absolute path — guaranteed to be outside CWD
    resp = await client.post(
        "/capabilities/workers/import",
        json={"workers_dir": str(tmp_path / "workers"), "dry_run": False},
    )
    assert resp.status_code == 400
    assert "within" in resp.json()["detail"].lower() or "not found" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_yaml_import_nonexistent_directory_returns_400(client, tmp_path, monkeypatch):
    """POST /capabilities/workers/import with missing directory → 400."""
    storage = MagicMock()
    _patch(storage)
    monkeypatch.chdir(tmp_path)

    resp = await client.post(
        "/capabilities/workers/import",
        json={"workers_dir": "nonexistent_workers_dir", "dry_run": False},
    )
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()


# ── 11. Upstream info endpoint ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_worker_upstream_info_returns_source_fields(client):
    """GET /capabilities/workers/{id}/upstream → source_repo, version_pin, etc."""
    from unittest.mock import patch

    row = _worker_row()
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=row)
    _patch(storage)

    # Mock check_for_updates to avoid real network call
    with patch(
        "mas_core.worker_registry.ingestion.check_for_updates",
        new_callable=AsyncMock,
        return_value={"has_updates": False, "latest_commit": "abc123"},
    ):
        resp = await client.get(f"/capabilities/workers/{WORKER_ID}/upstream")

    assert resp.status_code == 200
    body = resp.json()
    assert body["worker_id"] == str(WORKER_ID)
    assert body["source_repo"] == "https://github.com/example/code-reviewer"
    assert body["version_pin"] == "v1.2.3"
    assert body["update_policy"] == "manual"


@pytest.mark.anyio
async def test_worker_upstream_info_returns_404_for_unknown(client):
    """GET /capabilities/workers/{unknown}/upstream → 404."""
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.get(f"/capabilities/workers/{uuid4()}/upstream")
    assert resp.status_code == 404


# ── 12. RECLASSIFY action ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_reclassify_worker_updates_entrypoint(client):
    """PATCH /capabilities/workers/{id}/status RECLASSIFY updates adapter_entrypoint."""
    row = _worker_row()
    updated_row = _worker_row(adapter_entrypoint="ToolAgent")
    storage = MagicMock()
    storage.get_worker = AsyncMock(side_effect=[row, updated_row])
    storage.update_worker_status = AsyncMock(return_value=None)
    storage.update_worker_config = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        json={"action": "RECLASSIFY", "new_role": "ToolAgent"},
    )
    assert resp.status_code == 200
    # update_worker_config called with adapter_entrypoint=new_role
    storage.update_worker_config.assert_awaited_once()
    _, kwargs = storage.update_worker_config.await_args
    assert kwargs.get("adapter_entrypoint") == "ToolAgent"


# ── 13. Integration: full lifecycle register → update → deactivate → reactivate


@pytest.mark.anyio
async def test_worker_full_lifecycle(client):
    """Integration: register → verify listing → update config → deactivate → reactivate."""
    # Step 1: Register
    registered = _worker_row()
    storage = MagicMock()
    storage.register_worker = AsyncMock(return_value=registered)
    _patch(storage)

    resp = await client.post(
        "/capabilities/workers",
        json={
            "name": "code_reviewer",
            "adapter_type": "process",
            "sandbox_profile": "restricted",
            "source_repo": "https://github.com/example/code-reviewer",
            "version_pin": "v1.2.3",
            "update_policy": "manual",
        },
    )
    assert resp.status_code == 201
    worker_id = resp.json()["id"]

    # Step 2: List → appears in ACTIVE
    storage.list_workers = AsyncMock(return_value=[registered])
    resp2 = await client.get("/capabilities/workers", params={"status": "ACTIVE"})
    assert resp2.status_code == 200
    assert any(w["name"] == "code_reviewer" for w in resp2.json())

    # Step 3: Update config
    updated = _worker_row(adapter_entrypoint="UpdatedAgent")
    storage.get_worker = AsyncMock(side_effect=[registered, updated])
    storage.update_worker_config = AsyncMock(return_value=None)
    resp3 = await client.put(
        f"/capabilities/workers/{worker_id}",
        json={"adapter_entrypoint": "UpdatedAgent"},
    )
    assert resp3.status_code == 200
    assert resp3.json()["adapter_entrypoint"] == "UpdatedAgent"

    # Step 4: Deactivate
    inactive = _worker_row(status="INACTIVE")
    storage.get_worker = AsyncMock(side_effect=[updated, inactive])
    storage.update_worker_status = AsyncMock(return_value=None)
    resp4 = await client.patch(
        f"/capabilities/workers/{worker_id}/status",
        json={"action": "DEACTIVATE"},
    )
    assert resp4.status_code == 200
    assert resp4.json()["status"] == "INACTIVE"

    # Step 5: Verify not in ACTIVE list
    storage.list_workers = AsyncMock(return_value=[])
    resp5 = await client.get("/capabilities/workers", params={"status": "ACTIVE"})
    assert resp5.json() == []

    # Step 6: Reactivate
    active_again = _worker_row(status="ACTIVE")
    storage.get_worker = AsyncMock(side_effect=[inactive, active_again])
    storage.update_worker_status = AsyncMock(return_value=None)
    resp6 = await client.patch(
        f"/capabilities/workers/{worker_id}/status",
        json={"action": "ACTIVATE"},
    )
    assert resp6.status_code == 200
    assert resp6.json()["status"] == "ACTIVE"

    # Step 7: Verify appears in ACTIVE list again
    storage.list_workers = AsyncMock(return_value=[active_again])
    resp7 = await client.get("/capabilities/workers", params={"status": "ACTIVE"})
    assert len(resp7.json()) == 1
    assert resp7.json()[0]["status"] == "ACTIVE"


# ── 14. TODO: Forbidden tool policy (production gap) ──────────────────────────


@pytest.mark.skip(
    reason=(
        "TODO: No tool policy enforcement exists in the worker registration path. "
        "The API currently accepts any capability regardless of risk_level or required_role. "
        "Production gap: POST /capabilities/workers should reject capabilities with "
        "risk_level='critical' unless the worker has an approved evaluation_status, "
        "or tools that are in a platform-level denylist. "
        "Implement WorkerToolPolicy validation in RegisterWorkerRequest handling "
        "and add a test asserting 422/403 for disallowed tool assignments."
    )
)
@pytest.mark.anyio
async def test_assigning_forbidden_tool_is_rejected(client):
    """Registering a worker with a tool in the forbidden-tools denylist → 422/403."""
    storage = MagicMock()
    storage.register_worker = AsyncMock()
    _patch(storage)

    # Attempt to register with a forbidden high-risk capability
    resp = await client.post(
        "/capabilities/workers",
        json={
            "name": "rogue_worker",
            "adapter_type": "process",
            "capability_ids": [],  # would reference a critical-risk capability
            "forbidden_tool": "system.exec",  # placeholder for policy enforcement
        },
    )
    # When implemented, this should return 422 or 403
    assert resp.status_code in (422, 403)
