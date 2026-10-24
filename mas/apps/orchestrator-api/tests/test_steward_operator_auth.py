"""Operator-only authorization for mutating worker-steward governance."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

WORKER_ID = UUID("00000000-0000-4000-a000-0000000000a1")
CANDIDATE_ID = UUID("00000000-0000-4000-a000-0000000000a2")
ROLLOUT_ID = UUID("00000000-0000-4000-a000-0000000000a3")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            f"/capabilities/workers/{WORKER_ID}/steward",
            {"source_repo": "https://github.com/example/opencode", "exact_release": "1.18.21"},
        ),
        (f"/capabilities/workers/{WORKER_ID}/steward/monitor", None),
        (
            f"/capabilities/workers/{WORKER_ID}/steward/documentation",
            {"uri": "https://example.invalid/docs", "version": "1", "content_sha256": "a" * 64},
        ),
        (f"/capabilities/workers/{WORKER_ID}/steward/capabilities", {"capabilities": {}}),
        (
            f"/capabilities/workers/{WORKER_ID}/steward/candidates",
            {
                "semantic_version": "1.0.0",
                "adapter_version": "1.0.0",
                "upstream_compatibility_range": "==1.18.21",
            },
        ),
        (
            f"/capabilities/workers/{WORKER_ID}/steward/candidates/{CANDIDATE_ID}/stage",
            {"target_status": "SOURCE_REVIEW", "actor": "forged-operator"},
        ),
        (
            f"/capabilities/workers/{WORKER_ID}/steward/candidates/{CANDIDATE_ID}/certify",
            {"conformance": {"passed": True}, "checks": {"security": True}},
        ),
        (
            f"/capabilities/workers/{WORKER_ID}/steward/candidates/{CANDIDATE_ID}/approve",
            {"decided_by": "forged-operator", "reason": "forged approval"},
        ),
        (
            f"/capabilities/workers/{WORKER_ID}/steward/rollouts",
            {"actor": "forged-operator", "eligible_task_classes": ["read_only"]},
        ),
        (
            f"/capabilities/workers/{WORKER_ID}/steward/rollouts/{ROLLOUT_ID}/advance",
            {"target_status": "SHADOW", "sample_count": 0},
        ),
        (
            f"/capabilities/workers/{WORKER_ID}/steward/rollouts/{ROLLOUT_ID}/rollback",
            {"reason": "forged rollback"},
        ),
    ],
)
async def test_steward_mutations_reject_shared_service_principal(client, path, body) -> None:
    """A body actor field cannot elevate the MAS_API_KEY service principal."""
    from orchestrator_api import main

    storage = MagicMock()
    storage.create_approval_record = AsyncMock()
    previous_storage = getattr(main.app.state, "storage", None)
    main.app.state.storage = storage
    try:
        response = await client.post(path, json=body) if body is not None else await client.post(path)
    finally:
        main.app.state.storage = previous_storage

    assert response.status_code == 403
    assert response.json()["detail"] == "canonical mutation requires an operator credential"
    storage.create_approval_record.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"action": "ACTIVATE"},
        {"action": "RECLASSIFY", "new_role": "ToolAgent"},
    ],
)
async def test_worker_status_mutations_reject_shared_service_principal(client, body) -> None:
    """Service credentials cannot activate or reclassify a worker."""
    from orchestrator_api import main

    worker = {"id": WORKER_ID, "status": "INACTIVE", "adapter_entrypoint": "WorkerAgent"}
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker)
    storage.update_worker_status = AsyncMock()
    storage.update_worker_config = AsyncMock()
    previous_storage = getattr(main.app.state, "storage", None)
    main.app.state.storage = storage
    try:
        response = await client.patch(
            f"/capabilities/workers/{WORKER_ID}/status",
            json=body,
        )
    finally:
        main.app.state.storage = previous_storage

    assert response.status_code == 403
    assert worker["status"] == "INACTIVE"
    storage.get_worker.assert_not_awaited()
    storage.update_worker_status.assert_not_awaited()
    storage.update_worker_config.assert_not_awaited()


@pytest.mark.anyio
async def test_operator_activation_still_requires_governed_records(client, monkeypatch) -> None:
    """Operator identity does not weaken the existing activation predicate."""
    from orchestrator_api import main

    worker = {
        "id": WORKER_ID,
        "status": "INACTIVE",
        "source_repo": "local",
        "version_pin": None,
        "adapter_config": {},
        "model_mode": "none",
    }
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker)
    storage.get_worker_shell_version = AsyncMock(return_value=None)
    storage.get_runtime_adapter = AsyncMock(return_value=None)
    storage.get_skill_bundle = AsyncMock(return_value=None)
    storage.list_capability_snapshots = AsyncMock(return_value=[])
    storage.update_worker_status = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)

    response = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        headers={"X-API-Key": "test-operator-key"},
        json={"action": "ACTIVATE"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "WORKER_ACTIVATION_GOVERNANCE_BLOCKED"
    assert "missing active immutable WorkerShell" in response.json()["detail"]["blockers"]
    storage.update_worker_status.assert_not_awaited()


@pytest.mark.anyio
async def test_operator_can_deactivate_worker(client, monkeypatch) -> None:
    """Operator-only protection keeps normal deactivation available."""
    from orchestrator_api import main

    active = {"id": WORKER_ID, "status": "ACTIVE"}
    inactive = {"id": WORKER_ID, "status": "INACTIVE"}
    storage = MagicMock()
    storage.get_worker = AsyncMock(side_effect=[active, inactive])
    storage.update_worker_status = AsyncMock(return_value=None)
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_suspend_worker_identity", AsyncMock(return_value=None))

    response = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        headers={"X-API-Key": "test-operator-key"},
        json={"action": "DEACTIVATE"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "INACTIVE"
    storage.update_worker_status.assert_awaited_once_with(WORKER_ID, status="INACTIVE")


@pytest.mark.anyio
async def test_operator_can_create_candidate_through_steward_api(client, monkeypatch) -> None:
    """The operator credential can use the same canonical candidate endpoint."""
    from orchestrator_api import main

    from mas_core.worker_registry.steward import ExternalProvenance, ExternalWorkerSteward

    steward = ExternalWorkerSteward(
        worker_id=str(WORKER_ID),
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/opencode",
            exact_release="1.18.21",
            transport_type="process",
            security_scan_status="pending",
        ),
    )
    storage = MagicMock()
    storage.create_skill_bundle = AsyncMock(return_value={"id": UUID("00000000-0000-4000-a000-0000000000b1")})
    storage.create_runtime_adapter = AsyncMock(return_value={"id": UUID("00000000-0000-4000-a000-0000000000b2")})
    storage.create_skill_bundle_candidate = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/candidates",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "semantic_version": "1.0.0",
            "adapter_version": "1.0.0",
            "upstream_compatibility_range": "==1.18.21",
            "implementation_ref": "opencode-adapter@candidate",
        },
    )

    assert response.status_code == 201
    candidate = response.json()
    assert candidate["intake_status"] == "DISCOVERED"
    assert candidate["source_provenance"]["exact_release"] == "1.18.21"
    storage.create_skill_bundle.assert_awaited_once()
    storage.create_runtime_adapter.assert_awaited_once()
    storage.create_skill_bundle_candidate.assert_awaited_once()


@pytest.mark.anyio
async def test_candidate_stage_audit_actor_uses_authenticated_principal(client, monkeypatch) -> None:
    """A request body cannot assign a false actor to an intake transition."""
    from orchestrator_api import main

    from mas_core.worker_registry.steward import ExternalProvenance, ExternalWorkerSteward

    steward = ExternalWorkerSteward(
        worker_id=str(WORKER_ID),
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/opencode",
            exact_release="1.18.21",
            transport_type="process",
        ),
    )
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.18.21",
    )
    storage = MagicMock()
    storage.get_skill_bundle_candidate = AsyncMock(
        return_value={"id": candidate.candidate_id, "evidence_json": {}}
    )
    storage.update_skill_bundle_candidate = AsyncMock(
        return_value={"id": candidate.candidate_id}
    )
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/candidates/{candidate.candidate_id}/stage",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "target_status": "SOURCE_REVIEW",
            "actor": "forged-security-reviewer",
            "evidence": {"review": "source identified"},
        },
    )

    assert response.status_code == 200, response.text
    persisted_candidate = storage.update_skill_bundle_candidate.await_args.kwargs[
        "evidence"
    ]["candidate_record"]
    transition = persisted_candidate["evidence"]["intake_transitions"][-1]
    assert transition["actor"] == "operator"
    assert transition["actor"] != "forged-security-reviewer"


@pytest.mark.anyio
async def test_rollout_creation_audit_actor_uses_authenticated_principal(client, monkeypatch) -> None:
    """A request body cannot assign a false promotion actor to a rollout."""
    from orchestrator_api import main

    from mas_core.worker_registry.steward import (
        CandidateIntakeStatus,
        ExternalProvenance,
        ExternalWorkerSteward,
        StewardStatus,
    )

    steward = ExternalWorkerSteward(
        worker_id=str(WORKER_ID),
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/opencode",
            source_provider="github",
            exact_release="1.18.21",
            transport_type="process",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="operator")
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.18.21",
    )
    for stage in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(candidate.candidate_id, stage)
    certification = steward.certify_candidate(
        candidate.candidate_id,
        conformance={"passed": True},
        checks={},
    )
    assert certification.passed
    steward.approve_candidate(candidate.candidate_id)
    assert candidate.intake_status is CandidateIntakeStatus.APPROVED
    candidate_row, certification_row, approval_row = _rollout_persistence_rows(
        steward, candidate, certification
    )

    storage = MagicMock()
    storage.list_rollout_records = AsyncMock(return_value=[])
    storage.get_skill_bundle_candidate = AsyncMock(return_value=candidate_row)
    storage.list_certification_runs = AsyncMock(return_value=[certification_row])
    storage.get_approval_record = AsyncMock(return_value=approval_row)
    async def persist_rollout(**kwargs):
        assert steward.rollouts == {}
        assert kwargs["expected_candidate_snapshot"] == candidate.model_dump(
            mode="json"
        )
        return {"id": kwargs["rollout_id"]}

    storage.create_rollout_record = AsyncMock(side_effect=persist_rollout)
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "actor": "forged-rollout-approver",
            "eligible_task_classes": ["read_only"],
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["promotion_actor"] == "operator"
    assert storage.create_rollout_record.await_args.kwargs["promotion_actor"] == "operator"
    assert response.json()["rollout_id"] in {
        str(rollout_id) for rollout_id in steward.rollouts
    }


def _capability_recording_mocks(monkeypatch, *, worker_name: str):
    """Stub the local storage/runtime used by capability recording routes."""
    from orchestrator_api import main

    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value={"id": WORKER_ID, "name": worker_name})
    storage.create_capability_snapshot = AsyncMock(
        return_value={"id": UUID("00000000-0000-4000-a000-0000000000c1")}
    )
    steward = MagicMock()
    steward.steward_id = UUID("00000000-0000-4000-a000-0000000000c2")
    steward.record_capabilities = MagicMock()

    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    return storage, steward


@pytest.mark.anyio
async def test_api_records_canonical_conformance_declaration_for_file_worker(
    client, monkeypatch
) -> None:
    """The canonical worker's fixed conformance case is included in its snapshot."""
    from mas_core.worker_contract.conformance import (
        DETERMINISTIC_FILE_CONFORMANCE_DECLARATION,
    )

    storage, steward = _capability_recording_mocks(
        monkeypatch, worker_name="aiat_deterministic_file_worker"
    )
    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/capabilities",
        headers={"X-API-Key": "test-operator-key"},
        json={"capabilities": {"capability_names": ["deterministic.file_generate"]}},
    )

    assert response.status_code == 201, response.text
    capabilities = response.json()["capabilities"]
    assert capabilities["extensions"]["aiat.conformance"] == (
        DETERMINISTIC_FILE_CONFORMANCE_DECLARATION
    )
    steward.record_capabilities.assert_called_once()
    stored_capabilities = storage.create_capability_snapshot.await_args.kwargs[
        "capabilities"
    ]
    assert stored_capabilities["extensions"]["aiat.conformance"] == (
        DETERMINISTIC_FILE_CONFORMANCE_DECLARATION
    )
    assert response.json()["persisted"]["id"] == "00000000-0000-4000-a000-0000000000c1"


