"""Restart-safe active-pointer rehydration coverage for steward caches."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from mas_core.worker_registry.steward import (
    CandidateIntakeStatus,
    ExternalProvenance,
    ExternalWorkerSteward,
    StewardStatus,
)


class _RehydrationStorage:
    def __init__(self, *, steward: ExternalWorkerSteward, active_bundle_id: object, active_adapter_id: object) -> None:
        self._steward = steward
        self._active_bundle_id = active_bundle_id
        self._active_adapter_id = active_adapter_id

    async def get_steward_by_worker(self, worker_id):
        return {
            "id": self._steward.steward_id,
            "worker_id": worker_id,
            "status": "READY",
            "active_skill_bundle_id": self._active_bundle_id,
            "active_adapter_id": self._active_adapter_id,
        }

    async def get_external_provenance_by_worker(self, _worker_id):
        return self._steward.provenance.model_dump(mode="json")

    async def list_documentation_snapshots(self, _steward_id):
        return []

    async def list_capability_snapshots(self, _worker_id, *, steward_id=None):
        return []

    async def list_compatibility_matrices(self, _worker_id):
        candidate = next(iter(self._steward.candidates.values()))
        return [
            {
                "id": uuid4(),
                "runtime_version": "1.0.0",
                "adapter_version": "1.0.0",
                "contract_version": "aiat.adapter.v1",
                "model_profiles_json": {"worker": "profile-v1"},
                "capabilities_json": {"required_model_capabilities": ["tool_calling"]},
                "fixtures": ["worker_contract"],
                "passed": True,
                "created_at": None,
                "candidate_id": candidate.candidate_id,
            }
        ]

    async def list_skill_bundle_candidates(self, _worker_id):
        candidate = next(iter(self._steward.candidates.values()))
        return [
            {
                "evidence_json": {"candidate_record": candidate.model_dump(mode="json")},
                "intake_status": candidate.intake_status.value,
            }
        ]

    async def list_certification_runs(self, _worker_id):
        return []

    async def list_rollout_records(self, _worker_id):
        return []


def _steward_with_candidate() -> ExternalWorkerSteward:
    steward = ExternalWorkerSteward(
        worker_id="rehydration-test",
        steward_id=uuid4(),
        provenance=ExternalProvenance(
            canonical_source_repository="https://github.com/example/worker",
            exact_release="1.0.0",
            transport_type="http",
            security_scan_status="passed",
        ),
    )
    steward.transition(StewardStatus.READY, actor="test")
    steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
    )
    return steward


@pytest.mark.anyio
async def test_steward_runtime_rehydrates_durable_active_pointers() -> None:
    from orchestrator_api import main

    source = _steward_with_candidate()
    candidate = next(iter(source.candidates.values()))
    worker_id = uuid4()
    storage = _RehydrationStorage(
        steward=source,
        active_bundle_id=candidate.bundle.bundle_id,
        active_adapter_id=candidate.adapter.adapter_id,
    )
    main._worker_steward_runtimes.pop(str(worker_id), None)
    try:
        runtime = await main._steward_runtime(storage, worker_id)
        assert runtime is not None
        assert runtime.active_bundle is not None
        assert runtime.active_bundle.bundle_id == candidate.bundle.bundle_id
        assert runtime.active_adapter is not None
        assert runtime.active_adapter.adapter_id == candidate.adapter.adapter_id
        assert len(runtime.compatibility_matrices) == 1
        matrix = runtime.compatibility_matrices[0]
        assert matrix.model_profiles == {"worker": ("profile-v1",)}
        assert matrix.capabilities == {"required_model_capabilities": ["tool_calling"]}
        assert matrix.passed is True
    finally:
        main._worker_steward_runtimes.pop(str(worker_id), None)


@pytest.mark.anyio
async def test_steward_runtime_fails_closed_on_unknown_active_pointer() -> None:
    from orchestrator_api import main

    source = _steward_with_candidate()
    worker_id = uuid4()
    storage = _RehydrationStorage(
        steward=source,
        active_bundle_id=uuid4(),
        active_adapter_id=None,
    )
    main._worker_steward_runtimes.pop(str(worker_id), None)
    try:
        runtime = await main._steward_runtime(storage, worker_id)
        assert runtime is not None
        assert runtime.active_bundle is None
        assert runtime.active_adapter is None
    finally:
        main._worker_steward_runtimes.pop(str(worker_id), None)


@pytest.mark.anyio
async def test_steward_certification_blocks_before_unreviewed_candidate_execution(
    monkeypatch,
) -> None:
    from orchestrator_api import main
    from starlette.requests import Request

    from mas_core.worker_contract import conformance as conformance_module
    from mas_core.worker_registry import runtime_adapters

    worker_id = uuid4()
    steward = ExternalWorkerSteward(
        worker_id=str(worker_id),
        steward_id=uuid4(),
        provenance=ExternalProvenance(
            canonical_source_repository="https://example.invalid/local-worker",
            exact_release="1.0.0",
            transport_type="http",
            security_scan_status="pending",
        ),
        status=StewardStatus.READY,
    )
    candidate = steward.generate_candidate(
        semantic_version="1.0.0",
        adapter_version="1.0.0",
        upstream_compatibility_range="==1.0.0",
    )
    for status in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(candidate.candidate_id, status)

    storage = MagicMock()
    storage.get_worker = AsyncMock(
        return_value={
            "adapter_config": {},
            "sandbox_profile": "trusted",
            "version_pin": "1.0.0",
        }
    )
    storage.create_compatibility_matrix = AsyncMock()
    storage.create_certification_run = AsyncMock()
    storage.get_skill_bundle_candidate = AsyncMock(return_value=None)
    monkeypatch.setattr(main, "_storage", lambda: storage)
    monkeypatch.setattr(main, "_steward_runtime", AsyncMock(return_value=steward))

    adapter_for_transport = MagicMock(side_effect=AssertionError("unreviewed candidate must not execute"))
    monkeypatch.setattr(
        runtime_adapters,
        "adapter_for_transport",
        adapter_for_transport,
    )

    conformance_runner = MagicMock(side_effect=AssertionError("unreviewed candidate must not execute"))
    monkeypatch.setattr(
        conformance_module,
        "ConformanceRunner",
        conformance_runner,
    )

    operator_request = Request({"type": "http", "headers": []})
    operator_request.state.aiat_auth_principal = "operator"
    with pytest.raises(HTTPException) as exc_info:
        await main.certify_steward_candidate(
            worker_id,
            candidate.candidate_id,
            main.CandidateCertificationRequest(),
            operator_request,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "SECURITY_SCAN_EVIDENCE_REQUIRED"
    storage.create_compatibility_matrix.assert_not_awaited()
    storage.create_certification_run.assert_not_awaited()
    adapter_for_transport.assert_not_called()
    conformance_runner.assert_not_called()
    assert steward.compatibility_matrices == []
