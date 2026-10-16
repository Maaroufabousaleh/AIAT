"""First-party OCI workers use the governed steward path without looser gates."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.source_bound_security_scan import (
    FIRST_PARTY_BUILD_PROVENANCE_SUMMARY,
    FIRST_PARTY_DOCKERFILE_SHA256,
    FIRST_PARTY_IMAGE_DIGEST,
    FIRST_PARTY_MANIFEST_SHA256,
    FIRST_PARTY_PERMISSION_GRANTS,
    FIRST_PARTY_RUNTIME_ADAPTER_CONFIG,
    FIRST_PARTY_RUNTIME_SHA256,
    FIRST_PARTY_SEMGREP_CONFIG_SHA256,
    FIRST_PARTY_SOURCE_REPOSITORY,
    FIRST_PARTY_SOURCE_REVISION,
    FIRST_PARTY_TOOL_GRANTS,
    FIRST_PARTY_WORKER_ID,
    FIRST_PARTY_WORKER_VERSION,
    PINNED_SCANNERS,
    finalize_source_bound_scan_report,
    source_bound_security_scan_is_valid,
    source_bound_security_scan_report_integrity_is_valid,
    source_bound_security_scan_result_is_valid,
    source_bound_worker_identity_is_valid,
)
from mas_core.worker_registry.steward import (
    BundleStatus,
    CandidateIntakeStatus,
    ExternalProvenance,
    ExternalWorkerSteward,
    StewardStatus,
    StewardTransitionError,
    operational_promotion_checks,
)
from mas_core.worker_registry.worker_steward_readiness import (
    evaluate_worker_steward_readiness,
)

if TYPE_CHECKING:
    from uuid import UUID

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


def _valid_source_scan_report(worker_id: str = WORKER_ID) -> dict:
    source_dir = "/tmp/aiat-worker-security-scan-test/source"
    config_path = "/tmp/aiat-worker-security-scan-test/semgrep-default.yml"
    semgrep = PINNED_SCANNERS["semgrep"]
    trufflehog = PINNED_SCANNERS["trufflehog"]
    scanners = [
        {
            "name": "semgrep",
            "version": semgrep["version"],
            "version_argv": [semgrep["executable"], "--version"],
            "version_output_sha256": "a" * 64,
            "argv": [
                semgrep["executable"],
                "scan",
                "--config",
                config_path,
                "--json",
                "--metrics=off",
                "--disable-version-check",
                "--no-git-ignore",
                "--quiet",
                source_dir,
            ],
            "exit_code": 0,
            "status": "pass",
            "finding_count": 0,
            "scanner_error_count": 0,
            "scanner_errors": [],
            "findings": [],
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "result_sha256": "b" * 64,
            "stderr_sha256": "c" * 64,
        },
        {
            "name": "trufflehog",
            "version": trufflehog["version"],
            "version_argv": [trufflehog["executable"], "--version"],
            "version_output_sha256": "d" * 64,
            "argv": [
                trufflehog["executable"],
                "filesystem",
                "--json",
                "--no-update",
                "--no-verification",
                "--log-level=-1",
                source_dir,
            ],
            "exit_code": 0,
            "status": "pass",
            "finding_count": 0,
            "scanner_error_count": 0,
            "scanner_errors": [],
            "findings": [],
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "result_sha256": "e" * 64,
            "stderr_sha256": "f" * 64,
        },
    ]
    return finalize_source_bound_scan_report(
        {
            "schema_version": "aiat.first-party-worker-source-security-scan.v2",
            "image_build_provenance": dict(FIRST_PARTY_BUILD_PROVENANCE_SUMMARY),
            "status": "passed",
            "finding_review_status": "not_required",
            "worker_registry_id": worker_id,
            "candidate": {
                "worker_registry_id": worker_id,
                "manifest_worker_id": FIRST_PARTY_WORKER_ID,
                "version": FIRST_PARTY_WORKER_VERSION,
                "source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
                "source_revision": FIRST_PARTY_SOURCE_REVISION,
                "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
                "runtime_sha256": FIRST_PARTY_RUNTIME_SHA256,
                "dockerfile_sha256": FIRST_PARTY_DOCKERFILE_SHA256,
            },
            "manifest_sha256": FIRST_PARTY_MANIFEST_SHA256,
            "source_files": [
                {
                    "path": "mas/workers/aiat_deterministic_file_worker_process.py",
                    "sha256": FIRST_PARTY_RUNTIME_SHA256,
                },
                {
                    "path": "mas/workers/Dockerfile.aiat_deterministic_file_worker",
                    "sha256": FIRST_PARTY_DOCKERFILE_SHA256,
                },
            ],
            "semgrep_config_path": config_path,
            "semgrep_config_source_path": "/app/semgrep-default.yml",
            "semgrep_config_sha256": FIRST_PARTY_SEMGREP_CONFIG_SHA256,
            "semgrep_config_snapshot_mode": "0444",
            "scan_started_at": "2026-10-15T00:00:00+00:00",
            "scan_completed_at": "2026-10-15T00:00:01+00:00",
            "scanner_source_dir": source_dir,
            "scanners": scanners,
            "finding_count": 0,
            "scanner_error_count": 0,
        }
    )


def _pinned_scan_provenance(report: dict) -> ExternalProvenance:
    return ExternalProvenance(
        canonical_source_repository=FIRST_PARTY_SOURCE_REPOSITORY,
        source_provider="aiat",
        exact_release=FIRST_PARTY_WORKER_VERSION,
        commit_sha=FIRST_PARTY_SOURCE_REVISION,
        oci_image_digest=FIRST_PARTY_IMAGE_DIGEST,
        transport_type="oci",
        runtime_fingerprint=f"sha256:{FIRST_PARTY_RUNTIME_SHA256}",
        security_scan_status="passed",
        source_bound_security_scan=report,
    )


def _valid_first_party_registry_worker() -> dict:
    return {
        "id": WORKER_ID,
        "name": FIRST_PARTY_WORKER_ID,
        "version": FIRST_PARTY_WORKER_VERSION,
        "version_pin": f"{FIRST_PARTY_WORKER_VERSION}+{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}",
        "source_repo": FIRST_PARTY_SOURCE_REPOSITORY,
        "source_revision": FIRST_PARTY_SOURCE_REVISION,
        "adapter_type": "oci",
        "adapter_entrypoint": "ProcessAdapter",
        "adapter_module": None,
        "sandbox_profile": "sandboxed",
        "adapter_config": {
            **FIRST_PARTY_RUNTIME_ADAPTER_CONFIG,
            "governance_required": True,
            "governance_status": "compatibility",
            "migration_status": "native",
            "legacy_external_wrapper": False,
            "activation_blockers": [
                "missing immutable WorkerShell version",
                "missing certified runtime Adapter",
                "missing approved Skill Bundle",
                "missing capability snapshot",
            ],
            "steward_id": "00000000-0000-4000-8000-000000000099",
        },
        "model_mode": "none",
        "model_profile_id": None,
        "tool_grants": list(FIRST_PARTY_TOOL_GRANTS),
        "permission_grants": list(FIRST_PARTY_PERMISSION_GRANTS),
        "wrapper_config": {},
        "isolation_mode": "wrapper",
    }


def test_first_party_oci_process_adapter_is_not_a_legacy_process_wrapper() -> None:
    manifest = WorkerManifest.model_validate(
        {
            "metadata": {
                "id": FIRST_PARTY_WORKER_ID,
                "name": "AIAT Deterministic File Worker",
                "source_repo": FIRST_PARTY_SOURCE_REPOSITORY,
            },
            "runtime": {"transport": "oci"},
            "runtime_tier": "external",
            "integration": {
                "adapter_entrypoint": "ProcessAdapter",
                "isolation_mode": "wrapper",
            },
        }
    )

    assert manifest.is_legacy_external_wrapper is False


@pytest.mark.parametrize(
    ("transport", "adapter_entrypoint", "expected_legacy"),
    [
        ("process", "ProcessAdapter", True),
        ("http", "ProcessAdapter", True),
        ("oci", "WorkerAgent", True),
    ],
)
def test_non_oci_or_non_processadapter_external_wrapper_remains_legacy(
    transport: str,
    adapter_entrypoint: str,
    expected_legacy: bool,
) -> None:
    manifest = WorkerManifest.model_validate(
        {
            "metadata": {
                "id": "generic_external_worker",
                "name": "Generic External Worker",
                "source_repo": "https://example.test/worker",
            },
            "runtime": {"transport": transport},
            "runtime_tier": "external",
            "integration": {
                "adapter_entrypoint": adapter_entrypoint,
                "isolation_mode": "wrapper",
            },
        }
    )

    assert manifest.is_legacy_external_wrapper is expected_legacy


def test_source_bound_identity_requires_nonlegacy_oci_classification() -> None:
    report = _valid_source_scan_report()
    worker = _valid_first_party_registry_worker()

    assert source_bound_worker_identity_is_valid(
        worker,
        report,
        expected_worker_id=WORKER_ID,
    )
    worker["adapter_config"]["legacy_external_wrapper"] = True
    assert not source_bound_worker_identity_is_valid(
        worker,
        report,
        expected_worker_id=WORKER_ID,
    )


def test_source_bound_scan_is_required_and_rejects_report_tampering_and_identity_mismatch() -> None:
    report = _valid_source_scan_report()
    provenance = _pinned_scan_provenance(report)
    assert source_bound_security_scan_result_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
    ) is True
    assert source_bound_security_scan_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
    ) is True
    assert source_bound_security_scan_result_is_valid(
        report,
        expected_worker_id="00000000-0000-4000-8000-000000000999",
        provenance=provenance,
    ) is False
    assert source_bound_security_scan_result_is_valid(report, provenance=provenance) is False

    tampered = {**report, "finding_count": 1}
    assert not source_bound_security_scan_report_integrity_is_valid(
        tampered,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
    )

    forged_identity = dict(report)
    forged_identity["candidate"] = {**report["candidate"], "source_revision": "f" * 40}
    forged_identity = finalize_source_bound_scan_report(forged_identity)
    assert not source_bound_security_scan_result_is_valid(
        forged_identity,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
    )

    altered_invocation = dict(report)
    altered_invocation["scanners"] = [dict(row) for row in report["scanners"]]
    altered_invocation["scanners"][0]["argv"][-1] = "/tmp/untrusted"
    altered_invocation = finalize_source_bound_scan_report(altered_invocation)
    assert not source_bound_security_scan_result_is_valid(
        altered_invocation,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
    )


def test_source_scan_pass_requires_pinned_image_source_build_binding() -> None:
    report = _valid_source_scan_report()
    provenance = _pinned_scan_provenance(report)
    current_worker = _valid_first_party_registry_worker()
    assert source_bound_security_scan_result_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
        current_worker=current_worker,
    )
    assert source_bound_security_scan_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
        current_worker=current_worker,
    )
    assert operational_promotion_checks(
        provenance,
        worker_id=WORKER_ID,
        current_worker=current_worker,
    )["security"] is True

    inputs = _readiness_snapshot(provenance)
    inputs["worker"] = current_worker
    readiness = evaluate_worker_steward_readiness(**inputs)
    codes = {item["code"] for item in readiness["blockers"]}
    assert "security_scan_not_passed" not in codes
    assert "image_source_binding_not_verified" not in codes
    assert readiness["checks"]["source_bound_security_scan"]["status"] == "passed"
    assert readiness["checks"]["oci_image_source_binding"]["status"] == "verified_local_unsigned"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_sha256", "0" * 64),
        ("oci_index_digest", "sha256:" + "0" * 64),
        ("builder_authenticated", True),
        ("signed", True),
    ],
)
def test_source_scan_rejects_modified_build_binding_summary(field: str, value: object) -> None:
    report = _valid_source_scan_report()
    report["image_build_provenance"] = {
        **report["image_build_provenance"],
        field: value,
    }
    report = finalize_source_bound_scan_report(report)
    assert not source_bound_security_scan_report_integrity_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        provenance=_pinned_scan_provenance(report),
    )


def test_passed_source_scan_rejects_trufflehog_findings_exit_without_findings() -> None:
    report = _valid_source_scan_report()
    trufflehog = next(row for row in report["scanners"] if row["name"] == "trufflehog")
    trufflehog["exit_code"] = 183
    report = finalize_source_bound_scan_report(report)
    provenance = _pinned_scan_provenance(report)

    # Persistence uses the integrity validator, so a self-hashed but
    # contradictory PASS cannot be retained as passing certification.
    assert not source_bound_security_scan_report_integrity_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
    )
    assert not source_bound_security_scan_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("semgrep_config_path", "/tmp/unbound/semgrep-default.yml"),
        ("semgrep_config_source_path", "/tmp/untrusted/semgrep-default.yml"),
        ("semgrep_config_sha256", "0" * 64),
        ("semgrep_config_snapshot_mode", "0666"),
    ],
)
def test_source_scan_report_rejects_unbound_semgrep_config_snapshot(
    field: str,
    value: str,
) -> None:
    report = _valid_source_scan_report()
    report[field] = value
    tampered = finalize_source_bound_scan_report(report)
    assert not source_bound_security_scan_report_integrity_is_valid(
        tampered,
        expected_worker_id=WORKER_ID,
        provenance=_pinned_scan_provenance(tampered),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version_pin", "mutable:dev"),
        ("adapter_type", "process"),
        ("adapter_entrypoint", "OtherAdapter"),
        ("source_revision", "f" * 40),
        ("source_repo", "https://example.test/worker"),
        ("sandbox_profile", "trusted"),
        ("adapter_module", "untrusted.module"),
        ("isolation_mode", "native"),
        ("wrapper_config", {"command": "untrusted"}),
        ("model_mode", "aiat_gateway"),
        ("model_profile_id", "ungoverned-profile"),
        ("tool_grants", ["shell_exec"]),
        ("permission_grants", ["project.workspace.admin"]),
    ],
)
def test_scan_report_stops_matching_a_mutated_current_worker_identity(
    field: str,
    value: str,
) -> None:
    report = _valid_source_scan_report()
    worker = _valid_first_party_registry_worker()
    assert source_bound_worker_identity_is_valid(
        worker,
        report,
        expected_worker_id=WORKER_ID,
    )
    assert source_bound_security_scan_result_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        current_worker=worker,
    )
    mutated = {**worker, field: value}
    assert not source_bound_worker_identity_is_valid(
        mutated,
        report,
        expected_worker_id=WORKER_ID,
    )
    assert not source_bound_security_scan_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        current_worker=mutated,
    )

    mutated_config = dict(FIRST_PARTY_RUNTIME_ADAPTER_CONFIG)
    mutated_config["image"] = f"untrusted@{FIRST_PARTY_IMAGE_DIGEST}"
    mutated_image = {**worker, "adapter_config": mutated_config}
    assert not source_bound_worker_identity_is_valid(
        mutated_image,
        report,
        expected_worker_id=WORKER_ID,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sandbox_profile", "runc"),
        ("sandbox_runtime", "runc"),
        ("bidirectional_tool_protocol", False),
        ("tool_response_timeout_seconds", 300),
        ("runtime_version", "aiat-deterministic-file-worker/latest"),
        ("extra_runtime_option", True),
    ],
)
def test_scan_report_rejects_mutated_immutable_adapter_configuration(
    field: str,
    value: object,
) -> None:
    report = _valid_source_scan_report()
    worker = _valid_first_party_registry_worker()
    worker_config = dict(worker["adapter_config"])
    worker_config[field] = value
    mutated = {**worker, "adapter_config": worker_config}

    assert not source_bound_worker_identity_is_valid(
        mutated,
        report,
        expected_worker_id=WORKER_ID,
    )
    assert not source_bound_security_scan_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        current_worker=mutated,
    )


@pytest.mark.parametrize(
    "field",
    [
        "sandbox_runtime",
        "bidirectional_tool_protocol",
        "tool_response_timeout_seconds",
        "runtime_version",
        "execution_policy",
    ],
)
def test_scan_report_rejects_incomplete_immutable_adapter_configuration(field: str) -> None:
    report = _valid_source_scan_report()
    worker = _valid_first_party_registry_worker()
    config = dict(worker["adapter_config"])
    del config[field]
    mutated = {**worker, "adapter_config": config}

    assert not source_bound_worker_identity_is_valid(
        mutated,
        report,
        expected_worker_id=WORKER_ID,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("tool_grants", None), ("permission_grants", None)],
)
def test_scan_report_rejects_empty_present_worker_grants(field: str, value: object) -> None:
    report = _valid_source_scan_report()
    worker = _valid_first_party_registry_worker()
    mutated = {**worker, field: value}

    assert not source_bound_worker_identity_is_valid(
        mutated,
        report,
        expected_worker_id=WORKER_ID,
    )


def test_findings_report_integrity_does_not_satisfy_security_promotion() -> None:
    report = _valid_source_scan_report()
    report["scanners"] = [dict(row) for row in report["scanners"]]
    finding = {
        "finding_id": "a" * 64,
        "rule_id": "python.lang.security.audit.exec-detected",
        "path": "mas/workers/aiat_deterministic_file_worker_process.py",
        "line": 10,
        "severity": "WARNING",
    }
    report["scanners"][0].update(
        status="findings_review_required",
        finding_count=1,
        findings=[finding],
    )
    report.update(
        status="findings_review_required",
        finding_review_status="requires_human_review",
        finding_count=1,
    )
    report = finalize_source_bound_scan_report(report)
    provenance = _pinned_scan_provenance(report)
    assert source_bound_security_scan_report_integrity_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
    )
    assert not source_bound_security_scan_is_valid(
        report,
        expected_worker_id=WORKER_ID,
        provenance=provenance,
    )

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

    build_bound_candidate = ExternalWorkerSteward(
        worker_id=WORKER_ID,
        provenance=_pinned_scan_provenance(_valid_source_scan_report()),
    )
    build_bound_candidate.transition(StewardStatus.READY, actor="test-fixture")
    candidate = build_bound_candidate.generate_candidate(
        semantic_version="0.1.0",
        adapter_version="0.1.0",
        upstream_compatibility_range="==0.1.0",
        implementation_ref="mas_core.worker_registry.deterministic_file_worker:generate_project_file",
    )
    _advance_to_certifying(build_bound_candidate, candidate.candidate_id)
    certification = build_bound_candidate.certify_candidate(
        candidate.candidate_id,
        conformance={"passed": True},
        checks={},
    )
    assert certification.passed is True
    assert certification.checks["security"] is True
    assert candidate.intake_status == CandidateIntakeStatus.CERTIFYING
    assert candidate.bundle.status == BundleStatus.CERTIFIED
    assert candidate.approval_record_id is None
    with pytest.raises(StewardTransitionError, match="only approved candidates"):
        build_bound_candidate.start_rollout(
            candidate.candidate_id,
            actor="test-fixture",
            eligible_task_classes=["read_only"],
        )
