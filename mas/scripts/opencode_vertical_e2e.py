#!/usr/bin/env python3
"""Drive the disposable Level A project-to-OpenCode API journey.

The caller must provision an isolated development/test stack whose only model
endpoint is a deterministic local fixture. This driver uses the public AIAT
HTTP APIs; it never reads or writes the database directly and never prints
credentials, prompts, model responses, tool arguments, or tool responses.

Required environment:
  AIAT_LEVEL_A_API_URL              loopback URL for the isolated API
  AIAT_LEVEL_A_OPERATOR_API_KEY      disposable operator credential
  AIAT_LEVEL_A_WORKER_ID            UUID of the normally governed worker
  AIAT_LEVEL_A_PROVIDER_BOUNDARY    exactly ``local_fixture``

It exits 0 only when every required execution stage (including project
completion when requested) passes, 2 when a stage is honestly blocked or not
run, and 1 on a product/integration failure.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

EXPECTED_FILES = {
    "expenses.py": ("category", "total"),
    "sample_expenses.csv": ("category", "amount"),
    "test_expenses.py": ("test_",),
    "README.md": ("expense",),
}
REQUIRED_STAGES = (
    "PROJECT_CREATED",
    "WORKER_SELECTED",
    "WORKER_RUN_SUBMITTED",
    "HOST_RESERVED",
    "HOST_CLAIMED",
    "HOST_CONSUMER_EXECUTED",
    "RUNTIME_STARTED",
    "MODEL_REQUEST_RESOLVED",
    "LOCAL_MODEL_RESPONSE_RECEIVED",
    "TOOL_SERVICE_CALLED",
    "TOOL_EFFECT_PERSISTED",
    "FILE_GENERATED",
    "ARTIFACT_REGISTERED",
    "WORKER_RUN_SETTLED",
    "EVIDENCE_AVAILABLE",
    "PROJECT_COMPLETED",
    "CLEANUP_VERIFIED",
)
TOOL_GRANTS = [
    "opencode.workspace_read",
    "opencode.workspace_write",
    "opencode.workspace_pytest",
]
PERMISSION_REQUIREMENTS = ["repository.read", "repository.write", "tests.execute"]
LOCAL_FIXTURE_PROMPT = "AIAT_LEVEL_A_WORKSPACE_TOOL_LOOP"
TERMINAL_RUN_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}
MAX_RUN_BUDGET_USD = Decimal("0.25")


class APIError(RuntimeError):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def _stage(
    stages: dict[str, dict[str, Any]],
    name: str,
    status: str,
    *,
    reason: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    item: dict[str, Any] = {"status": status}
    if reason:
        item["reason"] = reason
    if evidence:
        item["evidence"] = evidence
    stages[name] = item


def _stage_not_reached(stages: dict[str, dict[str, Any]], name: str) -> bool:
    item = stages.get(name)
    return (
        isinstance(item, dict)
        and item.get("status") == "NOT_RUN"
        and item.get("reason") == "STAGE_NOT_REACHED"
    )


def _host_binding_has_reservation_history(binding: Any) -> bool:
    if not isinstance(binding, dict):
        return False

    binding_state = str(binding.get("state") or "").upper()
    reservation_state = str(binding.get("reservation_state") or "").upper()
    committed = bool(binding.get("committed_at"))
    released = bool(binding.get("released_at"))
    host_fields_present = all(
        binding.get(key)
        for key in ("id", "reservation_id", "host_uuid", "host_id", "owner")
    )
    return host_fields_present and committed and (
        (
            binding_state == "COMMITTED"
            and reservation_state in {"ACTIVE", "RESERVED", "COMMITTED"}
        )
        or (
            binding_state == "RELEASED"
            and reservation_state in {"RELEASED", "EXPIRED"}
            and released
        )
    )


def _host_consumer_execution_evidence(
    binding: Any,
    transitions: Any,
    *,
    run_id: str,
    expected_state: str,
    host_health: Any = None,
) -> tuple[str, str | None, dict[str, Any]]:
    """Classify persisted API readback for a host-consumer execution.

    Compose configuration and local health are not execution evidence. A
    consumer pass requires the run's durable host/reservation binding, a
    host-owner CLAIMED transition, and the canonical WorkerRunController
    DISPATCHING -> RUNNING transition, in that order. The local host health
    summary is volatile, so it is recorded only as optional corroboration.
    """

    if not isinstance(binding, dict):
        return "FAIL", "HOST_BINDING_NOT_FOUND", {}

    binding_state = str(binding.get("state") or "").upper()
    reservation_state = str(binding.get("reservation_state") or "").upper()
    evidence: dict[str, Any] = {
        "host_binding_id": binding.get("id"),
        "host_id": binding.get("host_id"),
        "reservation_id": binding.get("reservation_id"),
        "binding_state": binding_state or "UNKNOWN",
        "reservation_state": reservation_state or "UNKNOWN",
    }
    if not _host_binding_has_reservation_history(binding):
        return "FAIL", "DURABLE_HOST_RESERVATION_NOT_FOUND", evidence

    rows = transitions if isinstance(transitions, list) else []
    claim_index: int | None = None
    dispatch_index: int | None = None
    claim_row: dict[str, Any] | None = None
    dispatch_row: dict[str, Any] | None = None
    running_row: dict[str, Any] | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        from_state = str(row.get("from_state") or "").upper()
        to_state = str(row.get("to_state") or "").upper()
        actor = str(row.get("actor") or "")
        expected_host_actor = f"aiat-local-host:{binding.get('host_id')}"
        if (
            to_state == "CLAIMED"
            and from_state == "QUEUED"
            and actor == expected_host_actor
            and actor == str(binding.get("owner") or "")
        ):
            claim_index = index
            claim_row = row
        if (
            to_state == "DISPATCHING"
            and from_state == "READY"
            and actor == "worker-run-controller"
            and claim_index is not None
            and index > claim_index
        ):
            dispatch_index = index
            dispatch_row = row
        if (
            to_state == "RUNNING"
            and from_state == "DISPATCHING"
            and actor == "worker-run-controller"
            and dispatch_index is not None
            and index > dispatch_index
        ):
            running_row = row

    if claim_row is None:
        return "FAIL", "HOST_CLAIM_TRANSITION_NOT_FOUND", evidence
    if dispatch_row is None:
        evidence["claim_transition_id"] = claim_row.get("id")
        return "FAIL", "CANONICAL_DISPATCHING_TRANSITION_NOT_FOUND", evidence
    if running_row is None:
        evidence.update(
            {
                "claim_transition_id": claim_row.get("id"),
                "dispatch_transition_id": dispatch_row.get("id"),
            }
        )
        return "FAIL", "CANONICAL_RUNNING_TRANSITION_NOT_FOUND", evidence

    health_corroboration = "NOT_OBSERVED"
    if isinstance(host_health, dict):
        health_host_id = str(host_health.get("host_id") or "")
        summaries = host_health.get("last_runs")
        matching_summaries = [
            row
            for row in summaries
            if isinstance(row, dict) and str(row.get("run_id") or "") == run_id
        ] if isinstance(summaries, list) else []
        if health_host_id and matching_summaries:
            health_corroboration = (
                "MATCHED"
                if health_host_id == str(binding.get("host_id") or "")
                and any(
                    str(row.get("state") or "").upper() == expected_state.upper()
                    for row in matching_summaries
                )
                else "MISMATCHED"
            )
        elif health_host_id and health_host_id != str(binding.get("host_id") or ""):
            health_corroboration = "MISMATCHED"

    evidence.update(
        {
            "claim_transition_id": claim_row.get("id"),
            "dispatch_transition_id": dispatch_row.get("id"),
            "running_transition_id": running_row.get("id"),
            "running_transition_actor": "worker-run-controller",
            "claim_preceded_running": True,
            "host_health_corroboration": health_corroboration,
        }
    )
    return "PASS", None, evidence


def _project_evidence_readiness(
    evidence: Any,
    *,
    project_id: str,
    artifact_ids: set[str],
) -> tuple[bool, str, dict[str, Any]]:
    """Require a canonical complete package linked to the downloaded artifacts."""

    if not isinstance(evidence, dict):
        return False, "PROJECT_EVIDENCE_INVALID", {}
    refs = evidence.get("evidence_refs")
    artifact_refs = refs.get("artifacts") if isinstance(refs, dict) else None
    normalized_refs = (
        {str(value) for value in artifact_refs if value is not None}
        if isinstance(artifact_refs, list)
        else set()
    )
    checks = evidence.get("checks")
    required_checks_pass = isinstance(checks, list) and bool(checks) and all(
        isinstance(check, dict)
        and (
            check.get("required") is not True
            or check.get("passed") is True
        )
        for check in checks
    )
    summary = {
        "schema_version": evidence.get("schema_version"),
        "policy_id": evidence.get("policy_id"),
        "status": evidence.get("status"),
        "artifact_ref_count": len(normalized_refs),
        "required_checks_pass": required_checks_pass,
    }
    if evidence.get("schema_version") != "aiat.project-evidence-package.v1":
        return False, "PROJECT_EVIDENCE_SCHEMA_INVALID", summary
    if str(evidence.get("project_id") or "") != project_id:
        return False, "PROJECT_EVIDENCE_SCOPE_MISMATCH", summary
    if evidence.get("status") != "complete" or not required_checks_pass:
        return False, "PROJECT_EVIDENCE_POLICY_INCOMPLETE", summary
    if not artifact_ids or not artifact_ids.issubset(normalized_refs):
        return False, "PROJECT_ARTIFACTS_NOT_LINKED_IN_EVIDENCE", summary
    return True, "", summary


def _canonical_worker_readiness(
    api: API,
    *,
    worker_id: str,
    project_id: str,
) -> dict[str, Any]:
    """Read public control-plane projections through AIAT's readiness evaluator.

    There is no readiness API endpoint today. Reuse the same canonical
    evaluator as ``check_worker_run_readiness.py`` rather than duplicating its
    admission predicate or probing the mutating WorkerRun dispatch endpoint.
    """

    from mas_core.worker_registry.worker_run_readiness import evaluate_worker_run_readiness

    fetch_errors: dict[str, str] = {}

    def read(path: str, key: str) -> Any:
        try:
            _, _, payload, _ = api.request("GET", path)
            return payload
        except APIError as exc:
            fetch_errors[key] = exc.code
            return None

    worker_rows = read("/capabilities/workers", "worker")
    worker = next(
        (
            row
            for row in worker_rows
            if isinstance(row, dict) and str(row.get("id") or "") == worker_id
        ),
        None,
    ) if isinstance(worker_rows, list) else None
    if worker_rows is not None and not isinstance(worker_rows, list):
        fetch_errors["worker"] = "invalid_collection"

    project = read(f"/projects/{urllib.parse.quote(project_id)}", "project")
    if not isinstance(project, dict):
        project = None
    company_id = str(project.get("company_id") or "") if project else ""

    company: Any = None
    assignments: Any = []
    budgets: Any = []
    if company_id:
        company_payload = read(
            f"/companies/{urllib.parse.quote(company_id)}", "company"
        )
        company = (
            company_payload.get("company")
            if isinstance(company_payload, dict)
            else None
        )
        assignments = read(
            f"/companies/{urllib.parse.quote(company_id)}/assignments", "assignments"
        )
        budgets = read(
            f"/companies/{urllib.parse.quote(company_id)}/budgets", "budgets"
        )
    else:
        fetch_errors["company"] = "project_company_id_missing"
        fetch_errors["assignments"] = "project_company_id_missing"
        fetch_errors["budgets"] = "project_company_id_missing"

    model_profiles = read("/model-profiles", "model_profiles")
    worker_health = read(
        f"/capabilities/workers/{urllib.parse.quote(worker_id)}/health",
        "worker_health",
    )
    if not isinstance(assignments, list) and "assignments" not in fetch_errors:
        fetch_errors["assignments"] = "invalid_collection"
    if not isinstance(budgets, list) and "budgets" not in fetch_errors:
        fetch_errors["budgets"] = "invalid_collection"
    if not isinstance(model_profiles, list) and "model_profiles" not in fetch_errors:
        fetch_errors["model_profiles"] = "invalid_collection"
    readiness = evaluate_worker_run_readiness(
        worker=worker,
        project=project,
        company=company if isinstance(company, dict) else None,
        assignments=assignments if isinstance(assignments, list) else [],
        budgets=budgets if isinstance(budgets, list) else [],
        model_profiles=model_profiles if isinstance(model_profiles, list) else [],
        worker_id=worker_id,
        project_id=project_id,
        required_budget_usd=MAX_RUN_BUDGET_USD,
        require_sandbox=True,
        health=worker_health if isinstance(worker_health, dict) else None,
        fetch_errors=fetch_errors,
    )
    blockers = readiness.get("blockers")
    blocker_codes = [
        str(item["code"])
        for item in blockers
        if isinstance(item, dict)
        and isinstance(item.get("code"), str)
        and item["code"].replace("_", "").isalnum()
    ] if isinstance(blockers, list) else ["readiness_result_invalid"]
    checks = readiness.get("checks") if isinstance(readiness.get("checks"), dict) else {}
    worker_checks = checks.get("worker") if isinstance(checks.get("worker"), dict) else {}
    return {
        "schema_version": readiness.get("schema_version"),
        "status": readiness.get("status"),
        "blocker_codes": blocker_codes[:64],
        "worker_status": worker_checks.get("status"),
        "worker_evaluation_status": worker_checks.get("evaluation_status"),
        "immutable_pointers": worker_checks.get("immutable_pointers"),
        "project_state": (checks.get("project") or {}).get("state")
        if isinstance(checks.get("project"), dict)
        else None,
        "assignment_status": (checks.get("assignment") or {}).get("status")
        if isinstance(checks.get("assignment"), dict)
        else None,
        "sandbox": checks.get("sandbox"),
        "budget_status": checks.get("budgets"),
        "fetch_status": readiness.get("fetch_status"),
        "read_error_codes": dict(sorted(fetch_errors.items())[:16]),
    }


def _safe_error_code(status: int, body: bytes) -> str:
    """Extract only a stable reason code; never retain API error text."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        code = detail["code"]
        if code.replace("_", "").isalnum() and len(code) <= 80:
            return code.upper()
    if status == 409:
        return "GOVERNANCE_OR_STATE_BLOCKED"
    if status == 403:
        return "AUTHORITY_OR_ASSIGNMENT_BLOCKED"
    if status == 404:
        return "RESOURCE_NOT_FOUND"
    if status == 422:
        return "REQUEST_REJECTED"
    if status >= 500:
        return "SERVICE_UNAVAILABLE"
    return f"HTTP_{status}"


