"""Validate a completed OpenHands gate artifact before steward registration.

This command is intentionally read-only.  It does not mutate a candidate,
create an approval record, activate a worker, or call the steward API.  It
turns one exact GitHub gate-evaluation artifact into a narrow registration
preflight so an operator cannot accidentally submit evidence for another
candidate, image, or source revision.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

try:
    from openhands_certification_gates import GATE_IDS
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_certification_gates import GATE_IDS  # type: ignore


SCHEMA = "aiat.openhands-steward-registration-preflight.v1"
DEFAULT_SOURCE_COMMIT = "4c1237f391fe394e9f67505fe3a0bd2d81f84188"
DEFAULT_IMAGE_DIGEST = "sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97"
WORKER_ID = "coding-worker-openhands-candidate"


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _valid_sha(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{40}", str(value or ""), flags=re.IGNORECASE))


def _valid_digest(value: Any) -> bool:
    return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", str(value or ""), flags=re.IGNORECASE))


def validate(
    evidence: dict[str, Any],
    *,
    candidate_sha: str,
    source_commit: str = DEFAULT_SOURCE_COMMIT,
    image_digest: str = DEFAULT_IMAGE_DIGEST,
    candidate_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a sanitized, non-mutating steward-registration decision."""

    blockers: list[str] = []
    candidate_sha = str(candidate_sha or "").strip()
    source_commit = str(source_commit or "").strip()
    image_digest = str(image_digest or "").strip()
    if not _valid_sha(candidate_sha):
        blockers.append("candidate_sha_invalid")
    if not _valid_sha(source_commit):
        blockers.append("source_commit_invalid")
    if not _valid_digest(image_digest):
        blockers.append("image_digest_invalid")

    if evidence.get("schema_version") != "aiat.openhands-certification-gate-evaluation.v1":
        blockers.append("gate_evidence_schema_mismatch")
    if evidence.get("status") != "PASSED":
        blockers.append(f"gate_evidence_status:{evidence.get('status') or 'missing'}")
    if evidence.get("candidate_sha") != candidate_sha:
        blockers.append("candidate_sha_mismatch")
    if evidence.get("openhands_source_commit") != source_commit:
        blockers.append("source_commit_mismatch")
    if evidence.get("openhands_image_digest") != image_digest:
        blockers.append("image_digest_mismatch")
    if evidence.get("payloads_retained") is not False:
        blockers.append("payload_retention_not_false")

    evaluation = evidence.get("evaluation")
    if not isinstance(evaluation, dict):
        blockers.append("gate_evaluation_missing")
        evaluation = {}
    if evaluation.get("all_required_gates_passed") is not True:
        blockers.append("mandatory_gates_not_passed")
    if evaluation.get("mandatory_gate_count") != len(GATE_IDS):
        blockers.append("mandatory_gate_count_mismatch")
    if evaluation.get("passed_gate_count") != len(GATE_IDS):
        blockers.append("passed_gate_count_mismatch")
    if evaluation.get("not_run_gate_count") != 0:
        blockers.append("mandatory_gate_not_run")
    if evaluation.get("blocked_gate_count") != 0 or evaluation.get("failed_gate_count") != 0:
        blockers.append("mandatory_gate_blocked_or_failed")
    gates = evidence.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(GATE_IDS):
        blockers.append("gate_set_mismatch")
        gates = {}
    elif not all(isinstance(gates[gate_id], dict) for gate_id in GATE_IDS):
        blockers.append("gate_row_shape_invalid")
    else:
        non_pass = sorted(gate_id for gate_id in GATE_IDS if gates[gate_id].get("status") != "PASS")
        if non_pass:
            blockers.append("gate_status_not_pass:" + ",".join(non_pass))

    candidate_stage = "NOT_PROVIDED"
    candidate_id = None
    if candidate_record is not None:
        candidate_stage = str(candidate_record.get("intake_status") or "MISSING").upper()
        candidate_id = candidate_record.get("candidate_id") or candidate_record.get("id")
        if candidate_stage != "CERTIFYING":
            blockers.append("candidate_not_certifying")
        if str(candidate_record.get("worker_id") or "") != WORKER_ID:
            blockers.append("candidate_worker_identity_mismatch")

    ready = not blockers
    return {
        "schema_version": SCHEMA,
        "status": "READY_FOR_STEWARD_REGISTRATION" if ready else "BLOCKED_STEWARD_REGISTRATION",
        "registration_mutated": False,
        "candidate": {
            "worker_id": WORKER_ID,
            "candidate_id": str(candidate_id) if candidate_id else None,
            "candidate_sha": candidate_sha,
            "source_commit": source_commit,
            "image_digest": image_digest,
            "intake_status": candidate_stage,
        },
        "certification": {
            "gate_status": evidence.get("status") or "unknown",
            "mandatory_gate_count": len(GATE_IDS),
            "passed_gate_count": evaluation.get("passed_gate_count", 0),
            "payloads_retained": False,
        },
        "activation": {
            "worker_status": "INACTIVE",
            "candidate_transition": "CERTIFYING",
            "activation_approval_required": True,
            "rollout_allowed": False,
        },
        "blockers": blockers,
        "next_action": (
            "submit the exact artifact through the authenticated steward certification path; "
            "review and approve activation separately"
            if ready
            else "do not register or approve this artifact"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--source-commit", default=DEFAULT_SOURCE_COMMIT)
    parser.add_argument("--image-digest", default=DEFAULT_IMAGE_DIGEST)
    parser.add_argument("--candidate-record", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = validate(
        _load(args.evidence),
        candidate_sha=args.candidate_sha,
        source_commit=args.source_commit,
        image_digest=args.image_digest,
        candidate_record=_load(args.candidate_record) if args.candidate_record else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "blockers": report["blockers"]}, sort_keys=True))
    return 0 if report["status"] == "READY_FOR_STEWARD_REGISTRATION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
