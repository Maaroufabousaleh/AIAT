"""Unverified scalar security-scan claims cannot satisfy worker governance."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


@pytest.mark.anyio
async def test_external_worker_registration_rejects_scalar_scan_pass(client, monkeypatch) -> None:
    from orchestrator_api import main

    storage = MagicMock()
    storage.register_worker = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)

    response = await client.post(
        "/capabilities/workers",
        json={
            "name": "unverified-worker",
            "adapter_type": "oci",
            "source_repo": "https://github.com/example/worker",
            "version_pin": "1.0.0",
            "sandbox_profile": "sandboxed",
            "adapter_config": {
                "provenance": {"security_scan_status": " PaSsEd "},
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SECURITY_SCAN_EVIDENCE_REQUIRED"
    storage.register_worker.assert_not_awaited()


@pytest.mark.anyio
async def test_steward_creation_rejects_scalar_scan_pass_before_persisting(client, monkeypatch) -> None:
    from orchestrator_api import main

    worker_id = uuid4()
    storage = MagicMock()
    storage.get_worker = AsyncMock(
        return_value={"id": worker_id, "source_repo": "https://github.com/example/worker"}
    )
    storage.get_steward_by_worker = AsyncMock(return_value=None)
    storage.create_external_provenance = AsyncMock()
    storage.create_steward = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)

    response = await client.post(
        f"/capabilities/workers/{worker_id}/steward",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "source_repo": "https://github.com/example/worker",
            "exact_release": "1.0.0",
            "security_scan_status": "passed",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SECURITY_SCAN_EVIDENCE_REQUIRED"
    storage.create_external_provenance.assert_not_awaited()
    storage.create_steward.assert_not_awaited()


@pytest.mark.anyio
async def test_manifest_import_rejects_scalar_scan_pass_without_writes(
    client, monkeypatch, tmp_path
) -> None:
    from orchestrator_api import main

    workers_dir = tmp_path / "workers"
    workers_dir.mkdir()
    (workers_dir / "unverified-worker.yaml").write_text(
        """
metadata:
  id: unverified-worker
  name: Unverified Worker
  version: "1.0.0"
  source_repo: https://github.com/example/unverified-worker
  version_pin: "1.0.0"
runtime:
  transport: process
integration:
  isolation_mode: wrapper
sandbox:
  profile: sandboxed
runtime_tier: external
source_provenance:
  security_scan_status: passed
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    storage = MagicMock()
    storage.get_worker_by_name = AsyncMock()
    storage.register_worker = AsyncMock()
    storage.create_capability = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)

    response = await client.post(
        "/capabilities/workers/import",
        headers={"X-API-Key": "test-operator-key"},
        json={"workers_dir": str(workers_dir)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["errors"] == 1
    assert payload["details"][0]["details"].startswith(
        "SECURITY_SCAN_EVIDENCE_REQUIRED:"
    )
    storage.get_worker_by_name.assert_not_awaited()
    storage.register_worker.assert_not_awaited()
    storage.create_capability.assert_not_awaited()


@pytest.mark.anyio
async def test_candidate_certification_rejects_legacy_scalar_scan_pass(client, monkeypatch) -> None:
    from orchestrator_api import main

    from mas_core.worker_registry.steward import (
        CandidateIntakeStatus,
        ExternalProvenance,
        ExternalWorkerSteward,
        StewardStatus,
    )

    worker_id = uuid4()
    steward = ExternalWorkerSteward(
        worker_id=str(worker_id),
        steward_id=uuid4(),
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            exact_release="1.0.0",
            transport_type="process",
            # Simulates a legacy persisted row written before ingress closed.
            security_scan_status="passed",
        ),
        status=StewardStatus.READY,
    )
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
    )
    for stage in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(candidate.candidate_id, stage)

    storage = MagicMock()
    storage.get_worker = AsyncMock()
    storage.create_certification_run = AsyncMock()
    storage.create_compatibility_matrix = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))

    response = await client.post(
        f"/capabilities/workers/{worker_id}/steward/candidates/{candidate.candidate_id}/certify",
        headers={"X-API-Key": "test-operator-key"},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SECURITY_SCAN_EVIDENCE_REQUIRED"
    storage.get_worker.assert_awaited_once_with(worker_id)
    storage.create_certification_run.assert_not_awaited()
    storage.create_compatibility_matrix.assert_not_awaited()


@pytest.mark.anyio
async def test_worker_activation_does_not_trust_legacy_scalar_scan_pass() -> None:
    from orchestrator_api import main

    worker_id = uuid4()
    storage = MagicMock()
    storage.get_worker_shell_version = AsyncMock(return_value={"status": "active"})
    storage.get_runtime_adapter = AsyncMock(
        return_value={"status": "active", "conformance_status": "passed"}
    )
    storage.get_skill_bundle = AsyncMock(return_value={"status": "APPROVED"})
    storage.list_capability_snapshots = AsyncMock(return_value=[{"id": uuid4()}])
    storage.get_steward_by_worker = AsyncMock(return_value={"id": uuid4()})
    storage.get_external_provenance_by_worker = AsyncMock(
        return_value={"security_scan_status": "passed"}
    )
    worker = {
        "id": worker_id,
        "source_repo": "https://github.com/example/worker",
        "version_pin": "1.0.0",
        "adapter_config": {},
        "active_shell_version_id": uuid4(),
        "active_adapter_id": uuid4(),
        "active_skill_bundle_id": uuid4(),
        "model_mode": "none",
    }

    blockers = await main._worker_activation_blockers(storage, worker)

    assert "external worker security scan PASS requires retained, source-bound evidence" in blockers
