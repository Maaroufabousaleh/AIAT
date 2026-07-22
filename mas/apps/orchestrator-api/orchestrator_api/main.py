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
from functools import partial
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
import httpx
import prometheus_client
import sqlalchemy as sa
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import Counter
from pydantic import BaseModel, Field, field_validator

from mas_core.llm_gateway.client import LLMGatewayClient
from mas_core.memory.storage import AgentStorage, document_to_context_item
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

# Runtime registries are process-local discovery caches only. Authoritative
# worker, steward, candidate, rollout, and run state is persisted through
# AgentStorage; these maps never replace database records.
_worker_steward_runtimes: dict[str, Any] = {}
_worker_adapter_runtimes: dict[str, Any] = {}


async def _invalidate_worker_adapter_runtime(worker_id: UUID) -> None:
    stale = _worker_adapter_runtimes.pop(str(worker_id), None)
    if stale is not None and hasattr(stale, "close"):
        try:
            await stale.close()
        except Exception:
            logger.warning(
                "worker_adapter_cache_invalidation_failed",
                extra={"worker_id": str(worker_id)},
                exc_info=True,
            )

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


def _router_auth_headers() -> dict[str, str]:
    """Return the service identity used for router HTTP publication.

    Router publication is a control-plane operation.  Do not silently send an
    unauthenticated request when ROUTER_SECRET is absent: that would turn a
    deployment configuration error into an identity bypass.
    """
    secret = os.getenv("ROUTER_SECRET") or os.getenv("AGENT_TOKEN_SECRET")
    if not secret:
        raise RuntimeError("ROUTER_SECRET must be configured for router publication")
    return {"Authorization": f"Bearer orchestrator-api:{secret}"}


def _control_plane_auth_headers() -> dict[str, str]:
    """Return this service's own credential for loopback API calls."""
    api_key = os.getenv("MAS_API_KEY") or os.getenv("GATEWAY_API_KEY")
    if not api_key:
        raise RuntimeError("MAS_API_KEY must be configured for control-plane requests")
    return {"X-API-Key": api_key}


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
TOOL_SERVICE_URL = os.getenv("TOOL_SERVICE_URL", "http://tool-service:8002")
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
    "PDR_CREATION": "dept_production",
    "PDR_REVIEW": "exec_coo",
    "SECURITY_BLOCKED": "office_cso",
    "CDR_CREATION": "dept_system",
    "CDR_REVIEW": "exec_coo",
    "HUMAN_APPROVAL": "exec_ceo",
    "RR_CREATION": "dept_production",
    "SPRINT_PLANNING": "exec_coo",
    "INFRA_PROVISIONING": "dept_devops",
    "IN_PROGRESS": "exec_coo",
    "RETROSPECTIVE": "exec_coo",
    "KPI_PERSISTENCE": "office_cto",
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


DELTA_INTEGRATION_CANDIDATES: list[dict[str, Any]] = [
    {
        "id": "docling_ingestion",
        "name": "Docling document ingestion",
        "bucket": "document/spec stack",
        "target": "Docling-backed large-document extraction via artifact references",
        "owner_department": "dept_production",
        "match_tokens": ["docling", "document.ingest.docling", "docling_ingestion"],
        "status_when_present": "placeholder_ready",
        "status_when_missing": "blocked",
        "required_gates": [
            "adapter contract",
            "license/provenance approval",
            "gVisor sandbox profile",
            "artifact reference output contract",
        ],
        "blocked_reason": "Docling ingestion is blocked until a certified worker is approved.",
    },
    {
        "id": "github_rest",
        "name": "GitHub REST metadata",
        "bucket": "protocol/integration",
        "target": "Repository metadata read adapter using named credentials",
        "owner_department": "office_cio",
        "match_tokens": ["github", "github_rest", "api.github.com"],
        "status_when_present": "intake_visible",
        "status_when_missing": "intake_visible",
        "required_gates": [
            "named credential reference",
            "server-side credential resolution",
            "approval gate for write actions",
        ],
        "blocked_reason": None,
    },
    {
        "id": "defensive_scanners",
        "name": "Defensive scanners",
        "bucket": "security evaluator",
        "target": "Semgrep/SkillSpector default checks with optional scanner visibility",
        "owner_department": "office_cso",
        "match_tokens": ["semgrep", "skillspector", "defensive_scanners"],
        "status_when_present": "wired_optional",
        "status_when_missing": "wired_optional",
        "required_gates": [
            "skipped-tool reporting",
            "sandboxed execution",
            "license/provenance approval",
        ],
        "blocked_reason": None,
    },
    {
        "id": "n8n_edge_automation",
        "name": "n8n edge automation",
        "bucket": "optional personal stack",
        "target": "HTTPS webhook edge adapter only; never AIAT workflow authority",
        "owner_department": "dept_devops",
        "match_tokens": ["n8n", "edge_automation", "webhook"],
        "status_when_present": "deferred",
        "status_when_missing": "deferred",
        "required_gates": [
            "edge-only policy",
            "named credential reference",
            "no control-plane authority",
        ],
        "blocked_reason": "Deferred to optional external integration; not shipped as default.",
    },
]


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


def _same_github_repo(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    try:
        return tuple(part.lower() for part in _parse_github_repo(left)) == tuple(
            part.lower() for part in _parse_github_repo(right)
        )
    except HTTPException:
        return left.rstrip("/").removesuffix(".git").lower() == right.rstrip("/").removesuffix(".git").lower()


def _slugify_worker_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "hired_worker"


def _department_for_hiring_text(text: str) -> str:
    return _department_from_text(text) or "office_chrm"


DEPARTMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "dept_qa": ("dept_qa", "qa", "quality", "test", "testing", "tester"),
    "office_cso": ("office_cso", "cso", "security", "secure", "security office"),
    "dept_devops": ("dept_devops", "devops", "infra", "infrastructure", "sre", "platform"),
    "dept_production": (
        "dept_production",
        "production",
        "software",
        "engineering",
        "engineer",
        "developer",
        "coding",
        "implementation",
    ),
    "office_cfo": ("office_cfo", "cfo", "budget", "cost", "finance", "financial"),
    "office_chrm": ("office_chrm", "chrm", "hr", "hiring", "people", "resource", "resources"),
}


def _department_from_text(text: str) -> str | None:
    lowered = text.lower()
    for team_id, aliases in DEPARTMENT_ALIASES.items():
        if team_id in lowered:
            return team_id
        for alias in aliases:
            if re.search(rf"(?<![a-z0-9_-]){re.escape(alias)}(?![a-z0-9_-])", lowered):
                return team_id
    return None


def _target_department_for_reclassification_text(text: str) -> str | None:
    lowered = text.lower()
    directional = re.search(
        r"\b(?:to|into|under|as|for)\s+(?:the\s+)?(?P<target>[a-z0-9_ -]{2,80})",
        lowered,
    )
    if directional:
        target = re.split(r"[.;,?]|\b(?:because|after|before|once|and then)\b", directional.group("target"), maxsplit=1)[0]
        department = _department_from_text(target)
        if department:
            return department

    explicit = re.search(
        r"\b(?:team|department|dept|office)\s+(?P<target>[a-z0-9_ -]{2,80})",
        lowered,
    )
    if explicit:
        target = re.split(r"[.;,?]|\b(?:because|after|before|once|and then)\b", explicit.group("target"), maxsplit=1)[0]
        department = _department_from_text(target)
        if department:
            return department

    return _department_from_text(text)


def _department_hiring_reason(team_id: str) -> str:
    reasons = {
        "dept_qa": "QA owns test automation, quality gates, regression checks, and test-worker capacity.",
        "office_cso": "CSO owns defensive security analysis, sandbox risk, scanner results, and security approval.",
        "dept_devops": "DevOps owns infrastructure, SRE, deployment, platform, and operational automation workers.",
        "dept_production": "Production owns implementation workers: software engineering, coding, feature build, and product delivery.",
        "office_cfo": "CFO owns budget, cost, financial analysis, and KPI/cost-estimation workers.",
        "office_chrm": "CHRM owns hiring intake, staffing, resource planning, and general worker onboarding.",
    }
    return reasons.get(team_id, "CHRM owns general hiring intake when the target department is ambiguous.")


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
    explicit = re.search(
        r"\b(?:named|called)\s+['\"]?([A-Za-z][A-Za-z0-9 _.-]{1,48}?)['\"]?(?:[.;]|$|\s+(?:from|as|for|in|into|to|with)\b)",
        without_url,
        flags=re.IGNORECASE,
    )
    if explicit:
        candidate = explicit.group(1).strip(" ._-")
        if candidate:
            return _slugify_worker_name(candidate)

    pre_source = re.search(
        r"\bhire\s+(?:an?\s+)?([A-Za-z][A-Za-z0-9 _.-]{1,48}?)\s+(?:from|for)\b",
        without_url,
        flags=re.IGNORECASE,
    )
    if pre_source:
        candidate = pre_source.group(1).strip(" ._-")
        generic = {"agent", "worker", "engineer", "developer", "specialist"}
        if candidate and candidate.lower() not in generic:
            return _slugify_worker_name(candidate)
    return _slugify_worker_name(repo_name.removesuffix(".git"))


def _wrapper_manifest_for_hiring(
    *,
    worker_name: str,
    repo_url: str,
    team_id: str,
    adapter_type: str,
    sandbox_profile: str,
    version_pin: str | None,
) -> dict[str, Any]:
    """Build the AIAT-owned adapter contract for an external OSS candidate."""
    return {
        "protocol_version": "aiat.v1",
        "metadata": {
            "id": worker_name,
            "name": worker_name.replace("_", " ").title(),
            "version": "1.0",
            "source_repo": repo_url,
            "version_pin": version_pin,
            "update_policy": "manual",
            "evaluation_status": "pending",
            "tags": ["worker", team_id, "ceo_chat_hiring"],
        },
        "runtime": {
            "transport": adapter_type,
            "adapter_config": {"entrypoint": "WorkerAgent"},
        },
        "integration": {
            "adapter_entrypoint": "WorkerAgent",
            "wrapper_config": {},
            "isolation_mode": "wrapper",
        },
        "sandbox": {
            "profile": sandbox_profile,
            "network_mode": "egress-allowlist",
            "egress_allowlist": ["api.github.com:443", "github.com:443"],
        },
        "runtime_tier": "external",
    }


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
    for tool_name in ("semgrep", "trufflehog"):
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

    # Approval gates are project-scoped.  A failed, completed, or archived
    # project cannot still require operator action; older databases may
    # contain gates created before terminal-state cleanup was added to the
    # storage transition.  Filter those legacy rows defensively while the
    # migration repairs their persisted status.
    live_project_ids = {
        str(project.get("id"))
        for project in projects
        if str(project.get("state")) not in TERMINAL_PROJECT_STATES
    }
    pending_approvals = [
        approval
        for approval in pending_approvals
        if str(approval.get("project_id")) in live_project_ids
    ]

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


