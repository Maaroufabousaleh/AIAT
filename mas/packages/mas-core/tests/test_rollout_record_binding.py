from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.memory import models as t
from mas_core.memory.storage import AgentStorage
from mas_core.worker_registry.steward import (
    AdapterCandidate,
    BundleStatus,
    CandidateIntakeStatus,
    CandidateRecord,
    ExternalProvenance,
    SkillBundle,
)


class _Result:
    def __init__(self, row=None, *, rows=None, rowcount=0):
        self.row = row
        self.rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self.row

    def all(self):
        return self.rows


def _state(*, worker_id=None, steward_id=None):
    worker_id = worker_id or uuid4()
    steward_id = steward_id or uuid4()
    candidate_id = uuid4()
    bundle_id = uuid4()
    adapter_id = uuid4()
    certification_id = uuid4()
    approval_id = uuid4()
    provenance = ExternalProvenance(
        canonical_source_repository="https://example.invalid/test-worker",
        source_provider="github",
        exact_release="1.0.0",
        transport_type="process",
        security_scan_status="passed",
    )
    bundle_model = SkillBundle(
        bundle_id=bundle_id,
        semantic_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
        steward_id=steward_id,
        source_provenance=provenance,
        status=BundleStatus.APPROVED,
    )
    adapter_model = AdapterCandidate(
        adapter_id=adapter_id,
        version="1.0.0",
        steward_id=steward_id,
        source_provenance=provenance,
        transport_type="process",
        content_hash="a" * 64,
        status=BundleStatus.APPROVED,
    )
    candidate_model = CandidateRecord(
        candidate_id=candidate_id,
        worker_id=str(worker_id),
        steward_id=steward_id,
        intake_status=CandidateIntakeStatus.APPROVED,
        bundle=bundle_model,
        adapter=adapter_model,
        source_provenance=provenance,
        certification_id=certification_id,
        approval_record_id=approval_id,
    )
    candidate_record = candidate_model.model_dump(mode="json")
    return {
        "worker_id": worker_id,
        "steward_id": steward_id,
        "candidate_id": candidate_id,
        "bundle_id": bundle_id,
        "adapter_id": adapter_id,
        "certification_id": certification_id,
        "approval_id": approval_id,
        "worker": {"id": worker_id},
        "steward": {"id": steward_id, "worker_id": worker_id},
        "candidate": {
            "id": candidate_id,
            "worker_id": worker_id,
            "skill_bundle_id": bundle_id,
            "adapter_id": adapter_id,
            "intake_status": "APPROVED",
            "certification_run_id": certification_id,
            "approval_record_id": approval_id,
            "evidence_json": {"candidate_record": candidate_record},
        },
        "certification": {
            "id": certification_id,
            "worker_id": worker_id,
            "candidate_id": candidate_id,
            "steward_id": steward_id,
            "status": "passed",
        },
        "approval": {
            "id": approval_id,
            "scope_type": "worker_skill_bundle_candidate",
            "scope_id": candidate_id,
            "decision": "APPROVED",
        },
        "bundle": {
            "id": bundle_id,
            "worker_id": worker_id,
            "steward_id": steward_id,
            "content_hash": bundle_model.content_hash,
        },
        "adapter": {
            "id": adapter_id,
            "worker_id": worker_id,
            "content_hash": "a" * 64,
            "conformance_status": "passed",
            "status": "approved",
        },
    }


def _storage(results):
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=results)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=conn)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction
    storage = AgentStorage("postgresql+asyncpg://localhost/unused")
    storage._engine = engine
    return storage, conn, transaction


def _rollout_values(state):
    return {
        "rollout_id": uuid4(),
        "worker_id": state["worker_id"],
        "steward_id": state["steward_id"],
        "candidate_id": state["candidate_id"],
        "expected_candidate_snapshot": state["candidate"]["evidence_json"]["candidate_record"],
        "status": "PENDING",
        "eligible_task_classes": ["read_only"],
        "sample_targets": {"shadow": 10, "readonly_canary": 5, "live_canary": 3},
        "rollback_thresholds": {"regression_fraction": 0.1},
        "promotion_actor": "operator",
    }


