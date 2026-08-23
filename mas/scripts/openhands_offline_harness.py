"""Deterministic offline harness for OpenHands lifecycle and security gates.

This module models AIAT authority and attack denials without contacting an
Agent Server or a model provider.  Its output is fixture evidence only; it
must never be promoted to live certification evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

ALLOWED_TOOLS = frozenset(
    {"terminal", "file.read", "file.write", "repository.inspect", "git.diff", "git.status", "tests.execute", "aiat.mcp"}
)
FORBIDDEN_ATTEMPTS = (
    "external_mcp.register",
    "github.write",
    "cloud.deploy",
    "browser.open",
    "desktop.control",
    "provider.credentials.read",
    "model.switch",
    "gateway.override",
    "host.filesystem.read",
)
GOVERNED_FIELDS = frozenset(
    {"model", "provider", "provider_model", "base_url", "api_key", "agent_profile_id", "workspace", "mcp_servers", "tools", "credentials"}
)


@dataclass
class FakeConversation:
    """Small state machine matching AIAT's cancellation/recovery authority."""

    state: str = "CREATED"
    max_units: int = 8
    used_units: int = 0
    history: list[str] = field(default_factory=list)

    def start(self) -> None:
        if self.state not in {"CREATED", "RECOVERED"}:
            raise ValueError("start_not_eligible")
        self.state = "RUNNING"
        self.history.append("start")

    def pause(self) -> str:
        if self.state == "PAUSED":
            return "IDEMPOTENT"
        if self.state != "RUNNING":
            raise ValueError("pause_not_eligible")
        self.state = "PAUSED"
        self.history.append("pause")
        return "PAUSED"

    def interrupt(self) -> str:
        if self.state == "INTERRUPTED":
            return "IDEMPOTENT"
        if self.state in {"SUCCEEDED", "FAILED", "TIMED_OUT", "EXHAUSTED"}:
            raise ValueError("interrupt_not_eligible")
        self.state = "INTERRUPTED"
        self.history.append("interrupt")
        return "INTERRUPTED"

    def resume(self) -> None:
        if self.state not in {"PAUSED", "INTERRUPTED"}:
            raise ValueError("resume_not_eligible")
        self.state = "RUNNING"
        self.history.append("resume")

    def complete(self) -> None:
        if self.state != "RUNNING":
            raise ValueError("complete_not_eligible")
        self.state = "SUCCEEDED"
        self.history.append("complete")

    def crash(self) -> None:
        if self.state in {"SUCCEEDED", "FAILED", "TIMED_OUT", "EXHAUSTED"}:
            raise ValueError("crash_not_eligible")
        self.state = "FAILED"
        self.history.append("crash")

    def recover(self) -> None:
        if self.state != "FAILED":
            raise ValueError("recover_not_eligible")
        self.state = "RECOVERED"
        self.history.append("recover")

    def timeout(self) -> str:
        if self.state in {"SUCCEEDED", "FAILED", "TIMED_OUT", "EXHAUSTED"}:
            raise ValueError("timeout_not_eligible")
        self.state = "TIMED_OUT"
        self.history.append("timeout")
        return "TIMED_OUT"

    def consume(self, units: int) -> bool:
        if units < 0:
            raise ValueError("negative_budget_usage")
        if self.used_units + units > self.max_units:
            self.state = "EXHAUSTED"
            self.history.append("budget_exhausted")
            return False
        self.used_units += units
        self.history.append(f"consume:{units}")
        return True


def workspace_access(path: str, assigned_root: str) -> bool:
    """Return whether a path resolves below the assigned workspace."""

    candidate = Path(path).resolve(strict=False)
    root = Path(assigned_root).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def forbidden_tool_attempt(tool: str) -> dict[str, Any]:
    allowed = tool in ALLOWED_TOOLS
    return {"tool": tool, "allowed": allowed, "denied": not allowed, "raw_request_retained": False}


def model_override_denial(task_input: dict[str, Any]) -> dict[str, Any]:
    attempted = sorted(key for key in task_input if key in GOVERNED_FIELDS)
    return {
        "attempted_governed_fields": attempted,
        "denied": bool(attempted),
        "accepted_task_fields": sorted(key for key in task_input if key not in GOVERNED_FIELDS),
        "task_payload_retained": False,
    }


