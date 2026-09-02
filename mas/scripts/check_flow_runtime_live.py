"""Exercise the live flow-instance execution boundary with disposable fixtures.

The live certificate uses the real orchestrator API and storage owner.  It
creates two short-lived flow definitions and three short-lived projects,
drives parallel fan-out/join and switch routing, then exercises cancellation,
timeout/escalation, and safe retry.  Every project and flow is deleted in a
finally block and the final report retains only scalar case/cleanup status.

This is not a native watchdog, worker-canary, cold-crash, provider, sandbox,
or resource-limit certificate.  Those boundaries remain separate release
gates.  Licence/restriction metadata is informational only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

CHECK_SCHEMA = "aiat.flow-runtime-live.v1"
DEFAULT_URL = "http://127.0.0.1:8000"
REQUEST_TIMEOUT_SECONDS = 15.0
FIXTURE_PREFIX = "aiat-release-flow-live"


class FlowRuntimeFailure(RuntimeError):
    """A bounded functional mismatch in the live flow contract."""


class FlowRuntimeApi:
    """Small authenticated client that never retains response payloads."""

    def __init__(self, *, url: str, api_key: str, timeout: float) -> None:
        self.client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=timeout,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        try:
            response = await self.client.request(method, path, json=body)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise FlowRuntimeFailure(
                f"infrastructure/environment failure: {type(exc).__name__}"
            ) from exc
        except httpx.HTTPError as exc:
            raise FlowRuntimeFailure(
                f"infrastructure/environment failure: {type(exc).__name__}"
            ) from exc
        if response.status_code not in expected:
            # Do not include the response body: it may contain credentials,
            # payloads, SQL details, or project data.
            if response.status_code in {401, 403, 422}:
                classification = "harness/configuration failure"
            elif response.status_code >= 500:
                classification = "infrastructure/environment failure"
            else:
                classification = "provider functional failure"
            raise FlowRuntimeFailure(f"{classification}: HTTP {response.status_code} at {path}")
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise FlowRuntimeFailure(
                f"harness/configuration failure: malformed JSON at {path}"
            ) from exc


def _traversal_definition() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "fanout", "type": "parallel", "config": {"branches": ["branch_a", "branch_b"]}},
            {"id": "branch_a", "type": "task", "config": {"action": "fixture-a"}},
            {"id": "branch_b", "type": "task", "config": {"action": "fixture-b"}},
            {"id": "join", "type": "join", "config": {}},
            {
                "id": "switch",
                "type": "switch",
                "config": {"switch_key": "result", "switch_cases": {"ok": "ok", "fail": "fail"}},
            },
            {"id": "ok", "type": "task", "config": {"action": "fixture-ok"}},
            {"id": "fail", "type": "task", "config": {"action": "fixture-fail"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "start-fanout", "source": "start", "target": "fanout"},
            {"id": "fanout-a", "source": "fanout", "target": "branch_a"},
            {"id": "fanout-b", "source": "fanout", "target": "branch_b"},
            {"id": "a-join", "source": "branch_a", "target": "join"},
            {"id": "b-join", "source": "branch_b", "target": "join"},
            {"id": "join-switch", "source": "join", "target": "switch"},
            {"id": "switch-ok", "source": "switch", "target": "ok"},
            {"id": "switch-fail", "source": "switch", "target": "fail"},
            {"id": "ok-end", "source": "ok", "target": "end"},
            {"id": "fail-end", "source": "fail", "target": "end"},
        ],
    }


def _recovery_definition() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {
                "id": "escalating_task",
                "type": "task",
                "config": {
                    "action": "fixture-timeout",
                    "escalate_to_team": "exec_coo",
                },
            },
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "start-task", "source": "start", "target": "escalating_task"},
            {"id": "task-end", "source": "escalating_task", "target": "end"},
        ],
    }


def _fixture_report() -> dict[str, Any]:
    cases = {
        "parallel_fanout": "pass",
        "join_waits_for_one_branch": "pass",
        "join_scheduled_once": "pass",
        "switch_routes_selected_case": "pass",
        "completed_flow_reaches_terminal": "pass",
        "cancel_transitions_to_terminal": "pass",
        "timeout_records_escalation": "pass",
        "safe_retry_restores_last_safe_node": "pass",
    }
    return _base_report(
        mode="fixture",
        status="pass",
        cases=cases,
        case_count=len(cases),
        passed_case_count=len(cases),
        failed_case_count=0,
        cleanup_verified=False,
        mutation_performed=False,
        external_network_access_performed=False,
        external_provider_mutation_performed=False,
        reason="deterministic contract shape; live API was not requested",
    )


def _base_report(*, mode: str, status: str, **details: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "mode": mode,
        "status": status,
        "payload_free": True,
        "secret_free": True,
        "licence_metadata_is_gate": False,
        "mutation_performed": mode == "live",
        "external_network_access_performed": mode == "live",
        "external_provider_mutation_performed": False,
        "native_watchdog_status": "not_checked",
        "cold_crash_recovery_status": "not_checked",
        "failure_classification": {
            "harness_configuration_failure": "not_observed",
            "provider_functional_failure": "not_observed",
            "provider_resource_limit_failure": "not_checked",
            "infrastructure_environment_failure": "not_observed",
        },
    }
    report.update(details)
    return report


def _blocked(reason: str, *, classification: str) -> dict[str, Any]:
    report = _base_report(mode="live", status="blocked", reason=reason)
    report["failure_classification"][classification] = reason
    return report


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FlowRuntimeFailure(f"harness/configuration failure: {label} response is not an object")
    return value


def _id(value: dict[str, Any], label: str) -> str:
    identifier = str(value.get("id") or "").strip()
    if not identifier:
        raise FlowRuntimeFailure(f"harness/configuration failure: {label} response has no id")
    return identifier


def _active(value: dict[str, Any]) -> set[str]:
    raw = value.get("active_node_ids")
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise FlowRuntimeFailure("harness/configuration failure: malformed active_node_ids")
    return set(raw)


def _status(value: dict[str, Any], expected: str) -> None:
    observed = str(value.get("status") or "")
    if observed != expected:
        raise FlowRuntimeFailure(
            f"provider functional failure: expected flow status {expected}, observed {observed or '<missing>'}"
        )


def _case(
    cases: dict[str, str],
    name: str,
    passed: bool,
    *,
    observed: Any = None,
    expected: Any = None,
) -> None:
    cases[name] = "pass" if passed else "fail"
    if not passed:
        detail = ""
        if observed is not None or expected is not None:
            detail = f" (observed={observed!r}, expected={expected!r})"
        raise FlowRuntimeFailure(f"provider functional failure: case {name} failed{detail}")


async def _create_flow(api: FlowRuntimeApi, definition: dict[str, Any], label: str) -> str:
    payload = await api.request(
        "POST",
        "/flows",
        body={
            "name": f"{FIXTURE_PREFIX}-{label}-{uuid4().hex[:10]}",
            "description": "bounded disposable release-gate flow fixture",
            "definition_json": definition,
            "created_by": "release-gate-fixture",
            "is_active": False,
        },
        expected=(201,),
    )
    return _id(_require_mapping(payload, "flow create"), "flow create")


async def _create_project(api: FlowRuntimeApi, flow_id: str, label: str) -> str:
    payload = await api.request(
        "POST",
        "/projects",
        body={
            "name": f"{FIXTURE_PREFIX}-{label}-{uuid4().hex[:10]}",
            "description": "bounded disposable release-gate project fixture",
            "human_requester": "release-gate-fixture",
            "flow_id": flow_id,
            "workspace": {"mode": "none"},
            "config": {"release_gate_fixture": FIXTURE_PREFIX},
        },
        expected=(201,),
    )
    return _id(_require_mapping(payload, "project create"), "project create")


async def _instance_for_project(api: FlowRuntimeApi, project_id: str) -> dict[str, Any]:
    payload = await api.request("GET", f"/projects/{project_id}/flow-instance")
    instance = _require_mapping(payload, "flow instance")
    _id(instance, "flow instance")
    return instance


async def _action(api: FlowRuntimeApi, instance_id: str, action: str) -> dict[str, Any]:
    payload = await api.request(
        "POST",
        f"/flows/instances/{instance_id}/action",
        body={"action": action},
    )
    return _require_mapping(payload, f"instance {action}")


async def _node_action(
    api: FlowRuntimeApi,
    instance_id: str,
    node_id: str,
    action: str = "complete",
    *,
    output: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"node_id": node_id, "action": action}
    if output is not None:
        body["output"] = output
    if error is not None:
        body["error"] = error
    payload = await api.request(
        "POST",
        f"/flows/instances/{instance_id}/node-action",
        body=body,
    )
    return _require_mapping(payload, f"node {node_id} {action}")


async def _executions(api: FlowRuntimeApi, instance_id: str) -> list[dict[str, Any]]:
    payload = await api.request("GET", f"/flows/instances/{instance_id}/executions")
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise FlowRuntimeFailure("harness/configuration failure: malformed execution history")
    return payload


async def _delete(api: FlowRuntimeApi, path: str) -> bool:
    try:
        await api.request("DELETE", path, expected=(200, 204, 404))
        return True
    except FlowRuntimeFailure:
        return False


async def _gone(api: FlowRuntimeApi, path: str) -> bool:
    try:
        await api.request("GET", path, expected=(404,))
    except FlowRuntimeFailure:
        return False
    return True


async def _run_live(*, url: str, api_key: str, timeout: float) -> dict[str, Any]:
    api = FlowRuntimeApi(url=url, api_key=api_key, timeout=timeout)
    cases: dict[str, str] = {}
    flow_ids: list[str] = []
    project_ids: list[str] = []
    cleanup: dict[str, Any] = {
        "projects_deleted": 0,
        "flows_deleted": 0,
        "projects_remaining": 0,
        "flows_remaining": 0,
    }
    failure: FlowRuntimeFailure | None = None
    try:
        traversal_flow = await _create_flow(api, _traversal_definition(), "traversal")
        flow_ids.append(traversal_flow)
        traversal_project = await _create_project(api, traversal_flow, "traversal")
        project_ids.append(traversal_project)
        traversal_instance = await _instance_for_project(api, traversal_project)
        traversal_instance_id = _id(traversal_instance, "traversal instance")

        current = await _action(api, traversal_instance_id, "start")
        _case(cases, "start_activates_start_node", _active(current) == {"start"})
        current = await _node_action(api, traversal_instance_id, "start")
        _case(cases, "parallel_fanout", _active(current) == {"fanout"})
        current = await _node_action(api, traversal_instance_id, "fanout")
        _case(cases, "parallel_branches_activate", _active(current) == {"branch_a", "branch_b"})
        current = await _node_action(api, traversal_instance_id, "branch_a")
        _case(cases, "join_waits_for_one_branch", _active(current) == {"branch_b"})
        current = await _node_action(api, traversal_instance_id, "branch_b")
        executions = await _executions(api, traversal_instance_id)
        join_executions = [row for row in executions if row.get("node_id") == "join"]
        _case(cases, "join_scheduled_once", _active(current) == {"join"} and len(join_executions) == 1)
        current = await _node_action(api, traversal_instance_id, "join")
        _case(
            cases,
            "switch_activates",
            _active(current) == {"switch"},
            observed=sorted(_active(current)),
            expected=["switch"],
        )
        current = await _node_action(api, traversal_instance_id, "switch", output={"result": "ok"})
        _case(cases, "switch_routes_selected_case", _active(current) == {"ok"})
        current = await _node_action(api, traversal_instance_id, "ok")
        executions = await _executions(api, traversal_instance_id)
        end_executions = [row for row in executions if row.get("node_id") == "end"]
        _case(
            cases,
            "completed_flow_reaches_terminal",
            not _active(current)
            and current.get("status") == "COMPLETED"
            and len(end_executions) == 1
            and end_executions[0].get("status") == "COMPLETED",
        )

        cancel_project = await _create_project(api, traversal_flow, "cancel")
        project_ids.append(cancel_project)
        cancel_instance = await _instance_for_project(api, cancel_project)
        cancel_instance_id = _id(cancel_instance, "cancel instance")
        await _action(api, cancel_instance_id, "start")
        await _node_action(api, cancel_instance_id, "start")
        current = await _action(api, cancel_instance_id, "cancel")
        _case(cases, "cancel_transitions_to_terminal", current.get("status") == "CANCELLED" and not _active(current))
        current = await api.request("POST", f"/flows/instances/{cancel_instance_id}/retry")
        current = _require_mapping(current, "cancel retry")
        _case(cases, "safe_retry_restores_last_safe_node", current.get("status") == "RUNNING" and _active(current) == {"start"})

        recovery_flow = await _create_flow(api, _recovery_definition(), "recovery")
        flow_ids.append(recovery_flow)
        recovery_project = await _create_project(api, recovery_flow, "recovery")
        project_ids.append(recovery_project)
        recovery_instance = await _instance_for_project(api, recovery_project)
        recovery_instance_id = _id(recovery_instance, "recovery instance")
        await _action(api, recovery_instance_id, "start")
        await _node_action(api, recovery_instance_id, "start")
        current = await _node_action(
            api,
            recovery_instance_id,
            "escalating_task",
            action="timeout",
            error="bounded release-gate timeout fixture",
        )
        _case(
            cases,
            "timeout_records_escalation",
            current.get("status") == "FAILED"
            and current.get("escalated_to") == "exec_coo"
            and (current.get("context_json") or {}).get("last_timed_out_node_id") == "escalating_task",
        )
        current = await api.request("POST", f"/flows/instances/{recovery_instance_id}/retry")
        current = _require_mapping(current, "timeout retry")
        _case(cases, "timeout_safe_retry_reenters_flow", current.get("status") == "RUNNING" and _active(current) == {"start"})
    except FlowRuntimeFailure as exc:
        failure = exc
    finally:
        for project_id in reversed(project_ids):
            if await _delete(api, f"/projects/{project_id}"):
                cleanup["projects_deleted"] += 1
        for flow_id in reversed(flow_ids):
            if await _delete(api, f"/flows/{flow_id}"):
                cleanup["flows_deleted"] += 1
        for project_id in project_ids:
            if not await _gone(api, f"/projects/{project_id}"):
                cleanup["projects_remaining"] += 1
        for flow_id in flow_ids:
            if not await _gone(api, f"/flows/{flow_id}"):
                cleanup["flows_remaining"] += 1
        await api.close()

    cleanup_verified = (
        cleanup["projects_remaining"] == 0
        and cleanup["flows_remaining"] == 0
        and cleanup["projects_deleted"] == len(project_ids)
        and cleanup["flows_deleted"] == len(flow_ids)
    )
    if failure is not None:
        classification = "provider_functional_failure"
        message = str(failure)
        for name in (
            "harness/configuration failure",
            "infrastructure/environment failure",
            "provider functional failure",
        ):
            if message.startswith(name):
                classification = name.replace("/", "_").replace(" ", "_")
                break
        report = _base_report(
            mode="live",
            status="fail" if classification == "provider_functional_failure" else "blocked",
            reason=message,
            cases=cases,
            case_count=len(cases),
            passed_case_count=sum(value == "pass" for value in cases.values()),
            failed_case_count=sum(value == "fail" for value in cases.values()),
            cleanup=cleanup,
            cleanup_verified=cleanup_verified,
            failure_classification={
                "harness_configuration_failure": message if classification == "harness_configuration_failure" else "not_observed",
                "provider_functional_failure": message if classification == "provider_functional_failure" else "not_observed",
                "provider_resource_limit_failure": "not_checked",
                "infrastructure_environment_failure": message if classification == "infrastructure_environment_failure" else "not_observed",
            },
        )
        return report

    status = "pass" if cleanup_verified and len(cases) == sum(value == "pass" for value in cases.values()) else "fail"
    return _base_report(
        mode="live",
        status=status,
        cases=cases,
        case_count=len(cases),
        passed_case_count=sum(value == "pass" for value in cases.values()),
        failed_case_count=sum(value == "fail" for value in cases.values()),
        cleanup=cleanup,
        cleanup_verified=cleanup_verified,
        flow_fixture_count=len(flow_ids),
        project_fixture_count=len(project_ids),
    )


def _write_evidence(path: str, report: dict[str, Any]) -> None:
    evidence = dict(report)
    evidence["observed_at"] = datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")
    evidence["evidence_commit"] = _git_revision()
    Path(path).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_revision() -> str:
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unavailable"
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable evidence")
    parser.add_argument("--live", action="store_true", help="run the disposable API-backed certificate")
    parser.add_argument("--confirm", action="store_true", help="authorize disposable project/flow mutations")
    parser.add_argument(
        "--url",
        default=os.getenv("AIAT_FLOW_RUNTIME_URL", os.getenv("AIAT_ORCHESTRATOR_URL", DEFAULT_URL)),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIAT_OPERATOR_API_KEY", os.getenv("AIAT_API_KEY", os.getenv("MAS_API_KEY", ""))),
        help="operator bearer key (AIAT_OPERATOR_API_KEY/AIAT_API_KEY/MAS_API_KEY); never included in the report",
    )
    parser.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT_SECONDS)
    parser.add_argument("--evidence-output", help="write the scalar live certificate to this path")
    args = parser.parse_args(argv)

    if not args.live:
        report = _fixture_report()
    elif not args.confirm:
        report = _blocked(
            "live flow certificate requires explicit --confirm",
            classification="harness_configuration_failure",
        )
    elif not str(args.url).strip() or not str(args.api_key).strip():
        report = _blocked(
            "live flow certificate requires an orchestrator URL and API key",
            classification="harness_configuration_failure",
        )
    else:
        try:
            report = asyncio.run(
                _run_live(
                    url=str(args.url),
                    api_key=str(args.api_key),
                    timeout=max(1.0, min(float(args.timeout), 60.0)),
                )
            )
        except (ValueError, OSError) as exc:
            report = _blocked(
                f"harness/configuration failure: invalid live checker configuration ({type(exc).__name__})",
                classification="harness_configuration_failure",
            )

    if args.evidence_output:
        _write_evidence(args.evidence_output, report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"flow runtime live: {report['status']}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    sys.exit(main())
