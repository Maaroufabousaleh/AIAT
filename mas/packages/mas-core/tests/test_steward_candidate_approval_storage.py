from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.memory import models as t
from mas_core.memory.storage import AgentStorage


class _Result:
    def __init__(self, row=None, *, rowcount: int = 1):
        self.row = row
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self.row


def _state(*, status: str = "CERTIFYING", approval_id=None):
    from mas_core.worker_registry.steward import (
        CandidateIntakeStatus,
        ExternalProvenance,
        ExternalWorkerSteward,
        StewardStatus,
    )

    worker_id = uuid4()
    provenance = ExternalProvenance(
        canonical_source_repository="https://github.com/example/worker",
        source_provider="github",
        exact_release="1.0.0",
        transport_type="process",
        security_scan_status="passed",
    )
    steward = ExternalWorkerSteward(worker_id=str(worker_id), provenance=provenance)
    steward.transition(StewardStatus.READY, actor="operator")
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
    if status == "APPROVED":
        approval_id = approval_id or uuid4()
        candidate = steward.approve_candidate(
            candidate.candidate_id,
            approval_record_id=approval_id,
        )
    elif status != "CERTIFYING":
        raise ValueError("approval storage fixture supports CERTIFYING or APPROVED")

    candidate_id = candidate.candidate_id
    steward_id = steward.steward_id
    bundle_id = candidate.bundle.bundle_id
    adapter_id = candidate.adapter.adapter_id
    certification_id = certification.certification_id
    approval_id = candidate.approval_record_id
    candidate_record = candidate.model_dump(mode="json")
    candidate_row = {
        "id": candidate_id,
        "worker_id": worker_id,
        "skill_bundle_id": bundle_id,
        "adapter_id": adapter_id,
        "intake_status": candidate.intake_status.value,
        "diff_json": candidate.diff,
        "evidence_json": {"candidate_record": candidate_record},
        "certification_run_id": certification_id,
        "approval_record_id": approval_id,
    }
    state = {
        "worker_id": worker_id,
        "steward_id": steward_id,
        "candidate_id": candidate_id,
        "bundle_id": bundle_id,
        "adapter_id": adapter_id,
        "certification_id": certification_id,
        "approval_id": approval_id,
        "worker": {"id": worker_id},
        "steward": {"id": steward_id, "worker_id": worker_id},
        "candidate": candidate_row,
        "certification": {
            "id": certification_id,
            "worker_id": worker_id,
            "candidate_id": candidate_id,
            "steward_id": steward_id,
            "status": "passed",
        },
        "bundle": {
            "id": bundle_id,
            "worker_id": worker_id,
            "steward_id": steward_id,
            "content_hash": candidate.bundle.content_hash,
        },
        "adapter": {
            "id": adapter_id,
            "worker_id": worker_id,
            "content_hash": candidate.adapter.content_hash,
            "conformance_status": "passed",
            "status": "inactive",
        },
        "bundle_model_hash": candidate.bundle.content_hash,
    }
    state["snapshot"] = {
        key: candidate_row.get(key)
        for key in (
            "id",
            "worker_id",
            "skill_bundle_id",
            "adapter_id",
            "intake_status",
            "diff_json",
            "evidence_json",
            "certification_run_id",
            "approval_record_id",
        )
    }
    return state


def _storage(state, results):
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


def _locked_rows(state, *, current_candidate=None, approval=None):
    return [
        _Result(state["worker"]),
        _Result(state["steward"]),
        _Result(current_candidate or state["candidate"]),
        _Result(state["certification"]),
        _Result(state["bundle"]),
        _Result(state["adapter"]),
        *([_Result(approval)] if approval is not None else []),
    ]


def _approved_record(state, approval_id):
    result = deepcopy(state["candidate"]["evidence_json"]["candidate_record"])
    result["intake_status"] = "APPROVED"
    result["approval_record_id"] = str(approval_id)
    result["bundle"]["status"] = "APPROVED"
    result["adapter"]["status"] = "APPROVED"
    return result


def _approval(state, approval_id):
    return {
        "id": approval_id,
        "scope_type": "worker_skill_bundle_candidate",
        "scope_id": state["candidate_id"],
        "decision": "APPROVED",
        "decided_by": "operator",
        "reason": "reviewed",
        "evidence": {"reviewed": True},
        "expires_at": None,
    }


