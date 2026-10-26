"""Atomic rollout promotion must recheck its reviewed worker identity."""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.memory.storage import AgentStorage, worker_identity_snapshot_for_rollout
from mas_core.worker_registry.steward import (
    AdapterCandidate,
    BundleStatus,
    CandidateIntakeStatus,
    CandidateRecord,
    ExternalProvenance,
    SkillBundle,
)


class _Rows:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def first(self):
        return self.value


def _identity_rows():
    worker_id = uuid4()
    steward_id = uuid4()
    rollout_id = uuid4()
    candidate_id = uuid4()
    shell_id = uuid4()
    adapter_id = uuid4()
    bundle_id = uuid4()
    certification_id = uuid4()
    approval_id = uuid4()
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
    provenance_model = ExternalProvenance(
        canonical_source_repository=source_repo,
        source_provider="github",
        exact_release="0.1.0",
        commit_sha=source_revision,
        oci_image_digest=image_digest,
        protocol_api_version="aiat.worker.v1",
        adapter_version="0.1.0",
        transport_type="oci",
        runtime_fingerprint="sha256:" + "3" * 64,
        security_scan_status="passed",
    )
    provenance = {
        "id": uuid4(),
        "worker_id": worker_id,
        **provenance_model.model_dump(mode="json"),
        "provenance_hash": "a" * 64,
    }
    bundle_model = SkillBundle(
        bundle_id=bundle_id,
        semantic_version="0.1.0",
        upstream_compatibility_range="==0.1.0",
        steward_id=steward_id,
        source_provenance=provenance_model,
        status=BundleStatus.APPROVED,
    )
    adapter_model = AdapterCandidate(
        adapter_id=adapter_id,
        version="0.1.0",
        steward_id=steward_id,
        source_provenance=provenance_model,
        transport_type="oci",
        content_hash="c" * 64,
        status=BundleStatus.APPROVED,
        conformance_report={"passed": True},
    )
    candidate_model = CandidateRecord(
        candidate_id=candidate_id,
        worker_id=str(worker_id),
        steward_id=steward_id,
        intake_status=CandidateIntakeStatus.APPROVED,
        bundle=bundle_model,
        adapter=adapter_model,
        source_provenance=provenance_model,
        certification_id=certification_id,
        approval_record_id=approval_id,
    )
    rollout = {
        "id": rollout_id,
        "worker_id": worker_id,
        "steward_id": steward_id,
        "candidate_id": candidate_id,
        "status": "PROMOTING",
        "sample_count": 3,
        "sample_targets": {"shadow": 10, "readonly_canary": 5, "live_canary": 3},
        "comparison_metrics": {"regression_fraction": 0.0, "irreversible_side_effects": 0.0},
        "rollback_thresholds": {"regression_fraction": 0.10},
        "eligible_task_classes": ["read_only"],
        "in_flight_policy": "finish_pinned_version",
        "promotion_actor": "operator",
    }
    candidate = {
        "id": candidate_id,
        "worker_id": worker_id,
        "skill_bundle_id": bundle_id,
        "adapter_id": adapter_id,
        "intake_status": "APPROVED",
        "certification_run_id": certification_id,
        "approval_record_id": approval_id,
        "evidence_json": {
            "worker_shell_version_id": str(shell_id),
            "candidate_record": candidate_model.model_dump(mode="json"),
        },
    }
    certification = {
        "id": certification_id,
        "worker_id": worker_id,
        "candidate_id": candidate_id,
        "steward_id": steward_id,
        "status": "passed",
    }
    approval = {
        "id": approval_id,
        "scope_type": "worker_skill_bundle_candidate",
        "scope_id": candidate_id,
        "decision": "APPROVED",
    }
    bundle = {
        "id": bundle_id,
        "worker_id": worker_id,
        "steward_id": steward_id,
        "content_hash": bundle_model.content_hash,
    }
    adapter = {
        "id": adapter_id,
        "worker_id": worker_id,
        "content_hash": adapter_model.content_hash,
        "conformance_status": "passed",
    }
    shell = {
        "id": shell_id,
        "worker_id": worker_id,
        "status": "active",
    }
    steward = {"id": steward_id, "worker_id": worker_id, "status": "READY"}
    return (
        worker_id,
        rollout_id,
        steward_id,
        candidate_id,
        worker,
        provenance,
        rollout,
        candidate,
        shell,
        steward,
        certification,
        approval,
        bundle,
        adapter,
    )