@pytest.mark.anyio
async def test_api_rejects_conflicting_file_worker_conformance_declaration(
    client, monkeypatch
) -> None:
    """A caller cannot replace the canonical file-worker conformance case."""
    storage, steward = _capability_recording_mocks(
        monkeypatch, worker_name="aiat_deterministic_file_worker"
    )
    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/capabilities",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "capabilities": {
                "capability_names": ["deterministic.file_generate"],
                "extensions": {
                    "aiat.conformance": {
                        "schema_version": "aiat.conformance.v1",
                        "case_id": "caller.supplied.case",
                    }
                },
            }
        },
    )

    assert response.status_code == 422
    assert "declaration is not canonical" in response.json()["detail"]
    steward.record_capabilities.assert_not_called()
    storage.create_capability_snapshot.assert_not_awaited()


@pytest.mark.anyio
async def test_api_does_not_add_file_worker_conformance_to_other_workers(
    client, monkeypatch
) -> None:
    """Only the canonical file worker receives AIAT's fixed declaration."""
    storage, steward = _capability_recording_mocks(
        monkeypatch, worker_name="another_worker"
    )
    declared = {
        "capability_names": ["deterministic.file_generate"],
        "extensions": {"vendor.extension": {"mode": "preserve"}},
    }
    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/capabilities",
        headers={"X-API-Key": "test-operator-key"},
        json={"capabilities": declared},
    )

    assert response.status_code == 201, response.text
    assert response.json()["capabilities"]["extensions"] == declared["extensions"]
    stored_capabilities = storage.create_capability_snapshot.await_args.kwargs[
        "capabilities"
    ]
    assert stored_capabilities["extensions"] == declared["extensions"]
    assert "aiat.conformance" not in stored_capabilities["extensions"]
    steward.record_capabilities.assert_called_once()


