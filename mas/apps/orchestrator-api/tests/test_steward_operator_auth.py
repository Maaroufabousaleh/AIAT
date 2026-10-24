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

    storage = MagicMock()
    storage.list_rollout_records = AsyncMock(return_value=[])
    storage.create_rollout_record = AsyncMock(return_value={})
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