def _valid_results(state, *, prior_rollouts=None):
    return [
        _Result(state["worker"]),
        _Result(state["steward"]),
        _Result(state["candidate"]),
        _Result(state["certification"]),
        _Result(state["approval"]),
        _Result(state["bundle"]),
        _Result(state["adapter"]),
        _Result(rows=prior_rollouts or []),
        _Result(),  # rollout INSERT
    ]


@pytest.mark.anyio
async def test_rollout_insert_locks_and_binds_approved_candidate_evidence():
    state = _state()
    storage, conn, transaction = _storage(_valid_results(state))

    result = await storage.create_rollout_record(**_rollout_values(state))

    assert result["candidate_id"] == state["candidate_id"]
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert statements[-1].is_insert
    assert statements[-1].table is t.rollout_records
    assert all(statement.is_select for statement in statements[:-1])
    transaction.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.anyio
async def test_rollout_insert_rejects_mismatched_certification_before_writing():
    state = _state()
    state["certification"]["candidate_id"] = uuid4()
    storage, conn, _transaction = _storage(_valid_results(state))

    result = await storage.create_rollout_record(**_rollout_values(state))

    assert result is None
    assert conn.execute.await_count == 4
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_rollout_insert_rejects_candidate_payload_drift_before_writing():
    import copy

    state = _state()
    expected_candidate = copy.deepcopy(
        state["candidate"]["evidence_json"]["candidate_record"]
    )
    state["candidate"]["evidence_json"]["candidate_record"]["bundle"][
        "semantic_version"
    ] = "1.0.1"
    values = _rollout_values(state)
    values["expected_candidate_snapshot"] = expected_candidate
    storage, conn, _transaction = _storage(_valid_results(state))

    result = await storage.create_rollout_record(**values)

    assert result is None
    assert conn.execute.await_count == 3
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_rollout_insert_rejects_stored_bundle_hash_drift_before_writing():
    state = _state()
    state["bundle"]["content_hash"] = "f" * 64
    storage, conn, _transaction = _storage(_valid_results(state))

    result = await storage.create_rollout_record(**_rollout_values(state))

    assert result is None
    assert conn.execute.await_count == 7
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "prior_rollouts",
    [
        lambda state: [{"worker_id": state["worker_id"], "candidate_id": state["candidate_id"], "status": "ROLLED_BACK"}],
        lambda state: [{"worker_id": state["worker_id"], "candidate_id": uuid4(), "status": "PENDING"}],
        lambda state: [{"worker_id": state["worker_id"], "candidate_id": uuid4(), "status": "ROLLING_BACK"}],
    ],
)
async def test_rollout_insert_deduplicates_candidate_and_active_worker_rollouts(
    prior_rollouts,
):
    state = _state()
    storage, conn, _transaction = _storage(
        _valid_results(state, prior_rollouts=prior_rollouts(state))
    )

    result = await storage.create_rollout_record(**_rollout_values(state))

    assert result is None
    assert conn.execute.await_count == 8
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_rollout_transition_rejects_a_row_outside_expected_worker_scope():
    rollout_id = uuid4()
    expected_worker_id = uuid4()
    current = {
        "id": rollout_id,
        "worker_id": uuid4(),
        "steward_id": uuid4(),
        "candidate_id": uuid4(),
        "status": "PENDING",
    }
    storage, conn, _transaction = _storage([_Result(current)])
    storage.get_rollout_record = AsyncMock()
    expected_snapshot = dict(current)

    result = await storage.transition_rollout(
        rollout_id,
        worker_id=expected_worker_id,
        steward_id=current["steward_id"],
        candidate_id=current["candidate_id"],
        to_status="SHADOW",
        actor="operator",
        expected_status="PENDING",
        expected_rollout_snapshot=expected_snapshot,
    )

    assert result is None
    assert conn.execute.await_count == 1
    statement = conn.execute.await_args.args[0]
    assert statement.is_select
    storage.get_rollout_record.assert_not_awaited()