def _candidate_with_persisted_pass(*, rejected: bool = False):
    from mas_core.worker_registry.steward import (
        CandidateIntakeStatus,
        ExternalProvenance,
        ExternalWorkerSteward,
    )

    steward = ExternalWorkerSteward(
        worker_id=str(WORKER_ID),
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            source_provider="github",
            exact_release="1.0.0",
            transport_type="process",
            security_scan_status="passed",
        ),
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
    certification = steward.certify_candidate(
        candidate.candidate_id,
        conformance={"passed": True},
        checks={},
    )
    assert certification.passed
    if rejected:
        candidate.intake_status = CandidateIntakeStatus.REJECTED
    return steward, candidate, certification


@pytest.mark.anyio
async def test_candidate_approval_rejects_failed_lifecycle_before_writing_approval(
    client, monkeypatch
) -> None:
    """A cached pass cannot approve a candidate already in a rejected stage."""
    from orchestrator_api import main

    from mas_core.worker_registry.steward import CandidateIntakeStatus

    steward, candidate, certification = _candidate_with_persisted_pass(rejected=True)
    persisted_candidate = {
        "id": candidate.candidate_id,
        "worker_id": WORKER_ID,
        "intake_status": CandidateIntakeStatus.REJECTED.value,
        "certification_run_id": certification.certification_id,
        "approval_record_id": None,
        "evidence_json": {"candidate_record": candidate.model_dump(mode="json")},
    }
    storage = MagicMock()
    storage.get_skill_bundle_candidate = AsyncMock(return_value=persisted_candidate)
    storage.create_approval_record = AsyncMock(return_value={"id": UUID("00000000-0000-4000-a000-0000000000d1")})
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    monkeypatch.setattr(main, "_worker_steward_runtimes", {})

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/candidates/{candidate.candidate_id}/approve",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "decided_by": "forged-operator",
            "reason": "caller claims this is approved",
            "evidence": {"passed": True},
            "passed": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANDIDATE_NOT_APPROVABLE"
    assert candidate.intake_status is CandidateIntakeStatus.REJECTED
    assert candidate.approval_record_id is None
    storage.create_approval_record.assert_not_awaited()