@pytest.mark.anyio
async def test_candidate_approval_inserts_once_and_promotes_in_one_transaction():
    state = _state()
    serialized_bundle = state["candidate"]["evidence_json"]["candidate_record"]["bundle"]
    assert "content_hash" not in serialized_bundle
    assert state["bundle"]["content_hash"] == state["bundle_model_hash"]
    approval_id = uuid4()
    approval = _approval(state, approval_id)
    after_record = _approved_record(state, approval_id)
    results = [
        *_locked_rows(state),
        _Result(),  # approval insert
        _Result(rowcount=1),  # candidate lifecycle CAS
        _Result(),  # bundle status
        _Result(),  # adapter status
        _Result(),  # worker evaluation status
    ]
    storage, conn, transaction = _storage(state, results)

    result = await storage.approve_steward_candidate_atomically(
        worker_id=state["worker_id"],
        candidate_id=state["candidate_id"],
        expected_candidate_snapshot=state["snapshot"],
        candidate_record_after=after_record,
        approval=approval,
    )

    assert result is not None
    assert result["idempotent"] is False
    assert result["candidate"]["intake_status"] == "APPROVED"
    assert result["candidate"]["approval_record_id"] == approval_id
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert sum(
        statement.is_insert and statement.table is t.approval_records
        for statement in statements
    ) == 1
    assert sum(
        statement.is_update and statement.table is t.skill_bundle_candidates
        for statement in statements
    ) == 1
    transaction.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.anyio