def _mock_storage(rows, *, competing_rollout=None):
    (
        worker_id,
        rollout_id,
        steward_id,
        candidate_id,
        worker,
        provenance,
        rollout,
        candidate,
        shell,
        steward,
        certification,
        approval,
        bundle,
        adapter,
    ) = rows
    conn = MagicMock()
    conn.execute = AsyncMock(
        side_effect=[
            _Rows(worker),
            _Rows(provenance),
            _Rows(rollout),
            _Rows(competing_rollout),  # competing in-progress rollout, if supplied
            _Rows(candidate),
            _Rows(certification),
            _Rows(approval),
            _Rows(bundle),
            _Rows(adapter),
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


def _activation_kwargs(rows):
    worker_id, rollout_id, steward_id, candidate_id, worker, provenance, rollout, candidate = rows[:8]
    return {
        "rollout_id": rollout_id,
        "worker_id": worker_id,
        "steward_id": steward_id,
        "candidate_id": candidate_id,
        "completed_at": None,
        "expected_worker_identity": worker_identity_snapshot_for_rollout(worker),
        "expected_provenance_hash": str(provenance["provenance_hash"]),
        "expected_rollout_snapshot": copy.deepcopy(rollout),
        "expected_candidate_snapshot": copy.deepcopy(
            candidate["evidence_json"]["candidate_record"]
        ),
        "sample_count": rollout["sample_count"],
        "comparison_metrics": rollout["comparison_metrics"],
        "actor": "operator",
    }


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
    kwargs = _activation_kwargs(rows)
    worker["adapter_config"] = {
        **worker["adapter_config"],
        "image": "example.invalid/aiat-worker@sha256:" + "4" * 64,
    }
    storage, conn = _mock_storage(rows)

    result = await storage.activate_rollout_atomically(
        **{**kwargs, "expected_worker_identity": expected}
    )

    assert result is None
    assert conn.execute.await_count == 1


@pytest.mark.anyio
async def test_rollout_activation_rejects_changed_provenance_hash_before_writes() -> None:
    rows = _identity_rows()
    kwargs = _activation_kwargs(rows)
    provenance = rows[5]
    storage, conn = _mock_storage(rows)
    provenance["provenance_hash"] = "b" * 64

    result = await storage.activate_rollout_atomically(**kwargs)

    assert result is None
    assert conn.execute.await_count == 2


@pytest.mark.anyio
async def test_rollout_activation_rejects_candidate_provenance_identity_drift() -> None:
    rows = _identity_rows()
    kwargs = _activation_kwargs(rows)
    storage, conn = _mock_storage(rows)
    candidate = rows[7]
    candidate_record = candidate["evidence_json"]["candidate_record"]
    candidate_record["source_provenance"]["commit_sha"] = "5" * 40

    result = await storage.activate_rollout_atomically(**kwargs)

    assert result is None
    assert conn.execute.await_count == 5


@pytest.mark.anyio
async def test_rollout_activation_updates_pointers_when_snapshots_still_match() -> None:
    rows = _identity_rows()
    kwargs = _activation_kwargs(rows)
    storage, conn = _mock_storage(rows)

    result = await storage.activate_rollout_atomically(**kwargs)

    assert result == {"status": "ACTIVE"}
    assert conn.execute.await_count == 17
    storage.get_rollout_record.assert_awaited_once_with(rows[1])
    promotion_update = conn.execute.await_args_list[-2].args[0]
    persisted_values = {
        getattr(column, "name", column): getattr(value, "value", value)
        for column, value in promotion_update._values.items()
    }
    assert persisted_values["sample_count"] == 3
    assert persisted_values["comparison_metrics"] == {
        "regression_fraction": 0.0,
        "irreversible_side_effects": 0.0,
    }


@pytest.mark.anyio
async def test_rollout_activation_rejects_rollback_in_progress_before_writes() -> None:
    rows = _identity_rows()
    kwargs = _activation_kwargs(rows)
    competing_rollout = {
        "id": uuid4(),
        "worker_id": rows[0],
        "steward_id": rows[2],
        "candidate_id": uuid4(),
        "status": "ROLLING_BACK",
    }
    storage, conn = _mock_storage(rows, competing_rollout=competing_rollout)

    result = await storage.activate_rollout_atomically(**kwargs)

    assert result is None
    assert conn.execute.await_count == 4
    statement = conn.execute.await_args_list[3].args[0]
    values = statement.compile().params.values()
    assert any(
        isinstance(value, (list, tuple, set, frozenset))
        and "ROLLING_BACK" in value
        for value in values
    )
    assert all(
        statement.is_select
        for statement in [call.args[0] for call in conn.execute.await_args_list]
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sample_count", "metrics"),
    [
        (2, {"regression_fraction": 0.0, "irreversible_side_effects": 0.0}),
        (3, {"regression_fraction": 0.11, "irreversible_side_effects": 0.0}),
        (3, {"regression_fraction": 0.0, "irreversible_side_effects": 1.0}),
        (3, {"regression_fraction": 0.0, "irreversible_side_effects": -1.0}),
        (3, {"regression_fraction": float("nan"), "irreversible_side_effects": 0.0}),
        (3, {"regression_fraction": 0.0, "irreversible_side_effects": float("inf")}),
    ],
)
async def test_rollout_activation_rejects_unacceptable_canary_evidence_before_writes(
    sample_count, metrics
) -> None:
    rows = _identity_rows()
    kwargs = _activation_kwargs(rows)
    kwargs["sample_count"] = sample_count
    kwargs["comparison_metrics"] = metrics
    storage, conn = _mock_storage(rows)

    result = await storage.activate_rollout_atomically(**kwargs)

    assert result is None
    assert conn.execute.await_count == 3
    assert all(
        not getattr(call.args[0], "is_update", False)
        and not getattr(call.args[0], "is_insert", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_rollout_activation_rejects_changed_locked_canary_snapshot() -> None:
    rows = _identity_rows()
    kwargs = _activation_kwargs(rows)
    rows[6]["sample_count"] = 4
    storage, conn = _mock_storage(rows)

    result = await storage.activate_rollout_atomically(**kwargs)

    assert result is None
    assert conn.execute.await_count == 3


@pytest.mark.anyio
async def test_rollout_activation_rechecks_durable_approval_before_writes() -> None:
    rows = _identity_rows()
    kwargs = _activation_kwargs(rows)
    rows[11]["decision"] = "REVOKED"
    storage, conn = _mock_storage(rows)

    result = await storage.activate_rollout_atomically(**kwargs)

    assert result is None
    assert conn.execute.await_count == 7
    assert all(
        not getattr(call.args[0], "is_update", False)
        and not getattr(call.args[0], "is_insert", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_rollout_activation_rejects_drifted_serialized_bundle_hash() -> None:
    rows = _identity_rows()
    kwargs = _activation_kwargs(rows)
    rows[12]["content_hash"] = "d" * 64
    storage, conn = _mock_storage(rows)

    result = await storage.activate_rollout_atomically(**kwargs)

    assert result is None
    assert conn.execute.await_count == 9


@pytest.mark.anyio
async def test_rollout_activation_rejects_nonactive_shell_before_writes() -> None:
    rows = _identity_rows()
    kwargs = _activation_kwargs(rows)
    rows[8]["status"] = "inactive"
    storage, conn = _mock_storage(rows)

    result = await storage.activate_rollout_atomically(**kwargs)

    assert result is None
    assert conn.execute.await_count == 10
    assert all(
        not getattr(call.args[0], "is_update", False)
        and not getattr(call.args[0], "is_insert", False)
        for call in conn.execute.await_args_list
    )