@pytest.mark.anyio
async def test_candidate_approval_rejects_caller_pass_without_persisted_certification(
    client, monkeypatch
) -> None:
    """A request pass flag cannot replace the durable server certification row."""
    from orchestrator_api import main

    steward, candidate, certification = _candidate_with_persisted_pass()
    persisted_candidate = {
        "id": candidate.candidate_id,
        "worker_id": WORKER_ID,
        "intake_status": candidate.intake_status.value,
        "certification_run_id": certification.certification_id,
        "approval_record_id": None,
        "evidence_json": {"candidate_record": candidate.model_dump(mode="json")},
    }
    storage = MagicMock()
    storage.get_skill_bundle_candidate = AsyncMock(return_value=persisted_candidate)
    storage.get_worker = AsyncMock(
        return_value={"id": WORKER_ID, "name": "external_worker"}
    )
    storage.list_certification_runs = AsyncMock(return_value=[])
    storage.create_approval_record = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    monkeypatch.setattr(main, "_worker_steward_runtimes", {})

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/candidates/{candidate.candidate_id}/approve",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "decided_by": "forged-operator",
            "reason": "caller claims this is approved",
            "passed": True,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANDIDATE_CERTIFICATION_EVIDENCE_INVALID"
    assert candidate.intake_status.value == "CERTIFYING"
    assert candidate.approval_record_id is None
    storage.create_approval_record.assert_not_awaited()