@pytest.mark.anyio
async def test_rollout_transition_cas_and_audit_insert_share_expected_binding():
    rollout_id = uuid4()
    current = {
        "id": rollout_id,
        "worker_id": uuid4(),
        "steward_id": uuid4(),
        "candidate_id": uuid4(),
        "status": "PENDING",
        "sample_count": 0,
    }
    updated = {**current, "status": "SHADOW"}
    storage, conn, transaction = _storage(
        [_Result(current), _Result(rowcount=1), _Result()]
    )
    storage.get_rollout_record = AsyncMock(return_value=updated)

    result = await storage.transition_rollout(
        rollout_id,
        worker_id=current["worker_id"],
        steward_id=current["steward_id"],
        candidate_id=current["candidate_id"],
        to_status="SHADOW",
        actor="operator",
        expected_status="PENDING",
        expected_rollout_snapshot=current,
        evidence={"target_status": "SHADOW"},
    )

    assert result == updated
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert statements[0].is_select
    assert statements[1].is_update
    assert statements[1].table is t.rollout_records
    assert statements[2].is_insert
    assert statements[2].table is t.rollout_transitions
    storage.get_rollout_record.assert_awaited_once_with(rollout_id)
    transaction.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.anyio
async def test_rollout_transition_rejects_snapshot_drift_before_writes():
    current = {
        "id": uuid4(),
        "worker_id": uuid4(),
        "steward_id": uuid4(),
        "candidate_id": uuid4(),
        "status": "PENDING",
        "sample_count": 1,
        "eligible_task_classes": ["read_only"],
    }
    expected = {**current, "eligible_task_classes": ["low_risk"]}
    storage, conn, _transaction = _storage([_Result(current)])
    storage.get_rollout_record = AsyncMock()

    result = await storage.transition_rollout(
        current["id"],
        worker_id=current["worker_id"],
        steward_id=current["steward_id"],
        candidate_id=current["candidate_id"],
        to_status="SHADOW",
        actor="operator",
        expected_status="PENDING",
        expected_rollout_snapshot=expected,
    )

    assert result is None
    assert conn.execute.await_count == 1
    storage.get_rollout_record.assert_not_awaited()


@pytest.mark.anyio
async def test_rollout_transition_rejects_decreasing_sample_count():
    current = {
        "id": uuid4(),
        "worker_id": uuid4(),
        "steward_id": uuid4(),
        "candidate_id": uuid4(),
        "status": "PENDING",
        "sample_count": 2,
    }
    storage, conn, _transaction = _storage([_Result(current)])
    storage.get_rollout_record = AsyncMock()

    result = await storage.transition_rollout(
        current["id"],
        worker_id=current["worker_id"],
        steward_id=current["steward_id"],
        candidate_id=current["candidate_id"],
        to_status="SHADOW",
        actor="operator",
        expected_status="PENDING",
        expected_rollout_snapshot=current,
        sample_count=1,
    )

    assert result is None
    assert conn.execute.await_count == 1
    storage.get_rollout_record.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("eligible_task_classes", "sample_count"),
    [(["read_only"], 9), (["write"], 10)],
)
async def test_rollout_transition_rejects_early_canary_without_scope_and_samples(
    eligible_task_classes, sample_count
):
    current = {
        "id": uuid4(),
        "worker_id": uuid4(),
        "steward_id": uuid4(),
        "candidate_id": uuid4(),
        "status": "SHADOW",
        "sample_count": 0,
        "eligible_task_classes": eligible_task_classes,
        "sample_targets": {"shadow": 10, "readonly_canary": 5, "live_canary": 3},
    }
    storage, conn, _transaction = _storage([_Result(current)])
    storage.get_rollout_record = AsyncMock()

    result = await storage.transition_rollout(
        current["id"],
        worker_id=current["worker_id"],
        steward_id=current["steward_id"],
        candidate_id=current["candidate_id"],
        to_status="CANARY",
        actor="operator",
        expected_status="SHADOW",
        expected_rollout_snapshot=current,
        sample_count=sample_count,
    )

    assert result is None
    assert conn.execute.await_count == 1
    storage.get_rollout_record.assert_not_awaited()


