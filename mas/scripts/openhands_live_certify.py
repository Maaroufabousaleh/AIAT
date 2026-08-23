"""Run the governed OpenHands live adapter wave when operator state exists.

The command never fabricates a profile, model, bridge grant, workspace, or
provider credential.  A pending interface report may be exercised only by the
dedicated certification controller through a run-scoped authorization.  That
authorization is never activation approval and the worker remains inactive.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from mas_core.worker_contract import (
    AdapterContext,
    ModelProfileReference,
    WorkerRunRequest,
)
from mas_core.worker_registry.openhands_agent_server_adapter import (
    OpenHandsAgentServerAdapter,
    OpenHandsInterfaceVerification,
    issue_openhands_certification_authorization,
)

SCHEMA = "aiat.openhands-live-certification.v1"
_REQUIRED_ENV = (
    "OPENHANDS_SESSION_API_KEY",
    "AIAT_TOOL_SECRET",
    "OPENHANDS_MCP_SETTINGS_KEY",
    "OPENHANDS_MODEL_ID",
)
_CERTIFICATION_CONTROLLER = "aiat-github-actions"


def _load_task_definition(task_spec: Path | None) -> tuple[str | None, dict[str, Any], list[str]]:
    """Load the execution prompt without retaining it in evidence.

    The live Agent Server must receive the exact prompt from the governed task
    specification.  The prompt is still task payload, so the sanitized report
    keeps only scalar task metadata and a specification hash.
    """

    if task_spec is None:
        return None, {}, []
    try:
        loaded_task = json.loads(task_spec.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, {}, ["coding_task_spec_invalid"]
    if not isinstance(loaded_task, dict) or not str(loaded_task.get("prompt") or "").strip():
        return None, {}, ["coding_task_spec_invalid"]
    try:
        task_hash = hashlib.sha256(task_spec.read_bytes()).hexdigest()
    except OSError:
        return None, {}, ["coding_task_spec_invalid"]
    definition = {
        "task_id": loaded_task.get("task_id"),
        "test_command": loaded_task.get("test_command"),
        "expected_changed_paths": loaded_task.get("expected_changed_paths", []),
        "forbidden_changed_paths": loaded_task.get("forbidden_changed_paths", []),
        "task_spec_sha256": task_hash,
    }
    return str(loaded_task["prompt"]).strip(), definition, []


def _status_map(status: str) -> dict[str, str]:
    return {
        "coding_task": status,
        "pause": "NOT_RUN",
        "interrupt": "NOT_RUN",
        "resume": "NOT_RUN",
        "forced_failure": "NOT_RUN",
        "crash_recovery": "NOT_RUN",
        "timeout": "NOT_RUN",
        "budget": "NOT_RUN",
        "forbidden_tool": "NOT_RUN",
        "workspace_isolation": "NOT_RUN",
        "secret_isolation": "NOT_RUN",
        "zero_residue": "NOT_RUN",
    }


def _final_status(statuses: dict[str, str], blockers: list[str]) -> str:
    """Return a narrow wrapper status without promoting NOT_RUN to PASS."""

    if any(item.startswith("operator_configuration_missing:") for item in blockers):
        return "BLOCKED_OPERATOR_CONFIGURATION"
    if any(item.startswith("readiness:") for item in blockers):
        return "BLOCKED_RUNTIME_STARTUP"
    if any(item.startswith("certification_authorization:") for item in blockers):
        return "BLOCKED_CERTIFICATION_AUTHORIZATION"
    if any(item.startswith("runtime:") for item in blockers):
        return "FAILED_CERTIFICATION_IMPLEMENTATION"
    if statuses.get("coding_task") == "FAILED_MODEL_EXECUTION":
        return "FAILED_MODEL_EXECUTION"
    if statuses.get("zero_residue") == "FAILED_CLEANUP":
        return "BLOCKED_CLEANUP"
    if all(value == "PASS" for value in statuses.values()) and not blockers:
        return "PASS"
    return "BLOCKED_INCOMPLETE_MANDATORY_GATES"


async def certify(
    *,
    base_url: str,
    interface_report: Path,
    workspace: str | None,
    exercise_lifecycle: bool = False,
    task_spec: Path | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    report_payload = json.loads(interface_report.read_text(encoding="utf-8"))
    verification = OpenHandsInterfaceVerification.from_report(report_payload)
    statuses = _status_map("NOT_RUN")
    task_prompt, task_definition, task_spec_blockers = _load_task_definition(task_spec)
    blockers.extend(task_spec_blockers)
    missing = [name for name in _REQUIRED_ENV if not os.getenv(name, "").strip()]
    controller = os.getenv("OPENHANDS_CERTIFICATION_CONTROLLER", "").strip()
    controller_run_id = os.getenv("OPENHANDS_CERT_CONTROLLER_RUN_ID", "").strip()
    sandbox_profile = os.getenv("OPENHANDS_SANDBOX_PROFILE", "").strip()
    sandbox_runtime = os.getenv("OPENHANDS_SANDBOX_RUNTIME", "").strip()
    if controller != _CERTIFICATION_CONTROLLER:
        blockers.append("certification_controller_attestation_missing")
    if not controller_run_id:
        blockers.append("certification_controller_run_id_missing")
    if sandbox_profile.lower() != "gvisor":
        blockers.append("certification_sandbox_profile_must_be_gvisor")
    if sandbox_runtime.lower() != "runsc":
        blockers.append("certification_sandbox_runtime_must_be_runsc")
    if os.getenv("OPENHANDS_MCP_PRECONFIGURED") == "1":
        missing.extend(
            name
            for name in ("OPENHANDS_CERT_RUN_ID", "OPENHANDS_PROJECT_ID")
            if not os.getenv(name, "").strip()
        )
    if missing:
        blockers.append("operator_configuration_missing:" + ",".join(missing))
    if blockers:
        return {
            "schema_version": SCHEMA,
            "status": _final_status(statuses, blockers),
            "candidate": {
                "release": verification.release,
                "commit_sha": verification.commit_sha,
                "image_digest": verification.image_digest,
            },
            "worker_activation": "INACTIVE",
            "interface_verification": {
                "approved": verification.approved,
                "approval_status": "APPROVED" if verification.approved else "PENDING",
            },
            "certification_authorization": {
                "status": "NOT_ISSUED",
                "required": not verification.approved,
            },
            "activation_approval": {"status": "PENDING", "required": True},
            "gates": statuses,
            "events": {"retained": False},
            "cleanup": {"status": "NOT_RUN", "payloads_retained": False},
            "blockers": blockers,
            "task": task_definition,
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
            # The profile UUID is materialized by the disposable Agent Server
            # and supplied as a run-scoped workflow output.  It is not a
            # portable repository/operator input.
            "openhands_agent_profile_id": os.getenv("OPENHANDS_AGENT_PROFILE_ID", ""),
            "openhands_mcp_profile_ref": os.environ["OPENHANDS_MCP_SETTINGS_KEY"],
            "openhands_mcp_settings_key": os.environ["OPENHANDS_MCP_SETTINGS_KEY"],
            "openhands_mcp_preconfigured": os.getenv("OPENHANDS_MCP_PRECONFIGURED") == "1",
            "openhands_mcp_bridge_url": "http://tool-service:8002/openhands/mcp",
            "openhands_image_digest": verification.image_digest,
            "openhands_model_id": os.environ["OPENHANDS_MODEL_ID"],
            "openhands_cleanup_conversations": True,
            "openhands_public_skills_disabled": True,
            "openhands_plugins_disabled": True,
            "openhands_subagents_disabled": True,
            "openhands_browser_disabled": True,
            "openhands_direct_credentials_disabled": True,
            "openhands_certification_controller": controller,
            "openhands_certification_controller_run_id": controller_run_id,
            "openhands_certification_sandbox_profile": sandbox_profile.lower(),
            "openhands_certification_sandbox_runtime": sandbox_runtime.lower(),
        },
    )
    authorization = None
    if not verification.approved:
        try:
            authorization = issue_openhands_certification_authorization(
                verification,
                controller=controller,
                controller_run_id=controller_run_id,
                sandbox_profile=sandbox_profile,
                sandbox_runtime=sandbox_runtime,
            )
        except ValueError as exc:
            blockers.append(f"certification_authorization:{exc}")
            return {
                "schema_version": SCHEMA,
                "status": _final_status(statuses, blockers),
                "candidate": {
                    "release": verification.release,
                    "commit_sha": verification.commit_sha,
                    "image_digest": verification.image_digest,
                },
                "worker_activation": "INACTIVE",
                "certification_authorization": {"status": "NOT_ISSUED", "required": True},
                "activation_approval": {"status": "PENDING", "required": True},
                "gates": statuses,
                "events": {"retained": False},
                "cleanup": {"status": "NOT_RUN", "payloads_retained": False},
                "blockers": blockers,
                "task": task_definition,
                "security_policy": "certification authorization never implies activation approval",
            }
    if authorization is not None:
        adapter = OpenHandsAgentServerAdapter.for_certification(
            verification,
            authorization=authorization,
            base_url=base_url,
            worker_id="coding-worker-openhands-candidate",
            context=context,
        )
    else:
        adapter = OpenHandsAgentServerAdapter(
            verification,
            base_url=base_url,
            worker_id="coding-worker-openhands-candidate",
            context=context,
        )
    request = WorkerRunRequest(
        run_id=UUID(os.environ["OPENHANDS_CERT_RUN_ID"]) if os.getenv("OPENHANDS_CERT_RUN_ID") else uuid4(),
        idempotency_key=f"openhands-live-{uuid4().hex}",
        worker_id="coding-worker-openhands-candidate",
        task_type="coding",
        project_id=UUID(os.environ["OPENHANDS_PROJECT_ID"]) if os.getenv("OPENHANDS_PROJECT_ID") else None,
        task_input={
            "prompt": task_prompt
            or "In this disposable certification repository, make one minimal safe code change, run the existing tests, and report the result. Do not access credentials or external tools."
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
            statuses = _status_map("NOT_RUN")
            return {
                "schema_version": SCHEMA,
                "status": _final_status(statuses, blockers),
                "candidate": {"release": verification.release, "commit_sha": verification.commit_sha, "image_digest": verification.image_digest},
                "worker_activation": "INACTIVE",
                "certification_authorization": {
                    "status": "AUTHORIZED" if authorization is not None else "NOT_REQUIRED",
                    "controller_run_id": controller_run_id if authorization is not None else None,
                    "candidate_commit": verification.commit_sha if authorization is not None else None,
                    "image_digest": verification.image_digest if authorization is not None else None,
                    "sandbox_profile": sandbox_profile.lower() if authorization is not None else None,
                    "sandbox_runtime": sandbox_runtime.lower() if authorization is not None else None,
                },
                "activation_approval": {"status": "PENDING", "required": True},
                "gates": statuses,
                "readiness": {"checks": readiness.checks, "blockers": readiness.blockers},
                "events": {"retained": False},
                "cleanup": {"status": "NOT_RUN", "payloads_retained": False},
                "blockers": blockers,
                "task": task_definition,
                "security_policy": "no findings accepted; no activation performed",
            }
        await adapter.start(request)
        result_event = None
        event_count = 0
        async for event in adapter.events(request.run_id):
            event_count += 1
            if event.result is not None or event.error is not None:
                result_event = event
        statuses["coding_task"] = (
            "PASS"
            if result_event and result_event.result and result_event.result.success
            else "FAILED_MODEL_EXECUTION"
        )
        if statuses["coding_task"] != "PASS":
            blockers.append("live_coding_task_failed")
        if exercise_lifecycle:
            blockers.append("lifecycle_exercise_requires_dedicated_long_running_task_profile")
    except Exception as exc:  # runtime errors remain evidence, never activation
        statuses["coding_task"] = "FAILED_CERTIFICATION_IMPLEMENTATION"
        blockers.append(f"runtime:{type(exc).__name__}")
    finally:
        await adapter.close()
    statuses["zero_residue"] = "PASS" if not adapter._mcp_by_run else "FAILED_CLEANUP"
    if statuses["zero_residue"] != "PASS":
        blockers.append("run_scoped_mcp_grant_residue")
    return {
        "schema_version": SCHEMA,
        "status": _final_status(statuses, blockers),
        "candidate": {"release": verification.release, "commit_sha": verification.commit_sha, "image_digest": verification.image_digest},
        "worker_activation": "INACTIVE",
        "certification_authorization": {
            "status": "AUTHORIZED" if authorization is not None else "NOT_REQUIRED",
            "controller_run_id": controller_run_id if authorization is not None else None,
            "candidate_commit": verification.commit_sha if authorization is not None else None,
            "image_digest": verification.image_digest if authorization is not None else None,
            "sandbox_profile": sandbox_profile.lower() if authorization is not None else None,
            "sandbox_runtime": sandbox_runtime.lower() if authorization is not None else None,
        },
        "activation_approval": {"status": "PENDING", "required": True},
        "gates": statuses,
        "events": {"count": event_count, "payloads_retained": False},
        "cleanup": {"status": statuses["zero_residue"], "payloads_retained": False},
        "blockers": blockers,
        "task": task_definition,
        "security_policy": "certification authorization never implies activation approval; no activation performed",
    }


async def _cleanup_preconfigured_mcp(*, base_url: str, settings_key: str, session_key: str) -> dict[str, Any]:
    """Delete and verify a workflow-created MCP entry without retaining its grant."""

    if not base_url or not settings_key or not session_key:
        return {"status": "NOT_RUN", "reason": "cleanup_inputs_missing"}
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={"X-Session-API-Key": session_key, "Accept": "application/json"},
        timeout=httpx.Timeout(30.0, connect=10.0),
    ) as client:
        response = await client.delete(f"/api/settings/mcp/{settings_key}")
        if response.status_code not in {200, 404}:
            return {"status": "BLOCKED_CLEANUP", "reason": f"delete_http_{response.status_code}"}
        readback = await client.get("/api/settings")
        if readback.status_code >= 400:
            return {"status": "BLOCKED_CLEANUP", "reason": f"readback_http_{readback.status_code}"}
        payload = readback.json() if readback.content else {}
        config = payload.get("mcp_config") if isinstance(payload, dict) else None
        if not isinstance(config, dict):
            config = payload.get("mcp_servers") if isinstance(payload, dict) else None
        present = isinstance(config, dict) and settings_key in config
        return {
            "status": "BLOCKED_CLEANUP" if present else "PASS",
            "delete": "deleted" if response.status_code == 200 else "already_absent",
            "verified_absent": not present,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("OPENHANDS_AGENT_SERVER_URL", ""))
    parser.add_argument("--interface-report", type=Path, required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exercise-lifecycle", action="store_true")
    parser.add_argument("--task-spec", type=Path)
    args = parser.parse_args(argv)
    if not args.base_url:
        report = {
            "schema_version": SCHEMA,
            "status": "BLOCKED_OPERATOR_CONFIGURATION",
            "blockers": ["agent_server_url_missing"],
            "worker_activation": "INACTIVE",
        }
    else:
        report = asyncio.run(
            certify(
                base_url=args.base_url,
                interface_report=args.interface_report,
                workspace=args.workspace,
                exercise_lifecycle=args.exercise_lifecycle,
                task_spec=args.task_spec,
            )
        )
    if os.getenv("OPENHANDS_MCP_PRECONFIGURED") == "1":
        cleanup = asyncio.run(
            _cleanup_preconfigured_mcp(
                base_url=args.base_url,
                settings_key=os.getenv("OPENHANDS_MCP_SETTINGS_KEY", ""),
                session_key=os.getenv("OPENHANDS_SESSION_API_KEY", ""),
            )
        )
        report.setdefault("cleanup", {})["preconfigured_mcp"] = cleanup
        if cleanup.get("status") != "PASS":
            report.setdefault("blockers", []).append("run_scoped_mcp_cleanup_failed")
            report["status"] = "BLOCKED_CLEANUP"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report.get("status"), "blockers": report.get("blockers", [])}, sort_keys=True))
    return 0 if report.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
