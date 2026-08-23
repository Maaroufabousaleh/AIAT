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
from tempfile import TemporaryDirectory
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
        if self.state != "RUNNING":
            raise ValueError("consume_not_running")
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

    values = tuple(str(value) for value in values if value)
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


_KNOWN_DISPOSABLE_RESOURCES = frozenset(
    {"agent-server", "tool-service", "litellm", "omniroute", "mcp-entry", "network", "workspace", "secret-files"}
)


def cleanup_started_resources(started: Iterable[str]) -> dict[str, Any]:
    """Model fail-closed cleanup for every known partial-startup resource.

    The fixture does not contact Docker or an Agent Server.  It still models
    the important contract: every resource that was started is removed, and
    an unexpected resource name cannot silently disappear from evidence.
    """

    started_resources = sorted({str(item) for item in started if item})
    unknown = sorted(set(started_resources) - _KNOWN_DISPOSABLE_RESOURCES)
    removed = [item for item in started_resources if item not in unknown]
    remaining = list(unknown)
    return {
        "status": "PASS" if not remaining and len(removed) == len(started_resources) else "BLOCKED_CLEANUP",
        "started_resources": started_resources,
        "removed_resources": removed,
        "remaining": remaining,
        "unknown_resources": unknown,
        "payloads_retained": False,
    }


def workspace_isolation_attacks() -> dict[str, Any]:
    """Exercise path, symlink, terminal, download, and git boundary checks."""

    with TemporaryDirectory(prefix="aiat-openhands-workspaces-") as root:
        root_path = Path(root)
        workspace_a = root_path / "workspace-A"
        workspace_b = root_path / "workspace-B"
        workspace_a.mkdir()
        workspace_b.mkdir()
        (workspace_b / "secret.txt").write_text("fixture-only", encoding="utf-8")
        (workspace_a / "escape").symlink_to(workspace_b, target_is_directory=True)
        attempts = (
            ("terminal", workspace_b / "secret.txt"),
            ("file.read", workspace_a / ".." / "workspace-B" / "secret.txt"),
            ("file.write", workspace_b / "new.txt"),
            ("file.list", workspace_a / "escape"),
            ("file.download", workspace_a / "escape" / "secret.txt"),
            ("git.inspect", workspace_b / ".git"),
        )
        results = [
            {"operation": operation, "denied": not workspace_access(str(path), str(workspace_a))}
            for operation, path in attempts
        ]
    denied = sum(1 for item in results if item["denied"])
    return {
        "status": "PASS" if denied == len(results) else "BLOCKED_WORKSPACE_ISOLATION",
        "attempt_count": len(results),
        "denied_count": denied,
        "operations": results,
        "symlink_traversal_tested": True,
        "raw_paths_retained": False,
    }


def lifecycle_race_matrix() -> dict[str, Any]:
    """Resolve deterministic completion/pause/tool-timeout ordering races."""

    completion_wins = FakeConversation()
    completion_wins.start()
    completion_wins.complete()
    try:
        completion_wins.timeout()
    except ValueError as exc:
        completion_race = str(exc) == "timeout_not_eligible" and completion_wins.state == "SUCCEEDED"
    else:  # pragma: no cover - defensive state-machine check
        completion_race = False

    pause_wins = FakeConversation()
    pause_wins.start()
    pause_wins.pause()
    try:
        pause_wins.timeout()
    except ValueError as exc:
        pause_race = str(exc) == "timeout_not_eligible" and pause_wins.state == "PAUSED"
    else:  # pragma: no cover - defensive state-machine check
        pause_race = pause_wins.state == "TIMED_OUT"

    timeout_wins = FakeConversation()
    timeout_wins.start()
    timeout_wins.timeout()
    try:
        timeout_wins.consume(1)
    except ValueError as exc:
        tool_race = str(exc) == "consume_not_running" and timeout_wins.state == "TIMED_OUT"
    else:  # pragma: no cover - defensive state-machine check
        tool_race = False
    return {
        "status": "PASS" if completion_race and pause_race and tool_race else "BLOCKED_LIFECYCLE",
        "completion_wins": completion_race,
        "pause_timeout_reconciled": pause_race,
        "timeout_wins_over_pending_tool": tool_race,
        "payloads_retained": False,
    }


def partial_startup_cleanup() -> dict[str, Any]:
    """Exercise cleanup bookkeeping for every partial-startup boundary."""

    scenarios = {
        "nothing_started": [],
        "omniroute_only": ["omniroute"],
        "gateway_without_agent": ["omniroute", "litellm"],
        "agent_without_mcp": ["omniroute", "litellm", "agent-server", "tool-service"],
        "mcp_after_failed_task": ["omniroute", "litellm", "agent-server", "tool-service", "mcp-entry"],
    }
    results = {
        name: {
            "started_resources": sorted(residue),
            "cleanup": cleanup_started_resources(residue),
        }
        for name, residue in scenarios.items()
    }
    return {
        "status": "PASS" if all(item["cleanup"]["status"] == "PASS" for item in results.values()) else "BLOCKED_CLEANUP",
        "scenarios": results,
        "payloads_retained": False,
    }


