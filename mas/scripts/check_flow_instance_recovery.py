"""Inspect or explicitly exercise one flow-instance recovery boundary.

The default live action is a read-only status/execution-history check. State
changing actions require both ``--action`` and ``--confirm`` so a release
operator cannot mutate a flow instance accidentally. API/configuration errors
are blocked (exit 2); a response that violates the expected post-action state
fails (exit 1). This helper never claims a complete software project, worker
canary, provider recovery, or UI golden path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any

import httpx

FLOW_RECOVERY_SCHEMA = "aiat.flow-instance-recovery-readiness.v1"
TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
ACTION_PATHS = {
    "start": ("action", "RUNNING"),
    "pause": ("action", "PAUSED"),
    "resume": ("action", "RUNNING"),
    "cancel": ("action", "CANCELLED"),
    "retry": ("retry", "RUNNING"),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="query the orchestrator API")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--url",
        default=os.getenv("AIAT_ORCHESTRATOR_URL", os.getenv("ORCHESTRATOR_API_URL", "")),
        help="orchestrator base URL (or AIAT_ORCHESTRATOR_URL/ORCHESTRATOR_API_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIAT_API_KEY", os.getenv("MAS_API_KEY", "")),
        help="optional bearer key; never included in the report",
    )
    parser.add_argument("--instance-id", help="flow instance UUID to inspect or act on")
    parser.add_argument(
        "--action",
        choices=("status", "start", "pause", "resume", "cancel", "retry"),
        default="status",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="explicitly authorize the selected state-changing action",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def _static_report() -> dict[str, Any]:
    return {
        "schema_version": FLOW_RECOVERY_SCHEMA,
        "mode": "static",
        "status": "pass",
        "reason": "live mode was not requested",
        "action": "status",
        "scope": "declaration only; flow instance not checked",
        "recovery_boundary": {
            "instance_status": "not_checked",
            "execution_history": "not_checked",
            "state_change": "not_checked",
            "worker_canary": "not_checked",
            "ui_golden_path": "not_checked",
        },
    }


def _blocked(reason: str, *, action: str = "status", url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": FLOW_RECOVERY_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "action": action,
        "url_configured": url_configured,
        "scope": "flow instance status/execution history and explicitly confirmed action",
        "recovery_boundary": {
            "instance_status": "not_checked",
            "execution_history": "not_checked",
            "state_change": "not_checked",
            "worker_canary": "not_checked",
            "ui_golden_path": "not_checked",
        },
    }


def _summary(instance: Any, executions: Any) -> dict[str, Any] | None:
    if not isinstance(instance, dict) or not isinstance(executions, list):
        return None
    status = str(instance.get("status", "")).upper()
    if not status:
        return None
    states = Counter(
        str(row.get("status", "")).upper()
        for row in executions
        if isinstance(row, dict) and row.get("status") is not None
    )
    active_nodes = instance.get("active_node_ids")
    return {
        "status": status,
        "flow_id_present": bool(instance.get("flow_id")),
        "project_id_present": bool(instance.get("project_id")),
        "active_node_count": len(active_nodes) if isinstance(active_nodes, list) else 0,
        "retry_count": int(instance.get("retry_count") or 0),
        "execution_count": len(executions),
        "execution_status_counts": dict(sorted(states.items())),
        "terminal": status in TERMINAL_STATES,
        "recovery_context_present": bool(instance.get("context_json") or instance.get("context")),
    }


def inspect_live(
    *,
    url: str,
    api_key: str,
    instance_id: str | None,
    action: str,
    confirm: bool,
    timeout: float,
) -> dict[str, Any]:
    if not url.strip():
        return _blocked("missing live configuration: orchestrator URL", action=action)
    if not instance_id or not instance_id.strip():
        return _blocked("missing flow instance ID", action=action, url_configured=True)
    if action != "status" and not confirm:
        return _blocked(
            "state-changing flow action requires explicit --confirm",
            action=action,
            url_configured=True,
        )
    endpoint = url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key.strip() else {}
    base_url = f"{endpoint}/flows/instances/{instance_id}"
    try:
        response = httpx.get(base_url, headers=headers, timeout=timeout)
        response.raise_for_status()
        instance = response.json()
        executions_response = httpx.get(f"{base_url}/executions", headers=headers, timeout=timeout)
        executions_response.raise_for_status()
        executions = executions_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return _blocked(f"flow instance evidence unavailable: {type(exc).__name__}", action=action, url_configured=True)
    before = _summary(instance, executions)
    if before is None:
        return _blocked("orchestrator returned malformed flow instance evidence", action=action, url_configured=True)
    result: dict[str, Any] = {
        "schema_version": FLOW_RECOVERY_SCHEMA,
        "mode": "live",
        "status": "pass",
        "reason": "read-only flow instance status and execution history observed",
        "action": action,
        "instance_id": instance_id,
        "before": before,
        "scope": "flow instance status/execution history and explicitly confirmed action",
        "recovery_boundary": {
            "instance_status": "checked",
            "execution_history": "checked",
            "state_change": "not_requested" if action == "status" else "requested",
            "worker_canary": "not_checked",
            "ui_golden_path": "not_checked",
        },
    }
    if action == "status":
        return result

    action_path, expected_state = ACTION_PATHS[action]
    try:
        if action_path == "action":
            action_response = httpx.post(
                f"{base_url}/action",
                headers=headers,
                json={"action": action},
                timeout=timeout,
            )
        else:
            action_response = httpx.post(f"{base_url}/retry", headers=headers, timeout=timeout)
        action_response.raise_for_status()
        action_instance = action_response.json()
        after_response = httpx.get(base_url, headers=headers, timeout=timeout)
        after_response.raise_for_status()
        after_instance = after_response.json()
        after_executions_response = httpx.get(
            f"{base_url}/executions", headers=headers, timeout=timeout
        )
        after_executions_response.raise_for_status()
        after_executions = after_executions_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        result["status"] = "blocked"
        result["reason"] = f"confirmed flow action unavailable: {type(exc).__name__}"
        result["recovery_boundary"]["state_change"] = "blocked"
        return result
    after = _summary(after_instance, after_executions)
    action_summary = _summary(action_instance, [])
    if after is None:
        result["status"] = "blocked"
        result["reason"] = "orchestrator returned malformed post-action evidence"
        result["recovery_boundary"]["state_change"] = "blocked"
        return result
    result["after"] = after
    result["action_response_state"] = action_summary["status"] if action_summary else None
    if after["status"] != expected_state:
        result["status"] = "fail"
        result["reason"] = f"expected post-action state {expected_state}, got {after['status']}"
        result["recovery_boundary"]["state_change"] = "fail"
    else:
        result["reason"] = f"confirmed {action} action reached {expected_state}"
        result["recovery_boundary"]["state_change"] = "checked"
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = (
        inspect_live(
            url=args.url,
            api_key=args.api_key,
            instance_id=args.instance_id,
            action=args.action,
            confirm=args.confirm,
            timeout=args.timeout,
        )
        if args.live
        else _static_report()
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"flow instance recovery: {report['status']} — {report.get('reason', 'no reason')}")
    return 2 if report["status"] == "blocked" else (1 if report["status"] == "fail" else 0)


if __name__ == "__main__":
    sys.exit(main())