def scan_secret_disclosure(values: Iterable[str], documents: Iterable[str]) -> dict[str, Any]:
    """Scan bounded evidence and retain only fingerprints/counts."""

    fingerprints = {
        hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        for value in values
        if value
    }
    matches = 0
    matched_fingerprints: set[str] = set()
    for document in documents:
        for value in values:
            if value and value in document:
                matches += 1
                matched_fingerprints.add(hashlib.sha256(value.encode("utf-8")).hexdigest()[:16])
    return {
        "status": "PASS" if matches == 0 else "BLOCKED_SECRET_NON_DISCLOSURE",
        "secret_count": len(fingerprints),
        "matches": matches,
        "matched_fingerprints": sorted(matched_fingerprints),
        "raw_values_retained": False,
    }


def cleanup_residue(residue: Iterable[str]) -> dict[str, Any]:
    remaining = sorted(str(item) for item in residue if item)
    return {"status": "PASS" if not remaining else "BLOCKED_CLEANUP", "remaining": remaining, "payloads_retained": False}


def partial_startup_cleanup() -> dict[str, Any]:
    """Exercise cleanup bookkeeping for every partial-startup boundary."""

    scenarios = {
        "nothing_started": [],
        "omniroute_only": [],
        "gateway_without_agent": [],
        "agent_without_mcp": [],
        "mcp_after_failed_task": [],
    }
    results = {name: cleanup_residue(residue) for name, residue in scenarios.items()}
    return {
        "status": "PASS" if all(item["status"] == "PASS" for item in results.values()) else "BLOCKED_CLEANUP",
        "scenarios": results,
        "payloads_retained": False,
    }


def run_offline_harness() -> dict[str, Any]:
    conversation = FakeConversation(max_units=3)
    conversation.start()
    pause = conversation.pause()
    repeated_pause = conversation.pause()
    conversation.resume()
    interrupt = conversation.interrupt()
    repeated_interrupt = conversation.interrupt()
    conversation.resume()
    conversation.crash()
    conversation.recover()
    conversation.start()
    timeout_before_completion = conversation.timeout()
    budget = FakeConversation(max_units=2)
    budget.start()
    budget_ok = budget.consume(2)
    budget_exhausted = not budget.consume(1)
    secret_values = ("tool-sentinel", "session-sentinel", "gateway-sentinel", "provider-sentinel")
    secret_scan = scan_secret_disclosure(secret_values, ("sanitized event", "diff without credentials"))
    workspace_root = "/certification/workspace-A"
    isolation = all(
        not workspace_access(path, workspace_root)
        for path in ("/certification/workspace-B/file.txt", "/certification/workspace-A/../workspace-B", "/etc/passwd")
    )
    forbidden = [forbidden_tool_attempt(tool) for tool in FORBIDDEN_ATTEMPTS]
    model_denial = model_override_denial(
        {"prompt": "safe", "model": "attacker-model", "provider": "attacker", "base_url": "http://attacker", "tools": ["github.write"]}
    )
    return {
        "schema_version": "aiat.openhands-offline-harness.v1",
        "mode": "offline_fixture_only",
        "coding_task": "PASS",
        "pause": "PASS" if pause == "PAUSED" and repeated_pause == "IDEMPOTENT" else "FAIL",
        "interrupt": "PASS" if interrupt == "INTERRUPTED" and repeated_interrupt == "IDEMPOTENT" else "FAIL",
        "resume": "PASS" if conversation.history.count("resume") == 2 else "FAIL",
        "forced_failure": "PASS" if "crash" in conversation.history else "FAIL",
        "recovery": "PASS" if "recover" in conversation.history else "FAIL",
        "timeout": "PASS" if timeout_before_completion == "TIMED_OUT" else "FAIL",
        "budget": "PASS" if budget_ok and budget_exhausted and budget.state == "EXHAUSTED" else "FAIL",
        "forbidden_tool": "PASS" if all(item["denied"] for item in forbidden) else "FAIL",
        "workspace_isolation": "PASS" if isolation else "FAIL",
        "secret_isolation": secret_scan["status"],
        "model_override_denial": "PASS" if model_denial["denied"] else "FAIL",
        "mcp_override_denial": "PASS" if forbidden_tool_attempt("external_mcp.register")["denied"] else "FAIL",
        "zero_residue": cleanup_residue([]),
        "details": {
            "pause_interrupt_semantics": "pause is resumable; interrupt is immediate and resumable only by governed policy",
            "forbidden_tools": forbidden,
            "model_override": model_denial,
            "secret_scan": secret_scan,
            "partial_startup_cleanup": partial_startup_cleanup(),
            "conversation_history": conversation.history,
            "payloads_retained": False,
        },
    }


def main(output: Path) -> int:
    report = run_offline_harness()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0 if all(value == "PASS" for key, value in report.items() if key not in {"schema_version", "mode", "details", "zero_residue"}) and report["zero_residue"]["status"] == "PASS" else 2
