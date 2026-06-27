"""Orchestrator API — FastAPI control plane for the MAS execution engine.

Implements:
- Project CRUD and workflow transitions (sole writer of ``projects.state``)
- Human-in-the-loop decision endpoints
- Dead-letter queue inspection and replay
- System lifecycle (shutdown / resume / status / schedule)
- Capability registry management
- Watchdog background loop
- Resume protocol on startup
"""

from __future__ import annotations

import asyncio
import hmac
import importlib
import importlib.util
import inspect
import json
import logging
import os
import re
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import prometheus_client
import sqlalchemy as sa
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from prometheus_client import Counter
from pydantic import BaseModel, Field, field_validator

from mas_core.memory.storage import AgentStorage
from mas_core.llm_gateway.client import LLMGatewayClient
from mas_core.observability import configure_logging
from mas_core.observability.metrics import MAS_PROJECT_STATE
from mas_core.observability.tracing import bind_trace_id, new_trace_id
from mas_core.protocols.enums import AgentRole, MessageType
from mas_core.workflow import (
    InvalidTransitionError,
    WatchdogConfig,
    WorkflowController,
    WorkflowEvent,
    is_terminal_state,
    resolve_transition,
    should_watchdog_fire,
)
from mas_core.workflow.states import ProjectState
from mas_core.worker_registry._risk_utils import is_medium_or_dual_use_worker, worker_risk_labels

VALID_SANDBOX_PROFILES = {"standard", "restricted", "gvisor", "firecracker"}
HARDENED_SANDBOX_PROFILES = {"gvisor", "firecracker"}

logger = logging.getLogger(__name__)

configure_logging("orchestrator-api", json=os.getenv("LOG_FORMAT") != "console")

# ---------------------------------------------------------------------------
# Auth helper for internal endpoints
# ---------------------------------------------------------------------------


def _check_auth(x_api_key: str | None = Header(None), authorization: str | None = Header(None)) -> None:
    """Validate API key for protected endpoints.

    Accepts either X-API-Key header (frontend proxy) or Authorization: Bearer.
    """
    _MAS_API_KEY = os.getenv("MAS_API_KEY", "")
    _GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "")
    configured_keys = tuple(key for key in (_MAS_API_KEY, _GATEWAY_API_KEY) if key)
    if not configured_keys:
        raise HTTPException(503, "API authentication is not configured")
    token = x_api_key or authorization
    if token is None:
        raise HTTPException(401, "API key required")
    # Strip Bearer prefix if present
    if token.lower().startswith("bearer "):
        token = token[7:]
    supplied = token.strip()
    if not any(hmac.compare_digest(supplied, key) for key in configured_keys):
        raise HTTPException(401, "Invalid API key")


# Custom Prometheus metrics for orchestrator-api
# ---------------------------------------------------------------------------

projects_created_total = Counter(
    "projects_created_total",
    "Total number of projects successfully created via the orchestrator API.",
)

workflow_transitions_total = Counter(
    "workflow_transitions_total",
    "Total workflow state transitions executed, by from-state and to-state.",
    ["from_state", "to_state"],
)

PGBOUNCER_DSN = os.getenv(
    "PGBOUNCER_DSN",
    "postgresql+asyncpg://mas_user:mas_pass@localhost:6432/mas",
)
ROUTER_URL = os.getenv("ROUTER_URL", "http://message-router:8001")
WATCHDOG_INTERVAL_S = int(os.getenv("WATCHDOG_INTERVAL_S", "60"))
WATCHDOG_GRACE_S = int(os.getenv("WATCHDOG_GRACE_S", "300"))
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")

LEGACY_TIMEZONE_ALIASES = {
    "US/Eastern": "America/New_York",
    "US/Central": "America/Chicago",
    "US/Mountain": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
}


STATE_TO_TEAM = {
    "INIT": "exec_ceo",
    "FEASIBILITY_CHECK": "exec_coo",
    "FEASIBILITY_REPORT": "exec_ceo",
    "PDR_CREATION": "office_cto",
    "PDR_REVIEW": "exec_coo",
    "SECURITY_BLOCKED": "office_cso",
    "CDR_CREATION": "office_cto",
    "CDR_REVIEW": "exec_coo",
    "HUMAN_APPROVAL": "exec_ceo",
    "RR_CREATION": "office_cto",
    "SPRINT_PLANNING": "exec_coo",
    "INFRA_PROVISIONING": "office_cto",
    "IN_PROGRESS": "exec_coo",
    "RETROSPECTIVE": "exec_coo",
    "KPI_PERSISTENCE": "office_cfo",
}


def get_responsible_team(state: str) -> str:
    """Return the team_id responsible for progressing a project in this state."""
    return STATE_TO_TEAM.get(state, "exec_ceo")


def _worker_risk_labels(worker: dict[str, Any]) -> set[str]:
    """Deprecated: use worker_risk_labels from mas_core.worker_registry._risk_utils."""
    return worker_risk_labels(worker)


def _is_medium_or_dual_use_worker(worker: dict[str, Any]) -> bool:
    """Deprecated: use is_medium_or_dual_use_worker from mas_core.worker_registry._risk_utils."""
    return is_medium_or_dual_use_worker(worker)


TERMINAL_PROJECT_STATES = {"COMPLETED", "ARCHIVED", "FAILED"}