def _require_loopback(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("AIAT_LEVEL_A_API_URL must be an http loopback URL")
    host = parsed.hostname.lower()
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise ValueError("AIAT_LEVEL_A_API_URL must resolve directly to loopback")


class API:
    def __init__(self, base_url: str, key: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, bytes, Any, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "X-API-Key": self.key,
                "Authorization": f"Bearer {self.key}",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(25 * 1024 * 1024 + 1)
                if len(raw) > 25 * 1024 * 1024:
                    raise APIError(response.status, "RESPONSE_LIMIT_EXCEEDED")
                try:
                    decoded = json.loads(raw.decode("utf-8")) if raw else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    decoded = None
                return response.status, raw, decoded, response.headers
        except urllib.error.HTTPError as exc:
            raw = exc.read(64 * 1024)
            raise APIError(exc.code, _safe_error_code(exc.code, raw)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise APIError(0, "API_CONNECTION_FAILED") from None


def _verify_local_fixture_environment() -> tuple[API, str]:
    base_url = os.getenv("AIAT_LEVEL_A_API_URL", "").strip()
    api_key = os.getenv("AIAT_LEVEL_A_OPERATOR_API_KEY", "").strip()
    worker_id = os.getenv("AIAT_LEVEL_A_WORKER_ID", "").strip()
    boundary = os.getenv("AIAT_LEVEL_A_PROVIDER_BOUNDARY", "").strip()
    if not base_url or not api_key or not worker_id:
        raise ValueError(
            "AIAT_LEVEL_A_API_URL, AIAT_LEVEL_A_OPERATOR_API_KEY, and AIAT_LEVEL_A_WORKER_ID are required"
        )
    if boundary != "local_fixture":
        raise ValueError("provider boundary must be explicitly marked local_fixture")
    _require_loopback(base_url)
    timeout = max(1.0, min(float(os.getenv("AIAT_LEVEL_A_HTTP_TIMEOUT_SECONDS", "15")), 60.0))
    return API(base_url, api_key, timeout), worker_id


def _artifact_uri(row: dict[str, Any]) -> str:
    return str(row.get("uri") or row.get("path") or "").strip()


def _run_project_journey(
    *,
    api: API,
    worker_id: str,
    timeout_seconds: int,
    stages: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    project_id: str | None = None
    run_id: str | None = None
    project_key = f"level-a-opencode-{uuid4()}"
    project_payload = {
        "name": f"AIAT Level A disposable expense app {project_key[-8:]}",
        "description": "Disposable local integration task: create a CSV expense category-total module, test, sample, and README.",
        "start_immediately": False,
        "idempotency_key": project_key,
        "config": {"integration_profile": "opencode-level-a", "non_promotable": True},
    }
    try:
        status, _, project, _ = api.request("POST", "/projects", project_payload)
        if status != 201 or not isinstance(project, dict) or not project.get("id"):
            raise APIError(status, "PROJECT_CREATE_RESPONSE_INVALID")
        project_id = str(project["id"])
        _stage(
            stages,
            "PROJECT_CREATED",
            "PASS",
            evidence={"project_id": project_id, "state": project.get("state")},
        )
    except APIError as exc:
        _stage(stages, "PROJECT_CREATED", "FAIL", reason=exc.code)
        return project_id, run_id

    try:
        _, _, workers, _ = api.request("GET", "/capabilities/workers")
        worker = (
            next(
                (
                    item
                    for item in workers
                    if isinstance(item, dict)
                    and str(item.get("id") or item.get("worker_id") or "") == worker_id
                ),
                None,
            )
            if isinstance(workers, list)
            else None
        )
        if worker is None:
            _stage(stages, "WORKER_SELECTED", "BLOCKED", reason="WORKER_NOT_REGISTERED")
            return project_id, run_id
        worker_summary = {
            "worker_id": worker_id,
            "status": str(worker.get("status") or "UNKNOWN"),
            "adapter_type": str(
                worker.get("adapter_type") or worker.get("runtime_type") or "UNKNOWN"
            ),
            "evaluation_status": str(worker.get("evaluation_status") or "UNKNOWN"),
        }
        if worker_summary["adapter_type"].lower() != "opencode":
            _stage(
                stages,
                "WORKER_SELECTED",
                "BLOCKED",
                reason="WORKER_RUNTIME_IS_NOT_OPENCODE",
                evidence=worker_summary,
            )
            return project_id, run_id
        readiness = _canonical_worker_readiness(
            api,
            worker_id=worker_id,
            project_id=project_id,
        )
        worker_summary["readiness"] = readiness
        if readiness.get("status") != "pass":
            _stage(
                stages,
                "WORKER_SELECTED",
                "BLOCKED",
                reason="CANONICAL_WORKER_READINESS_BLOCKED",
                evidence=worker_summary,
            )
            return project_id, run_id
        _stage(stages, "WORKER_SELECTED", "PASS", evidence=worker_summary)
    except APIError as exc:
        _stage(stages, "WORKER_SELECTED", "FAIL", reason=exc.code)
        return project_id, run_id

    request_key = f"level-a-opencode-run-{uuid4()}"
    # The only configured model route is a local deterministic endpoint. Its
    # fixed scenario returns the bounded OpenAI-compatible tool calls for the
    # expense application; OpenCode still performs the real tool loop.
    prompt = LOCAL_FIXTURE_PROMPT
    dispatch = {
        "worker_id": worker_id,
        "idempotency_key": request_key,
        "task_type": "code_implementation",
        "task_input": {"prompt": prompt},
        "project_id": project_id,
        "tool_grants": TOOL_GRANTS,
        "permission_requirements": PERMISSION_REQUIREMENTS,
        "workspace_mode": "isolated",
        "timeout_seconds": timeout_seconds,
        "budget_usd": float(MAX_RUN_BUDGET_USD),
        "dispatch_mode": "queued",
        "queue_priority": 10,
        "lease_seconds": 300,
    }
    try:
        _, _, accepted, _ = api.request("POST", "/workers/runs", dispatch)
        if not isinstance(accepted, dict) or not accepted.get("run_id"):
            raise APIError(502, "WORKER_RUN_ACCEPTANCE_INVALID")
        run_id = str(accepted["run_id"])
        _stage(
            stages,
            "WORKER_RUN_SUBMITTED",
            "PASS",
            evidence={
                "run_id": run_id,
                "state": accepted.get("state"),
                "dispatch_mode": accepted.get("dispatch_mode"),
            },
        )
    except APIError as exc:
        state = "BLOCKED" if exc.status in {403, 409} else "FAIL"
        _stage(stages, "WORKER_RUN_SUBMITTED", state, reason=exc.code)
        return project_id, run_id

    deadline = time.monotonic() + timeout_seconds
    current: dict[str, Any] = {}
    run_poll_timed_out = False
    while time.monotonic() < deadline:
        try:
            _, _, current, _ = api.request("GET", f"/workers/runs/{urllib.parse.quote(run_id)}")
        except APIError as exc:
            _stage(stages, "WORKER_RUN_SETTLED", "FAIL", reason=exc.code)
            return project_id, run_id
        if not isinstance(current, dict):
            _stage(stages, "WORKER_RUN_SETTLED", "FAIL", reason="WORKER_RUN_RESPONSE_INVALID")
            return project_id, run_id
        state = str(current.get("state") or "UNKNOWN").upper()
        if state in TERMINAL_RUN_STATES:
            break
        time.sleep(1)
    else:
        _stage(stages, "WORKER_RUN_SETTLED", "FAIL", reason="WORKER_RUN_TIMEOUT")
        run_poll_timed_out = True

    run_state = str(current.get("state") or "UNKNOWN").upper()
    model_snapshot = current.get("model_resolution_snapshot_id")
    _stage(
        stages,
        "MODEL_REQUEST_RESOLVED",
        "PASS" if model_snapshot else "BLOCKED",
        reason=None if model_snapshot else "MODEL_RESOLUTION_SNAPSHOT_MISSING",
        evidence={"snapshot_id": str(model_snapshot)} if model_snapshot else None,
    )
    if run_poll_timed_out:
        _stage(
            stages,
            "WORKER_RUN_SETTLED",
            "FAIL",
            reason="WORKER_RUN_TIMEOUT",
            evidence={"run_id": run_id, "state": run_state},
        )
    elif run_state == "SUCCEEDED":
        _stage(
            stages, "WORKER_RUN_SETTLED", "PASS", evidence={"run_id": run_id, "state": run_state}
        )
    else:
        _stage(
            stages,
            "WORKER_RUN_SETTLED",
            "FAIL",
            reason=f"WORKER_RUN_{run_state}",
            evidence={"run_id": run_id, "state": run_state},
        )

    transition_states: set[str] = set()
    try:
        _, _, binding_response, _ = api.request(
            "GET", f"/workers/runs/{urllib.parse.quote(run_id)}/host-binding"
        )
        binding = (
            binding_response.get("host_binding") if isinstance(binding_response, dict) else None
        )
        _, _, transitions, _ = api.request(
            "GET", f"/workers/runs/{urllib.parse.quote(run_id)}/transitions"
        )
        transition_states = (
            {str(row.get("to_state") or "").upper() for row in transitions if isinstance(row, dict)}
            if isinstance(transitions, list)
            else set()
        )
        try:
            _, _, host_health, _ = api.request("GET", "/workers/host/local/health")
        except APIError:
            # last_runs is a volatile process summary. Durable binding and
            # transition evidence below remains authoritative when the status
            # endpoint is unavailable or the summary has already rolled over.
            host_health = None
        reserved = _host_binding_has_reservation_history(binding)
        if isinstance(binding, dict):
            _stage(
                stages,
                "HOST_RESERVED",
                "PASS" if reserved else "FAIL",
                reason=None if reserved else "DURABLE_HOST_RESERVATION_NOT_FOUND",
                evidence={
                    "host_binding_id": binding.get("id"),
                    "host_id": binding.get("host_id"),
                    "reservation_state": binding.get("reservation_state"),
                    "binding_state": binding.get("state"),
                },
            )
            claimed = reserved and any(
                isinstance(row, dict)
                and str(row.get("from_state") or "").upper() == "QUEUED"
                and str(row.get("to_state") or "").upper() == "CLAIMED"
                and str(row.get("actor") or "").startswith("aiat-local-host:")
                for row in transitions
            )
            _stage(
                stages,
                "HOST_CLAIMED",
                "PASS" if claimed else "FAIL",
                reason=None if claimed else "CLAIM_TRANSITION_NOT_FOUND",
                evidence={
                    "binding_state": binding.get("state"),
                    "observed_run_states": sorted(transition_states),
                },
            )
            execution_status, execution_reason, execution_evidence = (
                _host_consumer_execution_evidence(
                    binding,
                    transitions,
                    run_id=run_id,
                    expected_state=run_state,
                    host_health=host_health,
                )
            )
            _stage(
                stages,
                "HOST_CONSUMER_EXECUTED",
                execution_status,
                reason=execution_reason,
                evidence=execution_evidence,
            )
        else:
            _stage(stages, "HOST_RESERVED", "FAIL", reason="HOST_BINDING_NOT_COMMITTED")
            _stage(stages, "HOST_CLAIMED", "FAIL", reason="HOST_BINDING_NOT_COMMITTED")
            _stage(stages, "HOST_CONSUMER_EXECUTED", "FAIL", reason="HOST_BINDING_NOT_FOUND")
    except APIError as exc:
        _stage(stages, "HOST_RESERVED", "FAIL", reason=exc.code)
        _stage(stages, "HOST_CLAIMED", "FAIL", reason=exc.code)
        _stage(stages, "HOST_CONSUMER_EXECUTED", "FAIL", reason=exc.code)

    try:
        _, _, events, _ = api.request(
            "GET", f"/workers/runs/{urllib.parse.quote(run_id)}/events?limit=1000"
        )
        event_rows = events if isinstance(events, list) else []
        runtime_started = bool(
            transition_states.intersection(
                {"RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}
            )
        ) or any(
            isinstance(row, dict)
            and str(row.get("event_type") or "").lower() == "progress"
            and isinstance(row.get("event_json"), dict)
            and (row["event_json"].get("progress") or {}).get("phase")
            in {"session", "message", "tool", "artifact"}
            for row in event_rows
        )
        messages_seen = any(
            isinstance(row, dict)
            and isinstance(row.get("event_json"), dict)
            and (
                (row["event_json"].get("progress") or {}).get("phase") == "message"
                or (row["event_json"].get("extensions") or {})
                .get("event_type", "")
                .startswith("message.")
            )
            for row in event_rows
        )
        _stage(
            stages,
            "RUNTIME_STARTED",
            "PASS" if runtime_started else "FAIL",
            reason=None if runtime_started else "RUNTIME_ACCEPTANCE_EVENT_NOT_FOUND",
            evidence={"event_count": len(event_rows), "accepted_event_observed": runtime_started},
        )
        _stage(
            stages,
            "LOCAL_MODEL_RESPONSE_RECEIVED",
            "PASS" if messages_seen else "BLOCKED",
            reason=None if messages_seen else "NO_OPEN_CODE_MESSAGE_EVENT",
            evidence={"message_events_observed": messages_seen},
        )
    except APIError as exc:
        _stage(stages, "RUNTIME_STARTED", "FAIL", reason=exc.code)
        _stage(stages, "LOCAL_MODEL_RESPONSE_RECEIVED", "FAIL", reason=exc.code)

    try:
        _, _, effects_response, _ = api.request(
            "GET", f"/workers/runs/{urllib.parse.quote(run_id)}/tool-effects"
        )
        effect_rows = (
            effects_response.get("tool_effects", []) if isinstance(effects_response, dict) else []
        )
        effect_rows = effect_rows if isinstance(effect_rows, list) else []
        names = sorted({str(row.get("tool_name")) for row in effect_rows if isinstance(row, dict)})
        pytest_effects = [
            row
            for row in effect_rows
            if isinstance(row, dict)
            and row.get("tool_name") == "opencode.workspace_pytest"
            and row.get("state") == "COMPLETED"
            and row.get("response_success") is True
        ]
        completed_effects = [
            row for row in effect_rows if isinstance(row, dict) and row.get("state") == "COMPLETED"
        ]
        _stage(
            stages,
            "TOOL_SERVICE_CALLED",
            "PASS" if effect_rows else "FAIL",
            reason=None if effect_rows else "NO_DURABLE_TOOL_EFFECTS",
            evidence={"effect_count": len(effect_rows), "tool_names": names},
        )
        effects_ok = bool(completed_effects) and bool(pytest_effects)
        _stage(
            stages,
            "TOOL_EFFECT_PERSISTED",
            "PASS" if effects_ok else "FAIL",
            reason=None if effects_ok else "PYTEST_EFFECT_NOT_COMPLETED",
            evidence={
                "completed_effect_count": len(completed_effects),
                "pytest_effect_count": len(pytest_effects),
            },
        )
    except APIError as exc:
        effect_rows = []
        _stage(stages, "TOOL_SERVICE_CALLED", "FAIL", reason=exc.code)
        _stage(stages, "TOOL_EFFECT_PERSISTED", "FAIL", reason=exc.code)

    artifact_content: dict[str, bytes] = {}
    verified_artifact_ids: set[str] = set()
    try:
        _, _, artifacts, _ = api.request(
            "GET", f"/workers/runs/{urllib.parse.quote(run_id)}/artifacts"
        )
        artifact_rows = artifacts if isinstance(artifacts, list) else []
        artifact_summary = []
        for artifact in artifact_rows:
            if not isinstance(artifact, dict) or artifact.get("artifact_id") is None:
                continue
            uri = _artifact_uri(artifact)
            artifact_id = artifact.get("artifact_id")
            safe_id = urllib.parse.quote(str(artifact_id), safe="")
            try:
                _, data, _, headers = api.request(
                    "GET",
                    f"/projects/{urllib.parse.quote(project_id)}/artifacts/{safe_id}/download",
                )
            except APIError:
                continue
            digest = hashlib.sha256(data).hexdigest()
            expected_digest = str(
                headers.get("X-AIAT-Artifact-SHA256") or artifact.get("sha256") or ""
            ).lower()
            if expected_digest and digest != expected_digest:
                continue
            name = Path(uri).name
            artifact_content[name] = data
            verified_artifact_ids.add(str(artifact_id))
            artifact_summary.append(
                {"artifact_id": artifact_id, "uri": uri, "sha256": digest, "size_bytes": len(data)}
            )
        required_names = set(EXPECTED_FILES)
        valid_files = required_names.issubset(artifact_content)
        content_valid = valid_files and all(
            all(
                marker.lower() in artifact_content[name].decode("utf-8", errors="replace").lower()
                for marker in markers
            )
            for name, markers in EXPECTED_FILES.items()
        )
        _stage(
            stages,
            "FILE_GENERATED",
            "PASS" if content_valid else "FAIL",
            reason=None if content_valid else "EXPECTED_ARTIFACT_CONTENT_MISSING",
            evidence={"files": sorted(artifact_content), "required_files": sorted(required_names)},
        )
        _stage(
            stages,
            "ARTIFACT_REGISTERED",
            "PASS" if content_valid else "FAIL",
            reason=None if content_valid else "ARTIFACT_READBACK_INCOMPLETE",
            evidence={"artifacts": artifact_summary},
        )
    except APIError as exc:
        artifact_rows = []
        _stage(stages, "FILE_GENERATED", "FAIL", reason=exc.code)
        _stage(stages, "ARTIFACT_REGISTERED", "FAIL", reason=exc.code)

    try:
        _, _, evidence, _ = api.request(
            "GET", f"/projects/{urllib.parse.quote(project_id)}/evidence/package"
        )
        evidence_ok, evidence_reason, evidence_summary = _project_evidence_readiness(
            evidence,
            project_id=project_id,
            artifact_ids=verified_artifact_ids,
        )
        _stage(
            stages,
            "EVIDENCE_AVAILABLE",
            "PASS" if evidence_ok else "FAIL",
            reason=None if evidence_ok else evidence_reason,
            evidence=evidence_summary,
        )
    except APIError as exc:
        _stage(stages, "EVIDENCE_AVAILABLE", "FAIL", reason=exc.code)

    try:
        _, _, final_project, _ = api.request("GET", f"/projects/{urllib.parse.quote(project_id)}")
        final_state = (
            str(final_project.get("state") or "UNKNOWN").upper()
            if isinstance(final_project, dict)
            else "UNKNOWN"
        )
        completion_dependencies = (
            "WORKER_RUN_SETTLED",
            "TOOL_EFFECT_PERSISTED",
            "FILE_GENERATED",
            "ARTIFACT_REGISTERED",
            "EVIDENCE_AVAILABLE",
        )
        evidence_chain_passed = all(
            stages.get(name, {}).get("status") == "PASS"
            for name in completion_dependencies
        )
        completed = final_state == "COMPLETED" and evidence_chain_passed
        _stage(
            stages,
            "PROJECT_COMPLETED",
            "PASS" if completed else "BLOCKED",
            reason=None
            if completed
            else "PROJECT_WORKFLOW_COMPLETION_REQUIRES_ITS_GOVERNED_MILESTONES"
            if final_state != "COMPLETED"
            else "PROJECT_COMPLETION_EVIDENCE_CHAIN_INCOMPLETE",
            evidence={"state": final_state, "evidence_chain_passed": evidence_chain_passed},
        )
    except APIError as exc:
        _stage(stages, "PROJECT_COMPLETED", "FAIL", reason=exc.code)
    return project_id, run_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=int, default=420)
    parser.add_argument(
        "--evidence", type=Path, help="write secret-safe JSON evidence to a new file"
    )
    args = parser.parse_args(argv)
    if not 30 <= args.timeout_seconds <= 1_800:
        parser.error("--timeout-seconds must be between 30 and 1800")
    if args.evidence is not None and args.evidence.exists():
        parser.error("--evidence target already exists; choose a new path")

    stages: dict[str, dict[str, Any]] = {
        stage_name: {"status": "NOT_RUN", "reason": "STAGE_NOT_REACHED"}
        for stage_name in REQUIRED_STAGES
    }
    project_id: str | None = None
    run_id: str | None = None
    try:
        api, worker_id = _verify_local_fixture_environment()
    except (ValueError, OSError):
        for stage_name in REQUIRED_STAGES:
            _stage(
                stages,
                stage_name,
                "BLOCKED" if stage_name == "WORKER_SELECTED" else "NOT_RUN",
                reason="LOCAL_PROFILE_NOT_CONFIGURED",
            )
        report = _report(stages, project_id, run_id)
        _write_report(report, args.evidence)
        return 2

    try:
        _run_project_journey(
            api=api, worker_id=worker_id, timeout_seconds=args.timeout_seconds, stages=stages
        )
    except APIError as exc:
        stages.setdefault("PROJECT_CREATED", {"status": "FAIL", "reason": exc.code})
    except Exception as exc:  # keep the artifact safe while preserving failure type
        _stage(stages, "INTEGRATION_DRIVER", "FAIL", reason=type(exc).__name__)

    project_id = next(
        (
            item.get("evidence", {}).get("project_id")
            for item in stages.values()
            if isinstance(item, dict) and item.get("evidence", {}).get("project_id")
        ),
        None,
    )
    run_id = next(
        (
            item.get("evidence", {}).get("run_id")
            for item in stages.values()
            if isinstance(item, dict) and item.get("evidence", {}).get("run_id")
        ),
        None,
    )

    # The disposable compose profile owns isolated volumes and performs final
    # teardown. Delete the project through its normal API lifecycle here, only
    # after all readbacks have completed.
    if project_id:
        run_is_terminal = run_id is None
        if run_id:
            try:
                _, _, current_run, _ = api.request(
                    "GET", f"/workers/runs/{urllib.parse.quote(str(run_id))}"
                )
                current_state = (
                    str(current_run.get("state") or "UNKNOWN").upper()
                    if isinstance(current_run, dict)
                    else "UNKNOWN"
                )
                run_is_terminal = current_state in TERMINAL_RUN_STATES
                if not run_is_terminal:
                    with suppress(APIError):
                        api.request(
                            "POST",
                            f"/workers/runs/{urllib.parse.quote(str(run_id))}/cancel",
                            {"reason": "Level A disposable integration cleanup", "force": False},
                        )
                    # Cancellation may race a terminal transition. Always
                    # re-read canonical state before deciding whether the
                    # project and workspace can be removed.
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        time.sleep(0.5)
                        _, _, current_run, _ = api.request(
                            "GET", f"/workers/runs/{urllib.parse.quote(str(run_id))}"
                        )
                        current_state = (
                            str(current_run.get("state") or "UNKNOWN")
                            if isinstance(current_run, dict)
                            else "UNKNOWN"
                        )
                        run_is_terminal = current_state.upper() in TERMINAL_RUN_STATES
                        if run_is_terminal:
                            break
            except APIError as exc:
                _stage(stages, "CLEANUP_VERIFIED", "FAIL", reason=exc.code)
        if _stage_not_reached(stages, "CLEANUP_VERIFIED"):
            if not run_is_terminal:
                _stage(stages, "CLEANUP_VERIFIED", "BLOCKED", reason="WORKER_RUN_REMAINS_ACTIVE")
            else:
                try:
                    api.request("DELETE", f"/projects/{urllib.parse.quote(str(project_id))}")
                    try:
                        api.request("GET", f"/projects/{urllib.parse.quote(str(project_id))}")
                    except APIError as exc:
                        if exc.status == 404:
                            _stage(
                                stages,
                                "CLEANUP_VERIFIED",
                                "PASS",
                                evidence={"project_deleted": True},
                            )
                        else:
                            _stage(stages, "CLEANUP_VERIFIED", "FAIL", reason=exc.code)
                    else:
                        _stage(stages, "CLEANUP_VERIFIED", "FAIL", reason="PROJECT_STILL_PRESENT")
                except APIError as exc:
                    _stage(stages, "CLEANUP_VERIFIED", "FAIL", reason=exc.code)
    else:
        _stage(stages, "CLEANUP_VERIFIED", "NOT_RUN", reason="PROJECT_NOT_CREATED")

    report = _report(
        stages, str(project_id) if project_id else None, str(run_id) if run_id else None
    )
    _write_report(report, args.evidence)
    states = [stages.get(name, {}).get("status", "NOT_RUN") for name in REQUIRED_STAGES]
    if any(value == "FAIL" for value in states):
        return 1
    if any(value != "PASS" for value in states):
        return 2
    return 0


def _report(
    stages: dict[str, dict[str, Any]], project_id: str | None, run_id: str | None
) -> dict[str, Any]:
    statuses = [item.get("status") for item in stages.values()]
    return {
        "schema_version": "aiat.opencode-vertical-level-a.v1",
        "profile": "isolated-non-promotable-local-fixture",
        "provider_dispatch": "local_fixture_only",
        "project_id": project_id,
        "worker_run_id": run_id,
        "stages": stages,
        "summary": {
            "pass": statuses.count("PASS"),
            "fail": statuses.count("FAIL"),
            "blocked": statuses.count("BLOCKED"),
            "not_run": statuses.count("NOT_RUN"),
        },
    }


def _write_report(report: dict[str, Any], path: Path | None) -> None:
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if path is None:
        sys.stdout.write(rendered)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(rendered)
    sys.stdout.write(json.dumps(report["summary"], sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
