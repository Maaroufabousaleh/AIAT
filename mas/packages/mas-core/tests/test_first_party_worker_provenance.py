"""First-party OCI workers use the governed steward path without looser gates."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from mas_core.worker_registry.steward import (
    BundleStatus,
    CandidateIntakeStatus,
    ExternalProvenance,
    ExternalWorkerSteward,
    RolloutStatus,
    StewardStatus,
    StewardTransitionError,
    operational_promotion_checks,
)
from mas_core.worker_registry.worker_steward_readiness import (
    evaluate_worker_steward_readiness,
)

WORKER_ID = "00000000-0000-4000-8000-000000000301"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000302"
SOURCE_REPOSITORY = "https://github.com/Maaroufabousaleh/AIAT"
SOURCE_COMMIT = "a" * 40
OCI_DIGEST = f"sha256:{'b' * 64}"


def _first_party_provenance(*, security_scan_status: str = "pending") -> ExternalProvenance:
    return ExternalProvenance(
        canonical_source_repository=SOURCE_REPOSITORY,
        source_provider="aiat",
        commit_sha=SOURCE_COMMIT,
        oci_image_digest=OCI_DIGEST,
        transport_type="oci",
        security_scan_status=security_scan_status,
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"commit_sha": None}, "exact 40- or 64-character Git commit SHA"),
        ({"commit_sha": "working-tree"}, "exact 40- or 64-character Git commit SHA"),
        ({"oci_image_digest": None}, "OCI provenance requires an image digest"),
        ({"oci_image_digest": "aiat/worker:dev"}, "sha256 image digest"),
        ({"transport_type": "process"}, "OCI worker transport"),
        ({"canonical_source_repository": "local"}, "canonical AIAT HTTPS source repository"),
        (
            {"canonical_source_repository": "https://github.com/attacker/aiat"},
            "canonical AIAT HTTPS source repository",
        ),
        (
            {"canonical_source_repository": "https://example.test/aiat?branch=main"},
            "canonical AIAT HTTPS source repository",
        ),
    ],
)
def test_first_party_provenance_rejects_missing_or_mutable_identity(
    overrides: dict[str, str | None], message: str
) -> None:
    values = {
        "canonical_source_repository": SOURCE_REPOSITORY,
        "source_provider": "aiat",
        "commit_sha": SOURCE_COMMIT,
        "oci_image_digest": OCI_DIGEST,
        "transport_type": "oci",
    }
    values.update(overrides)

    with pytest.raises(ValidationError, match=message):
        ExternalProvenance(**values)


def _readiness_snapshot(provenance: ExternalProvenance) -> dict:
    return {
        "worker": {
            "id": WORKER_ID,
            "source_repo": SOURCE_REPOSITORY,
            "version_pin": SOURCE_COMMIT,
        },
        "steward": {
            "worker_id": WORKER_ID,
            "status": "READY",
            "provenance": provenance.model_dump(mode="json"),
        },
        "candidate": {
            "candidate_id": CANDIDATE_ID,
            "worker_id": WORKER_ID,
            "intake_status": "CERTIFYING",
            "bundle": {
                "bundle_id": "00000000-0000-4000-8000-000000000303",
                "documentation_refs": ["00000000-0000-4000-8000-000000000304"],
                "verified_capabilities": {"capabilities": {"file_write": True}},
            },
            "adapter": {
                "adapter_id": "00000000-0000-4000-8000-000000000305",
                "version": "0.1.0",
                "content_hash": "c" * 64,
            },
        },
        "worker_id": WORKER_ID,
        "candidate_id": CANDIDATE_ID,
    }


def test_valid_first_party_identity_only_clears_source_blocker() -> None:
    provenance = _first_party_provenance()
    assert provenance.source_provider == "aiat"
    assert provenance.canonical_source_repository == SOURCE_REPOSITORY
    assert provenance.commit_sha == SOURCE_COMMIT
    assert provenance.oci_image_digest == OCI_DIGEST
    assert provenance.transport_type == "oci"
    assert operational_promotion_checks(provenance) == {
        "provenance_pin": True,
        "security": False,
    }

    inputs = _readiness_snapshot(provenance)
    report = evaluate_worker_steward_readiness(**inputs)
    codes = {item["code"] for item in report["blockers"]}
    assert "worker_external_source_missing" not in codes
    assert "security_scan_not_passed" in codes

    inputs["worker"] = {**inputs["worker"], "source_repo": "local"}
    report = evaluate_worker_steward_readiness(**inputs)
    codes = {item["code"] for item in report["blockers"]}
    assert "worker_external_source_missing" in codes
    assert "security_scan_not_passed" in codes


def _advance_to_certifying(
    steward: ExternalWorkerSteward, candidate_id: UUID
) -> None:
    for stage in (
        CandidateIntakeStatus.SOURCE_REVIEW,
        CandidateIntakeStatus.SECURITY_REVIEW,
        CandidateIntakeStatus.INTERFACE_RESEARCH,
        CandidateIntakeStatus.GENERATED,
        CandidateIntakeStatus.CERTIFYING,
    ):
        steward.advance_candidate(candidate_id, stage)


def test_first_party_candidate_keeps_certification_and_approval_stages() -> None:
    pending_security = ExternalWorkerSteward(
        worker_id=WORKER_ID,
        provenance=_first_party_provenance(),
    )
    pending_security.transition(StewardStatus.READY, actor="test-fixture")
    blocked = pending_security.generate_candidate(
        semantic_version="0.1.0",
        adapter_version="0.1.0",
        upstream_compatibility_range="==0.1.0",
        implementation_ref="mas_core.worker_registry.deterministic_file_worker:generate_project_file",
    )
    _advance_to_certifying(pending_security, blocked.candidate_id)
    rejected = pending_security.certify_candidate(
        blocked.candidate_id,
        conformance={"passed": True},
        checks={"security": True},
    )
    assert rejected.passed is False
    assert blocked.intake_status == CandidateIntakeStatus.REJECTED
    assert blocked.approval_record_id is None

    approved_security = ExternalWorkerSteward(
        worker_id=WORKER_ID,
        provenance=_first_party_provenance(security_scan_status="passed"),
    )
    approved_security.transition(StewardStatus.READY, actor="test-fixture")
    candidate = approved_security.generate_candidate(
        semantic_version="0.1.0",
        adapter_version="0.1.0",
        upstream_compatibility_range="==0.1.0",
        implementation_ref="mas_core.worker_registry.deterministic_file_worker:generate_project_file",
    )
    with pytest.raises(StewardTransitionError, match="in CERTIFYING"):
        approved_security.certify_candidate(
            candidate.candidate_id,
            conformance={"passed": True},
            checks={},
        )

    _advance_to_certifying(approved_security, candidate.candidate_id)
    certification = approved_security.certify_candidate(
        candidate.candidate_id,
        conformance={"passed": True},
        checks={},
    )
    assert certification.passed is True
    assert candidate.intake_status == CandidateIntakeStatus.CERTIFYING
    assert candidate.bundle.status == BundleStatus.CERTIFIED
    assert candidate.approval_record_id is None
    with pytest.raises(StewardTransitionError, match="only approved candidates"):
        approved_security.start_rollout(
            candidate.candidate_id,
            actor="test-fixture",
            eligible_task_classes=["read_only"],
        )

    approval_id = UUID("00000000-0000-4000-8000-000000000306")
    approved_security.approve_candidate(
        candidate.candidate_id,
        approval_record_id=approval_id,
    )
    assert candidate.intake_status == CandidateIntakeStatus.APPROVED
    assert candidate.approval_record_id == approval_id
    rollout = approved_security.start_rollout(
        candidate.candidate_id,
        actor="test-fixture",
        eligible_task_classes=["read_only"],
    )
    assert rollout.status == RolloutStatus.PENDING
    assert candidate.intake_status == CandidateIntakeStatus.APPROVED