def _rollback_rows(state):
    return {
        "worker": {"id": state["worker_id"], "status": "ACTIVE"},
        "steward": {
            "id": state["steward_id"],
            "worker_id": state["worker_id"],
            "status": "READY",
        },
        "rollout": {
            "id": state["rollout_id"],
            "worker_id": state["worker_id"],
            "steward_id": state["steward_id"],
            "candidate_id": state["candidate_id"],
            "status": "PENDING",
        },
        "candidate": state["candidate"],
        "bundle": state["bundle"],
        "adapter": state["adapter"],
    }


@pytest.mark.anyio
async def test_rollout_rollback_rejects_cross_worker_steward_before_writes():
    from datetime import UTC, datetime

    state = _state()
    state["rollout_id"] = uuid4()
    rows = _rollback_rows(state)
    rows["steward"]["worker_id"] = uuid4()
    storage, conn, _transaction = _storage(
        [
            _Result(rows["worker"]),
            _Result(rows["steward"]),
        ]
    )

    result = await storage.rollback_rollout_atomically(
        rollout_id=state["rollout_id"],
        worker_id=state["worker_id"],
        steward_id=state["steward_id"],
        candidate_id=state["candidate_id"],
        expected_status="PENDING",
        reason="operator rollback",
        triggered_by="operator",
        completed_at=datetime.now(tz=UTC),
    )

    assert result is None
    assert conn.execute.await_count == 2
    assert all(
        not getattr(call.args[0], "is_update", False)
        and not getattr(call.args[0], "is_insert", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_rollout_rollback_rejects_historical_active_row_when_worker_points_elsewhere():
    from datetime import UTC, datetime

    state = _state()
    state["rollout_id"] = uuid4()
    current_shell_id = uuid4()
    state["candidate"]["evidence_json"]["worker_shell_version_id"] = str(
        current_shell_id
    )
    rows = _rollback_rows(state)
    rows["rollout"]["status"] = "ACTIVE"
    rows["worker"].update(
        active_skill_bundle_id=uuid4(),
        active_adapter_id=uuid4(),
        active_shell_version_id=uuid4(),
    )
    storage, conn, _transaction = _storage(
        [
            _Result(rows["worker"]),
            _Result(rows["steward"]),
            _Result(rows["rollout"]),
            _Result(rows["candidate"]),
        ]
    )

    result = await storage.rollback_rollout_atomically(
        rollout_id=state["rollout_id"],
        worker_id=state["worker_id"],
        steward_id=state["steward_id"],
        candidate_id=state["candidate_id"],
        expected_status="ACTIVE",
        reason="operator rollback",
        triggered_by="operator",
        completed_at=datetime.now(tz=UTC),
    )

    assert result is None
    assert conn.execute.await_count == 4
    assert all(
        not getattr(call.args[0], "is_update", False)
        and not getattr(call.args[0], "is_insert", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_rollout_rollback_accepts_current_active_pointer_binding():
    from datetime import UTC, datetime

    state = _state()
    state["rollout_id"] = uuid4()
    current_shell_id = uuid4()
    state["candidate"]["evidence_json"]["worker_shell_version_id"] = str(
        current_shell_id
    )
    rows = _rollback_rows(state)
    rows["rollout"]["status"] = "ACTIVE"
    rows["worker"].update(
        active_skill_bundle_id=state["bundle_id"],
        active_adapter_id=state["adapter_id"],
        active_shell_version_id=current_shell_id,
    )
    storage, conn, _transaction = _storage(
        [
            _Result(rows["worker"]),
            _Result(rows["steward"]),
            _Result(rows["rollout"]),
            _Result(rows["candidate"]),
            _Result(state["certification"]),
            _Result(state["approval"]),
            _Result(rows["bundle"]),
            _Result(rows["adapter"]),
            _Result(rows=[]),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(),
            _Result(),
            _Result(),
        ]
    )

    result = await storage.rollback_rollout_atomically(
        rollout_id=state["rollout_id"],
        worker_id=state["worker_id"],
        steward_id=state["steward_id"],
        candidate_id=state["candidate_id"],
        expected_status="ACTIVE",
        reason="operator rollback",
        triggered_by="operator",
        completed_at=datetime.now(tz=UTC),
    )

    assert result["rollout"]["status"] == "ROLLED_BACK"
    assert result["target_candidate_id"] is None
    assert conn.execute.await_count == 17


@pytest.mark.anyio
async def test_rollout_rollback_rejects_current_bundle_snapshot_hash_drift():
    from datetime import UTC, datetime

    state = _state()
    state["rollout_id"] = uuid4()
    rows = _rollback_rows(state)
    rows["bundle"]["content_hash"] = "e" * 64
    storage, conn, _transaction = _storage(
        [
            _Result(rows["worker"]),
            _Result(rows["steward"]),
            _Result(rows["rollout"]),
            _Result(rows["candidate"]),
            _Result(state["certification"]),
            _Result(state["approval"]),
            _Result(rows["bundle"]),
            _Result(rows["adapter"]),
        ]
    )

    result = await storage.rollback_rollout_atomically(
        rollout_id=state["rollout_id"],
        worker_id=state["worker_id"],
        steward_id=state["steward_id"],
        candidate_id=state["candidate_id"],
        expected_status="PENDING",
        reason="operator rollback",
        triggered_by="operator",
        completed_at=datetime.now(tz=UTC),
    )

    assert result is None
    assert conn.execute.await_count == 8
    assert all(
        not getattr(call.args[0], "is_update", False)
        and not getattr(call.args[0], "is_insert", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_rollout_rollback_rejects_previous_bundle_hash_drift():
    from datetime import UTC, datetime

    state = _state()
    state["rollout_id"] = uuid4()
    rows = _rollback_rows(state)
    target = _state(worker_id=state["worker_id"], steward_id=state["steward_id"])
    shell_id = uuid4()
    target["candidate"]["evidence_json"]["worker_shell_version_id"] = str(shell_id)
    target_rollout = {
        "id": uuid4(),
        "worker_id": state["worker_id"],
        "steward_id": state["steward_id"],
        "candidate_id": target["candidate_id"],
        "status": "ACTIVE",
        "started_at": datetime.now(tz=UTC),
    }
    target["bundle"]["content_hash"] = "b" * 64
    storage, conn, _transaction = _storage(
        [
            _Result(rows["worker"]),
            _Result(rows["steward"]),
            _Result(rows["rollout"]),
            _Result(rows["candidate"]),
            _Result(state["certification"]),
            _Result(state["approval"]),
            _Result(rows["bundle"]),
            _Result(rows["adapter"]),
            _Result(rows=[target_rollout]),
            _Result(target["candidate"]),
            _Result(target["bundle"]),
            _Result(target["adapter"]),
            _Result(
                {"id": shell_id, "worker_id": state["worker_id"], "status": "active"}
            ),
        ]
    )

    result = await storage.rollback_rollout_atomically(
        rollout_id=state["rollout_id"],
        worker_id=state["worker_id"],
        steward_id=state["steward_id"],
        candidate_id=state["candidate_id"],
        expected_status="PENDING",
        reason="operator rollback",
        triggered_by="operator",
        completed_at=datetime.now(tz=UTC),
    )

    assert result is None
    assert conn.execute.await_count == 13
    assert all(
        not getattr(call.args[0], "is_update", False)
        and not getattr(call.args[0], "is_insert", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_rollout_rollback_updates_rollout_pointers_and_evidence_in_one_transaction():
    from datetime import UTC, datetime

    state = _state()
    state["rollout_id"] = uuid4()
    rows = _rollback_rows(state)
    storage, conn, transaction = _storage(
        [
            _Result(rows["worker"]),
            _Result(rows["steward"]),
            _Result(rows["rollout"]),
            _Result(rows["candidate"]),
            _Result(state["certification"]),
            _Result(state["approval"]),
            _Result(rows["bundle"]),
            _Result(rows["adapter"]),
            _Result(rows=[]),  # no previous active rollout; deactivate safely
            _Result(rowcount=1),  # current adapter superseded
            _Result(rowcount=1),  # current bundle superseded
            _Result(rowcount=1),  # worker pointers cleared
            _Result(rowcount=1),  # steward pointers cleared
            _Result(rowcount=1),  # rollout CAS
            _Result(),  # ROLLING_BACK transition
            _Result(),  # ROLLED_BACK transition
            _Result(),  # rollback evidence
        ]
    )
    storage.get_rollout_record = AsyncMock()
    completed_at = datetime.now(tz=UTC)

    result = await storage.rollback_rollout_atomically(
        rollout_id=state["rollout_id"],
        worker_id=state["worker_id"],
        steward_id=state["steward_id"],
        candidate_id=state["candidate_id"],
        expected_status="PENDING",
        reason="operator rollback",
        triggered_by="operator@example.invalid",
        completed_at=completed_at,
    )

    assert result["rollout"]["status"] == "ROLLED_BACK"
    assert result["target_candidate_id"] is None
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert all(statement.is_select for statement in statements[:9])
    assert [statement.table for statement in statements[9:]] == [
        t.runtime_adapters,
        t.skill_bundles,
        t.worker_registry,
        t.steward_agents,
        t.rollout_records,
        t.rollout_transitions,
        t.rollout_transitions,
        t.rollback_records,
    ]
    transaction.__aexit__.assert_awaited_once_with(None, None, None)
    storage.get_rollout_record.assert_not_awaited()


@pytest.mark.anyio
async def test_rollout_rollback_restores_only_the_last_active_candidate():
    from datetime import UTC, datetime

    state = _state()
    state["rollout_id"] = uuid4()
    rows = _rollback_rows(state)
    target = _state(worker_id=state["worker_id"], steward_id=state["steward_id"])
    target_record = target["candidate"]["evidence_json"]["candidate_record"]
    target_record["worker_id"] = str(state["worker_id"])
    target_record["steward_id"] = str(state["steward_id"])
    target["candidate"]["worker_id"] = state["worker_id"]
    target["bundle"]["worker_id"] = state["worker_id"]
    target["bundle"]["steward_id"] = state["steward_id"]
    target["adapter"]["worker_id"] = state["worker_id"]
    target["certification"].update(
        worker_id=state["worker_id"],
        candidate_id=target["candidate_id"],
        steward_id=state["steward_id"],
    )
    target["approval"]["scope_id"] = target["candidate_id"]
    shell_id = uuid4()
    target["candidate"]["evidence_json"]["worker_shell_version_id"] = str(shell_id)
    target_rollout = {
        "id": uuid4(),
        "worker_id": state["worker_id"],
        "steward_id": state["steward_id"],
        "candidate_id": target["candidate_id"],
        "status": "ACTIVE",
        "started_at": datetime.now(tz=UTC),
    }
    storage, conn, _transaction = _storage(
        [
            _Result(rows["worker"]),
            _Result(rows["steward"]),
            _Result(rows["rollout"]),
            _Result(rows["candidate"]),
            _Result(state["certification"]),
            _Result(state["approval"]),
            _Result(rows["bundle"]),
            _Result(rows["adapter"]),
            _Result(rows=[target_rollout]),
            _Result(target["candidate"]),
            _Result(target["bundle"]),
            _Result(target["adapter"]),
            _Result(
                {"id": shell_id, "worker_id": state["worker_id"], "status": "active"}
            ),
            _Result(target["certification"]),
            _Result(target["approval"]),
            _Result(rowcount=1),  # current adapter superseded
            _Result(rowcount=1),  # current bundle superseded
            _Result(rowcount=1),  # target adapter restored
            _Result(rowcount=1),  # target bundle restored
            _Result(rowcount=1),  # worker pointers restored
            _Result(rowcount=1),  # steward pointers restored
            _Result(rowcount=1),  # rollout CAS
            _Result(),
            _Result(),
            _Result(),
        ]
    )

    result = await storage.rollback_rollout_atomically(
        rollout_id=state["rollout_id"],
        worker_id=state["worker_id"],
        steward_id=state["steward_id"],
        candidate_id=state["candidate_id"],
        expected_status="PENDING",
        reason="operator rollback",
        triggered_by="operator",
        completed_at=datetime.now(tz=UTC),
    )

    assert result["target_candidate_id"] == target["candidate_id"]
    assert result["target_bundle_id"] == target["bundle_id"]
    assert result["target_adapter_id"] == target["adapter_id"]
    assert result["target_shell_id"] == shell_id
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert all(statement.is_select for statement in statements[:15])
    assert statements[17].table is t.runtime_adapters
    assert statements[18].table is t.skill_bundles
    assert statements[19].table is t.worker_registry
    assert statements[20].table is t.steward_agents


@pytest.mark.anyio
async def test_rollout_rollback_cas_failure_aborts_the_transaction():
    from datetime import UTC, datetime

    state = _state()
    state["rollout_id"] = uuid4()
    rows = _rollback_rows(state)
    storage, _conn, transaction = _storage(
        [
            _Result(rows["worker"]),
            _Result(rows["steward"]),
            _Result(rows["rollout"]),
            _Result(rows["candidate"]),
            _Result(state["certification"]),
            _Result(state["approval"]),
            _Result(rows["bundle"]),
            _Result(rows["adapter"]),
            _Result(rows=[]),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=0),  # rollout CAS lost after updates were prepared
        ]
    )

    with pytest.raises(RuntimeError, match="rollback rollout compare-and-set failed"):
        await storage.rollback_rollout_atomically(
            rollout_id=state["rollout_id"],
            worker_id=state["worker_id"],
            steward_id=state["steward_id"],
            candidate_id=state["candidate_id"],
            expected_status="PENDING",
            reason="operator rollback",
            triggered_by="operator",
            completed_at=datetime.now(tz=UTC),
        )

    assert isinstance(transaction.__aexit__.await_args.args[1], RuntimeError)


@pytest.mark.anyio
async def test_rollout_rollback_completes_an_existing_rolling_back_state():
    from datetime import UTC, datetime

    state = _state()
    state["rollout_id"] = uuid4()
    rows = _rollback_rows(state)
    rows["rollout"]["status"] = "ROLLING_BACK"
    storage, conn, _transaction = _storage(
        [
            _Result(rows["worker"]),
            _Result(rows["steward"]),
            _Result(rows["rollout"]),
            _Result(rows["candidate"]),
            _Result(state["certification"]),
            _Result(state["approval"]),
            _Result(rows["bundle"]),
            _Result(rows["adapter"]),
            _Result(rows=[]),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(rowcount=1),
            _Result(),  # ROLLED_BACK transition only; no self-transition
            _Result(),  # rollback evidence
        ]
    )

    result = await storage.rollback_rollout_atomically(
        rollout_id=state["rollout_id"],
        worker_id=state["worker_id"],
        steward_id=state["steward_id"],
        candidate_id=state["candidate_id"],
        expected_status="ROLLING_BACK",
        reason="operator resumes rollback",
        triggered_by="operator",
        completed_at=datetime.now(tz=UTC),
    )

    assert result["rollout"]["status"] == "ROLLED_BACK"
    statements = [call.args[0] for call in conn.execute.await_args_list]
    transitions = [
        statement
        for statement in statements
        if getattr(statement, "table", None) is t.rollout_transitions
    ]
    assert len(transitions) == 1
    assert transitions[0].is_insert