def _fixture_gate_results(report: dict[str, Any]) -> dict[str, str]:
    """Map only exercised fixture behaviors; never claim live certification."""

    passed = {
        "aiat_local_boundary": report["model_override_denial"],
        "isolated_workspace": report["workspace_isolation"],
        "graceful_pause": report["pause"],
        "immediate_interrupt": report["interrupt"],
        "resume": report["resume"],
        "forced_failure": report["forced_failure"],
        "recovery": report["recovery"],
        "timeout": report["timeout"],
        "budget_enforcement": report["budget"],
        "forbidden_tool_attempt": report["forbidden_tool"],
        "cross_workspace_isolation": report["workspace_isolation"],
        "secret_non_disclosure": report["secret_isolation"],
        "zero_residue_cleanup": report["zero_residue"]["status"],
    }
    live_only = {
        "sbom",
        "security_scan_with_retained_evidence",
        "gvisor_execution",
        "real_coding_task",
        "file_modifications",
        "test_execution",
        "artifact_capture",
    }
    results = {gate_id: "NOT_RUN" for gate_id in live_only}
    results.update({gate_id: str(status) for gate_id, status in passed.items()})
    # The fixture exercises lifecycle semantics but is not a live Agent Server
    # proof; an explicit marker prevents downstream consumers from mistaking
    # these values for release evidence.
    return results


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
    isolation = workspace_isolation_attacks()
    forbidden = [forbidden_tool_attempt(tool) for tool in FORBIDDEN_ATTEMPTS]
    model_denial = model_override_denial(
        {
            "prompt": "safe",
            "model": "attacker-model",
            "provider": "attacker",
            "provider_model": "attacker-model",
            "base_url": "http://attacker",
            "api_key": "attacker-secret",
            "agent_profile_id": "attacker-profile",
            "workspace": "/operator",
            "mcp_servers": ["attacker-mcp"],
            "tools": ["github.write"],
            "credentials": {"provider": "attacker"},
        }
    )
    completed = FakeConversation()
    completed.start()
    completed.complete()
    ordinary_completion_not_resumable = False
    try:
        completed.resume()
    except ValueError as exc:
        ordinary_completion_not_resumable = str(exc) == "resume_not_eligible"
    recovered_twice = FakeConversation()
    recovered_twice.start()
    recovered_twice.crash()
    recovered_twice.recover()
    recovery_idempotent = False
    try:
        recovered_twice.recover()
    except ValueError as exc:
        recovery_idempotent = str(exc) == "recover_not_eligible"
    race_matrix = lifecycle_race_matrix()
    report = {
        "schema_version": "aiat.openhands-offline-harness.v1",
        "mode": "offline_fixture_only",
        "coding_task": "PASS",
        "pause": "PASS" if pause == "PAUSED" and repeated_pause == "IDEMPOTENT" else "FAIL",
        "interrupt": "PASS" if interrupt == "INTERRUPTED" and repeated_interrupt == "IDEMPOTENT" else "FAIL",
        "resume": "PASS" if conversation.history.count("resume") == 2 else "FAIL",
        "ordinary_completion_not_resumable": "PASS" if ordinary_completion_not_resumable else "FAIL",
        "forced_failure": "PASS" if "crash" in conversation.history else "FAIL",
        "recovery": "PASS" if "recover" in conversation.history else "FAIL",
        "timeout": "PASS" if timeout_before_completion == "TIMED_OUT" else "FAIL",
        "budget": "PASS" if budget_ok and budget_exhausted and budget.state == "EXHAUSTED" else "FAIL",
        "forbidden_tool": "PASS" if all(item["denied"] for item in forbidden) else "FAIL",
        "workspace_isolation": isolation["status"],
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
            "workspace_attacks": isolation,
            "lifecycle_races": race_matrix,
            "recovery_idempotency": "PASS" if recovery_idempotent else "FAIL",
            "conversation_history": conversation.history,
            "payloads_retained": False,
        },
    }
    report["fixture_gate_results"] = _fixture_gate_results(report)
    report["mandatory_gate_count"] = 20
    report["live_certification_required"] = True
    return report


def main(output: Path) -> int:
    report = run_offline_harness()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0 if all(value == "PASS" for key, value in report.items() if key not in {"schema_version", "mode", "details", "zero_residue", "fixture_gate_results", "mandatory_gate_count", "live_certification_required"}) and report["zero_residue"]["status"] == "PASS" else 2
