"""Canonical mandatory-gate definitions and evaluation for OpenHands."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

GATE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {"gate_id": "sbom", "phase": "supply_chain", "live_required": False},
    {"gate_id": "security_scan_with_retained_evidence", "phase": "supply_chain", "live_required": False},
    {"gate_id": "aiat_local_boundary", "phase": "governance", "live_required": False},
    {"gate_id": "gvisor_execution", "phase": "runtime", "live_required": True},
    {"gate_id": "isolated_workspace", "phase": "workspace", "live_required": True},
    {"gate_id": "real_coding_task", "phase": "coding", "live_required": True},
    {"gate_id": "file_modifications", "phase": "coding", "live_required": True},
    {"gate_id": "test_execution", "phase": "coding", "live_required": True},
    {"gate_id": "artifact_capture", "phase": "evidence", "live_required": True},
    {"gate_id": "graceful_pause", "phase": "lifecycle", "live_required": True},
    {"gate_id": "immediate_interrupt", "phase": "lifecycle", "live_required": True},
    {"gate_id": "resume", "phase": "lifecycle", "live_required": True},
    {"gate_id": "forced_failure", "phase": "recovery", "live_required": True},
    {"gate_id": "recovery", "phase": "recovery", "live_required": True},
    {"gate_id": "timeout", "phase": "lifecycle", "live_required": True},
    {"gate_id": "budget_enforcement", "phase": "governance", "live_required": True},
    {"gate_id": "forbidden_tool_attempt", "phase": "security", "live_required": True},
    {"gate_id": "cross_workspace_isolation", "phase": "security", "live_required": True},
    {"gate_id": "secret_non_disclosure", "phase": "security", "live_required": True},
    {"gate_id": "zero_residue_cleanup", "phase": "cleanup", "live_required": True},
)

GATE_IDS = tuple(item["gate_id"] for item in GATE_DEFINITIONS)
_KNOWN_STATUSES = {"PASS", "NOT_RUN"}


def initial_gate_map() -> dict[str, dict[str, Any]]:
    """Return a complete map; every mandatory gate starts as ``NOT_RUN``."""

    return {
        item["gate_id"]: {
            **item,
            "required": True,
            "status": "NOT_RUN",
            "evidence_refs": [],
            "failure_class": None,
            "sanitized_details": {},
        }
        for item in GATE_DEFINITIONS
    }


def evaluate_gate_map(
    gates: Mapping[str, Mapping[str, Any]],
    *,
    blocker_status: str | None = None,
) -> dict[str, Any]:
    """Evaluate a gate map without allowing omitted gates to pass.

    The returned report contains counts and only scalar/sanitized details.
    ``blocker_status`` is used for an external prerequisite such as a missing
    provider secret; it never turns ``NOT_RUN`` into ``PASS``.
    """

    missing = sorted(set(GATE_IDS) - set(gates))
    unknown = sorted(set(gates) - set(GATE_IDS))
    statuses: dict[str, str] = {}
    invalid: list[str] = []
    for gate_id in GATE_IDS:
        row = gates.get(gate_id) or {}
        status = str(row.get("status") or "NOT_RUN").upper()
        statuses[gate_id] = status
        if status not in _KNOWN_STATUSES and not status.startswith(("BLOCKED_", "FAILED_")):
            invalid.append(gate_id)

    passed = sorted(gate_id for gate_id, status in statuses.items() if status == "PASS")
    not_run = sorted(gate_id for gate_id, status in statuses.items() if status == "NOT_RUN")
    blocked = sorted(gate_id for gate_id, status in statuses.items() if status.startswith("BLOCKED_"))
    failed = sorted(gate_id for gate_id, status in statuses.items() if status.startswith("FAILED_"))
    if missing or unknown or invalid:
        final_status = "FAILED_CERTIFICATION_IMPLEMENTATION"
    elif all(status == "PASS" for status in statuses.values()):
        final_status = "PASSED"
    elif failed:
        final_status = statuses[failed[0]]
    elif blocker_status and blocker_status != "PASS":
        final_status = blocker_status
    elif blocked:
        final_status = statuses[blocked[0]]
    else:
        final_status = "BLOCKED_INCOMPLETE_MANDATORY_GATES"
    return {
        "status": final_status,
        "mandatory_gate_count": len(GATE_IDS),
        "passed_gate_count": len(passed),
        "not_run_gate_count": len(not_run),
        "blocked_gate_count": len(blocked),
        "failed_gate_count": len(failed),
        "all_required_gates_passed": final_status == "PASSED",
        "passed_gates": passed,
        "not_run_gates": not_run,
        "blocked_gates": blocked,
        "failed_gates": failed,
        "missing_gates": missing,
        "unknown_gates": unknown,
        "invalid_gates": invalid,
    }
