"""Validate the sanitized OpenHands certification evidence tree.

The workflow intentionally uploads evidence even when a prerequisite is
missing.  This checker validates that the retained JSON is parseable, the
mandatory gate evaluation has the complete gate set, statuses remain narrow,
and no report asserts that payloads, credentials, raw responses, or logs were
retained.  It never reads or prints secret values and it does not turn a
blocked certification into a pass.
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

SCHEMA = "aiat.openhands-certification-evidence-schema.v1"
GATE_SCHEMA = "aiat.openhands-certification-gate-evaluation.v1"
_SHA_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
_ALLOWED_STATUSES = {
    "PASSED",
    "BLOCKED_OPERATOR_CONFIGURATION",
    # Certification authorization is deliberately distinct from activation
    # approval.  A trusted certification controller may be unable to obtain a
    # run-scoped authorization even though the candidate remains inactive; the
    # evidence must preserve that narrow state instead of collapsing it into a
    # generic incomplete-gates result.
    "BLOCKED_CERTIFICATION_AUTHORIZATION",
    "BLOCKED_MISSING_OPERATOR_SECRET",
    "BLOCKED_SECURITY_TRIAGE",
    "BLOCKED_SCANNER_COVERAGE",
    "BLOCKED_RUNTIME_STARTUP",
    "BLOCKED_GATEWAY_PROVENANCE",
    "BLOCKED_GVISOR",
    "BLOCKED_MODEL_GATEWAY",
    "BLOCKED_PROVIDER",
    "BLOCKED_TOOL_BRIDGE",
    "BLOCKED_LIFECYCLE",
    "BLOCKED_OPENHANDS_LIVE_EXECUTION_CONTRACT",
    "BLOCKED_WORKSPACE_ISOLATION",
    "BLOCKED_SECRET_NON_DISCLOSURE",
    "BLOCKED_CLEANUP",
    "BLOCKED_INCOMPLETE_MANDATORY_GATES",
    "FAILED_INFRASTRUCTURE",
    "FAILED_CERTIFICATION_IMPLEMENTATION",
    "FAILED_MODEL_EXECUTION",
}
_RETENTION_KEYS = frozenset(
    {
        "payload_retained",
        "payloads_retained",
        "raw_payload_retained",
        "raw_payloads_retained",
        "response_payload_retained",
        "response_payloads_retained",
        "provider_payloads_retained",
        "credentials_or_payloads_retained",
        "credentials_retained",
        "credential_retained",
        "provider_credential_retained",
        "credential_values_retained",
        "gateway_key_retained",
        "management_key_retained",
        "grant_value_retained",
        "raw_response_retained",
        "raw_responses_retained",
        "raw_logs_retained",
        "container_logs_retained",
        "model_payloads_retained",
        "raw_model_payloads_retained",
        "unnecessary_logs_retained",
        "raw_values_retained",
        "secret_value_retained",
        "secrets_retained",
        "source_or_payload_retained",
    }
)


def _load(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, type(exc).__name__


def _load_json_lines(path: Path) -> tuple[Any, str | None]:
    """Load a deliberately retained JSON Lines scanner output.

    TruffleHog's ``--json`` contract is one JSON finding per line rather than
    one JSON document.  Treating that output as a single JSON document makes
    a valid scanner result look like corrupted evidence.  Keep the format
    explicit and fail closed if any individual line is malformed; do not
    include line contents in the diagnostic.
    """

    try:
        rows: list[Any] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                return None, f"JSONLineDecodeError:{line_number}"
        return rows, None
    except (OSError, UnicodeDecodeError) as exc:
        return None, type(exc).__name__


def _sensitive_true_flags(value: Any, *, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text.lower() in _RETENTION_KEYS and child is True:
                findings.append(child_path)
            findings.extend(_sensitive_true_flags(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_sensitive_true_flags(child, path=f"{path}[{index}]"))
    return findings


def _validate_gate_evaluation(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["gate_evaluation_root_not_object"]
    if value.get("schema_version") != GATE_SCHEMA:
        errors.append("gate_evaluation_schema_mismatch")
    status = str(value.get("status") or "")
    # Gate rows retain precise statuses such as FAILED_FILE_MODIFICATIONS and
    # FAILED_TEST_EXECUTION.  The gate evaluator deliberately accepts the
    # same explicit BLOCKED_/FAILED_ families; rejecting them here would turn
    # valid fail-closed evidence into a secondary schema failure.
    if status not in _ALLOWED_STATUSES and not status.startswith(("BLOCKED_", "FAILED_")):
        errors.append("gate_evaluation_status_unknown")
    candidate_sha = str(value.get("candidate_sha") or "")
    if not _SHA_RE.fullmatch(candidate_sha):
        errors.append("gate_evaluation_candidate_sha_invalid")
    gates = value.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(GATE_IDS):
        errors.append("gate_evaluation_gate_set_incomplete")
    elif any(not isinstance(gates[gate_id], dict) for gate_id in GATE_IDS):
        errors.append("gate_evaluation_gate_row_invalid")
    elif status == "PASSED" and any(gates[gate_id].get("status") != "PASS" for gate_id in GATE_IDS):
        errors.append("passed_status_contains_non_pass_gate")
    evaluation = value.get("evaluation")
    if not isinstance(evaluation, dict):
        errors.append("gate_evaluation_summary_missing")
    else:
        if evaluation.get("mandatory_gate_count") != len(GATE_IDS):
            errors.append("gate_evaluation_gate_count_mismatch")
        if status == "PASSED" and evaluation.get("all_required_gates_passed") is not True:
            errors.append("passed_status_without_all_gates_passed")
        if status != "PASSED" and evaluation.get("all_required_gates_passed") is True:
            errors.append("blocked_status_claims_all_gates_passed")
    return errors


def validate(root: Path) -> dict[str, Any]:
    """Return a scalar-only validation report for one workflow evidence tree."""

    errors: list[str] = []
    if not root.is_dir():
        errors.append("evidence_root_missing")
        return {
            "schema_version": SCHEMA,
            "status": "FAILED_CERTIFICATION_IMPLEMENTATION",
            "errors": errors,
            "json_file_count": 0,
            "payloads_retained": False,
        }

    json_files = sorted(root.rglob("*.json"))
    if not json_files:
        errors.append("evidence_json_missing")
    gate_path = root / "certification" / "gate-evaluation.json"
    gate_evaluation: Any = None
    for path in json_files:
        relative = path.relative_to(root).as_posix()
        loader = _load_json_lines if relative == "certification/trufflehog.json" else _load
        value, parse_error = loader(path)
        if parse_error:
            error_kind = "jsonl_invalid" if loader is _load_json_lines else "json_invalid"
            errors.append(f"{error_kind}:{relative}:{parse_error}")
            continue
        flags = _sensitive_true_flags(value)
        errors.extend(f"sensitive_retention_flag:{relative}:{flag}" for flag in flags)
        if path == gate_path:
            gate_evaluation = value
    if gate_evaluation is None:
        errors.append("gate_evaluation_missing")
    else:
        errors.extend(_validate_gate_evaluation(gate_evaluation))
    final_status = (
        str(gate_evaluation.get("status") or "UNKNOWN")
        if isinstance(gate_evaluation, dict)
        else "UNKNOWN"
    )
    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not errors else "FAILED_CERTIFICATION_IMPLEMENTATION",
        "errors": sorted(set(errors)),
        "json_file_count": len(json_files),
        "final_certification_status": final_status,
        "payloads_retained": False,
        "raw_values_retained": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = validate(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "errors": report["errors"]}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