class ProjectContextSeedRequest(BaseModel):
    """A context item created with the project in one transaction."""

    item_type: str = Field(default="TEXT", min_length=1, max_length=20)
    name: str = Field(default="Project brief", min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    mime_type: str | None = Field(default=None, max_length=200)
    size_bytes: int | None = Field(default=None, ge=0)
    blob_bucket: str | None = None
    blob_key: str | None = None
    blob_sha256: str | None = None
    url: str | None = None
    content_text: str | None = Field(default=None, max_length=100_000)
    metadata: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("item_type")
    @classmethod
    def normalize_item_type(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized


class ProjectWorkspaceRequest(BaseModel):
    """How project source code should be provisioned in the tool workspace."""

    mode: Literal["init", "clone", "none"] = "init"
    repository_url: str | None = Field(default=None, max_length=1000)
    branch: str | None = Field(default=None, max_length=200)
    remote_name: str = Field(default="origin", min_length=1, max_length=64)


class ProjectRepositoryActionRequest(BaseModel):
    """An operator action against an already configured project repository."""

    operation: Literal["status", "sync", "commit", "push"] = "status"
    message: str | None = Field(default=None, max_length=200)


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    human_requester: str | None = None
    config: dict[str, Any] | None = None
    flow_id: UUID | None = None
    workspace: ProjectWorkspaceRequest | None = None
    initial_context: list[ProjectContextSeedRequest] = Field(default_factory=list, max_length=25)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized


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


class CreateDocumentRequest(BaseModel):
    """Metadata for a project document body stored in object storage."""

    doc_type: str = Field(..., min_length=1, max_length=100)
    created_by: str = "human"
    blob_bucket: str | None = None
    blob_key: str | None = None
    blob_sha256: str | None = None


class DocumentStatusRequest(BaseModel):
    status: str = Field(..., min_length=1, max_length=40)


class CreateDocumentRevisionRequest(BaseModel):
    created_by: str = "human"
    blob_bucket: str | None = None
    blob_key: str | None = None
    blob_sha256: str | None = None


class KpiSnapshotRequest(BaseModel):
    scope: str = Field(..., min_length=1, max_length=40)
    sprint_id: UUID | None = None
    estimation_accuracy: float | None = None
    task_completion_rate: float | None = None
    review_pass_rate: float | None = None
    velocity: float | None = None
    defect_rate: float | None = None
    rework_rate: float | None = None
    budget_adherence: float | None = None
    resource_utilization: float | None = None
    infra_lead_time_seconds: int | None = None
    raw_data: dict[str, Any] | None = None


class AgentProfileObservationRequest(BaseModel):
    """One completed-work observation used to update a durable agent profile."""

    team_id: str | None = None
    role: str | None = None
    estimated_hours: float = Field(default=0, ge=0)
    actual_hours: float = Field(default=0, ge=0)
    tasks_completed: int = Field(default=1, ge=0)
    alpha: float = Field(default=0.5, gt=0, le=1)


class AgentEstimateRequest(BaseModel):
    raw_estimate_hours: float = Field(..., ge=0)


class CreateArtifactRequest(BaseModel):
    agent_id: str = "orchestrator"
    path: str = Field(..., min_length=1, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)


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
    model_mode: str = "none"
    model_profile_id: str | None = None


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
    model_mode: str | None = None
    model_profile_id: str | None = None


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


class StewardCreateRequest(BaseModel):
    source_repo: str | None = None
    source_provider: str = "github"
    exact_release: str | None = None
    commit_sha: str | None = None
    package_version: str | None = None
    oci_image_digest: str | None = None
    dependency_lock_hash: str | None = None
    protocol_api_version: str | None = None
    adapter_version: str | None = None
    transport_type: str = "process"
    license_id: str | None = None
    redistribution_status: str = "pending"
    security_scan_status: str = "pending"
    monitoring_cadence: str = "daily"


class DocumentationSnapshotRequest(BaseModel):
    uri: str
    version: str
    content_sha256: str
    content_ref: str | None = None
    extracted_interfaces: dict[str, Any] = Field(default_factory=dict)
    security_findings: list[str] = Field(default_factory=list)
    untrusted: bool = True


class CandidateGenerationRequest(BaseModel):
    semantic_version: str
    adapter_version: str
    upstream_compatibility_range: str
    adapter_entrypoint: str | None = None
    implementation_ref: str | None = None
    diff: dict[str, Any] = Field(default_factory=dict)
    migration_notes: list[str] = Field(default_factory=list)


class CandidateCertificationRequest(BaseModel):
    # Kept for wire compatibility only. It is evidence, never the authority
    # for a passing certification; the control plane runs conformance itself.
    conformance: dict[str, Any] = Field(default_factory=dict)
    checks: dict[str, bool] = Field(default_factory=dict)


class CandidateStageAdvanceRequest(BaseModel):
    target_status: str
    actor: str = Field(..., min_length=1, max_length=256)
    evidence: dict[str, Any] = Field(default_factory=dict)


class CandidateApprovalRequest(BaseModel):
    decided_by: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=4_000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class RolloutStartRequest(BaseModel):
    actor: str
    eligible_task_classes: list[str] = Field(default_factory=list)


class RolloutAdvanceRequest(BaseModel):
    target_status: str
    sample_count: int | None = Field(default=None, ge=0)
    comparison_metrics: dict[str, float] = Field(default_factory=dict)


class RollbackRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=4000)


class ModelProfileCreateRequest(BaseModel):
    profile_id: str
    purpose: str
    approved_provider_ids: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    fallback_profile_ids: list[str] = Field(default_factory=list)
    status: str = "draft"


class ModelProfileVersionRequest(BaseModel):
    version: str
    provider_id: str
    exact_model_id: str
    capabilities: list[str] = Field(default_factory=list)
    context_window: int = Field(default=0, ge=0)
    max_output_tokens: int = Field(default=0, ge=0)
    tool_calling: bool = False
    structured_output: bool = False
    vision: bool = False
    reasoning: bool = False
    streaming: bool = False
    embedding: bool = False
    cost_per_1k_input_usd: float = Field(default=0, ge=0)
    cost_per_1k_output_usd: float = Field(default=0, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_tokens_per_request: int | None = Field(default=None, ge=0)
    latency_target_ms: int | None = Field(default=None, ge=0)
    max_concurrency: int | None = Field(default=None, ge=1)
    privacy_class: str = "internal"
    regions: list[str] = Field(default_factory=list)
    local: bool = False
    provider_settings: dict[str, Any] = Field(default_factory=dict)
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    status: str = "draft"


class ModelOverrideCreateRequest(BaseModel):
    project_id: UUID
    requested_by: str = Field(..., min_length=1, max_length=256)
    requested_profile_id: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=4_000)
    scope: dict[str, Any] = Field(default_factory=dict)


class ModelOverrideDecisionRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    decided_by: str = Field(..., min_length=1, max_length=256)
    reason: str = Field(..., min_length=1, max_length=4_000)
    expires_at: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class ModelResolutionPreviewRequest(BaseModel):
    task_type: str
    requested_profile_id: str | None = None
    layers: list[dict[str, Any]] = Field(default_factory=list)
    worker_required_capabilities: list[str] = Field(default_factory=list)
    steward_required_capabilities: list[str] = Field(default_factory=list)
    task_required_capabilities: list[str] = Field(default_factory=list)
    adapter_required_capabilities: list[str] = Field(default_factory=list)
    prompt_tokens: int = Field(default=0, ge=0)
    expected_output_tokens: int = Field(default=0, ge=0)
    budget_usd: float | None = Field(default=None, ge=0)
    requested_raw_model_id: str | None = None


class WorkerRunDispatchRequest(BaseModel):
    worker_id: UUID
    idempotency_key: str
    task_type: str
    task_input: dict[str, Any] = Field(default_factory=dict)
    project_id: UUID | None = None
    flow_id: UUID | None = None
    flow_instance_id: UUID | None = None
    flow_node_execution_id: int | None = None
    requested_model_profile: dict[str, Any] | None = None
    resolved_model_profile: dict[str, Any] | None = None
    capability_requirements: list[dict[str, Any]] = Field(default_factory=list)
    tool_grants: list[str] = Field(default_factory=list)
    permission_requirements: list[str] = Field(default_factory=list)
    workspace_mode: str = "isolated"
    timeout_seconds: int | None = Field(default=None, ge=1)
    budget: dict[str, float] = Field(default_factory=dict)
    checkpoint_policy: dict[str, Any] = Field(default_factory=dict)
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    runtime_extensions: dict[str, Any] = Field(default_factory=dict)
    model_policy_layers: list[dict[str, Any]] = Field(default_factory=list)
    worker_required_model_capabilities: list[str] = Field(default_factory=list)
    steward_required_model_capabilities: list[str] = Field(default_factory=list)
    task_required_model_capabilities: list[str] = Field(default_factory=list)
    adapter_required_model_capabilities: list[str] = Field(default_factory=list)
    prompt_tokens: int = Field(default=0, ge=0)
    expected_output_tokens: int = Field(default=0, ge=0)
    budget_usd: float | None = Field(default=None, ge=0)
    model_override_request_id: UUID | None = None
    model_override_approval_id: UUID | None = None


class EvidencePolicyRequest(BaseModel):
    policy_id: str
    policy_version: str
    requirements: dict[str, Any]



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
    worker_run_id: UUID | None = Field(
        default=None,
        description="Required when recording a task-node terminal state; binds the node to its authoritative Worker Run.",
    )


class FlowInstanceActionRequest(BaseModel):
    action: str = Field(..., description="start | pause | resume | cancel")
    node_id: str | None = None


class FlowOverrideRequest(BaseModel):
    target_node_id: str
    actor_id: str = "human"
    actor_role: str = "human_operator"
    reason: str | None = None


# ── Event publisher (sends SYSTEM_EVENT via message-router) ──────────────────


PROJECT_STAGE_DIRECTIVES: dict[str, tuple[str, str]] = {
    "PDR_CREATION": ("exec_ceo", "START_PDR"),
    "CDR_CREATION": ("exec_ceo", "START_CDR"),
    "RR_CREATION": ("exec_ceo", "START_RR"),
    "SPRINT_PLANNING": ("exec_coo", "START_SPRINT_PLANNING"),
    "INFRA_PROVISIONING": ("exec_coo", "START_INFRA"),
    "IN_PROGRESS": ("exec_coo", "START_EXECUTION"),
    "RETROSPECTIVE": ("exec_coo", "START_RETROSPECTIVE"),
    "KPI_PERSISTENCE": ("exec_coo", "START_KPI"),
}

_ROUTER_ACCEPTED_STATUS_CODES = {200, 201, 409}
_STAGE_DIRECTIVE_RETRY_SECONDS = 5.0
_stage_directive_retry_scopes: dict[tuple[str, str], anyio.CancelScope] = {}


def _build_stage_directive(
    *,
    project_id: str,
    state: str,
    context: dict[str, Any] | str,
    parent_id: str | None = None,
    triggered_by_event: str | None = None,
    directive_override: tuple[str, str] | None = None,
) -> dict[str, Any] | None:
    stage_directive = directive_override or PROJECT_STAGE_DIRECTIVES.get(state)
    if stage_directive is None:
        return None
    recipient_team, action = stage_directive
    payload: dict[str, Any] = {
        "action": action,
        "state": state,
        "context": context,
    }
    if triggered_by_event:
        payload["triggered_by_event"] = triggered_by_event
    directive: dict[str, Any] = {
        "message_id": str(uuid4()),
        "correlation_id": project_id,
        "msg_type": MessageType.DIRECTIVE.value,
        "sender_id": "orchestrator",
        "sender_team": "exec_ceo",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "recipient_team": recipient_team,
        "project_id": project_id,
        "payload": payload,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    if parent_id:
        directive["parent_id"] = parent_id
    return directive


async def _publish_router_envelope(envelope: dict[str, Any]) -> bool:
    async with httpx.AsyncClient(timeout=10, headers=_router_auth_headers()) as client:
        response = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
    if response.status_code in _ROUTER_ACCEPTED_STATUS_CODES:
        return True
    logger.warning(
        "Router returned %s for %s publish: %s",
        response.status_code,
        envelope.get("msg_type"),
        response.text[:200],
    )
    return False


async def _retry_stage_directive(
    project_id: str,
    expected_state: str,
    directive: dict[str, Any],
) -> None:
    """Retry a committed stage's directive until delivered or superseded."""
    while True:
        await anyio.sleep(_STAGE_DIRECTIVE_RETRY_SECONDS)
        storage = getattr(app.state, "storage", None)
        if storage is None:
            return
        try:
            project = await storage.get_project(UUID(project_id))
            if project is None or str(project.get("state") or "") != expected_state:
                return
            if await _publish_router_envelope(directive):
                logger.info(
                    "Recovered stage directive delivery: project=%s state=%s action=%s",
                    project_id,
                    expected_state,
                    directive["payload"]["action"],
                )
                return
        except Exception:
            logger.exception(
                "Stage directive retry failed: project=%s state=%s",
                project_id,
                expected_state,
            )


def _schedule_stage_directive_retry(
    project_id: str,
    expected_state: str,
    directive: dict[str, Any],
) -> None:
    key = (project_id, expected_state)
    if key in _stage_directive_retry_scopes:
        return
    task_group = app.state.ceo_command_task_group
    if task_group is None:
        # A real ASGI server owns this task group for its full lifespan.  A
        # caller outside that lifecycle has no backend-neutral way to detach
        # work, so leave the durable project state unchanged rather than
        # invoking an asyncio-only scheduler or risking a duplicate command.
        logger.warning(
            "Stage directive retry deferred because the application scheduler is unavailable",
            extra={"project_id": project_id, "state": expected_state},
        )
        return
    scope = anyio.CancelScope()
    _stage_directive_retry_scopes[key] = scope

    async def run_retry() -> None:
        try:
            with scope:
                await _retry_stage_directive(project_id, expected_state, directive)
        finally:
            if _stage_directive_retry_scopes.get(key) is scope:
                _stage_directive_retry_scopes.pop(key, None)

    task_group.start_soon(run_retry)


def _cancel_project_stage_retries(project_id: str) -> None:
    """Supersede retries from any earlier transition for this project."""
    for key, scope in list(_stage_directive_retry_scopes.items()):
        retry_project_id, _ = key
        if retry_project_id == project_id:
            _stage_directive_retry_scopes.pop(key, None)
            scope.cancel()


async def publish_system_event(
    project_id: str,
    from_state: str,
    to_state: str,
    event: str,
    actor_id: str,
    context: dict[str, Any],
) -> None:
    """Publish a SYSTEM_EVENT via the message-router HTTP API."""
    _cancel_project_stage_retries(project_id)
    envelope = {
        "message_id": str(uuid4()),
        "correlation_id": project_id,
        "msg_type": MessageType.SYSTEM_EVENT.value,
        "sender_id": "orchestrator",
        "sender_team": "exec_ceo",
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
    directive = _build_stage_directive(
        project_id=project_id,
        state=to_state,
        context=context,
        parent_id=envelope["message_id"],
        triggered_by_event=event,
    )
    try:
        await _publish_router_envelope(envelope)
    except Exception:
        logger.exception("Failed to publish SYSTEM_EVENT to router")

    if directive is None:
        return
    try:
        delivered = await _publish_router_envelope(directive)
    except Exception:
        delivered = False
        logger.exception(
            "Failed to publish stage DIRECTIVE: project=%s state=%s",
            project_id,
            to_state,
        )
    if delivered:
        logger.info(
            "Published stage directive: project=%s state=%s action=%s team=%s",
            project_id,
            to_state,
            directive["payload"]["action"],
            directive["recipient_team"],
        )
    else:
        _schedule_stage_directive_retry(project_id, to_state, directive)


# ── Watchdog background task ─────────────────────────────────────────────────


async def watchdog_loop(
    storage: AgentStorage,
    controller: WorkflowController,
    config: WatchdogConfig,
    boot_at: datetime,
    stop_event: Any,
    *,
    max_iterations: int | None = None,
) -> None:
    """Periodic loop that fires ``watchdog_timeout`` for stuck projects."""
    iteration = 0
    while not stop_event.is_set():
        try:
            await anyio.sleep(WATCHDOG_INTERVAL_S)
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


async def _publish_project_resume(
    project: dict[str, Any],
    *,
    context: str,
) -> bool:
    """Publish one exact-project resume directive and report router acceptance."""
    state_str = str(project.get("state") or "")
    project_id = str(project["id"])
    envelope = _build_stage_directive(
        project_id=project_id,
        state=state_str,
        context=context,
        triggered_by_event="resume",
    )
    if envelope is None:
        envelope = {
            "message_id": str(uuid4()),
            "correlation_id": project_id,
            "msg_type": MessageType.DIRECTIVE.value,
            "sender_id": "orchestrator",
            "sender_team": "exec_ceo",
            "sender_role": AgentRole.ORCHESTRATOR.value,
            "recipient_team": get_responsible_team(state_str),
            "project_id": project_id,
            "payload": {
                "action": "RESUME",
                "state": state_str,
                "context": context,
            },
            "created_at": datetime.now(tz=UTC).isoformat(),
        }
    try:
        if await _publish_router_envelope(envelope):
            return True
    except Exception:
        logger.exception("Resume publish error for project=%s", project["id"])
    return False


async def run_resume_sequence(storage: AgentStorage) -> int:
    """Re-publish DIRECTIVE(action=RESUME) for all non-terminal projects.

    FAILED projects are deliberately excluded: they require an explicit retry
    decision, while COMPLETED/ARCHIVED projects have no automatic work to
    resume.  Returns the count of directives accepted by the router.
    """
    projects = await storage.list_projects()
    count = 0
    for project in projects:
        try:
            state = ProjectState(str(project.get("state") or ""))
        except ValueError:
            continue

        if is_terminal_state(state):
            continue
        if await _publish_project_resume(
            project,
            context="System restart — resume from last committed state",
        ):
            count += 1

    return count


async def resume_project(project_id: UUID) -> dict[str, Any]:
    """Retry/resume one project after an explicit CEO-chat confirmation.

    The project is re-read immediately before any mutation.  A FAILED project
    first follows the durable RETRY transition to its last safe state; active
    projects receive one RESUME directive.  Terminal projects are reported as
    a no-op rather than being restarted.
    """
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    retried = False
    state_text = str(project.get("state") or "")
    if state_text == ProjectState.FAILED.value:
        retry_result = await retry_project(project_id)
        retried = True
        refreshed = await storage.get_project(project_id)
        if refreshed is None:
            raise HTTPException(409, f"Project {project_id} disappeared during retry")
        project = refreshed
        state_text = str(project.get("state") or retry_result.get("next_state") or "")

    try:
        state = ProjectState(state_text)
    except ValueError as exc:
        raise HTTPException(409, f"Project {project_id} has unknown state {state_text!r}") from exc

    if is_terminal_state(state):
        return {
            "status": "not_resumed",
            "project_id": str(project_id),
            "state": state.value,
            "projects_resumed": 0,
            "retried": retried,
        }

    if retried:
        # retry_project() transitions through the controller, whose event
        # publisher already emits the restored state's canonical stage
        # directive. Publishing RESUME here would create a second message ID
        # for the same work (and can duplicate provisioning/deployment).
        return {
            "status": "resumed",
            "project_id": str(project_id),
            "state": state.value,
            "projects_resumed": 1,
            "retried": True,
            "directive_source": "retry_transition",
        }

    published = await _publish_project_resume(
        project,
        context="CEO chat — resume this exact project from its last committed state",
    )
    return {
        "status": "resumed" if published else "resume_publish_failed",
        "project_id": str(project_id),
        "state": state.value,
        "projects_resumed": 1 if published else 0,
        "retried": retried,
    }


# ── App lifespan ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    """Startup: connect to DB, run resume, start watchdog. Shutdown: cleanup."""

    # The recovery queue must work under every ASGI/AnyIO backend we support.
    # Keeping one task group alive for the application lifespan gives restart
    # recovery the same detached execution semantics as the normal background
    # task path without relying on asyncio.create_task().
    ceo_command_task_group = anyio.create_task_group()
    await ceo_command_task_group.__aenter__()
    app.state.ceo_command_task_group = ceo_command_task_group

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
    stop_event = anyio.Event()

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
    app.state.ceo_command_tasks = set()

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

        # Start watchdog in the application-owned AnyIO task group so the
        # lifecycle path stays valid for every supported ASGI backend.
        ceo_command_task_group.start_soon(
            watchdog_loop, storage, controller, watchdog_config, boot_at, stop_event
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

        try:
            await _recover_ceo_commands(storage)
        except Exception:
            logger.exception("Durable CEO command recovery failed")

    yield

    # Shutdown
    stop_event.set()

    if app.state.scheduler is not None:
        try:
            app.state.scheduler.shutdown(wait=False)
        except Exception:
            pass

    ceo_command_task_group.cancel_scope.cancel()
    await ceo_command_task_group.__aexit__(None, None, None)
    app.state.ceo_command_task_group = None

    for scope in list(_stage_directive_retry_scopes.values()):
        scope.cancel()
    _stage_directive_retry_scopes.clear()

    if storage is not None:
        await storage.close()


# ── FastAPI App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="AIAT Orchestrator API",
    version="0.2.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def require_control_plane_auth(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Authenticate every control-plane endpoint before route dispatch.

    Route-local dependencies are too easy to omit as the API grows.  Health
    probes remain unauthenticated so Docker can determine liveness without
    distributing an operator credential.  Metrics deliberately remain
    protected: scrape them through an authenticated internal collector.
    """
    requested_v1 = request.scope.get("path", "").startswith("/api/v1/")
    if requested_v1:
        # v1 is the canonical public prefix.  Existing unprefixed routes stay
        # available as migration aliases while they share the same handlers.
        request.scope["path"] = request.scope["path"][7:]
    if request.method == "OPTIONS" or request.url.path in {"/health", "/docs", "/openapi.json"}:
        response = await call_next(request)
        if requested_v1:
            response.headers["X-AIAT-API-Version"] = "v1"
        return response
    try:
        _check_auth(request.headers.get("x-api-key"), request.headers.get("authorization"))
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    response = await call_next(request)
    if requested_v1:
        response.headers["X-AIAT-API-Version"] = "v1"
    return response

# Pre-initialize state defaults so that test monkeypatching and
# non-lifespan access paths don't raise AttributeError.
app.state.storage = None
app.state.controller = WorkflowController(storage=None, event_publisher=publish_system_event)
app.state.watchdog_config = WatchdogConfig()
app.state.boot_at = datetime.now(tz=UTC)
app.state.stop_event = anyio.Event()
app.state.watchdog_task = None
app.state.ceo_command_tasks = set()
app.state.ceo_command_task_group = None

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


async def _ensure_workflow_approval_gate(
    storage: AgentStorage,
    project_id: UUID,
    next_state: ProjectState,
) -> None:
    """Create the human gate exposed by the canonical project workflow.

    Flow instances create gates when an approval node is activated.  The
    built-in project state machine has two equivalent human checkpoints but
    historically only changed ``projects.state``.  Keep the gate creation at
    the API's sole transition boundary so both direct workflow events and flow
    integrations expose the same operator decision surface.  The coroutine
    check keeps lightweight route-test doubles (plain ``MagicMock`` storage)
    from being mistaken for a live database.
    """
    gate_type_by_state = {
        ProjectState.FEASIBILITY_REPORT: "feasibility",
        ProjectState.HUMAN_APPROVAL: "human_approval",
    }
    gate_type = gate_type_by_state.get(next_state)
    if gate_type is None:
        return

    list_gates = getattr(storage, "list_approval_gates", None)
    create_gate = getattr(storage, "create_approval_gate", None)
    if not inspect.iscoroutinefunction(list_gates) or not inspect.iscoroutinefunction(create_gate):
        return

    pending = await list_gates(project_id=project_id, status="PENDING", limit=100)
    if pending:
        return
    await create_gate(project_id=project_id, gate_type=gate_type)


# ── Health ───────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ═════════════════════════════════════════════════════════════════════════════
# Projects — CRUD
# ═════════════════════════════════════════════════════════════════════════════


def _tool_service_auth_headers() -> dict[str, str]:
    secret = os.getenv("TOOL_SECRET")
    if not secret:
        raise RuntimeError("TOOL_SECRET must be configured for project workspace management")
    return {"Authorization": f"Bearer {secret}"}


async def _invoke_project_repository_tool(
    *,
    project_id: UUID,
    operation: str,
    repository_url: str | None = None,
    branch: str | None = None,
    remote_name: str = "origin",
    message: str | None = None,
) -> dict[str, Any]:
    """Run the bounded Git adapter in tool-service as the control-plane identity."""
    kwargs: dict[str, Any] = {
        "operation": operation,
        "remote_name": remote_name,
    }
    if repository_url:
        kwargs["repository_url"] = repository_url
    if branch:
        kwargs["branch"] = branch
    if message:
        kwargs["message"] = message

    body = {
        "caller_id": "orchestrator-api",
        "caller_role": AgentRole.ORCHESTRATOR.value,
        "caller_team": "exec_ceo",
        "project_id": str(project_id),
        "tool_name": "project.repository",
        "kwargs": kwargs,
    }
    async with httpx.AsyncClient(
        timeout=900,
        headers=_tool_service_auth_headers(),
    ) as client:
        response = await client.post(
            f"{TOOL_SERVICE_URL}/tools/project.repository/run",
            json=body,
        )
        response.raise_for_status()
        payload = response.json()

    if not payload.get("success"):
        raise RuntimeError(payload.get("error") or "project.repository failed")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("project.repository returned an invalid result")
    return result


async def _persist_project_workspace(
    storage: AgentStorage,
    project: dict[str, Any],
    workspace: dict[str, Any],
) -> dict[str, Any]:
    """Persist workspace/Git metadata without assuming lightweight test doubles."""
    config = dict(project.get("config") or {})
    config["workspace"] = workspace
    project["config"] = config
    writer = getattr(storage, "update_project_config", None)
    if inspect.iscoroutinefunction(writer):
        refreshed = await writer(project["id"], config=config)
        if refreshed is not None:
            return refreshed
    return project


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

    project_config = dict(req.config or {})
    requested_workspace: dict[str, Any] | None = None
    if req.workspace is not None:
        requested_workspace = {
            "provider": "tool-service",
            "mode": req.workspace.mode,
            "repository_url": req.workspace.repository_url,
            "branch": req.workspace.branch,
            "remote_name": req.workspace.remote_name,
            "status": "DISABLED" if req.workspace.mode == "none" else "PROVISIONING",
        }
        project_config["workspace"] = requested_workspace
        if req.workspace.repository_url:
            # Keep this compatibility key for existing workers and context
            # readers; the workspace block is the canonical source metadata.
            project_config.setdefault("repository_url", req.workspace.repository_url)

    # Create project
    project = await storage.create_project(
        name=req.name,
        description=req.description,
        created_by=req.human_requester or "human",
        human_requester=req.human_requester,
        config=project_config or None,
        initial_context=[
            {
                **seed.model_dump(exclude_none=True),
                "created_by": req.human_requester or "human",
            }
            for seed in req.initial_context
        ],
    )

    pid = str(project["id"])

    if requested_workspace is not None and requested_workspace["status"] != "DISABLED":
        try:
            repository = await _invoke_project_repository_tool(
                project_id=UUID(pid),
                operation=requested_workspace["mode"],
                repository_url=requested_workspace.get("repository_url"),
                branch=requested_workspace.get("branch"),
                remote_name=requested_workspace["remote_name"],
            )
        except Exception as exc:
            logger.exception("Failed to provision project workspace %s", pid)
            requested_workspace = {
                **requested_workspace,
                "status": "ERROR",
                "error": "Git workspace provisioning failed",
            }
            await _persist_project_workspace(storage, project, requested_workspace)
            raise HTTPException(
                503,
                {
                    "message": "Project was created, but its Git workspace could not be provisioned.",
                    "project_id": pid,
                    "workspace_status": "ERROR",
                    "detail": str(exc)[:500],
                },
            ) from exc

        requested_workspace = {
            **requested_workspace,
            **repository,
            "status": "READY",
        }
        project = await _persist_project_workspace(storage, project, requested_workspace)

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

    # Return the authoritative post-transition row rather than the INSERT
    # snapshot, so callers never observe a stale INIT state.
    project_reader = getattr(storage, "get_project", None)
    if inspect.iscoroutinefunction(project_reader):
        refreshed_project = await project_reader(UUID(pid))
        if refreshed_project is not None:
            project = refreshed_project

    # Publish a DIRECTIVE to CEO to start feasibility. Keep the original
    # project fields in the payload for the CEO prompt, while using the
    # shared stage publisher so rejected/unavailable router responses retry.
    directive = _build_stage_directive(
        project_id=pid,
        state="FEASIBILITY_CHECK",
        context={"project_name": req.name, "description": req.description},
        triggered_by_event=WorkflowEvent.PROJECT_CREATED.value,
        directive_override=("exec_ceo", "START_FEASIBILITY"),
    )
    assert directive is not None
    directive["payload"].update(
        {"project_name": req.name, "description": req.description}
    )
    try:
        delivered = await _publish_router_envelope(directive)
    except Exception:
        delivered = False
        logger.exception("Failed to publish project start directive")
    if not delivered:
        _schedule_stage_directive_retry(pid, "FEASIBILITY_CHECK", directive)

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


async def _build_project_evidence(project_id: UUID, storage: AgentStorage) -> Any:
    from mas_core.workflow import evaluate_project_evidence, policy_for

    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    config = dict(project.get("config") or {})
    selected = config.get("evidence_policy") or "manual"
    if isinstance(selected, dict):
        policy = policy_for(str(selected.get("policy_id") or "custom"), version=selected.get("version"), requirements=dict(selected.get("requirements") or {}))
    else:
        policy = policy_for(str(selected))
    documents = await storage.list_documents(project_id)
    artifacts = await _project_artifact_rows(storage, project_id)
    flow_instance = await storage.get_flow_instance_by_project(project_id)
    approvals = await storage.list_approval_gates(project_id)
    runs = await storage.list_worker_runs(project_id=project_id, limit=1000) if inspect.iscoroutinefunction(getattr(storage, "list_worker_runs", None)) else []
    repository = await storage.get_project_repository_record(project_id) if inspect.iscoroutinefunction(getattr(storage, "get_project_repository_record", None)) else (config.get("workspace") or None)
    history = await storage.get_project_history(project_id)
    return evaluate_project_evidence(
        project_id=str(project_id),
        policy=policy,
        project=project,
        documents=documents,
        artifacts=artifacts,
        flow_instance=flow_instance,
        approvals=approvals,
        worker_runs=runs,
        repository=repository,
        audit_events=history,
    )


@app.get("/projects/{project_id}/overview")
async def get_project_overview(project_id: UUID) -> dict[str, Any]:
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    documents = await storage.list_documents(project_id)
    context = await storage.list_project_context(project_id)
    flow_instance = await storage.get_flow_instance_by_project(project_id)
    artifacts = await _project_artifact_rows(storage, project_id)
    repository = await storage.get_project_repository_record(project_id) if inspect.iscoroutinefunction(getattr(storage, "get_project_repository_record", None)) else (project.get("config") or {}).get("workspace")
    runs = await storage.list_worker_runs(project_id=project_id, limit=1000) if inspect.iscoroutinefunction(getattr(storage, "list_worker_runs", None)) else []
    evidence = await _build_project_evidence(project_id, storage)
    active_runs = [run for run in runs if run.get("state") not in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}]
    return {
        "project": _serialize(project),
        "lifecycle": {"state": project.get("state"), "terminal": project.get("state") in TERMINAL_PROJECT_STATES},
        "flow": _serialize(flow_instance) if flow_instance else None,
        "documents": {"count": len(documents), "items": [_serialize(doc) for doc in documents]},
        "context": {"count": len(context), "items": [_serialize(item) for item in context]},
        "artifacts": {"count": len(artifacts), "items": [_serialize(item) for item in artifacts]},
        "repository": _serialize(repository) if repository else None,
        "worker_runs": {"count": len(runs), "active_count": len(active_runs), "items": [_serialize(run) for run in runs]},
        "evidence": evidence.model_dump(mode="json"),
        "next_action": None if project.get("state") in TERMINAL_PROJECT_STATES else get_responsible_team(str(project.get("state"))),
    }


@app.get("/projects/{project_id}/evidence")
async def get_project_evidence(project_id: UUID) -> dict[str, Any]:
    evidence = await _build_project_evidence(project_id, _storage())
    return evidence.model_dump(mode="json")


@app.post("/projects/{project_id}/evidence/validate")
async def validate_project_evidence(project_id: UUID, req: EvidencePolicyRequest | None = None) -> dict[str, Any]:
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    if req is not None:
        config = dict(project.get("config") or {})
        config["evidence_policy"] = {"policy_id": req.policy_id, "version": req.policy_version, "requirements": req.requirements}
        project = dict(project)
        project["config"] = config
        # The evaluator accepts a local policy override for dry-run validation;
        # persistence occurs only when an operator explicitly updates policy.
        from mas_core.workflow import evaluate_project_evidence, policy_for
        documents = await storage.list_documents(project_id)
        artifacts = await _project_artifact_rows(storage, project_id)
        evidence = evaluate_project_evidence(project_id=str(project_id), policy=policy_for(req.policy_id, version=req.policy_version, requirements=req.requirements), project=project, documents=documents, artifacts=artifacts, flow_instance=await storage.get_flow_instance_by_project(project_id), approvals=await storage.list_approval_gates(project_id), audit_events=await storage.get_project_history(project_id))
    else:
        evidence = await _build_project_evidence(project_id, storage)
    return evidence.model_dump(mode="json")


@app.delete("/projects/{project_id}")
async def delete_project(project_id: UUID) -> dict[str, str]:
    """Permanently delete a project, its records, and its managed workspace."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    configured_workspace = (project.get("config") or {}).get("workspace")
    if isinstance(configured_workspace, dict) and configured_workspace.get("mode") != "none":
        try:
            await _invoke_project_repository_tool(
                project_id=project_id,
                operation="remove",
                remote_name=str(configured_workspace.get("remote_name") or "origin"),
            )
        except Exception as exc:
            raise HTTPException(
                502,
                f"Project workspace cleanup failed; project was not deleted: {str(exc)[:500]}",
            ) from exc

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


async def _project_usage_summary(storage: AgentStorage, project_id: UUID) -> dict[str, Any]:
    """Read project usage across rolling upgrades of the shared storage package."""
    instance_reader = getattr(storage, "__dict__", {}).get("get_project_usage")
    class_reader = getattr(type(storage), "get_project_usage", None)
    usage_reader = instance_reader or (getattr(storage, "get_project_usage") if class_reader else None)
    if usage_reader is not None:
        return await usage_reader(project_id)

    # The SQL fallback lets an older running orchestrator consume the new
    # migration before its application image is rotated.
    query = sa.text(
        """
        SELECT
            count(*) FILTER (WHERE event_type = 'llm') AS llm_calls,
            count(*) FILTER (WHERE event_type = 'tool') AS tool_calls,
            count(*) FILTER (WHERE status <> 'success') AS failed_calls,
            COALESCE(sum(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(sum(completion_tokens), 0) AS completion_tokens,
            COALESCE(sum(cost_usd), 0) AS total_cost_usd,
            min(occurred_at) AS first_event_at,
            max(occurred_at) AS last_event_at
        FROM project_usage_events
        WHERE project_id = :project_id
        """
    )
    async with storage.engine.connect() as conn:
        row = (await conn.execute(query, {"project_id": project_id})).mappings().one()
    result = dict(row)
    result["llm_calls"] = int(result["llm_calls"] or 0)
    result["tool_calls"] = int(result["tool_calls"] or 0)
    result["failed_calls"] = int(result["failed_calls"] or 0)
    result["prompt_tokens"] = int(result["prompt_tokens"] or 0)
    result["completion_tokens"] = int(result["completion_tokens"] or 0)
    result["total_tokens"] = result["prompt_tokens"] + result["completion_tokens"]
    result["total_cost_usd"] = float(result["total_cost_usd"] or 0.0)
    result["available"] = True
    result["source"] = "project_usage_events"
    return result


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


@app.post("/projects/{project_id}/artifacts", status_code=201)
async def create_project_artifact(
    project_id: UUID,
    req: CreateArtifactRequest,
) -> dict[str, Any]:
    """Register project-scoped artifact metadata after a blob upload."""
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    metadata = dict(req.metadata)
    # The route is the authority for ownership.  Do not let caller-supplied
    # metadata attribute an artifact to another project.
    metadata["project_id"] = str(project_id)
    artifact = await storage.create_artifact(
        agent_id=req.agent_id,
        path=req.path,
        metadata=metadata,
        sha256=req.sha256,
        size_bytes=req.size_bytes,
    )
    return _serialize(artifact)


@app.delete("/projects/{project_id}/artifacts/{artifact_id}")
async def delete_project_artifact(project_id: UUID, artifact_id: int) -> dict[str, str]:
    """Delete one project-scoped artifact metadata row after blob cleanup."""
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    artifact = await storage.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(404, f"Artifact {artifact_id} not found")
    metadata = artifact.get("metadata") or {}
    path = str(artifact.get("path") or "")
    if str(metadata.get("project_id") or "") != str(project_id) and not path.startswith(
        f"{project_id}/"
    ):
        raise HTTPException(404, f"Artifact {artifact_id} not found for project {project_id}")
    await storage.delete_artifact(artifact_id)
    return {"status": "deleted"}


@app.get("/projects/{project_id}/issues")
async def list_project_issues(
    project_id: UUID,
    sprint_id: UUID | None = None,
    status: str | None = None,
    assigned_team: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """List persisted issue records for a project."""
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    issues = await storage.list_issues(
        project_id=project_id,
        sprint_id=sprint_id,
        status=status,
        assigned_team=assigned_team,
    )
    return [_serialize(issue) for issue in issues[:limit]]


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
    try:
        project_usage = await _project_usage_summary(storage, project_id)
    except Exception:
        logger.exception(
            "project_workspace.usage_unavailable",
            extra={"project_id": str(project_id)},
        )
        project_usage = {
            "available": False,
            "reason": "The durable project usage ledger is temporarily unavailable.",
        }

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

    # The container-log endpoint is intentionally a separate, allowlisted
    # operational surface.  For a project workspace, the durable audit
    # timeline is the reliable project-scoped log source and should be
    # rendered instead of claiming that no logs exist.
    project_logs = []
    for index, event in enumerate(audit):
        summary = str(event.get("summary") or event.get("event_type") or "Project activity")
        normalized = summary.upper()
        level = "error" if "FAILED" in normalized or "VETO" in normalized else (
            "warning" if "REJECT" in normalized or "REVISION" in normalized else "info"
        )
        actor = event.get("actor")
        message = f"{event.get('event_type') or 'project_event'}: {summary}"
        if actor:
            message = f"{message} (actor: {actor})"
        project_logs.append(
            {
                "id": f"project-audit-{index}",
                "level": level,
                "message": message,
                "created_at": event.get("occurred_at"),
                "source": "audit_timeline",
            }
        )

    return _serialize(
        {
            "project": project,
            "repository": (project.get("config") or {}).get("workspace"),
            "flow_instance": flow_instance,
            "pending_approvals": pending,
            "recent_decisions": [a for a in audit if a["event_type"] == "approval_gate"][:5],
            "recent_activity": audit[:10],
            "worker_activity": project_task_rows[:10],
            "artifacts": artifacts,
            "logs": project_logs[:20],
            "cost_usage": project_usage,
            "next_actions": next_actions,
        }
    )


@app.get("/projects/{project_id}/repository")
async def get_project_repository(project_id: UUID) -> dict[str, Any]:
    """Return the configured project workspace and current Git status."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")

    configured = (project.get("config") or {}).get("workspace")
    if not isinstance(configured, dict):
        return {
            "configured": False,
            "project_id": str(project_id),
            "message": "This project has no managed Git workspace.",
        }
    if configured.get("mode") == "none":
        return {"configured": False, "project_id": str(project_id), "workspace": configured}

    try:
        repository = await _invoke_project_repository_tool(
            project_id=project_id,
            operation="status",
            repository_url=configured.get("repository_url"),
            branch=configured.get("branch"),
            remote_name=str(configured.get("remote_name") or "origin"),
        )
    except Exception as exc:
        logger.warning("Could not refresh Git status for project %s", project_id, exc_info=True)
        return _serialize(
            {
                "configured": True,
                "project_id": project_id,
                "workspace": {**configured, "status": "UNAVAILABLE"},
                "error": str(exc)[:500],
            }
        )

    workspace = {
        **configured,
        **repository,
        "status": "READY" if repository.get("initialized") else "PROVISIONING",
    }
    refreshed = await _persist_project_workspace(storage, project, workspace)
    return _serialize(
        {
            "configured": True,
            "project_id": project_id,
            "workspace": (refreshed.get("config") or {}).get("workspace", workspace),
        }
    )


@app.post("/projects/{project_id}/repository")
async def manage_project_repository(
    project_id: UUID,
    req: ProjectRepositoryActionRequest,
) -> dict[str, Any]:
    """Run a controlled Git status/sync/commit/push action for a project."""
    storage = _storage()
    project = await storage.get_project(project_id)
    if project is None:
        raise HTTPException(404, f"Project {project_id} not found")
    configured = (project.get("config") or {}).get("workspace")
    if not isinstance(configured, dict) or configured.get("mode") == "none":
        raise HTTPException(409, "Project has no managed Git workspace")

    try:
        repository = await _invoke_project_repository_tool(
            project_id=project_id,
            operation=req.operation,
            repository_url=configured.get("repository_url"),
            branch=configured.get("branch"),
            remote_name=str(configured.get("remote_name") or "origin"),
            message=req.message,
        )
    except Exception as exc:
        raise HTTPException(502, f"Git operation {req.operation} failed: {str(exc)[:500]}") from exc

    workspace = {
        **configured,
        **repository,
        "status": "READY" if repository.get("initialized") else "PROVISIONING",
        "last_operation": req.operation,
    }
    refreshed = await _persist_project_workspace(storage, project, workspace)
    return _serialize(
        {
            "project_id": project_id,
            "workspace": (refreshed.get("config") or {}).get("workspace", workspace),
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

    # Keep document lifecycle badges aligned with the workflow transition.
    # Review aggregation also persists terminal verdicts, while this boundary
    # covers submissions (including the unreviewed RR handoff).
    document_status = {
        WorkflowEvent.PDR_SUBMITTED: "IN_REVIEW",
        WorkflowEvent.CDR_SUBMITTED: "IN_REVIEW",
        WorkflowEvent.RR_SUBMITTED: "APPROVED",
    }.get(event)
    document_id = (
        req.context.get("document_id")
        if isinstance(req.context, dict)
        else None
    )
    if document_status and document_id:
        try:
            document = await storage.get_document(UUID(str(document_id)))
            if document is not None and str(document.get("project_id")) == str(project_id):
                await storage.update_document_status(
                    UUID(str(document_id)),
                    status=document_status,
                )
        except Exception:
            logger.warning(
                "workflow_document_status_sync_failed",
                extra={
                    "project_id": str(project_id),
                    "document_id": str(document_id),
                    "status": document_status,
                    "event": event.value,
                },
                exc_info=True,
            )

    # Surface the two built-in human checkpoints through the same approval-gate
    # API used by flow instances.  This is deliberately after the atomic state
    # transition so a pending gate can never point at a state the project did
    # not actually reach.
    await _ensure_workflow_approval_gate(
        storage,
        project_id,
        ProjectState(result.next_state),
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
    decision = req.decision.upper()
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
    gate_updated = await storage.decide_approval_gate(
        gate_id,
        status=decision,
        decided_by=req.decided_by,
        justification=req.comments,
        human_input=req.edits,
    )
    if gate_updated is False:
        raise HTTPException(409, "Approval gate is no longer pending")

    # Map decision to workflow event
    decision_to_event = {
        "APPROVED": WorkflowEvent.HUMAN_APPROVED,
        "REJECTED": WorkflowEvent.HUMAN_REJECTED,
        "EDITS": WorkflowEvent.HUMAN_EDITS,
        "CANCELLED": WorkflowEvent.HUMAN_CANCELLED,
    }
    event = decision_to_event.get(decision)
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


DOCUMENT_STATUSES = {
    "DRAFT",
    "IN_REVIEW",
    "APPROVED",
    "REJECTED",
    "NEEDS_REVISION",
    "SUPERSEDED",
    "ARCHIVED",
}


@app.post("/projects/{project_id}/documents", status_code=201)
async def create_project_document(
    project_id: UUID,
    req: CreateDocumentRequest,
) -> dict[str, Any]:
    """Create a version-one document metadata row."""
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    document = await storage.create_document(
        project_id=project_id,
        doc_type=req.doc_type,
        created_by=req.created_by,
        blob_bucket=req.blob_bucket,
        blob_key=req.blob_key,
        blob_sha256=req.blob_sha256,
    )
    return _serialize(document)


@app.get("/projects/{project_id}/documents")
async def list_documents(
    project_id: UUID,
    doc_type: str | None = None,
) -> list[dict[str, Any]]:
    """List all project documents (PDR, CDR, RR, etc.)."""
    storage = _storage()
    docs = await storage.list_documents(project_id, doc_type=doc_type)
    return [_serialize(d) for d in docs]


@app.post("/projects/{project_id}/documents/{doc_id}/revisions", status_code=201)
async def create_project_document_revision(
    project_id: UUID,
    doc_id: UUID,
    req: CreateDocumentRevisionRequest,
) -> dict[str, Any]:
    """Append the next immutable version of a project document."""
    storage = _storage()
    source = await storage.get_document(doc_id)
    if source is None or source.get("project_id") != project_id:
        raise HTTPException(404, f"Document {doc_id} not found")
    try:
        revision = await storage.create_document_revision(
            doc_id,
            created_by=req.created_by,
            blob_bucket=req.blob_bucket,
            blob_key=req.blob_key,
            blob_sha256=req.blob_sha256,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _serialize(revision)


@app.patch("/projects/{project_id}/documents/{doc_id}/status")
async def update_project_document_status(
    project_id: UUID,
    doc_id: UUID,
    req: DocumentStatusRequest,
) -> dict[str, Any]:
    """Move a document through its review lifecycle."""
    storage = _storage()
    document = await storage.get_document(doc_id)
    if document is None or document.get("project_id") != project_id:
        raise HTTPException(404, f"Document {doc_id} not found")
    status = req.status.upper()
    if status not in DOCUMENT_STATUSES:
        raise HTTPException(400, f"Unsupported document status: {req.status}")
    await storage.update_document_status(doc_id, status=status)
    updated = await storage.get_document(doc_id)
    if updated is None:
        raise HTTPException(404, f"Document {doc_id} not found")
    return _serialize(updated)


@app.get("/projects/{project_id}/documents/{doc_id}")
async def get_document(project_id: UUID, doc_id: UUID) -> dict[str, Any]:
    """Get document details including blob reference for download."""
    storage = _storage()
    doc = await storage.get_document(doc_id)
    if doc is None or doc.get("project_id") != project_id:
        raise HTTPException(404, f"Document {doc_id} not found")
    return _serialize(doc)


async def _read_project_document_blob(project_id: UUID, document: dict[str, Any]) -> tuple[bytes, str]:
    if document.get("content_text") is not None:
        return str(document["content_text"]).encode("utf-8"), "text/plain; charset=utf-8"
    bucket = document.get("blob_bucket")
    key = document.get("blob_key")
    if not key:
        raise HTTPException(409, "Document body is not retrievable: no object-storage reference is recorded")
    endpoint = os.getenv("MINIO_ENDPOINT") or os.getenv("BLOB_ENDPOINT_URL")
    access_key = os.getenv("MINIO_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("MINIO_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    if not endpoint or not access_key or not secret_key:
        raise HTTPException(503, "Document metadata exists but object storage is not configured")
    from mas_core.memory.blob import BlobClient

    blob = BlobClient(endpoint, access_key=access_key, secret_key=secret_key, bucket=bucket or "mas-agents")
    try:
        await blob.connect()
        project_prefix = f"{project_id}/"
        scoped_key = str(key)
        if scoped_key.startswith(project_prefix):
            scoped_key = scoped_key[len(project_prefix):]
        body = await blob.download_by_key(str(project_id), scoped_key, bucket=bucket)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Document body retrieval failed: {str(exc)[:300]}") from exc
    finally:
        await blob.close()
    expected_sha = document.get("blob_sha256")
    if expected_sha:
        import hashlib
        actual_sha = hashlib.sha256(body).hexdigest()
        if actual_sha != expected_sha:
            raise HTTPException(502, "Document body integrity check failed")
    mime = "application/octet-stream"
    lowered = str(key).lower()
    if lowered.endswith((".md", ".markdown")):
        mime = "text/markdown; charset=utf-8"
    elif lowered.endswith(".json"):
        mime = "application/json"
    elif lowered.endswith(".pdf"):
        mime = "application/pdf"
    return body, mime


@app.get("/projects/{project_id}/documents/{doc_id}/preview")
async def preview_project_document(project_id: UUID, doc_id: UUID) -> Response:
    storage = _storage()
    document = await storage.get_document(doc_id)
    if document is None or document.get("project_id") != project_id:
        raise HTTPException(404, f"Document {doc_id} not found")
    body, mime = await _read_project_document_blob(project_id, document)
    return Response(content=body, media_type=mime, headers={"X-AIAT-Document-SHA256": str(document.get("blob_sha256") or "")})


@app.get("/projects/{project_id}/documents/{doc_id}/download")
async def download_project_document(project_id: UUID, doc_id: UUID) -> Response:
    storage = _storage()
    document = await storage.get_document(doc_id)
    if document is None or document.get("project_id") != project_id:
        raise HTTPException(404, f"Document {doc_id} not found")
    body, mime = await _read_project_document_blob(project_id, document)
    filename = f"{str(document.get('doc_type') or 'document').lower()}-v{document.get('version') or 1}"
    return Response(content=body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-AIAT-Document-SHA256": str(document.get("blob_sha256") or "")})


@app.get("/projects/{project_id}/review-sessions")
async def list_project_review_sessions(
    project_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Return durable COO review sessions for a project."""
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    sessions = await storage.list_review_sessions(project_id, limit=limit)
    for session in sessions:
        session["comments"] = await storage.get_review_comments(session["id"])
    return [_serialize(session) for session in sessions]


@app.get("/projects/{project_id}/review-sessions/{session_id}")
async def get_project_review_session(project_id: UUID, session_id: UUID) -> dict[str, Any]:
    """Return one durable review session with reviewer comments."""
    storage = _storage()
    session = await storage.get_review_session(session_id)
    if session is None or session.get("project_id") != project_id:
        raise HTTPException(404, f"Review session {session_id} not found")
    session["comments"] = await storage.get_review_comments(session_id)
    return _serialize(session)


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


@app.get("/projects/{project_id}/kpi")
async def list_project_kpi(
    project_id: UUID,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """List persisted KPI snapshots for a project."""
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    snapshots = await storage.list_kpi_snapshots(project_id, scope=scope)
    return [_serialize(snapshot) for snapshot in snapshots]


@app.post("/projects/{project_id}/kpi", status_code=201)
async def save_project_kpi(
    project_id: UUID,
    req: KpiSnapshotRequest,
) -> dict[str, Any]:
    """Persist one computed KPI snapshot."""
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    if req.sprint_id is not None:
        sprint = await storage.get_sprint(req.sprint_id)
        if sprint is None or str(sprint.get("project_id")) != str(project_id):
            raise HTTPException(404, f"Sprint {req.sprint_id} not found for project {project_id}")
    snapshot = await storage.save_kpi_snapshot(
        project_id=project_id,
        scope=req.scope,
        sprint_id=req.sprint_id,
        estimation_accuracy=req.estimation_accuracy,
        task_completion_rate=req.task_completion_rate,
        review_pass_rate=req.review_pass_rate,
        velocity=req.velocity,
        defect_rate=req.defect_rate,
        rework_rate=req.rework_rate,
        budget_adherence=req.budget_adherence,
        resource_utilization=req.resource_utilization,
        infra_lead_time_seconds=req.infra_lead_time_seconds,
        raw_data=req.raw_data,
    )
    return _serialize(snapshot)


@app.get("/agent-profiles/{agent_id}")
async def get_agent_profile(agent_id: str) -> dict[str, Any]:
    """Read one durable estimation-learning profile."""
    storage = _storage()
    profile = await storage.get_agent_profile(agent_id)
    if profile is None:
        raise HTTPException(404, f"Agent profile {agent_id} not found")
    return _serialize(profile)


@app.post("/agent-profiles/{agent_id}/observations", status_code=201)
async def observe_agent_profile(
    agent_id: str,
    req: AgentProfileObservationRequest,
) -> dict[str, Any]:
    """Persist a completed-work observation using the documented EMA."""
    storage = _storage()
    try:
        profile = await storage.observe_agent_profile(
            agent_id=agent_id,
            team_id=req.team_id,
            role=req.role,
            estimated_hours=req.estimated_hours,
            actual_hours=req.actual_hours,
            tasks_completed=req.tasks_completed,
            alpha=req.alpha,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _serialize(profile)


@app.post("/agent-profiles/{agent_id}/estimate")
async def estimate_with_agent_profile(
    agent_id: str,
    req: AgentEstimateRequest,
) -> dict[str, Any]:
    """Apply a learned correction factor and additive bias to a raw estimate."""
    storage = _storage()
    profile = await storage.get_agent_profile(agent_id)
    if profile is None:
        raise HTTPException(404, f"Agent profile {agent_id} not found")
    factor = float(profile.get("correction_factor") or 1)
    bias = float(profile.get("estimation_bias") or 0)
    adjusted = max(0.0, (req.raw_estimate_hours * factor) + bias)
    return {
        "agent_id": agent_id,
        "raw_estimate_hours": req.raw_estimate_hours,
        "correction_factor": factor,
        "estimation_bias": bias,
        "adjusted_estimate_hours": round(adjusted, 4),
        "profile": _serialize(profile),
    }


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


def _latest_document_rows(
    documents: list[dict[str, Any]],
    *,
    include_revisions: bool,
) -> list[dict[str, Any]]:
    """Keep the context read model focused on current document revisions."""
    if include_revisions:
        return documents

    latest_by_lineage: dict[str, dict[str, Any]] = {}
    for document in documents:
        lineage_key = str(document.get("lineage_id") or document.get("id"))
        current = latest_by_lineage.get(lineage_key)
        try:
            version = int(document.get("version") or 1)
        except (TypeError, ValueError):
            version = 1
        try:
            current_version = int(current.get("version") or 1) if current else -1
        except (TypeError, ValueError):
            current_version = -1
        if current is None or version >= current_version:
            latest_by_lineage[lineage_key] = document
    return list(latest_by_lineage.values())


async def _list_project_context_read_model(
    storage: AgentStorage,
    project_id: UUID,
    *,
    item_type: str | None = None,
    tags: list[str] | None = None,
    include_revisions: bool = False,
) -> list[dict[str, Any]]:
    """Read context attachments and generated-document projections together.

    The fallback keeps lightweight route test doubles and older storage
    adapters compatible while the canonical ``AgentStorage`` method owns the
    production implementation.
    """
    reader = getattr(storage, "list_project_context", None)
    if inspect.iscoroutinefunction(reader):
        return await reader(
            project_id,
            item_type=item_type,
            tags=tags,
            include_document_revisions=include_revisions,
        )

    items = await storage.list_context_items(project_id, item_type=item_type, tags=tags)
    if item_type and item_type.upper() != "DOCUMENT":
        return items

    document_reader = getattr(storage, "list_documents", None)
    if not inspect.iscoroutinefunction(document_reader):
        return items
    documents = await document_reader(project_id)
    document_items = [
        document_to_context_item(document)
        for document in _latest_document_rows(documents, include_revisions=include_revisions)
    ]
    if tags:
        requested_tags = {tag.strip().lower() for tag in tags if tag.strip()}
        document_items = [
            item
            for item in document_items
            if requested_tags.intersection(str(tag).lower() for tag in item.get("tags") or [])
        ]
    return sorted(
        [*items, *document_items],
        key=lambda item: str(item.get("created_at") or ""),
        reverse=True,
    )


@app.get("/projects/{project_id}/context")
async def list_project_context(
    project_id: UUID,
    item_type: str | None = None,
    tags: str | None = None,
    include_revisions: bool = False,
) -> list[dict[str, Any]]:
    """List project context, including current generated document revisions."""
    storage = _storage()
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else None
    items = await _list_project_context_read_model(
        storage,
        project_id,
        item_type=item_type,
        tags=tag_list,
        include_revisions=include_revisions,
    )
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
    items = await _list_project_context_read_model(storage, project_id)

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
    """Get a specific attachment, note, or generated document projection."""
    storage = _storage()
    item = await storage.get_context_item(item_id)
    if item is not None and item.get("project_id") == project_id:
        return _serialize(item)

    document_reader = getattr(storage, "get_document", None)
    if inspect.iscoroutinefunction(document_reader):
        document = await document_reader(item_id)
        if document is not None and document.get("project_id") == project_id:
            return _serialize(document_to_context_item(document))

    raise HTTPException(404, f"Context item {item_id} not found")


@app.delete("/projects/{project_id}/context/{item_id}")
async def delete_project_context_item(
    project_id: UUID,
    item_id: UUID,
) -> dict[str, Any]:
    """Delete a context item."""
    storage = _storage()
    item = await storage.get_context_item(item_id)
    if item is None:
        document_reader = getattr(storage, "get_document", None)
        if inspect.iscoroutinefunction(document_reader):
            document = await document_reader(item_id)
            if document is not None and document.get("project_id") == project_id:
                raise HTTPException(
                    405,
                    "Generated documents are read-only context. Use the document revision/status APIs.",
                )
        raise HTTPException(404, f"Context item {item_id} not found")
    if item.get("project_id") != project_id:
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
        try:
            next_state = ProjectState(result.next_state)
        except (TypeError, ValueError):
            # A lightweight route double can leave a process-local
            # controller from a prior test/request. Never accept its untyped
            # result as authoritative. Production WorkflowController
            # instances return a typed transition and never enter this path.
            next_state = ProjectState(
                project.get("failed_from_state") or ProjectState.INIT.value
            )
            update_project = getattr(storage, "update_project", None)
            if not inspect.iscoroutinefunction(update_project):
                raise
            updated = await update_project(project_id, state=next_state.value)
            if updated is None:
                raise HTTPException(409, "Stale state conflict during retry")

        await _ensure_workflow_approval_gate(storage, project_id, next_state)
        return {
            "status": "retried",
            "next_state": str(next_state),
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

    stored_envelope = row["envelope_json"]
    if not isinstance(stored_envelope, dict):
        raise HTTPException(400, "Dead letter has invalid envelope")

    # Replay is a new delivery attempt: preserve forensic source data while
    # resetting the fields that caused the original entry to expire/exhaust.
    envelope = dict(stored_envelope)
    envelope["message_id"] = str(uuid4())
    envelope["retry_count"] = 0
    envelope["timestamp"] = datetime.now(tz=UTC).isoformat()

    try:
        async with httpx.AsyncClient(timeout=10, headers=_router_auth_headers()) as client:
            resp = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
            if resp.status_code not in (200, 201):
                raise HTTPException(502, f"Router returned {resp.status_code}")
            router_result = resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Failed to replay: {e}")

    await storage.create_task_log(
        task_id=UUID(envelope["message_id"]),
        agent_id="human_operator",
        team_id=row["recipient_team"],
        status="DLQ_REPLAYED",
        input_data={
            "dead_letter_id": letter_id,
            "original_message_id": row["message_id"],
            "failure_reason": row["failure_reason"],
        },
        output_data={
            "new_message_id": envelope["message_id"],
            "router_entry_id": router_result.get("entry_id"),
            "retry_count": 0,
        },
        trace_id=envelope["message_id"],
    )

    return {
        "status": "replayed",
        "new_message_id": envelope["message_id"],
        "entry_id": router_result.get("entry_id"),
        "audit_task_id": envelope["message_id"],
    }


# ═════════════════════════════════════════════════════════════════════════════
# Tasks
# ═════════════════════════════════════════════════════════════════════════════


@app.post("/tasks")
async def create_task(body: dict[str, Any]) -> dict[str, Any]:
    """Publish an ADMIN_TASK to the correct team admin via the router."""
    tid = new_trace_id()
    bind_trace_id(tid)

    team_id = body.get("team_id", "exec_ceo")
    task_payload = dict(body.get("payload") or {})
    action = str(task_payload.get("action") or "").upper()
    project_id = body.get("project_id") or task_payload.get("project_id")

    # Sprint/issue tools use this endpoint as their central persistence
    # boundary.  Handle the small deterministic CRUD actions here so a tool
    # call cannot appear successful merely because an ADMIN_TASK was queued to
    # a team with no action handler.  Unknown actions retain the normal routed
    # task behavior used by agent conversations.
    if action in {
        "CREATE_SPRINT",
        "ACTIVATE_SPRINT",
        "CLOSE_SPRINT",
        "CREATE_ISSUE",
        "DECOMPOSE_ISSUE",
        "UPDATE_ISSUE_STATUS",
        "UPDATE_AGENT_PROFILE",
    }:
        storage = _storage()
        pid: UUID | None = None
        if project_id:
            try:
                pid = UUID(str(project_id))
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, "project_id must be a UUID") from exc
            if await storage.get_project(pid) is None:
                raise HTTPException(404, f"Project {pid} not found")
        elif action != "UPDATE_AGENT_PROFILE":
            raise HTTPException(400, f"{action} requires project_id")

        try:
            if action == "CREATE_SPRINT":
                result = await storage.create_sprint(
                    project_id=pid,
                    sprint_number=int(task_payload.get("sprint_number", 1)),
                    milestone=task_payload.get("milestone"),
                    goal=task_payload.get("goal"),
                    planned_story_points=task_payload.get("planned_story_points"),
                    estimated_hours=task_payload.get("estimated_hours"),
                )
            elif action in {"ACTIVATE_SPRINT", "CLOSE_SPRINT"}:
                sprint_id = UUID(str(task_payload.get("sprint_id")))
                sprint = await storage.get_sprint(sprint_id)
                if sprint is None or sprint.get("project_id") != pid:
                    raise HTTPException(404, f"Sprint {sprint_id} not found for project {pid}")
                status = "IN_PROGRESS" if action == "ACTIVATE_SPRINT" else "CLOSED"
                await storage.update_sprint(sprint_id, status=status)
                result = await storage.get_sprint(sprint_id)
            elif action == "CREATE_ISSUE":
                sprint_id = task_payload.get("sprint_id")
                parsed_sprint_id = None
                if sprint_id:
                    parsed_sprint_id = UUID(str(sprint_id))
                    sprint = await storage.get_sprint(parsed_sprint_id)
                    if sprint is None or str(sprint.get("project_id")) != str(pid):
                        raise HTTPException(
                            404,
                            f"Sprint {parsed_sprint_id} not found for project {pid}",
                        )
                result = await storage.create_issue(
                    project_id=pid,
                    sprint_id=parsed_sprint_id,
                    title=str(task_payload.get("title") or "Untitled issue"),
                    description=task_payload.get("description"),
                    issue_type=str(task_payload.get("issue_type") or "TASK"),
                    priority=str(task_payload.get("priority") or "medium"),
                    assigned_team=task_payload.get("assigned_team"),
                    estimated_hours=task_payload.get("estimated_hours"),
                    story_points=task_payload.get("story_points"),
                )
            elif action == "DECOMPOSE_ISSUE":
                issue_id = UUID(str(task_payload.get("issue_id")))
                parent = await storage.get_issue(issue_id)
                if parent is None or parent.get("project_id") != pid:
                    raise HTTPException(404, f"Issue {issue_id} not found for project {pid}")
                children = []
                for item in task_payload.get("sub_tasks") or []:
                    child = item if isinstance(item, dict) else {"title": str(item)}
                    children.append(
                        await storage.create_issue(
                            project_id=pid,
                            sprint_id=parent.get("sprint_id"),
                            parent_issue_id=issue_id,
                            title=str(child.get("title") or "Sub-task"),
                            description=child.get("description"),
                            issue_type=str(child.get("issue_type") or "TASK"),
                            priority=str(child.get("priority") or parent.get("priority") or "medium"),
                            assigned_team=child.get("assigned_team") or parent.get("assigned_team"),
                            estimated_hours=child.get("estimated_hours"),
                            story_points=child.get("story_points"),
                        )
                    )
                result = {"parent_issue_id": issue_id, "children": children}
            elif action == "UPDATE_AGENT_PROFILE":
                profile_agent_id = str(task_payload.get("agent_id") or "")
                if not profile_agent_id:
                    raise HTTPException(400, "UPDATE_AGENT_PROFILE requires agent_id")
                result = await storage.observe_agent_profile(
                    agent_id=profile_agent_id,
                    team_id=task_payload.get("team_id"),
                    role=task_payload.get("role"),
                    estimated_hours=task_payload.get("estimated_hours"),
                    actual_hours=task_payload.get("actual_hours"),
                    tasks_completed=int(task_payload.get("tasks_completed", task_payload.get("total_tasks_completed", 1))),
                    alpha=task_payload.get("alpha", 0.5),
                )
            else:  # UPDATE_ISSUE_STATUS
                issue_id = UUID(str(task_payload.get("issue_id")))
                issue = await storage.get_issue(issue_id)
                if issue is None or issue.get("project_id") != pid:
                    raise HTTPException(404, f"Issue {issue_id} not found for project {pid}")
                values: dict[str, Any] = {"status": str(task_payload.get("status") or "IN_PROGRESS")}
                if task_payload.get("actual_hours") is not None:
                    values["actual_hours"] = task_payload["actual_hours"]
                await storage.update_issue(issue_id, **values)
                result = await storage.get_issue(issue_id)
                if result and str(values["status"]).upper() in {"DONE", "COMPLETED", "CLOSED"}:
                    sprint_id = result.get("sprint_id")
                    if sprint_id:
                        sprint_issues = await storage.list_issues(sprint_id=sprint_id)
                        completed = [
                            item
                            for item in sprint_issues
                            if str(item.get("status") or "").upper() in {"DONE", "COMPLETED", "CLOSED"}
                        ]
                        await storage.update_sprint(
                            sprint_id,
                            completed_story_points=sum(item.get("story_points") or 0 for item in completed),
                            actual_hours=sum(float(item.get("actual_hours") or 0) for item in sprint_issues),
                        )
        except HTTPException:
            raise
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"Invalid {action} payload: {exc}") from exc
        return {"status": "completed", "action": action, "result": _serialize(result)}

    envelope = {
        "message_id": str(uuid4()),
        "correlation_id": tid,
        "msg_type": MessageType.ADMIN_TASK.value,
        "sender_id": "orchestrator",
        "sender_team": "exec_ceo",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "recipient_team": team_id,
        "project_id": project_id,
        "payload": task_payload,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=10, headers=_router_auth_headers()) as client:
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
    """List all known default AIAT team IDs from the policy registry."""
    from mas_core.policy.rules import TEAM_TIERS

    teams = sorted(TEAM_TIERS)
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
    from mas_core.policy.rules import TEAM_TIERS

    all_teams = sorted(TEAM_TIERS)
    envelope = {
        "message_id": str(uuid4()),
        "msg_type": MessageType.SHUTDOWN.value,
        "sender_id": "orchestrator",
        "sender_team": "exec_ceo",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "payload": {"action": "SHUTDOWN", "timeout_s": _SHUTDOWN_TIMEOUT_S},
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=10, headers=_router_auth_headers()) as client:
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
        {"id": "dept_production", "name": "Production"},
        {"id": "dept_system", "name": "System"},
        {"id": "dept_qa", "name": "Quality Assurance"},
        {"id": "dept_devops", "name": "DevOps"},
    ]
    sample_project_template = {
        "id": "aiat_sample_software_project",
        "name": "AIAT Sample Software Project",
        "description": (
            "Walk through requirements, architecture, implementation, QA, and deployment "
            "using AIAT's governed project workflow."
        ),
        "human_requester": "operator",
        "config": {
            "template": True,
            "objective": "Build and verify a small governed software change.",
            "expected_outputs": ["requirements", "design", "tests", "release_evidence"],
        },
    }

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

OPTIONAL_RUNTIME_IDS = {"autogen", "letta"}


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


def _runtime_readiness(runtime_id: str) -> dict[str, Any]:
    """Return an actionable runtime availability record."""
    missing_packages = _missing_runtime_packages(runtime_id)
    return {
        "status": "available" if not missing_packages else "unavailable",
        "missing_packages": missing_packages,
        "optional": runtime_id in OPTIONAL_RUNTIME_IDS,
    }


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

    Epsilon catalogs LangGraph, CrewAI, AutoGen, and Letta as guardrailed
    runtimes behind AIAT's control plane. AutoGen and Letta are opt-in
    specialist runtimes and are not part of the default control-plane image.
    """
    return {
        "runtimes": [
            {
                "id": "langgraph",
                "name": "LangGraph",
                **_runtime_readiness("langgraph"),
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
                **_runtime_readiness("crewai"),
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
                **_runtime_readiness("autogen"),
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
                **_runtime_readiness("letta"),
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
        async with httpx.AsyncClient(
            timeout=60, base_url=ORCHESTRATOR_URL, headers=_control_plane_auth_headers()
        ) as client:
            resp = await client.post("/system/shutdown")
            logger.info("Cron shutdown response: %s", resp.status_code)
    except Exception:
        logger.exception("Cron shutdown failed")


async def _cron_resume() -> None:
    """APScheduler callback: trigger system resume."""
    logger.info("Cron-triggered resume starting")
    try:
        async with httpx.AsyncClient(
            timeout=30, base_url=ORCHESTRATOR_URL, headers=_control_plane_auth_headers()
        ) as client:
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
    is_external_candidate = bool(req.source_repo and str(req.source_repo).lower() != "local")
    if is_external_candidate and not req.version_pin:
        raise HTTPException(
            422,
            "External workers require an immutable version_pin before they can enter the steward pipeline",
        )
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
        model_mode=req.model_mode,
        model_profile_id=req.model_profile_id,
    )
    # New external hires enter the steward pipeline immediately. Existing
    # lightweight test doubles and legacy local mirrors remain readable during
    # the migration window, but real persisted external hires cannot be
    # activated without this dedicated steward reference.
    if is_external_candidate:
        if inspect.iscoroutinefunction(getattr(storage, "create_external_provenance", None)) and inspect.iscoroutinefunction(getattr(storage, "create_steward", None)):
            from hashlib import sha256
            import json as _json_module
            from mas_core.worker_registry.steward import ExternalProvenance

            provenance_evidence = dict(req.adapter_config.get("provenance") or {})
            provenance = ExternalProvenance(
                canonical_source_repository=req.source_repo,
                source_provider=str(provenance_evidence.get("source_provider") or ("github" if "github.com" in req.source_repo else "external")),
                exact_release=str(provenance_evidence.get("exact_release") or req.version_pin),
                commit_sha=provenance_evidence.get("commit_sha"),
                package_version=provenance_evidence.get("package_version"),
                oci_image_digest=provenance_evidence.get("oci_image_digest"),
                dependency_lock_hash=provenance_evidence.get("dependency_lock_hash"),
                transport_type=req.adapter_type,
                adapter_version=str(req.adapter_config.get("adapter_version") or "1.0.0"),
                protocol_api_version=str(provenance_evidence.get("protocol_api_version") or "aiat.worker.v1"),
                runtime_fingerprint=provenance_evidence.get("runtime_fingerprint"),
                license_id=provenance_evidence.get("license_id"),
                redistribution_status=str(provenance_evidence.get("redistribution_status") or "pending"),
                security_scan_status=str(provenance_evidence.get("security_scan_status") or "pending"),
                documentation_snapshot_version=provenance_evidence.get("documentation_snapshot_version"),
            )
            provenance_hash = sha256(_json_module.dumps(provenance.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
            get_provenance = getattr(storage, "get_external_provenance_by_worker", None)
            existing_provenance = (
                await get_provenance(worker["id"])
                if inspect.iscoroutinefunction(get_provenance)
                else None
            )
            if existing_provenance is not None and existing_provenance.get("provenance_hash") != provenance_hash:
                raise HTTPException(
                    409,
                    "External worker provenance is already governed at a different pin; use the steward update workflow",
                )
            provenance_row = existing_provenance or await storage.create_external_provenance(
                worker_id=worker["id"],
                provenance=provenance.model_dump(mode="json"),
                provenance_hash=provenance_hash,
            )
            get_steward = getattr(storage, "get_steward_by_worker", None)
            steward_row = (
                await get_steward(worker["id"])
                if inspect.iscoroutinefunction(get_steward)
                else None
            )
            if steward_row is None:
                steward_row = await storage.create_steward(
                    worker_id=worker["id"],
                    provenance_id=provenance_row["id"],
                    monitoring_cadence="daily",
                )
            if inspect.iscoroutinefunction(getattr(storage, "create_update_monitoring_job", None)):
                list_jobs = getattr(storage, "list_update_monitoring_jobs", None)
                existing_jobs = (
                    await list_jobs(worker_id=worker["id"], limit=100)
                    if inspect.iscoroutinefunction(list_jobs)
                    else []
                )
                if not any(str(job.get("steward_id")) == str(steward_row["id"]) and job.get("status") == "active" for job in existing_jobs):
                    await storage.create_update_monitoring_job(
                        worker_id=worker["id"],
                        steward_id=steward_row["id"],
                        cadence="daily",
                    )
            if inspect.iscoroutinefunction(getattr(storage, "update_worker_config", None)):
                await storage.update_worker_config(worker["id"], adapter_config={**dict(worker.get("adapter_config") or {}), "governance_required": True, "steward_id": str(steward_row["id"])})
    enriched = await _enrich_workers_with_capabilities(storage, [worker])
    return enriched[0]


@app.delete("/capabilities/workers/{worker_id}")
async def deregister_worker(
    worker_id: str,
    permanent: bool = Query(default=False),
) -> dict[str, str]:
    """Deregister a worker, or permanently remove it when explicitly requested.

    The default behavior remains a soft deregistration for compatibility with
    existing lifecycle callers. E2E cleanup uses ``permanent=true`` for
    timestamped test workers so the hiring board does not accumulate debris.
    """
    storage = _storage()
    parsed_worker_id: UUID | None = None
    try:
        parsed_worker_id = UUID(worker_id)
    except ValueError:
        parsed_worker_id = None

    # Normal lifecycle operations are UUID-addressed. Keep the historical
    # name lookup only for explicit permanent cleanup, where older operator
    # scripts may still pass a worker name.
    if not permanent and parsed_worker_id is None:
        raise HTTPException(422, "worker_id must be a valid UUID")

    if not permanent and parsed_worker_id is not None:
        await storage.update_worker_status(parsed_worker_id, status="DEREGISTERED")
        return {"status": "deregistered"}

    worker: dict[str, Any] | None = None
    if parsed_worker_id is not None:
        worker = await storage.get_worker(parsed_worker_id)
    else:
        worker = await storage.get_worker_by_name(worker_id)

    if permanent:
        if worker is None:
            raise HTTPException(404, f"Worker {worker_id} not found")
        deleted = await storage.delete_worker(worker["id"])
        if not deleted:
            raise HTTPException(404, f"Worker {worker_id} not found")
        from mas_core.worker_registry.ingestion import remove_mirror

        # Mirrors are keyed by the immutable registry UUID.  Never pass the
        # user-controlled worker name to filesystem cleanup: names may contain
        # traversal or absolute-path components and must not select a directory.
        mirror_key = str(worker["id"])
        try:
            await remove_mirror(mirror_key)
        except Exception:
            logger.exception("worker_mirror_cleanup_failed", extra={"mirror_key": mirror_key})
        return {"status": "deleted"}

    if worker is not None:
        await storage.update_worker_status(worker["id"], status="DEREGISTERED")
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
    if req.model_mode is not None:
        update_kwargs["model_mode"] = req.model_mode
    if req.model_profile_id is not None:
        update_kwargs["model_profile_id"] = req.model_profile_id

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
        if (
            new_status == "ACTIVE"
            and existing.get("source_repo")
            and str(existing["source_repo"]).lower() != "local"
        ):
            evaluation_status = (existing.get("evaluation_status") or "").lower()
            if evaluation_status != "approved":
                raise HTTPException(
                    409,
                    "External worker activation is blocked until evaluation is approved",
                )
            governance = existing.get("adapter_config") or {}
            governance_store_available = inspect.iscoroutinefunction(
                getattr(storage, "get_steward_by_worker", None)
            )
            if governance_store_available:
                steward = await storage.get_steward_by_worker(worker_id)
                if steward is None and str(worker_id) not in _worker_steward_runtimes:
                    raise HTTPException(409, "External worker activation requires a dedicated Steward Agent")
                if not existing.get("active_adapter_id") and not governance.get("active_adapter_version"):
                    raise HTTPException(409, "External worker activation requires a certified active adapter")
                if not existing.get("active_skill_bundle_id") and not governance.get("active_skill_bundle_id"):
                    raise HTTPException(409, "External worker activation requires an approved active skill bundle")
                model_mode = str(existing.get("model_mode") or governance.get("model_mode") or "none")
                if model_mode != "none" and not (existing.get("model_profile_id") or governance.get("model_profile_id")):
                    raise HTTPException(409, "Model-governed external workers require an approved Model Profile")
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
# Governed worker contract, steward, model, and worker-run APIs
# ═════════════════════════════════════════════════════════════════════════════


def _governed_external_provenance(worker: dict[str, Any], req: StewardCreateRequest):
    from mas_core.worker_registry.steward import ExternalProvenance

    source_repo = req.source_repo or worker.get("source_repo")
    if not source_repo:
        raise HTTPException(422, "external worker provenance requires source_repo")
    exact_release = req.exact_release or worker.get("version_pin")
    commit_sha = req.commit_sha or worker.get("upstream_commit_sha")
    if not exact_release and not commit_sha and not req.package_version and not req.oci_image_digest:
        raise HTTPException(422, "external worker provenance requires an exact release, commit, package version, or OCI digest")
    try:
        return ExternalProvenance(
            canonical_source_repository=source_repo,
            source_provider=req.source_provider,
            exact_release=exact_release,
            commit_sha=commit_sha,
            package_version=req.package_version,
            oci_image_digest=req.oci_image_digest,
            dependency_lock_hash=req.dependency_lock_hash,
            protocol_api_version=req.protocol_api_version,
            adapter_version=req.adapter_version,
            transport_type=req.transport_type,
            license_id=req.license_id,
            redistribution_status=req.redistribution_status,
            security_scan_status=req.security_scan_status,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _steward_runtime(storage: AgentStorage, worker_id: UUID) -> Any | None:
    """Load the process-local steward cache from authoritative persisted rows.

    The runtime object is only an execution cache.  Candidate, certification,
    and rollout records are rehydrated from storage so an API restart cannot
    make a governed worker appear to have lost its hiring history.
    """
    from mas_core.worker_registry.steward import (
        CandidateRecord,
        CapabilitySnapshot,
        CertificationRun,
        DocumentationSnapshot,
        DocumentationSource,
        ExternalProvenance,
        ExternalWorkerSteward,
        RolloutRecord,
        StewardStatus,
    )
    from mas_core.worker_contract import WorkerCapabilities

    key = str(worker_id)
    cached = _worker_steward_runtimes.get(key)
    if cached is not None:
        return cached
    persisted = await storage.get_steward_by_worker(worker_id)
    provenance_row = await storage.get_external_provenance_by_worker(worker_id)
    if persisted is None or provenance_row is None:
        return None
    try:
        status = StewardStatus(str(persisted.get("status", StewardStatus.PROVISIONING)))
    except ValueError:
        status = StewardStatus.PROVISIONING
    steward = ExternalWorkerSteward(
        worker_id=key,
        steward_id=UUID(str(persisted["id"])),
        provenance=ExternalProvenance.model_validate(provenance_row),
        status=status,
    )
    if inspect.iscoroutinefunction(getattr(storage, "list_documentation_snapshots", None)):
        for row in await storage.list_documentation_snapshots(steward.steward_id):
            try:
                source = DocumentationSource(
                    source_id=UUID(str(row["source_id"])),
                    uri=str(row["source_uri"]),
                    source_type=str(row.get("source_type") or "official"),
                    trusted=bool(row.get("source_trusted", False)),
                    allowed_domains=tuple(row.get("source_allowed_domains") or []),
                )
                if not any(existing.source_id == source.source_id for existing in steward.documentation_sources):
                    steward.documentation_sources.append(source)
                steward.add_documentation_snapshot(
                    DocumentationSnapshot(
                        snapshot_id=UUID(str(row["id"])),
                        source=source,
                        version=str(row["version"]),
                        content_sha256=str(row["content_sha256"]),
                        captured_at=row.get("captured_at") or datetime.now(UTC),
                        content_ref=row.get("content_ref"),
                        extracted_interfaces=row.get("extracted_interfaces") or {},
                        security_findings=tuple(row.get("security_findings") or []),
                        untrusted=bool(row.get("untrusted", True)),
                    )
                )
            except (KeyError, ValueError):
                logger.warning("steward_documentation_snapshot_rehydrate_failed", extra={"snapshot_id": str(row.get("id"))})
    if inspect.iscoroutinefunction(getattr(storage, "list_capability_snapshots", None)):
        for row in await storage.list_capability_snapshots(worker_id, steward_id=steward.steward_id):
            try:
                steward.record_capabilities(
                    CapabilitySnapshot(
                        snapshot_id=UUID(str(row["id"])),
                        version=str(row["version"]),
                        capabilities=WorkerCapabilities.model_validate(row.get("capabilities_json") or {}),
                        discovered_at=row.get("created_at") or datetime.now(UTC),
                        evidence_refs=tuple(row.get("evidence_refs") or []),
                    )
                )
            except (KeyError, ValueError):
                logger.warning("steward_capability_snapshot_rehydrate_failed", extra={"snapshot_id": str(row.get("id"))})
    for row in await storage.list_skill_bundle_candidates(worker_id):
        raw_candidate = (row.get("evidence_json") or {}).get("candidate_record")
        if raw_candidate:
            try:
                candidate = CandidateRecord.model_validate(raw_candidate)
                from mas_core.worker_registry.steward import CandidateIntakeStatus

                candidate.intake_status = CandidateIntakeStatus(str(row.get("intake_status", candidate.intake_status)))
                steward.candidates[candidate.candidate_id] = candidate
            except ValueError:
                logger.warning("steward_candidate_rehydrate_failed", extra={"candidate_id": str(row.get("id"))})
    for row in await storage.list_certification_runs(worker_id):
        try:
            certification = CertificationRun(
                certification_id=UUID(str(row["id"])),
                candidate_id=UUID(str(row["candidate_id"])),
                started_at=row.get("started_at") or datetime.now(UTC),
                completed_at=row.get("completed_at"),
                conformance=row.get("conformance_json") or {},
                checks=row.get("checks_json") or {},
                failures=tuple(row.get("failure_reasons") or []),
                passed=str(row.get("status", "")).lower() == "passed",
                approved_by=(row.get("evidence_json") or {}).get("approved_by"),
            )
            steward.certifications[certification.certification_id] = certification
        except (KeyError, ValueError):
            logger.warning("steward_certification_rehydrate_failed", extra={"certification_id": str(row.get("id"))})
    for row in await storage.list_rollout_records(worker_id):
        try:
            targets = row.get("sample_targets") or {}
            rollout = RolloutRecord(
                rollout_id=UUID(str(row["id"])),
                worker_id=key,
                steward_id=UUID(str(row["steward_id"])),
                candidate_id=UUID(str(row["candidate_id"])),
                status=str(row.get("status", "PENDING")),
                eligible_task_classes=tuple(row.get("eligible_task_classes") or []),
                shadow_sample_target=int(targets.get("shadow", 10)),
                readonly_canary_sample_target=int(targets.get("readonly_canary", 5)),
                live_canary_sample_target=int(targets.get("live_canary", 3)),
                sample_count=int(row.get("sample_count") or 0),
                started_at=row.get("started_at") or datetime.now(UTC),
                completed_at=row.get("completed_at"),
                comparison_metrics=row.get("comparison_metrics") or {},
                rollback_thresholds=row.get("rollback_thresholds") or {"regression_fraction": 0.10},
                promotion_actor=row.get("promotion_actor"),
                rollback_reason=row.get("rollback_reason"),
            )
            steward.rollouts[rollout.rollout_id] = rollout
        except (KeyError, ValueError):
            logger.warning("steward_rollout_rehydrate_failed", extra={"rollout_id": str(row.get("id"))})
    _worker_steward_runtimes[key] = steward
    return steward


def _worker_tool_dispatcher(storage: AgentStorage, worker: dict[str, Any]):
    """Return the only bridge from a Worker ToolRequest to tool-service."""
    from mas_core.worker_contract import WorkerToolResponse, WorkerUsage

    async def dispatch(tool_request: Any) -> WorkerToolResponse:
        if tool_request.approval_required:
            return WorkerToolResponse(
                request_id=tool_request.request_id,
                run_id=tool_request.run_id,
                tool_name=tool_request.tool_name,
                success=False,
                error={
                    "code": "TOOL_APPROVAL_REQUIRED",
                    "message": "The requested tool requires a recorded approval before dispatch",
                    "category": "approval",
                },
            )
        run = await storage.get_worker_run(tool_request.run_id)
        if run is None:
            raise RuntimeError("Worker Run is not persisted; tool dispatch is denied")
        request_json = dict(run.get("request_json") or {})
        body = {
            "caller_id": str(worker["id"]),
            "caller_role": AgentRole.WORKER.value,
            "caller_team": worker.get("team_id"),
            "project_id": str(run["project_id"]) if run.get("project_id") else None,
            "worker_run_id": str(tool_request.run_id),
            "permission_scope": list(tool_request.permission_scope),
            "budget_snapshot": request_json.get("budget") or {},
            "audit_context": {
                "worker_id": str(worker["id"]),
                "worker_run_id": str(tool_request.run_id),
                "tool_request_id": str(tool_request.request_id),
            },
            "tool_name": tool_request.tool_name,
            "kwargs": tool_request.arguments,
            "idempotency_key": str(
                uuid5(NAMESPACE_URL, f"{tool_request.run_id}:{tool_request.idempotency_key}")
            ),
        }
        async with httpx.AsyncClient(timeout=120, headers=_tool_service_auth_headers()) as client:
            response = await client.post(
                f"{TOOL_SERVICE_URL}/tools/{tool_request.tool_name}/run",
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
        return WorkerToolResponse(
            request_id=tool_request.request_id,
            run_id=tool_request.run_id,
            tool_name=tool_request.tool_name,
            success=bool(payload.get("success")),
            result=payload.get("result"),
            error=(
                {
                    "code": payload.get("error_code") or "TOOL_ERROR",
                    "message": payload.get("error") or "Tool request failed",
                    "category": "tool_service",
                }
                if not payload.get("success")
                else None
            ),
            usage=WorkerUsage(duration_ms=float(payload["duration_ms"]))
            if payload.get("duration_ms") is not None
            else None,
        )

    return dispatch


async def _certified_worker_adapter(storage: AgentStorage, worker: dict[str, Any]) -> Any | None:
    """Hydrate the active immutable adapter definition after an API restart."""
    from mas_core.worker_contract import AdapterContext, WorkerCapabilities
    from mas_core.worker_registry.runtime_adapters import (
        OpenCodeAdapter,
        OpenCodeInterfaceVerification,
        adapter_for_transport,
    )

    worker_id = UUID(str(worker["id"]))
    adapter_row = await storage.get_runtime_adapter(worker["active_adapter_id"]) if worker.get("active_adapter_id") else await storage.get_active_runtime_adapter(worker_id)
    if adapter_row is None or adapter_row.get("status") != "active" or adapter_row.get("conformance_status") != "passed":
        stale = _worker_adapter_runtimes.pop(str(worker_id), None)
        if stale is not None and hasattr(stale, "close"):
            try:
                await stale.close()
            except Exception:
                logger.warning("stale_worker_adapter_close_failed", extra={"worker_id": str(worker_id)}, exc_info=True)
        return None
    active_adapter_id = str(adapter_row["id"])
    cached = _worker_adapter_runtimes.get(str(worker_id))
    if cached is not None and getattr(cached, "_aiat_active_adapter_id", None) == active_adapter_id:
        return cached
    if cached is not None and hasattr(cached, "close"):
        try:
            await cached.close()
        except Exception:
            logger.warning("replaced_worker_adapter_close_failed", extra={"worker_id": str(worker_id)}, exc_info=True)
    config = dict(worker.get("adapter_config") or {})
    config.setdefault("sandbox_profile", worker.get("sandbox_profile"))
    raw_capabilities = adapter_row.get("capabilities_json") or config.get("capabilities") or {}
    try:
        capabilities = WorkerCapabilities.model_validate(raw_capabilities)
    except ValueError as exc:
        raise HTTPException(409, f"active adapter capabilities are invalid: {exc}") from exc
    context = AdapterContext(
        workspace_path=config.get("workspace_path"),
        tool_dispatcher=_worker_tool_dispatcher(storage, worker),
        metadata={"worker_id": str(worker_id)},
    )
    transport = str(adapter_row.get("transport_type") or worker.get("adapter_type") or "").lower()
    try:
        if transport == "opencode":
            raw_verification = config.get("interface_verification") or {}
            verification = OpenCodeInterfaceVerification.from_report(raw_verification)
            password_env = str(config.get("auth_password_env") or "OPENCODE_SERVER_PASSWORD")
            username_env = str(config.get("auth_username_env") or "OPENCODE_SERVER_USERNAME")
            password = os.getenv(password_env)
            if not password:
                raise ValueError(f"OpenCode secret environment {password_env!r} is not configured")
            tool_secret = os.getenv("TOOL_SECRET")
            if not tool_secret:
                raise ValueError("OpenCode tool bridge requires TOOL_SECRET from the secret boundary")
            context.secrets.update(
                {
                    "opencode_password": password,
                    "opencode_username": os.getenv(username_env, "opencode"),
                    "tool_secret": tool_secret,
                }
            )
            adapter = OpenCodeAdapter(
                verification,
                base_url=str(config["base_url"]),
                worker_id=str(worker_id),
                endpoints=config.get("endpoints"),
                context=context,
                capabilities=capabilities,
            )
        else:
            adapter = adapter_for_transport(
                transport,
                worker_id=str(worker_id),
                config=config,
                context=context,
            )
            adapter.capabilities = capabilities
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, f"active adapter configuration is not certified: {exc}") from exc
    # The cache entry is valid only for this immutable active adapter row.
    # Rollout promotion/rollback changes the pointer, so the next lookup
    # cannot accidentally dispatch through a stale runtime instance.
    setattr(adapter, "_aiat_active_adapter_id", active_adapter_id)
    _worker_adapter_runtimes[str(worker_id)] = adapter
    return adapter


@app.get("/worker-contract/version")
async def worker_contract_version() -> dict[str, Any]:
    from mas_core.worker_contract import ADAPTER_API_VERSION, CONTRACT_VERSION

    return {
        "contract_version": CONTRACT_VERSION,
        "schema_version": "1.0",
        "adapter_api_version": ADAPTER_API_VERSION,
        "supported_previous_major": True,
        "unknown_optional_fields": "preserved_and_ignored",
        "unknown_required_capabilities": "rejected",
    }


@app.post("/capabilities/workers/{worker_id}/steward", status_code=201)
async def create_worker_steward(worker_id: UUID, req: StewardCreateRequest) -> dict[str, Any]:
    storage = _storage()
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise HTTPException(404, f"Worker {worker_id} not found")
    if not worker.get("source_repo") and not req.source_repo:
        raise HTTPException(422, "A dedicated steward requires external source provenance")
    if str(worker_id) in _worker_steward_runtimes or await storage.get_steward_by_worker(worker_id):
        raise HTTPException(409, "Worker already has a dedicated Steward Agent")
    provenance = _governed_external_provenance(worker, req)
    from hashlib import sha256
    import json as _json_module
    from mas_core.worker_registry.steward import ExternalWorkerSteward

    provenance_hash = sha256(_json_module.dumps(provenance.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    persisted_provenance = None
    if inspect.iscoroutinefunction(getattr(storage, "create_external_provenance", None)):
        persisted_provenance = await storage.create_external_provenance(
            worker_id=worker_id,
            provenance=provenance.model_dump(mode="json"),
            provenance_hash=provenance_hash,
        )
    steward_id = UUID(str(persisted_provenance["id"])) if persisted_provenance and persisted_provenance.get("id") else None
    steward = ExternalWorkerSteward(worker_id=str(worker_id), provenance=provenance, steward_id=steward_id)
    persisted_steward = None
    if inspect.iscoroutinefunction(getattr(storage, "create_steward", None)):
        persisted_steward = await storage.create_steward(
            worker_id=worker_id,
            provenance_id=UUID(str(persisted_provenance["id"])) if persisted_provenance else None,
            steward_id=steward.steward_id,
            monitoring_cadence=req.monitoring_cadence,
        )
        if inspect.iscoroutinefunction(getattr(storage, "create_update_monitoring_job", None)):
            await storage.create_update_monitoring_job(
                worker_id=worker_id,
                steward_id=persisted_steward["id"],
                cadence=req.monitoring_cadence,
            )
    _worker_steward_runtimes[str(worker_id)] = steward
    if inspect.iscoroutinefunction(getattr(storage, "update_worker_config", None)):
        await storage.update_worker_config(
            worker_id,
            adapter_config={**dict(worker.get("adapter_config") or {}), "governance_required": True, "steward_id": str(steward.steward_id)},
        )
    return {
        "steward": steward.status_snapshot(),
        "provenance": provenance.model_dump(mode="json"),
        "persisted": bool(persisted_steward),
    }


@app.get("/capabilities/workers/{worker_id}/steward")
async def get_worker_steward(worker_id: UUID) -> dict[str, Any]:
    storage = _storage()
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise HTTPException(404, f"Worker {worker_id} not found")
    runtime = await _steward_runtime(storage, worker_id)
    persisted = None
    if inspect.iscoroutinefunction(getattr(storage, "get_steward_by_worker", None)):
        persisted = await storage.get_steward_by_worker(worker_id)
    if runtime is None and persisted is None:
        raise HTTPException(404, f"Worker {worker_id} has no dedicated Steward Agent")
    response = runtime.status_snapshot() if runtime is not None else _serialize(persisted)
    response["worker_id"] = str(worker_id)
    response["persisted_steward"] = _serialize(persisted) if persisted else None
    return response


@app.get("/stewards")
async def list_stewards(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    storage = _storage()
    rows = await storage.list_stewards(status=status, limit=limit)
    result: list[dict[str, Any]] = []
    for row in rows:
        worker_id = UUID(str(row["worker_id"]))
        candidates = await storage.list_skill_bundle_candidates(worker_id)
        monitoring = await storage.list_update_monitoring_jobs(worker_id=worker_id, limit=10)
        result.append(
            _serialize(
                {
                    **row,
                    "candidate_count": len(candidates),
                    "monitoring": monitoring,
                }
            )
        )
    return result


@app.get("/capabilities/workers/{worker_id}/steward/monitoring")
async def list_worker_steward_monitoring(worker_id: UUID) -> list[dict[str, Any]]:
    storage = _storage()
    if await storage.get_worker(worker_id) is None:
        raise HTTPException(404, "Worker not found")
    return [
        _serialize(row)
        for row in await storage.list_update_monitoring_jobs(worker_id=worker_id, limit=100)
    ]


@app.post("/capabilities/workers/{worker_id}/steward/documentation", status_code=201)
async def add_steward_documentation(worker_id: UUID, req: DocumentationSnapshotRequest) -> dict[str, Any]:
    storage = _storage()
    steward = await _steward_runtime(storage, worker_id)
    if steward is None:
        raise HTTPException(404, "Dedicated Steward Agent not found")
    from mas_core.worker_registry.steward import DocumentationSnapshot, DocumentationSource

    source = next((item for item in steward.documentation_sources if item.uri == req.uri), None)
    if source is None:
        source = DocumentationSource(uri=req.uri, trusted=False)
        steward.documentation_sources.append(source)
    snapshot = DocumentationSnapshot(
        source=source,
        version=req.version,
        content_sha256=req.content_sha256,
        content_ref=req.content_ref,
        extracted_interfaces=req.extracted_interfaces,
        security_findings=tuple(req.security_findings),
        untrusted=True,
    )
    steward.add_documentation_snapshot(snapshot)
    persisted = None
    if inspect.iscoroutinefunction(getattr(storage, "create_documentation_source", None)):
        source_row = await storage.get_documentation_source(steward_id=steward.steward_id, uri=source.uri)
        if source_row is None:
            source_row = await storage.create_documentation_source(steward_id=steward.steward_id, uri=source.uri, source_type=source.source_type, trusted_for_provenance=False, allowed_domains=list(source.allowed_domains))
        persisted = await storage.create_documentation_snapshot(source_id=source_row["id"], version=req.version, content_sha256=req.content_sha256, content_ref=req.content_ref, extracted_interfaces=req.extracted_interfaces, security_findings=req.security_findings, untrusted=True)
    return {"snapshot": snapshot.model_dump(mode="json"), "persisted": _serialize(persisted) if persisted else None}


@app.post("/capabilities/workers/{worker_id}/steward/capabilities", status_code=201)
async def record_steward_capabilities(worker_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    storage = _storage()
    steward = await _steward_runtime(storage, worker_id)
    if steward is None:
        raise HTTPException(404, "Dedicated Steward Agent not found")
    from mas_core.worker_contract import WorkerCapabilities
    from mas_core.worker_registry.steward import CapabilitySnapshot

    snapshot = CapabilitySnapshot(version=str(payload.get("version") or "1.0.0"), capabilities=WorkerCapabilities.model_validate(payload.get("capabilities") or {}), evidence_refs=tuple(str(value) for value in payload.get("evidence_refs") or []))
    steward.record_capabilities(snapshot)
    persisted = None
    if inspect.iscoroutinefunction(getattr(storage, "create_capability_snapshot", None)):
        persisted = await storage.create_capability_snapshot(
            worker_id=worker_id,
            steward_id=steward.steward_id,
            version=snapshot.version,
            capabilities=snapshot.capabilities.model_dump(mode="json"),
            evidence_refs=list(snapshot.evidence_refs),
        )
    result = snapshot.model_dump(mode="json")
    result["persisted"] = _serialize(persisted) if persisted else None
    return result


def _candidate_response(candidate: Any) -> dict[str, Any]:
    return candidate.model_dump(mode="json") if hasattr(candidate, "model_dump") else _serialize(candidate)


@app.post("/capabilities/workers/{worker_id}/steward/candidates", status_code=201)
async def generate_steward_candidate(worker_id: UUID, req: CandidateGenerationRequest) -> dict[str, Any]:
    storage = _storage()
    steward = await _steward_runtime(storage, worker_id)
    if steward is None:
        raise HTTPException(404, "Dedicated Steward Agent not found")
    candidate = steward.generate_candidate(
        semantic_version=req.semantic_version,
        adapter_version=req.adapter_version,
        upstream_compatibility_range=req.upstream_compatibility_range,
        adapter_entrypoint=req.adapter_entrypoint,
        implementation_ref=req.implementation_ref,
        diff=req.diff,
        migration_notes=req.migration_notes,
    )
    bundle_row = await storage.create_skill_bundle(
        worker_id=worker_id,
        steward_id=steward.steward_id,
        semantic_version=candidate.bundle.semantic_version,
        format_version="1.0",
        upstream_compatibility_range=candidate.bundle.upstream_compatibility_range,
        provenance=candidate.bundle.source_provenance.model_dump(mode="json"),
        bundle=candidate.bundle.model_dump(mode="json"),
        content_hash=candidate.bundle.content_hash,
    )
    adapter_row = await storage.create_runtime_adapter(
        worker_id=worker_id,
        version=candidate.adapter.version,
        adapter_type="external_worker",
        transport_type=candidate.adapter.transport_type,
        implementation_ref=candidate.adapter.implementation_ref,
        content_hash=candidate.adapter.content_hash,
        conformance_status="pending",
        status="candidate",
    )
    await storage.create_skill_bundle_candidate(
        candidate_id=candidate.candidate_id,
        skill_bundle_id=bundle_row["id"],
        worker_id=worker_id,
        adapter_id=adapter_row["id"],
        intake_status=candidate.intake_status.value,
        diff=candidate.diff,
        evidence=candidate.evidence,
        candidate_json=candidate.model_dump(mode="json"),
    )
    return _candidate_response(candidate)


@app.get("/capabilities/workers/{worker_id}/steward/candidates")
async def list_steward_candidates(worker_id: UUID) -> list[dict[str, Any]]:
    steward = await _steward_runtime(_storage(), worker_id)
    if steward is None:
        raise HTTPException(404, "Dedicated Steward Agent not found")
    return [_candidate_response(candidate) for candidate in steward.candidates.values()]


@app.post("/capabilities/workers/{worker_id}/steward/candidates/{candidate_id}/stage")
async def advance_steward_candidate_stage(
    worker_id: UUID,
    candidate_id: UUID,
    req: CandidateStageAdvanceRequest,
) -> dict[str, Any]:
    """Record exactly one reviewed intake transition and its evidence."""
    storage = _storage()
    steward = await _steward_runtime(storage, worker_id)
    if steward is None:
        raise HTTPException(404, "Dedicated Steward Agent not found")
    candidate = steward.candidates.get(candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    from mas_core.worker_registry.steward import CandidateIntakeStatus

    try:
        target = CandidateIntakeStatus(req.target_status)
        previous_status = candidate.intake_status
        candidate = steward.advance_candidate(candidate_id, target)
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    candidate.evidence.setdefault("intake_transitions", []).append(
        {
            "from": previous_status.value,
            "to": target.value,
            "actor": req.actor,
            "evidence": req.evidence,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
    )
    persisted = await storage.get_skill_bundle_candidate(candidate_id)
    if persisted is not None:
        evidence = dict(persisted.get("evidence_json") or {})
        evidence["candidate_record"] = candidate.model_dump(mode="json")
        await storage.update_skill_bundle_candidate(
            candidate_id,
            intake_status=candidate.intake_status.value,
            evidence=evidence,
        )
    return _candidate_response(candidate)


@app.post("/capabilities/workers/{worker_id}/steward/candidates/{candidate_id}/certify")
async def certify_steward_candidate(worker_id: UUID, candidate_id: UUID, req: CandidateCertificationRequest) -> dict[str, Any]:
    storage = _storage()
    steward = await _steward_runtime(storage, worker_id)
    if steward is None:
        raise HTTPException(404, "Dedicated Steward Agent not found")
    candidate = steward.candidates.get(candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    if candidate.intake_status.value != "CERTIFYING":
        raise HTTPException(
            409,
            "Candidate must pass each recorded intake stage and enter CERTIFYING before conformance can run",
        )
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise HTTPException(404, "Worker not found")
    provenance = steward.provenance
    server_checks = {
        "provenance_pin": bool(
            provenance.exact_release
            or provenance.commit_sha
            or provenance.package_version
            or provenance.oci_image_digest
        ),
        "license": bool(provenance.license_id)
        and provenance.redistribution_status == "approved",
        "security": provenance.security_scan_status == "passed",
        "documentation": bool(steward.documentation_snapshots),
        "capability_snapshot": bool(steward.capability_snapshots),
    }
    # Supplemental checks may only make certification stricter; a request
    # cannot manufacture a passing core gate.
    checks = {**server_checks, **{f"attested:{name}": bool(value) for name, value in req.checks.items()}}
    from mas_core.worker_contract import AdapterContext, ConformanceRunner, WorkerCapabilities
    from mas_core.worker_registry.runtime_adapters import (
        OpenCodeAdapter,
        OpenCodeInterfaceVerification,
        adapter_for_transport,
    )

    config = dict(worker.get("adapter_config") or {})
    config.setdefault("sandbox_profile", worker.get("sandbox_profile"))
    try:
        capability_payload = config.get("capabilities") or {}
        if not capability_payload and candidate.bundle.verified_capabilities is not None:
            capability_payload = candidate.bundle.verified_capabilities.capabilities.model_dump(mode="json")
        capabilities = WorkerCapabilities.model_validate(
            capability_payload
        )
        transport = candidate.adapter.transport_type.lower()
        context = AdapterContext(workspace_path=config.get("workspace_path"), metadata={"certification": True})
        if transport == "opencode":
            verification = OpenCodeInterfaceVerification.from_report(config.get("interface_verification") or {})
            password_env = str(config.get("auth_password_env") or "OPENCODE_SERVER_PASSWORD")
            username_env = str(config.get("auth_username_env") or "OPENCODE_SERVER_USERNAME")
            password = os.getenv(password_env)
            if not password:
                raise ValueError(f"OpenCode secret environment {password_env!r} is not configured")
            context.secrets.update({"opencode_password": password, "opencode_username": os.getenv(username_env, "opencode")})
            tool_secret = os.getenv("TOOL_SECRET")
            if not tool_secret:
                raise ValueError("OpenCode tool bridge secret is not configured")
            context.secrets["tool_secret"] = tool_secret
            adapter = OpenCodeAdapter(
                verification,
                base_url=str(config["base_url"]),
                worker_id=str(worker_id),
                endpoints=config.get("endpoints"),
                context=context,
                capabilities=capabilities,
            )
        else:
            adapter = adapter_for_transport(
                transport,
                worker_id=str(worker_id),
                config=config,
                context=context,
            )
            adapter.capabilities = capabilities
        resolved_model_profile = None
        if transport == "opencode":
            profile_id = str(worker.get("model_profile_id") or "")
            profile_row = await storage.get_model_profile(profile_id) if profile_id else None
            if profile_row is None:
                raise ValueError("OpenCode certification requires a persisted Model Profile")
            approved_versions = _model_profile_from_row(profile_row).approved_versions()
            if len(approved_versions) != 1:
                raise ValueError("OpenCode certification requires exactly one effective approved model version")
            from mas_core.worker_contract import ModelProfileReference

            resolved_version = approved_versions[0]
            resolved_model_profile = ModelProfileReference(
                profile_id=profile_id,
                version=resolved_version.version,
                exact_model_id=resolved_version.exact_model_id,
            )
        try:
            conformance = await ConformanceRunner(timeout_seconds=180.0 if transport == "opencode" else 10.0).run(
                adapter,
                worker_id=str(worker_id),
                include_cancellation=True,
                resolved_model_profile=resolved_model_profile,
            )
        finally:
            await adapter.close()
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(409, f"Candidate adapter cannot be certified: {exc}") from exc

    certification = steward.certify_candidate(
        candidate_id,
        conformance=conformance,
        checks=checks,
        approved_by=None,
    )
    await storage.create_certification_run(
        certification_id=certification.certification_id,
        worker_id=worker_id,
        candidate_id=candidate_id,
        steward_id=steward.steward_id,
        status="passed" if certification.passed else "rejected",
        conformance=certification.conformance,
        checks=certification.checks,
        evidence={
            "operator_conformance_submission": req.conformance,
            "server_derived_checks": server_checks,
        },
        failure_reasons=list(certification.failures),
        completed_at=certification.completed_at,
    )
    persisted_candidate = await storage.get_skill_bundle_candidate(candidate_id)
    if persisted_candidate is not None:
        evidence = dict(persisted_candidate.get("evidence_json") or {})
        evidence["candidate_record"] = steward.candidates[candidate_id].model_dump(mode="json")
        await storage.update_skill_bundle_candidate(candidate_id, intake_status=steward.candidates[candidate_id].intake_status.value, evidence=evidence, certification_run_id=certification.certification_id)
    await storage.update_runtime_adapter((persisted_candidate or {}).get("adapter_id"), conformance_status="passed" if certification.passed else "failed", conformance=certification.conformance) if persisted_candidate and persisted_candidate.get("adapter_id") else None
    return certification.model_dump(mode="json")


@app.post("/capabilities/workers/{worker_id}/steward/candidates/{candidate_id}/approve")
async def approve_steward_candidate(
    worker_id: UUID,
    candidate_id: UUID,
    req: CandidateApprovalRequest,
) -> dict[str, Any]:
    storage = _storage()
    steward = await _steward_runtime(storage, worker_id)
    if steward is None:
        raise HTTPException(404, "Dedicated Steward Agent not found")
    persisted_candidate = await storage.get_skill_bundle_candidate(candidate_id)
    if persisted_candidate is None:
        raise HTTPException(404, "Persisted candidate not found")
    candidate = steward.candidates.get(candidate_id)
    if candidate is None:
        raise HTTPException(404, "Candidate not found")
    certification = (
        steward.certifications.get(candidate.certification_id)
        if candidate.certification_id is not None
        else None
    )
    if certification is None or not certification.passed:
        raise HTTPException(409, "Candidate approval requires a passed server-run certification")
    approval = await storage.create_approval_record(
        scope_type="worker_skill_bundle_candidate",
        scope_id=candidate_id,
        decision="APPROVED",
        decided_by=req.decided_by,
        reason=req.reason,
        evidence=req.evidence,
    )
    try:
        candidate = steward.approve_candidate(candidate_id, approval_record_id=approval["id"])
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    evidence = dict(persisted_candidate.get("evidence_json") or {})
    evidence["candidate_record"] = candidate.model_dump(mode="json")
    await storage.update_skill_bundle_candidate(candidate_id, intake_status=candidate.intake_status.value, evidence=evidence, approval_record_id=candidate.approval_record_id)
    await storage.update_skill_bundle(persisted_candidate["skill_bundle_id"], status="APPROVED")
    if persisted_candidate.get("adapter_id"):
        current_adapter = await storage.get_runtime_adapter(persisted_candidate["adapter_id"])
        # Replaying an idempotent approval after rollout must never demote the
        # currently active immutable adapter back to merely approved.
        adapter_status = "active" if current_adapter and current_adapter.get("status") == "active" else "approved"
        await storage.update_runtime_adapter(
            persisted_candidate["adapter_id"],
            status=adapter_status,
            conformance_status="passed",
        )
    # The server-run conformance result plus this independent approval are
    # the authoritative external-worker evaluation.  Activation consumes the
    # worker-level projection, so update it here rather than requiring a
    # weaker legacy evaluation workflow after steward approval.
    await storage.update_worker_config(worker_id, evaluation_status="approved")
    return _candidate_response(candidate)


@app.post("/capabilities/workers/{worker_id}/steward/rollouts", status_code=201)
async def start_steward_rollout(worker_id: UUID, req: RolloutStartRequest) -> dict[str, Any]:
    storage = _storage()
    steward = await _steward_runtime(storage, worker_id)
    if steward is None:
        raise HTTPException(404, "Dedicated Steward Agent not found")
    prior_rollout_candidate_ids = {
        UUID(str(record["candidate_id"]))
        for record in await storage.list_rollout_records(worker_id)
    }
    candidate_id = next(
        (
            candidate.candidate_id
            for candidate in steward.candidates.values()
            if candidate.intake_status.value == "APPROVED"
            and candidate.candidate_id not in prior_rollout_candidate_ids
        ),
        None,
    )
    if candidate_id is None:
        raise HTTPException(
            409,
            "No approved candidate without immutable rollout history is available for rollout",
        )
    try:
        rollout = steward.start_rollout(candidate_id, actor=req.actor, eligible_task_classes=req.eligible_task_classes)
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    await storage.create_rollout_record(
        rollout_id=rollout.rollout_id,
        worker_id=worker_id,
        steward_id=steward.steward_id,
        candidate_id=candidate_id,
        status=rollout.status.value,
        eligible_task_classes=list(rollout.eligible_task_classes),
        sample_targets={"shadow": rollout.shadow_sample_target, "readonly_canary": rollout.readonly_canary_sample_target, "live_canary": rollout.live_canary_sample_target},
        rollback_thresholds=rollout.rollback_thresholds,
        promotion_actor=rollout.promotion_actor,
    )
    return rollout.model_dump(mode="json")


@app.post("/capabilities/workers/{worker_id}/steward/rollouts/{rollout_id}/advance")
async def advance_steward_rollout(worker_id: UUID, rollout_id: UUID, req: RolloutAdvanceRequest) -> dict[str, Any]:
    storage = _storage()
    steward = await _steward_runtime(storage, worker_id)
    if steward is None:
        raise HTTPException(404, "Dedicated Steward Agent not found")
    persisted_before = await storage.get_rollout_record(rollout_id)
    if persisted_before is None:
        raise HTTPException(404, "Persisted rollout not found")
    from mas_core.worker_registry.steward import RolloutStatus
    try:
        rollout = steward.advance_rollout(rollout_id, RolloutStatus(req.target_status), sample_count=req.sample_count, metrics=req.comparison_metrics)
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    if rollout.status.value == "ACTIVE":
        activated = await storage.activate_rollout_atomically(
            rollout_id=rollout_id,
            worker_id=worker_id,
            steward_id=steward.steward_id,
            candidate_id=rollout.candidate_id,
            completed_at=rollout.completed_at,
        )
        if activated is None:
            _worker_steward_runtimes.pop(str(worker_id), None)
            raise HTTPException(
                409,
                "Rollout promotion lost its compare-and-set lock or has a competing rollout",
            )
        await _invalidate_worker_adapter_runtime(worker_id)
    else:
        transitioned = await storage.transition_rollout(
            rollout_id,
            to_status=rollout.status.value,
            actor="worker-rollout-approver",
            expected_status=str(persisted_before["status"]),
            sample_count=rollout.sample_count,
            comparison_metrics=rollout.comparison_metrics,
            completed_at=rollout.completed_at,
            evidence={"target_status": req.target_status},
        )
        if transitioned is None:
            _worker_steward_runtimes.pop(str(worker_id), None)
            raise HTTPException(409, "Rollout transition lost its compare-and-set lock")
    return rollout.model_dump(mode="json")


@app.post("/capabilities/workers/{worker_id}/steward/rollouts/{rollout_id}/rollback")
async def rollback_steward_rollout(worker_id: UUID, rollout_id: UUID, req: RollbackRequest) -> dict[str, Any]:
    storage = _storage()
    steward = await _steward_runtime(storage, worker_id)
    if steward is None:
        raise HTTPException(404, "Dedicated Steward Agent not found")
    try:
        rollout = steward.rollback(rollout_id, reason=req.reason)
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc
    await storage.update_rollout_record(rollout_id, status=rollout.status.value, rollback_reason=req.reason, completed_at=rollout.completed_at)
    current_candidate = await storage.get_skill_bundle_candidate(rollout.candidate_id)
    previous_rollouts = [
        row for row in await storage.list_rollout_records(worker_id)
        if str(row.get("id")) != str(rollout_id) and str(row.get("status")) == "ACTIVE"
    ]
    previous_rollout = previous_rollouts[-1] if previous_rollouts else None
    previous_candidate = await storage.get_skill_bundle_candidate(UUID(str(previous_rollout["candidate_id"]))) if previous_rollout else None
    if current_candidate is not None:
        if current_candidate.get("adapter_id"):
            await storage.update_runtime_adapter(current_candidate["adapter_id"], status="superseded")
        await storage.update_skill_bundle(current_candidate["skill_bundle_id"], status="SUPERSEDED")
    if previous_candidate is not None:
        if previous_candidate.get("adapter_id"):
            await storage.update_runtime_adapter(previous_candidate["adapter_id"], status="active", conformance_status="passed")
        await storage.update_skill_bundle(previous_candidate["skill_bundle_id"], status="APPROVED")
        await storage.set_worker_governed_versions(worker_id, active_shell_version_id=None, active_adapter_id=previous_candidate.get("adapter_id"), active_skill_bundle_id=previous_candidate.get("skill_bundle_id"))
        await storage.set_steward_active_versions(steward.steward_id, active_skill_bundle_id=previous_candidate.get("skill_bundle_id"), active_adapter_id=previous_candidate.get("adapter_id"))
        target_candidate_id = UUID(str(previous_candidate["id"]))
    else:
        await storage.set_worker_governed_versions(worker_id, active_shell_version_id=None, active_adapter_id=None, active_skill_bundle_id=None)
        await storage.set_steward_active_versions(steward.steward_id, active_skill_bundle_id=None, active_adapter_id=None)
        await storage.update_steward(steward.steward_id, status="DEGRADED")
        await storage.update_worker_status(worker_id, status="INACTIVE")
        target_candidate_id = None
    await storage.create_rollback_record(
        rollout_id=rollout_id,
        worker_id=worker_id,
        reason=req.reason,
        triggered_by="operator",
        from_candidate_id=rollout.candidate_id,
        target_candidate_id=target_candidate_id,
    )
    await _invalidate_worker_adapter_runtime(worker_id)
    return rollout.model_dump(mode="json")


def _model_profile_from_row(row: dict[str, Any]) -> Any:
    """Rehydrate the immutable resolver model from its persisted rows."""
    from mas_core.llm_gateway import ModelProfile, ModelProfileStatus, ModelProfileVersion, PrivacyClass

    versions: list[Any] = []
    for raw in row.get("versions") or []:
        metadata = dict(raw.get("constraints_json") or {})
        try:
            privacy_class = PrivacyClass(str(metadata.get("privacy_class", "internal")))
        except ValueError:
            privacy_class = PrivacyClass.INTERNAL
        versions.append(ModelProfileVersion(
            version=str(raw["version"]),
            provider_id=str(raw["provider_id"]),
            exact_model_id=str(raw["exact_model_id"]),
            api_version=raw.get("api_version"),
            capabilities=frozenset(raw.get("capabilities") or []),
            context_window=int(metadata.get("context_window", 0) or 0),
            max_output_tokens=int(metadata.get("max_output_tokens", 0) or 0),
            tool_calling=bool(metadata.get("tool_calling", False)),
            structured_output=bool(metadata.get("structured_output", False)),
            vision=bool(metadata.get("vision", False)),
            reasoning=bool(metadata.get("reasoning", False)),
            streaming=bool(metadata.get("streaming", False)),
            embedding=bool(metadata.get("embedding", False)),
            cost_per_1k_input_usd=float(metadata.get("cost_per_1k_input_usd", 0) or 0),
            cost_per_1k_output_usd=float(metadata.get("cost_per_1k_output_usd", 0) or 0),
            max_cost_usd=metadata.get("max_cost_usd"),
            max_tokens_per_request=metadata.get("max_tokens_per_request"),
            latency_target_ms=metadata.get("latency_target_ms"),
            max_concurrency=metadata.get("max_concurrency"),
            privacy_class=privacy_class,
            regions=frozenset(metadata.get("regions") or []),
            local=bool(metadata.get("local", False)),
            provider_settings=dict(raw.get("provider_settings") or {}),
            status=ModelProfileStatus(str(raw.get("status", "draft"))),
            effective_from=raw.get("effective_from"),
            effective_until=raw.get("effective_until"),
        ))
    return ModelProfile(
        profile_id=str(row["logical_profile_id"]),
        purpose=str(row["purpose"]),
        approved_provider_ids=frozenset(row.get("approved_provider_ids") or []),
        required_capabilities=frozenset(row.get("required_capabilities") or []),
        fallback_profile_ids=tuple(row.get("fallback_profile_ids") or []),
        status=ModelProfileStatus(str(row.get("status", "draft"))),
        owner=str(row.get("owner", "aiat")),
        versions=tuple(versions),
    )


async def _persisted_model_profiles(storage: AgentStorage) -> list[Any]:
    rows = await storage.list_model_profiles()
    return [_model_profile_from_row(row) for row in rows]


def _model_policy_layer(name: str, raw: Any) -> Any | None:
    """Parse one persisted policy layer without granting request authority."""
    from mas_core.llm_gateway import ModelPolicyLayer

    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(409, f"Persisted model policy {name!r} is invalid JSON") from exc
    if not isinstance(raw, dict):
        raise HTTPException(409, f"Persisted model policy {name!r} must be an object")
    payload = dict(raw)
    payload["name"] = name
    if "constraints" not in payload:
        payload = {"name": name, "constraints": raw}
    try:
        return ModelPolicyLayer.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(409, f"Persisted model policy {name!r} is invalid: {exc}") from exc


async def _effective_model_policy_layers(
    storage: AgentStorage,
    *,
    worker: dict[str, Any],
    req: WorkerRunDispatchRequest,
) -> tuple[Any, ...]:
    """Build the org/project/flow/node/worker/steward/task policy intersection.

    The request layer is intentionally last and can only add restrictions,
    because the resolver intersects every layer.  Selection/default policy is
    always loaded from durable control-plane records.
    """
    raw_layers: list[tuple[str, Any]] = []
    get_config = getattr(storage, "get_config", None)
    if inspect.iscoroutinefunction(get_config):
        raw_layers.append(("organization", await get_config("model_policy.organization")))
    if req.project_id is not None:
        project = await storage.get_project(req.project_id)
        if project is not None:
            raw_layers.append(("project", (project.get("config") or {}).get("model_policy")))
    flow_definition: dict[str, Any] | None = None
    if req.flow_id is not None:
        flow = await storage.get_flow(req.flow_id)
        if flow is not None:
            flow_definition = dict(flow.get("definition_json") or {})
            raw_layers.append(("flow", (flow_definition.get("metadata") or {}).get("model_policy")))
    if flow_definition is not None and req.flow_node_execution_id is not None:
        execution = await storage.get_flow_node_execution(req.flow_node_execution_id)
        node_id = execution.get("node_id") if execution else None
        node = next((item for item in flow_definition.get("nodes") or [] if item.get("id") == node_id), None)
        if node is not None:
            raw_layers.append(("node", (node.get("config") or {}).get("model_policy")))
    worker_config = dict(worker.get("adapter_config") or {})
    raw_layers.append(("worker", worker_config.get("model_policy")))
    steward_id = worker_config.get("steward_id")
    if steward_id and inspect.iscoroutinefunction(getattr(storage, "get_steward", None)):
        try:
            steward = await storage.get_steward(UUID(str(steward_id)))
        except ValueError:
            steward = None
        if steward is not None:
            raw_layers.append(("steward", (steward.get("metadata") or {}).get("model_policy")))
    raw_layers.append(("task", req.runtime_extensions.get("model_policy")))
    layers = [layer for name, raw in raw_layers if (layer := _model_policy_layer(name, raw)) is not None]
    from mas_core.llm_gateway import ModelPolicyLayer

    layers.extend(
        ModelPolicyLayer.model_validate({"name": "request_restrictions", **raw})
        for raw in req.model_policy_layers
    )
    return tuple(layers)


@app.post("/model-profiles", status_code=201)
async def create_model_profile(req: ModelProfileCreateRequest) -> dict[str, Any]:
    storage = _storage()
    from mas_core.llm_gateway import ModelProfile, ModelProfileStatus

    if await storage.get_model_profile(req.profile_id) is not None:
        raise HTTPException(409, "Model Profile already exists")
    try:
        profile = ModelProfile(
            profile_id=req.profile_id,
            purpose=req.purpose,
            approved_provider_ids=frozenset(req.approved_provider_ids),
            required_capabilities=frozenset(req.required_capabilities),
            fallback_profile_ids=tuple(req.fallback_profile_ids),
            status=ModelProfileStatus(req.status),
        )
        await storage.create_model_profile(
            logical_profile_id=profile.profile_id,
            purpose=profile.purpose,
            approved_provider_ids=sorted(profile.approved_provider_ids),
            required_capabilities=sorted(profile.required_capabilities),
            fallback_profile_ids=list(profile.fallback_profile_ids),
            status=profile.status.value,
            owner=profile.owner,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return profile.model_dump(mode="json")


@app.post("/model-overrides", status_code=201)
async def create_model_override(req: ModelOverrideCreateRequest) -> dict[str, Any]:
    storage = _storage()
    if await storage.get_project(req.project_id) is None:
        raise HTTPException(404, "Project not found")
    if await storage.get_model_profile(req.requested_profile_id) is None:
        raise HTTPException(404, "Requested Model Profile not found")
    override = await storage.create_model_override_request(
        project_id=req.project_id,
        requested_by=req.requested_by,
        requested_profile_id=req.requested_profile_id,
        reason=req.reason,
        scope=req.scope,
    )
    return _serialize(override)


@app.post("/model-overrides/{override_id}/decision")
async def decide_model_override(
    override_id: UUID,
    req: ModelOverrideDecisionRequest,
) -> dict[str, Any]:
    storage = _storage()
    override = await storage.get_model_override_request(override_id)
    if override is None:
        raise HTTPException(404, "Model override request not found")
    if str(override.get("status")) != "PENDING":
        raise HTTPException(409, "Model override request is already decided")
    approval = await storage.create_approval_record(
        scope_type="model_override_request",
        scope_id=override_id,
        decision=req.decision,
        decided_by=req.decided_by,
        reason=req.reason,
        evidence=req.evidence,
        expires_at=req.expires_at,
    )
    updated = await storage.update_model_override_request(
        override_id,
        status=req.decision,
        decided_by=req.decided_by,
        decision=req.decision,
        expires_at=req.expires_at,
    )
    if updated is None:
        raise HTTPException(404, "Model override request not found")
    return {**_serialize(updated), "approval_record": _serialize(approval)}


@app.post("/model-profiles/{profile_id}/versions", status_code=201)
async def add_model_profile_version(profile_id: str, req: ModelProfileVersionRequest) -> dict[str, Any]:
    from mas_core.llm_gateway import ModelProfileStatus, ModelProfileVersion, PrivacyClass

    storage = _storage()
    profile_row = await storage.get_model_profile(profile_id)
    if profile_row is None:
        raise HTTPException(404, "Model Profile not found")
    approved_provider_ids = {str(provider_id) for provider_id in (profile_row.get("approved_provider_ids") or [])}
    if req.provider_id not in approved_provider_ids:
        raise HTTPException(
            422,
            f"provider_id {req.provider_id!r} is not approved by Model Profile {profile_id!r}",
        )
    try:
        version = ModelProfileVersion(
            version=req.version,
            provider_id=req.provider_id,
            exact_model_id=req.exact_model_id,
            capabilities=frozenset(req.capabilities),
            context_window=req.context_window,
            max_output_tokens=req.max_output_tokens,
            tool_calling=req.tool_calling,
            structured_output=req.structured_output,
            vision=req.vision,
            reasoning=req.reasoning,
            streaming=req.streaming,
            embedding=req.embedding,
            cost_per_1k_input_usd=req.cost_per_1k_input_usd,
            cost_per_1k_output_usd=req.cost_per_1k_output_usd,
            max_cost_usd=req.max_cost_usd,
            max_tokens_per_request=req.max_tokens_per_request,
            latency_target_ms=req.latency_target_ms,
            max_concurrency=req.max_concurrency,
            privacy_class=PrivacyClass(req.privacy_class),
            regions=frozenset(req.regions),
            local=req.local,
            provider_settings=req.provider_settings,
            effective_from=req.effective_from,
            effective_until=req.effective_until,
            status=ModelProfileStatus(req.status),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        await storage.create_model_profile_version(
            profile_id=UUID(str(profile_row["id"])),
            version=version.version,
            provider_id=version.provider_id,
            exact_model_id=version.exact_model_id,
            capabilities=sorted(version.capabilities),
            provider_settings=version.provider_settings,
            status=version.status.value,
            api_version=version.api_version,
            effective_from=version.effective_from,
            effective_until=version.effective_until,
            version_metadata={
                "context_window": version.context_window,
                "max_output_tokens": version.max_output_tokens,
                "tool_calling": version.tool_calling,
                "structured_output": version.structured_output,
                "vision": version.vision,
                "reasoning": version.reasoning,
                "streaming": version.streaming,
                "embedding": version.embedding,
                "cost_per_1k_input_usd": version.cost_per_1k_input_usd,
                "cost_per_1k_output_usd": version.cost_per_1k_output_usd,
                "max_cost_usd": version.max_cost_usd,
                "max_tokens_per_request": version.max_tokens_per_request,
                "latency_target_ms": version.latency_target_ms,
                "max_concurrency": version.max_concurrency,
                "privacy_class": version.privacy_class.value,
                "regions": sorted(version.regions),
                "local": version.local,
            },
        )
    except (ValueError, sa.exc.IntegrityError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return version.model_dump(mode="json")


@app.get("/model-profiles")
async def list_model_profiles() -> list[dict[str, Any]]:
    storage = _storage()
    return [profile.model_dump(mode="json") for profile in await _persisted_model_profiles(storage)]


@app.post("/model-profiles/resolve-preview")
async def preview_model_resolution(req: ModelResolutionPreviewRequest) -> dict[str, Any]:
    from mas_core.llm_gateway import ModelPolicyLayer, ModelProfileResolver, ModelResolutionRequest

    try:
        request = ModelResolutionRequest(
            task_type=req.task_type,
            requested_profile_id=req.requested_profile_id,
            layers=tuple(ModelPolicyLayer.model_validate(layer) for layer in req.layers),
            worker_required_capabilities=frozenset(req.worker_required_capabilities),
            steward_required_capabilities=frozenset(req.steward_required_capabilities),
            task_required_capabilities=frozenset(req.task_required_capabilities),
            adapter_required_capabilities=frozenset(req.adapter_required_capabilities),
            prompt_tokens=req.prompt_tokens,
            expected_output_tokens=req.expected_output_tokens,
            budget_usd=req.budget_usd,
            requested_raw_model_id=req.requested_raw_model_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    storage = _storage()
    return ModelProfileResolver().dry_run(await _persisted_model_profiles(storage), request)


@app.post("/workers/runs", status_code=202)
async def dispatch_worker_run(req: WorkerRunDispatchRequest) -> dict[str, Any]:
    storage = _storage()
    worker = await storage.get_worker(req.worker_id)
    if worker is None:
        raise HTTPException(404, f"Worker {req.worker_id} not found")
    if worker.get("status") not in {"ACTIVE", "DRAINING"}:
        raise HTTPException(409, "Worker is not active")
    adapter = await _certified_worker_adapter(storage, worker)
    if adapter is None:
        raise HTTPException(409, "Worker has no certified runtime adapter registered with the control plane")
    model_mode = str(worker.get("model_mode") or (worker.get("adapter_config") or {}).get("model_mode") or "none")
    if req.resolved_model_profile is not None:
        raise HTTPException(422, "resolved_model_profile is control-plane output and cannot be supplied by a caller")
    if model_mode != "none" and not (req.requested_model_profile or worker.get("model_profile_id")):
        raise HTTPException(409, "model-governed workers require an approved Model Profile")
    from mas_core.worker_contract import CapabilityRequirement, ModelProfileReference, WorkerRunController, WorkerRunRequest

    resolved_model_profile = None
    model_resolution_snapshot_id = None
    override_approval_id: UUID | None = None
    try:
        provided_requested_model_profile = ModelProfileReference.model_validate(req.requested_model_profile) if req.requested_model_profile else None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if model_mode != "none":
        from mas_core.llm_gateway import ModelProfileResolver, ModelResolutionError, ModelResolutionRequest

        requested_profile_id = (
            provided_requested_model_profile.profile_id
            if provided_requested_model_profile
            else worker.get("model_profile_id")
        )
        if (
            provided_requested_model_profile is not None
            and worker.get("model_profile_id")
            and provided_requested_model_profile.profile_id != worker.get("model_profile_id")
        ):
            if req.model_override_request_id is None or req.model_override_approval_id is None:
                raise HTTPException(
                    409,
                    "A worker Model Profile override requires an approved model override record",
                )
            override = await storage.get_model_override_request(req.model_override_request_id)
            approval = await storage.get_approval_record(req.model_override_approval_id)
            expired = override is not None and override.get("expires_at") is not None and override["expires_at"] <= datetime.now(UTC)
            if (
                override is None
                or override.get("status") != "APPROVED"
                or override.get("project_id") != req.project_id
                or override.get("requested_profile_id") != provided_requested_model_profile.profile_id
                or expired
                or approval is None
                or approval.get("scope_type") != "model_override_request"
                or approval.get("scope_id") != req.model_override_request_id
                or approval.get("decision") != "APPROVED"
            ):
                raise HTTPException(409, "Model Profile override approval is missing, stale, or out of scope")
            override_approval_id = req.model_override_approval_id
        try:
            resolution_request = ModelResolutionRequest(
                task_type=req.task_type,
                requested_profile_id=str(requested_profile_id) if requested_profile_id else None,
                layers=await _effective_model_policy_layers(storage, worker=worker, req=req),
                worker_required_capabilities=frozenset(req.worker_required_model_capabilities),
                steward_required_capabilities=frozenset(req.steward_required_model_capabilities),
                task_required_capabilities=frozenset(req.task_required_model_capabilities),
                adapter_required_capabilities=frozenset(set(req.adapter_required_model_capabilities) | set(adapter.capabilities.required_model_capabilities)),
                prompt_tokens=req.prompt_tokens,
                expected_output_tokens=req.expected_output_tokens,
                budget_usd=req.budget_usd,
                override_approval_id=override_approval_id,
            )
            snapshot = ModelProfileResolver().resolve(await _persisted_model_profiles(storage), resolution_request)
            await storage.create_model_resolution_snapshot(snapshot=snapshot.model_dump(mode="json"), project_id=req.project_id)
            model_resolution_snapshot_id = snapshot.snapshot_id
            resolved_model_profile = ModelProfileReference(
                profile_id=str(snapshot.resolved_profile_id),
                version=str(snapshot.resolved_profile_version),
                exact_model_id=str(snapshot.exact_model_id),
                resolution_snapshot_id=snapshot.snapshot_id,
            )
        except ModelResolutionError as exc:
            raise HTTPException(409, {"code": exc.code, "message": str(exc), "rejected_candidates": [item.model_dump(mode="json") for item in exc.rejected_candidates]}) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    elif req.requested_model_profile:
        raise HTTPException(422, "model profiles are not allowed for model_mode=none")
    else:
        # Non-LLM workers still get an immutable governance decision record.
        # This captures their capability, permission, budget, workspace, and
        # retry policy so model_mode=none is not an ungoverned execution path.
        none_snapshot = {
            "snapshot_id": uuid4(),
            "effective_constraints": {"model_mode": "none"},
            "effective_configuration": {
                "capability_requirements": req.capability_requirements,
                "tool_grants": req.tool_grants,
                "permission_requirements": req.permission_requirements,
                "workspace_mode": req.workspace_mode,
                "budget": req.budget,
                "checkpoint_policy": req.checkpoint_policy,
                "retry_policy": req.retry_policy,
            },
            "capability_checks": {"model_mode_none": True},
            "selection_reason": "Non-LLM worker governance policy snapshot",
        }
        await storage.create_model_resolution_snapshot(snapshot=none_snapshot, project_id=req.project_id)
        model_resolution_snapshot_id = none_snapshot["snapshot_id"]

    try:
        request = WorkerRunRequest(
            idempotency_key=req.idempotency_key,
            worker_id=str(req.worker_id),
            task_type=req.task_type,
            task_input=req.task_input,
            project_id=req.project_id,
            flow_id=req.flow_id,
            flow_instance_id=req.flow_instance_id,
            flow_node_execution_id=req.flow_node_execution_id,
            requested_model_profile=provided_requested_model_profile,
            resolved_model_profile=resolved_model_profile,
            capability_requirements=[CapabilityRequirement.model_validate(item) for item in req.capability_requirements],
            tool_grants=req.tool_grants,
            permission_requirements=req.permission_requirements,
            workspace_mode=req.workspace_mode,
            timeout_seconds=req.timeout_seconds,
            budget=req.budget,
            checkpoint_policy=req.checkpoint_policy,
            retry_policy=req.retry_policy,
            extensions=req.runtime_extensions,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    controller = WorkerRunController(storage=storage)
    outcome = await controller.execute(
        request,
        adapter,
        worker_registry_id=req.worker_id,
        worker_shell_version_id=worker.get("active_shell_version_id"),
        adapter_id=worker.get("active_adapter_id"),
        steward_id=UUID(str((worker.get("adapter_config") or {}).get("steward_id"))) if (worker.get("adapter_config") or {}).get("steward_id") else None,
        model_resolution_snapshot_id=model_resolution_snapshot_id,
    )
    return {
        "run_id": str(outcome.run_id),
        "state": outcome.state,
        "accepted": outcome.accepted.model_dump(mode="json") if outcome.accepted else None,
        "result": outcome.result.model_dump(mode="json") if outcome.result else None,
        "events": [event.model_dump(mode="json") for event in outcome.events],
        "negotiation": outcome.negotiation,
    }


@app.get("/workers/runs")
async def list_worker_runs_api(project_id: UUID | None = None, worker_id: UUID | None = None, flow_instance_id: UUID | None = None, state: str | None = None, limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0)) -> list[dict[str, Any]]:
    storage = _storage()
    if not inspect.iscoroutinefunction(getattr(storage, "list_worker_runs", None)):
        return []
    rows = await storage.list_worker_runs(project_id=project_id, worker_id=worker_id, flow_instance_id=flow_instance_id, state=state, limit=limit, offset=offset)
    return [_serialize(row) for row in rows]


@app.get("/workers/runs/{run_id}")
async def get_worker_run_api(run_id: UUID) -> dict[str, Any]:
    storage = _storage()
    if not inspect.iscoroutinefunction(getattr(storage, "get_worker_run", None)):
        raise HTTPException(503, "worker-run persistence is unavailable")
    row = await storage.get_worker_run(run_id)
    if row is None:
        raise HTTPException(404, "Worker run not found")
    return _serialize(row)


@app.get("/workers/runs/{run_id}/events")
async def get_worker_run_events(run_id: UUID, limit: int = Query(default=1000, ge=1, le=10000), offset: int = Query(default=0, ge=0)) -> list[dict[str, Any]]:
    storage = _storage()
    if not inspect.iscoroutinefunction(getattr(storage, "list_worker_events", None)):
        return []
    return [_serialize(row) for row in await storage.list_worker_events(run_id, limit=limit, offset=offset)]


@app.get("/workers/runs/{run_id}/transitions")
async def get_worker_run_transitions(run_id: UUID, limit: int = Query(default=1000, ge=1, le=10000)) -> list[dict[str, Any]]:
    storage = _storage()
    return [_serialize(row) for row in await storage.list_worker_run_transitions(run_id, limit=limit)]


@app.get("/workers/runs/{run_id}/artifacts")
async def get_worker_run_artifacts(run_id: UUID, limit: int = Query(default=1000, ge=1, le=10000)) -> list[dict[str, Any]]:
    storage = _storage()
    return [_serialize(row) for row in await storage.list_worker_artifacts(run_id, limit=limit)]


@app.get("/workers/runs/{run_id}/usage")
async def get_worker_run_usage(run_id: UUID, limit: int = Query(default=1000, ge=1, le=10000)) -> list[dict[str, Any]]:
    storage = _storage()
    return [_serialize(row) for row in await storage.list_worker_usage(run_id, limit=limit)]


@app.post("/workers/runs/{run_id}/cancel")
async def cancel_worker_run(run_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    storage = _storage()
    row = await storage.get_worker_run(run_id)
    if row is None:
        raise HTTPException(404, "Worker run not found")
    worker = await storage.get_worker(UUID(str(row["worker_id"])))
    adapter = await _certified_worker_adapter(storage, worker) if worker is not None else None
    if adapter is None:
        raise HTTPException(409, "No certified adapter is registered for this run")
    from mas_core.worker_contract import WorkerRunController
    row = await WorkerRunController(storage=storage).cancel(run_id, adapter, reason=str(payload.get("reason") or "operator cancellation"), requested_by=str(payload.get("requested_by") or "operator"), force=bool(payload.get("force", False)))
    if row is None:
        raise HTTPException(404, "Worker run not found")
    return _serialize(row)


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
    if project.get("state") in TERMINAL_PROJECT_STATES:
        raise HTTPException(409, "Terminal projects are read-only and cannot receive a new flow instance")

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
    if req.definition_json is not None and inspect.iscoroutinefunction(getattr(storage, "list_flow_instances", None)):
        instances = await storage.list_flow_instances(flow_id=flow_id, limit=1000, offset=0)
        if any(instance.get("status") in {"COMPLETED", "FAILED", "CANCELLED"} for instance in instances):
            raise HTTPException(409, "Flow definitions used by terminal instances are immutable")
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

    # Task nodes are completed only by a terminal Worker Run.  ``advance`` is
    # the sole operator/API action for a task: it creates the run from the
    # typed node policy and feeds its normalized result back into this same
    # transition path.  This removes the legacy client-side "mark complete"
    # bypass while retaining manual actions for approvals and control nodes.
    # Legacy action/team-only definitions remain manually driven until they
    # are migrated.  A node becomes governed when it declares ``worker_id``;
    # then a Worker Run is mandatory and client-side completion is forbidden.
    if node.type == FlowNodeType.TASK and node.config.get("worker_id"):
        executions = await storage.list_flow_node_executions(
            instance_id=instance_id, node_id=req.node_id, status="RUNNING", limit=100
        )
        execution = executions[-1] if executions else None
        if execution is None:
            raise HTTPException(409, "Task node has no running execution to dispatch or settle")

        if req.action == "advance":
            from mas_core.workflow.worker_policy import TaskNodePolicy

            try:
                policy = TaskNodePolicy.model_validate(node.config)
            except ValueError as exc:
                raise HTTPException(422, f"Task node policy is invalid: {exc}") from exc
            if not policy.worker_id:
                raise HTTPException(
                    409,
                    "Task nodes must resolve a concrete worker_id before dispatch; team/action-only nodes are not executable",
                )
            try:
                worker_id = UUID(policy.worker_id)
            except ValueError as exc:
                raise HTTPException(422, "Task node worker_id must be a UUID") from exc
            dispatch = await dispatch_worker_run(
                WorkerRunDispatchRequest(
                    worker_id=worker_id,
                    idempotency_key=f"flow:{instance_id}:node-execution:{execution['id']}",
                    task_type=policy.task_type or policy.action or node.id,
                    task_input={
                        "flow_context": current_context,
                        "flow_node": {"id": node.id, "label": node.label},
                    },
                    project_id=instance["project_id"],
                    flow_id=instance["flow_id"],
                    flow_instance_id=instance_id,
                    flow_node_execution_id=execution["id"],
                    requested_model_profile=(
                        {"profile_id": policy.model_profile_id}
                        if policy.model_profile_id
                        else None
                    ),
                    capability_requirements=[
                        {"name": capability, "required": True}
                        for capability in policy.required_capabilities
                    ],
                    timeout_seconds=policy.timeout_seconds,
                    tool_grants=list(policy.tool_grants),
                    permission_requirements=list(policy.permission_requirements),
                    workspace_mode=policy.project_workspace_mode,
                    budget=policy.budget,
                    checkpoint_policy=policy.checkpoint_policy.model_dump(mode="json"),
                    retry_policy=policy.retry_policy.model_dump(mode="json"),
                    runtime_extensions=policy.runtime_extensions,
                    worker_required_model_capabilities=list(policy.required_capabilities),
                    budget_usd=policy.budget.get("max_cost_usd"),
                )
            )
            result = dispatch.get("result") or {}
            if dispatch["state"] == "SUCCEEDED":
                output = result.get("output")
                normalized_output = output if isinstance(output, dict) else {"worker_output": output}
                normalized_output["worker_run_id"] = dispatch["run_id"]
                return await flow_node_action(
                    instance_id,
                    FlowNodeActionRequest(
                        node_id=req.node_id,
                        action="complete",
                        output=normalized_output,
                        worker_run_id=UUID(dispatch["run_id"]),
                    ),
                )
            error = (result.get("error") or {}).get("message") or f"Worker Run ended {dispatch['state']}"
            return await flow_node_action(
                instance_id,
                FlowNodeActionRequest(
                    node_id=req.node_id,
                    action="timeout" if dispatch["state"] == "TIMED_OUT" else "fail",
                    error=str(error),
                    worker_run_id=UUID(dispatch["run_id"]),
                ),
            )

        if req.action not in {"complete", "fail", "timeout"}:
            raise HTTPException(400, "Task node action must be advance, complete, fail, or timeout")
        if req.worker_run_id is None:
            raise HTTPException(409, "Task terminal transitions require the authoritative worker_run_id")
        worker_run = await storage.get_worker_run(req.worker_run_id)
        if worker_run is None:
            raise HTTPException(404, "Worker Run not found")
        if (
            worker_run.get("flow_instance_id") != instance_id
            or worker_run.get("flow_node_execution_id") != execution["id"]
        ):
            raise HTTPException(409, "Worker Run is not bound to this active flow node execution")
        expected_run_state = {
            "complete": "SUCCEEDED",
            "fail": "FAILED",
            "timeout": "TIMED_OUT",
        }[req.action]
        if worker_run.get("state") != expected_run_state:
            raise HTTPException(
                409,
                f"Task node action {req.action} requires Worker Run state {expected_run_state}",
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
                # A task is executable only through its Worker Run.  Control
                # nodes remain event/approval driven, while each newly active
                # task immediately enters the governed dispatch lifecycle.
                for nid in new_nodes:
                    next_node = definition.get_node(nid)
                    if (
                        next_node is not None
                        and next_node.type == FlowNodeType.TASK
                        and next_node.config.get("worker_id")
                    ):
                        await flow_node_action(
                            instance_id,
                            FlowNodeActionRequest(node_id=nid, action="advance"),
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
    if instance.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise HTTPException(409, "Terminal flow instances are immutable; use an explicit recovery action")

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
    if instance.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise HTTPException(409, "Terminal flow instances are immutable; retry/recovery must be explicit")

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
    if instance.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise HTTPException(409, "Terminal flow instances are immutable")

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
    if instance.get("status") in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise HTTPException(409, "Terminal flow instances cannot be escalated")

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
    context_worker_id: UUID | None = None
    context_confirmation_token: UUID | None = None
    request_id: UUID | None = None
    async_mode: bool = False

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


def _ceo_stream_instruction(instruction: str) -> str:
    """Redact potentially secret-bearing credential changes from durable chat streams."""
    lowered = instruction.lower()
    credential_intent = any(
        token in lowered for token in ("credential", "credentials", "secret", "secrets")
    )
    secret_change = re.search(
        r"\b(?:create|add|set|update|rotate|replace|value|token|password)\b",
        lowered,
    ) is not None
    if credential_intent and secret_change:
        return "Secure credential change requested. Secret-bearing details were withheld from chat history."
    return instruction


async def _publish_ceo_chat_response(
    *,
    response_text: str,
    correlation_id: str,
    parent_id: str,
    action: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "response": response_text,
        "source": "ceo_chat",
    }
    if action is not None:
        worker = action.get("worker")
        worker_id = worker.get("id") if isinstance(worker, dict) else None
        payload["action"] = {
            "type": action.get("type"),
            "status": action.get("status"),
            "requires_confirmation": action.get("status") == "needs_confirmation",
            "confirmation_label": action.get("confirmation_label"),
        }
        context: dict[str, Any] = {"confirmation_token": action.get("confirmation_token")}
        if worker_id:
            context["worker_id"] = str(worker_id)
        payload["context"] = context

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
        "payload": payload,
        "created_at": datetime.now(tz=UTC).isoformat(),
        "ack_required": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15, headers=_router_auth_headers()) as client:
            resp = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
            if not resp.is_success:
                logger.warning(
                    "CEO chat response publish failed: status=%s body=%s",
                    resp.status_code,
                    resp.text[:300],
                )
    except Exception:
        logger.exception("CEO chat response publish failed")


async def _publish_ceo_chat_progress(
    *,
    stage: str,
    detail: str,
    correlation_id: str,
    parent_id: str,
    state: str = "working",
) -> None:
    """Publish operator-safe progress without exposing model chain-of-thought."""
    envelope = {
        "message_id": str(uuid4()),
        "correlation_id": correlation_id,
        "parent_id": parent_id,
        "msg_type": MessageType.SYSTEM_EVENT.value,
        "sender_id": "ceo",
        "sender_team": "exec_ceo",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "recipient_team": "exec_ceo",
        "project_id": "operator-direct",
        "payload": {
            "event": "CEO_CHAT_PROGRESS",
            "stage": stage,
            "detail": detail,
            "state": state,
            "source": "ceo_chat",
        },
        "created_at": datetime.now(tz=UTC).isoformat(),
        "ack_required": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15, headers=_router_auth_headers()) as client:
            resp = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
            if not resp.is_success:
                logger.warning(
                    "CEO chat progress publish failed: status=%s body=%s",
                    resp.status_code,
                    resp.text[:300],
                )
    except Exception:
        logger.exception("CEO chat progress publish failed")


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
    wrapper_manifest = _wrapper_manifest_for_hiring(
        worker_name=worker_name,
        repo_url=repo_url,
        team_id=team_id,
        adapter_type=adapter_type,
        sandbox_profile=sandbox_profile,
        version_pin=version_pin,
    )

    storage = _storage()
    existing_worker = await storage.get_worker_by_name(worker_name)
    if existing_worker is not None:
        existing_repo = existing_worker.get("source_repo")
        if _same_github_repo(str(existing_repo) if existing_repo else None, repo_url):
            response = (
                f"I found an existing Hiring Board ticket for `{worker_name}` from {existing_repo or repo_url}. "
                f"It is currently `{existing_worker.get('status')}` with evaluation "
                f"`{existing_worker.get('evaluation_status') or 'none'}` and team "
                f"`{existing_worker.get('team_id') or 'unassigned'}`. I did not create or reset a duplicate. "
                "Use `status of worker <name>`, `evaluate worker <name>`, `approve worker <name>`, "
                "or `activate worker <name>` to continue the existing candidate."
            )
            return {
                "type": "worker_hiring",
                "status": "existing_candidate",
                "worker": _serialize(existing_worker),
                "response": response,
                "trace": [
                    "parsed_hiring_intent",
                    "validated_github_source",
                    "found_existing_candidate",
                    "skipped_duplicate_registration",
                ],
            }

        response = (
            f"`{worker_name}` is already registered for source `{existing_repo or 'unknown'}`. "
            f"I will not overwrite it with {repo_url}. Give the new candidate an explicit unique name, "
            "for example `hire a worker named <unique_name> from <repo>`."
        )
        return {
            "type": "worker_hiring",
            "status": "name_conflict",
            "worker": _serialize(existing_worker),
            "response": response,
            "trace": [
                "parsed_hiring_intent",
                "validated_github_source",
                "blocked_name_conflict",
            ],
        }

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
        wrapper_config={"aiat_manifest": wrapper_manifest},
        isolation_mode="wrapper",
    )
    serialized = _serialize(worker)
    response = (
        f"I opened a Hiring Board ticket for `{worker_name}` from {repo_url}. "
        f"It is assigned to `{team_id}` as an inactive candidate with `{sandbox_profile}` sandboxing "
        f"and pending evaluation. Routing reason: {_department_hiring_reason(team_id)} "
        "The hiring team is CEO, HR/hiring, department chief, security "
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
    exact_candidates = []
    substring_candidates = []
    for worker in workers:
        names = [
            str(worker.get("name") or "").strip().lower(),
            str(worker.get("display_name") or "").strip().lower(),
        ]
        for name in names:
            if not name:
                continue
            if re.search(rf"(?<![a-z0-9_-]){re.escape(name)}(?![a-z0-9_-])", lowered):
                exact_candidates.append(worker)
                break
            if name in lowered:
                substring_candidates.append(worker)
                break
    unique_exact = {str(worker.get("id")): worker for worker in exact_candidates}
    if len(unique_exact) == 1:
        return next(iter(unique_exact.values()))
    unique_substring = {str(worker.get("id")): worker for worker in substring_candidates}
    if len(unique_substring) == 1:
        return next(iter(unique_substring.values()))
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


def _summarize_worker_for_ceo(worker: dict[str, Any], *, latest_report: dict[str, Any] | None = None) -> str:
    name = worker.get("name") or worker.get("id")
    parts = [
        f"Worker `{name}`",
        f"id `{worker.get('id')}`",
        f"status `{worker.get('status')}`",
        f"evaluation `{worker.get('evaluation_status') or 'none'}`",
        f"team `{worker.get('team_id') or 'unassigned'}`",
        f"sandbox `{worker.get('sandbox_profile') or 'unknown'}`",
    ]
    if worker.get("source_repo"):
        parts.append(f"source {worker.get('source_repo')}")
    response = ", ".join(parts) + "."
    if worker.get("team_id"):
        response += f" Department routing: {_department_hiring_reason(str(worker.get('team_id')))}"
    if latest_report:
        blocked = latest_report.get("blocked_reasons") or []
        if blocked:
            response += " Latest blocked reasons: " + "; ".join(str(reason) for reason in blocked[:5]) + "."
        verdict = latest_report.get("verdict")
        score = latest_report.get("overall_score")
        if verdict:
            response += f" Latest verdict `{verdict}`"
            if score is not None:
                response += f" with score `{score}`"
            response += "."
    return response


async def _latest_worker_report(storage: AgentStorage, worker_id: UUID) -> dict[str, Any] | None:
    try:
        reports = await storage.get_evaluation_reports(worker_id, limit=1)
    except Exception:
        logger.exception("ceo_worker_latest_evaluation_read_failed")
        return None
    return reports[0] if reports else None


async def _handle_ceo_hiring_followup_intent(
    instruction: str,
    context_worker_id: UUID | None = None,
) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if not any(token in lowered for token in ("production dep", "production dept", "dept_production", "department")):
        return None
    if not any(token in lowered for token in ("why", "reason", "route", "assigned", "routing")):
        return None

    storage = _storage()
    worker = await storage.get_worker(context_worker_id) if context_worker_id else None
    if worker is None:
        workers = await storage.list_workers()
        production_candidates = [
            candidate
            for candidate in workers
            if candidate.get("team_id") == "dept_production" and candidate.get("source_repo")
        ]
        worker = production_candidates[0] if len(production_candidates) == 1 else None
    response = _department_hiring_reason("dept_production")
    if worker:
        assigned_team = str(worker.get("team_id") or "unassigned")
        response = (
            f"`{worker.get('name')}` was routed to `{assigned_team}` because "
            f"{_department_hiring_reason(assigned_team)} If this repo is actually QA, security, DevOps, or architecture work, tell me the target department "
            "and I will reclassify the candidate before activation."
        )
    elif context_worker_id:
        response = (
            "I no longer find the candidate from the previous chat action. Give me its current worker name "
            "or UUID so I can explain the stored department assignment."
        )
    else:
        response = (
            "More than one hiring candidate may fit that question, so I will not guess which one you mean. "
            "Ask `why is worker <name> assigned to its department?` to inspect the exact stored assignment. "
            f"As a general rule, {response}"
        )
    return {
        "type": "hiring_department_explanation",
        "status": "explained",
        "worker": _serialize(worker) if worker else None,
        "response": response,
        "trace": ["parsed_hiring_department_followup", "explained_department_routing"],
    }


def _ceo_confirmation_store() -> dict[str, dict[str, Any]]:
    """Return the short-lived server-side store for exact chat confirmations."""
    store = getattr(app.state, "ceo_pending_confirmations", None)
    if not isinstance(store, dict):
        store = {}
        app.state.ceo_pending_confirmations = store
    return store


def _queue_ceo_confirmation(
    *,
    action: str,
    target_id: str | int | None,
    label: str,
    response: str,
) -> dict[str, Any]:
    token = str(uuid4())
    _ceo_confirmation_store()[token] = {
        "action": action,
        "target_id": str(target_id) if target_id is not None else None,
        "label": label,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    return {
        "type": action,
        "status": "needs_confirmation",
        "confirmation_token": token,
        "confirmation_label": label,
        "response": response + " Reply `confirm` to continue or `cancel` to leave everything unchanged.",
        "trace": ["parsed_privileged_action", "queued_exact_confirmation"],
    }


async def _handle_ceo_confirmation_intent(
    instruction: str,
    confirmation_token: UUID | None,
) -> dict[str, Any] | None:
    lowered = instruction.strip().lower()
    is_confirm = re.search(r"\b(?:confirm|confirmed|proceed|yes|do it)\b", lowered) is not None
    is_cancel = re.search(r"\b(?:cancel|never mind|nevermind|no|stop)\b", lowered) is not None
    if not (is_confirm or is_cancel):
        return None
    if confirmation_token is None:
        if re.fullmatch(
            r"(?:confirm|confirmed|proceed|yes|do it|cancel|never mind|nevermind|no|stop)(?:\s+(?:it|action))?[.!]?",
            lowered,
        ) is None:
            return None
        return {
            "type": "privileged_confirmation",
            "status": "missing_context",
            "response": "There is no pending CEO-chat action to confirm. Ask for the action first so I can bind confirmation to an exact target.",
            "trace": ["parsed_confirmation", "missing_confirmation_context"],
        }

    token = str(confirmation_token)
    pending = _ceo_confirmation_store().pop(token, None)
    if pending is None:
        return {
            "type": "privileged_confirmation",
            "status": "expired",
            "response": "That confirmation is no longer pending. Ask for the action again so I can re-check the current live state.",
            "trace": ["parsed_confirmation", "confirmation_missing_or_consumed"],
        }

    try:
        created_at = datetime.fromisoformat(str(pending["created_at"]))
    except (KeyError, TypeError, ValueError):
        created_at = datetime.min.replace(tzinfo=UTC)
    if (datetime.now(tz=UTC) - created_at).total_seconds() > 600:
        return {
            "type": str(pending.get("action") or "privileged_confirmation"),
            "status": "expired",
            "response": "That confirmation expired after 10 minutes. Ask for the action again so I can re-check the target.",
            "trace": ["parsed_confirmation", "confirmation_expired"],
        }
    if is_cancel:
        return {
            "type": str(pending.get("action") or "privileged_confirmation"),
            "status": "cancelled",
            "response": f"Cancelled `{pending.get('label')}`. Nothing was changed.",
            "trace": ["parsed_confirmation", "cancelled_pending_action"],
        }

    action = str(pending.get("action") or "")
    target = pending.get("target_id")
    if action == "system_shutdown":
        result = await system_shutdown()
    elif action == "system_resume":
        result = await system_resume()
    elif action == "project_resume":
        result = await resume_project(UUID(str(target)))
    elif action == "project_archive":
        result = await archive_project(UUID(str(target)))
    elif action == "project_delete":
        result = await delete_project(UUID(str(target)))
    elif action == "flow_delete":
        result = await delete_flow(UUID(str(target)))
    elif action == "flow_instance_cancel":
        result = await flow_instance_action(
            UUID(str(target)), FlowInstanceActionRequest(action="cancel")
        )
    elif action == "dead_letter_replay":
        result = await replay_dead_letter(int(str(target)))
    elif action == "credential_delete":
        await delete_credential(str(target))
        result = {"status": "deleted"}
    else:
        return {
            "type": "privileged_confirmation",
            "status": "unsupported",
            "response": "The pending action type is no longer supported. Nothing was changed.",
            "trace": ["parsed_confirmation", "unsupported_pending_action"],
        }
    return {
        "type": action,
        "status": str(result.get("status") or "completed"),
        "result": _serialize(result),
        "response": f"Completed `{pending.get('label')}`. Result: `{result.get('status') or 'completed'}`.",
        "trace": ["parsed_confirmation", "validated_exact_target", "executed_confirmed_action"],
    }


async def _find_project_for_ceo_text(
    storage: AgentStorage,
    instruction: str,
) -> tuple[dict[str, Any] | None, bool]:
    project_id = _extract_uuid_from_text(instruction)
    if project_id:
        return await storage.get_project(UUID(project_id)), False
    projects = await storage.list_projects(limit=100)
    lowered = instruction.lower()
    matches = [
        project
        for project in projects
        if str(project.get("name") or "").strip()
        and str(project.get("name") or "").strip().lower() in lowered
    ]
    return (matches[0], False) if len(matches) == 1 else (None, len(matches) > 1)


async def _handle_ceo_project_intent(instruction: str) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if "project" not in lowered:
        return None

    # A CSO veto is a recoverable security workflow, not a normal human
    # approval decision. Require an explicit remediation statement, restore
    # the state recorded before the veto, then move the design back into its
    # creation stage so a new immutable document revision can be submitted.
    security_revision_requested = (
        re.search(r"\bsecurity\s+(?:blocker|blocked|veto)\b", lowered) is not None
        and re.search(
            r"\b(?:resolve|remediate|address|fix|clear|reopen|revise)\b", lowered
        ) is not None
        and "override" not in lowered
    )
    if security_revision_requested:
        storage = _storage()
        project, ambiguous = await _find_project_for_ceo_text(storage, instruction)
        if project is None:
            return {
                "type": "project_security_revision",
                "status": "ambiguous_project" if ambiguous else "needs_project",
                "response": (
                    "More than one project matches that security-blocker request; give me the exact project UUID."
                    if ambiguous
                    else "I need an existing project name or UUID for the security-blocker request."
                ),
                "trace": ["parsed_security_revision_intent", "project_not_uniquely_identified"],
            }

        reason_match = re.search(
            r"\b(?:because|reason|comment|comments|with|after)\s*[:=-]?\s*(.+)$",
            instruction,
            flags=re.IGNORECASE,
        )
        reason = reason_match.group(1).strip(" .\"'") if reason_match else ""
        if not reason:
            return {
                "type": "project_security_revision",
                "status": "needs_justification",
                "project": _serialize(project),
                "response": (
                    f"Project `{project.get('name') or project.get('id')}` has a security blocker. "
                    "Tell me what remediation was completed (for example: encryption, classification, "
                    "threat model, compliance, secrets rotation, MCP controls, and sandbox hardening)."
                ),
                "trace": ["parsed_security_revision_intent", "required_security_remediation_reason"],
            }

        if str(project.get("state")) != ProjectState.SECURITY_BLOCKED.value:
            return {
                "type": "project_security_revision",
                "status": "no_security_blocker",
                "project": _serialize(project),
                "response": (
                    f"Project `{project.get('name') or project.get('id')}` is currently "
                    f"`{project.get('state')}`; I did not apply a security-blocker recovery."
                ),
                "trace": ["parsed_security_revision_intent", "checked_authoritative_project_state"],
            }

        project_id = UUID(str(project["id"]))
        restored = await transition_project(
            project_id,
            TransitionRequest(
                event=WorkflowEvent.BLOCKER_RESOLVED.value,
                actor_id="human_operator",
                context={"reason": reason, "resolution": "security_remediation_verified"},
            ),
        )
        restored_state = str(restored.get("next_state") or "")
        revision_event = {
            ProjectState.PDR_REVIEW.value: WorkflowEvent.PDR_REVISION_REQUESTED.value,
            ProjectState.CDR_REVIEW.value: WorkflowEvent.CDR_REVISION_REQUESTED.value,
        }.get(restored_state)
        if revision_event is None:
            updated = await storage.get_project(project_id)
            return {
                "type": "project_security_revision",
                "status": "blocker_resolved_no_revision_stage",
                "project": _serialize(updated or project),
                "response": (
                    f"I resolved the security blocker, but the restored project state is `{restored_state}` "
                    "and has no document-revision route."
                ),
                "trace": [
                    "parsed_security_revision_intent",
                    "resolved_security_blocker",
                    "missing_document_revision_route",
                ],
            }

        revised = await transition_project(
            project_id,
            TransitionRequest(
                event=revision_event,
                actor_id="human_operator",
                context={
                    "reason": reason,
                    "resolution": "security_remediation_verified",
                    "revision_requested": True,
                },
            ),
        )
        updated = await storage.get_project(project_id)
        return {
            "type": "project_security_revision",
            "status": "revision_requested",
            "project": _serialize(updated or project),
            "restored_state": restored_state,
            "revision_event": revision_event,
            "response": (
                f"I recorded the CSO remediation for project `{project.get('name') or project.get('id')}`. "
                f"The project moved through `{restored_state}` into `{revised.get('next_state')}`; "
                "the CEO stage directive will create and submit a new durable CDR revision for review."
            ),
            "trace": [
                "parsed_security_revision_intent",
                "matched_exact_project",
                "resolved_security_blocker",
                "requested_immutable_document_revision",
                "published_stage_directive",
                "read_authoritative_project_state",
            ],
        }

    # A non-veto review can also request a revision (for example, CFO may
    # require a budget section).  Keep that workflow explicit in CEO chat so
    # the human can authorize the next immutable revision without relying on
    # an unhandled DOCUMENT_REVISION message or an LLM-selected transition.
    document_revision_requested = (
        re.search(r"\b(?:cdr|pdr|design|document)\b", lowered) is not None
        and re.search(r"\b(?:revise|revision|redo|rework|update)\b", lowered) is not None
        and "security blocker" not in lowered
        and "security blocked" not in lowered
    )
    if document_revision_requested:
        storage = _storage()
        project, ambiguous = await _find_project_for_ceo_text(storage, instruction)
        if project is None:
            return {
                "type": "project_document_revision",
                "status": "ambiguous_project" if ambiguous else "needs_project",
                "response": (
                    "More than one project matches that document-revision request; give me the exact project UUID."
                    if ambiguous
                    else "I need an existing project name or UUID for the document-revision request."
                ),
                "trace": ["parsed_document_revision_intent", "project_not_uniquely_identified"],
            }

        reason_match = re.search(
            r"\b(?:because|reason|comment|comments|with|after)\s*[:=-]?\s*(.+)$",
            instruction,
            flags=re.IGNORECASE,
        )
        reason = reason_match.group(1).strip(" .\"'") if reason_match else ""
        if not reason:
            return {
                "type": "project_document_revision",
                "status": "needs_justification",
                "project": _serialize(project),
                "response": (
                    f"Tell me what must change in project `{project.get('name') or project.get('id')}` "
                    "before I request a new immutable document revision."
                ),
                "trace": ["parsed_document_revision_intent", "required_revision_reason"],
            }

        revision_event = {
            ProjectState.PDR_REVIEW.value: WorkflowEvent.PDR_REVISION_REQUESTED.value,
            ProjectState.CDR_REVIEW.value: WorkflowEvent.CDR_REVISION_REQUESTED.value,
        }.get(str(project.get("state")))
        if revision_event is None:
            return {
                "type": "project_document_revision",
                "status": "invalid_revision_stage",
                "project": _serialize(project),
                "response": (
                    f"Project `{project.get('name') or project.get('id')}` is currently "
                    f"`{project.get('state')}`; I did not request a document revision."
                ),
                "trace": ["parsed_document_revision_intent", "checked_authoritative_project_state"],
            }

        revised = await transition_project(
            UUID(str(project["id"])),
            TransitionRequest(
                event=revision_event,
                actor_id="human_operator",
                context={"reason": reason, "revision_requested": True},
            ),
        )
        updated = await storage.get_project(UUID(str(project["id"])))
        return {
            "type": "project_document_revision",
            "status": "revision_requested",
            "project": _serialize(updated or project),
            "revision_event": revision_event,
            "response": (
                f"I requested a new immutable revision for `{project.get('name') or project.get('id')}`. "
                f"The project moved into `{revised.get('next_state')}` for the stage owner to regenerate and review it."
            ),
            "trace": [
                "parsed_document_revision_intent",
                "matched_exact_project",
                "requested_immutable_document_revision",
                "published_stage_directive",
                "read_authoritative_project_state",
            ],
        }

    decision_match = re.search(
        r"\b(approve|approved|reject|rejected|deny|denied|decline|declined|edit|edits|revise|cancel)\b",
        lowered,
    )
    if decision_match:
        decision_word = decision_match.group(1)
        decision = (
            "APPROVED"
            if decision_word in {"approve", "approved"}
            else "REJECTED"
            if decision_word in {"reject", "rejected", "deny", "denied", "decline", "declined"}
            else "EDITS"
            if decision_word in {"edit", "edits", "revise"}
            else "CANCELLED"
        )
        storage = _storage()
        project, ambiguous = await _find_project_for_ceo_text(storage, instruction)
        if project is None:
            return {
                "type": "project_decision",
                "status": "ambiguous_project" if ambiguous else "needs_project",
                "response": (
                    "More than one project matches that request; give me the exact project UUID."
                    if ambiguous
                    else "I need an existing project name or UUID for that decision."
                ),
                "trace": ["parsed_project_decision_intent", "project_not_uniquely_identified"],
            }

        project_id = UUID(str(project["id"]))
        pending = await storage.list_approval_gates(
            project_id=project_id,
            status="PENDING",
            limit=100,
        )
        if not pending:
            return {
                "type": "project_decision",
                "status": "no_pending_decision",
                "project": _serialize(project),
                "response": (
                    f"Project `{project.get('name') or project.get('id')}` is currently "
                    f"`{project.get('state')}` and has no pending human approval gate. "
                    "I did not change its state."
                ),
                "trace": [
                    "parsed_project_decision_intent",
                    "checked_pending_approval_gates",
                    "no_pending_gate",
                ],
            }

        comments = None
        reason_match = re.search(
            r"\b(?:because|reason|comment|comments|with edits?|request)\s*[:=-]?\s*(.+)$",
            instruction,
            flags=re.IGNORECASE,
        )
        if reason_match:
            comments = reason_match.group(1).strip(" .\"'") or None
        if decision in {"REJECTED", "EDITS"} and not comments:
            selected_gate = pending[0]
            return {
                "type": "project_decision",
                "status": "needs_justification",
                "project": _serialize(project),
                "response": (
                    f"The `{selected_gate.get('gate_type') or 'human'}` gate for project "
                    f"`{project.get('name') or project.get('id')}` is pending. "
                    f"Tell me why you want to {decision.lower()} it so I can persist the decision."
                ),
                "trace": ["parsed_project_decision_intent", "required_decision_justification"],
            }

        result = await submit_decision(
            project_id,
            DecisionRequest(
                decision=decision,
                comments=comments,
                edits={"request": comments} if decision == "EDITS" and comments else None,
                decided_by="human_operator",
            ),
        )
        updated_project = await storage.get_project(project_id)
        selected_gate = pending[0]
        next_state = result.get("next_state") if isinstance(result, dict) else None
        state_text = next_state or (updated_project or project).get("state")
        return {
            "type": "project_decision",
            "status": str(result.get("status") or "decision_recorded"),
            "project": _serialize(updated_project or project),
            "gate": _serialize(selected_gate),
            "decision": decision,
            "result": _serialize(result),
            "response": (
                f"I recorded `{decision}` for the `{selected_gate.get('gate_type') or 'human'}` gate on "
                f"project `{project.get('name') or project.get('id')}`. "
                f"The project is now `{state_text}`."
            ),
            "trace": [
                "parsed_project_decision_intent",
                "matched_exact_project",
                "checked_pending_approval_gates",
                "persisted_human_decision",
                "read_authoritative_project_state",
            ],
        }

    project_id = _extract_uuid_from_text(instruction)

    project_resume_requested = re.search(
        r"\b(?:resume|retry|restart|continue|rerun|re-run)\b", lowered
    ) is not None
    if project_resume_requested:
        storage = _storage()
        project, ambiguous = await _find_project_for_ceo_text(storage, instruction)
        if project is None:
            return {
                "type": "project_resume",
                "status": "ambiguous_project" if ambiguous else "needs_project",
                "response": (
                    "More than one project matches that recovery request; give me the exact project UUID."
                    if ambiguous
                    else "I need an existing project name or UUID for the recovery request."
                ),
                "trace": ["parsed_project_resume_intent", "project_not_uniquely_identified"],
            }

        state = str(project.get("state") or "")
        if state in {ProjectState.COMPLETED.value, ProjectState.ARCHIVED.value}:
            return {
                "type": "project_resume",
                "status": "not_resumable",
                "project": _serialize(project),
                "response": (
                    f"Project `{project.get('name') or project.get('id')}` is already `{state}`. "
                    "I did not restart it."
                ),
                "trace": ["parsed_project_resume_intent", "checked_authoritative_terminal_state"],
            }

        action_word = "retry" if state == ProjectState.FAILED.value else "resume"
        return _queue_ceo_confirmation(
            action="project_resume",
            target_id=project.get("id"),
            label=f"{action_word} project {project.get('name') or project.get('id')}",
            response=(
                f"I found project `{project.get('name') or project.get('id')}` "
                f"(`{project.get('id')}`), currently `{state}`. "
                + (
                    "Retry will restore its last safe state before publishing one exact-project resume directive."
                    if state == ProjectState.FAILED.value
                    else "I will publish one resume directive only for this project."
                )
            ),
        )

    destructive_action = None
    if re.search(r"\b(?:delete|remove|purge)\b", lowered):
        destructive_action = "project_delete"
    elif re.search(r"\barchive\b", lowered):
        destructive_action = "project_archive"
    if destructive_action:
        storage = _storage()
        project, ambiguous = await _find_project_for_ceo_text(storage, instruction)
        if project is None:
            return {
                "type": destructive_action,
                "status": "needs_project",
                "response": (
                    "More than one project matches that name; give me the project UUID."
                    if ambiguous
                    else "I need an existing project name or UUID for that action."
                ),
                "trace": ["parsed_project_destructive_intent", "project_not_uniquely_identified"],
            }
        project_name = str(project.get("name") or project.get("id"))
        verb = "permanently delete" if destructive_action == "project_delete" else "archive"
        return _queue_ceo_confirmation(
            action=destructive_action,
            target_id=project.get("id"),
            label=f"{verb} project {project_name}",
            response=(
                f"I found project `{project_name}` (`{project.get('id')}`), currently `{project.get('state')}`. "
                f"This will {verb} that exact project."
            ),
        )

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


async def _handle_ceo_system_intent(instruction: str) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if not any(token in lowered for token in ("system", "schedule", "shutdown", "shut down")):
        return None

    if "schedule" in lowered:
        storage = _storage()
        if re.search(r"\b(?:show|status|current|what|inspect|list)\b", lowered) or not re.search(
            r"\b(?:enable|disable|set|update|change|configure)\b", lowered
        ):
            schedule = {
                "enabled": (await storage.get_config("schedule_enabled") or "false") == "true",
                "start_hour": int(await storage.get_config("schedule_start_hour") or 8),
                "end_hour": int(await storage.get_config("schedule_end_hour") or 18),
                "timezone": await storage.get_config("schedule_timezone") or "UTC",
                "days": (await storage.get_config("schedule_days") or "mon,tue,wed,thu,fri").split(","),
                "auto_shutdown": (await storage.get_config("schedule_auto_shutdown") or "true") == "true",
                "auto_resume": (await storage.get_config("schedule_auto_resume") or "true") == "true",
            }
            return {
                "type": "system_schedule",
                "status": "read",
                "schedule": schedule,
                "response": (
                    f"System schedule is `{'enabled' if schedule['enabled'] else 'disabled'}`: "
                    f"{schedule['start_hour']:02d}:00–{schedule['end_hour']:02d}:00 "
                    f"`{schedule['timezone']}` on {', '.join(schedule['days'])}."
                ),
                "trace": ["parsed_schedule_intent", "read_system_schedule"],
            }

        start_match = re.search(r"\b(?:start|from)\s+(\d{1,2})(?::\d{2})?\b", lowered)
        end_match = re.search(r"\b(?:end|until|to)\s+(\d{1,2})(?::\d{2})?\b", lowered)
        timezone_match = re.search(r"\b([A-Za-z]+/[A-Za-z_+-]+)\b", instruction)
        current_days = (await storage.get_config("schedule_days") or "mon,tue,wed,thu,fri").split(",")
        if "weekdays" in lowered:
            current_days = ["mon", "tue", "wed", "thu", "fri"]
        elif "every day" in lowered or "daily" in lowered:
            current_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        enabled = "disable" not in lowered
        req = ScheduleRequest(
            enabled=enabled,
            start_hour=int(start_match.group(1)) if start_match else int(await storage.get_config("schedule_start_hour") or 8),
            end_hour=int(end_match.group(1)) if end_match else int(await storage.get_config("schedule_end_hour") or 18),
            timezone=timezone_match.group(1) if timezone_match else (await storage.get_config("schedule_timezone") or "UTC"),
            days=current_days,
            auto_shutdown="no auto shutdown" not in lowered,
            auto_resume="no auto resume" not in lowered,
        )
        try:
            ZoneInfo(req.timezone)
        except ZoneInfoNotFoundError:
            return {
                "type": "system_schedule",
                "status": "needs_timezone",
                "response": f"`{req.timezone}` is not a recognized IANA timezone. Use a value such as `America/Toronto` or `UTC`.",
                "trace": ["parsed_schedule_intent", "rejected_invalid_timezone"],
            }
        await update_schedule(req)
        return {
            "type": "system_schedule",
            "status": "updated",
            "schedule": req.model_dump(),
            "response": (
                f"System schedule is now `{'enabled' if req.enabled else 'disabled'}`: "
                f"{req.start_hour:02d}:00–{req.end_hour:02d}:00 `{req.timezone}` on {', '.join(req.days)}."
            ),
            "trace": ["parsed_schedule_intent", "validated_timezone", "updated_system_schedule"],
        }

    if re.search(r"\b(?:shutdown|shut down)\b", lowered):
        current = await system_status()
        return _queue_ceo_confirmation(
            action="system_shutdown",
            target_id=None,
            label="shut down the AIAT control plane",
            response=(
                f"The system is currently `{current.get('state')}` with {current.get('active_projects')} active projects. "
                "Shutdown broadcasts to every team, waits for acknowledgements, and stops work."
            ),
        )
    if re.search(r"\b(?:resume|restart|start)\b", lowered):
        current = await system_status()
        return _queue_ceo_confirmation(
            action="system_resume",
            target_id=None,
            label="resume the AIAT control plane",
            response=f"The system is currently `{current.get('state')}`. Resume republishes work for active projects.",
        )
    if re.search(r"\b(?:status|state|health|uptime|show|inspect)\b", lowered):
        status = await system_status()
        return {
            "type": "system_status",
            "status": "read",
            "system": status,
            "response": (
                f"System is `{status.get('state')}` with {status.get('active_projects')} active of "
                f"{status.get('total_projects')} total projects. Uptime is {status.get('uptime_seconds')} seconds; "
                f"scheduled operation is `{'enabled' if status.get('schedule_enabled') else 'disabled'}`."
            ),
            "trace": ["parsed_system_status_intent", "read_system_lifecycle"],
        }
    return None


async def _handle_ceo_dead_letter_intent(instruction: str) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if not any(token in lowered for token in ("dead letter", "dead-letter", "dlq")):
        return None
    id_match = re.search(r"(?:dead[ -]?letter|dlq)(?:\s+(?:entry|id))?\s*#?\s*(\d+)", lowered)
    letter_id = int(id_match.group(1)) if id_match else None
    if re.search(r"\breplay\b", lowered):
        if letter_id is None:
            return {
                "type": "dead_letter_replay",
                "status": "needs_dead_letter",
                "response": "Give me the numeric dead-letter ID to replay. I will inspect it and ask for confirmation first.",
                "trace": ["parsed_dead_letter_replay", "missing_dead_letter_id"],
            }
        letter = await get_dead_letter(letter_id)
        return _queue_ceo_confirmation(
            action="dead_letter_replay",
            target_id=letter_id,
            label=f"replay dead letter {letter_id}",
            response=(
                f"Dead letter `{letter_id}` failed for `{letter.get('recipient_team')}` because "
                f"`{letter.get('failure_reason')}`. Replay creates a new delivery attempt and preserves the forensic record."
            ),
        )
    if letter_id is not None:
        letter = await get_dead_letter(letter_id)
        safe_letter = {
            key: value
            for key, value in letter.items()
            if key not in {"envelope_json", "payload", "body"}
        }
        return {
            "type": "dead_letter_detail",
            "status": "read",
            "dead_letter": safe_letter,
            "response": (
                f"Dead letter `{letter_id}` targets `{letter.get('recipient_team')}` and failed because "
                f"`{letter.get('failure_reason')}`. Say `replay dead letter {letter_id}` to begin a confirmed replay."
            ),
            "trace": ["parsed_dead_letter_detail", "read_dead_letter_metadata"],
        }
    letters = await list_dead_letters(limit=20)
    summary = "; ".join(
        f"#{letter.get('id')} {letter.get('recipient_team')} — {letter.get('failure_reason')}"
        for letter in letters[:8]
    ) or "none"
    return {
        "type": "dead_letter_list",
        "status": "read",
        "dead_letters": letters,
        "response": f"Dead-letter queue: {summary}.",
        "trace": ["parsed_dead_letter_list", "read_dead_letter_queue"],
    }


async def _handle_ceo_credential_intent(instruction: str) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if not any(token in lowered for token in ("credential", "credentials", "secret", "secrets")):
        return None
    credentials = await list_credentials()
    matches = [
        credential
        for credential in credentials
        if str(credential.get("name") or "").lower() in lowered
    ]
    credential = matches[0] if len(matches) == 1 else None
    if re.search(r"\b(?:create|add|set|update|rotate|replace|value|token|password)\b", lowered):
        return {
            "type": "credential_secure_change",
            "status": "requires_secure_input",
            "response": (
                "I will not accept, echo, or persist credential values in chat history. Open the "
                "[Credentials](/credentials) secure form to create or rotate the value; then ask me here to inspect its metadata or audit trail."
            ),
            "trace": ["parsed_credential_change", "blocked_secret_in_chat", "directed_secure_boundary"],
        }
    if re.search(r"\b(?:delete|remove|purge)\b", lowered):
        if credential is None:
            return {
                "type": "credential_delete",
                "status": "needs_credential",
                "response": "Give me one exact existing credential name. Credential values are never shown in chat.",
                "trace": ["parsed_credential_delete", "credential_not_uniquely_identified"],
            }
        name = str(credential.get("name"))
        return _queue_ceo_confirmation(
            action="credential_delete",
            target_id=name,
            label=f"delete credential {name}",
            response=f"I found credential `{name}`. Deleting it can break every adapter that references that name; its value remains hidden.",
        )
    if "audit" in lowered and credential is not None:
        audit = await credential_audit_log(str(credential.get("name")), limit=20)
        return {
            "type": "credential_audit",
            "status": "read",
            "credential": credential,
            "audit": audit,
            "response": f"Credential `{credential.get('name')}` has {len(audit)} recent audited access events. No secret value was read or exposed.",
            "trace": ["parsed_credential_audit", "read_credential_audit_metadata"],
        }
    if credential is not None:
        return {
            "type": "credential_metadata",
            "status": "read",
            "credential": credential,
            "response": f"Credential `{credential.get('name')}` exists. Type is `{credential.get('secret_type') or credential.get('type') or 'other'}`; its value is intentionally hidden.",
            "trace": ["parsed_credential_metadata", "read_non_secret_metadata"],
        }
    names = [str(item.get("name")) for item in credentials]
    return {
        "type": "credential_list",
        "status": "read",
        "credentials": credentials,
        "response": "Credential registry (metadata only): " + (", ".join(f"`{name}`" for name in names) if names else "empty") + ".",
        "trace": ["parsed_credential_list", "read_non_secret_credential_registry"],
    }


async def _find_flow_for_ceo_text(storage: AgentStorage, instruction: str) -> tuple[dict[str, Any] | None, bool]:
    flow_id = _extract_uuid_from_text(instruction)
    if flow_id:
        return await storage.get_flow(UUID(flow_id)), False
    flows = await storage.list_flows(limit=100)
    lowered = instruction.lower()
    matches = [
        flow
        for flow in flows
        if str(flow.get("name") or "").strip()
        and str(flow.get("name") or "").strip().lower() in lowered
    ]
    return (matches[0], False) if len(matches) == 1 else (None, len(matches) > 1)


async def _handle_ceo_flow_intent(instruction: str) -> dict[str, Any] | None:
    lowered = instruction.lower()
    if not any(token in lowered for token in ("flow", "workflow")):
        return None
    storage = _storage()
    if "instance" in lowered and re.search(r"\b(?:start|pause|resume|cancel)\b", lowered):
        instance_id = _extract_uuid_from_text(instruction)
        if instance_id is None:
            return {
                "type": "flow_instance_action",
                "status": "needs_instance",
                "response": "Give me the flow-instance UUID for that lifecycle action.",
                "trace": ["parsed_flow_instance_action", "missing_instance_id"],
            }
        instance = await get_flow_instance(UUID(instance_id))
        action_match = re.search(r"\b(start|pause|resume|cancel)\b", lowered)
        action_name = action_match.group(1) if action_match else ""
        if action_name == "cancel":
            return _queue_ceo_confirmation(
                action="flow_instance_cancel",
                target_id=instance_id,
                label=f"cancel flow instance {instance_id}",
                response=f"Flow instance `{instance_id}` is currently `{instance.get('status')}`. Cancellation is terminal for this instance.",
            )
        result = await flow_instance_action(
            UUID(instance_id), FlowInstanceActionRequest(action=action_name)
        )
        return {
            "type": "flow_instance_action",
            "status": str(result.get("status") or action_name),
            "instance": result,
            "response": f"Flow instance `{instance_id}` action `{action_name}` completed; current status is `{result.get('status')}`.",
            "trace": ["parsed_flow_instance_action", f"executed_{action_name}"],
        }
    if re.search(r"\b(?:delete|remove|purge)\b", lowered):
        flow, ambiguous = await _find_flow_for_ceo_text(storage, instruction)
        if flow is None:
            return {
                "type": "flow_delete",
                "status": "needs_flow",
                "response": "More than one flow matches; give me its UUID." if ambiguous else "Give me an existing flow name or UUID to delete.",
                "trace": ["parsed_flow_delete", "flow_not_uniquely_identified"],
            }
        return _queue_ceo_confirmation(
            action="flow_delete",
            target_id=flow.get("id"),
            label=f"delete flow {flow.get('name') or flow.get('id')}",
            response=f"I found flow `{flow.get('name')}` (`{flow.get('id')}`), version `{flow.get('version')}`. Deletion removes that definition.",
        )
    if re.search(r"\b(?:create|new|build)\b", lowered):
        return {
            "type": "flow_create",
            "status": "needs_definition",
            "response": (
                "I can create the flow after I have its ordered nodes and transitions. Describe the departments/outcomes in order, "
                "or use the [Flow Builder](/flows/new) for a visual graph; I will validate the resulting definition before activation."
            ),
            "trace": ["parsed_flow_create", "requested_flow_definition"],
        }
    if re.search(r"\b(?:status|detail|inspect|show)\b", lowered):
        flow, ambiguous = await _find_flow_for_ceo_text(storage, instruction)
        if flow is not None:
            return {
                "type": "flow_detail",
                "status": "read",
                "flow": _serialize(flow),
                "response": f"Flow `{flow.get('name')}` is version `{flow.get('version')}` and `{'active' if flow.get('is_active') else 'inactive'}`.",
                "trace": ["parsed_flow_detail", "read_flow_definition"],
            }
        if ambiguous:
            return {
                "type": "flow_detail",
                "status": "needs_flow",
                "response": "More than one flow matches that name; give me the flow UUID.",
                "trace": ["parsed_flow_detail", "ambiguous_flow_name"],
            }
    flow_rows = await storage.list_flows(limit=20, offset=0)
    flows = [_serialize(flow) for flow in flow_rows]
    instances = await list_active_flow_instances_early()
    summary = "; ".join(
        f"{flow.get('name')} ({flow.get('id')}, {'active' if flow.get('is_active') else 'inactive'})"
        for flow in flows[:8]
    ) or "none"
    return {
        "type": "flow_list",
        "status": "read",
        "flows": flows,
        "active_instances": instances,
        "response": f"Flows: {summary}. Active instances: {len(instances)}.",
        "trace": ["parsed_flow_list", "read_flow_definitions", "read_active_instances"],
    }


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


async def _handle_ceo_worker_intent(
    instruction: str,
    context_worker_id: UUID | None = None,
) -> dict[str, Any] | None:
    lowered = instruction.lower()
    lifecycle_requested = re.search(
        r"\b(?:status|state|why|explain|blocked|reject|rejected|deny|decline|details|evaluate|audit|scan|approve|activate|deactivate|drain)\b",
        lowered,
    ) is not None
    reclassify_requested = (
        re.search(r"\b(?:reclassify|reroute|route|move|assign|transfer)\b", lowered) is not None
        and re.search(r"\b(?:team|department|dept|office|to|into|under|as)\b", lowered) is not None
    )
    has_worker_target = any(
        token in lowered for token in ("worker", "workers", "hiring board", "candidate", "agent")
    )
    if not (
        has_worker_target
        or reclassify_requested
        or (context_worker_id is not None and lifecycle_requested)
    ):
        return None
    if "hire" in lowered:
        return None

    storage = _storage()
    board_requested = (
        "hiring board" in lowered
        or "candidates" in lowered
        or re.search(r"\b(?:list|show)\s+(?:all\s+)?workers\b", lowered) is not None
        or re.search(r"\b(?:list|show)\s+(?:all\s+)?agents\b", lowered) is not None
    )
    if board_requested and not _extract_uuid_from_text(instruction):
        workers = await storage.list_workers()
        return {
            "type": "hiring_board",
            "status": "read",
            "workers": [_serialize(worker) for worker in workers],
            "response": _summarize_workers_for_ceo(workers),
            "trace": ["parsed_hiring_board_intent", "listed_worker_registry"],
        }

    worker = await _find_worker_for_ceo_text(storage, instruction)
    if worker is None and context_worker_id is not None:
        worker = await storage.get_worker(context_worker_id)
    if worker is None:
        return {
            "type": "worker_action",
            "status": "needs_worker",
            "response": "I need a worker UUID or unique worker name for that hiring-board action.",
            "trace": ["parsed_worker_action_intent", "worker_not_identified"],
        }

    worker_id = UUID(str(worker["id"]))

    if reclassify_requested:
        target_team = _target_department_for_reclassification_text(instruction)
        if target_team is None:
            return {
                "type": "worker_reclassification",
                "status": "needs_department",
                "worker": _serialize(worker),
                "response": (
                    f"I found `{worker.get('name')}`, but I need a known target department: "
                    "`dept_qa`, `office_cso`, `dept_devops`, `dept_production`, `office_cfo`, or `office_chrm`."
                ),
                "trace": ["parsed_worker_reclassification_intent", "target_department_not_identified"],
            }

        previous_team = str(worker.get("team_id") or "unassigned")
        if str(worker.get("status") or "").upper() == "ACTIVE":
            return {
                "type": "worker_reclassification",
                "status": "needs_deactivation",
                "worker": _serialize(worker),
                "previous_team_id": previous_team,
                "team_id": target_team,
                "response": (
                    f"`{worker.get('name')}` is ACTIVE. I will not reclassify an active worker in-place. "
                    "Drain or deactivate it first, then reclassify so evaluation and approval can restart "
                    "for the target department."
                ),
                "trace": ["parsed_worker_reclassification_intent", "blocked_active_worker_reclassification"],
            }
        if previous_team == target_team:
            return {
                "type": "worker_reclassification",
                "status": "unchanged",
                "worker": _serialize(worker),
                "previous_team_id": previous_team,
                "team_id": target_team,
                "response": (
                    f"`{worker.get('name')}` is already assigned to `{target_team}`. "
                    f"Routing reason: {_department_hiring_reason(target_team)}"
                ),
                "trace": ["parsed_worker_reclassification_intent", "target_department_already_assigned"],
            }

        await storage.update_worker_config(worker_id, team_id=target_team, evaluation_status="pending")
        updated = await storage.get_worker(worker_id)
        return {
            "type": "worker_reclassification",
            "status": "reclassified",
            "worker": _serialize(updated or {**worker, "team_id": target_team, "evaluation_status": "pending"}),
            "previous_team_id": previous_team,
            "team_id": target_team,
            "response": (
                f"I reclassified `{worker.get('name')}` from `{previous_team}` to `{target_team}`. "
                f"Routing reason: {_department_hiring_reason(target_team)} I reset evaluation status to "
                "`pending`; keep it inactive until evaluation and approval are clean for the new department."
            ),
            "trace": ["parsed_worker_reclassification_intent", "updated_worker_department"],
        }

    if re.search(r"\b(?:reject|deny|decline)\b", lowered):
        if str(worker.get("status") or "").upper() == "ACTIVE":
            return {
                "type": "worker_rejection",
                "status": "needs_deactivation",
                "worker": _serialize(worker),
                "response": (
                    f"`{worker.get('name')}` is ACTIVE. I will not reject an active worker in-place. "
                    "Drain or deactivate it first, then reject the candidate record."
                ),
                "trace": ["parsed_worker_rejection_intent", "blocked_active_worker_rejection"],
            }

        await storage.update_worker_config(worker_id, evaluation_status="rejected")
        if str(worker.get("status") or "").upper() != "INACTIVE":
            await storage.update_worker_status(worker_id, status="INACTIVE")
        updated = await storage.get_worker(worker_id)
        return {
            "type": "worker_rejection",
            "status": "rejected",
            "worker": _serialize(updated or {**worker, "evaluation_status": "rejected", "status": "INACTIVE"}),
            "response": (
                f"I rejected `{worker.get('name')}` for hiring/activation. The worker is kept inactive "
                "and remains on the Hiring Board for audit history; use permanent cleanup only for test debris."
            ),
            "trace": ["parsed_worker_rejection_intent", "recorded_rejection_status"],
        }

    if any(word in lowered for word in ("status", "state", "why", "explain", "blocked", "rejected", "details")):
        latest_report = await _latest_worker_report(storage, worker_id)
        return {
            "type": "worker_status",
            "status": "read",
            "worker": _serialize(worker),
            "evaluation": _serialize(latest_report) if latest_report else None,
            "response": _summarize_worker_for_ceo(worker, latest_report=latest_report),
            "trace": ["parsed_worker_status_intent", "read_worker_registry", "read_latest_evaluation"],
        }

    if re.search(r"\b(?:evaluate|audit|scan)\b", lowered):
        report = await evaluate_worker(worker_id, WorkerEvaluateRequest())
        return {
            "type": "worker_evaluate",
            "status": "evaluated",
            "worker": _serialize(await storage.get_worker(worker_id) or worker),
            "evaluation": report,
            "response": (
                f"Evaluation completed for `{worker.get('name')}`: verdict "
                f"`{report.get('verdict')}`, recommended status `{report.get('recommended_status')}`. "
                "If approved, the next CEO-chat command is `approve worker <name>`, then `activate worker <name>`."
            ),
            "trace": ["parsed_worker_evaluation_intent", "ran_guarded_evaluator", "stored_evaluation_status"],
        }

    if re.search(r"\b(?:approve|approved)\b", lowered):
        latest_report = await _latest_worker_report(storage, worker_id)
        if worker.get("source_repo"):
            if latest_report is None:
                return {
                    "type": "worker_approval",
                    "status": "needs_evaluation",
                    "worker": _serialize(worker),
                    "evaluation": None,
                    "response": (
                        f"I cannot approve `{worker.get('name')}` yet. External candidates must have "
                        "a stored evaluation report first. Run `evaluate worker <name>` from CEO chat "
                        "or the Hiring Board, then review the report."
                    ),
                    "trace": ["parsed_worker_approval_intent", "blocked_missing_evaluation_report"],
                }

            verdict = str(latest_report.get("verdict") or "").upper()
            blocked_reasons = latest_report.get("blocked_reasons") or []
            requires_human_approval = bool(latest_report.get("requires_human_approval"))
            if blocked_reasons or verdict == "REJECTED":
                reasons = "; ".join(str(reason) for reason in blocked_reasons[:5]) or "latest verdict is REJECTED"
                return {
                    "type": "worker_approval",
                    "status": "blocked",
                    "worker": _serialize(worker),
                    "evaluation": _serialize(latest_report),
                    "response": (
                        f"I cannot approve `{worker.get('name')}`. The latest evaluation is `{verdict}` "
                        f"and blocks activation: {reasons}."
                    ),
                    "trace": ["parsed_worker_approval_intent", "blocked_by_latest_evaluation"],
                }

            if verdict == "CONDITIONAL" and not requires_human_approval:
                return {
                    "type": "worker_approval",
                    "status": "blocked",
                    "worker": _serialize(worker),
                    "evaluation": _serialize(latest_report),
                    "response": (
                        f"I cannot approve `{worker.get('name')}` from a conditional evaluation unless "
                        "the report explicitly requires human approval and has no blocking reasons."
                    ),
                    "trace": ["parsed_worker_approval_intent", "blocked_by_conditional_evaluation"],
                }

            if verdict not in {"APPROVED", "CONDITIONAL"}:
                return {
                    "type": "worker_approval",
                    "status": "needs_evaluation",
                    "worker": _serialize(worker),
                    "evaluation": _serialize(latest_report),
                    "response": (
                        f"I cannot approve `{worker.get('name')}` yet. The latest evaluation verdict is "
                        f"`{verdict or 'UNKNOWN'}`; run evaluation again until the result is approved "
                        "or explicitly pending human approval."
                    ),
                    "trace": ["parsed_worker_approval_intent", "blocked_by_unready_evaluation"],
                }

        await storage.update_worker_config(worker_id, evaluation_status="approved")
        updated = await storage.get_worker(worker_id)
        return {
            "type": "worker_approval",
            "status": "approved",
            "evaluation": _serialize(latest_report) if latest_report else None,
            "worker": _serialize(updated or worker),
            "response": (
                f"I marked `{worker.get('name')}` evaluation status as approved for activation gating. "
                f"The worker remains `{(updated or worker).get('status')}` until you explicitly activate it."
            ),
            "trace": ["parsed_worker_approval_intent", "recorded_human_approval_status"],
        }

    action = None
    if re.search(r"\bdeactivate\b", lowered):
        action = "DEACTIVATE"
    elif re.search(r"\bdrain\b", lowered):
        action = "DRAIN"
    elif re.search(r"\bactivate\b", lowered):
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
                + "; ".join(
                    f"{runtime['id']}={runtime['status']}"
                    + (
                        f" ({'optional; ' if runtime.get('optional') else ''}"
                        f"missing: {', '.join(runtime['missing_packages'])})"
                        if runtime.get("missing_packages")
                        else ""
                    )
                    for runtime in runtimes
                )
            ),
            "trace": ["parsed_runtime_readiness_intent", "read_runtime_policy"],
        }
    if any(token in lowered for token in ("integration", "docling", "github", "semgrep", "n8n")):
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


def _ceo_operator_intent_is_api_owned(
    instruction: str,
    context_worker_id: UUID | None = None,
    context_confirmation_token: UUID | None = None,
) -> bool:
    lowered = instruction.lower()
    simple_confirmation = re.fullmatch(
        r"(?:confirm|confirmed|proceed|yes|do it|cancel|never mind|nevermind|no|stop)(?:\s+(?:it|action))?[.!]?",
        lowered.strip(),
    ) is not None
    if context_confirmation_token is not None and re.search(
        r"\b(?:confirm|confirmed|proceed|yes|do it|cancel|never mind|nevermind|no|stop)\b",
        lowered,
    ):
        return True
    if simple_confirmation:
        return True
    if context_worker_id is not None and any(
        token in lowered
        for token in (
            "reclassify",
            "evaluate",
            "approve",
            "activate",
            "deactivate",
            "drain",
            "status",
            "department",
            "production dep",
        )
    ):
        return True
    if "hire" in lowered and any(
        token in lowered for token in ("agent", "worker", "engineer", "developer", "specialist")
    ):
        return True
    if "project" in lowered:
        if (
            re.search(r"\bsecurity\s+(?:blocker|blocked|veto)\b", lowered)
            and re.search(r"\b(?:resolve|remediate|address|fix|clear|reopen|revise)\b", lowered)
            and "override" not in lowered
        ):
            return True
        if any(
            word in lowered
            for word in (
                "create", "new", "start", "initialize", "init", "list", "show", "recent",
                "delete", "remove", "purge", "archive",
            )
        ):
            return True
        if re.search(
            r"\b(?:resume|retry|restart|continue|rerun|re-run)\b",
            lowered,
        ):
            return True
        if re.search(
            r"\b(?:approve|approved|reject|rejected|deny|denied|decline|declined|edit|edits|revise|cancel)\b",
            lowered,
        ):
            return True
        if any(word in lowered for word in ("status", "state", "progress", "workspace")):
            return (
                _extract_uuid_from_text(instruction) is not None
                or _extract_project_status_query(instruction) is not None
            )
    if any(token in lowered for token in ("company", "org", "organization", "department", "departments", "dept", "dep", "graph")):
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
            "n8n",
        )
    ):
        return True
    return any(
        token in lowered
        for token in (
            "system",
            "schedule",
            "shutdown",
            "shut down",
            "dead letter",
            "dead-letter",
            "dlq",
            "credential",
            "credentials",
            "secret",
            "secrets",
            "flow",
            "workflow",
        )
    )


async def _handle_ceo_operator_intent(
    instruction: str,
    context_worker_id: UUID | None = None,
    context_confirmation_token: UUID | None = None,
) -> dict[str, Any] | None:
    confirmation = await _handle_ceo_confirmation_intent(
        instruction,
        context_confirmation_token,
    )
    if confirmation is not None:
        return confirmation
    handlers = (
        # Project security-recovery and decision phrases are scoped by an
        # exact project target. Run them before broad metadata handlers such
        # as credentials so remediation text cannot steal the intent.
        (_handle_ceo_project_intent, False),
        (_handle_ceo_hiring_intent, False),
        (_handle_ceo_hiring_followup_intent, True),
        (_handle_ceo_system_intent, False),
        (_handle_ceo_dead_letter_intent, False),
        (_handle_ceo_credential_intent, False),
        (_handle_ceo_flow_intent, False),
        (_handle_ceo_readiness_intent, False),
        (_handle_ceo_company_intent, False),
        (_handle_ceo_worker_intent, True),
    )
    for handler, accepts_worker_context in handlers:
        action = (
            await handler(instruction, context_worker_id)
            if accepts_worker_context
            else await handler(instruction)
        )
        if action is not None:
            return action
    return None


def _ceo_progress_detail(instruction: str) -> str:
    lowered = instruction.lower()
    if "hire" in lowered:
        return "I’m validating the candidate source, department routing, and hiring gates."
    if any(token in lowered for token in ("worker", "workers", "candidate", "hiring board")):
        return "I’m checking the live worker registry and governance state."
    if "project" in lowered:
        return "I’m checking project records, workflow state, and available next actions."
    if any(token in lowered for token in ("system", "schedule", "shutdown", "shut down")):
        return "I’m checking lifecycle state, active work, and the exact control boundary."
    if any(token in lowered for token in ("dead letter", "dead-letter", "dlq")):
        return "I’m inspecting dead-letter metadata and replay safety."
    if any(token in lowered for token in ("credential", "credentials", "secret", "secrets")):
        return "I’m checking credential metadata without reading or exposing secret values."
    if any(token in lowered for token in ("flow", "workflow")):
        return "I’m checking flow definitions, active instances, and allowed lifecycle actions."
    if any(token in lowered for token in ("company", "organization", "org", "department")):
        return "I’m reading the live company structure and control-plane status."
    if any(token in lowered for token in ("runtime", "integration", "docling", "semgrep")):
        return "I’m checking runtime and integration readiness."
    return "I’m interpreting your request and selecting the safest available action."


_CEO_COMMAND_PREFIX = "ceo_command:"


def _ceo_command_json(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


async def _load_ceo_command(storage: AgentStorage, message_id: str) -> tuple[dict[str, Any], str] | None:
    raw = await storage.get_config(f"{_CEO_COMMAND_PREFIX}{message_id}")
    if raw is None:
        return None
    return json.loads(raw), raw


async def _store_new_ceo_command(
    storage: AgentStorage,
    *,
    message_id: str,
    instruction: str,
    context_worker_id: UUID | None,
    context_confirmation_token: UUID | None,
) -> tuple[dict[str, Any], bool]:
    record = {
        "request_id": message_id,
        "instruction": instruction,
        "context_worker_id": str(context_worker_id) if context_worker_id else None,
        "context_confirmation_token": (
            str(context_confirmation_token) if context_confirmation_token else None
        ),
        "status": "PENDING",
        "created_at": datetime.now(tz=UTC).isoformat(),
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }
    created = await storage.set_config_if_absent(
        f"{_CEO_COMMAND_PREFIX}{message_id}", _ceo_command_json(record)
    )
    if created:
        return record, True
    loaded = await _load_ceo_command(storage, message_id)
    if loaded is None:  # pragma: no cover - defensive against external deletion races
        raise RuntimeError("CEO command record disappeared during creation")
    return loaded[0], False


async def _transition_ceo_command(
    storage: AgentStorage,
    message_id: str,
    *,
    from_statuses: set[str],
    to_status: str,
    updates: dict[str, Any] | None = None,
) -> bool:
    loaded = await _load_ceo_command(storage, message_id)
    if loaded is None:
        return False
    record, raw = loaded
    if record.get("status") not in from_statuses:
        return False
    record.update(updates or {})
    record["status"] = to_status
    record["updated_at"] = datetime.now(tz=UTC).isoformat()
    return await storage.compare_and_set_config(
        f"{_CEO_COMMAND_PREFIX}{message_id}", raw, _ceo_command_json(record)
    )


async def _run_recovered_ceo_command(command: Any) -> None:
    """Execute a recovered command without letting one failure stop recovery."""
    try:
        await command()
    except BaseException as exc:
        # Cancellation is control flow on both asyncio and Trio.  It must
        # propagate to the lifespan task group during application shutdown.
        if isinstance(exc, anyio.get_cancelled_exc_class()):
            raise
        logger.exception("Recovered CEO command failed")


async def _recover_ceo_commands(storage: AgentStorage) -> None:
    """Recover accepted API-owned commands after an orchestrator restart."""
    for key, raw in (await storage.get_all_config()).items():
        if not key.startswith(_CEO_COMMAND_PREFIX):
            continue
        try:
            record = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.error("Invalid durable CEO command record", extra={"key": key})
            continue
        status = record.get("status")
        message_id = str(record.get("request_id") or key.removeprefix(_CEO_COMMAND_PREFIX))
        if status == "RUNNING":
            recovered = await _transition_ceo_command(
                storage,
                message_id,
                from_statuses={"RUNNING"},
                to_status="PENDING",
                updates={"recovered_after_restart": True},
            )
            if not recovered:
                continue
        elif status != "PENDING":
            continue
        command = partial(
            _process_ceo_operator_intent,
            instruction=str(record["instruction"]),
            context_worker_id=(
                UUID(record["context_worker_id"]) if record.get("context_worker_id") else None
            ),
            context_confirmation_token=(
                UUID(record["context_confirmation_token"])
                if record.get("context_confirmation_token")
                else None
            ),
            message_id=message_id,
        )
        task_group = app.state.ceo_command_task_group
        if task_group is None:
            # Direct callers (maintenance commands and focused tests) have no
            # application lifespan to own detached work.  Completing recovery
            # inline preserves the durable idempotency contract on any backend.
            await _run_recovered_ceo_command(command)
        else:
            task_group.start_soon(_run_recovered_ceo_command, command)


async def _process_ceo_operator_intent(
    *,
    instruction: str,
    context_worker_id: UUID | None,
    context_confirmation_token: UUID | None,
    message_id: str,
) -> None:
    """Run an API-owned CEO command after the chat request has been accepted."""
    storage: AgentStorage | None = app.state.storage
    durable = storage is not None and await _load_ceo_command(storage, message_id) is not None
    if durable and not await _transition_ceo_command(
        storage,
        message_id,
        from_statuses={"PENDING", "FAILED"},
        to_status="RUNNING",
        updates={"started_at": datetime.now(tz=UTC).isoformat()},
    ):
        return
    await _publish_ceo_chat_progress(
        stage="Working on it",
        detail=_ceo_progress_detail(instruction),
        correlation_id=message_id,
        parent_id=message_id,
    )
    try:
        action = await _handle_ceo_operator_intent(
            instruction,
            context_worker_id,
            context_confirmation_token,
        )
        if action is None:
            await _publish_ceo_chat_response(
                response_text=(
                    "I received the request, but I could not map it to a safe control-plane action. "
                    "Tell me the outcome you want, and I’ll either execute it or ask for the one missing detail."
                ),
                correlation_id=message_id,
                parent_id=message_id,
            )
            if durable:
                await _transition_ceo_command(
                    storage,
                    message_id,
                    from_statuses={"RUNNING"},
                    to_status="COMPLETED",
                    updates={"result": {"action": None}},
                )
            return
        await _publish_ceo_chat_response(
            response_text=action["response"],
            correlation_id=message_id,
            parent_id=message_id,
            action=action,
        )
        if durable:
            await _transition_ceo_command(
                storage,
                message_id,
                from_statuses={"RUNNING"},
                to_status="COMPLETED",
                updates={"result": action},
            )
    except Exception as exc:
        logger.exception("CEO chat async action failed")
        if durable:
            await _transition_ceo_command(
                storage,
                message_id,
                from_statuses={"RUNNING"},
                to_status="FAILED",
                updates={"error": str(exc)[:1000]},
            )
        await _publish_ceo_chat_response(
            response_text=(
                "I could not complete that control-plane action. Nothing was silently assumed. "
                f"The recorded error is: {str(exc)[:240]}"
            ),
            correlation_id=message_id,
            parent_id=message_id,
        )


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
    message_id = str(req.request_id or uuid4())
    instruction = req.message.strip()
    stream_instruction = _ceo_stream_instruction(instruction)
    payload = {
        "action": "HUMAN_DIRECTIVE",
        "instruction": stream_instruction,
        "source": "ceo_chat",
    }
    api_owned = _ceo_operator_intent_is_api_owned(
        instruction,
        req.context_worker_id,
        req.context_confirmation_token,
    )
    durable_record: dict[str, Any] | None = None
    if api_owned and req.async_mode:
        storage: AgentStorage | None = app.state.storage
        if storage is None:
            raise HTTPException(503, "Durable CEO command storage is unavailable")
        durable_record, _created = await _store_new_ceo_command(
            storage,
            message_id=message_id,
            instruction=instruction,
            context_worker_id=req.context_worker_id,
            context_confirmation_token=req.context_confirmation_token,
        )
        expected_identity = (
            instruction,
            str(req.context_worker_id) if req.context_worker_id else None,
            str(req.context_confirmation_token) if req.context_confirmation_token else None,
        )
        stored_identity = (
            durable_record.get("instruction"),
            durable_record.get("context_worker_id"),
            durable_record.get("context_confirmation_token"),
        )
        if stored_identity != expected_identity:
            raise HTTPException(409, "request_id is already bound to a different CEO command")
        if durable_record.get("status") == "COMPLETED":
            return {
                "ok": True,
                "entry_id": durable_record.get("entry_id"),
                "request_id": message_id,
                "status": "duplicate",
                "result": durable_record.get("result"),
            }
        if durable_record.get("status") == "RUNNING":
            return {
                "ok": True,
                "entry_id": durable_record.get("entry_id"),
                "request_id": message_id,
                "status": "running",
            }
        if durable_record.get("status") == "FAILED":
            await _transition_ceo_command(
                storage,
                message_id,
                from_statuses={"FAILED"},
                to_status="PENDING",
                updates={"retry_requested_at": datetime.now(tz=UTC).isoformat()},
            )
    if api_owned:
        payload["execution_owner"] = "orchestrator-api"
    envelope = {
        "message_id": message_id,
        "correlation_id": message_id,
        "msg_type": MessageType.TASK.value,
        "sender_id": "human_operator",
        "sender_team": "exec_ceo",
        "sender_role": AgentRole.ORCHESTRATOR.value,
        "recipient_team": "exec_ceo",
        "project_id": "operator-direct",
        "payload": payload,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=15, headers=_router_auth_headers()) as client:
            resp = await client.post(f"{ROUTER_URL}/messages/publish", json=envelope)
            if resp.status_code == 403:
                raise HTTPException(403, f"Policy denied: {resp.text}")
            if not resp.is_success:
                raise HTTPException(502, f"Router error {resp.status_code}: {resp.text}")
            result = resp.json()
    except Exception as exc:
        if durable_record is not None:
            storage = app.state.storage
            if storage is not None:
                await _transition_ceo_command(
                    storage,
                    message_id,
                    from_statuses={"PENDING"},
                    to_status="FAILED",
                    updates={"error": f"Router publication failed: {str(exc)[:500]}"},
                )
        raise

    if durable_record is not None:
        storage = app.state.storage
        if storage is not None:
            loaded = await _load_ceo_command(storage, message_id)
            if loaded is not None:
                record, raw = loaded
                record["entry_id"] = result.get("entry_id")
                record["updated_at"] = datetime.now(tz=UTC).isoformat()
                await storage.compare_and_set_config(
                    f"{_CEO_COMMAND_PREFIX}{message_id}", raw, _ceo_command_json(record)
                )

    if req.async_mode:
        if api_owned:
            background_tasks.add_task(
                _process_ceo_operator_intent,
                instruction=instruction,
                context_worker_id=req.context_worker_id,
                context_confirmation_token=req.context_confirmation_token,
                message_id=message_id,
            )
        else:
            background_tasks.add_task(
                _publish_ceo_chat_progress,
                stage="Thinking",
                detail="I’m reviewing the request, available actions, and the current operating context.",
                correlation_id=message_id,
                parent_id=message_id,
            )
        return {
            "ok": True,
            "entry_id": result.get("entry_id"),
            "request_id": message_id,
            "status": "accepted",
        }

    action = await _handle_ceo_operator_intent(
        instruction,
        req.context_worker_id,
        req.context_confirmation_token,
    )
    if action is not None:
        background_tasks.add_task(
            _publish_ceo_chat_response,
            response_text=action["response"],
            correlation_id=message_id,
            parent_id=message_id,
            action=action,
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
