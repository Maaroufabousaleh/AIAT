"""First-party candidate governance writes use locked compare-and-set semantics."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.memory import models as t
from mas_core.memory.storage import AgentStorage, worker_identity_snapshot_for_rollout
from mas_core.worker_registry.source_bound_security_scan import (
    FIRST_PARTY_IMAGE_DIGEST,
    FIRST_PARTY_RUNTIME_SHA256,
    FIRST_PARTY_SOURCE_REPOSITORY,
    FIRST_PARTY_SOURCE_REVISION,
    FIRST_PARTY_WORKER_ID,
    FIRST_PARTY_WORKER_VERSION,
)


class _Result:
    def __init__(self, row=None, *, rowcount: int = 1):
        self.row = row
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self.row


def _candidate_state(*, status: str = "CERTIFYING"):
    worker_id = uuid4()
    candidate_id = uuid4()
    bundle_id = uuid4()
    adapter_id = uuid4()
    steward_id = uuid4()
    provenance_hash = "a" * 64
    report_sha256 = "b" * 64
    source = {
        "canonical_source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_provider": "aiat",
        "exact_release": FIRST_PARTY_WORKER_VERSION,
        "commit_sha": FIRST_PARTY_SOURCE_REVISION,
        "package_version": FIRST_PARTY_WORKER_VERSION,
        "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
        "dependency_lock_hash": "c" * 64,
        "protocol_api_version": "aiat.worker.v1",
        "adapter_version": "0.1.1",
        "transport_type": "oci",
        "runtime_fingerprint": f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
    }
    worker = {
        "id": worker_id,
        "name": FIRST_PARTY_WORKER_ID,
        "adapter_type": "oci",
        "adapter_config": {
            "image": f"aiat-deterministic-file-worker@{FIRST_PARTY_IMAGE_DIGEST}",
            "capabilities": {"file_write": True},
        },
        "sandbox_profile": "sandboxed",
        "capability_ids": [],
        "team_id": "dept_engineering",
        "status": "INACTIVE",
        "version": FIRST_PARTY_WORKER_VERSION,
        "source_repo": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_revision": FIRST_PARTY_SOURCE_REVISION,
        "version_pin": f"{FIRST_PARTY_WORKER_VERSION}+{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}",
        "update_policy": "manual",
        "evaluation_status": "pending",
        "adapter_entrypoint": "ProcessAdapter",
        "adapter_module": None,
        "wrapper_config": {},
        "isolation_mode": "wrapper",
        "active_shell_version_id": None,
        "active_adapter_id": None,
        "active_skill_bundle_id": None,
        "model_profile_id": None,
        "model_mode": "none",
        "upstream_commit_sha": FIRST_PARTY_SOURCE_REVISION,
    }
    provenance = {
        "id": uuid4(),
        "worker_id": worker_id,
        **source,
        "security_scan_status": "passed",
        "provenance_hash": provenance_hash,
    }
    scan_report = {"status": "passed", "report_sha256": report_sha256}
    steward = {
        "id": steward_id,
        "worker_id": worker_id,
        "metadata": {
            "source_provenance_extra": {
                "source_bound_security_scan": scan_report,
            }
        },
    }
    candidate_record = {
        "candidate_id": str(candidate_id),
        "worker_id": str(worker_id),
        "steward_id": str(steward_id),
        "intake_status": status,
        "certification_id": None,
        "approval_record_id": None,
        "source_provenance": source,
        "bundle": {
            "bundle_id": str(bundle_id),
            "semantic_version": "0.1.1",
            "steward_id": str(steward_id),
            "source_provenance": source,
        },
        "adapter": {
            "adapter_id": str(adapter_id),
            "version": "0.1.1",
            "transport_type": "oci",
            "implementation_ref": FIRST_PARTY_SOURCE_REVISION,
            "content_hash": "d" * 64,
            "source_provenance": source,
        },
    }
    candidate = {
        "id": candidate_id,
        "worker_id": worker_id,
        "skill_bundle_id": bundle_id,
        "adapter_id": adapter_id,
        "intake_status": status,
        "certification_run_id": None,
        "approval_record_id": None,
        "evidence_json": {"candidate_record": candidate_record},
    }
    bundle = {
        "id": bundle_id,
        "worker_id": worker_id,
        "steward_id": steward_id,
        "semantic_version": "0.1.1",
        "content_hash": "e" * 64,
    }
    adapter = {
        "id": adapter_id,
        "worker_id": worker_id,
        "version": "0.1.1",
        "transport_type": "oci",
        "implementation_ref": FIRST_PARTY_SOURCE_REVISION,
        "content_hash": "d" * 64,
        "conformance_status": "pending",
        "status": "candidate",
    }
    snapshot = {
        "id": candidate_id,
        "worker_id": worker_id,
        "skill_bundle_id": bundle_id,
        "adapter_id": adapter_id,
        "intake_status": status,
        "certification_run_id": None,
        "approval_record_id": None,
        "evidence_json": deepcopy(candidate["evidence_json"]),
        "candidate_record": deepcopy(candidate_record),
    }
    return locals()


def _fake_storage(state, later_results: list[_Result]):
    conn = MagicMock()
    locked_rows = [
        state["worker"],
        state["provenance"],
        state["steward"],
        state["candidate"],
        *later_results,
    ]
    conn.execute = AsyncMock(
        side_effect=[_Result(row) if not isinstance(row, _Result) else row for row in locked_rows]
    )
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=conn)
    tx.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = tx
    storage = AgentStorage("postgresql+asyncpg://localhost/unused")
    storage._engine = engine
    return storage, conn, tx


def _expected(state):
    return {
        "worker_id": state["worker_id"],
        "candidate_id": state["candidate_id"],
        "expected_worker_identity": worker_identity_snapshot_for_rollout(
            state["worker"]
        ),
        "expected_provenance_hash": state["provenance_hash"],
        "expected_scan_report_sha256": state["report_sha256"],
        "expected_candidate_snapshot": state["snapshot"],
    }


@pytest.mark.anyio
async def test_certification_rejects_concurrent_candidate_reject_before_writes():
    state = _candidate_state()
    certification_id = uuid4()
    candidate_after = {
        **deepcopy(state["candidate_record"]),
        "intake_status": "CERTIFYING",
        "certification_id": str(certification_id),
    }
    certification = {
        "id": certification_id,
        "worker_id": state["worker_id"],
        "candidate_id": state["candidate_id"],
        "steward_id": state["steward"]["id"],
        "status": "passed",
        "conformance_json": {},
        "checks_json": {},
        "evidence_json": {
            "source_identity_fingerprint": "f" * 64,
            "source_bound_security_scan_report_sha256": state["report_sha256"],
        },
        "failure_reasons": [],
        "completed_at": None,
    }
    state["candidate"]["intake_status"] = "REJECTED"
    state["candidate"]["evidence_json"]["candidate_record"][
        "intake_status"
    ] = "REJECTED"
    storage, conn, _tx = _fake_storage(state, [])

    result = await storage.persist_first_party_candidate_certification_atomically(
        **_expected(state),
        expected_source_identity_fingerprint="f" * 64,
        certification=certification,
        candidate_record_after=candidate_after,
        shell_values={
            "id": uuid4(),
            "worker_id": state["worker_id"],
            "version": "governed-test",
            "content_hash": "9" * 64,
        },
    )

    assert result is None
    assert conn.execute.await_count == 4
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_certification_persists_only_after_candidate_identity_is_locked():
    state = _candidate_state()
    certification_id = uuid4()
    candidate_after = {
        **deepcopy(state["candidate_record"]),
        "intake_status": "CERTIFYING",
        "certification_id": str(certification_id),
    }
    certification = {
        "id": certification_id,
        "worker_id": state["worker_id"],
        "candidate_id": state["candidate_id"],
        "steward_id": state["steward"]["id"],
        "status": "passed",
        "conformance_json": {"passed": True},
        "checks_json": {"security": True},
        "evidence_json": {
            "source_identity_fingerprint": "f" * 64,
            "source_bound_security_scan_report_sha256": state["report_sha256"],
        },
        "failure_reasons": [],
        "completed_at": None,
    }
    shell = {
        "id": uuid4(),
        "worker_id": state["worker_id"],
        "version": "governed-test",
        "content_hash": "9" * 64,
    }
    results = [
        _Result(state["bundle"]),
        _Result(state["adapter"]),
        _Result(),  # shell lookup: no existing immutable version
        _Result(),  # shell INSERT
        _Result(),  # certification INSERT
        _Result(rowcount=1),  # candidate CAS update
        _Result(),  # adapter conformance update
    ]
    storage, conn, tx = _fake_storage(state, results)

    result = await storage.persist_first_party_candidate_certification_atomically(
        **_expected(state),
        expected_source_identity_fingerprint="f" * 64,
        certification=certification,
        candidate_record_after=candidate_after,
        shell_values=shell,
    )

    assert result is not None
    assert result["candidate"]["intake_status"] == "CERTIFYING"
    assert result["candidate"]["certification_run_id"] == certification_id
    assert result["worker_shell_version"]["content_hash"] == shell["content_hash"]
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert any(
        statement.is_insert and statement.table is t.certification_runs
        for statement in statements
    )
    assert tx.__aexit__.await_args.args[0] is None


@pytest.mark.anyio
async def test_approval_rejects_concurrent_candidate_reject_without_orphan_approval():
    state = _candidate_state()
    state["candidate"]["intake_status"] = "REJECTED"
    state["candidate"]["evidence_json"]["candidate_record"][
        "intake_status"
    ] = "REJECTED"
    storage, conn, _tx = _fake_storage(state, [])

    result = await storage.approve_first_party_candidate_atomically(
        **_expected(state),
        expected_source_identity_fingerprint="f" * 64,
        approval={"id": uuid4()},
        candidate_record_after={},
    )

    assert result is None
    assert conn.execute.await_count == 4
    assert all(
        not getattr(call.args[0], "is_insert", False)
        and not getattr(call.args[0], "is_update", False)
        for call in conn.execute.await_args_list
    )


@pytest.mark.anyio
async def test_approval_cas_failure_rolls_back_inserted_approval():
    state = _candidate_state()
    certification_id = uuid4()
    state["candidate"]["certification_run_id"] = certification_id
    state["candidate"]["evidence_json"]["candidate_record"][
        "certification_id"
    ] = str(certification_id)
    state["snapshot"]["certification_run_id"] = certification_id
    state["snapshot"]["candidate_record"]["certification_id"] = str(
        certification_id
    )
    state["snapshot"]["evidence_json"] = deepcopy(
        state["candidate"]["evidence_json"]
    )
    state["candidate_record"] = state["candidate"]["evidence_json"][
        "candidate_record"
    ]
    state["certification"] = {
        "id": certification_id,
        "worker_id": state["worker_id"],
        "candidate_id": state["candidate_id"],
        "status": "passed",
        "evidence_json": {
            "source_identity_fingerprint": "f" * 64,
            "source_bound_security_scan_report_sha256": state["report_sha256"],
        },
    }
    state["adapter"]["conformance_status"] = "passed"
    approval_id = uuid4()
    candidate_after = {
        **state["candidate_record"],
        "intake_status": "APPROVED",
        "approval_record_id": str(approval_id),
    }
    results = [
        _Result(state["certification"]),
        _Result(state["bundle"]),
        _Result(state["adapter"]),
        _Result(),  # approval INSERT
        _Result(rowcount=0),  # candidate CAS update
    ]
    storage, conn, tx = _fake_storage(state, results)

    with pytest.raises(RuntimeError, match="approval CAS failed"):
        await storage.approve_first_party_candidate_atomically(
            **_expected(state),
            expected_source_identity_fingerprint="f" * 64,
            approval={
                "id": approval_id,
                "scope_type": "worker_skill_bundle_candidate",
                "scope_id": state["candidate_id"],
                "decision": "APPROVED",
                "decided_by": "operator",
                "reason": "reviewed",
                "evidence": {},
                "expires_at": None,
            },
            candidate_record_after=candidate_after,
        )

    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert any(statement.is_insert and statement.table is t.approval_records for statement in statements)
    assert tx.__aexit__.await_args.args[0] is RuntimeError


@pytest.mark.anyio
async def test_approval_updates_all_governed_rows_in_one_transaction():
    state = _candidate_state()
    certification_id = uuid4()
    state["candidate"]["certification_run_id"] = certification_id
    state["candidate"]["evidence_json"]["candidate_record"][
        "certification_id"
    ] = str(certification_id)
    state["snapshot"]["certification_run_id"] = certification_id
    state["snapshot"]["candidate_record"]["certification_id"] = str(
        certification_id
    )
    state["snapshot"]["evidence_json"] = deepcopy(
        state["candidate"]["evidence_json"]
    )
    state["candidate_record"] = state["candidate"]["evidence_json"][
        "candidate_record"
    ]
    state["certification"] = {
        "id": certification_id,
        "worker_id": state["worker_id"],
        "candidate_id": state["candidate_id"],
        "status": "passed",
        "evidence_json": {
            "source_identity_fingerprint": "f" * 64,
            "source_bound_security_scan_report_sha256": state["report_sha256"],
        },
    }
    state["adapter"]["conformance_status"] = "passed"
    approval_id = uuid4()
    candidate_after = {
        **state["candidate_record"],
        "intake_status": "APPROVED",
        "approval_record_id": str(approval_id),
    }
    results = [
        _Result(state["certification"]),
        _Result(state["bundle"]),
        _Result(state["adapter"]),
        _Result(),  # approval INSERT
        _Result(rowcount=1),  # candidate CAS update
        _Result(),  # bundle status update
        _Result(),  # adapter status update
        _Result(),  # worker evaluation update
    ]
    storage, conn, tx = _fake_storage(state, results)

    result = await storage.approve_first_party_candidate_atomically(
        **_expected(state),
        expected_source_identity_fingerprint="f" * 64,
        approval={
            "id": approval_id,
            "scope_type": "worker_skill_bundle_candidate",
            "scope_id": state["candidate_id"],
            "decision": "APPROVED",
            "decided_by": "operator",
            "reason": "reviewed",
            "evidence": {},
            "expires_at": None,
        },
        candidate_record_after=candidate_after,
    )

    assert result is not None
    assert result["candidate"]["intake_status"] == "APPROVED"
    statements = [call.args[0] for call in conn.execute.await_args_list]
    inserted_tables = [
        statement.table for statement in statements if statement.is_insert
    ]
    assert inserted_tables == [t.approval_records]
    assert sum(statement.is_update for statement in statements) == 4
    assert tx.__aexit__.await_args.args[0] is None