@pytest.mark.anyio
async def test_candidate_approval_uses_persisted_certification_and_authenticated_actor(
    client, monkeypatch
) -> None:
    """Approval is a separate operator decision backed by a persisted pass."""
    from orchestrator_api import main

    steward, candidate, certification = _candidate_with_persisted_pass()
    persisted_candidate = {
        "id": candidate.candidate_id,
        "worker_id": WORKER_ID,
        "skill_bundle_id": candidate.bundle.bundle_id,
        "adapter_id": candidate.adapter.adapter_id,
        "intake_status": candidate.intake_status.value,
        "certification_run_id": certification.certification_id,
        "approval_record_id": None,
        "evidence_json": {"candidate_record": candidate.model_dump(mode="json")},
    }
    storage = MagicMock()
    storage.get_skill_bundle_candidate = AsyncMock(return_value=persisted_candidate)
    storage.get_worker = AsyncMock(
        return_value={"id": WORKER_ID, "name": "external_worker"}
    )
    storage.list_certification_runs = AsyncMock(
        return_value=[
            {
                "id": certification.certification_id,
                "worker_id": WORKER_ID,
                "candidate_id": candidate.candidate_id,
                "status": "passed",
            }
        ]
    )
    async def approve_atomically(**kwargs):
        # The route must not expose the proposed approval in its shared runtime
        # cache while the durable transaction is pending.
        assert steward.candidates[candidate.candidate_id].intake_status.value == "CERTIFYING"
        assert kwargs["approval"]["decided_by"] == "operator"
        approval_id = kwargs["approval"]["id"]
        approved_record = kwargs["candidate_record_after"]
        approved_row = {
            **persisted_candidate,
            "intake_status": "APPROVED",
            "approval_record_id": approval_id,
            "evidence_json": {"candidate_record": approved_record},
        }
        return {
            "candidate": approved_row,
            "approval": kwargs["approval"],
            "idempotent": False,
        }

    storage.approve_steward_candidate_atomically = AsyncMock(
        side_effect=approve_atomically
    )
    storage.create_approval_record = AsyncMock()
    storage.update_skill_bundle_candidate = AsyncMock()
    storage.update_skill_bundle = AsyncMock()
    storage.update_runtime_adapter = AsyncMock()
    storage.update_worker_config = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    monkeypatch.setattr(main, "_worker_steward_runtimes", {})

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/candidates/{candidate.candidate_id}/approve",
        headers={"X-API-Key": "test-operator-key"},
        json={
            "decided_by": "forged-operator",
            "reason": "operator reviewed the candidate",
            "evidence": {"review": "complete"},
            "passed": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["intake_status"] == "APPROVED"
    storage.approve_steward_candidate_atomically.assert_awaited_once()
    assert response.json()["approval_record_id"] == str(
        storage.approve_steward_candidate_atomically.await_args.kwargs["approval"]["id"]
    )
    assert (
        storage.approve_steward_candidate_atomically.await_args.kwargs[
            "approval"
        ]["decided_by"]
        == "operator"
    )
    assert (
        storage.approve_steward_candidate_atomically.await_args.kwargs[
            "approval"
        ]["decided_by"]
        != "forged-operator"
    )
    storage.create_approval_record.assert_not_awaited()
    assert storage.list_certification_runs.await_args.args == (WORKER_ID,)
    storage.update_skill_bundle_candidate.assert_not_awaited()
    storage.update_skill_bundle.assert_not_awaited()
    storage.update_runtime_adapter.assert_not_awaited()
    storage.update_worker_config.assert_not_awaited()
    assert steward.candidates[candidate.candidate_id].intake_status.value == "APPROVED"

@pytest.mark.anyio
async def test_rollout_creation_discards_runtime_when_persistence_fails(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    steward, candidate, certification = _approved_rollout_fixture()
    candidate_row, certification_row, approval_row = _rollout_persistence_rows(
        steward, candidate, certification
    )
    storage = MagicMock()
    storage.list_rollout_records = AsyncMock(return_value=[])
    storage.get_skill_bundle_candidate = AsyncMock(return_value=candidate_row)
    storage.list_certification_runs = AsyncMock(return_value=[certification_row])
    storage.get_approval_record = AsyncMock(return_value=approval_row)
    storage.create_rollout_record = AsyncMock(
        side_effect=RuntimeError("offline failure")
    )
    runtime_cache = {str(WORKER_ID): steward}
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    monkeypatch.setattr(main, "_worker_steward_runtimes", runtime_cache)

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts",
        headers={"X-API-Key": "test-operator-key"},
        json={"actor": "forged-actor", "eligible_task_classes": ["read_only"]},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ROLLOUT_PERSISTENCE_FAILED"
    assert steward.rollouts == {}
    assert str(WORKER_ID) not in runtime_cache

def _approved_rollout_fixture(worker_id: UUID = WORKER_ID):
    from mas_core.worker_registry.steward import (
        CandidateIntakeStatus,
        ExternalProvenance,
        ExternalWorkerSteward,
        StewardStatus,
    )

    steward = ExternalWorkerSteward(
        worker_id=str(worker_id),
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/opencode",
            source_provider="github",
            exact_release="1.18.21",
            transport_type="process",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="operator")
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.18.21",
    )
    for stage in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(candidate.candidate_id, stage)
    certification = steward.certify_candidate(
        candidate.candidate_id,
        conformance={"passed": True},
        checks={},
    )
    assert certification.passed
    steward.approve_candidate(candidate.candidate_id)
    return steward, candidate, certification

def _rollout_persistence_rows(steward, candidate, certification):
    candidate_row = {
        "id": candidate.candidate_id,
        "worker_id": UUID(str(candidate.worker_id)),
        "skill_bundle_id": candidate.bundle.bundle_id,
        "adapter_id": candidate.adapter.adapter_id,
        "intake_status": "APPROVED",
        "certification_run_id": candidate.certification_id,
        "approval_record_id": candidate.approval_record_id,
        "evidence_json": {"candidate_record": candidate.model_dump(mode="json")},
    }
    certification_row = {
        "id": certification.certification_id,
        "worker_id": UUID(str(candidate.worker_id)),
        "candidate_id": candidate.candidate_id,
        "steward_id": steward.steward_id,
        "status": "passed",
    }
    approval_row = {
        "id": candidate.approval_record_id,
        "scope_type": "worker_skill_bundle_candidate",
        "scope_id": candidate.candidate_id,
        "decision": "APPROVED",
    }
    return candidate_row, certification_row, approval_row

@pytest.mark.anyio
@pytest.mark.parametrize(
    "broken_binding",
    ["candidate_worker", "embedded_candidate", "certification", "approval"],
)
async def test_rollout_rejects_unbound_candidate_before_runtime_mutation(
    client, monkeypatch, broken_binding
) -> None:
    from orchestrator_api import main

    steward, candidate, certification = _approved_rollout_fixture()
    candidate_row, certification_row, approval_row = _rollout_persistence_rows(
        steward, candidate, certification
    )
    if broken_binding == "candidate_worker":
        candidate_row["worker_id"] = UUID("00000000-0000-4000-a000-000000000099")
    elif broken_binding == "embedded_candidate":
        candidate_row["evidence_json"]["candidate_record"]["steward_id"] = str(
            UUID("00000000-0000-4000-a000-000000000099")
        )
    elif broken_binding == "certification":
        certification_row["candidate_id"] = UUID(
            "00000000-0000-4000-a000-000000000099"
        )
    elif broken_binding == "approval":
        approval_row["scope_id"] = UUID("00000000-0000-4000-a000-000000000099")

    storage = MagicMock()
    storage.list_rollout_records = AsyncMock(return_value=[])
    storage.get_skill_bundle_candidate = AsyncMock(return_value=candidate_row)
    storage.list_certification_runs = AsyncMock(return_value=[certification_row])
    storage.get_approval_record = AsyncMock(return_value=approval_row)
    storage.create_rollout_record = AsyncMock(return_value={})
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    runtime_cache = {str(WORKER_ID): steward}
    monkeypatch.setattr(main, "_worker_steward_runtimes", runtime_cache)
    start_rollout = MagicMock(wraps=steward.start_rollout)
    monkeypatch.setattr(steward, "start_rollout", start_rollout)

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts",
        headers={"X-API-Key": "test-operator-key"},
        json={"actor": "forged-actor", "eligible_task_classes": ["read_only"]},
    )

    assert response.status_code == 409, response.text
    start_rollout.assert_not_called()
    storage.create_rollout_record.assert_not_awaited()
    assert steward.rollouts == {}
    assert str(WORKER_ID) not in runtime_cache