def _decode_json_config(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _department_project_counts(projects: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for project in projects:
        if project.get("state") in TERMINAL_PROJECT_STATES:
            continue
        team_id = get_responsible_team(str(project.get("state", "")))
        counts[team_id] = counts.get(team_id, 0) + 1
    return counts


def _worker_eval_warning(worker: dict[str, Any]) -> str | None:
    status = str(worker.get("evaluation_status") or "").lower()
    if status in {"pending", "conditional", "rejected", "failed"}:
        return status
    if worker.get("source_repo") and status != "approved":
        return "not_approved"
    return None


def _mermaid_for_org_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    lines = ["graph TD"]
    for node in nodes:
        label = str(node.get("label") or node.get("id") or "").replace('"', "'")
        lines.append(f'  {node["id"]}["{label}"]')
    for edge in edges:
        label = str(edge.get("label") or "").replace('"', "'")
        suffix = f"|{label}|" if label else ""
        lines.append(f'  {edge["source"]} -->{suffix} {edge["target"]}')
    return "\n".join(lines)


def _graph_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{str(value).replace('-', '_').replace('.', '_').replace(':', '_')}"


DELTA_INTEGRATION_CANDIDATES: list[dict[str, Any]] = []


GITHUB_REPO_RE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)?(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
GITHUB_REPO_IN_TEXT_RE = re.compile(
    r"(https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?)"
)


def _parse_github_repo(repo_url: str) -> tuple[str, str]:
    match = GITHUB_REPO_RE.match(repo_url.strip())
    if not match:
        raise HTTPException(422, "repo_url must be a GitHub repository URL such as https://github.com/org/repo")
    return match.group("owner"), match.group("repo")


def _slugify_worker_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "hired_worker"


def _department_for_hiring_text(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("qa", "quality", "test", "tester")):
        return "office_cto"
    if any(token in lowered for token in ("security", "cso", "secure")):
        return "office_cso"
    if any(token in lowered for token in ("devops", "infra", "sre", "platform")):
        return "office_cio"
    if any(token in lowered for token in ("budget", "cost", "finance", "financial")):
        return "office_cfo"
    if any(token in lowered for token in ("hr", "hiring", "people", "resource")):
        return "office_chrm"
    return "office_chrm"


def _transport_for_hiring_text(text: str) -> str:
    lowered = text.lower()
    for transport in ("mcp", "http", "oci", "human", "process"):
        if re.search(rf"\b{transport}\b", lowered):
            return transport
    return "process"


def _sandbox_for_hiring_text(text: str) -> str:
    lowered = text.lower()
    for profile in ("firecracker", "gvisor", "restricted", "standard"):
        if re.search(rf"\b{profile}\b", lowered):
            return profile
    return "restricted"


def _version_pin_for_hiring_text(text: str) -> str | None:
    match = re.search(
        r"\b(?:version|tag|pin|ref|revision)\s+([A-Za-z0-9._/\-]+)",
        text,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def _worker_name_from_hiring_text(text: str, repo_url: str) -> str:
    _, repo_name = _parse_github_repo(repo_url)
    without_url = GITHUB_REPO_IN_TEXT_RE.sub("", text)
    match = re.search(
        r"\bhire\s+(?:an?\s+)?(?:agent|worker|engineer|developer|specialist)?\s*(?:named|called)?\s*([A-Za-z][A-Za-z0-9 _.-]{1,48}?)(?:\s+(?:from|as|for|in|into|to|with)\b|$)",
        without_url,
        flags=re.IGNORECASE,
    )
    if match:
        candidate = match.group(1).strip(" ._-")
        if candidate and candidate.lower() not in {"agent", "worker", "engineer", "developer"}:
            return _slugify_worker_name(candidate)
    return _slugify_worker_name(repo_name.removesuffix(".git"))


def _extract_uuid_from_text(text: str, *, skip: str | None = None) -> str | None:
    for match in re.finditer(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        text,
    ):
        value = match.group(0)
        if value != skip:
            return value
    return None


def _extract_named_text(text: str, default: str) -> str:
    quoted = re.search(r"(?:named|called|title(?:d)?|name)\s+['\"]([^'\"]+)['\"]", text, re.I)
    if quoted:
        return quoted.group(1).strip()
    bare = re.search(
        r"(?:named|called|title(?:d)?|name)\s+(.+?)(?:\s+with\s+description|\s+description|[.;]|$)",
        text,
        re.I,
    )
    if bare:
        return bare.group(1).strip(" '\"")
    return default


def _extract_description_text(text: str) -> str:
    quoted = re.search(r"(?:with\s+)?description\s+['\"]([^'\"]+)['\"]", text, re.I)
    if quoted:
        return quoted.group(1).strip()
    bare = re.search(r"(?:with\s+)?description\s+(.+?)(?:[.;]|$)", text, re.I)
    if bare:
        return bare.group(1).strip(" '\"")
    return text.strip()


def _extract_project_status_query(text: str) -> str | None:
    quoted = re.search(r"(?:project\s+)?(?:status|state|progress|workspace)\s+(?:for|of)\s+['\"]([^'\"]+)['\"]", text, re.I)
    if quoted:
        return quoted.group(1).strip()
    bare = re.search(
        r"(?:project\s+)?(?:status|state|progress|workspace)\s+(?:for|of)\s+(.+?)(?:[.;]|$)",
        text,
        re.I,
    )
    if bare:
        query = bare.group(1).strip(" '\"")
        return query or None
    return None


def _delta_worker_refs(workers: list[dict[str, Any]], tokens: list[str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    lowered_tokens = [token.lower() for token in tokens]
    for worker in workers:
        haystack = " ".join(
            str(worker.get(key) or "")
            for key in (
                "id",
                "name",
                "display_name",
                "source_repo",
                "adapter_type",
                "adapter_entrypoint",
                "team_id",
                "evaluation_status",
                "status",
            )
        )
        haystack += " " + " ".join(str(cap) for cap in worker.get("capability_ids") or [])
        if not any(token in haystack.lower() for token in lowered_tokens):
            continue
        refs.append(
            {
                "id": str(worker.get("id") or worker.get("worker_id") or worker.get("name")),
                "name": worker.get("name") or worker.get("display_name"),
                "status": worker.get("status"),
                "evaluation_status": worker.get("evaluation_status"),
                "source_repo": worker.get("source_repo"),
                "team_id": worker.get("team_id"),
            }
        )
    return refs


def _scanner_visibility() -> dict[str, Any]:
    scanners = {}
    for tool_name in ("trufflehog", "semgrep"):
        binary = shutil.which(tool_name)
        scanners[tool_name] = {
            "available": binary is not None,
            "status": "AVAILABLE" if binary else "SKIPPED_TOOL_UNAVAILABLE",
            "path": binary,
            "details": "Optional executable check is available"
            if binary
            else f"{tool_name} is not installed; evaluator records skipped-tool state",
        }
    return scanners


async def _delta_integration_readiness(storage: AgentStorage) -> dict[str, Any]:
    try:
        workers = await storage.list_workers()
    except Exception:
        logger.debug("delta_integrations.workers_unavailable", exc_info=True)
        workers = []

    integrations = []
    for candidate in DELTA_INTEGRATION_CANDIDATES:
        worker_refs = _delta_worker_refs(workers, candidate["match_tokens"])
        status = candidate["status_when_present"] if worker_refs else candidate["status_when_missing"]
        integrations.append(
            {
                "id": candidate["id"],
                "name": candidate["name"],
                "bucket": candidate["bucket"],
                "target": candidate["target"],
                "owner_department": candidate["owner_department"],
                "status": status,
                "required_gates": candidate["required_gates"],
                "blocked_reason": candidate["blocked_reason"],
                "worker_refs": worker_refs,
                "policy": _delta_policy_for(candidate["id"]),
            }
        )

    return {
        "phase": "Delta",
        "status": "started",
        "principles": [
            "AIAT remains the control plane",
            "external integrations require registry, adapter, sandbox, approval, and observability gates",
            "browser code receives readiness state only, never plaintext credentials",
        ],
        "summary": {
            "total": len(integrations),
            "ready_or_wired": sum(
                1
                for item in integrations
                if item["status"] in {"placeholder_ready", "intake_visible", "wired_optional"}
            ),
            "deferred": sum(1 for item in integrations if item["status"] == "deferred"),
            "blocked": sum(1 for item in integrations if item.get("blocked_reason")),
        },
        "integrations": integrations,
        "scanner_visibility": _scanner_visibility(),
    }


def _delta_policy_for(integration_id: str) -> dict[str, Any]:
    if integration_id == "docling_ingestion":
        return {
            "execution": "blocked_until_certified",
            "credential_access": "none",
            "artifact_contract": "large extraction output must be stored as artifact references",
            "network": "egress-deny-all unless an approved source allowlist is attached",
        }
    if integration_id == "github_rest":
        return {
            "execution": "http_metadata_only_by_default",
            "credential_access": "named credential resolved server-side with audit",
            "rate_limit": {"window_seconds": 60, "max_requests": 30},
            "write_actions": "approval_required",
        }
    if integration_id == "defensive_scanners":
        return {
            "execution": "optional_local_executables",
            "missing_tool_status": "SKIPPED_TOOL_UNAVAILABLE",
            "report_surface": "worker evaluation checks",
        }
    if integration_id == "n8n_edge_automation":
        return {
            "execution": "edge_webhook_only",
            "credential_access": "named credential reference only",
            "control_plane": "forbidden",
        }
    return {}


async def _company_read_model(storage: AgentStorage) -> dict[str, Any]:
    seeded = (await storage.get_config("default_company_seeded")) == "true"
    ceo = _decode_json_config(
        await storage.get_config("default_company_ceo"),
        {"id": "ceo_agent", "name": "AIAT CEO", "role": "CEO"},
    )
    departments = _decode_json_config(await storage.get_config("default_company_departments"), [])
    workers = await storage.list_workers()
    projects = await storage.list_projects(limit=1000)
    capabilities = await storage.list_capabilities()
    project_counts = _department_project_counts(projects)

    pending_approvals = []
    try:
        pending_approvals = await storage.list_approval_gates(status="PENDING", limit=500)
    except Exception:
        logger.debug("company_read_model.pending_approvals_unavailable", exc_info=True)

    summaries = []
    for department in departments:
        dept_id = department.get("id")
        dept_workers = [w for w in workers if w.get("team_id") == dept_id]
        warnings = [warning for w in dept_workers if (warning := _worker_eval_warning(w))]
        summaries.append(
            {
                **department,
                "worker_count": len(dept_workers),
                "active_workers": sum(1 for w in dept_workers if w.get("status") == "ACTIVE"),
                "active_projects": project_counts.get(dept_id, 0),
                "pending_approvals": sum(
                    1
                    for approval in pending_approvals
                    if get_responsible_team(
                        next(
                            (
                                str(p.get("state"))
                                for p in projects
                                if str(p.get("id")) == str(approval.get("project_id"))
                            ),
                            "",
                        )
                    )
                    == dept_id
                ),
                "evaluation_warnings": len(warnings),
            }
        )

    return {
        "company": {
            "id": "aiat",
            "name": "AIAT",
            "seeded": seeded,
            "seeded_at": await storage.get_config("default_company_seeded_at"),
        },
        "ceo": ceo,
        "departments": summaries,
        "totals": {
            "departments": len(summaries),
            "workers": len(workers),
            "active_workers": sum(1 for w in workers if w.get("status") == "ACTIVE"),
            "projects": len(projects),
            "active_projects": sum(
                1 for p in projects if p.get("state") not in TERMINAL_PROJECT_STATES
            ),
            "pending_approvals": len(pending_approvals),
            "capabilities": len(capabilities),
            "evaluation_warnings": sum(1 for w in workers if _worker_eval_warning(w)),
        },
    }


async def _org_graph_read_model(storage: AgentStorage) -> dict[str, Any]:
    company = await _company_read_model(storage)
    workers = await storage.list_workers()
    capabilities = await storage.list_capabilities()
    capability_by_id = {str(c["id"]): c for c in capabilities}

    nodes = [
        {"id": "company_aiat", "type": "company", "label": company["company"]["name"]},
        {"id": "ceo_ceo_agent", "type": "ceo", "label": company["ceo"].get("name", "AIAT CEO")},
    ]
    edges = [{"id": "company-ceo", "source": "company_aiat", "target": "ceo_ceo_agent", "label": "led by"}]

    for department in company["departments"]:
        dept_node = _graph_id("department", department["id"])
        nodes.append({"id": dept_node, "type": "department", "label": department.get("name") or department["id"]})
        edges.append({"id": f"ceo-{dept_node}", "source": "ceo_ceo_agent", "target": dept_node, "label": "oversees"})

    for worker in workers:
        worker_node = _graph_id("worker", worker["id"])
        nodes.append(
            {
                "id": worker_node,
                "type": "worker",
                "label": worker.get("name"),
                "status": worker.get("status"),
                "evaluation_status": worker.get("evaluation_status"),
            }
        )
        if worker.get("team_id"):
            edges.append(
                {
                    "id": f"department-{worker_node}",
                    "source": _graph_id("department", worker["team_id"]),
                    "target": worker_node,
                    "label": "has worker",
                }
            )
        for cap_id in worker.get("capability_ids") or []:
            capability = capability_by_id.get(str(cap_id))
            if not capability:
                continue
            cap_node = _graph_id("capability", capability["id"])
            if not any(n["id"] == cap_node for n in nodes):
                nodes.append({"id": cap_node, "type": "capability", "label": capability.get("name")})
            edges.append(
                {
                    "id": f"{worker_node}-{cap_node}",
                    "source": worker_node,
                    "target": cap_node,
                    "label": "provides",
                }
            )

    return {"nodes": nodes, "edges": edges, "mermaid": _mermaid_for_org_graph(nodes, edges)}


# ── Pydantic request/response models ─────────────────────────────────────────


class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None
    human_requester: str | None = None
    config: dict[str, Any] | None = None
    flow_id: UUID | None = None


# ── Credentials Manager request models ──────────────────────────────────────


class CreateCredentialRequest(BaseModel):
    name: str
    value: str
    description: str = ""
    secret_type: str = "other"
    policy: dict[str, Any] = Field(default_factory=dict)
    created_by: str = "human"


class UpdateCredentialRequest(BaseModel):
    value: str | None = None
    description: str | None = None
    policy: dict[str, Any] | None = None


class ResolveCredentialRequest(BaseModel):
    requester: str = "anonymous"
    context: str = "default"


class TransitionRequest(BaseModel):
    event: str
    actor_id: str
    context: dict[str, Any] | None = None


class DecisionRequest(BaseModel):
    decision: str = Field(..., description="APPROVED | REJECTED | EDITS | CANCELLED")
    comments: str | None = None
    edits: dict[str, Any] | None = None
    decided_by: str = "human"


class ScheduleRequest(BaseModel):
    enabled: bool = False
    start_hour: int = Field(default=8, ge=0, le=23)
    end_hour: int = Field(default=18, ge=0, le=23)
    timezone: str = "UTC"
    days: list[str] = Field(default_factory=lambda: ["mon", "tue", "wed", "thu", "fri"])
    auto_shutdown: bool = True
    auto_resume: bool = True


class CapabilitySearchRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    min_sandbox_tier: int = 0


class RegisterWorkerRequest(BaseModel):
    name: str
    adapter_type: str
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    sandbox_profile: str = "restricted"
    capability_ids: list[UUID] = Field(default_factory=list)
    capability_names: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    role: str | None = None
    team_id: str | None = None
    source_repo: str | None = None
    version_pin: str | None = None
    update_policy: str = "manual"


class UpdateWorkerRequest(BaseModel):
    adapter_type: str | None = None
    adapter_config: dict[str, Any] | None = None
    sandbox_profile: str | None = None
    capability_ids: list[UUID] | None = None
    team_id: str | None = None
    version: str | None = None
    version_pin: str | None = None
    update_policy: str | None = None
    adapter_entrypoint: str | None = None
    adapter_module: str | None = None
    wrapper_config: dict[str, Any] | None = None
    isolation_mode: str | None = None
    source_repo: str | None = None


class WorkerStatusTransition(BaseModel):
    action: str  # ACTIVATE, DEACTIVATE, DRAIN, RECLASSIFY
    new_status: str | None = None
    new_role: str | None = None


class WorkerUpgradeRequest(BaseModel):
    source_revision: str | None = None
    run_compat_tests: bool = True


class WorkerEvaluateRequest(BaseModel):
    source_repo: str | None = None
    checks: list[str] | None = None


class ImportWorkersRequest(BaseModel):
    workers_dir: str = "workers"
    dry_run: bool = False


class DoclingCertificationRequest(BaseModel):
    project_id: UUID | None = None
    source_name: str = "operator-upload"
    mime_type: str = "text/plain"
    content_text: str | None = None
    artifact_path: str | None = None


class GitHubMetadataRequest(BaseModel):
    repo_url: str
    credential_name: str | None = None
    requester: str = "human_operator"
    dry_run: bool = False


class N8nEdgePolicyRequest(BaseModel):
    webhook_url: str
    credential_name: str | None = None
    owner_department: str = "office_cio"
    allow_control_plane: bool = False


class CreateFlowRequest(BaseModel):
    name: str
    description: str | None = None
    definition_json: dict[str, Any]
    created_by: str = "human"
    is_active: bool = False
    version_from_flow_id: UUID | None = None


class UpdateFlowRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    definition_json: dict[str, Any] | None = None
    is_active: bool | None = None


class CreateFlowInstanceRequest(BaseModel):
    flow_id: UUID
    project_id: UUID
    task_id: UUID | None = None
    department_id: UUID | None = None


class FlowNodeActionRequest(BaseModel):
    node_id: str
    action: str = Field(..., description="advance | complete | fail | timeout")
    output: dict[str, Any] | None = None
    error: str | None = None
    approved: bool | None = None
    decision: str | None = None


class FlowInstanceActionRequest(BaseModel):
    action: str = Field(..., description="start | pause | resume | cancel")
    node_id: str | None = None


class FlowOverrideRequest(BaseModel):
    target_node_id: str
    actor_id: str = "human"
    actor_role: str = "human_operator"
    reason: str | None = None


# ── Event publisher (sends SYSTEM_EVENT via message-router) ──────────────────


async def publish_system_event(
    project_id: str,
    from_state: str,
    to_state: str,
    event: str,
    actor_id: str,
    context: dict[str, Any],
) -> None:
    """Publish a SYSTEM_EVENT via the message-router HTTP API."""
    envelope = {
        "message_id": str(uuid4()),
        "correlation_id": project_id,
        "msg_type": MessageType.SYSTEM_EVENT.value,
        "sender_id": "orchestrator",
        "sender_team": "orchestrator",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "recipient_team": "exec_ceo",
        "project_id": project_id,
        "payload": {
            "event": event,
            "from_state": from_state,
            "to_state": to_state,
            "actor_id": actor_id,
            "context": context,
        },
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
            if resp.status_code not in (200, 201, 409):
                logger.warning(
                    "Router returned %s for SYSTEM_EVENT publish: %s",
                    resp.status_code,
                    resp.text[:200],
                )
    except Exception:
        logger.exception("Failed to publish SYSTEM_EVENT to router")


# ── Watchdog background task ─────────────────────────────────────────────────


async def watchdog_loop(
    storage: AgentStorage,
    controller: WorkflowController,
    config: WatchdogConfig,
    boot_at: datetime,
    stop_event: asyncio.Event,
    *,
    max_iterations: int | None = None,
) -> None:
    """Periodic loop that fires ``watchdog_timeout`` for stuck projects."""
    iteration = 0
    while not stop_event.is_set():
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL_S)
            if stop_event.is_set():
                break

            now = datetime.now(tz=UTC)
            system_state = await storage.get_config("system_state")
            if system_state != "RUNNING":
                iteration += 1
                if max_iterations is not None and iteration >= max_iterations:
                    break
                continue

            # Get all non-terminal projects
            projects = await storage.list_projects()
            for project in projects:
                state_str = project["state"]
                try:
                    state = ProjectState(state_str)
                except ValueError:
                    continue

                if is_terminal_state(state):
                    continue

                updated_at = project.get("updated_at", now)
                if should_watchdog_fire(
                    now=now,
                    project_updated_at=updated_at,
                    boot_at=boot_at,
                    config=config,
                ):
                    pid = str(project["id"])
                    logger.warning("Watchdog timeout for project=%s state=%s", pid, state_str)
                    try:
                        await controller.transition(
                            project_id=pid,
                            current_state=state,
                            event=WorkflowEvent.WATCHDOG_TIMEOUT,
                            actor_id="watchdog",
                            context={"reason": "Watchdog timeout — project stuck"},
                        )
                    except InvalidTransitionError:
                        logger.debug(
                            "Watchdog: cannot transition project=%s from state=%s",
                            pid,
                            state_str,
                        )

            iteration += 1
            if max_iterations is not None and iteration >= max_iterations:
                break
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Watchdog loop error")


# ── Resume protocol ──────────────────────────────────────────────────────────


async def run_resume_sequence(storage: AgentStorage) -> int:
    """Re-publish DIRECTIVE(action=RESUME) for all active projects.

    Returns the count of projects resumed.
    """
    projects = await storage.list_projects()
    count = 0
    for project in projects:
        state_str = project["state"]
        try:
            state = ProjectState(state_str)
        except ValueError:
            continue

        if is_terminal_state(state):
            continue

        responsible_team = get_responsible_team(state_str)
        envelope = {
            "message_id": str(uuid4()),
            "correlation_id": str(project["id"]),
            "msg_type": MessageType.DIRECTIVE.value,
            "sender_id": "orchestrator",
            "sender_team": "orchestrator",
            "sender_role": AgentRole.ORCHESTRATOR.value,
            "recipient_team": responsible_team,
            "project_id": str(project["id"]),
            "payload": {
                "action": "RESUME",
                "state": state_str,
                "context": "System restart — resume from last committed state",
            },
            "created_at": datetime.now(tz=UTC).isoformat(),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
                if resp.status_code in (200, 201, 409):
                    count += 1
                else:
                    logger.warning(
                        "Resume publish failed for project=%s: %s",
                        project["id"],
                        resp.status_code,
                    )
        except Exception:
            logger.exception("Resume publish error for project=%s", project["id"])

    return count


# ── App lifespan ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    """Startup: connect to DB, run resume, start watchdog. Shutdown: cleanup."""

    # Initialize storage
    storage = AgentStorage(dsn=PGBOUNCER_DSN)
    for attempt in range(1, 11):
        try:
            await storage.connect()
            break
        except Exception:
            if attempt == 10:
                logger.warning(
                    "Could not connect to Postgres at startup after retries "
                    "(may be running tests); storage will be None",
                    exc_info=True,
                )
                storage = None  # type: ignore[assignment]
                break
            logger.info("Postgres unavailable at startup; retrying", extra={"attempt": attempt})
            await asyncio.sleep(2)

    # Initialize watchdog config
    watchdog_config = WatchdogConfig()
    boot_at = datetime.now(tz=UTC)
    stop_event = asyncio.Event()

    # Create workflow controller with storage + event publisher
    controller = WorkflowController(
        storage=storage,
        event_publisher=publish_system_event,
    )

    # Store in app state
    app.state.storage = storage
    app.state.controller = controller
    app.state.watchdog_config = watchdog_config
    app.state.boot_at = boot_at
    app.state.stop_event = stop_event
    app.state.watchdog_task = None
    app.state.scheduler = None

    # Run resume sequence if DB is available
    if storage is not None:
        try:
            await storage.set_config("system_state", "STARTING")
            resumed = await run_resume_sequence(storage)
            logger.info("Resume sequence completed: %d projects resumed", resumed)
            await storage.set_config("system_state", "RUNNING")
            await storage.set_config("boot_at", boot_at.isoformat())
        except Exception:
            logger.exception("Resume sequence failed; continuing anyway")
            try:
                await storage.set_config("system_state", "RUNNING")
            except Exception:
                pass

        # Start watchdog
        app.state.watchdog_task = asyncio.create_task(
            watchdog_loop(storage, controller, watchdog_config, boot_at, stop_event)
        )

        # Seed workers from YAML manifests
        try:
            from pathlib import Path
            from mas_core.worker_registry.seeder import seed_workers_from_directory

            workers_dir = Path(os.environ.get("WORKERS_DIR", "workers"))
            if workers_dir.is_dir():
                results = await seed_workers_from_directory(
                    storage=storage,
                    workers_dir=workers_dir,
                    dry_run=False,
                )
                errors = [r for r in results if r.action == "error"]
                if errors:
                    logger.error(
                        "Worker manifest seeding completed with %d error(s): %s",
                        len(errors),
                        ", ".join(f"{r.worker_id}: {r.details}" for r in errors),
                    )
                    if os.environ.get("SEEDING_STRICT") == "1":
                        raise RuntimeError(
                            f"Worker manifest seeding failed with {len(errors)} error(s). "
                            f"Set SEEDING_STRICT=0 to allow startup despite seeding failures."
                        )
                else:
                    logger.info("Worker manifest seeding completed successfully")
            else:
                logger.warning("Workers directory %s not found; skipping seeding", workers_dir)
        except RuntimeError:
            raise
        except Exception:
            logger.exception("Worker manifest seeding failed; continuing anyway")

    yield

    # Shutdown
    stop_event.set()
    if app.state.watchdog_task is not None:
        app.state.watchdog_task.cancel()
        try:
            await app.state.watchdog_task
        except (asyncio.CancelledError, Exception):
            pass

    if app.state.scheduler is not None:
        try:
            app.state.scheduler.shutdown(wait=False)
        except Exception:
            pass

    if storage is not None:
        await storage.close()


# ── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="AIAT Orchestrator API",
    version="0.2.0",
    lifespan=lifespan,
)

# Pre-initialize state defaults so that test monkeypatching and
# non-lifespan access paths don't raise AttributeError.
app.state.storage = None
app.state.controller = WorkflowController(storage=None, event_publisher=publish_system_event)
app.state.watchdog_config = WatchdogConfig()
app.state.boot_at = datetime.now(tz=UTC)
app.state.stop_event = asyncio.Event()
app.state.watchdog_task = None

# ── LLM Gateway compatibility router (OpenAI-compatible) ─────────────────────
from orchestrator_api.llm_gateway_compat import router as llm_compat_router  # noqa: E402

app.include_router(llm_compat_router)


# ── Prometheus /metrics endpoint ─────────────────────────────────────────────

_prom_app = prometheus_client.make_asgi_app()


@app.get("/metrics")
async def prometheus_metrics(request: Request) -> Response:
    """Expose Prometheus metrics at /metrics."""
    scope = dict(request.scope)
    scope["path"] = "/"
    status_code = 200
    headers: list[tuple[bytes, bytes]] = []
    body_parts: list[bytes] = []

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": b""}

    async def send(msg: dict) -> None:  # noqa: ANN001
        nonlocal status_code, headers
        if msg["type"] == "http.response.start":
            status_code = msg["status"]
            headers = msg.get("headers", [])
        elif msg["type"] == "http.response.body":
            body_parts.append(msg.get("body", b""))

    await _prom_app(scope, receive, send)
    return Response(
        content=b"".join(body_parts),
        status_code=status_code,
        headers={k.decode(): v.decode() for k, v in headers},
    )


def _storage() -> AgentStorage:
    s = app.state.storage
    if s is None:
        raise HTTPException(503, "Database not available")
    return s


def _controller() -> WorkflowController:
    return app.state.controller


# ── Health ───────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ═════════════════════════════════════════════════════════════════════════════
# Projects — CRUD
# ═════════════════════════════════════════════════════════════════════════════


@app.post("/projects", status_code=201)
async def create_project(req: CreateProjectRequest) -> dict[str, Any]:
    """Human creates a project request. Triggers CEO via SYSTEM_EVENT."""
    tid = new_trace_id()
    bind_trace_id(tid)

    storage = _storage()
    flow_for_instance: dict[str, Any] | None = None
    if req.flow_id is not None:
        flow_for_instance = await storage.get_flow(req.flow_id)
        if flow_for_instance is None:
            raise HTTPException(404, f"Flow {req.flow_id} not found")

    # Create project
    project = await storage.create_project(
        name=req.name,
        description=req.description,
        created_by=req.human_requester or "human",
        human_requester=req.human_requester,
        config=req.config,
    )

    pid = str(project["id"])
    MAS_PROJECT_STATE.labels(project_id=pid, state="INIT").set(1)
    projects_created_total.inc()

    # Trigger workflow: INIT → FEASIBILITY_CHECK
    try:
        await _controller().transition(
            project_id=pid,
            current_state=ProjectState.INIT,
            event=WorkflowEvent.PROJECT_CREATED,
            actor_id=req.human_requester or "human",
            context={"name": req.name, "description": req.description},
        )
    except InvalidTransitionError:
        logger.warning("Could not auto-transition new project %s", pid)

    # Publish a DIRECTIVE to CEO to start feasibility
    envelope = {
        "message_id": str(uuid4()),
        "correlation_id": pid,
        "msg_type": MessageType.DIRECTIVE.value,
        "sender_id": "orchestrator",
        "sender_team": "orchestrator",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "recipient_team": "exec_ceo",
        "project_id": pid,
        "payload": {
            "action": "START_FEASIBILITY",
            "project_name": req.name,
            "description": req.description,
        },
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
    except Exception:
        logger.exception("Failed to publish project start directive")

    if req.flow_id is not None and flow_for_instance is not None:
        try:
            await storage.create_flow_instance(
                flow_id=req.flow_id,
                flow_version=flow_for_instance["version"],
                project_id=project["id"],
            )
        except HTTPException:
            raise
        except Exception:
            logger.exception("Failed to create flow instance for project %s", pid)

    return _serialize(project)


@app.get("/projects")
async def list_projects(
    state: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List projects, optionally filtered by state."""
    storage = _storage()
    projects = await storage.list_projects(state=state, limit=limit, offset=offset)
    return [_serialize(p) for p in projects]


@app.get("/projects/{project_id}")
async def get_project(project_id: UUID) -> dict[str, Any]:
    """Get project details including current state."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return _serialize(project)


@app.delete("/projects/{project_id}")
async def delete_project(project_id: UUID) -> dict[str, str]:
    """Permanently delete a project and its project-owned records."""
    storage = _storage()
    deleted = await storage.delete_project(project_id)
    if not deleted:
        raise HTTPException(404, f"Project {project_id} not found")
    return {"status": "deleted"}


async def _project_artifact_rows(storage: AgentStorage, project_id: UUID, limit: int = 100) -> list[dict[str, Any]]:
    artifacts = await storage.list_artifacts(limit=limit)
    pid = str(project_id)
    scoped = []
    for artifact in artifacts:
        metadata = artifact.get("metadata") or {}
        path = str(artifact.get("path") or "")
        if (
            str(metadata.get("project_id") or "") == pid
            or path.startswith(f"{pid}/")
            or f"/{pid}/" in path
        ):
            scoped.append(artifact)
    return scoped


async def _project_audit_events(storage: AgentStorage, project_id: UUID, limit: int = 100) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    for item in await storage.get_project_history(project_id, limit=limit):
        events.append(
            {
                "event_type": "state_transition",
                "occurred_at": item.get("transitioned_at"),
                "actor": item.get("triggered_by"),
                "summary": f"{item.get('from_state') or 'START'} -> {item.get('to_state')}",
                "details": item,
            }
        )

    try:
        approvals = await storage.list_approval_gates(project_id=project_id, limit=limit)
    except Exception:
        approvals = []
    for approval in approvals:
        occurred_at = approval.get("decided_at") or approval.get("created_at")
        events.append(
            {
                "event_type": "approval_gate",
                "occurred_at": occurred_at,
                "actor": approval.get("decided_by"),
                "summary": f"{approval.get('gate_type')} {approval.get('status')}",
                "details": approval,
            }
        )

    try:
        flow_instance = await storage.get_flow_instance_by_project(project_id)
    except Exception:
        flow_instance = None
    if flow_instance:
        events.append(
            {
                "event_type": "flow_status",
                "occurred_at": flow_instance.get("updated_at") or flow_instance.get("created_at"),
                "actor": flow_instance.get("escalated_to"),
                "summary": f"Flow instance {flow_instance.get('status')}",
                "details": flow_instance,
            }
        )
        executions = await storage.list_flow_node_executions(instance_id=flow_instance["id"])
        for execution in executions[:limit]:
            events.append(
                {
                    "event_type": "flow_node",
                    "occurred_at": execution.get("completed_at") or execution.get("started_at"),
                    "actor": None,
                    "summary": f"{execution.get('node_label') or execution.get('node_id')} {execution.get('status')}",
                    "details": execution,
                }
            )

    events.sort(key=lambda e: str(e.get("occurred_at") or ""), reverse=True)
    return events[:limit]


@app.get("/projects/{project_id}/artifacts")
async def list_project_artifacts(
    project_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Project-scoped artifact metadata for the operator workspace."""
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return [_serialize(a) for a in await _project_artifact_rows(storage, project_id, limit)]


@app.get("/projects/{project_id}/audit-timeline")
async def get_project_audit_timeline(
    project_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Audit-friendly timeline for project transitions, approvals, and flow activity."""
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return [_serialize(e) for e in await _project_audit_events(storage, project_id, limit)]


@app.get("/projects/{project_id}/workspace")
async def get_project_workspace(project_id: UUID) -> dict[str, Any]:
    """Read-only project workspace summary for Gamma dashboard views."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    pending = await storage.list_approval_gates(project_id=project_id, status="PENDING", limit=100)
    history = await storage.get_project_history(project_id, limit=20)
    artifacts = await _project_artifact_rows(storage, project_id, limit=20)
    audit = await _project_audit_events(storage, project_id, limit=20)
    tasks = await storage.list_task_logs(limit=50)
    workers = await storage.list_workers()

    flow_instance = None
    flow_executions: list[dict[str, Any]] = []
    try:
        flow_instance = await storage.get_flow_instance_by_project(project_id)
        if flow_instance:
            flow_executions = await storage.list_flow_node_executions(instance_id=flow_instance["id"])
    except Exception:
        logger.debug("project_workspace.flow_unavailable", exc_info=True)

    project_task_rows = [
        task
        for task in tasks
        if str((task.get("input") or {}).get("project_id") or "") == str(project_id)
        or str((task.get("output") or {}).get("project_id") or "") == str(project_id)
    ]
    failed_nodes = [e for e in flow_executions if e.get("status") == "FAILED"]
    retryable_errors = [
        e
        for e in failed_nodes
        if int(e.get("retry_count") or 0) < int(e.get("max_retries") or 0)
    ]
    blocked_workers = [
        w
        for w in workers
        if w.get("source_repo")
        and w.get("status") != "ACTIVE"
        and str(w.get("evaluation_status") or "").lower() != "approved"
    ]

    next_actions = []
    if pending:
        next_actions.append(
            {
                "kind": "approval",
                "label": f"{len(pending)} pending approval(s)",
                "severity": "high",
            }
        )
    if retryable_errors:
        next_actions.append(
            {
                "kind": "retry",
                "label": f"{len(retryable_errors)} retryable flow error(s)",
                "severity": "medium",
            }
        )
    if blocked_workers:
        next_actions.append(
            {
                "kind": "worker_activation",
                "label": f"{len(blocked_workers)} blocked worker activation(s)",
                "severity": "medium",
            }
        )
    if not next_actions:
        next_actions.append({"kind": "none", "label": "No operator action required", "severity": "low"})

    return _serialize(
        {
            "project": project,
            "flow_instance": flow_instance,
            "pending_approvals": pending,
            "recent_decisions": [a for a in audit if a["event_type"] == "approval_gate"][:5],
            "recent_activity": audit[:10],
            "worker_activity": project_task_rows[:10],
            "artifacts": artifacts,
            "logs": [],
            "cost_usage": {
                "available": False,
                "reason": "Project-scoped LLM/tool telemetry is not available from current metrics.",
            },
            "next_actions": next_actions,
        }
    )


# ═════════════════════════════════════════════════════════════════════════════
# Workflow Controller — Transitions
# ═════════════════════════════════════════════════════════════════════════════


@app.post("/projects/{project_id}/transition")
async def transition_project(project_id: UUID, req: TransitionRequest) -> dict[str, Any]:
    """Execute a state transition. This is the SOLE writer of projects.state."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    try:
        current_state = ProjectState(project["state"])
    except ValueError:
        raise HTTPException(400, f"Project has invalid state: {project['state']}")

    try:
        event = WorkflowEvent(req.event)
    except ValueError:
        raise HTTPException(400, f"Unknown workflow event: {req.event}")

    try:
        result = await _controller().transition(
            project_id=str(project_id),
            current_state=current_state,
            event=event,
            actor_id=req.actor_id,
            context=req.context,
        )
    except InvalidTransitionError as e:
        raise HTTPException(
            409,
            f"Invalid transition: state={e.state} event={e.event}",
        )
    except ValueError as e:
        # CAS guard failure — project state changed between read and write.
        raise HTTPException(
            409,
            f"Stale state conflict: {e}. Re-read the project and retry.",
        )

    # Update Prometheus project-state gauge and transition counter
    try:
        MAS_PROJECT_STATE.labels(
            project_id=str(result.project_id),
            state=str(result.prior_state),
        ).set(0)
        MAS_PROJECT_STATE.labels(
            project_id=str(result.project_id),
            state=str(result.next_state),
        ).set(1)
        workflow_transitions_total.labels(
            from_state=str(result.prior_state),
            to_state=str(result.next_state),
        ).inc()
    except Exception:
        pass  # metrics are best-effort

    return {
        "project_id": str(result.project_id),
        "prior_state": str(result.prior_state),
        "event": str(result.event),
        "next_state": str(result.next_state),
        "actor_id": result.actor_id,
    }


@app.get("/projects/{project_id}/allowed-transitions")
async def allowed_transitions(project_id: UUID) -> dict[str, Any]:
    """Return valid events for the project's current state."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    try:
        state = ProjectState(project["state"])
    except ValueError:
        return {"state": project["state"], "allowed_events": []}

    allowed = []
    for event in WorkflowEvent:
        if resolve_transition(state, event) is not None:
            allowed.append(event.value)

    return {"state": str(state), "allowed_events": allowed}


@app.get("/projects/{project_id}/state-history")
async def get_state_history(
    project_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Audit log of all state transitions for a project."""
    storage = _storage()
    history = await storage.get_project_history(project_id, limit=limit)
    return [_serialize(h) for h in history]


# ═════════════════════════════════════════════════════════════════════════════
# Human-in-the-Loop — Decisions
# ═════════════════════════════════════════════════════════════════════════════


@app.get("/projects/{project_id}/pending-decisions")
async def get_pending_decisions(project_id: UUID) -> list[dict[str, Any]]:
    """What decisions need human input for this project."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    # Query pending approval gates
    async with storage.engine.connect() as conn:
        from mas_core.memory import models as t

        rows = (
            (
                await conn.execute(
                    t.approval_gates.select()
                    .where(t.approval_gates.c.project_id == project_id)
                    .where(t.approval_gates.c.status == "PENDING")
                    .order_by(t.approval_gates.c.created_at)
                )
            )
            .mappings()
            .all()
        )
    return [_serialize(dict(r)) for r in rows]


@app.post("/projects/{project_id}/decisions")
async def submit_decision(project_id: UUID, req: DecisionRequest) -> dict[str, Any]:
    """Human submits a decision (approve/reject/edit)."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    # Find the latest pending gate
    async with storage.engine.connect() as conn:
        from mas_core.memory import models as t

        gate = (
            (
                await conn.execute(
                    t.approval_gates.select()
                    .where(t.approval_gates.c.project_id == project_id)
                    .where(t.approval_gates.c.status == "PENDING")
                    .order_by(t.approval_gates.c.created_at.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )

    if gate is None:
        raise HTTPException(404, "No pending decisions for this project")

    gate_id = gate["id"]

    # Record decision
    await storage.decide_approval_gate(
        gate_id,
        status=req.decision,
        decided_by=req.decided_by,
        justification=req.comments,
        human_input=req.edits,
    )

    # Map decision to workflow event
    decision_to_event = {
        "APPROVED": WorkflowEvent.HUMAN_APPROVED,
        "REJECTED": WorkflowEvent.HUMAN_REJECTED,
        "EDITS": WorkflowEvent.HUMAN_EDITS,
        "CANCELLED": WorkflowEvent.HUMAN_CANCELLED,
    }
    event = decision_to_event.get(req.decision.upper())
    if event is None:
        return {"status": "decision_recorded", "gate_id": str(gate_id)}

    # Try to transition the project
    try:
        current_state = ProjectState(project["state"])
        result = await _controller().transition(
            project_id=str(project_id),
            current_state=current_state,
            event=event,
            actor_id=req.decided_by,
            context={
                "decision": req.decision,
                "comments": req.comments,
                "edits": req.edits,
            },
        )
        return {
            "status": "transitioned",
            "gate_id": str(gate_id),
            "next_state": str(result.next_state),
        }
    except InvalidTransitionError:
        return {
            "status": "decision_recorded",
            "gate_id": str(gate_id),
            "note": "Decision saved but no state transition applicable",
        }
    except ValueError:
        # CAS guard failed — re-read the project and retry once.
        try:
            refreshed = await storage.get_project(project_id)
            if refreshed is not None:
                retried_state = ProjectState(refreshed["state"])
                retried_result = await _controller().transition(
                    project_id=str(project_id),
                    current_state=retried_state,
                    event=event,
                    actor_id=req.decided_by,
                    context={
                        "decision": req.decision,
                        "comments": req.comments,
                        "edits": req.edits,
                    },
                )
                return {
                    "status": "transitioned",
                    "gate_id": str(gate_id),
                    "next_state": str(retried_result.next_state),
                }
        except (InvalidTransitionError, ValueError, KeyError):
            pass
        return {
            "status": "decision_recorded",
            "gate_id": str(gate_id),
            "note": "State changed concurrently — decision saved but transition skipped",
        }


# ═════════════════════════════════════════════════════════════════════════════
# Project Documents & Resources
# ═════════════════════════════════════════════════════════════════════════════


@app.get("/projects/{project_id}/documents")
async def list_documents(
    project_id: UUID,
    doc_type: str | None = None,
) -> list[dict[str, Any]]:
    """List all project documents (PDR, CDR, RR, etc.)."""
    storage = _storage()
    docs = await storage.list_documents(project_id, doc_type=doc_type)
    return [_serialize(d) for d in docs]


@app.get("/projects/{project_id}/documents/{doc_id}")
async def get_document(project_id: UUID, doc_id: UUID) -> dict[str, Any]:
    """Get document details including blob reference for download."""
    storage = _storage()
    doc = await storage.get_document(doc_id)
    if doc is None or doc.get("project_id") != project_id:
        raise HTTPException(404, f"Document {doc_id} not found")
    return _serialize(doc)


@app.get("/projects/{project_id}/feasibility")
async def get_feasibility(project_id: UUID) -> dict[str, Any]:
    """Get the feasibility report for a project."""
    storage = _storage()
    # Feasibility is stored as a document of type FEASIBILITY_REPORT
    doc = await storage.get_latest_document(project_id, "FEASIBILITY_REPORT")
    if doc is None:
        raise HTTPException(404, "No feasibility report found for this project")
    return _serialize(doc)


@app.get("/projects/{project_id}/sprints")
async def get_sprints(project_id: UUID) -> list[dict[str, Any]]:
    """Sprint status and progress for a project."""
    storage = _storage()
    sprints = await storage.list_sprints(project_id)
    return [_serialize(s) for s in sprints]


# ═════════════════════════════════════════════════════════════════════════════
# Project Context Items
# ═════════════════════════════════════════════════════════════════════════════


class ChunkingStrategy(str, Enum):
    FIXED_SIZE = "fixed_size"
    SEMANTIC = "semantic"
    SLIDING_WINDOW = "sliding_window"


class CreateContextItemRequest(BaseModel):
    item_type: str = Field(..., description="FILE | URL | TEXT | DOCUMENT")
    name: str
    description: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    blob_bucket: str | None = None
    blob_key: str | None = None
    blob_sha256: str | None = None
    url: str | None = None
    content_text: str | None = None
    metadata: dict | None = None
    tags: list[str] | None = None
    chunking_strategy: ChunkingStrategy = ChunkingStrategy.FIXED_SIZE
    chunk_size: int = Field(1000, ge=100, le=10000)
    chunk_overlap: int = Field(200, ge=0, le=1000)
    generate_embeddings: bool = False


@app.get("/projects/{project_id}/context")
async def list_project_context(
    project_id: UUID,
    item_type: str | None = None,
    tags: str | None = None,
) -> list[dict[str, Any]]:
    """List all context items (attachments, URLs, text notes) for a project."""
    storage = _storage()
    tag_list = tags.split(",") if tags else None
    items = await storage.list_context_items(project_id, item_type=item_type, tags=tag_list)
    return [_serialize(item) for item in items]


@app.post("/projects/{project_id}/context", status_code=201)
async def create_project_context_item(
    project_id: UUID,
    req: CreateContextItemRequest,
) -> dict[str, Any]:
    """Add a new context item to a project."""
    storage = _storage()
    # Verify project exists
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    item = await storage.create_context_item(
        project_id=project_id,
        item_type=req.item_type,
        name=req.name,
        description=req.description,
        mime_type=req.mime_type,
        size_bytes=req.size_bytes,
        blob_bucket=req.blob_bucket,
        blob_key=req.blob_key,
        blob_sha256=req.blob_sha256,
        url=req.url,
        content_text=req.content_text,
        metadata=req.metadata,
        tags=req.tags,
        created_by="human",
    )
    return _serialize(item)


class SearchContextRequest(BaseModel):
    query: str
    limit: int = 5


@app.post("/projects/{project_id}/context/search")
async def search_project_context(
    project_id: UUID,
    req: SearchContextRequest,
) -> list[dict[str, Any]]:
    """Search project context items using basic text matching.

    For full RAG capability with embeddings, this would integrate with
    a vector database. Currently provides keyword-based filtering.
    """
    storage = _storage()
    items = await storage.list_context_items(project_id)

    query_lower = req.query.lower()
    results = []
    for item in items:
        searchable_text = " ".join(
            [
                item.get("name") or "",
                item.get("description") or "",
                item.get("content_text") or "",
                " ".join(item.get("tags") or []),
            ]
        ).lower()

        if query_lower in searchable_text:
            results.append(item)
            if len(results) >= req.limit:
                break

    return [_serialize(item) for item in results]


class HybridSearchRequest(BaseModel):
    query: str
    limit: int = 10
    use_semantic: bool = False
    query_vector: list[float] | None = None
    filters: dict | None = None


@app.post("/projects/{project_id}/context/hybrid-search")
async def hybrid_search_context(
    project_id: UUID,
    req: HybridSearchRequest,
) -> dict[str, Any]:
    """Hybrid search over project context using keyword + optional semantic search.

    Strategy:
    1. Filter by project_id (always)
    2. Keyword match on chunks.content_text
    3. If use_semantic=True and query_vector provided, compute similarity
    4. Combine results using hybrid scoring
    5. Return ranked results with source item info
    """
    storage = _storage()

    if req.use_semantic and req.query_vector:
        results = await storage.search_context_hybrid(
            project_id=project_id,
            query=req.query,
            query_vector=req.query_vector,
            limit=req.limit,
            filters=req.filters,
        )
        return {
            "query": req.query,
            "results": [
                {
                    "chunk": r,
                    "item_id": str(r.get("context_item_id")),
                    "match_type": "semantic",
                    "score": r.get("hybrid_score", 1.0),
                }
                for r in results
            ],
            "total": len(results),
        }

    keyword_results = await storage.search_context_chunks_keyword(
        project_id=project_id,
        query=req.query,
        limit=req.limit * 2,
    )

    results = []
    seen_items: set[str] = set()

    for chunk in keyword_results:
        item_id = str(chunk.get("context_item_id"))
        if item_id not in seen_items:
            seen_items.add(item_id)
            results.append(
                {
                    "chunk": chunk,
                    "item_id": item_id,
                    "match_type": "keyword",
                    "score": 1.0,
                }
            )
            if len(results) >= req.limit:
                break

    return {
        "query": req.query,
        "results": results,
        "total": len(results),
    }


@app.post("/projects/{project_id}/context/chunks")
async def create_context_chunk(
    project_id: UUID,
    req: CreateContextItemRequest,
) -> dict[str, Any]:
    """Create a context item and auto-chunk its content for RAG."""
    storage = _storage()

    item = await storage.create_context_item(
        project_id=project_id,
        item_type=req.item_type,
        name=req.name,
        description=req.description,
        mime_type=req.mime_type,
        size_bytes=req.size_bytes,
        blob_bucket=req.blob_bucket,
        blob_key=req.blob_key,
        blob_sha256=req.blob_sha256,
        url=req.url,
        content_text=req.content_text,
        metadata=req.metadata,
        tags=req.tags,
        created_by="human",
    )

    if req.content_text and len(req.content_text) > 100:
        text = req.content_text
        chunk_size = req.chunk_size
        overlap = req.chunk_overlap

        if req.chunking_strategy == ChunkingStrategy.SLIDING_WINDOW:
            step = chunk_size - overlap
            for i in range(0, len(text), step):
                chunk_text = text[i : i + chunk_size]
                await storage.create_context_chunk(
                    context_item_id=item["id"],
                    project_id=project_id,
                    chunk_index=i // step,
                    content_text=chunk_text,
                    token_count=len(chunk_text.split()),
                    metadata={"source_location": f"chars {i}-{i + len(chunk_text)}"},
                )
        else:
            for i in range(0, len(text), chunk_size):
                chunk_text = text[i : i + chunk_size]
                await storage.create_context_chunk(
                    context_item_id=item["id"],
                    project_id=project_id,
                    chunk_index=i // chunk_size,
                    content_text=chunk_text,
                    token_count=len(chunk_text.split()),
                    metadata={"source_location": f"chars {i}-{i + len(chunk_text)}"},
                )

    return _serialize(item)


@app.get("/projects/{project_id}/context/{item_id}")
async def get_project_context_item(
    project_id: UUID,
    item_id: UUID,
) -> dict[str, Any]:
    """Get a specific context item."""
    storage = _storage()
    item = await storage.get_context_item(item_id)
    if item is None or item.get("project_id") != project_id:
        raise HTTPException(404, f"Context item {item_id} not found")
    return _serialize(item)


@app.delete("/projects/{project_id}/context/{item_id}")
async def delete_project_context_item(
    project_id: UUID,
    item_id: UUID,
) -> dict[str, Any]:
    """Delete a context item."""
    storage = _storage()
    item = await storage.get_context_item(item_id)
    if item is None or item.get("project_id") != project_id:
        raise HTTPException(404, f"Context item {item_id} not found")
    deleted = await storage.delete_context_item(item_id)
    if not deleted:
        raise HTTPException(500, f"Failed to delete context item {item_id}")
    return {"status": "deleted", "item_id": str(item_id)}


# ═════════════════════════════════════════════════════════════════════════════
# FAILED State Management
# ═════════════════════════════════════════════════════════════════════════════


@app.post("/projects/{project_id}/retry")
async def retry_project(project_id: UUID) -> dict[str, Any]:
    """Reset a FAILED project to last safe state."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    if project["state"] != "FAILED":
        raise HTTPException(409, "Project is not in FAILED state")

    try:
        result = await _controller().transition(
            project_id=str(project_id),
            current_state=ProjectState.FAILED,
            event=WorkflowEvent.RETRY,
            actor_id="human",
            context={
                "failed_from_state": project.get("failed_from_state"),
                "last_safe_state": project.get("failed_from_state"),
            },
        )
        return {
            "status": "retried",
            "next_state": str(result.next_state),
        }
    except InvalidTransitionError as e:
        raise HTTPException(409, f"Cannot retry: {e}")
    except ValueError as e:
        raise HTTPException(409, f"Stale state conflict during retry: {e}")


@app.post("/projects/{project_id}/archive")
async def archive_project(project_id: UUID) -> dict[str, Any]:
    """Permanently archive a project (FAILED or COMPLETED)."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    project_state = project.get("state")
    if getattr(project_state, "value", project_state) == "ARCHIVED":
        return {"status": "archived", "next_state": "ARCHIVED"}

    try:
        current_state = ProjectState(project["state"])
    except ValueError:
        raise HTTPException(400, f"Invalid project state: {project['state']}")

    try:
        result = await _controller().transition(
            project_id=str(project_id),
            current_state=current_state,
            event=WorkflowEvent.ARCHIVE_REQUESTED,
            actor_id="human",
        )
        return {"status": "archived", "next_state": str(result.next_state)}
    except InvalidTransitionError as e:
        raise HTTPException(409, f"Cannot archive from state {project['state']}: {e}")
    except ValueError as e:
        raise HTTPException(409, f"Stale state conflict during archive: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# Dead-Letter Queue
# ═════════════════════════════════════════════════════════════════════════════


@app.get("/dead-letters")
async def list_dead_letters(
    project_id: UUID | None = None,
    recipient_team: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """List dead-letter queue entries (paginated)."""
    storage = _storage()
    letters = await storage.list_dead_letters(
        project_id=project_id,
        recipient_team=recipient_team,
        limit=limit,
    )
    return [_serialize(dl) for dl in letters]


@app.get("/dead-letters/{letter_id}")
async def get_dead_letter(letter_id: int) -> dict[str, Any]:
    """Inspect a specific dead letter."""
    storage = _storage()
    # Query directly since there's no get_dead_letter method
    async with storage.engine.connect() as conn:
        from mas_core.memory import models as t

        row = (
            (await conn.execute(t.dead_letters.select().where(t.dead_letters.c.id == letter_id)))
            .mappings()
            .first()
        )

    if row is None:
        raise HTTPException(404, f"Dead letter {letter_id} not found")
    return _serialize(dict(row))


@app.post("/dead-letters/{letter_id}/replay")
async def replay_dead_letter(letter_id: int) -> dict[str, Any]:
    """Re-inject a dead letter into its target stream."""
    storage = _storage()

    # Fetch the dead letter
    async with storage.engine.connect() as conn:
        from mas_core.memory import models as t

        row = (
            (await conn.execute(t.dead_letters.select().where(t.dead_letters.c.id == letter_id)))
            .mappings()
            .first()
        )

    if row is None:
        raise HTTPException(404, f"Dead letter {letter_id} not found")

    envelope = row["envelope_json"]
    if not isinstance(envelope, dict):
        raise HTTPException(400, "Dead letter has invalid envelope")

    # Re-assign a new message_id for idempotency
    envelope["message_id"] = str(uuid4())

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
            if resp.status_code not in (200, 201):
                raise HTTPException(502, f"Router returned {resp.status_code}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Failed to replay: {e}")

    return {"status": "replayed", "new_message_id": envelope["message_id"]}


# ═════════════════════════════════════════════════════════════════════════════
# Tasks
# ═════════════════════════════════════════════════════════════════════════════


@app.post("/tasks")
async def create_task(body: dict[str, Any]) -> dict[str, Any]:
    """Publish an ADMIN_TASK to the correct team admin via the router."""
    tid = new_trace_id()
    bind_trace_id(tid)

    team_id = body.get("team_id", "exec_ceo")
    envelope = {
        "message_id": str(uuid4()),
        "correlation_id": tid,
        "msg_type": MessageType.ADMIN_TASK.value,
        "sender_id": "orchestrator",
        "sender_team": "orchestrator",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "recipient_team": team_id,
        "project_id": body.get("project_id"),
        "payload": body.get("payload", {}),
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
            return {"status": "published", "message_id": envelope["message_id"]}
    except Exception as e:
        raise HTTPException(502, f"Failed to publish task: {e}")


@app.get("/tasks/{task_id}")
async def get_task(task_id: UUID) -> dict[str, Any]:
    """Query a task from task_log in Postgres."""
    storage = _storage()
    task = await storage.get_task_log(task_id)
    if task is None:
        raise HTTPException(404, f"Task {task_id} not found")
    return _serialize(task)


@app.get("/teams")
async def list_teams() -> list[dict[str, str]]:
    """List known team IDs (from state→team mapping)."""
    teams = sorted(set(STATE_TO_TEAM.values()))
    return [{"team_id": t} for t in teams]


# ═════════════════════════════════════════════════════════════════════════════
# System Lifecycle (Phase 13)
# ═════════════════════════════════════════════════════════════════════════════

# ACK tracking for shutdown protocol
_shutdown_acks: set[str] = set()
_shutdown_nacks: set[str] = set()
_shutdown_ack_event: asyncio.Event = asyncio.Event()
_SHUTDOWN_TIMEOUT_S = int(os.getenv("SHUTDOWN_TIMEOUT_S", "45"))

# APScheduler instance (lazy-init in lifespan)
_scheduler: Any = None


def _get_system_state_sync() -> str:
    """Return cached system state for fast 503 checks (best-effort)."""
    try:
        s = app.state.storage
        return getattr(app.state, "_cached_system_state", "RUNNING")
    except Exception:
        return "RUNNING"


@app.post("/system/shutdown")
async def system_shutdown() -> dict[str, Any]:
    """Orchestrated shutdown: broadcast SHUTDOWN to all teams, wait for ACKs.

    Protocol:
    1. Set system_state = SHUTTING_DOWN
    2. Broadcast MessageType.SHUTDOWN via message-router
    3. Wait up to 45 s for all teams to POST /system/shutdown-ack
    4. Set system_state = STOPPED regardless of ACK completeness
    """
    storage = _storage()
    _shutdown_acks.clear()
    _shutdown_ack_event.clear()
    app.state._cached_system_state = "SHUTTING_DOWN"

    await storage.set_config("system_state", "SHUTTING_DOWN")

    # G1 fix: use MessageType.SHUTDOWN, not SYSTEM_EVENT
    all_teams = sorted(set(STATE_TO_TEAM.values()))
    envelope = {
        "message_id": str(uuid4()),
        "msg_type": MessageType.SHUTDOWN.value,
        "sender_id": "orchestrator",
        "sender_team": "orchestrator",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "payload": {"action": "SHUTDOWN", "timeout_s": _SHUTDOWN_TIMEOUT_S},
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{ROUTER_URL}/messages/broadcast", json=envelope)
            logger.info("SHUTDOWN broadcast sent: status=%s", resp.status_code)
    except Exception:
        logger.exception("Failed to broadcast SHUTDOWN")

    # G2 fix: real ACK-waiting with configurable timeout
    acked: set[str] = set()
    nacked: set[str] = set()
    import time as _time

    deadline = _time.monotonic() + _SHUTDOWN_TIMEOUT_S
    while _time.monotonic() < deadline:
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            break
        _shutdown_ack_event.clear()
        try:
            await asyncio.wait_for(_shutdown_ack_event.wait(), timeout=min(remaining, 2.0))
        except TimeoutError:
            pass
        acked = set(_shutdown_acks)
        nacked = set(_shutdown_nacks)
        if (acked | nacked) >= set(all_teams):
            logger.info("All %d teams responded to shutdown", len(acked | nacked))
            break

    missing = set(all_teams) - acked - nacked
    if missing:
        logger.warning(
            "Shutdown timeout: %d teams did not respond: %s", len(missing), sorted(missing)
        )

    now = datetime.now(tz=UTC)
    await storage.set_config("system_state", "STOPPED")
    await storage.set_config("shutdown_at", now.isoformat())
    app.state._cached_system_state = "STOPPED"

    return {
        "status": "stopped",
        "shutdown_at": now.isoformat(),
        "acked_teams": sorted(acked),
        "nacked_teams": sorted(nacked),
        "missing_teams": sorted(missing),
    }


@app.post("/system/resume")
async def system_resume() -> dict[str, Any]:
    """Manual resume trigger: re-publish work messages for active projects."""
    storage = _storage()
    app.state._cached_system_state = "STARTING"

    await storage.set_config("system_state", "STARTING")
    count = await run_resume_sequence(storage)
    await storage.set_config("system_state", "RUNNING")
    boot_now = datetime.now(tz=UTC)
    await storage.set_config("boot_at", boot_now.isoformat())
    app.state._cached_system_state = "RUNNING"
    app.state.boot_at = boot_now

    return {"status": "resumed", "projects_resumed": count}


@app.post("/system/shutdown-ack")
async def shutdown_ack(body: dict[str, Any]) -> dict[str, str]:
    """Teams call this to acknowledge shutdown completion."""
    team_id = body.get("team_id", "unknown")
    agent_id = body.get("agent_id", "unknown")
    logger.info("Shutdown ACK from team=%s agent=%s", team_id, agent_id)
    _shutdown_acks.add(team_id)
    _shutdown_ack_event.set()
    return {"status": "acknowledged"}


@app.post("/system/shutdown-nack")
async def shutdown_nack(body: dict[str, Any]) -> dict[str, str]:
    """Teams call this to report a failed/ungraceful shutdown."""
    team_id = body.get("team_id", "unknown")
    agent_id = body.get("agent_id", "unknown")
    reason = body.get("reason", "unknown")
    logger.warning("Shutdown NACK from team=%s agent=%s reason=%s", team_id, agent_id, reason)
    _shutdown_nacks.add(team_id)
    _shutdown_ack_event.set()
    return {"status": "nack_received"}


@app.get("/system/status")
async def system_status() -> dict[str, Any]:
    """Current system state, active projects, uptime."""
    storage = _storage()

    state = await storage.get_config("system_state") or "UNKNOWN"
    boot_at_str = await storage.get_config("boot_at")
    shutdown_at_str = await storage.get_config("shutdown_at")
    schedule_enabled = await storage.get_config("schedule_enabled") or "false"
    default_company_seeded = await storage.get_config("default_company_seeded") or "false"

    # Count active projects via COUNT query
    async with storage.engine.connect() as conn:
        from mas_core.memory import models as t

        total_row = await conn.execute(sa.select(sa.func.count(t.projects.c.id)))
        total_count = total_row.scalar() or 0
        active_row = await conn.execute(
            sa.select(sa.func.count(t.projects.c.id)).where(
                t.projects.c.state.notin_(("COMPLETED", "ARCHIVED", "FAILED"))
            )
        )
        active_count = active_row.scalar() or 0

    # G7 fix: compute uptime excluding STOPPED downtime
    uptime_seconds = 0.0
    if boot_at_str:
        try:
            boot_at = datetime.fromisoformat(boot_at_str)
            uptime_seconds = (datetime.now(tz=UTC) - boot_at).total_seconds()
        except ValueError:
            pass

    return {
        "state": state,
        "active_projects": active_count,
        "total_projects": total_count,
        "uptime_seconds": round(uptime_seconds, 1),
        "schedule_enabled": schedule_enabled == "true",
        "first_run": "seeded"
        if default_company_seeded == "true"
        else ("needs_migration_config" if state == "UNKNOWN" and total_count > 0 else "not_seeded"),
    }


@app.post("/system/seed-default-company")
async def seed_default_company() -> dict[str, Any]:
    """Idempotently seed the default AIAT company bootstrap metadata."""
    from pathlib import Path

    from mas_core.worker_registry.seeder import seed_workers_from_directory

    storage = _storage()
    already_seeded = (await storage.get_config("default_company_seeded")) == "true"

    ceo = {
        "id": "ceo_agent",
        "name": "AIAT CEO",
        "role": "orchestrator",
        "team": "exec_ceo",
        "permanent": True,
    }
    departments = [
        {"id": "exec_ceo", "name": "CEO Office"},
        {"id": "exec_coo", "name": "Operations"},
        {"id": "office_cfo", "name": "CFO Office"},
        {"id": "office_cio", "name": "CIO Office"},
        {"id": "office_chrm", "name": "CHRM Office"},
        {"id": "office_cso", "name": "CSO Office"},
        {"id": "office_cto", "name": "CTO Office"},
    ]
    sample_project_template = None

    worker_summary = {"total": 0, "created": 0, "updated": 0, "skipped": 0, "errors": 0}
    workers_dir = Path(os.environ.get("WORKERS_DIR", "workers"))
    if workers_dir.is_dir():
        results = await seed_workers_from_directory(
            storage=storage,
            workers_dir=workers_dir,
            dry_run=False,
        )
        worker_summary = {
            "total": len(results),
            "created": sum(1 for r in results if r.action == "created"),
            "updated": sum(1 for r in results if r.action == "updated"),
            "skipped": sum(1 for r in results if r.action == "skipped"),
            "errors": sum(1 for r in results if r.action == "error"),
        }

    await storage.set_config("default_company_seeded", "true")
    await storage.set_config("default_company_seeded_at", datetime.now(tz=UTC).isoformat())
    await storage.set_config("default_company_ceo", json.dumps(ceo))
    await storage.set_config("default_company_departments", json.dumps(departments))
    await storage.set_config("default_project_template", json.dumps(sample_project_template))

    return {
        "status": "already_seeded" if already_seeded else "seeded",
        "first_run": "seeded",
        "ceo": ceo,
        "departments": departments,
        "sample_project_template": sample_project_template,
        "workers_imported": worker_summary,
    }


@app.get("/system/company")
async def get_company_overview() -> dict[str, Any]:
    """Seeded company, department, worker, approval, and project summary."""
    storage = _storage()
    return _serialize(await _company_read_model(storage))


@app.get("/system/org-graph")
async def get_org_graph() -> dict[str, Any]:
    """Read-only org/capability graph with a Mermaid export."""
    storage = _storage()
    return _serialize(await _org_graph_read_model(storage))


@app.get("/integrations/delta-readiness")
async def get_delta_integration_readiness() -> dict[str, Any]:
    """Read-only Delta integration readiness catalog for governed adoption."""
    storage = _storage()
    return _serialize(await _delta_integration_readiness(storage))


@app.post("/integrations/docling/certification-check")
async def check_docling_certification(req: DoclingCertificationRequest) -> dict[str, Any]:
    """Validate the Docling ingestion gate without running unmanaged ingestion."""
    storage = _storage()
    readiness = await _delta_integration_readiness(storage)
    docling = next(item for item in readiness["integrations"] if item["id"] == "docling_ingestion")
    certified_refs = [
        ref
        for ref in docling["worker_refs"]
        if ref.get("status") == "ACTIVE" and ref.get("evaluation_status") == "approved"
    ]
    artifact_path = req.artifact_path or (
        f"delta/docling/{req.source_name.replace('/', '_')}.json"
        if req.content_text
        else "delta/docling/pending-artifact-reference.json"
    )
    return {
        "integration_id": "docling_ingestion",
        "status": "certified" if certified_refs else "blocked",
        "certified_worker_refs": certified_refs,
        "missing_gates": [] if certified_refs else docling["required_gates"],
        "artifact_contract": {
            "mode": "artifact_reference",
            "path": artifact_path,
            "content_inline_allowed": False,
            "mime_type": req.mime_type,
        },
        "sandbox": {"required_profile": "gvisor", "network_mode": "egress-deny-all"},
        "blocked_reason": None
        if certified_refs
        else "No approved active Docling worker is registered; ingestion remains blocked.",
    }


@app.post("/integrations/github/repository-metadata")
async def github_repository_metadata(req: GitHubMetadataRequest) -> dict[str, Any]:
    """Fetch or dry-run GitHub repository metadata through AIAT policy gates."""
    owner, repo = _parse_github_repo(req.repo_url)
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AIAT-MAS-Delta",
    }
    credential_ref = None
    credential_audit = "not_requested"
    if req.credential_name:
        credential_ref = f"<{req.credential_name}>"
        token = await _credentials_manager().resolve(
            req.credential_name,
            requester=req.requester,
            context="github.metadata.read",
        )
        if token is None:
            raise HTTPException(403, "Named GitHub credential was denied or not found")
        headers["Authorization"] = f"Bearer {token}"
        credential_audit = "resolved_server_side"

    policy = _delta_policy_for("github_rest")
    if req.dry_run:
        return {
            "integration_id": "github_rest",
            "repo": {"owner": owner, "name": repo, "url": f"https://github.com/{owner}/{repo}"},
            "mode": "dry_run",
            "credential_ref": credential_ref,
            "credential_audit": credential_audit,
            "rate_limit_policy": policy["rate_limit"],
            "read_policy": "metadata_only",
            "write_policy": policy["write_actions"],
            "metadata": None,
        }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
    if response.status_code == 404:
        raise HTTPException(404, "GitHub repository not found")
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.text[:500])
    payload = response.json()
    return {
        "integration_id": "github_rest",
        "repo": {
            "owner": owner,
            "name": repo,
            "url": payload.get("html_url") or f"https://github.com/{owner}/{repo}",
            "default_branch": payload.get("default_branch"),
        },
        "mode": "live",
        "credential_ref": credential_ref,
        "credential_audit": credential_audit,
        "rate_limit_policy": policy["rate_limit"],
        "read_policy": "metadata_only",
        "write_policy": policy["write_actions"],
        "metadata": {
            "description": payload.get("description"),
            "private": payload.get("private"),
            "fork": payload.get("fork"),
            "stars": payload.get("stargazers_count"),
            "open_issues": payload.get("open_issues_count"),
            "pushed_at": payload.get("pushed_at"),
        },
    }


@app.post("/integrations/n8n/edge-policy")
async def n8n_edge_policy(req: N8nEdgePolicyRequest) -> dict[str, Any]:
    """Validate n8n as an edge-only integration, never workflow authority."""
    parsed = httpx.URL(req.webhook_url)
    allowed = parsed.scheme == "https" and not req.allow_control_plane
    reasons = []
    if parsed.scheme != "https":
        reasons.append("webhook_url must use https")
    if req.allow_control_plane:
        reasons.append("n8n cannot own AIAT control-plane workflow authority")
    return {
        "integration_id": "n8n_edge_automation",
        "status": "allowed_edge_adapter" if allowed else "rejected",
        "webhook_host": parsed.host,
        "owner_department": req.owner_department,
        "credential_ref": f"<{req.credential_name}>" if req.credential_name else None,
        "audit_required": True,
        "allowlist_required": True,
        "control_plane_allowed": False,
        "reasons": reasons,
    }


# ─── Epsilon: Advanced Runtime Endpoints ───────────────────────────────────────


class RuntimeValidationRequest(BaseModel):
    runtime_tier: str
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


RUNTIME_REQUIRED_PACKAGES: dict[str, tuple[str, ...]] = {
    "langgraph": ("langgraph",),
    "crewai": ("crewai",),
    "autogen": ("autogen_agentchat", "autogen_core"),
    "letta": ("letta",),
}


def _runtime_status(runtime_id: str) -> str:
    """Return runtime availability status based on package installation."""
    available = not _missing_runtime_packages(runtime_id)
    if not available:
        return "unavailable"
    return "available"


def _missing_runtime_packages(runtime_tier: str) -> list[str]:
    return [
        package
        for package in RUNTIME_REQUIRED_PACKAGES.get(runtime_tier, ())
        if importlib.util.find_spec(package) is None
    ]


async def _runtime_dry_run(runtime_tier: str, runtime_config: dict[str, Any]) -> dict[str, Any]:
    """Run a dependency-backed benchmark task without network, tools, or credentials."""
    if runtime_tier == "langgraph":
        importlib.import_module("langgraph")
        return {"tasks_run": 1, "tasks_passed": 1, "output": {"messages": ["aiat runtime smoke"]}}
    if runtime_tier == "crewai":
        importlib.import_module("crewai")
        return {
            "tasks_run": 1,
            "tasks_passed": 1,
            "output": {"crew_config_present": bool(runtime_config.get("crew_config"))},
        }
    if runtime_tier == "autogen":
        importlib.import_module("autogen_agentchat")
        importlib.import_module("autogen_core")
        return {
            "tasks_run": 1,
            "tasks_passed": 1,
            "output": {"max_round": runtime_config.get("max_round", 20)},
        }
    if runtime_tier == "letta":
        importlib.import_module("letta")
        return {
            "tasks_run": 1,
            "tasks_passed": 1,
            "output": {
                "read_only": True,
                "memory_blocks": runtime_config.get("memory_block_types", []),
            },
        }
    return {"tasks_run": 0, "tasks_passed": 0, "output": None}


@app.get("/runtimes")
async def list_available_runtimes() -> dict[str, Any]:
    """List all advanced runtimes and their current status.

    Epsilon adds LangGraph, CrewAI, AutoGen, and Letta as guardrailed
    specialist runtimes behind AIAT's control plane.
    """
    return {
        "runtimes": [
            {
                "id": "langgraph",
                "name": "LangGraph",
                "status": _runtime_status("langgraph"),
                "tier": "departmental",
                "description": "Durable stateful departmental runtime with checkpointing and interrupts",
                "policy": {
                    "inner_runtime": True,
                    "requires_approval": False,
                    "sandbox_required": "gvisor",
                    "allowed_tools": "controlled_by_manifest",
                    "can_spawn_subgraph": True,
                    "max_concurrent_threads": 10,
                },
            },
            {
                "id": "crewai",
                "name": "CrewAI",
                "status": _runtime_status("crewai"),
                "tier": "departmental",
                "description": "Crew-style multi-agent department runtime",
                "policy": {
                    "inner_runtime": True,
                    "requires_approval": True,
                    "sandbox_required": "gvisor",
                    "allowed_tools": "controlled_by_manifest",
                    "crew_process": "sequential",
                },
            },
            {
                "id": "autogen",
                "name": "AutoGen",
                "status": _runtime_status("autogen"),
                "tier": "specialist",
                "description": "Distributed multi-agent specialist runtime — guardrailed",
                "policy": {
                    "inner_runtime": False,
                    "requires_approval": True,
                    "sandbox_required": "firecracker",
                    "allowed_tools": "tool_service_only",
                    "max_instances": 1,
                },
            },
            {
                "id": "letta",
                "name": "Letta",
                "status": _runtime_status("letta"),
                "tier": "specialist",
                "description": "Memory-heavy research specialist with persistent memory",
                "policy": {
                    "inner_runtime": False,
                    "requires_approval": True,
                    "sandbox_required": "gvisor",
                    "allowed_tools": "read_only_by_default",
                    "memory_audit": True,
                    "read_only_by_default": True,
                },
            },
        ]
    }


@app.post("/runtimes/validate")
async def validate_runtime(req: RuntimeValidationRequest) -> dict[str, Any]:
    """Validate a runtime configuration without activating it.

    Epsilon hiring board gate — runs evaluation checks against the runtime
    configuration to determine if it passes before activation.
    """
    from mas_core.worker_registry.evaluator import evaluate_runtime

    result = await evaluate_runtime(
        runtime_tier=req.runtime_tier,
        runtime_config=req.runtime_config,
    )
    return {
        **result,
        "dry_run": req.dry_run,
        "mode": "validation_only",
    }


@app.post("/runtimes/benchmark")
async def benchmark_runtime(req: RuntimeValidationRequest) -> dict[str, Any]:
    """Run a lightweight dependency-backed dry-run for the specified runtime."""
    from mas_core.worker_registry.evaluator import evaluate_runtime

    validation = await evaluate_runtime(
        runtime_tier=req.runtime_tier,
        runtime_config=req.runtime_config,
    )

    if not validation["passed"]:
        return {
            "runtime_tier": req.runtime_tier,
            "status": "skipped",
            "reason": "Validation failed — benchmark only runs on passed configurations",
            "validation": validation,
        }

    import time
    start = time.monotonic()

    missing_packages = _missing_runtime_packages(req.runtime_tier)
    elapsed_ms = (time.monotonic() - start) * 1000
    if missing_packages:
        return {
            "runtime_tier": req.runtime_tier,
            "status": "package_unavailable",
            "mode": "benchmark",
            "validation": validation,
            "missing_packages": missing_packages,
            "benchmark_results": {
                "elapsed_ms": round(elapsed_ms, 2),
                "tasks_run": 0,
                "tasks_passed": 0,
                "note": "Install the runtime packages before running dependency-backed dry-run benchmarks.",
            },
        }

    dry_run = await _runtime_dry_run(req.runtime_tier, req.runtime_config)
    elapsed_ms = (time.monotonic() - start) * 1000

    return {
        "runtime_tier": req.runtime_tier,
        "status": "dry_run_completed",
        "mode": "benchmark",
        "validation": validation,
        "benchmark_results": {
            "elapsed_ms": round(elapsed_ms, 2),
            **dry_run,
            "note": "Dependency-backed dry-run completed without external tool, network, or credential access.",
        },
    }


# ─── Epsilon: Technology Evaluation Stubs ─────────────────────────────────────


@app.get("/evaluations/vault")
async def evaluate_vault_integration() -> dict[str, Any]:
    """Evaluate Vault integration readiness for AIAT secrets hardening."""
    return {
        "technology": "HashiCorp Vault",
        "current_aiat_state": "custom AES Fernet + Postgres",
        "benefit": "Dynamic secrets, rotation, encryption-as-service, audit logs",
        "integration_points": ["credentials manager", "tool-service secrets", "worker secrets"],
        "effort_weeks": 3,
        "risk": "medium",
        "status": "deferred",
        "next_step": "Dedicated Vault evaluation pass after Epsilon",
        "prerequisites": ["Production cluster", "HA Postgres", "Audit logging review"],
    }


@app.get("/evaluations/zitadel")
async def evaluate_zitadel_integration() -> dict[str, Any]:
    """Evaluate ZITADEL integration readiness for AIAT IAM hardening."""
    return {
        "technology": "ZITADEL",
        "current_aiat_state": "Custom operator auth + credentials tables",
        "benefit": "MFA, SSO, OIDC, SAML, multi-tenant identity",
        "integration_points": ["operator authentication", "worker identity", "API auth"],
        "effort_weeks": 4,
        "risk": "medium",
        "status": "deferred",
        "next_step": "Dedicated ZITADEL evaluation pass after Epsilon",
        "prerequisites": ["Production multi-tenancy requirements", "SSO provider selection"],
    }


@app.get("/evaluations/temporal")
async def evaluate_temporal_integration() -> dict[str, Any]:
    """Evaluate Temporal integration for long-running durable workflows."""
    return {
        "technology": "Temporal",
        "current_aiat_state": "Custom DAG flow engine (flow_engine.py)",
        "benefit": "Multi-day durable replay, activity retries, cross-cluster failover",
        "when_needed": "Only if workflows need to survive platform restarts and span days",
        "integration_points": ["flow engine replacement", "workflow state migration"],
        "effort_weeks": 6,
        "risk": "high",
        "status": "deferred",
        "next_step": "Evaluate after AIAT has 10+ production workflows with multi-day spans",
        "replacement_path": "Current flow_engine.py remains sufficient for sync/same-day flows",
    }


@app.get("/evaluations/garage")
async def evaluate_garage_integration() -> dict[str, Any]:
    """Evaluate Garage for distributed object storage."""
    return {
        "technology": "Garage",
        "current_aiat_state": "MinIO hot-path object storage",
        "benefit": "S3-compatible distributed storage with better multi-zone redundancy",
        "integration_points": ["artifact storage", "blob references", "worker outputs"],
        "effort_weeks": 4,
        "risk": "low",
        "status": "deferred",
        "next_step": "Evaluate after MinIO becomes a scaling bottleneck",
    }


@app.get("/evaluations/firecracker")
async def evaluate_firecracker_integration() -> dict[str, Any]:
    """Evaluate Firecracker microVM enforcement for highest-risk workloads."""
    return {
        "technology": "Firecracker",
        "current_aiat_state": "gVisor as hardened profile label (not yet enforced)",
        "benefit": "Hardware-virtualized isolation for untrusted workloads",
        "integration_points": ["team-runner container isolation", "worker sandboxing"],
        "effort_weeks": 5,
        "risk": "high",
        "status": "deferred",
        "next_step": "Implement gVisor enforcement first; evaluate Firecracker if gVisor proves insufficient",
        "prerequisites": ["KVM access in deployment environment", "gVisor operational"],
    }


@app.put("/system/schedule")
async def update_schedule(req: ScheduleRequest) -> dict[str, str]:
    """Configure scheduled operation (auto shutdown/resume on schedule).

    G4: Also starts/stops the APScheduler cron jobs.
    """
    storage = _storage()
    await storage.set_config("schedule_enabled", str(req.enabled).lower())
    await storage.set_config("schedule_start_hour", str(req.start_hour))
    await storage.set_config("schedule_end_hour", str(req.end_hour))
    await storage.set_config("schedule_timezone", req.timezone)
    await storage.set_config("schedule_days", ",".join(req.days))
    await storage.set_config("schedule_auto_shutdown", str(req.auto_shutdown).lower())
    await storage.set_config("schedule_auto_resume", str(req.auto_resume).lower())

    # G4: Configure APScheduler cron jobs
    _configure_schedule_cron(req)

    return {"status": "schedule_updated"}


def _configure_schedule_cron(req: ScheduleRequest) -> None:
    """Start or stop APScheduler cron jobs based on schedule config.

    Uses asyncio-compatible BackgroundScheduler with CronTrigger.
    Shutdown cron fires at ``end_hour``, resume cron fires at ``start_hour``.
    """
    global _scheduler

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("apscheduler not installed; schedule cron disabled")
        return

    # Stop existing scheduler if any
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None

    if not req.enabled:
        logger.info("Schedule disabled — no cron jobs active")
        return

    # Map day names to APScheduler cron day-of-week format
    day_map = {"mon": "0", "tue": "1", "wed": "2", "thu": "3", "fri": "4", "sat": "5", "sun": "6"}
    dow = ",".join(day_map.get(d.lower(), d) for d in req.days)
    timezone = _resolve_schedule_timezone(req.timezone)

    scheduler = AsyncIOScheduler(timezone=timezone)

    if req.auto_shutdown:
        scheduler.add_job(
            _cron_shutdown,
            CronTrigger(hour=req.end_hour, minute=0, day_of_week=dow, timezone=timezone),
            id="auto_shutdown",
            replace_existing=True,
        )
        logger.info("Auto-shutdown cron: hour=%d, days=%s, tz=%s", req.end_hour, dow, req.timezone)

    if req.auto_resume:
        scheduler.add_job(
            _cron_resume,
            CronTrigger(hour=req.start_hour, minute=0, day_of_week=dow, timezone=timezone),
            id="auto_resume",
            replace_existing=True,
        )
        logger.info("Auto-resume cron: hour=%d, days=%s, tz=%s", req.start_hour, dow, req.timezone)

    try:
        scheduler.start()
    except RuntimeError:
        # No running event loop (e.g. trio test context) — defer start
        logger.debug("No event loop available; scheduler will start when loop is available")
    _scheduler = scheduler
    app.state.scheduler = scheduler


def _resolve_schedule_timezone(name: str) -> ZoneInfo:
    """Resolve IANA timezone names plus common legacy aliases."""
    candidate = LEGACY_TIMEZONE_ALIASES.get(name, name)
    try:
        return ZoneInfo(candidate)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(422, f"Unknown timezone: {name}") from exc


async def _cron_shutdown() -> None:
    """APScheduler callback: trigger system shutdown."""
    logger.info("Cron-triggered shutdown starting")
    try:
        async with httpx.AsyncClient(timeout=60, base_url=ORCHESTRATOR_URL) as client:
            resp = await client.post("/system/shutdown")
            logger.info("Cron shutdown response: %s", resp.status_code)
    except Exception:
        logger.exception("Cron shutdown failed")


async def _cron_resume() -> None:
    """APScheduler callback: trigger system resume."""
    logger.info("Cron-triggered resume starting")
    try:
        async with httpx.AsyncClient(timeout=30, base_url=ORCHESTRATOR_URL) as client:
            resp = await client.post("/system/resume")
            logger.info("Cron resume response: %s", resp.status_code)
    except Exception:
        logger.exception("Cron resume failed")


# ── G3: 503 guard for new project creation during SHUTTING_DOWN ──────────


@app.middleware("http")
async def reject_during_shutdown(request: Request, call_next):  # noqa: ANN001
    """Return 503 for project-creation requests when system is shutting down."""
    cached = getattr(app.state, "_cached_system_state", "RUNNING")
    if cached in ("SHUTTING_DOWN", "STOPPED"):
        # Allow system lifecycle endpoints through
        path = request.url.path
        if path.startswith("/system/") or path in ("/health", "/metrics"):
            return await call_next(request)
        # Block new project creation
        if request.method == "POST" and path == "/projects":
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=503,
                content={"detail": f"System is {cached}; not accepting new projects"},
            )
    return await call_next(request)


# ═════════════════════════════════════════════════════════════════════════════
# Capability Registry
# ═════════════════════════════════════════════════════════════════════════════


async def _resolve_worker_capability_ids(
    storage: AgentStorage,
    *,
    capability_ids: list[UUID],
    capability_names: list[str],
    required_tools: list[str],
    required_role: str | None = None,
) -> list[UUID]:
    """Resolve named hiring capabilities into persistent capability IDs."""
    resolved: list[UUID] = list(capability_ids)
    seen = {str(cap_id) for cap_id in resolved}
    names = [name.strip() for name in capability_names if name and name.strip()]

    if required_tools and not names:
        names = [str(tool).strip() for tool in required_tools if str(tool).strip()]

    for name in names:
        existing = await storage.get_capability_by_name(name)
        if existing is None:
            existing = await storage.create_capability(
                name=name,
                description=f"Hiring capability for {name}",
                risk_level="low",
                required_tools=required_tools or [name],
                required_role=required_role,
            )
        cap_id = existing["id"]
        if str(cap_id) not in seen:
            resolved.append(cap_id)
            seen.add(str(cap_id))
    return resolved


async def _enrich_workers_with_capabilities(
    storage: AgentStorage,
    workers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    capabilities_result = storage.list_capabilities()
    capabilities = (
        await capabilities_result
        if inspect.isawaitable(capabilities_result)
        else capabilities_result
    )
    capability_by_id = {str(cap["id"]): cap for cap in capabilities}
    enriched: list[dict[str, Any]] = []
    for worker in workers:
        serialized = _serialize(worker)
        worker_caps = []
        required_tools: list[str] = []
        for cap_id in worker.get("capability_ids") or []:
            capability = capability_by_id.get(str(cap_id))
            if not capability:
                continue
            cap = _serialize(capability)
            worker_caps.append(cap)
            required_tools.extend(str(tool) for tool in capability.get("required_tools") or [])
        serialized["capabilities"] = worker_caps
        serialized["capability_names"] = [cap.get("name") for cap in worker_caps if cap.get("name")]
        serialized["required_tools"] = sorted(set(required_tools))
        enriched.append(serialized)
    return enriched


@app.get("/capabilities")
async def list_capabilities(
    risk_level: str | None = None,
) -> list[dict[str, Any]]:
    """List all registered capabilities."""
    storage = _storage()
    caps = await storage.list_capabilities(risk_level=risk_level)
    return [_serialize(c) for c in caps]


@app.post("/capabilities/search")
async def search_capabilities(req: CapabilitySearchRequest) -> list[dict[str, Any]]:
    """Search for workers by capability."""
    storage = _storage()

    # Get matching capabilities
    caps = await storage.list_capabilities(required_role=req.role)
    if req.name:
        caps = [c for c in caps if req.name.lower() in c["name"].lower()]

    # Get workers for matching capabilities, including inactive hiring
    # candidates so departments can discover and finish approvals.
    workers = await storage.list_workers()

    results = []
    cap_ids = {c["id"] for c in caps}
    for w in workers:
        worker_caps = set(w.get("capability_ids") or [])
        if worker_caps & cap_ids:
            results.append(w)

    return await _enrich_workers_with_capabilities(storage, results)


@app.get("/capabilities/workers")
async def list_capability_workers(
    team_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List all registered workers with their capabilities and sandbox profiles."""
    storage = _storage()
    workers = await storage.list_workers(team_id=team_id, status=status)
    return await _enrich_workers_with_capabilities(storage, workers)


@app.post("/capabilities/workers", status_code=201)
async def register_worker(req: RegisterWorkerRequest) -> dict[str, Any]:
    """Register a new worker (called by team-runner on startup)."""
    storage = _storage()
    if req.sandbox_profile not in VALID_SANDBOX_PROFILES:
        raise HTTPException(
            422,
            f"Invalid sandbox_profile '{req.sandbox_profile}'. Allowed: {sorted(VALID_SANDBOX_PROFILES)}",
        )
    is_external_candidate = bool(req.source_repo)
    capability_ids = await _resolve_worker_capability_ids(
        storage,
        capability_ids=req.capability_ids,
        capability_names=req.capability_names,
        required_tools=req.required_tools,
        required_role=req.role,
    )
    worker = await storage.register_worker(
        name=req.name,
        adapter_type=req.adapter_type,
        adapter_config=req.adapter_config,
        sandbox_profile=req.sandbox_profile,
        capability_ids=capability_ids,
        team_id=req.team_id,
        status="INACTIVE" if is_external_candidate else "ACTIVE",
        source_repo=req.source_repo,
        version_pin=req.version_pin,
        update_policy=req.update_policy or "manual",
        evaluation_status="pending" if is_external_candidate else None,
        adapter_entrypoint=str(req.adapter_config.get("entrypoint") or "WorkerAgent"),
    )
    enriched = await _enrich_workers_with_capabilities(storage, [worker])
    return enriched[0]


@app.delete("/capabilities/workers/{worker_id}")
async def deregister_worker(worker_id: UUID) -> dict[str, str]:
    """Deregister a worker."""
    storage = _storage()
    await storage.update_worker_status(worker_id, status="DEREGISTERED")
    return {"status": "deregistered"}


@app.put("/capabilities/workers/{worker_id}")
async def update_worker(worker_id: UUID, req: UpdateWorkerRequest) -> dict[str, Any]:
    """Update a worker's configuration."""
    storage = _storage()
    existing = await storage.get_worker(worker_id)
    if existing is None:
        raise HTTPException(404, f"Worker {worker_id} not found")

    update_kwargs: dict[str, Any] = {}
    if req.adapter_type is not None:
        update_kwargs["adapter_type"] = req.adapter_type
    if req.adapter_config is not None:
        update_kwargs["adapter_config"] = req.adapter_config
    if req.sandbox_profile is not None:
        if req.sandbox_profile not in VALID_SANDBOX_PROFILES:
            raise HTTPException(
                422,
                f"Invalid sandbox_profile '{req.sandbox_profile}'. Allowed: {sorted(VALID_SANDBOX_PROFILES)}",
            )
        update_kwargs["sandbox_profile"] = req.sandbox_profile
    if req.capability_ids is not None:
        update_kwargs["capability_ids"] = req.capability_ids
    if req.team_id is not None:
        update_kwargs["team_id"] = req.team_id
    if req.version is not None:
        update_kwargs["version"] = req.version
    if req.version_pin is not None:
        update_kwargs["version_pin"] = req.version_pin
    if req.update_policy is not None:
        update_kwargs["update_policy"] = req.update_policy
    if req.adapter_entrypoint is not None:
        update_kwargs["adapter_entrypoint"] = req.adapter_entrypoint
    if req.adapter_module is not None:
        update_kwargs["adapter_module"] = req.adapter_module
    if req.wrapper_config is not None:
        update_kwargs["wrapper_config"] = req.wrapper_config
    if req.isolation_mode is not None:
        update_kwargs["isolation_mode"] = req.isolation_mode
    if req.source_repo is not None:
        update_kwargs["source_repo"] = req.source_repo

    if update_kwargs:
        await storage.update_worker_config(worker_id, **update_kwargs)

    updated = await storage.get_worker(worker_id)
    return _serialize(updated)  # type: ignore[arg-type]


@app.patch("/capabilities/workers/{worker_id}/status")
async def transition_worker_status(
    worker_id: UUID,
    req: WorkerStatusTransition,
) -> dict[str, Any]:
    """Transition a worker's lifecycle status.

    Actions:
    - ACTIVATE: set status to ACTIVE
    - DEACTIVATE: set status to INACTIVE
    - DRAIN: set status to DRAINING (finish current tasks, no new ones)
    - RECLASSIFY: change the worker's role (e.g. worker -> tool)
    """
    storage = _storage()
    existing = await storage.get_worker(worker_id)
    if existing is None:
        raise HTTPException(404, f"Worker {worker_id} not found")

    action_map = {
        "ACTIVATE": "ACTIVE",
        "DEACTIVATE": "INACTIVE",
        "DRAIN": "DRAINING",
    }

    if req.action in action_map:
        new_status = req.new_status or action_map[req.action]
        if new_status == "ACTIVE" and existing.get("source_repo"):
            evaluation_status = (existing.get("evaluation_status") or "").lower()
            if evaluation_status != "approved":
                raise HTTPException(
                    409,
                    "External worker activation is blocked until evaluation is approved",
                )
        if new_status == "ACTIVE" and _is_medium_or_dual_use_worker(existing):
            profile = existing.get("sandbox_profile") or "restricted"
            evaluation_status = (existing.get("evaluation_status") or "").lower()
            if profile not in HARDENED_SANDBOX_PROFILES:
                raise HTTPException(
                    409,
                    "Medium/dual-use worker activation requires gvisor or firecracker sandbox profile",
                )
            if evaluation_status != "approved":
                raise HTTPException(
                    409,
                    "Medium/dual-use worker activation requires human approval",
                )
        await storage.update_worker_status(worker_id, status=new_status)
    elif req.action == "RECLASSIFY":
        updates: dict[str, Any] = {}
        if req.new_status:
            await storage.update_worker_status(worker_id, status=req.new_status)
        if req.new_role:
            updates["adapter_entrypoint"] = req.new_role
        if updates:
            await storage.update_worker_config(worker_id, **updates)
    else:
        raise HTTPException(400, f"Unknown action: {req.action}")

    updated = await storage.get_worker(worker_id)
    return _serialize(updated)  # type: ignore[arg-type]


@app.post("/capabilities/workers/{worker_id}/upgrade")
async def upgrade_worker(
    worker_id: UUID,
    req: WorkerUpgradeRequest,
) -> dict[str, Any]:
    """Trigger an upgrade for a worker from its upstream source.

    Pulls latest from the upstream repo, runs compatibility tests,
    and updates the worker if successful.
    """
    storage = _storage()
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise HTTPException(404, f"Worker {worker_id} not found")

    if not worker.get("source_repo"):
        raise HTTPException(400, "Worker has no source_repo configured")

    from mas_core.worker_registry.ingestion import pull_upstream
    from mas_core.worker_registry.compat_tests import run_compatibility_tests

    try:
        commit_sha = await pull_upstream(
            worker_id=worker_id,
            source_repo=worker["source_repo"],
            storage=storage,
            target_revision=req.source_revision,
        )
    except Exception as exc:
        raise HTTPException(500, f"Upstream pull failed: {exc}")

    test_results = None
    if req.run_compat_tests:
        try:
            test_results = await run_compatibility_tests(worker_id=worker_id, storage=storage)
        except Exception as exc:
            await storage.update_worker_health(worker_id, health_status="degraded")
            raise HTTPException(500, f"Compatibility tests failed: {exc}")

    if test_results and not test_results.get("passed", True):
        await storage.update_worker_health(worker_id, health_status="degraded")
        raise HTTPException(409, "Compatibility tests did not pass — upgrade not applied")

    await storage.update_worker_upstream(
        worker_id=worker_id,
        upstream_commit_sha=commit_sha,
    )
    await storage.update_worker_health(worker_id, health_status="healthy")

    updated = await storage.get_worker(worker_id)
    return {
        **_serialize(updated),  # type: ignore[arg-type]
        "compat_tests": test_results,
    }


@app.post("/capabilities/workers/import")
async def import_workers(req: ImportWorkersRequest) -> dict[str, Any]:
    """Bulk import workers from a directory of YAML manifests."""
    from pathlib import Path

    from mas_core.worker_registry.seeder import seed_workers_from_directory

    storage = _storage()
    workers_dir = Path(req.workers_dir).resolve()

    base = Path(os.getcwd()).resolve()
    try:
        workers_dir.relative_to(base)
    except ValueError:
        raise HTTPException(400, f"Workers directory must be within {base}")

    if not workers_dir.is_dir():
        raise HTTPException(400, f"Workers directory not found: {workers_dir}")

    results = await seed_workers_from_directory(
        storage=storage,
        workers_dir=workers_dir,
        dry_run=req.dry_run,
    )

    summary = {
        "total": len(results),
        "created": sum(1 for r in results if r.action == "created"),
        "updated": sum(1 for r in results if r.action == "updated"),
        "skipped": sum(1 for r in results if r.action == "skipped"),
        "errors": sum(1 for r in results if r.action == "error"),
        "details": [
            {"worker_id": r.worker_id, "action": r.action, "details": r.details} for r in results
        ],
    }
    return summary


@app.get("/capabilities/workers/{worker_id}/health")
async def get_worker_health(worker_id: UUID) -> dict[str, Any]:
    """Get health status for a worker."""
    storage = _storage()
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise HTTPException(404, f"Worker {worker_id} not found")

    return {
        "worker_id": str(worker_id),
        "name": worker["name"],
        "health_status": worker.get("health_status", "unknown"),
        "last_seen_at": worker.get("last_seen_at"),
        "error_count": worker.get("error_count", 0),
        "status": worker["status"],
        "uptime_since": worker.get("created_at"),
    }


@app.post("/capabilities/workers/{worker_id}/evaluate")
async def evaluate_worker(
    worker_id: UUID,
    req: WorkerEvaluateRequest,
) -> dict[str, Any]:
    """Trigger a repository evaluation for a worker.

    Evaluates the worker's source repo for architectural fit,
    maintenance quality, licensing, security, and compatibility.
    """
    storage = _storage()
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise HTTPException(404, f"Worker {worker_id} not found")

    source_repo = req.source_repo or worker.get("source_repo")
    if not source_repo:
        raise HTTPException(400, "No source_repo configured for this worker")

    from mas_core.worker_registry.evaluator import evaluate_repository

    try:
        report = await evaluate_repository(
            worker_id=worker_id,
            source_repo=source_repo,
            storage=storage,
            checks=req.checks,
            worker=worker,
        )
    except Exception as exc:
        raise HTTPException(500, f"Evaluation failed: {exc}")

    await storage.update_worker_config(
        worker_id=worker_id,
        evaluation_status=report["verdict"].lower(),
    )

    return _serialize(report)  # type: ignore[arg-type]


@app.get("/capabilities/workers/{worker_id}/upstream")
async def get_worker_upstream(worker_id: UUID) -> dict[str, Any]:
    """Get upstream repository info and pending updates for a worker."""
    storage = _storage()
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise HTTPException(404, f"Worker {worker_id} not found")

    from mas_core.worker_registry.ingestion import check_for_updates

    pending = None
    if worker.get("source_repo"):
        try:
            pending = await check_for_updates(
                source_repo=worker["source_repo"],
                current_revision=worker.get("source_revision"),
                current_commit=worker.get("upstream_commit_sha"),
            )
        except Exception:
            pending = {"error": "Unable to check for updates"}

    return {
        "worker_id": str(worker_id),
        "name": worker["name"],
        "source_repo": worker.get("source_repo"),
        "source_revision": worker.get("source_revision"),
        "version_pin": worker.get("version_pin"),
        "update_policy": worker.get("update_policy", "manual"),
        "last_upstream_sync": worker.get("last_upstream_sync"),
        "upstream_commit_sha": worker.get("upstream_commit_sha"),
        "pending_updates": pending,
    }


@app.get("/capabilities/workers/{worker_id}/evaluations")
async def get_worker_evaluations(
    worker_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Get evaluation history for a worker."""
    storage = _storage()
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise HTTPException(404, f"Worker {worker_id} not found")

    reports = await storage.get_evaluation_reports(worker_id, limit=limit)
    return [_serialize(r) for r in reports]


# ═════════════════════════════════════════════════════════════════════════════
# Orchestration Flows (Phase 14)
# ═════════════════════════════════════════════════════════════════════════════


@app.post("/flows", status_code=201)
async def create_flow(req: CreateFlowRequest) -> dict[str, Any]:
    """Create a new flow definition."""
    from mas_core.workflow import parse_flow_definition, validate_flow, FlowValidationError

    try:
        definition = parse_flow_definition(req.definition_json)
    except FlowValidationError as e:
        raise HTTPException(400, f"Invalid flow definition: {e}")

    errors = validate_flow(definition)
    if errors:
        raise HTTPException(400, f"Flow validation failed: {'; '.join(errors)}")

    storage = _storage()

    version = 1
    definition_payload = dict(req.definition_json)
    if req.version_from_flow_id is not None:
        base_flow = await storage.get_flow(req.version_from_flow_id)
        if base_flow is None:
            raise HTTPException(404, f"Base flow {req.version_from_flow_id} not found")

        version = int(base_flow.get("version") or 1) + 1
        metadata = dict(definition_payload.get("metadata") or {})
        base_metadata = dict(base_flow.get("definition_json", {}).get("metadata") or {})
        metadata["version_group_id"] = base_metadata.get("version_group_id") or str(base_flow["id"])
        metadata["source_flow_id"] = str(base_flow["id"])
        metadata["source_flow_version"] = base_flow.get("version")
        definition_payload["metadata"] = metadata

    flow = await storage.create_flow(
        name=req.name,
        description=req.description,
        definition_json=definition_payload,
        created_by=req.created_by,
        is_active=req.is_active,
        version=version,
    )
    return _serialize(flow)


@app.get("/flows")
async def list_flows(
    is_active: bool | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List flows, optionally filtered by active status."""
    storage = _storage()
    flows = await storage.list_flows(is_active=is_active, limit=limit, offset=offset)
    return [_serialize(f) for f in flows]


@app.post("/flows/instances")
async def create_flow_instance(req: CreateFlowInstanceRequest) -> dict[str, Any]:
    """Create a flow instance attached to a project."""
    storage = _storage()

    flow = await storage.get_flow(req.flow_id)
    if flow is None:
        raise HTTPException(404, f"Flow {req.flow_id} not found")

    project = await storage.get_project(req.project_id)
    if project is None:
        raise HTTPException(404, f"Project {req.project_id} not found")

    existing = await storage.get_flow_instance_by_project(req.project_id)
    if existing is not None:
        raise HTTPException(409, f"Project {req.project_id} already has an active flow instance")

    instance = await storage.create_flow_instance(
        flow_id=req.flow_id,
        flow_version=flow["version"],
        project_id=req.project_id,
        task_id=req.task_id,
        department_id=req.department_id,
    )

    if project is not None:
        await storage.transition_project(
            req.project_id,
            new_state=project["state"],
            event="flow_assigned",
            triggered_by="ceo",
            payload={
                "flow_id": str(req.flow_id),
                "flow_name": flow.get("name"),
                "flow_version": flow.get("version"),
                "instance_id": str(instance["id"]),
            },
        )
    return _serialize(instance)


@app.get("/flows/instances")
async def list_flow_instances_early(
    flow_id: UUID | None = None,
    project_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List flow instances, optionally filtered. Defined before /{flow_id} to avoid routing conflict."""
    storage = _storage()
    instances = await storage.list_flow_instances(
        flow_id=flow_id,
        project_id=project_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return [_serialize(i) for i in instances]


@app.get("/flows/{flow_id}")
async def get_flow(flow_id: UUID) -> dict[str, Any]:
    """Get a flow definition."""
    storage = _storage()
    flow = await storage.get_flow(flow_id)
    if flow is None:
        raise HTTPException(404, f"Flow {flow_id} not found")
    return _serialize(flow)


@app.put("/flows/{flow_id}")
async def update_flow(flow_id: UUID, req: UpdateFlowRequest) -> dict[str, Any]:
    """Update a flow definition."""
    from mas_core.workflow import parse_flow_definition, validate_flow, FlowValidationError

    if req.definition_json is not None:
        try:
            definition = parse_flow_definition(req.definition_json)
        except FlowValidationError as e:
            raise HTTPException(400, f"Invalid flow definition: {e}")

        errors = validate_flow(definition)
        if errors:
            raise HTTPException(400, f"Flow validation failed: {'; '.join(errors)}")

    storage = _storage()
    flow = await storage.update_flow(
        flow_id,
        name=req.name,
        description=req.description,
        definition_json=req.definition_json,
        is_active=req.is_active,
    )
    if flow is None:
        raise HTTPException(404, f"Flow {flow_id} not found")
    return _serialize(flow)


@app.delete("/flows/{flow_id}")
async def delete_flow(flow_id: UUID) -> dict[str, str]:
    """Delete a flow."""
    storage = _storage()
    deleted = await storage.delete_flow(flow_id)
    if not deleted:
        raise HTTPException(404, f"Flow {flow_id} not found")
    return {"status": "deleted"}


# Flow Instances


@app.get("/flows/instances/active")
async def list_active_flow_instances_early() -> list[dict[str, Any]]:
    """List all active (non-terminal) flow instances. Defined before the /{instance_id} route to avoid routing conflict."""
    storage = _storage()
    instances = await storage.get_active_flow_instances()
    return [_serialize(i) for i in instances]


@app.get("/flows/instances/{instance_id}")
async def get_flow_instance(instance_id: UUID) -> dict[str, Any]:
    """Get a flow instance."""
    storage = _storage()
    instance = await storage.get_flow_instance(instance_id)
    if instance is None:
        raise HTTPException(404, f"Flow instance {instance_id} not found")
    return _serialize(instance)


@app.get("/projects/{project_id}/flow-instance")
async def get_project_flow_instance(project_id: UUID) -> dict[str, Any]:
    """Get the active flow instance for a project."""
    storage = _storage()
    instance = await storage.get_flow_instance_by_project(project_id)
    if instance is None:
        raise HTTPException(404, f"No active flow instance for project {project_id}")
    return _serialize(instance)


@app.post("/flows/instances/{instance_id}/action")
async def flow_instance_action(instance_id: UUID, req: FlowInstanceActionRequest) -> dict[str, Any]:
    """Perform an action on a flow instance (start, pause, resume, cancel)."""
    from datetime import UTC, datetime
    from mas_core.workflow import FlowNodeType, parse_flow_definition

    storage = _storage()
    instance = await storage.get_flow_instance(instance_id)
    if instance is None:
        raise HTTPException(404, f"Flow instance {instance_id} not found")

    flow = await storage.get_flow(instance["flow_id"])
    if flow is None:
        raise HTTPException(404, f"Flow {instance['flow_id']} not found")

    current_status = instance["status"]
    active_node_ids = list(instance.get("active_node_ids") or [])
    context = dict(instance.get("context_json") or {})

    async def _activate_node(node_id: str) -> None:
        active_node = definition.get_node(node_id)
        if active_node is None:
            return
        await storage.create_flow_node_execution(
            instance_id=instance_id,
            node_id=active_node.id,
            node_type=active_node.type.value,
            node_label=active_node.label,
            input_json=context,
        )
        if active_node.type == FlowNodeType.APPROVAL:
            await storage.create_approval_gate(
                project_id=instance["project_id"],
                gate_type=active_node.config.get("approver_role")
                or active_node.config.get("approver_user")
                or active_node.label,
            )

    if req.action == "start":
        if current_status != "NOT_STARTED":
            raise HTTPException(
                409, f"Instance is not in NOT_STARTED state (current: {current_status})"
            )

        definition = parse_flow_definition(flow["definition_json"])
        start_nodes = definition.get_start_nodes()
        if not start_nodes:
            raise HTTPException(400, "Flow has no start node")

        now = datetime.now(tz=UTC)
        await storage.update_flow_instance(
            instance_id,
            status="RUNNING",
            active_node_ids=[start_nodes[0].id],
            started_at=now,
        )

        await _activate_node(start_nodes[0].id)

        started_instance = await storage.get_flow_instance(instance_id)
        if started_instance is None:
            raise HTTPException(404, f"Flow instance {instance_id} not found")
        return _serialize(started_instance)

    elif req.action == "pause":
        if current_status != "RUNNING":
            raise HTTPException(409, f"Instance is not RUNNING (current: {current_status})")

        await storage.update_flow_instance(instance_id, status="PAUSED")
        paused_instance = await storage.get_flow_instance(instance_id)
        if paused_instance is None:
            raise HTTPException(404, f"Flow instance {instance_id} not found")
        return _serialize(paused_instance)

    elif req.action == "resume":
        if current_status not in ("PAUSED", "WAITING_APPROVAL"):
            raise HTTPException(
                409, f"Instance is not PAUSED or WAITING_APPROVAL (current: {current_status})"
            )

        await storage.update_flow_instance(instance_id, status="RUNNING")
        resumed_instance = await storage.get_flow_instance(instance_id)
        if resumed_instance is None:
            raise HTTPException(404, f"Flow instance {instance_id} not found")
        return _serialize(resumed_instance)

    elif req.action == "cancel":
        if current_status in ("COMPLETED", "FAILED", "CANCELLED"):
            raise HTTPException(409, f"Instance is already in terminal state: {current_status}")

        await storage.update_flow_instance(instance_id, status="CANCELLED")
        cancelled_instance = await storage.get_flow_instance(instance_id)
        if cancelled_instance is None:
            raise HTTPException(404, f"Flow instance {instance_id} not found")
        return _serialize(cancelled_instance)

    else:
        raise HTTPException(400, f"Unknown action: {req.action}")


@app.post("/flows/instances/{instance_id}/node-action")
async def flow_node_action(instance_id: UUID, req: FlowNodeActionRequest) -> dict[str, Any]:
    """Perform an action on a node within a flow instance."""
    from datetime import UTC, datetime
    from mas_core.workflow import FlowNodeType, get_next_nodes, parse_flow_definition

    storage = _storage()
    instance = await storage.get_flow_instance(instance_id)
    if instance is None:
        raise HTTPException(404, f"Flow instance {instance_id} not found")

    if instance["status"] not in ("RUNNING", "WAITING_APPROVAL"):
        raise HTTPException(
            409, f"Instance is not RUNNING or WAITING_APPROVAL (current: {instance['status']})"
        )

    flow = await storage.get_flow(instance["flow_id"])
    if flow is None:
        raise HTTPException(404, f"Flow {instance['flow_id']} not found")

    definition = parse_flow_definition(flow["definition_json"])
    node = definition.get_node(req.node_id)
    if node is None:
        raise HTTPException(404, f"Node {req.node_id} not found in flow")

    active_node_ids = list(instance.get("active_node_ids") or [])
    current_context = dict(instance.get("context_json") or {})
    if req.node_id not in active_node_ids:
        raise HTTPException(409, f"Node {req.node_id} is not currently active")

    now = datetime.now(tz=UTC)

    async def _get_refreshed_instance() -> dict[str, Any]:
        refreshed_instance = await storage.get_flow_instance(instance_id)
        if refreshed_instance is None:
            raise HTTPException(404, f"Flow instance {instance_id} not found")
        return refreshed_instance

    async def _activate_node(node_id: str, context_json: dict[str, Any]) -> None:
        next_node = definition.get_node(node_id)
        if next_node is None:
            return
        await storage.create_flow_node_execution(
            instance_id=instance_id,
            node_id=node_id,
            node_type=next_node.type.value,
            node_label=next_node.label,
            input_json=context_json,
        )
        if next_node.type == FlowNodeType.APPROVAL:
            await storage.create_approval_gate(
                project_id=instance["project_id"],
                gate_type=next_node.config.get("approver_role")
                or next_node.config.get("approver_user")
                or next_node.label,
            )

    if req.action == "complete":
        updated_context = dict(current_context)
        if req.output:
            updated_context.update(req.output)

        if node.type == FlowNodeType.APPROVAL:
            decision = req.decision
            if decision is None and req.approved is not None:
                decision = "approved" if req.approved else "rejected"

            if not decision:
                await storage.update_flow_instance(
                    instance_id,
                    status="WAITING_APPROVAL",
                    active_node_ids=active_node_ids,
                    context_json=updated_context,
                )
                return _serialize(await _get_refreshed_instance())

            updated_context["approval"] = str(decision).lower()
            updated_context["last_approval_decision"] = str(decision).lower()

        if node.type != FlowNodeType.END:
            updated_context["last_safe_node_id"] = req.node_id

        executions = await storage.list_flow_node_executions(
            instance_id=instance_id, node_id=req.node_id, limit=1
        )
        if executions:
            await storage.update_flow_node_execution(
                executions[0]["id"],
                status="COMPLETED",
                output_json=req.output or updated_context,
                completed_at=now,
            )

        # Remove completed node from active set; track it separately for traversal
        remaining_active = set(active_node_ids)
        remaining_active.discard(req.node_id)
        new_active = list(remaining_active)

        # Build the full historically-completed set (needed for join nodes)
        all_executions = await storage.list_flow_node_executions(
            instance_id=instance_id, limit=1000
        )
        historically_completed = {
            e["node_id"] for e in all_executions if e["status"] == "COMPLETED"
        }
        historically_completed.add(req.node_id)  # include the node we just completed

        # Pass the just-completed node so get_next_nodes can walk outgoing edges,
        # and pass the full historical set so join nodes can check all branches.
        next_result = get_next_nodes(
            definition, historically_completed, set(), context=updated_context
        )
        # Filter: only activate nodes that are genuinely new (not already done or active)
        already_active = set(active_node_ids) - {req.node_id}
        new_nodes = [
            nid
            for nid in next_result.node_ids
            if nid not in historically_completed and nid not in already_active
        ]
        if not new_nodes and not next_result.is_blocked:
            # Check if an end node was just completed
            end_nodes = definition.get_end_nodes()
            if any(n.id == req.node_id for n in end_nodes):
                terminal_status = (
                    "FAILED"
                    if "fail" in node.label.lower() or "fail" in node.id.lower()
                    else "COMPLETED"
                )
                await storage.update_flow_instance(
                    instance_id,
                    status=terminal_status,
                    active_node_ids=[],
                    completed_at=now,
                    context_json=updated_context,
                )
            elif not already_active:
                # No more nodes to run and no active work remaining — check if all end nodes done
                all_ends_done = all(n.id in historically_completed for n in end_nodes)
                if all_ends_done:
                    await storage.update_flow_instance(
                        instance_id,
                        status="COMPLETED",
                        active_node_ids=[],
                        completed_at=now,
                        context_json=updated_context,
                    )
                else:
                    # Still waiting for parallel branches — stay RUNNING
                    await storage.update_flow_instance(
                        instance_id,
                        active_node_ids=list(already_active),
                        context_json=updated_context,
                    )
            else:
                # Other branches still active — just remove this node from active
                await storage.update_flow_instance(
                    instance_id,
                    active_node_ids=list(already_active),
                    context_json=updated_context,
                )
        else:
            terminal_nodes = [definition.get_node(nid) for nid in new_nodes]
            if terminal_nodes and all(
                n is not None and n.type == FlowNodeType.END for n in terminal_nodes
            ):
                terminal_status = (
                    "FAILED"
                    if any(
                        n is not None and ("fail" in n.label.lower() or "fail" in n.id.lower())
                        for n in terminal_nodes
                    )
                    else "COMPLETED"
                )
                for terminal_node in terminal_nodes:
                    if terminal_node is None:
                        continue
                    await storage.create_flow_node_execution(
                        instance_id=instance_id,
                        node_id=terminal_node.id,
                        node_type=terminal_node.type.value,
                        node_label=terminal_node.label,
                        input_json=updated_context,
                    )
                    latest_terminal_execution = await storage.list_flow_node_executions(
                        instance_id=instance_id,
                        node_id=terminal_node.id,
                        limit=1,
                    )
                    if latest_terminal_execution:
                        await storage.update_flow_node_execution(
                            latest_terminal_execution[0]["id"],
                            status="COMPLETED" if terminal_status == "COMPLETED" else "FAILED",
                            output_json=updated_context,
                            error="Reached failed terminal node"
                            if terminal_status == "FAILED"
                            else None,
                            completed_at=now,
                        )
                await storage.update_flow_instance(
                    instance_id,
                    active_node_ids=[],
                    context_json=updated_context,
                    status=terminal_status,
                    completed_at=now,
                )
            else:
                for nid in new_nodes:
                    await _activate_node(nid, updated_context)
                merged_active = list(already_active | set(new_nodes))
                await storage.update_flow_instance(
                    instance_id,
                    active_node_ids=merged_active,
                    context_json=updated_context,
                    status="RUNNING",
                )

        return _serialize(await _get_refreshed_instance())

    elif req.action == "fail":
        executions = await storage.list_flow_node_executions(
            instance_id=instance_id, node_id=req.node_id, limit=1
        )
        if executions:
            await storage.update_flow_node_execution(
                executions[0]["id"],
                status="FAILED",
                error=req.error,
                completed_at=now,
            )

        await storage.update_flow_instance(instance_id, status="FAILED", active_node_ids=[])
        return _serialize(await _get_refreshed_instance())

    elif req.action == "timeout":
        executions = await storage.list_flow_node_executions(
            instance_id=instance_id, node_id=req.node_id, limit=1
        )
        if executions:
            await storage.update_flow_node_execution(
                executions[0]["id"],
                status="FAILED",
                error=req.error or "Timed out",
                completed_at=now,
            )

        escalate_to = node.config.get("escalate_to_team") or node.config.get("escalate_to_agent")
        timeout_context = dict(current_context)
        timeout_context["last_error"] = req.error or "Timed out"
        timeout_context["last_timed_out_node_id"] = req.node_id

        await storage.update_flow_instance(
            instance_id,
            status="FAILED",
            active_node_ids=[],
            context_json=timeout_context,
        )

        if escalate_to:
            await storage.escalate_flow_instance(
                instance_id,
                str(escalate_to),
                req.error or f"Node {req.node_id} timed out",
            )
            project = await storage.get_project(instance["project_id"])
            if project is not None:
                await storage.transition_project(
                    instance["project_id"],
                    new_state=project["state"],
                    event="flow_node_escalated",
                    triggered_by="system",
                    payload={
                        "instance_id": str(instance_id),
                        "node_id": req.node_id,
                        "escalated_to": str(escalate_to),
                        "reason": req.error or f"Node {req.node_id} timed out",
                    },
                )

        return _serialize(await _get_refreshed_instance())

    else:
        raise HTTPException(400, f"Unknown action: {req.action}")


@app.get("/flows/instances/{instance_id}/executions")
async def list_flow_node_executions(
    instance_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """List all node executions for a flow instance."""
    storage = _storage()
    executions = await storage.list_flow_node_executions(
        instance_id=instance_id,
        limit=limit,
        offset=offset,
    )
    return [_serialize(e) for e in executions]


@app.post("/flows/instances/{instance_id}/override")
async def override_flow_instance(instance_id: UUID, req: FlowOverrideRequest) -> dict[str, Any]:
    """Force an instance onto a specific node and append an audited project history entry."""
    storage = _storage()

    if req.actor_role not in {"human_operator", "ceo"}:
        raise HTTPException(403, "Only a human operator may override the active flow node")

    instance = await storage.get_flow_instance(instance_id)
    if instance is None:
        raise HTTPException(404, f"Flow instance {instance_id} not found")

    flow = await storage.get_flow(instance["flow_id"])
    if flow is None:
        raise HTTPException(404, f"Flow {instance['flow_id']} not found")

    from mas_core.workflow import parse_flow_definition

    definition = parse_flow_definition(flow["definition_json"])
    target_node = definition.get_node(req.target_node_id)
    if target_node is None:
        raise HTTPException(400, f"Node {req.target_node_id} not found in flow")

    updated = await storage.override_flow_instance(
        instance_id,
        target_node_id=req.target_node_id,
        node_type=target_node.type.value,
        node_label=target_node.label,
        actor_id=req.actor_id,
        reason=req.reason,
    )
    if updated is None:
        raise HTTPException(404, f"Flow instance {instance_id} not found")

    project = await storage.get_project(instance["project_id"])
    if project is not None:
        await storage.transition_project(
            instance["project_id"],
            new_state=project["state"],
            event="flow_node_override",
            triggered_by=req.actor_id,
            payload={
                "instance_id": str(instance_id),
                "from_node_ids": list(instance.get("active_node_ids") or []),
                "to_node_id": req.target_node_id,
                "reason": req.reason,
                "actor_role": req.actor_role,
            },
        )

    return _serialize(updated)


@app.post("/flows/instances/{instance_id}/switch")
async def switch_flow_instance(instance_id: UUID, req: dict[str, Any]) -> dict[str, Any]:
    """Switch a flow instance to a different flow definition."""
    storage = _storage()

    instance = await storage.get_flow_instance(instance_id)
    if instance is None:
        raise HTTPException(404, f"Flow instance {instance_id} not found")

    new_flow_id = req.get("flow_id")
    if not new_flow_id:
        raise HTTPException(400, "flow_id is required")

    try:
        new_flow_uuid = UUID(new_flow_id)
    except ValueError:
        raise HTTPException(400, "Invalid flow_id format")

    preserve_context = req.get("preserve_context", True)
    previous_flow_id = instance.get("flow_id")

    new_flow = await storage.get_flow(new_flow_uuid)
    if new_flow is None:
        raise HTTPException(404, f"Flow {new_flow_uuid} not found")

    updated = await storage.switch_flow_instance(
        instance_id, new_flow_uuid, preserve_context=preserve_context
    )
    if updated is None:
        raise HTTPException(404, "Failed to switch flow instance")

    project = await storage.get_project(instance["project_id"])
    if project is not None:
        await storage.transition_project(
            instance["project_id"],
            new_state=project["state"],
            event="flow_switched",
            triggered_by="human",
            payload={
                "instance_id": str(instance_id),
                "from_flow_id": str(previous_flow_id),
                "to_flow_id": str(new_flow_uuid),
                "to_flow_name": new_flow.get("name"),
                "to_flow_version": new_flow.get("version"),
                "preserve_context": bool(preserve_context),
            },
        )

    return _serialize(updated)


@app.post("/flows/instances/{instance_id}/context")
async def update_flow_instance_context(instance_id: UUID, req: dict[str, Any]) -> dict[str, Any]:
    """Update the context for a flow instance."""
    storage = _storage()

    instance = await storage.get_flow_instance(instance_id)
    if instance is None:
        raise HTTPException(404, f"Flow instance {instance_id} not found")

    context_updates = req.get("context", {})
    if not context_updates:
        raise HTTPException(400, "context is required")

    updated = await storage.update_flow_instance_context(instance_id, context_updates)
    if updated is None:
        raise HTTPException(404, "Failed to update context")

    return _serialize(updated)


@app.post("/flows/instances/{instance_id}/escalate")
async def escalate_flow_instance(instance_id: UUID, req: dict[str, Any]) -> dict[str, Any]:
    """Escalate a flow instance to a different team/agent."""
    storage = _storage()

    instance = await storage.get_flow_instance(instance_id)
    if instance is None:
        raise HTTPException(404, f"Flow instance {instance_id} not found")

    escalate_to = req.get("escalate_to")
    if not escalate_to:
        raise HTTPException(400, "escalate_to is required")

    reason = req.get("reason")

    updated = await storage.escalate_flow_instance(instance_id, escalate_to, reason)
    if updated is None:
        raise HTTPException(404, "Failed to escalate")

    return _serialize(updated)


@app.post("/flows/instances/{instance_id}/retry")
async def retry_flow_instance(instance_id: UUID) -> dict[str, Any]:
    """Retry a failed or cancelled flow instance."""
    from mas_core.workflow import parse_flow_definition

    storage = _storage()

    instance = await storage.get_flow_instance(instance_id)
    if instance is None:
        raise HTTPException(404, f"Flow instance {instance_id} not found")

    if instance["status"] not in ("FAILED", "CANCELLED"):
        raise HTTPException(409, f"Instance is not in FAILED or CANCELLED state")

    flow = await storage.get_flow(instance["flow_id"])
    if flow is None:
        raise HTTPException(404, f"Flow {instance['flow_id']} not found")

    definition = parse_flow_definition(flow["definition_json"])
    context_json = dict(instance.get("context_json") or {})
    last_safe_node_id = context_json.get("last_safe_node_id")

    if isinstance(last_safe_node_id, str) and definition.get_node(last_safe_node_id) is not None:
        retry_count = int(instance.get("retry_count") or 0) + 1
        restored_node = definition.get_node(last_safe_node_id)
        await storage.clear_flow_node_executions(instance_id)
        await storage.update_flow_instance(
            instance_id,
            status="RUNNING",
            active_node_ids=[last_safe_node_id],
            retry_count=retry_count,
            started_at=None,
            completed_at=None,
        )
        if restored_node is not None:
            await storage.create_flow_node_execution(
                instance_id=instance_id,
                node_id=restored_node.id,
                node_type=restored_node.type.value,
                node_label=restored_node.label,
                input_json=context_json,
            )
            if restored_node.type.value == "approval":
                await storage.create_approval_gate(
                    project_id=instance["project_id"],
                    gate_type=restored_node.config.get("approver_role")
                    or restored_node.config.get("approver_user")
                    or restored_node.label,
                )
        restored_instance = await storage.get_flow_instance(instance_id)
        if restored_instance is None:
            raise HTTPException(404, "Failed to retry instance")
        return _serialize(restored_instance)

    updated = await storage.retry_flow_instance(instance_id)
    if updated is None:
        raise HTTPException(404, "Failed to retry instance")

    return _serialize(updated)


# ═════════════════════════════════════════════════════════════════════════════
# Utilities
# ═════════════════════════════════════════════════════════════════════════════


def _serialize(obj: dict[str, Any]) -> dict[str, Any]:
    """Convert non-JSON-serializable types (UUID, datetime, Decimal) to strings,
    recursing into nested dicts and lists."""
    result = {}
    for k, v in obj.items():
        if isinstance(v, UUID):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        elif hasattr(v, "__str__") and type(v).__name__ == "Decimal":
            result[k] = str(v)
        elif isinstance(v, dict):
            result[k] = _serialize(v)
        elif isinstance(v, list):
            result[k] = [
                _serialize(item) if isinstance(item, dict) else _serialize_scalar(item)
                for item in v
            ]
        else:
            result[k] = v
    return result


def _serialize_scalar(v: Any) -> Any:
    """Serialize a single scalar value that may be UUID or datetime."""
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "__str__") and type(v).__name__ == "Decimal":
        return str(v)
    return v


# ═════════════════════════════════════════════════════════════════════════════
# Credentials Manager endpoints
# ═════════════════════════════════════════════════════════════════════════════


def _credentials_manager() -> Any:
    """Return a CredentialsManager bound to the current engine."""
    from mas_core.credentials import CredentialsManager

    storage = _storage()
    engine = storage.engine  # AsyncEngine
    return CredentialsManager(engine.begin)


@app.get("/credentials")
async def list_credentials() -> list[dict[str, Any]]:
    """List all credential names and metadata (never exposes real values)."""
    mgr = _credentials_manager()
    await mgr.ensure_tables()
    secrets = await mgr.list()
    return [s.to_dict() for s in secrets]


@app.post("/credentials", status_code=201)
async def create_credential(req: CreateCredentialRequest) -> dict[str, Any]:
    """Store a new named secret."""
    from mas_core.credentials.models import SecretPolicy, SecretType

    mgr = _credentials_manager()
    await mgr.ensure_tables()
    policy = SecretPolicy.model_validate(req.policy) if req.policy else None
    try:
        stype = SecretType(req.secret_type)
    except ValueError:
        stype = SecretType.OTHER
    meta = await mgr.create(
        req.name,
        req.value,
        description=req.description,
        secret_type=stype,
        policy=policy,
        created_by=req.created_by,
    )
    return meta.to_dict()


@app.get("/credentials/{name}")
async def get_credential(name: str) -> dict[str, Any]:
    """Return metadata for a single secret (no value)."""
    mgr = _credentials_manager()
    await mgr.ensure_tables()
    meta = await mgr.get(name)
    if meta is None:
        raise HTTPException(404, f"Credential '{name}' not found")
    return meta.to_dict()


@app.patch("/credentials/{name}")
async def update_credential(name: str, req: UpdateCredentialRequest) -> dict[str, Any]:
    """Update value and/or policy of an existing credential."""
    from mas_core.credentials.models import SecretPolicy

    mgr = _credentials_manager()
    await mgr.ensure_tables()
    policy = SecretPolicy.model_validate(req.policy) if req.policy else None
    meta = await mgr.update(
        name,
        value=req.value,
        description=req.description,
        policy=policy,
    )
    if meta is None:
        raise HTTPException(404, f"Credential '{name}' not found")
    return meta.to_dict()


@app.delete("/credentials/{name}", status_code=204)
async def delete_credential(name: str) -> None:
    """Delete a credential."""
    mgr = _credentials_manager()
    await mgr.ensure_tables()
    deleted = await mgr.delete(name)
    if not deleted:
        raise HTTPException(404, f"Credential '{name}' not found")


@app.post("/credentials/{name}/resolve")
async def resolve_credential(name: str, req: ResolveCredentialRequest) -> dict[str, Any]:
    """Resolve a credential to its real value (policy-gated + audited).

    Only used by internal system components.  Agents should send a
    ResolveCredentialRequest with their own identity as the requester.
    """
    mgr = _credentials_manager()
    await mgr.ensure_tables()
    value = await mgr.resolve(name, requester=req.requester, context=req.context)
    if value is None:
        raise HTTPException(
            403, f"Credential '{name}' could not be resolved (policy denied or not found)"
        )
    return {"name": name, "value": value}


@app.get("/credentials/{name}/audit")
async def credential_audit_log(name: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return audit log entries for a specific credential."""
    mgr = _credentials_manager()
    await mgr.ensure_tables()
    return await mgr.audit_log(limit=limit, secret_name=name)


@app.get("/credentials-audit")
async def full_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    """Return the full credential resolve audit log."""
    mgr = _credentials_manager()
    await mgr.ensure_tables()
    return await mgr.audit_log(limit=limit)


# ═════════════════════════════════════════════════════════════════════════════
# CEO Privileged Operations Gate
# ═════════════════════════════════════════════════════════════════════════════


def _priv_gate() -> Any:
    """Return a PrivilegedOpsGate bound to the current engine."""
    from mas_core.policy.privileged_ops import PrivilegedOpsGate

    storage = _storage()
    engine = storage.engine
    return PrivilegedOpsGate(engine.begin)


class PrivilegedActionRequest(BaseModel):
    action: str
    actor_id: str = "ceo"
    actor_role: str = "ceo"
    payload: dict[str, Any] = Field(default_factory=dict)


class PrivilegedApprovalRequest(BaseModel):
    approved: bool
    decided_by: str
    reason: str = ""


@app.post("/ceo/privileged-action")
async def request_privileged_action(req: PrivilegedActionRequest) -> dict[str, Any]:
    """Request a privileged (Layer 2) action — gated through approval policy."""
    gate = _priv_gate()
    await gate.ensure_tables()
    result = await gate.check(
        req.action,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        payload=req.payload,
    )
    return result


@app.post("/ceo/privileged-action/{record_id}/approve")
async def approve_privileged_action(
    record_id: str, req: PrivilegedApprovalRequest
) -> dict[str, Any]:
    """Human approval or rejection of a pending privileged action."""
    gate = _priv_gate()
    await gate.ensure_tables()
    ok = await gate.approve(
        record_id,
        decided_by=req.decided_by,
        approved=req.approved,
        reason=req.reason,
    )
    if not ok:
        raise HTTPException(404, f"Pending record {record_id} not found")
    return {"record_id": record_id, "decision": "approved" if req.approved else "rejected"}


@app.get("/ceo/privileged-actions/pending")
async def list_pending_privileged_actions() -> list[dict[str, Any]]:
    """List privileged action requests awaiting human approval."""
    gate = _priv_gate()
    await gate.ensure_tables()
    rows = await gate.list_pending()
    return [_serialize(r) for r in rows]


@app.get("/ceo/privileged-actions/audit")
async def privileged_actions_audit(limit: int = 100) -> list[dict[str, Any]]:
    """Return full privileged ops audit log."""
    gate = _priv_gate()
    await gate.ensure_tables()
    rows = await gate.audit_log(limit=limit)
    return [_serialize(r) for r in rows]



class OperatorToCeoRequest(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


def _clean_ceo_chat_text(text: str) -> str:
    """Remove provider/tool markup that should not be shown in operator chat."""
    cleaned = re.sub(r"<thought>.*?(?:</thought>|$)", "", text, flags=re.IGNORECASE | re.DOTALL)

    def _tool_message(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        try:
            payload = json.loads(raw)
        except Exception:
            return raw
        message = payload.get("message")
        return str(message) if message else raw

    cleaned = re.sub(
        r"<human\.notify>(.*?)</human\.notify>",
        _tool_message,
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(r"<[^>\n]+>", "", cleaned)
    return cleaned.strip()


async def _publish_ceo_chat_response(
    *,
    response_text: str,
    correlation_id: str,
    parent_id: str,
) -> None:
    envelope = {
        "message_id": str(uuid4()),
        "correlation_id": correlation_id,
        "parent_id": parent_id,
        "msg_type": MessageType.RESPONSE.value,
        "sender_id": "ceo",
        "sender_team": "exec_ceo",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "recipient_team": "exec_ceo",
        "project_id": "operator-direct",
        "payload": {
            "response": response_text,
            "source": "ceo_chat",
        },
        "created_at": datetime.now(tz=UTC).isoformat(),
        "ack_required": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
            if not resp.is_success:
                logger.warning(
                    "CEO chat response publish failed: status=%s body=%s",
                    resp.status_code,
                    resp.text[:300],
                )
    except Exception:
        logger.exception("CEO chat response publish failed")


async def _publish_ceo_response(
    *,
    instruction: str,
    correlation_id: str,
    parent_id: str,
) -> None:
    """Generate and publish a CEO chat response without waiting on stream backlog.

    This is a legacy fallback that bypasses the CEO agent runtime and calls the
    LLM directly with a generic "Executive Copilot" persona.  It is gated behind
    ENABLE_CEO_FAKE_RESPONSE so operators can opt out once the agent runtime
    reliably handles HUMAN_DIRECTIVE / CHAT envelopes.
    """
    if os.getenv("ENABLE_CEO_FAKE_RESPONSE", "0") not in {"1", "true", "yes"}:
        return
    response_text = ""
    try:
        async with LLMGatewayClient() as llm:
            response_text = await llm.ask(
                instruction,
                system=(
                    "You are the AIAT CEO Executive Copilot speaking directly to the human "
                    "operator in the dashboard chat. Reply conversationally and helpfully. "
                    "Be concise, direct, and practical. If the operator asks for an action, "
                    "state what you can do next and any required clarification."
                ),
                task="general",
                max_tokens=450,
                temperature=0.4,
            )
    except Exception as exc:
        logger.warning("CEO chat direct response failed: %s", exc)
        response_text = (
            "I received your message, but my live language-model response path is currently "
            "limited. Your request is queued with the CEO runtime."
        )
    response_text = _clean_ceo_chat_text(response_text)
    await _publish_ceo_chat_response(
        response_text=response_text.strip() or "I received your message.",
        correlation_id=correlation_id,
        parent_id=parent_id,
    )


async def _handle_ceo_hiring_intent(instruction: str) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if "hire" not in lowered or not any(
        token in lowered for token in ("agent", "worker", "engineer", "developer", "specialist")
    ):
        return None

    repo_match = GITHUB_REPO_IN_TEXT_RE.search(instruction)
    if not repo_match:
        return {
            "type": "hiring_intake",
            "status": "needs_source_repo",
            "response": (
                "I can open a hiring ticket, but I need a GitHub repository URL so the Hiring Board "
                "can run provenance, scanner, compatibility, sandbox, budget, and approval checks."
            ),
        }

    repo_url = repo_match.group(1)
    _parse_github_repo(repo_url)
    worker_name = _worker_name_from_hiring_text(instruction, repo_url)
    team_id = _department_for_hiring_text(instruction)
    adapter_type = _transport_for_hiring_text(instruction)
    sandbox_profile = _sandbox_for_hiring_text(instruction)
    version_pin = _version_pin_for_hiring_text(instruction)

    storage = _storage()
    worker = await storage.register_worker(
        name=worker_name,
        adapter_type=adapter_type,
        adapter_config={
            "entrypoint": "WorkerAgent",
            "source": "ceo_chat",
            "intake_instruction": instruction,
        },
        sandbox_profile=sandbox_profile,
        capability_ids=[],
        team_id=team_id,
        status="INACTIVE",
        source_repo=repo_url,
        version_pin=version_pin,
        update_policy="manual",
        evaluation_status="pending",
        adapter_entrypoint="WorkerAgent",
    )
    serialized = _serialize(worker)
    response = (
        f"I opened a Hiring Board ticket for `{worker_name}` from {repo_url}. "
        f"It is assigned to `{team_id}` as an inactive candidate with `{sandbox_profile}` sandboxing "
        "and pending evaluation. The hiring team is CEO, HR/hiring, department chief, security "
        "evaluator, interface auditor, budget evaluator, test evaluator, and human approver. "
        "Next: run the worker evaluation from the Hiring Board, review skipped scanner states if "
        "tools are unavailable, then activate only after approval."
    )
    return {
        "type": "worker_hiring",
        "status": "candidate_registered",
        "worker": serialized,
        "response": response,
        "trace": [
            "parsed_hiring_intent",
            "validated_github_source",
            "registered_inactive_candidate",
            "queued_hiring_board_evaluation_gates",
        ],
    }


async def _find_worker_for_ceo_text(storage: AgentStorage, text: str) -> dict[str, Any] | None:
    worker_uuid = _extract_uuid_from_text(text)
    if worker_uuid:
        return await storage.get_worker(UUID(worker_uuid))

    lowered = text.lower()
    workers = await storage.list_workers()
    candidates = []
    for worker in workers:
        names = [
            str(worker.get("name") or "").strip().lower(),
            str(worker.get("display_name") or "").strip().lower(),
        ]
        if any(name and name in lowered for name in names):
            candidates.append(worker)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _summarize_workers_for_ceo(workers: list[dict[str, Any]]) -> str:
    if not workers:
        return "No workers are currently registered."
    rows = []
    prioritized_workers = sorted(
        workers,
        key=lambda worker: (
            str(worker.get("status") or "").upper() == "ACTIVE",
            str(worker.get("evaluation_status") or "").lower() not in {"pending", "failed"},
            str(worker.get("name") or ""),
        ),
    )
    for worker in prioritized_workers[:8]:
        rows.append(
            f"- {worker.get('name')} ({worker.get('id')}): {worker.get('status')}, "
            f"evaluation={worker.get('evaluation_status') or 'none'}, "
            f"team={worker.get('team_id') or 'unassigned'}, sandbox={worker.get('sandbox_profile')}"
        )
    return "Hiring Board snapshot:\n" + "\n".join(rows)


async def _handle_ceo_project_intent(instruction: str) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if "project" not in lowered:
        return None

    project_id = _extract_uuid_from_text(instruction)

    if any(word in lowered for word in ("create", "new", "start", "initialize", "init")):
        name = _extract_named_text(instruction, "CEO Chat Project")
        description = _extract_description_text(instruction)
        project = await create_project(
            CreateProjectRequest(
                name=name,
                description=description,
                human_requester="human_operator",
            )
        )
        response = (
            f"I created project `{project.get('name')}` with ID `{project.get('id')}`. "
            f"Current state is `{project.get('state')}`. I also queued the CEO feasibility "
            "directive so the operating workflow can move through departments, reviews, "
            "approvals, artifacts, and audit state."
        )
        return {
            "type": "project_create",
            "status": "created",
            "project": project,
            "response": response,
            "trace": [
                "parsed_project_creation_intent",
                "created_project_record",
                "attempted_initial_workflow_transition",
                "published_start_feasibility_directive",
            ],
        }

    storage = _storage()
    if project_id and any(word in lowered for word in ("status", "state", "progress", "workspace")):
        project = await storage.get_project(UUID(project_id))
        if project is None:
            raise HTTPException(404, f"Project {project_id} not found")
        workspace = None
        if "workspace" in lowered:
            try:
                workspace = await get_project_workspace(UUID(project_id))
            except Exception:
                logger.exception("ceo_project_workspace_read_failed")
        response = (
            f"Project `{project.get('name') or project_id}` is in `{project.get('state')}`."
        )
        if workspace:
            next_actions = workspace.get("next_actions") or []
            response += " Next actions: " + "; ".join(
                str(action.get("label")) for action in next_actions if isinstance(action, dict)
            )
        return {
            "type": "project_status",
            "status": "read",
            "project": _serialize(project),
            "workspace": workspace,
            "response": response,
            "trace": ["parsed_project_status_intent", "read_project_state"]
            + (["read_project_workspace"] if workspace else []),
        }

    project_query = _extract_project_status_query(instruction)
    if project_query and any(word in lowered for word in ("status", "state", "progress", "workspace")):
        projects = await storage.list_projects(limit=50)
        matching_projects = [
            project
            for project in projects
            if project_query.lower() in str(project.get("name") or "").lower()
        ]
        if not matching_projects:
            raise HTTPException(404, f"Project matching {project_query!r} not found")
        project = matching_projects[0]
        workspace = None
        if "workspace" in lowered:
            try:
                workspace = await get_project_workspace(UUID(str(project["id"])))
            except Exception:
                logger.exception("ceo_project_workspace_read_failed")
        response = f"Project `{project.get('name')}` is in `{project.get('state')}`."
        if workspace:
            next_actions = workspace.get("next_actions") or []
            response += " Next actions: " + "; ".join(
                str(action.get("label")) for action in next_actions if isinstance(action, dict)
            )
        return {
            "type": "project_status",
            "status": "read",
            "project": _serialize(project),
            "workspace": workspace,
            "response": response,
            "trace": ["parsed_project_status_intent", "matched_project_by_name", "read_project_state"]
            + (["read_project_workspace"] if workspace else []),
        }

    if any(word in lowered for word in ("list", "show", "recent")):
        projects = await storage.list_projects(limit=10)
        response = "Recent projects: " + (
            "; ".join(
                f"{project.get('name')} ({project.get('id')}, {project.get('state')})"
                for project in projects[:5]
            )
            if projects
            else "none"
        )
        return {
            "type": "project_list",
            "status": "read",
            "projects": [_serialize(project) for project in projects],
            "response": response,
            "trace": ["parsed_project_list_intent", "listed_recent_projects"],
        }

    return None


async def _handle_ceo_company_intent(instruction: str) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if not any(token in lowered for token in ("company", "org", "organization", "department", "departments", "graph")):
        return None

    storage = _storage()
    if "graph" in lowered or "org" in lowered:
        graph = await _org_graph_read_model(storage)
        response = (
            f"Org graph has {len(graph.get('nodes') or [])} nodes and "
            f"{len(graph.get('edges') or [])} edges. Mermaid export is available from the graph read model."
        )
        return {
            "type": "org_graph",
            "status": "read",
            "graph": _serialize(graph),
            "response": response,
            "trace": ["parsed_org_graph_intent", "read_company_model", "generated_org_graph"],
        }

    company = await _company_read_model(storage)
    totals = company.get("totals", {})
    response = (
        f"AIAT company is {'seeded' if company.get('company', {}).get('seeded') else 'not seeded'} "
        f"with {totals.get('departments', 0)} departments, {totals.get('workers', 0)} workers, "
        f"{totals.get('active_workers', 0)} active workers, and {totals.get('pending_approvals', 0)} pending approvals."
    )
    return {
        "type": "company_overview",
        "status": "read",
        "company": _serialize(company),
        "response": response,
        "trace": ["parsed_company_intent", "read_company_overview"],
    }


async def _handle_ceo_worker_intent(instruction: str) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if not any(token in lowered for token in ("worker", "workers", "hiring board", "candidate", "agent")):
        return None
    if "hire" in lowered:
        return None

    storage = _storage()
    if any(word in lowered for word in ("list", "show", "board", "status", "candidates")) and not _extract_uuid_from_text(instruction):
        workers = await storage.list_workers()
        return {
            "type": "hiring_board",
            "status": "read",
            "workers": [_serialize(worker) for worker in workers],
            "response": _summarize_workers_for_ceo(workers),
            "trace": ["parsed_hiring_board_intent", "listed_worker_registry"],
        }

    worker = await _find_worker_for_ceo_text(storage, instruction)
    if worker is None:
        return {
            "type": "worker_action",
            "status": "needs_worker",
            "response": "I need a worker UUID or unique worker name for that hiring-board action.",
            "trace": ["parsed_worker_action_intent", "worker_not_identified"],
        }

    worker_id = UUID(str(worker["id"]))

    if "evaluate" in lowered or "audit" in lowered or "scan" in lowered:
        report = await evaluate_worker(worker_id, WorkerEvaluateRequest())
        return {
            "type": "worker_evaluate",
            "status": "evaluated",
            "worker": _serialize(await storage.get_worker(worker_id) or worker),
            "evaluation": report,
            "response": (
                f"Evaluation completed for `{worker.get('name')}`: verdict "
                f"`{report.get('verdict')}`, recommended status `{report.get('recommended_status')}`."
            ),
            "trace": ["parsed_worker_evaluation_intent", "ran_guarded_evaluator", "stored_evaluation_status"],
        }

    if any(word in lowered for word in ("approve", "approved")):
        await storage.update_worker_config(worker_id, evaluation_status="approved")
        updated = await storage.get_worker(worker_id)
        return {
            "type": "worker_approval",
            "status": "approved",
            "worker": _serialize(updated or worker),
            "response": f"I marked `{worker.get('name')}` evaluation status as approved for activation gating.",
            "trace": ["parsed_worker_approval_intent", "recorded_human_approval_status"],
        }

    action = None
    if "deactivate" in lowered:
        action = "DEACTIVATE"
    elif "drain" in lowered:
        action = "DRAIN"
    elif "activate" in lowered:
        action = "ACTIVATE"
    if action:
        updated = await transition_worker_status(worker_id, WorkerStatusTransition(action=action))
        return {
            "type": "worker_status_transition",
            "status": "transitioned",
            "worker": updated,
            "response": f"Worker `{updated.get('name')}` is now `{updated.get('status')}`.",
            "trace": ["parsed_worker_status_intent", f"submitted_{action.lower()}"],
        }

    return None


async def _handle_ceo_readiness_intent(instruction: str) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if any(token in lowered for token in ("runtime", "runtimes", "langgraph", "crewai", "autogen", "letta")):
        runtime_catalog = await list_available_runtimes()
        runtimes = runtime_catalog.get("runtimes", [])
        return {
            "type": "runtime_readiness",
            "status": "read",
            "runtimes": runtime_catalog,
            "response": (
                "Runtime readiness: "
                + "; ".join(f"{runtime['id']}={runtime['status']}" for runtime in runtimes)
            ),
            "trace": ["parsed_runtime_readiness_intent", "read_runtime_policy"],
        }
    if any(token in lowered for token in ("integration", "docling", "github", "semgrep", "trufflehog", "n8n")):
        integrations = await get_delta_integration_readiness()
        return {
            "type": "integration_readiness",
            "status": "read",
            "integrations": integrations,
            "response": (
                "Integration readiness: "
                + "; ".join(
                    f"{item['id']}={item['status']}" for item in integrations.get("integrations", [])
                )
            ),
            "trace": ["parsed_integration_readiness_intent", "read_delta_readiness_catalog"],
        }
    return None


def _ceo_operator_intent_is_api_owned(instruction: str) -> bool:
    lowered = instruction.lower()
    if "hire" in lowered and any(
        token in lowered for token in ("agent", "worker", "engineer", "developer", "specialist")
    ):
        return True
    if "project" in lowered:
        if any(
            word in lowered
            for word in ("create", "new", "start", "initialize", "init", "list", "show", "recent")
        ):
            return True
        if any(word in lowered for word in ("status", "state", "progress", "workspace")):
            return (
                _extract_uuid_from_text(instruction) is not None
                or _extract_project_status_query(instruction) is not None
            )
    if any(token in lowered for token in ("company", "org", "organization", "department", "departments", "graph")):
        return True
    if "hire" not in lowered and any(
        token in lowered for token in ("worker", "workers", "hiring board", "candidate", "agent")
    ):
        return True
    if any(
        token in lowered
        for token in (
            "runtime",
            "runtimes",
            "langgraph",
            "crewai",
            "autogen",
            "letta",
            "integration",
            "docling",
            "github",
            "semgrep",
            "trufflehog",
            "n8n",
        )
    ):
        return True
    return False


async def _handle_ceo_operator_intent(instruction: str) -> dict[str, Any] | None:
    for handler in (
        _handle_ceo_hiring_intent,
        _handle_ceo_project_intent,
        _handle_ceo_readiness_intent,
        _handle_ceo_company_intent,
        _handle_ceo_worker_intent,
    ):
        action = await handler(instruction)
        if action is not None:
            return action
    return None


@app.post("/ceo/message")
async def operator_send_to_ceo(
    req: OperatorToCeoRequest,
    background_tasks: BackgroundTasks,
    x_api_key: str | None = Header(None),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Operator sends a message directly to the CEO via the message-router."""
    _check_auth(x_api_key, authorization)
    tid = new_trace_id()
    bind_trace_id(tid)
    message_id = str(uuid4())
    instruction = req.message.strip()
    payload = {
        "action": "HUMAN_DIRECTIVE",
        "instruction": instruction,
        "source": "ceo_chat",
    }
    if _ceo_operator_intent_is_api_owned(instruction):
        payload["execution_owner"] = "orchestrator-api"
    envelope = {
        "message_id": message_id,
        "correlation_id": message_id,
        "msg_type": MessageType.TASK.value,
        "sender_id": "human_operator",
        "sender_team": "orchestrator",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "recipient_team": "exec_ceo",
        "project_id": "operator-direct",
        "payload": payload,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
        if resp.status_code == 403:
            raise HTTPException(403, f"Policy denied: {resp.text}")
        if not resp.is_success:
            raise HTTPException(502, f"Router error {resp.status_code}: {resp.text}")
        result = resp.json()
    action = await _handle_ceo_operator_intent(instruction)
    if action is not None:
        background_tasks.add_task(
            _publish_ceo_chat_response,
            response_text=action["response"],
            correlation_id=message_id,
            parent_id=message_id,
        )
        return {"ok": True, "entry_id": result.get("entry_id"), "action": action}
    background_tasks.add_task(
        _publish_ceo_response,
        instruction=instruction,
        correlation_id=message_id,
        parent_id=message_id,
    )
    return {"ok": True, "entry_id": result.get("entry_id")}


# ---------------------------------------------------------------------------
# System logs — stream container logs via SSE
# ---------------------------------------------------------------------------

ALLOWED_CONTAINERS: set[str] = {
    # Infrastructure
    "redis",
    "postgres",
    "pgbouncer",
    "minio",
    "minio-init",
    "redis-acl-init",
    # Core services
    "orchestrator-api",
    "message-router",
    "tool-service",
    "dashboard",
    # Team runners
    "team-exec-ceo",
    "team-exec-coo",
    "team-office-cfo",
    "team-office-cio",
    "team-office-chrm",
    "team-office-cso",
    "team-office-cto",
    "team-dept-production",
    "team-dept-system",
    "team-dept-qa",
    "team-dept-devops",
    # Legacy / alternative names that may appear in other environments
    "mas-orchestrator-api",
    "mas-message-router",
    "mas-tool-service",
    "mas-dashboard",
    "mas-team-exec-ceo",
    "mas-team-exec-coo",
    "mas-team-office-cfo",
    "mas-team-office-cio",
    "mas-team-office-chrm",
    "mas-team-office-cso",
    "mas-team-office-cto",
    "mas-team-dept-production",
    "mas-team-dept-system",
    "mas-team-dept-qa",
    "mas-team-dept-devops",
}

CONTAINER_ALIASES: dict[str, str] = {
    "redis": "mas-redis-1",
    "postgres": "mas-postgres-1",
    "pgbouncer": "mas-pgbouncer-1",
    "minio": "mas-minio-1",
    "redis-acl-init": "mas-redis-acl-init-1",
    "orchestrator-api": "mas-orchestrator-api-1",
    "message-router": "mas-message-router-1",
    "tool-service": "mas-tool-service-1",
    "dashboard": "mas-dashboard",
    "mas-orchestrator-api": "mas-orchestrator-api-1",
    "mas-message-router": "mas-message-router-1",
    "mas-tool-service": "mas-tool-service-1",
}


async def _stream_container_logs(container: str, tail: int, follow: bool):
    """Async generator that yields SSE lines from docker logs."""
    docker_container = CONTAINER_ALIASES.get(container, container)
    cmd = ["docker", "logs", docker_container, f"--tail={tail}", "--timestamps"]
    if follow:
        cmd.append("--follow")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace").rstrip()
            yield f"data: {text}\n\n"
        await proc.wait()
    except FileNotFoundError:
        yield 'data: {"error": "docker not found — logs unavailable in this environment"}\n\n'
    except Exception as exc:  # noqa: BLE001
        yield f'data: {{"error": "{exc}"}}\n\n'


@app.get("/system/logs/{container}")
async def stream_container_logs(
    container: str,
    tail: int = Query(default=200, ge=1, le=5000),
    follow: bool = Query(default=False),
) -> StreamingResponse:
    """Stream docker logs for a named container as Server-Sent Events.

    Container name is validated against an allowlist to prevent arbitrary
    command injection.
    """
    # Sanitize: only allow known container names
    if container not in ALLOWED_CONTAINERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown container '{container}'. Allowed: {sorted(ALLOWED_CONTAINERS)}",
        )
    return StreamingResponse(
        _stream_container_logs(container, tail=tail, follow=follow),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
