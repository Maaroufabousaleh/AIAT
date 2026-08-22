"""Run the governed OpenHands live adapter wave when operator state exists.

The command never fabricates a profile, model, bridge grant, workspace, or
provider credential.  It can prove health/runsc independently, but it refuses
to start a coding conversation until the interface report is separately
approved and all AIAT-bound configuration is present.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from mas_core.worker_contract import (
    AdapterContext,
    ModelProfileReference,
    WorkerRunRequest,
)
from mas_core.worker_registry.openhands_agent_server_adapter import (
    OpenHandsAgentServerAdapter,
    OpenHandsInterfaceVerification,
)

SCHEMA = "aiat.openhands-live-certification.v1"
_REQUIRED_ENV = (
    "OPENHANDS_SESSION_API_KEY",
    "AIAT_TOOL_SECRET",
    "OPENHANDS_AGENT_PROFILE_ID",
    "OPENHANDS_MCP_SETTINGS_KEY",
    "OPENHANDS_MODEL_ID",
)


def _status_map(status: str) -> dict[str, str]:
    return {
        "coding_task": status,
        "pause": "NOT_RUN",
        "interrupt": "NOT_RUN",
        "resume": "NOT_RUN",
        "crash_recovery": "NOT_RUN",
        "timeout": "NOT_RUN",
        "budget": "NOT_RUN",
        "forbidden_tool": "NOT_RUN",
        "workspace_isolation": "NOT_RUN",
        "secret_isolation": "NOT_RUN",
        "zero_residue": "NOT_RUN",
    }


async def certify(
    *,
    base_url: str,
    interface_report: Path,
    workspace: str | None,
    exercise_lifecycle: bool = False,
) -> dict[str, Any]:
    blockers: list[str] = []
    report_payload = json.loads(interface_report.read_text(encoding="utf-8"))
    verification = OpenHandsInterfaceVerification.from_report(report_payload)
    statuses = _status_map("BLOCKED")
    missing = [name for name in _REQUIRED_ENV if not os.getenv(name, "").strip()]
    if not verification.approved:
        blockers.append("interface_verification_not_steward_approved")
    if missing:
        blockers.append("operator_configuration_missing:" + ",".join(missing))
    if blockers:
        return {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "candidate": {
                "release": verification.release,
                "commit_sha": verification.commit_sha,
                "image_digest": verification.image_digest,
            },
            "worker_activation": "INACTIVE",
            "gates": statuses,
            "events": {"retained": False},
            "cleanup": {"status": "NOT_RUN", "payloads_retained": False},
            "blockers": blockers,
            "security_policy": "no findings accepted; no activation performed",
        }

    workspace_path = workspace or "/workspace/project"
    context = AdapterContext(
        workspace_path=workspace_path,
        secrets={
            "openhands_session_api_key": os.environ["OPENHANDS_SESSION_API_KEY"],
            "tool_secret": os.environ["AIAT_TOOL_SECRET"],
        },
        metadata={
            "openhands_agent_profile_id": os.environ["OPENHANDS_AGENT_PROFILE_ID"],
            "openhands_mcp_profile_ref": os.environ["OPENHANDS_MCP_SETTINGS_KEY"],
            "openhands_mcp_settings_key": os.environ["OPENHANDS_MCP_SETTINGS_KEY"],
            "openhands_mcp_bridge_url": "http://tool-service:8002/openhands/mcp",
            "openhands_image_digest": verification.image_digest,
            "openhands_cleanup_conversations": True,
            "openhands_public_skills_disabled": True,
            "openhands_plugins_disabled": True,
            "openhands_subagents_disabled": True,
            "openhands_browser_disabled": True,
            "openhands_direct_credentials_disabled": True,
        },
    )
    adapter = OpenHandsAgentServerAdapter(
        verification,
        base_url=base_url,
        worker_id="coding-worker-openhands-candidate",
        context=context,
    )
    request = WorkerRunRequest(
        run_id=uuid4(),
        idempotency_key=f"openhands-live-{uuid4().hex}",
        worker_id="coding-worker-openhands-candidate",
        task_type="coding",
        task_input={
            "prompt": (
                "In this disposable certification repository, make one minimal safe code change, "
                "run the existing tests, and report the result. Do not access credentials or external tools."
            )
        },
        resolved_model_profile=ModelProfileReference(
            profile_id="aiat-live-certification",
            exact_model_id=os.environ["OPENHANDS_MODEL_ID"],
        ),
        tool_grants=["aiat.repository.read", "aiat.repository.write", "aiat.tests.execute"],
        permission_requirements=["repository.read", "repository.write", "tests.execute"],
        timeout_seconds=int(os.getenv("OPENHANDS_CERT_TIMEOUT_SECONDS", "300")),
        budget={"max_iterations": int(os.getenv("OPENHANDS_CERT_MAX_ITERATIONS", "20"))},
    )
    try:
        readiness = await adapter.readiness(request)
        if not readiness.ready:
            blockers.extend([f"readiness:{item}" for item in readiness.blockers])
            statuses = _status_map("BLOCKED")
            return {
                "schema_version": SCHEMA,
                "status": "BLOCKED",
                "candidate": {"release": verification.release, "commit_sha": verification.commit_sha, "image_digest": verification.image_digest},
                "worker_activation": "INACTIVE",
                "gates": statuses,
                "readiness": {"checks": readiness.checks, "blockers": readiness.blockers},
                "events": {"retained": False},
                "cleanup": {"status": "NOT_RUN", "payloads_retained": False},
                "blockers": blockers,
                "security_policy": "no findings accepted; no activation performed",
            }
        await adapter.start(request)
        result_event = None
        event_count = 0
        async for event in adapter.events(request.run_id):
            event_count += 1
            if event.result is not None or event.error is not None:
                result_event = event
        statuses["coding_task"] = "PASS" if result_event and result_event.result and result_event.result.success else "FAIL"
        if statuses["coding_task"] != "PASS":
            blockers.append("live_coding_task_failed")
        if exercise_lifecycle:
            blockers.append("lifecycle_exercise_requires_dedicated_long_running_task_profile")
    except Exception as exc:  # runtime errors remain evidence, never activation
        statuses["coding_task"] = "FAIL"
        blockers.append(f"runtime:{type(exc).__name__}")
    finally:
        await adapter.close()
    statuses["zero_residue"] = "PASS" if not adapter._mcp_by_run else "FAIL"
    if statuses["zero_residue"] != "PASS":
        blockers.append("run_scoped_mcp_grant_residue")
    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not blockers and statuses["coding_task"] == "PASS" else "BLOCKED",
        "candidate": {"release": verification.release, "commit_sha": verification.commit_sha, "image_digest": verification.image_digest},
        "worker_activation": "INACTIVE",
        "gates": statuses,
        "events": {"count": event_count, "payloads_retained": False},
        "cleanup": {"status": statuses["zero_residue"], "payloads_retained": False},
        "blockers": blockers,
        "security_policy": "no findings accepted; no activation performed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("OPENHANDS_AGENT_SERVER_URL", ""))
    parser.add_argument("--interface-report", type=Path, required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exercise-lifecycle", action="store_true")
    args = parser.parse_args(argv)
    if not args.base_url:
        report = {"schema_version": SCHEMA, "status": "BLOCKED", "blockers": ["agent_server_url_missing"], "worker_activation": "INACTIVE"}
    else:
        report = asyncio.run(
            certify(
                base_url=args.base_url,
                interface_report=args.interface_report,
                workspace=args.workspace,
                exercise_lifecycle=args.exercise_lifecycle,
            )
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report.get("status"), "blockers": report.get("blockers", [])}, sort_keys=True))
    return 0 if report.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
