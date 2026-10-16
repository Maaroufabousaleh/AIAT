"""Atomic rollout promotion must recheck its reviewed worker identity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.memory.storage import AgentStorage, worker_identity_snapshot_for_rollout


class _Rows:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def first(self):
        return self.value


def _candidate_record(provenance: dict) -> dict:
    identity_fields = (
        "canonical_source_repository",
        "source_provider",
        "exact_release",
        "commit_sha",
        "package_version",
        "oci_image_digest",
        "dependency_lock_hash",
        "protocol_api_version",
        "adapter_version",
        "transport_type",
        "runtime_fingerprint",
    )
    return {
        "source_provenance": {
            key: provenance.get(key) for key in identity_fields
        }
    }


def _identity_rows():
    worker_id = uuid4()
    steward_id = uuid4()
    rollout_id = uuid4()
    candidate_id = uuid4()
    shell_id = uuid4()
    adapter_id = uuid4()
    bundle_id = uuid4()
    image_digest = "sha256:" + "1" * 64
    source_repo = "https://example.invalid/aiat-worker"
    source_revision = "2" * 40
    worker = {
        "id": worker_id,
        "name": "test-oci-worker",
        "adapter_type": "oci",
        "adapter_config": {
            "image": f"example.invalid/aiat-worker@{image_digest}",
            "sandbox_runtime": "runsc",
            "tool_grants": ["file_write"],
        },
        "sandbox_profile": "sandboxed",
        "capability_ids": [uuid4()],
        "team_id": "dept_qa",
        "status": "INACTIVE",
        "version": "0.1.0",
        "source_repo": source_repo,
        "source_revision": source_revision,
        "version_pin": "0.1.0+sha256.1111",
        "update_policy": "manual",
        "evaluation_status": "approved",
        "adapter_entrypoint": "ProcessAdapter",
        "adapter_module": None,
        "wrapper_config": {},
        "isolation_mode": "wrapper",
        "active_shell_version_id": None,
        "active_adapter_id": None,
        "active_skill_bundle_id": None,
        "model_profile_id": None,
        "model_mode": "none",
        "upstream_commit_sha": None,
    }
    provenance = {
        "id": uuid4(),
        "worker_id": worker_id,
        "canonical_source_repository": source_repo,
        "source_provider": "aiat",
        "exact_release": "0.1.0",
        "commit_sha": source_revision,
        "package_version": None,
        "oci_image_digest": image_digest,
        "dependency_lock_hash": None,
        "protocol_api_version": "aiat.worker.v1",
        "adapter_version": "0.1.0",
        "transport_type": "oci",
        "runtime_fingerprint": "sha256:" + "3" * 64,
        "security_scan_status": "passed",
        "provenance_hash": "a" * 64,
    }
    rollout = {
        "id": rollout_id,
        "worker_id": worker_id,
        "steward_id": steward_id,
        "candidate_id": candidate_id,
        "status": "PROMOTING",
    }
    candidate = {
        "id": candidate_id,
        "worker_id": worker_id,
        "skill_bundle_id": bundle_id,
        "adapter_id": adapter_id,
        "approval_record_id": uuid4(),
        "evidence_json": {
            "worker_shell_version_id": str(shell_id),
            "candidate_record": _candidate_record(provenance),
        },
    }
    shell = {
        "id": shell_id,
        "worker_id": worker_id,
        "status": "active",
    }
    steward = {"id": steward_id, "worker_id": worker_id, "status": "READY"}
    return worker_id, rollout_id, steward_id, candidate_id, worker, provenance, rollout, candidate, shell, steward


def _mock_storage(rows):
    worker_id, rollout_id, steward_id, candidate_id, worker, provenance, rollout, candidate, shell, steward = rows
    conn = MagicMock()
    conn.execute = AsyncMock(
        side_effect=[
            _Rows(worker),
            _Rows(provenance),
            _Rows(rollout),
            _Rows(None),  # no competing rollout
            _Rows(candidate),
            _Rows(shell),
            _Rows(steward),
            MagicMock(rowcount=1),  # adapter update
            MagicMock(rowcount=1),  # skill bundle update
            MagicMock(rowcount=1),  # steward update
            MagicMock(rowcount=1),  # worker pointer update
            MagicMock(rowcount=1),  # rollout CAS update
            MagicMock(rowcount=1),  # rollout transition insert
        ]
    )
    begin = MagicMock()
    begin.__aenter__ = AsyncMock(return_value=conn)
    begin.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = begin
    storage = AgentStorage("postgresql+asyncpg://localhost/unused")
    storage._engine = engine
    storage.get_rollout_record = AsyncMock(return_value={"status": "ACTIVE"})
    return storage, conn


@pytest.mark.anyio
async def test_rollout_activation_fails_closed_without_precheck_snapshots() -> None:
    rows = _identity_rows()
    storage, _conn = _mock_storage(rows)

    result = await storage.activate_rollout_atomically(
        rollout_id=rows[1],
        worker_id=rows[0],
        steward_id=rows[2],
        candidate_id=rows[3],
        completed_at=None,
    )

    assert result is None
    storage._engine.begin.assert_not_called()


@pytest.mark.anyio
async def test_rollout_activation_rejects_changed_worker_config_before_writes() -> None:
    rows = _identity_rows()
    worker_id, rollout_id, steward_id, candidate_id, worker, _provenance, *_ = rows
    expected = worker_identity_snapshot_for_rollout(worker)
    worker["adapter_config"] = {
        **worker["adapter_config"],
        "image": "example.invalid/aiat-worker@sha256:" + "4" * 64,
    }
    storage, conn = _mock_storage(rows)

    result = await storage.activate_rollout_atomically(
        rollout_id=rollout_id,
        worker_id=worker_id,
        steward_id=steward_id,
        candidate_id=candidate_id,
        completed_at=None,
        expected_worker_identity=expected,
        expected_provenance_hash="a" * 64,
    )

    assert result is None
    assert conn.execute.await_count == 1


@pytest.mark.anyio
async def test_rollout_activation_rejects_changed_provenance_hash_before_writes() -> None:
    rows = _identity_rows()
    worker_id, rollout_id, steward_id, candidate_id, worker, provenance, *_ = rows
    storage, conn = _mock_storage(rows)
    provenance["provenance_hash"] = "b" * 64

    result = await storage.activate_rollout_atomically(
        rollout_id=rollout_id,
        worker_id=worker_id,
        steward_id=steward_id,
        candidate_id=candidate_id,
        completed_at=None,
        expected_worker_identity=worker_identity_snapshot_for_rollout(worker),
        expected_provenance_hash="a" * 64,
    )

    assert result is None
    assert conn.execute.await_count == 2


@pytest.mark.anyio
async def test_rollout_activation_rejects_candidate_provenance_identity_drift() -> None:
    rows = _identity_rows()
    worker_id, rollout_id, steward_id, candidate_id, worker, provenance, *_ = rows
    storage, conn = _mock_storage(rows)
    candidate = rows[7]
    candidate_record = candidate["evidence_json"]["candidate_record"]
    candidate_record["source_provenance"]["commit_sha"] = "5" * 40

    result = await storage.activate_rollout_atomically(
        rollout_id=rollout_id,
        worker_id=worker_id,
        steward_id=steward_id,
        candidate_id=candidate_id,
        completed_at=None,
        expected_worker_identity=worker_identity_snapshot_for_rollout(worker),
        expected_provenance_hash=str(provenance["provenance_hash"]),
    )

    assert result is None
    assert conn.execute.await_count == 5


@pytest.mark.anyio
async def test_rollout_activation_updates_pointers_when_snapshots_still_match() -> None:
    rows = _identity_rows()
    worker_id, rollout_id, steward_id, candidate_id, worker, provenance, *_ = rows
    storage, conn = _mock_storage(rows)

    result = await storage.activate_rollout_atomically(
        rollout_id=rollout_id,
        worker_id=worker_id,
        steward_id=steward_id,
        candidate_id=candidate_id,
        completed_at=None,
        expected_worker_identity=worker_identity_snapshot_for_rollout(worker),
        expected_provenance_hash=str(provenance["provenance_hash"]),
    )

    assert result == {"status": "ACTIVE"}
    assert conn.execute.await_count == 13
    storage.get_rollout_record.assert_awaited_once_with(rollout_id)