async def test_candidate_approval_replay_returns_existing_approval_without_writes():
    state = _state(status="APPROVED")
    approval = _approval(state, state["approval_id"])
    results = [*_locked_rows(state), _Result(approval)]
    storage, conn, _transaction = _storage(state, results)

    result = await storage.approve_steward_candidate_atomically(
        worker_id=state["worker_id"],
        candidate_id=state["candidate_id"],
        expected_candidate_snapshot=state["snapshot"],
    )

    assert conn.execute.await_count == 7
    assert result is not None
    assert result["idempotent"] is True
    assert result["approval"]["id"] == state["approval_id"]
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_stale_certifying_approval_retries_resolve_to_the_same_durable_approval():
    state = _state()
    approval_id = uuid4()
    approval = _approval(state, approval_id)
    approved_candidate = deepcopy(state["candidate"])
    approved_candidate["intake_status"] = "APPROVED"
    approved_candidate["approval_record_id"] = approval_id
    approved_candidate["evidence_json"]["candidate_record"] = _approved_record(
        state, approval_id
    )
    results = [
        *_locked_rows(state, current_candidate=approved_candidate),
        _Result(approval),
    ]
    storage, conn, _transaction = _storage(state, results)

    result = await storage.approve_steward_candidate_atomically(
        worker_id=state["worker_id"],
        candidate_id=state["candidate_id"],
        expected_candidate_snapshot=state["snapshot"],
        candidate_record_after=_approved_record(state, uuid4()),
        approval=_approval(state, uuid4()),
    )

    assert conn.execute.await_count == 7
    assert result is not None
    assert result["idempotent"] is True
    assert result["approval"]["id"] == approval_id
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_stale_certifying_approval_does_not_accept_changed_candidate_identity():
    state = _state()
    approval_id = uuid4()
    approval = _approval(state, approval_id)
    approved_candidate = deepcopy(state["candidate"])
    approved_candidate["intake_status"] = "APPROVED"
    approved_candidate["approval_record_id"] = approval_id
    approved_candidate["evidence_json"]["candidate_record"] = _approved_record(
        state, approval_id
    )
    approved_candidate["evidence_json"]["candidate_record"]["source_provenance"] = {
        "exact_release": "changed"
    }
    storage, conn, _transaction = _storage(
        state,
        _locked_rows(state, current_candidate=approved_candidate),
    )

    result = await storage.approve_steward_candidate_atomically(
        worker_id=state["worker_id"],
        candidate_id=state["candidate_id"],
        expected_candidate_snapshot=state["snapshot"],
        candidate_record_after=_approved_record(state, uuid4()),
        approval=approval,
    )

    assert result is None
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_stale_certifying_approval_requires_a_certifying_expected_record():
    state = _state()
    approval_id = uuid4()
    approval = _approval(state, approval_id)
    approved_candidate = deepcopy(state["candidate"])
    approved_candidate["intake_status"] = "APPROVED"
    approved_candidate["approval_record_id"] = approval_id
    approved_candidate["evidence_json"]["candidate_record"] = _approved_record(
        state, approval_id
    )
    malformed_expected = deepcopy(state["snapshot"])
    malformed_expected["evidence_json"]["candidate_record"][
        "intake_status"
    ] = "BLOCKED"
    storage, conn, _transaction = _storage(
        state,
        _locked_rows(state, current_candidate=approved_candidate),
    )

    result = await storage.approve_steward_candidate_atomically(
        worker_id=state["worker_id"],
        candidate_id=state["candidate_id"],
        expected_candidate_snapshot=malformed_expected,
        candidate_record_after=_approved_record(state, uuid4()),
        approval=approval,
    )

    assert result is None
    assert conn.execute.await_count == 3


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("source_provenance", "exact_release", "changed"),
        ("bundle", "content_hash", "c" * 64),
        ("bundle", "verified_capabilities", ["forged-capability"]),
        ("adapter", "implementation_ref", "changed-implementation"),
        ("adapter", "content_hash", "c" * 64),
        ("adapter", "adapter_id", str(uuid4())),
    ],
)
@pytest.mark.anyio
async def test_candidate_approval_rejects_mutated_proposed_candidate_record(
    section, field, value
):
    state = _state()
    approval_id = uuid4()
    after_record = _approved_record(state, approval_id)
    after_record[section][field] = value
    storage, conn, _transaction = _storage(state, _locked_rows(state))

    result = await storage.approve_steward_candidate_atomically(
        worker_id=state["worker_id"],
        candidate_id=state["candidate_id"],
        expected_candidate_snapshot=state["snapshot"],
        candidate_record_after=after_record,
        approval=_approval(state, approval_id),
    )

    assert result is None
    assert conn.execute.await_count == 6
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.parametrize(
    ("row_index", "field", "value"),
    [
        (4, "id", uuid4()),
        (4, "worker_id", uuid4()),
        (4, "steward_id", uuid4()),
        (4, "content_hash", "c" * 64),
        (4, "content_hash", None),
        (5, "id", uuid4()),
        (5, "worker_id", uuid4()),
        (5, "content_hash", "c" * 64),
        (5, "content_hash", None),
        (5, "conformance_status", "failed"),
    ],
)
@pytest.mark.anyio
async def test_candidate_approval_rejects_locked_bundle_or_adapter_drift(
    row_index, field, value
):
    state = _state()
    locked_rows = _locked_rows(state)
    locked_rows[row_index].row[field] = value
    storage, conn, _transaction = _storage(state, locked_rows)
    approval_id = uuid4()

    result = await storage.approve_steward_candidate_atomically(
        worker_id=state["worker_id"],
        candidate_id=state["candidate_id"],
        expected_candidate_snapshot=state["snapshot"],
        candidate_record_after=_approved_record(state, approval_id),
        approval=_approval(state, approval_id),
    )

    assert result is None
    assert conn.execute.await_count == 6
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_candidate_approval_cas_failure_rolls_back_inserted_approval():
    state = _state()
    approval_id = uuid4()
    results = [
        *_locked_rows(state),
        _Result(),  # approval insert is inside the same transaction
        _Result(rowcount=0),  # candidate CAS loses
    ]
    storage, conn, transaction = _storage(state, results)

    with pytest.raises(RuntimeError, match="approval CAS failed"):
        await storage.approve_steward_candidate_atomically(
            worker_id=state["worker_id"],
            candidate_id=state["candidate_id"],
            expected_candidate_snapshot=state["snapshot"],
            candidate_record_after=_approved_record(state, approval_id),
            approval=_approval(state, approval_id),
        )

    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert any(
        statement.is_insert and statement.table is t.approval_records
        for statement in statements
    )
    assert not any(
        statement.is_update and statement.table in {t.skill_bundles, t.runtime_adapters}
        for statement in statements
    )
    exit_args = transaction.__aexit__.await_args.args
    assert exit_args[0] is RuntimeError


@pytest.mark.anyio
async def test_candidate_approval_preserves_an_already_active_adapter():
    state = _state()
    state["adapter"]["status"] = "active"
    approval_id = uuid4()
    results = [
        *_locked_rows(state),
        _Result(),
        _Result(rowcount=1),
        _Result(),
        _Result(),
        _Result(),
    ]
    storage, conn, _transaction = _storage(state, results)

    result = await storage.approve_steward_candidate_atomically(
        worker_id=state["worker_id"],
        candidate_id=state["candidate_id"],
        expected_candidate_snapshot=state["snapshot"],
        candidate_record_after=_approved_record(state, approval_id),
        approval=_approval(state, approval_id),
    )

    assert result is not None
    adapter_update = next(
        statement
        for statement in (call.args[0] for call in conn.execute.await_args_list)
        if statement.is_update and statement.table is t.runtime_adapters
    )
    status_value = next(
        value
        for column, value in adapter_update._values.items()
        if getattr(column, "key", column) == "status"
    )
    assert status_value.value == "active"
