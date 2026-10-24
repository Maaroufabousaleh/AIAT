"""First-party candidate governance revalidates source identity at write boundaries."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


def _candidate_state():
    from mas_core.worker_contract import WorkerCapabilities
    from mas_core.worker_contract.conformance import (
        DETERMINISTIC_FILE_CONFORMANCE_DECLARATION,
    )
    from mas_core.worker_registry.source_bound_security_scan import (
        FIRST_PARTY_IMAGE_DIGEST,
        FIRST_PARTY_RUNTIME_SHA256,
        FIRST_PARTY_SOURCE_REPOSITORY,
        FIRST_PARTY_SOURCE_REVISION,
        FIRST_PARTY_WORKER_ID,
        FIRST_PARTY_WORKER_VERSION,
    )
    from mas_core.worker_registry.steward import (
        CandidateIntakeStatus,
        CapabilitySnapshot,
        DocumentationSnapshot,
        DocumentationSource,
        ExternalProvenance,
        ExternalWorkerSteward,
        StewardStatus,
    )

    worker_id = uuid4()
    scan_report = {
        "schema_version": "test-source-bound-scan",
        "status": "passed",
        "finding_count": 0,
        "scanner_error_count": 0,
        "report_sha256": "11" * 32,
    }
    provenance = ExternalProvenance(
        canonical_source_repository=FIRST_PARTY_SOURCE_REPOSITORY,
        source_provider="aiat",
        exact_release=FIRST_PARTY_WORKER_VERSION,
        commit_sha=FIRST_PARTY_SOURCE_REVISION,
        oci_image_digest=FIRST_PARTY_IMAGE_DIGEST,
        transport_type="oci",
        runtime_fingerprint=f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
        content_hash="a" * 64,
        security_scan_status="passed",
        source_bound_security_scan=scan_report,
        provenance_hash="persisted-provenance-hash-1",
    )
    steward = ExternalWorkerSteward(
        worker_id=str(worker_id),
        steward_id=uuid4(),
        provenance=provenance,
        status=StewardStatus.READY,
    )
    steward.add_documentation_snapshot(
        DocumentationSnapshot(
            source=DocumentationSource(uri=FIRST_PARTY_SOURCE_REPOSITORY, trusted=True),
            version=FIRST_PARTY_WORKER_VERSION,
            content_sha256="b" * 64,
        )
    )
    steward.record_capabilities(
        CapabilitySnapshot(
            version=FIRST_PARTY_WORKER_VERSION,
            capabilities=WorkerCapabilities(
                capability_names=["deterministic.file_generate"],
                extensions={
                    "aiat.conformance": DETERMINISTIC_FILE_CONFORMANCE_DECLARATION
                },
            ),
        )
    )
    candidate = steward.generate_candidate(
        semantic_version="0.1.1",
        adapter_version="0.1.1",
        upstream_compatibility_range="==0.1.1",
        adapter_entrypoint="ProcessAdapter",
        implementation_ref=FIRST_PARTY_SOURCE_REVISION,
    )
    candidate_id = candidate.candidate_id
    for stage in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(candidate_id, stage)

    worker = {
        "id": worker_id,
        "name": FIRST_PARTY_WORKER_ID,
        "version": FIRST_PARTY_WORKER_VERSION,
        "version_pin": f"{FIRST_PARTY_WORKER_VERSION}+{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}",
        "source_repo": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_revision": FIRST_PARTY_SOURCE_REVISION,
        "adapter_type": "oci",
        "adapter_entrypoint": "ProcessAdapter",
        "sandbox_profile": "sandboxed",
        "adapter_config": {
            "image": f"aiat-deterministic-file-worker@{FIRST_PARTY_IMAGE_DIGEST}",
            "capabilities": {},
        },
    }
    provenance_row = provenance.model_dump(mode="json")
    provenance_row.pop("source_bound_security_scan", None)
    provenance_row.update(
        {
            "id": uuid4(),
            "worker_id": worker_id,
            "provenance_hash": "persisted-provenance-hash-1",
        }
    )
    candidate_row = {
        "id": candidate_id,
        "worker_id": worker_id,
        "skill_bundle_id": candidate.bundle.bundle_id,
        "adapter_id": candidate.adapter.adapter_id,
        "intake_status": candidate.intake_status.value,
        "evidence_json": {"candidate_record": candidate.model_dump(mode="json")},
    }
    steward_row = {
        "id": steward.steward_id,
        "worker_id": worker_id,
        "metadata": {
            "source_provenance_extra": {"source_bound_security_scan": scan_report}
        },
    }
    return {
        "worker_id": worker_id,
        "candidate_id": candidate_id,
        "worker": worker,
        "provenance_row": provenance_row,
        "scan_report": scan_report,
        "candidate_row": candidate_row,
        "steward_row": steward_row,
        "steward": steward,
    }


def _storage_for(state):
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=state["worker"])
    storage.get_external_provenance_by_worker = AsyncMock(
        return_value=state["provenance_row"]
    )
    storage.get_steward_by_worker = AsyncMock(return_value=state["steward_row"])
    storage.get_skill_bundle_candidate = AsyncMock(return_value=state["candidate_row"])
    storage.create_skill_bundle = AsyncMock(
        side_effect=lambda **kwargs: {"id": kwargs["bundle_id"], **kwargs}
    )
    storage.create_runtime_adapter = AsyncMock(
        side_effect=lambda **kwargs: {"id": kwargs["adapter_id"], **kwargs}
    )
    storage.create_skill_bundle_candidate = AsyncMock()
    storage.list_certification_runs = AsyncMock(return_value=[])
    storage.create_certification_run = AsyncMock()
    storage.create_approval_record = AsyncMock(return_value={"id": uuid4()})
    storage.persist_first_party_candidate_certification_atomically = AsyncMock(
        side_effect=lambda **kwargs: {
            "certification": dict(kwargs["certification"]),
            "candidate": dict(state["candidate_row"]),
            "worker_shell_version": kwargs.get("shell_values"),
            "compatibility_matrix": kwargs.get("compatibility_matrix"),
        }
    )
    storage.approve_first_party_candidate_atomically = AsyncMock(
        side_effect=lambda **kwargs: {
            "candidate": dict(state["candidate_row"]),
            "approval": dict(kwargs.get("approval") or {}),
            "idempotent": False,
        }
    )
    storage.update_skill_bundle_candidate = AsyncMock()
    storage.update_skill_bundle = AsyncMock()
    storage.get_runtime_adapter = AsyncMock(return_value=None)
    storage.update_runtime_adapter = AsyncMock()
    storage.update_worker_config = AsyncMock()
    storage.create_compatibility_matrix = None
    return storage


def _patch_source_scan_validation(monkeypatch) -> None:
    from mas_core.worker_registry import source_bound_security_scan

    # These endpoint tests cover boundary reloading and write ordering. The
    # scan validator itself has separate focused integrity tests.
    monkeypatch.setattr(
        source_bound_security_scan,
        "source_bound_security_scan_is_valid",
        lambda *_args, **_kwargs: True,
    )


@pytest.mark.anyio
async def test_candidate_generation_persists_matching_bundle_and_adapter_ids(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    state = _candidate_state()
    storage = _storage_for(state)
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/candidates",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "semantic_version": "0.1.2",
            "adapter_version": "0.1.2",
            "upstream_compatibility_range": "==0.1.2",
            "adapter_entrypoint": "ProcessAdapter",
            "implementation_ref": "candidate-source-revision",
        },
    )

    assert response.status_code == 201, response.text
    response_candidate = response.json()
    persisted_candidate = storage.create_skill_bundle_candidate.await_args.kwargs
    bundle_id = storage.create_skill_bundle.await_args.kwargs["bundle_id"]
    adapter_id = storage.create_runtime_adapter.await_args.kwargs["adapter_id"]
    assert response_candidate["bundle"]["bundle_id"] == str(bundle_id)
    assert response_candidate["adapter"]["adapter_id"] == str(adapter_id)
    assert persisted_candidate["skill_bundle_id"] == bundle_id
    assert persisted_candidate["adapter_id"] == adapter_id
    assert persisted_candidate["candidate_json"]["bundle"]["bundle_id"] == str(
        bundle_id
    )
    assert persisted_candidate["candidate_json"]["adapter"]["adapter_id"] == str(
        adapter_id
    )


def _certification_row(state, fingerprint: str) -> dict:
    return {
        "id": state["certification_id"],
        "worker_id": state["worker_id"],
        "candidate_id": state["candidate_id"],
        "status": "passed",
        "evidence_json": {
            "source_identity_fingerprint": fingerprint,
            "source_bound_security_scan_report_sha256": state["scan_report"]["report_sha256"],
        },
    }


def _persist_candidate_lifecycle(state) -> None:
    candidate = state["steward"].candidates[state["candidate_id"]]
    row = state["candidate_row"]
    row["intake_status"] = candidate.intake_status.value
    row["certification_run_id"] = candidate.certification_id
    row["approval_record_id"] = candidate.approval_record_id
    evidence = dict(row.get("evidence_json") or {})
    evidence["candidate_record"] = candidate.model_dump(mode="json")
    row["evidence_json"] = evidence


@pytest.mark.anyio
async def test_certification_does_not_write_pass_if_provenance_changes_during_conformance(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    storage = _storage_for(state)
    changed_provenance = dict(state["provenance_row"])
    changed_provenance["provenance_hash"] = "changed-during-conformance"
    storage.get_external_provenance_by_worker = AsyncMock(
        side_effect=[state["provenance_row"], changed_provenance]
    )
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )

    class FakeAdapter:
        capabilities = None

        async def close(self):
            return None

    class FakeConformanceRunner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs):
            return {"passed": True, "tests": [{"name": "deterministic-contract"}]}

    from mas_core import worker_contract
    from mas_core.worker_registry import runtime_adapters

    monkeypatch.setattr(runtime_adapters, "adapter_for_transport", lambda *_args, **_kwargs: FakeAdapter())
    monkeypatch.setattr(worker_contract, "ConformanceRunner", FakeConformanceRunner)

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/candidates/{state['candidate_id']}/certify",
        headers={"X-API-Key": "test-operator-key"},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANDIDATE_SOURCE_OR_LIFECYCLE_CHANGED_DURING_CONFORMANCE"
    assert state["candidate_id"] in state["steward"].candidates
    assert state["steward"].candidates[state["candidate_id"]].certification_id is None
    assert state["steward"].certifications == {}
    storage.persist_first_party_candidate_certification_atomically.assert_not_awaited()
    storage.create_certification_run.assert_not_awaited()
    assert storage.create_compatibility_matrix is None
    storage.update_skill_bundle_candidate.assert_not_awaited()
    storage.update_runtime_adapter.assert_not_awaited()


@pytest.mark.anyio
async def test_certification_rejects_first_party_candidate_without_fixed_conformance_snapshot(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    candidate = state["steward"].candidates[state["candidate_id"]]
    snapshot = candidate.bundle.verified_capabilities
    capabilities = snapshot.capabilities.model_copy(update={"extensions": {}})
    candidate.bundle = candidate.bundle.model_copy(
        update={
            "verified_capabilities": snapshot.model_copy(
                update={"capabilities": capabilities}
            )
        }
    )
    state["candidate_row"]["evidence_json"]["candidate_record"] = candidate.model_dump(
        mode="json"
    )

    storage = _storage_for(state)
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )
    conformance_calls = []

    class FakeAdapter:
        capabilities = None

        async def close(self):
            return None

    class FakeConformanceRunner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs):
            conformance_calls.append(True)
            return {"passed": True, "tests": [{"name": "universal-echo"}]}

    from mas_core import worker_contract
    from mas_core.worker_registry import runtime_adapters

    adapter_factory = MagicMock(return_value=FakeAdapter())
    monkeypatch.setattr(
        runtime_adapters,
        "adapter_for_transport",
        adapter_factory,
    )
    monkeypatch.setattr(worker_contract, "ConformanceRunner", FakeConformanceRunner)

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/candidates/{state['candidate_id']}/certify",
        headers={"X-API-Key": "test-operator-key"},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DETERMINISTIC_CONFORMANCE_CASE_REQUIRED"
    assert candidate.certification_id is None
    assert state["steward"].certifications == {}
    assert conformance_calls == []
    adapter_factory.assert_not_called()
    storage.persist_first_party_candidate_certification_atomically.assert_not_awaited()
    storage.create_certification_run.assert_not_awaited()


@pytest.mark.anyio
async def test_certification_does_not_write_pass_if_candidate_is_rejected_during_conformance(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    storage = _storage_for(state)
    rejected_row = {
        **state["candidate_row"],
        "intake_status": "REJECTED",
        "evidence_json": {
            **state["candidate_row"]["evidence_json"],
            "candidate_record": {
                **state["candidate_row"]["evidence_json"]["candidate_record"],
                "intake_status": "REJECTED",
            },
        },
    }
    storage.get_skill_bundle_candidate = AsyncMock(
        side_effect=[state["candidate_row"], rejected_row]
    )
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )

    class FakeAdapter:
        capabilities = None

        async def close(self):
            return None

    class FakeConformanceRunner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs):
            return {"passed": True, "tests": [{"name": "deterministic-contract"}]}

    from mas_core import worker_contract
    from mas_core.worker_registry import runtime_adapters

    monkeypatch.setattr(runtime_adapters, "adapter_for_transport", lambda *_args, **_kwargs: FakeAdapter())
    monkeypatch.setattr(worker_contract, "ConformanceRunner", FakeConformanceRunner)

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/candidates/{state['candidate_id']}/certify",
        headers={"X-API-Key": "test-operator-key"},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANDIDATE_LIFECYCLE_STALE"
    assert state["steward"].certifications == {}
    storage.persist_first_party_candidate_certification_atomically.assert_not_awaited()
    storage.create_certification_run.assert_not_awaited()
    storage.update_skill_bundle_candidate.assert_not_awaited()
    storage.update_runtime_adapter.assert_not_awaited()


@pytest.mark.anyio
async def test_certification_rechecks_candidate_at_persistent_write_boundary(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    rejected_row = {
        **state["candidate_row"],
        "intake_status": "REJECTED",
        "evidence_json": {
            **state["candidate_row"]["evidence_json"],
            "candidate_record": {
                **state["candidate_row"]["evidence_json"]["candidate_record"],
                "intake_status": "REJECTED",
            },
        },
    }
    storage = _storage_for(state)
    # Initial load and post-conformance check are current. The final read,
    # immediately before the certification row write, sees a persisted reject.
    storage.get_skill_bundle_candidate = AsyncMock(
        side_effect=[state["candidate_row"], state["candidate_row"], rejected_row]
    )
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )

    class FakeAdapter:
        capabilities = None

        async def close(self):
            return None

    class FakeConformanceRunner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs):
            return {"passed": True, "tests": [{"name": "deterministic-contract"}]}

    from mas_core import worker_contract
    from mas_core.worker_registry import runtime_adapters

    monkeypatch.setattr(runtime_adapters, "adapter_for_transport", lambda *_args, **_kwargs: FakeAdapter())
    monkeypatch.setattr(worker_contract, "ConformanceRunner", FakeConformanceRunner)

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/candidates/{state['candidate_id']}/certify",
        headers={"X-API-Key": "test-operator-key"},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANDIDATE_LIFECYCLE_STALE"
    storage.persist_first_party_candidate_certification_atomically.assert_not_awaited()
    storage.create_certification_run.assert_not_awaited()
    assert storage.create_compatibility_matrix is None
    storage.update_skill_bundle_candidate.assert_not_awaited()
    storage.update_runtime_adapter.assert_not_awaited()
    assert state["steward"].certifications == {}
    assert state["steward"].candidates[state["candidate_id"]].certification_id is None


@pytest.mark.anyio
async def test_unchanged_source_identity_can_be_certified_and_is_bound_in_evidence(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    storage = _storage_for(state)
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )

    class FakeAdapter:
        capabilities = None

        async def close(self):
            return None

    class FakeConformanceRunner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs):
            return {"passed": True, "tests": [{"name": "deterministic-contract"}]}

    from mas_core import worker_contract
    from mas_core.worker_registry import runtime_adapters

    monkeypatch.setattr(runtime_adapters, "adapter_for_transport", lambda *_args, **_kwargs: FakeAdapter())
    monkeypatch.setattr(worker_contract, "ConformanceRunner", FakeConformanceRunner)
    monkeypatch.setattr(
        main,
        "_candidate_worker_shell_values",
        AsyncMock(
            return_value={
                "id": uuid4(),
                "worker_id": state["worker_id"],
                "version": f"governed-{state['candidate_id']}",
                "content_hash": "f" * 64,
            }
        ),
    )

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/candidates/{state['candidate_id']}/certify",
        headers={"X-API-Key": "test-operator-key"},
        json={},
    )

    assert response.status_code == 200, response.text
    assert response.json()["passed"] is True
    storage.persist_first_party_candidate_certification_atomically.assert_awaited_once()
    storage.create_certification_run.assert_not_awaited()
    evidence = storage.persist_first_party_candidate_certification_atomically.await_args.kwargs[
        "certification"
    ]["evidence_json"]
    assert evidence["source_identity_fingerprint"]
    assert evidence["source_bound_security_scan_report_sha256"] == state["scan_report"]["report_sha256"]


@pytest.mark.anyio
async def test_approval_rejects_candidate_with_stale_embedded_scan_before_any_write(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    from mas_core.worker_registry.steward import CertificationRun

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    storage = _storage_for(state)
    changed_report = dict(state["scan_report"])
    changed_report["report_sha256"] = "22" * 32
    storage.get_steward_by_worker.return_value = {
        **state["steward_row"],
        "metadata": {
            "source_provenance_extra": {"source_bound_security_scan": changed_report}
        },
    }
    certification_id = uuid4()
    state["certification_id"] = certification_id
    state["steward"].candidates[state["candidate_id"]].certification_id = certification_id
    _persist_candidate_lifecycle(state)
    state["steward"].certifications[certification_id] = CertificationRun(
        certification_id=certification_id,
        candidate_id=state["candidate_id"],
        conformance={"passed": True},
        checks={"security": True},
        passed=True,
        completed_at=datetime.now(UTC),
    )
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/candidates/{state['candidate_id']}/approve",
        headers={"X-API-Key": "test-operator-key"},
        json={"decided_by": "operator", "reason": "reviewed candidate"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SECURITY_SCAN_EVIDENCE_STALE"
    storage.list_certification_runs.assert_not_awaited()
    storage.approve_first_party_candidate_atomically.assert_not_awaited()
    storage.create_approval_record.assert_not_awaited()
    storage.update_skill_bundle_candidate.assert_not_awaited()
    storage.update_skill_bundle.assert_not_awaited()
    storage.update_worker_config.assert_not_awaited()


@pytest.mark.anyio
async def test_approval_accepts_unchanged_candidate_with_matching_bound_certification(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    from mas_core.worker_registry.steward import CertificationRun

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    storage = _storage_for(state)
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )

    source_state = await main._first_party_candidate_source_state(
        storage,
        worker_id=state["worker_id"],
        candidate_id=state["candidate_id"],
        candidate_row=state["candidate_row"],
        runtime_candidate=state["steward"].candidates[state["candidate_id"]],
    )
    certification_id = uuid4()
    state["certification_id"] = certification_id
    state["steward"].candidates[state["candidate_id"]].certification_id = certification_id
    _persist_candidate_lifecycle(state)
    state["steward"].certifications[certification_id] = CertificationRun(
        certification_id=certification_id,
        candidate_id=state["candidate_id"],
        conformance={"passed": True},
        checks={"security": True},
        passed=True,
        completed_at=datetime.now(UTC),
    )
    storage.list_certification_runs.return_value = [
        {
            "id": certification_id,
            "worker_id": state["worker_id"],
            "candidate_id": state["candidate_id"],
            "status": "passed",
            "evidence_json": {
                "source_identity_fingerprint": source_state["identity_fingerprint"],
                "source_bound_security_scan_report_sha256": source_state["report_sha256"],
            },
        }
    ]

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/candidates/{state['candidate_id']}/approve",
        headers={"X-API-Key": "test-operator-key"},
        json={"decided_by": "forged-audit-actor", "reason": "reviewed candidate"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["intake_status"] == "APPROVED"
    storage.approve_first_party_candidate_atomically.assert_awaited_once()
    approval = storage.approve_first_party_candidate_atomically.await_args.kwargs[
        "approval"
    ]
    assert approval["decided_by"] == "operator"
    storage.create_approval_record.assert_not_awaited()
    storage.update_worker_config.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("runtime_status", "persisted_status", "expected_code"),
    [
        ("REJECTED", "REJECTED", "CANDIDATE_NOT_APPROVABLE"),
        ("CERTIFYING", "REJECTED", "CANDIDATE_LIFECYCLE_STALE"),
    ],
)
async def test_approval_rejects_rejected_or_stale_candidate_before_any_write(
    client, monkeypatch, runtime_status, persisted_status, expected_code
) -> None:
    from orchestrator_api import main

    from mas_core.worker_registry.steward import CandidateIntakeStatus, CertificationRun

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    candidate = state["steward"].candidates[state["candidate_id"]]
    certification_id = uuid4()
    state["certification_id"] = certification_id
    candidate.certification_id = certification_id
    candidate.intake_status = CandidateIntakeStatus(runtime_status)
    _persist_candidate_lifecycle(state)
    state["candidate_row"]["intake_status"] = persisted_status
    if persisted_status != runtime_status:
        evidence = dict(state["candidate_row"]["evidence_json"])
        evidence["candidate_record"] = {
            **evidence["candidate_record"],
            "intake_status": persisted_status,
        }
        state["candidate_row"]["evidence_json"] = evidence
    state["steward"].certifications[certification_id] = CertificationRun(
        certification_id=certification_id,
        candidate_id=state["candidate_id"],
        conformance={"passed": True},
        checks={"security": True},
        passed=True,
        completed_at=datetime.now(UTC),
    )
    storage = _storage_for(state)
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/candidates/{state['candidate_id']}/approve",
        headers={"X-API-Key": "test-operator-key"},
        json={"decided_by": "operator", "reason": "reviewed candidate"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == expected_code
    storage.approve_first_party_candidate_atomically.assert_not_awaited()
    storage.create_approval_record.assert_not_awaited()
    storage.update_skill_bundle_candidate.assert_not_awaited()
    storage.update_skill_bundle.assert_not_awaited()
    storage.update_runtime_adapter.assert_not_awaited()
    storage.update_worker_config.assert_not_awaited()


@pytest.mark.anyio
async def test_approval_rechecks_current_candidate_after_certification_lookup(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    from mas_core.worker_registry.steward import CertificationRun

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    candidate = state["steward"].candidates[state["candidate_id"]]
    certification_id = uuid4()
    candidate.certification_id = certification_id
    state["certification_id"] = certification_id
    _persist_candidate_lifecycle(state)
    state["steward"].certifications[certification_id] = CertificationRun(
        certification_id=certification_id,
        candidate_id=state["candidate_id"],
        conformance={"passed": True},
        checks={"security": True},
        passed=True,
        completed_at=datetime.now(UTC),
    )
    source_state = await main._first_party_candidate_source_state(
        _storage_for(state),
        worker_id=state["worker_id"],
        candidate_id=state["candidate_id"],
        candidate_row=state["candidate_row"],
        runtime_candidate=candidate,
    )
    rejected_row = {
        **state["candidate_row"],
        "intake_status": "REJECTED",
        "evidence_json": {
            **state["candidate_row"]["evidence_json"],
            "candidate_record": {
                **state["candidate_row"]["evidence_json"]["candidate_record"],
                "intake_status": "REJECTED",
            },
        },
    }
    storage = _storage_for(state)
    storage.get_skill_bundle_candidate = AsyncMock(
        side_effect=[state["candidate_row"], rejected_row]
    )
    storage.list_certification_runs.return_value = [
        _certification_row(state, source_state["identity_fingerprint"])
    ]
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/candidates/{state['candidate_id']}/approve",
        headers={"X-API-Key": "test-operator-key"},
        json={"decided_by": "operator", "reason": "reviewed candidate"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANDIDATE_LIFECYCLE_STALE"
    storage.approve_first_party_candidate_atomically.assert_not_awaited()
    storage.create_approval_record.assert_not_awaited()
    storage.update_skill_bundle_candidate.assert_not_awaited()
    storage.update_skill_bundle.assert_not_awaited()
    storage.update_runtime_adapter.assert_not_awaited()
    storage.update_worker_config.assert_not_awaited()


@pytest.mark.anyio
async def test_first_party_rollout_uses_the_exact_precheck_identity_and_provenance_hash(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    from mas_core.worker_registry.steward import RolloutRecord, RolloutStatus

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    storage = _storage_for(state)
    rollout_id = uuid4()
    state["steward"].rollouts[rollout_id] = RolloutRecord(
        rollout_id=rollout_id,
        worker_id=str(state["worker_id"]),
        steward_id=state["steward"].steward_id,
        candidate_id=state["candidate_id"],
        status=RolloutStatus.PROMOTING,
        sample_count=5,
    )
    storage.get_rollout_record = AsyncMock(
        return_value={
            "id": rollout_id,
            "candidate_id": state["candidate_id"],
            "status": RolloutStatus.PROMOTING.value,
        }
    )
    storage.activate_rollout_atomically = AsyncMock(return_value={"activated": True})
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )
    monkeypatch.setattr(main, "operational_promotion_checks", lambda *_args, **_kwargs: {})

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/rollouts/{rollout_id}/advance",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "target_status": "ACTIVE",
            "sample_count": 3,
            "comparison_metrics": {"regression_fraction": 0.0},
        },
    )

    assert response.status_code == 200
    kwargs = storage.activate_rollout_atomically.await_args.kwargs
    from mas_core.memory.storage import worker_identity_snapshot_for_rollout

    assert kwargs["expected_worker_identity"] == worker_identity_snapshot_for_rollout(
        state["worker"]
    )
    assert kwargs["expected_provenance_hash"] == state["provenance_row"]["provenance_hash"]


@pytest.mark.anyio
async def test_first_party_rollout_rejects_identity_change_between_precheck_reads(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    from mas_core.worker_registry.steward import RolloutRecord, RolloutStatus

    state = _candidate_state()
    _patch_source_scan_validation(monkeypatch)
    storage = _storage_for(state)
    changed_worker = {**state["worker"], "version_pin": "changed-before-activation"}
    storage.get_worker = AsyncMock(side_effect=[state["worker"], changed_worker])
    rollout_id = uuid4()
    state["steward"].rollouts[rollout_id] = RolloutRecord(
        rollout_id=rollout_id,
        worker_id=str(state["worker_id"]),
        steward_id=state["steward"].steward_id,
        candidate_id=state["candidate_id"],
        status=RolloutStatus.PROMOTING,
        sample_count=5,
    )
    storage.get_rollout_record = AsyncMock(
        return_value={
            "id": rollout_id,
            "candidate_id": state["candidate_id"],
            "status": RolloutStatus.PROMOTING.value,
        }
    )
    storage.activate_rollout_atomically = AsyncMock(return_value={"activated": True})
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(
        main, "_steward_runtime", AsyncMock(return_value=state["steward"])
    )
    monkeypatch.setattr(main, "operational_promotion_checks", lambda *_args, **_kwargs: {})

    response = await client.post(
        f"/capabilities/workers/{state['worker_id']}/steward/rollouts/{rollout_id}/advance",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "target_status": "ACTIVE",
            "sample_count": 3,
            "comparison_metrics": {"regression_fraction": 0.0},
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ROLLOUT_SOURCE_IDENTITY_CHANGED_DURING_PRECHECK"
    storage.activate_rollout_atomically.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("guard_case", "expected_status", "expected_code"),
    [
        ("runtime_steward_worker", 404, None),
        ("runtime_candidate_id", 404, None),
        ("runtime_candidate_worker", 404, None),
        ("persisted_row_missing", 404, "CANDIDATE_WORKER_BINDING_MISMATCH"),
        ("persisted_row_candidate", 404, "CANDIDATE_WORKER_BINDING_MISMATCH"),
        ("persisted_row_worker", 404, "CANDIDATE_WORKER_BINDING_MISMATCH"),
        ("embedded_record_missing", 404, "CANDIDATE_WORKER_BINDING_MISMATCH"),
        ("embedded_record_candidate", 404, "CANDIDATE_WORKER_BINDING_MISMATCH"),
        ("embedded_record_worker", 404, "CANDIDATE_WORKER_BINDING_MISMATCH"),
        ("legacy_lifecycle", 409, "CANDIDATE_LIFECYCLE_STALE"),
    ],
)
async def test_certification_fails_closed_on_runtime_and_persisted_candidate_bindings(
    client, monkeypatch, guard_case, expected_status, expected_code
) -> None:
    from orchestrator_api import main

    from mas_core import worker_contract
    from mas_core.worker_registry import runtime_adapters

    state = _candidate_state()
    worker_id = state["worker_id"]
    candidate_id = state["candidate_id"]
    steward = state["steward"]
    storage = _storage_for(state)
    runtime_steward = steward

    if guard_case == "runtime_steward_worker":
        from mas_core.worker_registry.steward import ExternalWorkerSteward

        runtime_steward = ExternalWorkerSteward(
            worker_id=str(uuid4()),
            provenance=steward.provenance,
            steward_id=steward.steward_id,
            status=steward.status,
        )
    elif guard_case == "runtime_candidate_id":
        candidate = steward.candidates[candidate_id]
        steward.candidates[candidate_id] = candidate.model_copy(
            update={"candidate_id": uuid4()}
        )
    elif guard_case == "runtime_candidate_worker":
        candidate = steward.candidates[candidate_id]
        steward.candidates[candidate_id] = candidate.model_copy(
            update={"worker_id": str(uuid4())}
        )
    elif guard_case == "persisted_row_missing":
        storage.get_skill_bundle_candidate = AsyncMock(return_value=None)
    elif guard_case == "persisted_row_candidate":
        state["candidate_row"]["id"] = uuid4()
    elif guard_case == "persisted_row_worker":
        state["candidate_row"]["worker_id"] = uuid4()
    elif guard_case == "embedded_record_missing":
        state["candidate_row"]["evidence_json"] = {}
    elif guard_case in {"embedded_record_candidate", "embedded_record_worker"}:
        record = state["candidate_row"]["evidence_json"]["candidate_record"]
        field = "candidate_id" if guard_case == "embedded_record_candidate" else "worker_id"
        record[field] = str(uuid4())
    elif guard_case == "legacy_lifecycle":
        from mas_core.worker_registry.steward import ExternalProvenance

        legacy_provenance = ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            source_provider="github",
            exact_release="1.0.0",
            transport_type="process",
            security_scan_status="passed",
        )
        candidate = steward.candidates[candidate_id].model_copy(
            update={"source_provenance": legacy_provenance}
        )
        candidates = dict(steward.candidates)
        candidates[candidate_id] = candidate
        from mas_core.worker_registry.steward import ExternalWorkerSteward

        runtime_steward = ExternalWorkerSteward(
            worker_id=steward.worker_id,
            provenance=legacy_provenance,
            steward_id=steward.steward_id,
            status=steward.status,
        )
        runtime_steward.candidates.update(candidates)
        state["worker"]["name"] = "legacy_external_worker"
        state["candidate_row"]["intake_status"] = "SOURCE_REVIEW"
        state["candidate_row"]["evidence_json"]["candidate_record"] = (
            candidate.model_dump(mode="json")
        )

    cache = {str(worker_id): runtime_steward}
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=runtime_steward))
    monkeypatch.setattr(main, "_worker_steward_runtimes", cache)
    adapter_factory = MagicMock(side_effect=AssertionError("adapter must not be constructed"))
    monkeypatch.setattr(runtime_adapters, "adapter_for_transport", adapter_factory)
    monkeypatch.setattr(
        worker_contract,
        "ConformanceRunner",
        MagicMock(side_effect=AssertionError("conformance must not run")),
    )

    response = await client.post(
        f"/capabilities/workers/{worker_id}/steward/candidates/{candidate_id}/certify",
        headers={"X-API-Key": "test-operator-key"},
        json={},
    )

    assert response.status_code == expected_status
    if expected_code is not None:
        assert response.json()["detail"]["code"] == expected_code
    adapter_factory.assert_not_called()
    storage.persist_first_party_candidate_certification_atomically.assert_not_awaited()
    storage.create_certification_run.assert_not_awaited()
    assert steward.certifications == {}
    assert steward.candidates[candidate_id].certification_id is None
    if guard_case != "legacy_lifecycle":
        assert str(worker_id) not in cache
    if guard_case.startswith("runtime_"):
        storage.get_skill_bundle_candidate.assert_not_awaited()
    else:
        storage.get_skill_bundle_candidate.assert_awaited_once()