def _pending_rollout_fixture(steward, candidate):
    from mas_core.worker_registry.steward import RolloutRecord, RolloutStatus

    rollout = RolloutRecord(
        worker_id=str(WORKER_ID),
        steward_id=steward.steward_id,
        candidate_id=candidate.candidate_id,
        status=RolloutStatus.PENDING,
    )
    steward.rollouts[rollout.rollout_id] = rollout
    return rollout

def _persisted_rollout_fixture(rollout):
    return {
        "id": rollout.rollout_id,
        "worker_id": UUID(str(rollout.worker_id)),
        "steward_id": rollout.steward_id,
        "candidate_id": rollout.candidate_id,
        "status": rollout.status.value,
        "eligible_task_classes": list(rollout.eligible_task_classes),
        "sample_count": rollout.sample_count,
        "sample_targets": {
            "shadow": rollout.shadow_sample_target,
            "readonly_canary": rollout.readonly_canary_sample_target,
            "live_canary": rollout.live_canary_sample_target,
        },
        "comparison_metrics": dict(rollout.comparison_metrics),
        "rollback_thresholds": dict(rollout.rollback_thresholds),
        "in_flight_policy": rollout.in_flight_policy,
        "promotion_actor": rollout.promotion_actor,
        "rollback_reason": rollout.rollback_reason,
    }

@pytest.mark.anyio
async def test_rollout_advance_persists_worker_steward_candidate_scope(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    steward, candidate, _certification = _approved_rollout_fixture()
    rollout = _pending_rollout_fixture(steward, candidate)
    persisted = _persisted_rollout_fixture(rollout)
    storage = MagicMock()

    async def persist_transition(*_args, **kwargs):
        assert rollout.status.value == "PENDING"
        assert kwargs["expected_rollout_snapshot"] == persisted
        return {**persisted, "status": "SHADOW"}

    storage.get_rollout_record = AsyncMock(return_value=persisted)
    storage.transition_rollout = AsyncMock(side_effect=persist_transition)
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts/{rollout.rollout_id}/advance",
        headers={"X-API-Key": "test-operator-key"},
        json={"target_status": "SHADOW", "sample_count": 0},
    )

    assert response.status_code == 200, response.text
    kwargs = storage.transition_rollout.await_args.kwargs
    assert kwargs["worker_id"] == WORKER_ID
    assert kwargs["steward_id"] == steward.steward_id
    assert kwargs["candidate_id"] == candidate.candidate_id
    assert kwargs["expected_status"] == "PENDING"
    assert kwargs["actor"] == "operator"
    assert rollout.status.value == "PENDING"
    assert steward.rollouts[rollout.rollout_id].status.value == "SHADOW"

