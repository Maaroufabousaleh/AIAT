"""Validate the neutral, not-yet-run OpenHands/OpenCode comparison record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA = "aiat.external-worker-comparison.v1"
OPENHANDS_COMMIT = "4c1237f391fe394e9f67505fe3a0bd2d81f84188"
OPENHANDS_IMAGE_DIGEST = "sha256:36f847d1dfbbbdce90052437b06a3c6e76b8a54683228182eaf73085f03fcd97"
OPENCODE_COMMIT = "826d9ad46a22bef0294998e08daa3c4904fea28f"
OPENCODE_IMAGE_DIGEST = "sha256:56c82ee8b5ead35406a83102ad1960030b7ab58dcd591e3ab5f44c2b5e0170cb"

METRICS = (
    "task_success",
    "tests_passed",
    "correctness",
    "diff_size",
    "diff_quality",
    "wall_time_ms",
    "model_tokens",
    "approximate_model_cost",
    "tool_calls",
    "pause",
    "interrupt",
    "recovery",
    "timeout",
    "security_gate",
    "integration_complexity",
    "cleanup",
)


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA:
        errors.append("schema_version_mismatch")
    if payload.get("status") != "NOT_RUN":
        errors.append("comparison_must_remain_not_run")
    if payload.get("decision") is not None:
        errors.append("comparison_decision_must_be_empty")
    candidates = payload.get("candidates")
    if not isinstance(candidates, dict):
        errors.append("candidates_missing")
        candidates = {}
    expected = {
        "openhands": (OPENHANDS_COMMIT, OPENHANDS_IMAGE_DIGEST),
        "opencode": (OPENCODE_COMMIT, OPENCODE_IMAGE_DIGEST),
    }
    for name, (commit, digest) in expected.items():
        candidate = candidates.get(name)
        if not isinstance(candidate, dict):
            errors.append(f"candidate_missing:{name}")
            continue
        if candidate.get("source_commit") != commit:
            errors.append(f"candidate_commit_mismatch:{name}")
        if candidate.get("image_digest") != digest:
            errors.append(f"candidate_image_digest_mismatch:{name}")
        if candidate.get("worker_status") != ("INACTIVE" if name == "openhands" else "CURRENT_DEFAULT_UNCHANGED"):
            errors.append(f"candidate_status_mismatch:{name}")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics_missing")
        metrics = {}
    for metric in METRICS:
        row = metrics.get(metric)
        if not isinstance(row, dict) or row.get("status") != "NOT_RUN" or row.get("value") is not None:
            errors.append(f"metric_not_run_or_non_scalar:{metric}")
    if payload.get("payloads_retained") is not False:
        errors.append("payload_retention_policy_missing")
    return {
        "schema_version": "aiat.external-worker-comparison-validation.v1",
        "status": "PASS" if not errors else "FAILED_CERTIFICATION_IMPLEMENTATION",
        "errors": errors,
        "comparison_status": payload.get("status"),
        "decision": payload.get("decision"),
        "payloads_retained": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.record.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    report = validate(payload if isinstance(payload, dict) else {})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "errors": report["errors"]}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
