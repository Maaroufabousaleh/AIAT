"""Evaluate one selected external-worker steward before certification.

The evaluator is intentionally read-model only.  It does not generate a
candidate, run adapter conformance, approve a candidate, activate a worker,
or start a rollout.  It turns persisted steward/candidate evidence into a
small, secret-safe list of technical blockers that an operator can review
before invoking the separate certification workflow.

Licence and resource-restriction fields are retained as provenance metadata
and are deliberately absent from the operational predicate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from mas_core.worker_registry.source_bound_security_scan import (
    source_bound_security_scan_is_valid,
    source_bound_security_scan_required,
    source_bound_security_scan_result_is_valid,
)

WORKER_STEWARD_READINESS_SCHEMA = "aiat.worker-steward-readiness.v1"
READY_STEWARD_STATUS = "READY"
CERTIFIABLE_STEWARD_STATUSES = frozenset({"PROVISIONING", READY_STEWARD_STATUS})
CERTIFIABLE_CANDIDATE_STAGES = frozenset({"CERTIFYING", "APPROVED"})
TERMINAL_CANDIDATE_STAGES = frozenset({"REJECTED", "BLOCKED"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _blocker(code: str, reason: str) -> dict[str, str]:
    return {"code": code, "reason": reason}


def _candidate_identifier(candidate: Mapping[str, Any] | None) -> str:
    if candidate is None:
        return ""
    return _text(candidate.get("candidate_id") or candidate.get("id"))


def _nested_mapping(value: Any, key: str) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    nested = value.get(key)
    return nested if isinstance(nested, Mapping) else None


def _candidate_evidence(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    value = candidate.get("evidence") or candidate.get("evidence_json")
    return value if isinstance(value, Mapping) else {}


def _certification_from_candidate(candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    evidence = _candidate_evidence(candidate)
    value = evidence.get("certification")
    if isinstance(value, Mapping):
        return value
    return None


def _passed_certification(
    candidate: Mapping[str, Any], certification: Mapping[str, Any] | None,
) -> bool:
    source = certification or _certification_from_candidate(candidate)
    return bool(source and source.get("passed") is True)


def _passed_matrix(matrices: Iterable[Any], candidate: Mapping[str, Any]) -> bool | None:
    """Return matching matrix status when a caller supplied matrix rows.

    Compatibility matrices are created by the certification workflow and are
    not exposed by the current read-only steward endpoint.  ``None`` therefore
    means not checked rather than a false certification claim.
    """

    candidate_adapter = _nested_mapping(candidate.get("adapter"), "source_provenance")
    candidate_version = _text(candidate.get("adapter", {}).get("version")) if isinstance(candidate.get("adapter"), Mapping) else ""
    for row in matrices:
        if not isinstance(row, Mapping):
            continue
        matrix_adapter = _text(row.get("adapter_version"))
        matrix_source = _nested_mapping(row.get("provenance"), "source_provenance")
        if matrix_adapter and candidate_version and matrix_adapter != candidate_version:
            continue
        if candidate_adapter and matrix_source and candidate_adapter.get("canonical_source_repository") != matrix_source.get("canonical_source_repository"):
            continue
        return bool(row.get("passed") is True)
    return None


def evaluate_worker_steward_readiness(
    *,
    worker: Mapping[str, Any] | None,
    steward: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
    worker_id: str,
    candidate_id: str,
    certification: Mapping[str, Any] | None = None,
    compatibility_matrices: Iterable[Any] = (),
    fetch_errors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return certification-readiness for one explicitly selected worker/candidate.

    The candidate identifier is required even when a steward has many
    candidates: this function never chooses the newest or only candidate for
    an operator.  A pass means the selected candidate is technically ready for
    the separate server-side conformance/certification operation.  It does not
    mean the candidate is certified, approved, active, or safe to dispatch.
    """

    selected_worker_id = _text(worker_id)
    selected_candidate_id = _text(candidate_id)
    blockers: list[dict[str, str]] = []
    fetch_status = {str(key): "blocked" for key in (fetch_errors or {})}
    if fetch_errors:
        for key, reason in sorted(fetch_errors.items()):
            blockers.append(_blocker(f"read_{key}_unavailable", reason))

    worker_seen = worker is not None
    steward_seen = steward is not None
    candidate_seen = candidate is not None
    source_scan_status = "not_checked"
    image_source_binding_status = "not_required"
    if worker is None:
        blockers.append(_blocker("worker_not_found", "the selected worker was not returned by the control plane"))
    else:
        fetch_status.setdefault("worker", "observed")
        returned_worker_id = _text(worker.get("id"))
        if returned_worker_id and returned_worker_id != selected_worker_id:
            blockers.append(_blocker("worker_selection_mismatch", "the returned worker ID differs from the selected worker ID"))
        if not _text(worker.get("source_repo")) or _text(worker.get("source_repo")).lower() == "local":
            blockers.append(_blocker("worker_external_source_missing", "steward certification readiness requires an external source repository"))
        if not _text(worker.get("version_pin")):
            blockers.append(_blocker("worker_version_pin_missing", "the worker has no immutable source/runtime version pin"))

    if steward is None:
        blockers.append(_blocker("steward_not_found", "the selected worker has no dedicated steward read model"))
    else:
        fetch_status.setdefault("steward", "observed")
        returned_worker_id = _text(steward.get("worker_id"))
        if returned_worker_id and returned_worker_id != selected_worker_id:
            blockers.append(_blocker("steward_worker_mismatch", "the steward belongs to a different worker"))
        steward_status = _text(steward.get("status")).upper()
        if steward_status not in CERTIFIABLE_STEWARD_STATUSES:
            blockers.append(
                _blocker(
                    "steward_state_blocks_certification",
                    "the dedicated steward is suspended, retired, degraded, or otherwise unavailable for certification",
                )
            )
        provenance = _nested_mapping(steward, "provenance")
        if provenance is None:
            blockers.append(_blocker("provenance_missing", "the steward has no external provenance read model"))
        else:
            pinned = any(
                _text(provenance.get(field))
                for field in ("exact_release", "commit_sha", "package_version", "oci_image_digest")
            )
            if not pinned:
                blockers.append(_blocker("provenance_pin_missing", "external provenance has no exact release, commit, package, or OCI digest"))
            scan_report = provenance.get("source_bound_security_scan")
            scan_status = _text(provenance.get("security_scan_status")).lower()
            report_status = (
                scan_report.get("status")
                if isinstance(scan_report, Mapping)
                else None
            )
            source_scan_status = _text(
                provenance.get("source_bound_security_scan_status")
                or report_status
            ).lower() or "missing"
            scan_required = source_bound_security_scan_required(
                provenance,
                worker=worker,
            )
            source_scan_result_valid = scan_required and source_bound_security_scan_result_is_valid(
                scan_report,
                expected_worker_id=selected_worker_id,
                provenance=provenance,
                current_worker=worker,
            )
            image_source_binding_status = "not_verified" if scan_required else "not_required"
            if scan_status != "passed" or (
                scan_required
                and not source_bound_security_scan_is_valid(
                    scan_report,
                    expected_worker_id=selected_worker_id,
                    provenance=provenance,
                    current_worker=worker,
                )
            ):
                blockers.append(
                    _blocker(
                        "security_scan_not_passed",
                        (
                            "the source scan passed, but independent OCI image/source binding is required before certification"
                            if source_scan_result_valid
                            else "a server-generated source-bound scan report with zero findings and scanner errors is required before certification"
                        ),
                    )
                )
            if scan_required and source_scan_result_valid:
                blockers.append(
                    _blocker(
                        "image_source_binding_not_verified",
                        "the clean source scan does not independently verify that the pinned OCI image was built from that source",
                    )
                )

    if not selected_candidate_id:
        blockers.append(_blocker("candidate_selection_required", "a candidate ID must be explicitly selected; no candidate is auto-selected"))
    if candidate is None:
        blockers.append(_blocker("candidate_not_found", "the selected candidate was not returned by the control plane"))
    else:
        fetch_status.setdefault("candidate", "observed")
        returned_candidate_id = _candidate_identifier(candidate)
        if returned_candidate_id and returned_candidate_id != selected_candidate_id:
            blockers.append(_blocker("candidate_selection_mismatch", "the returned candidate ID differs from the selected candidate ID"))
        stage = _text(candidate.get("intake_status")).upper()
        if stage not in CERTIFIABLE_CANDIDATE_STAGES:
            reason = "the candidate is terminal and cannot enter certification" if stage in TERMINAL_CANDIDATE_STAGES else "the candidate must be in CERTIFYING or APPROVED stage"
            blockers.append(_blocker("candidate_stage_not_certifiable", reason))

        bundle = _nested_mapping(candidate, "bundle")
        adapter = _nested_mapping(candidate, "adapter")
        if bundle is None:
            blockers.append(_blocker("skill_bundle_missing", "the candidate has no immutable skill bundle"))
        else:
            # The current steward endpoint exposes the immutable bundle UUID
            # but does not project the durable content hash.  Accept the UUID
            # as the binding here and report hash presence for future richer
            # read models instead of inventing a false blocker.
            if not _text(bundle.get("bundle_id") or bundle.get("id")):
                blockers.append(_blocker("skill_bundle_id_missing", "the candidate skill bundle has no immutable ID"))
            documentation_refs = bundle.get("documentation_refs")
            if not isinstance(documentation_refs, (list, tuple)) or not documentation_refs:
                blockers.append(_blocker("documentation_snapshot_missing", "the candidate has no steward documentation snapshot references"))
            if not isinstance(bundle.get("verified_capabilities"), Mapping):
                blockers.append(_blocker("capability_snapshot_missing", "the candidate has no verified capability snapshot"))
        if adapter is None:
            blockers.append(_blocker("runtime_adapter_missing", "the candidate has no immutable runtime adapter"))
        else:
            if not _text(adapter.get("content_hash")):
                blockers.append(_blocker("runtime_adapter_hash_missing", "the candidate runtime adapter has no content hash"))
            if not _text(adapter.get("version")):
                blockers.append(_blocker("runtime_adapter_version_missing", "the candidate runtime adapter has no version"))

        if stage == "APPROVED" and not _passed_certification(candidate, certification):
            blockers.append(_blocker("candidate_certification_missing", "an APPROVED candidate must retain a passed certification record"))

    matrix_status = _passed_matrix(compatibility_matrices, candidate or {}) if candidate is not None else None
    if matrix_status is False:
        blockers.append(_blocker("compatibility_matrix_failed", "the supplied compatibility matrix for this candidate did not pass"))

    steward_status = _text(steward.get("status")).upper() if steward else "missing"
    candidate_stage = _text(candidate.get("intake_status")).upper() if candidate else "missing"
    report = {
        "schema_version": WORKER_STEWARD_READINESS_SCHEMA,
        "status": "pass" if not blockers else "blocked",
        "licence_metadata_is_gate": False,
        "selected": {
            "worker_id": selected_worker_id,
            "candidate_id": selected_candidate_id,
        },
        "checks": {
            "worker": {
                "present": worker_seen,
                "source": _text(worker.get("source_repo")) if worker else "missing",
                "version_pin": bool(_text(worker.get("version_pin"))) if worker else False,
            },
            "steward": {
                "present": steward_seen,
                "status": steward_status,
                "ready": steward_status == READY_STEWARD_STATUS,
                "certifiable": steward_status in CERTIFIABLE_STEWARD_STATUSES,
                "security_scan": _text((_nested_mapping(steward, "provenance") or {}).get("security_scan_status")) if steward else "missing",
            },
            "source_bound_security_scan": {
                "status": source_scan_status,
            },
            "oci_image_source_binding": {
                "status": image_source_binding_status,
            },
            "candidate": {
                "present": candidate_seen,
                "stage": candidate_stage,
                "certifiable_stage": candidate_stage in CERTIFIABLE_CANDIDATE_STAGES,
                "certification_passed": _passed_certification(candidate, certification) if candidate else False,
                "skill_bundle_hash_present": bool(
                    candidate
                    and _text((_nested_mapping(candidate, "bundle") or {}).get("content_hash"))
                ),
            },
            "compatibility_matrix": {
                "status": "passed" if matrix_status is True else "failed" if matrix_status is False else "not_checked",
            },
            "activation": {
                "status": "not_checked",
                "active_pointers_unchanged": True,
            },
        },
        "fetch_status": fetch_status,
        "blockers": blockers,
        "scope": "read-only selected external-worker steward certification readiness; no candidate generation, conformance run, approval, activation, rollout, identity, or provider mutation",
    }
    return report


__all__ = [
    "CERTIFIABLE_CANDIDATE_STAGES",
    "TERMINAL_CANDIDATE_STAGES",
    "WORKER_STEWARD_READINESS_SCHEMA",
    "evaluate_worker_steward_readiness",
]