@pytest.mark.anyio
@pytest.mark.parametrize(
    "broken_binding",
    [
        "other_worker",
        "other_steward",
        "other_candidate",
        "runtime_worker",
        "candidate_owner",
        "runtime_status",
        "persisted_sample_target_changed",
    ],
)
async def test_rollout_advance_rejects_unbound_or_stale_rollout_before_mutation(
    client, monkeypatch, broken_binding
) -> None:
    from orchestrator_api import main

    steward, candidate, _certification = _approved_rollout_fixture()
    rollout = _pending_rollout_fixture(steward, candidate)
    persisted = _persisted_rollout_fixture(rollout)
    if broken_binding == "other_worker":
        persisted["worker_id"] = UUID("00000000-0000-4000-a000-000000000099")
    elif broken_binding == "other_steward":
        persisted["steward_id"] = UUID("00000000-0000-4000-a000-000000000099")
    elif broken_binding == "other_candidate":
        persisted["candidate_id"] = UUID("00000000-0000-4000-a000-000000000099")
    elif broken_binding == "runtime_worker":
        steward.worker_id = str(UUID("00000000-0000-4000-a000-000000000099"))
    elif broken_binding == "candidate_owner":
        candidate.worker_id = str(UUID("00000000-0000-4000-a000-000000000099"))
    elif broken_binding == "runtime_status":
        from mas_core.worker_registry.steward import RolloutStatus

        rollout.status = RolloutStatus.SHADOW
    else:
        persisted["sample_targets"] = {**persisted["sample_targets"], "shadow": 100}

    storage = MagicMock()
    storage.get_rollout_record = AsyncMock(return_value=persisted)
    storage.transition_rollout = AsyncMock(return_value={"status": "SHADOW"})
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    advance = MagicMock(wraps=steward.advance_rollout)
    monkeypatch.setattr(steward, "advance_rollout", advance)
    runtime_cache = {str(WORKER_ID): steward}
    monkeypatch.setattr(main, "_worker_steward_runtimes", runtime_cache)

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts/{rollout.rollout_id}/advance",
        headers={"X-API-Key": "test-operator-key"},
        json={"target_status": "SHADOW", "sample_count": 0},
    )

    assert response.status_code in {404, 409}, response.text
    advance.assert_not_called()
    storage.transition_rollout.assert_not_awaited()
    if broken_binding != "other_worker":
        assert str(WORKER_ID) not in runtime_cache
    else:
        assert rollout.status.value == "PENDING"

