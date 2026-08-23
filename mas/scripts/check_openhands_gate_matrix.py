"""Evaluate the OpenHands mandatory gate matrix fail-closed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from openhands_certification_gates import GATE_DEFINITIONS, evaluate_gate_map, initial_gate_map
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_certification_gates import (  # type: ignore
        GATE_DEFINITIONS,
        evaluate_gate_map,
        initial_gate_map,
    )

SCHEMA = "aiat.openhands-certification-gate-evaluation.v1"


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_gate_rows(path: Path | None) -> dict[str, dict[str, Any]]:
    gates = initial_gate_map()
    if path is None or not path.is_file():
        return gates
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = payload.get("gates") if isinstance(payload, dict) else payload
    if isinstance(source, list):
        source = {str(row.get("gate_id")): row for row in source if isinstance(row, dict) and row.get("gate_id")}
    if isinstance(source, dict):
        for gate_id, row in source.items():
            if gate_id in gates and isinstance(row, dict):
                gates[gate_id] = {**gates[gate_id], **row}
    return gates


def _set_gate(gates: dict[str, dict[str, Any]], gate_id: str, status: str, evidence: str) -> None:
    if gate_id not in gates or status == "NOT_RUN":
        return
    gates[gate_id]["status"] = status
    gates[gate_id]["evidence_refs"] = [evidence]


def _evidence_blocker_status(evidence_root: Path) -> str | None:
    """Map scalar run evidence to a narrow fail-closed blocker class."""

    def report(name: str) -> dict[str, Any]:
        return _json(evidence_root / name)

    for name in (
        "gateway/route-probe.json",
        "gateway/provider-provisioning.json",
        "runtime/runtime-provisioning.json",
        "live/live-certification.json",
    ):
        value = report(name)
        failure_class = str(value.get("failure_class") or "")
        if failure_class.startswith(("MODEL_GATEWAY", "OPENHANDS_TO_GATEWAY", "LITELLM_TO_OMNIROUTE")):
            return "BLOCKED_MODEL_GATEWAY"
        if failure_class in {
            "LITELLM_STARTUP_FAILURE",
            "LITELLM_HEALTH_FAILURE",
            "OMNIROUTE_STARTUP_FAILURE",
            "OMNIROUTE_HEALTH_FAILURE",
        }:
            return "BLOCKED_RUNTIME_STARTUP"
        if failure_class in {"OPENHANDS_AGENT_SERVER_STARTUP_FAILURE", "OPENHANDS_AGENT_SERVER_HEALTH_FAILURE"}:
            return "BLOCKED_GVISOR"
        if failure_class in {"TOOL_SERVICE_STARTUP_FAILURE", "TOOL_SERVICE_HEALTH_FAILURE"}:
            return "BLOCKED_TOOL_BRIDGE"
        if failure_class.startswith("PROVIDER_") or failure_class == "MISSING_PROVIDER_SECRET":
            return "BLOCKED_PROVIDER"
        if name.startswith("runtime/") and value.get("status") == "BLOCKED":
            return "BLOCKED_TOOL_BRIDGE"
        if name.startswith("live/") and value.get("status") == "BLOCKED":
            return "BLOCKED_LIFECYCLE"

    for name in ("gateway/litellm/health.json", "gateway/omniroute/health.json"):
        if report(name).get("health_status") == "BLOCKED":
            return "BLOCKED_RUNTIME_STARTUP"
    topology = report("gateway/network-topology.json")
    network_startup = report("gateway/network-startup.json")
    if network_startup.get("failure_class") == "FAILED_INFRASTRUCTURE" or network_startup.get("status") == "BLOCKED":
        return "FAILED_INFRASTRUCTURE"
    if topology.get("topology_status") == "BLOCKED":
        return "FAILED_INFRASTRUCTURE"
    runsc_gateway = report("gateway/runsc-to-litellm.json")
    if runsc_gateway.get("status") == "BLOCKED":
        return "BLOCKED_MODEL_GATEWAY"
    if report("startup/startup.json").get("health_status") == "BLOCKED":
        return "BLOCKED_GVISOR"
    if report("bridge/startup.json").get("health_status") == "BLOCKED":
        return "BLOCKED_TOOL_BRIDGE"

    # Runtime/harness failures take precedence over scan interpretation.  A
    # failed Agent Server or bridge must not be relabelled as a security
    # finding merely because the candidate report is incomplete.
    certification = report("certification/candidate-certification.json")
    failure_classes = {
        str(value)
        for value in certification.get("failure_classes", [])
        if value
    }
    scanner_errors = int(certification.get("scanner_errors") or 0)
    if (
        scanner_errors > 0
        or "SCANNER_COVERAGE_INCOMPLETE" in failure_classes
        or "SCANNER_EXECUTION_FAILURE" in failure_classes
    ):
        return "BLOCKED_SCANNER_COVERAGE"
    if (
        certification.get("status") == "findings_review_required"
        or int(certification.get("raw_findings_count") or 0) > 0
        or certification.get("security_findings_interpretable") is False
    ):
        return "BLOCKED_SECURITY_TRIAGE"
    cleanup = report("gateway/cleanup.json")
    if cleanup and cleanup.get("zero_residue") is False:
        return "BLOCKED_CLEANUP"
    return None


def derive_gate_rows(evidence_root: Path) -> dict[str, dict[str, Any]]:
    """Derive only explicitly evidenced gate statuses from a run directory."""

    gates = initial_gate_map()
    certification_path = evidence_root / "certification" / "candidate-certification.json"
    certification = _json(certification_path)
    source_sbom = certification.get("source_sbom") if isinstance(certification.get("source_sbom"), dict) else {}
    image_sbom = certification.get("image_sbom") if isinstance(certification.get("image_sbom"), dict) else {}
    if source_sbom.get("status") == "pass" and image_sbom.get("status") == "pass":
        _set_gate(gates, "sbom", "PASS", str(certification_path.relative_to(evidence_root)))
    boundary = certification.get("aiat_local_boundary") if isinstance(certification.get("aiat_local_boundary"), dict) else {}
    if boundary.get("status") == "pass":
        _set_gate(gates, "aiat_local_boundary", "PASS", str(certification_path.relative_to(evidence_root)))
    if certification.get("security_findings_interpretable") is True and not certification.get("scanner_errors"):
        _set_gate(gates, "security_scan_with_retained_evidence", "PASS", str(certification_path.relative_to(evidence_root)))

    startup_path = evidence_root / "startup" / "startup.json"
    startup = _json(startup_path)
    runtime_path = evidence_root / "startup" / "container-runtime.json"
    runtime = runtime_path.read_text(encoding="utf-8").strip() if runtime_path.is_file() else ""
    if startup.get("health_status") == "PASS" and runtime.strip('"') == "runsc":
        _set_gate(gates, "gvisor_execution", "PASS", str(startup_path.relative_to(evidence_root)))

    live_path = evidence_root / "live" / "live-certification.json"
    live = _json(live_path)
    live_gates = live.get("gates") if isinstance(live.get("gates"), dict) else {}
    mapping = {
        "coding_task": "real_coding_task",
        "file_modifications": "file_modifications",
        "test_execution": "test_execution",
        "artifact_capture": "artifact_capture",
        "isolated_workspace": "isolated_workspace",
        "pause": "graceful_pause",
        "interrupt": "immediate_interrupt",
        "resume": "resume",
        "forced_failure": "forced_failure",
        "crash_recovery": "recovery",
        "timeout": "timeout",
        "budget": "budget_enforcement",
        "forbidden_tool": "forbidden_tool_attempt",
        "workspace_isolation": "cross_workspace_isolation",
        "secret_isolation": "secret_non_disclosure",
        "zero_residue": "zero_residue_cleanup",
    }
    for source, gate_id in mapping.items():
        status = str(live_gates.get(source) or "NOT_RUN").upper()
        _set_gate(gates, gate_id, status, str(live_path.relative_to(evidence_root)))
    cleanup_path = evidence_root / "gateway" / "cleanup.json"
    cleanup = _json(cleanup_path)
    if cleanup.get("zero_residue") is True:
        _set_gate(gates, "zero_residue_cleanup", "PASS", str(cleanup_path.relative_to(evidence_root)))
    return gates


def evaluate(
    *,
    gate_status_path: Path | None = None,
    provider_status: str | None = None,
    configuration_status: str | None = None,
    candidate_sha: str | None = None,
    source_commit: str | None = None,
    image_digest: str | None = None,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    gates = _load_gate_rows(gate_status_path) if gate_status_path else derive_gate_rows(evidence_root) if evidence_root else initial_gate_map()
    effective_blocker = provider_status if provider_status and provider_status != "PASS" else None
    if effective_blocker is None and configuration_status and configuration_status not in {
        "PASS",
        "READY_FOR_CERTIFICATION_AUTHORIZATION",
    }:
        effective_blocker = configuration_status
    if effective_blocker is None and evidence_root is not None:
        effective_blocker = _evidence_blocker_status(evidence_root)
    result = evaluate_gate_map(gates, blocker_status=effective_blocker)
    return {
        "schema_version": SCHEMA,
        "status": result["status"],
        "candidate_sha": candidate_sha,
        "openhands_source_commit": source_commit,
        "openhands_image_digest": image_digest,
        "gates": gates,
        "evaluation": result,
        "provider_configuration_status": provider_status,
        "configuration_status": configuration_status,
        "evidence_blocker_status": effective_blocker,
        "evidence_root_used": bool(evidence_root),
        "payloads_retained": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gate-status", type=Path)
    parser.add_argument("--provider-status")
    parser.add_argument("--configuration-status")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--source-commit")
    parser.add_argument("--image-digest")
    parser.add_argument("--evidence-root", type=Path)
    args = parser.parse_args(argv)
    report = evaluate(
        gate_status_path=args.gate_status,
        provider_status=args.provider_status,
        configuration_status=args.configuration_status,
        candidate_sha=args.candidate_sha,
        source_commit=args.source_commit,
        image_digest=args.image_digest,
        evidence_root=args.evidence_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "mandatory_gate_count": len(GATE_DEFINITIONS)}, sort_keys=True))
    return 0 if report["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