@pytest.mark.anyio
async def test_rollout_advance_discards_runtime_when_persistence_raises(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    steward, candidate, _certification = _approved_rollout_fixture()
    rollout = _pending_rollout_fixture(steward, candidate)
    persisted = _persisted_rollout_fixture(rollout)
    storage = MagicMock()
    storage.get_rollout_record = AsyncMock(return_value=persisted)
    storage.transition_rollout = AsyncMock(side_effect=RuntimeError("offline failure"))
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    runtime_cache = {str(WORKER_ID): steward}
    monkeypatch.setattr(main, "_worker_steward_runtimes", runtime_cache)

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts/{rollout.rollout_id}/advance",
        headers={"X-API-Key": "test-operator-key"},
        json={"target_status": "SHADOW", "sample_count": 0},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ROLLOUT_TRANSITION_PERSISTENCE_FAILED"
    assert rollout.status.value == "PENDING"
    assert str(WORKER_ID) not in runtime_cache

@pytest.mark.anyio
@pytest.mark.parametrize("target_status", ["ROLLING_BACK", "ROLLED_BACK"])
async def test_rollout_advance_requires_atomic_rollback_route(
    client, monkeypatch, target_status
) -> None:
    from orchestrator_api import main

    steward, candidate, _certification = _approved_rollout_fixture()
    rollout = _pending_rollout_fixture(steward, candidate)
    persisted = _persisted_rollout_fixture(rollout)
    storage = MagicMock()
    storage.get_rollout_record = AsyncMock(return_value=persisted)
    storage.transition_rollout = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    advance = MagicMock(wraps=steward.advance_rollout)
    monkeypatch.setattr(steward, "advance_rollout", advance)

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts/{rollout.rollout_id}/advance",
        headers={"X-API-Key": "test-operator-key"},
        json={"target_status": target_status},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ROLLOUT_ROLLBACK_ENDPOINT_REQUIRED"
    advance.assert_not_called()
    storage.transition_rollout.assert_not_awaited()
    assert rollout.status.value == "PENDING"

@pytest.mark.anyio
async def test_rollout_advance_rejects_sample_count_regression_before_mutation(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    steward, candidate, _certification = _approved_rollout_fixture()
    rollout = _pending_rollout_fixture(steward, candidate)
    rollout.sample_count = 4
    persisted = _persisted_rollout_fixture(rollout)
    storage = MagicMock()
    storage.get_rollout_record = AsyncMock(return_value=persisted)
    storage.transition_rollout = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    advance = MagicMock(wraps=steward.advance_rollout)
    monkeypatch.setattr(steward, "advance_rollout", advance)

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts/{rollout.rollout_id}/advance",
        headers={"X-API-Key": "test-operator-key"},
        json={"target_status": "SHADOW", "sample_count": 3},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ROLLOUT_SAMPLE_COUNT_REGRESSION"
    advance.assert_not_called()
    storage.transition_rollout.assert_not_awaited()
    assert rollout.sample_count == 4
    assert rollout.status.value == "PENDING"

@pytest.mark.anyio
async def test_rollout_rollback_commits_then_discards_process_local_runtime(
    client, monkeypatch
) -> None:
    from datetime import UTC, datetime

    from orchestrator_api import main

    steward, candidate, _certification = _approved_rollout_fixture()
    rollout = _pending_rollout_fixture(steward, candidate)
    persisted = _rollback_route_fixture(candidate, rollout)
    completed_at = datetime.now(tz=UTC)
    storage = MagicMock()
    storage.get_rollout_record = AsyncMock(return_value=persisted)

    async def persist_rollback(**kwargs):
        assert rollout.status.value == "PENDING"
        assert kwargs["worker_id"] == WORKER_ID
        assert kwargs["steward_id"] == steward.steward_id
        assert kwargs["candidate_id"] == candidate.candidate_id
        assert kwargs["expected_status"] == "PENDING"
        return {
            "rollout": {
                **persisted,
                "status": "ROLLED_BACK",
                "rollback_reason": kwargs["reason"],
                "completed_at": completed_at,
            },
            "target_candidate_id": None,
            "target_bundle_id": None,
            "target_adapter_id": None,
            "target_shell_id": None,
        }

    storage.rollback_rollout_atomically = AsyncMock(side_effect=persist_rollback)
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    runtime_cache = {str(WORKER_ID): steward}
    monkeypatch.setattr(main, "_worker_steward_runtimes", runtime_cache)
    rollback = MagicMock(wraps=steward.rollback)
    restore_pointers = MagicMock(wraps=steward.restore_active_pointers)
    monkeypatch.setattr(steward, "rollback", rollback)
    monkeypatch.setattr(steward, "restore_active_pointers", restore_pointers)

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts/{rollout.rollout_id}/rollback",
        headers={"X-API-Key": "test-operator-key"},
        json={"reason": "operator rollback"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ROLLED_BACK"
    assert datetime.fromisoformat(
        response.json()["completed_at"].replace("Z", "+00:00")
    ) == completed_at
    assert rollout.status.value == "PENDING"
    storage.rollback_rollout_atomically.assert_awaited_once()
    rollback.assert_not_called()
    restore_pointers.assert_not_called()
    assert str(WORKER_ID) not in runtime_cache

@pytest.mark.anyio
@pytest.mark.parametrize("broken_binding", ["other_worker", "other_steward", "other_candidate"])
async def test_rollout_rollback_rejects_wrong_persisted_binding_before_mutation(
    client, monkeypatch, broken_binding
) -> None:
    from orchestrator_api import main

    steward, candidate, _certification = _approved_rollout_fixture()
    rollout = _pending_rollout_fixture(steward, candidate)
    persisted = _rollback_route_fixture(candidate, rollout)
    if broken_binding == "other_worker":
        persisted["worker_id"] = UUID("00000000-0000-4000-a000-000000000099")
    elif broken_binding == "other_steward":
        persisted["steward_id"] = UUID("00000000-0000-4000-a000-000000000099")
    else:
        persisted["candidate_id"] = UUID("00000000-0000-4000-a000-000000000099")

    storage = MagicMock()
    storage.get_rollout_record = AsyncMock(return_value=persisted)
    storage.rollback_rollout_atomically = AsyncMock()
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    runtime_cache = {str(WORKER_ID): steward}
    monkeypatch.setattr(main, "_worker_steward_runtimes", runtime_cache)
    rollback = MagicMock(wraps=steward.rollback)
    monkeypatch.setattr(steward, "rollback", rollback)

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts/{rollout.rollout_id}/rollback",
        headers={"X-API-Key": "test-operator-key"},
        json={"reason": "operator rollback"},
    )

    assert response.status_code in {404, 409}, response.text
    assert rollout.status.value == "PENDING"
    rollback.assert_not_called()
    storage.rollback_rollout_atomically.assert_not_awaited()
    if broken_binding != "other_worker":
        assert str(WORKER_ID) not in runtime_cache

@pytest.mark.anyio
async def test_rollout_rollback_discards_runtime_when_atomic_persistence_fails(
    client, monkeypatch
) -> None:
    from orchestrator_api import main

    steward, candidate, _certification = _approved_rollout_fixture()
    rollout = _pending_rollout_fixture(steward, candidate)
    persisted = _rollback_route_fixture(candidate, rollout)
    storage = MagicMock()
    storage.get_rollout_record = AsyncMock(return_value=persisted)
    storage.rollback_rollout_atomically = AsyncMock(
        side_effect=RuntimeError("offline persistence failure")
    )
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))
    runtime_cache = {str(WORKER_ID): steward}
    monkeypatch.setattr(main, "_worker_steward_runtimes", runtime_cache)
    rollback = MagicMock(wraps=steward.rollback)
    monkeypatch.setattr(steward, "rollback", rollback)

    response = await client.post(
        f"/capabilities/workers/{WORKER_ID}/steward/rollouts/{rollout.rollout_id}/rollback",
        headers={"X-API-Key": "test-operator-key"},
        json={"reason": "operator rollback"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ROLLOUT_ROLLBACK_PERSISTENCE_FAILED"
    assert rollout.status.value == "PENDING"
    rollback.assert_not_called()
    assert str(WORKER_ID) not in runtime_cache

def _rollback_route_fixture(candidate, rollout):
    return {
        "id": rollout.rollout_id,
        "worker_id": UUID(str(rollout.worker_id)),
        "steward_id": rollout.steward_id,
        "candidate_id": candidate.candidate_id,
        "status": rollout.status.value,
    }
