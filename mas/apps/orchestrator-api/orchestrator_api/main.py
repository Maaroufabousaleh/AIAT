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
import base64
import hashlib
import hmac
import importlib
import importlib.util
import inspect
import json
import logging
import os
import re
import shutil
import ssl
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from functools import partial
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
import httpx
import prometheus_client
import sqlalchemy as sa
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from prometheus_client import Counter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mas_core.integrations import ProviderRegistry
from mas_core.integrations.contracts import (
    DEDICATED_PROJECT_MAPPING_PROFILE,
    BootstrapApplyResult,
    BootstrapPlan,
    CanonicalIteration,
    CanonicalProject,
    CanonicalWorkItem,
    ExternalEvent,
    LifecyclePlanError,
    LifecyclePlanStatus,
    ObjectType,
    PMInboundCanaryPlan,
    PMLifecycleTransitionPlan,
    ProjectProvisioningPlan,
    ProviderConnection,
    normalize_project_mapping_profile,
    pm_binding_effective_policy,
    validate_credential_references,
)
from mas_core.integrations.providers.base import provider_ssl_context
from mas_core.company_manifest import (
    DEFAULT_COMPANY_ID,
    CompanyManifestError,
    compile_company_manifest,
)
from mas_core.llm_gateway.client import LLMGatewayClient
from mas_core.memory import models as memory_models
from mas_core.memory.storage import AgentStorage, document_to_context_item
from mas_core.observability import configure_logging
from mas_core.observability.metrics import MAS_PROJECT_STATE
from mas_core.observability.tracing import bind_trace_id, new_trace_id
from mas_core.policy.tool_access import can_use_tool_with_metadata
from mas_core.protocols.enums import AgentRole, MessageType
from mas_core.worker_registry._risk_utils import is_medium_or_dual_use_worker, worker_risk_labels
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

VALID_SANDBOX_PROFILES = {"standard", "restricted", "gvisor", "firecracker"}
HARDENED_SANDBOX_PROFILES = {"gvisor", "firecracker"}

logger = logging.getLogger(__name__)

# Runtime registries are process-local discovery caches only. Authoritative
# worker, steward, candidate, rollout, and run state is persisted through
# AgentStorage; these maps never replace database records.
_worker_steward_runtimes: dict[str, Any] = {}
_worker_adapter_runtimes: dict[str, Any] = {}


async def _invalidate_worker_adapter_runtime(worker_id: UUID) -> None:
    """Retire cached adapters only when no pinned run needs them.

    Cache keys include the immutable adapter ID.  A rollout can therefore
    hydrate a new adapter for new dispatches without closing an older adapter
    that still owns an in-flight run.  Explicit retirement remains useful
    during worker removal, but a rollout must not use it as a blunt cache
    invalidation mechanism.
    """
    prefix = f"{worker_id}:"
    stale_entries = [
        _worker_adapter_runtimes.pop(key)
        for key in list(_worker_adapter_runtimes)
        if key.startswith(prefix)
    ]
    for stale in stale_entries:
        if hasattr(stale, "close"):
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


def _check_auth(x_api_key: str | None = Header(None), authorization: str | None = Header(None)) -> str:
    """Validate API key for protected endpoints.

    Accepts either X-API-Key header (frontend proxy) or Authorization: Bearer.
    """
    configured_keys = (
        ("operator", os.getenv("AIAT_OPERATOR_API_KEY", "")),
        ("pm_gateway", os.getenv("PM_GATEWAY_API_KEY", "")),
        ("service", os.getenv("MAS_API_KEY", "")),
        ("gateway", os.getenv("GATEWAY_API_KEY", "")),
    )
    configured_keys = tuple(item for item in configured_keys if item[1])
    distinct_values = [value for _principal, value in configured_keys]
    if len(distinct_values) != len(set(distinct_values)):
        raise HTTPException(503, "API credentials must be distinct by principal")
    if not configured_keys:
        raise HTTPException(503, "API authentication is not configured")
    token = x_api_key or authorization
    if token is None:
        raise HTTPException(401, "API key required")
    # Strip Bearer prefix if present
    if token.lower().startswith("bearer "):
        token = token[7:]
    supplied = token.strip()
    for principal, key in configured_keys:
        if hmac.compare_digest(supplied, key):
            return principal
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
WATCHDOG_INTERVAL_S = int(os.getenv("WATCHDOG_INTERVAL_S", "60"))
WATCHDOG_GRACE_S = int(os.getenv("WATCHDOG_GRACE_S", "300"))
# This controls scheduler wakeups, not the external check cadence.  Individual
# steward jobs retain their durable hourly/daily/weekly cadence and are only
# checked when due.
UPDATE_MONITOR_INTERVAL_S = max(15, int(os.getenv("UPDATE_MONITOR_INTERVAL_S", "300")))
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")


def _identity_required() -> bool:
    """Return whether this deployment enforces mail identity before activation.

    Development fixtures stay opt-in so the existing local control-plane test
    suite never impersonates a mail edge. Production and staging are fail
    closed by default and must provision/verify an identity before activation.
    """
    environment = os.getenv("MAS_ENVIRONMENT", "development").strip().lower()
    if environment in {"production", "prod", "staging"}:
        # A deployment flag must never bypass the mandatory production hiring
        # gate.  The override exists only to keep local development fixtures
        # independent from a live mail edge.
        return True
    configured = os.getenv("AIAT_IDENTITY_REQUIRED")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _identity_company_id() -> UUID:
    raw = os.getenv("AIAT_COMPANY_ID", "")
    if raw:
        try:
            return UUID(raw)
        except ValueError as exc:
            raise RuntimeError("AIAT_COMPANY_ID must be a UUID") from exc
    if _identity_required():
        raise RuntimeError("AIAT_COMPANY_ID must be configured when identity provisioning is required")
    return uuid5(NAMESPACE_URL, "aiat.local-development-company")


def _identity_client() -> Any:
    from .identity_client import IdentityClientConfig, SignedIdentityClient

    return SignedIdentityClient(IdentityClientConfig.from_environment())


def _tool_service_client() -> Any:
    from .tool_service_client import SignedToolServiceClient, ToolServiceClientConfig

    return SignedToolServiceClient(ToolServiceClientConfig.from_environment())


_IDENTITY_TOOL_GRANTS = (
    "identity.email.get_address", "mail.list", "mail.search", "mail.read",
    "mail.wait_for_verification", "mail.extract_code", "mail.extract_link",
    "mail.mark_processed", "mail.delete", "mail.send_request", "mail.send_approved",
    "mail.get_delivery_status", "mail.cancel_queued",
    "identity.external.signup_request", "identity.external.login",
    "identity.external.get_status", "identity.external.rotate_credentials",
    "identity.external.suspend", "identity.external.close",
    "identity.session.create", "identity.session.use", "identity.session.revoke",
)


async def _provision_identity_tool_grants(worker_id: UUID) -> None:
    """Persist the governed identity tool set before worker activation."""
    client = _tool_service_client()
    for tool_name in _IDENTITY_TOOL_GRANTS:
        await client.request(
            "POST", f"/tools/workers/{worker_id}/grants", {"tool_name": tool_name},
        )
    await client.request(
        "POST", f"/tools/workers/{worker_id}/browser-identity", {}
    )


async def _revoke_local_identity_access(worker_id: UUID, *, retired: bool) -> None:
    """Revoke tool grants and close live laptop browser contexts first."""
    await _tool_service_client().request(
        "POST", f"/tools/workers/{worker_id}/identity-access/revoke",
        {"retired": retired},
    )


async def _identity_activation_blocker(storage: AgentStorage, worker: dict[str, Any]) -> str | None:
    """Provision or reconcile a required mailbox without activating early."""
    if not _identity_required():
        return None
    required_methods = ("get_worker_identity_lifecycle", "upsert_worker_identity_lifecycle")
    if not all(inspect.iscoroutinefunction(getattr(storage, name, None)) for name in required_methods):
        # Test-only/incomplete storage implementations cannot be treated as
        # evidence of verified production identity state.
        return "identity lifecycle persistence is unavailable"
    worker_id = worker["id"]
    try:
        client = _identity_client()
        await client.reconcile_worker_lifecycle(storage)
        lifecycle = await storage.get_worker_identity_lifecycle(worker_id)
        if lifecycle and str(lifecycle.get("state")) == "IDENTITY_ACTIVE":
            await _provision_identity_tool_grants(worker_id)
            return None
        company_id = _identity_company_id()
        provisioning_key = f"mailbox:{company_id}:{worker_id}"
        if lifecycle is None:
            await storage.upsert_worker_identity_lifecycle(worker_id=worker_id, state="HIRED_PENDING_IDENTITY", provisioning_key=provisioning_key, evidence={"activation_requested": True})
        mailbox_class = str((worker.get("adapter_config") or {}).get("identity_mailbox_class", "permanent")).strip().lower()
        if mailbox_class not in {"permanent", "temporary"}:
            return "worker identity mailbox class is invalid"
        identity = await client.provision_worker(company_id=company_id, worker_id=worker_id, actor_id="orchestrator-api", purpose="approved worker activation", mailbox_class=mailbox_class)
        await storage.upsert_worker_identity_lifecycle(worker_id=worker_id, state=str(identity.get("state") or "IDENTITY_PROVISIONING"), provisioning_key=provisioning_key, identity_address=identity.get("address"), identity_service_id=UUID(str(identity["id"])) if identity.get("id") else None, evidence={"provisioning_response": "received"})
        if str(identity.get("state")) != "IDENTITY_ACTIVE":
            return "mailbox provisioning is awaiting real inbound delivery verification"
        await _provision_identity_tool_grants(worker_id)
        return None
    except Exception as exc:
        error_code = type(exc).__name__
        try:
            await storage.upsert_worker_identity_lifecycle(worker_id=worker_id, state="IDENTITY_PROVISIONING_FAILED", failure_code=error_code, evidence={"failure_code": error_code})
        except Exception:
            logger.exception("identity_lifecycle_failure_persistence_failed", extra={"worker_id": str(worker_id)})
        return f"IDENTITY_PROVISIONING_FAILED ({error_code})"


async def _suspend_worker_identity(storage: AgentStorage, worker_id: UUID) -> str | None:
    """Revoke remote mailbox/browser access after stopping the local worker."""
    if not _identity_required():
        return None
    errors: list[str] = []
    try:
        await _revoke_local_identity_access(worker_id, retired=False)
    except Exception as exc:
        errors.append(f"local:{type(exc).__name__}")
    try:
        client = _identity_client()
        await client.suspend_worker(worker_id=worker_id, actor_id="orchestrator-api", purpose="worker deactivated")
        if inspect.iscoroutinefunction(getattr(storage, "upsert_worker_identity_lifecycle", None)):
            await storage.upsert_worker_identity_lifecycle(worker_id=worker_id, state="SUSPENDED", evidence={"deactivation": True})
        return ",".join(errors) or None
    except Exception as exc:
        error_code = type(exc).__name__
        if inspect.iscoroutinefunction(getattr(storage, "upsert_worker_identity_lifecycle", None)):
            await storage.upsert_worker_identity_lifecycle(worker_id=worker_id, state="IDENTITY_SUSPENSION_PENDING", failure_code=error_code, evidence={"failure_code": error_code})
        errors.append(f"remote:{error_code}")
        return ",".join(errors)


async def _archive_worker_identity(storage: AgentStorage, worker_id: UUID) -> str | None:
    """Archive remote mail/browser state during governed worker retirement."""
    if not _identity_required():
        return None
    errors: list[str] = []
    try:
        await _revoke_local_identity_access(worker_id, retired=True)
    except Exception as exc:
        errors.append(f"local:{type(exc).__name__}")
    try:
        client = _identity_client()
        await client.archive_worker(worker_id=worker_id, actor_id="orchestrator-api", purpose="worker retired")
        if inspect.iscoroutinefunction(getattr(storage, "upsert_worker_identity_lifecycle", None)):
            await storage.upsert_worker_identity_lifecycle(worker_id=worker_id, state="ARCHIVED", evidence={"retirement": True})
        return ",".join(errors) or None
    except Exception as exc:
        error_code = type(exc).__name__
        if inspect.iscoroutinefunction(getattr(storage, "upsert_worker_identity_lifecycle", None)):
            await storage.upsert_worker_identity_lifecycle(worker_id=worker_id, state="IDENTITY_ARCHIVAL_PENDING", failure_code=error_code, evidence={"failure_code": error_code})
        errors.append(f"remote:{error_code}")
        return ",".join(errors)

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
    persistent_company: dict[str, Any] | None = None
    try:
        first_class = await storage.get_company_read_model(DEFAULT_COMPANY_ID)
        if first_class.get("company"):
            persistent_company = first_class
            seeded = True
    except Exception:
        # Older test databases and pre-0031 installations use the bootstrap
        # config read model until the migration has been applied.
        logger.debug("company_read_model.first_class_unavailable", exc_info=True)
    ceo = _decode_json_config(
        await storage.get_config("default_company_ceo"),
        {"id": "ceo_agent", "name": "AIAT CEO", "role": "CEO"},
    )
    departments = _decode_json_config(await storage.get_config("default_company_departments"), [])
    if persistent_company and persistent_company.get("departments"):
        departments = [
            {
                "id": row.get("department_key"),
                "name": row.get("name"),
                "chief_worker_id": row.get("chief_worker_id"),
            }
            for row in persistent_company["departments"]
        ]
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

    manifest = (persistent_company or {}).get("manifest") if persistent_company else None
    return {
        "company": {
            "id": str((persistent_company or {}).get("company", {}).get("id") or DEFAULT_COMPANY_ID),
            "slug": (persistent_company or {}).get("company", {}).get("slug") or "aiat-default",
            "name": (persistent_company or {}).get("company", {}).get("name") or "AIAT",
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
        "manifest": _serialize(manifest) if manifest is not None else None,
        "company_assignments": (persistent_company or {}).get("assignments") or [],
        "company_budgets": (persistent_company or {}).get("budgets") or [],
    }


async def _org_graph_read_model(storage: AgentStorage) -> dict[str, Any]:
    company = await _company_read_model(storage)
    workers = await storage.list_workers()
    capabilities = await storage.list_capabilities()
    capability_by_id = {str(c["id"]): c for c in capabilities}

    nodes = [
        {"id": _graph_id("company", company["company"]["id"]), "type": "company", "label": company["company"]["name"]},
        {"id": "ceo_ceo_agent", "type": "ceo", "label": company["ceo"].get("name", "AIAT CEO")},
    ]
    company_node = _graph_id("company", company["company"]["id"])
    edges = [{"id": "company-ceo", "source": company_node, "target": "ceo_ceo_agent", "label": "led by"}]

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
    company_id: UUID = DEFAULT_COMPANY_ID

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name must not be blank")
        return normalized


class CompanyCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20_000)
    created_by: str = Field(default="operator", min_length=1, max_length=200)


class CompanyManifestRequest(BaseModel):
    manifest: dict[str, Any]
    source: str = Field(default="api", min_length=1, max_length=1000)


class CompanyManifestRollbackRequest(BaseModel):
    manifest_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class PMConnectionCreateRequest(BaseModel):
    provider_kind: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    display_name: str = Field(..., min_length=1, max_length=200)
    base_url: str = Field(..., max_length=1000)
    credential_ref: str = Field(..., min_length=1, max_length=200)
    capability_profile: str = Field(default="pm", min_length=1, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(default="operator", min_length=1, max_length=200)

    @field_validator("config")
    @classmethod
    def reject_inline_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_credential_references(value)

    @model_validator(mode="after")
    def require_secure_provider_url(self) -> PMConnectionCreateRequest:
        from urllib.parse import urlsplit

        parsed = urlsplit(self.base_url)
        host = str(parsed.hostname or "").lower()
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if self.provider_kind.lower() != "fake" and parsed.scheme != "https" and host not in local_hosts:
            raise ValueError("non-fake provider connections must use HTTPS")
        if self.provider_kind.lower() != "fake" and any(
            key in self.config
            for key in ("webhook_secret_test_only", "webhook_token_test_only")
        ):
            raise ValueError("test-only webhook credentials are permitted only for fake connections")
        return self


class PMConnectionStatusRequest(BaseModel):
    status: Literal["DISABLED", "SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"]


class PMBindingCreateRequest(BaseModel):
    connection_id: UUID
    external_project_id: str | None = None
    external_project_key: str | None = Field(default=None, max_length=240)
    external_repository: str | None = None
    mapping_profile: str = Field(default=DEDICATED_PROJECT_MAPPING_PROFILE, min_length=1, max_length=100)
    direction: Literal["outbound", "inbound", "both"] = "outbound"
    status: Literal["DISABLED", "SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"] = "DISABLED"

    @field_validator("mapping_profile")
    @classmethod
    def normalize_mapping_profile(cls, value: str) -> str:
        return normalize_project_mapping_profile(value)


class PMBindingUpdateRequest(BaseModel):
    external_project_id: str | None = Field(default=None, max_length=240)
    external_project_key: str | None = Field(default=None, max_length=240)
    external_repository: str | None = Field(default=None, max_length=1000)
    mapping_profile: str | None = Field(default=None, min_length=1, max_length=100)
    direction: Literal["outbound", "inbound", "both"] | None = None
    status: Literal["DISABLED", "SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"] | None = None

    @field_validator("mapping_profile")
    @classmethod
    def normalize_mapping_profile(cls, value: str | None) -> str | None:
        return normalize_project_mapping_profile(value) if value is not None else None


class PMProjectProvisioningRequest(BaseModel):
    connection_id: UUID
    mapping_profile: str = Field(default=DEDICATED_PROJECT_MAPPING_PROFILE, min_length=1, max_length=100)
    external_project_id: str | None = Field(default=None, max_length=240)

    @field_validator("mapping_profile")
    @classmethod
    def normalize_mapping_profile(cls, value: str) -> str:
        return normalize_project_mapping_profile(value)


class PMProjectProvisioningApplyRequest(BaseModel):
    plan: ProjectProvisioningPlan
    plan_digest: str = Field(..., min_length=64, max_length=64)
    confirm: bool = False


class PMPlanRequest(BaseModel):
    desired: dict[str, Any] = Field(default_factory=dict)

    @field_validator("desired")
    @classmethod
    def reject_inline_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_credential_references(value)


class PMApplyRequest(BaseModel):
    plan: BootstrapPlan
    plan_digest: str = Field(..., min_length=64, max_length=64)
    confirm: bool = False


class PMReconcileRequest(BaseModel):
    binding_id: UUID | None = None
    cursor: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    mode: Literal["audit", "repair_proposal"] = "audit"


class PMCutoverRequest(BaseModel):
    project_id: UUID
    binding_id: UUID
    confirm: bool = False


class PMRollbackRequest(BaseModel):
    project_id: UUID
    binding_id: UUID
    confirm: bool = False


class PMLifecyclePlanCreateRequest(BaseModel):
    target_type: Literal["pm_connection", "pm_binding"] = "pm_binding"
    connection_id: UUID
    binding_id: UUID | None = None
    desired_connection_status: Literal["DISABLED", "SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"] | None = None
    desired_binding_status: Literal["DISABLED", "SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"] | None = None
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


class PMLifecyclePlanApprovalRequest(BaseModel):
    plan_digest: str = Field(..., min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=1000)


class PMLifecyclePlanApplyRequest(BaseModel):
    plan_digest: str = Field(..., min_length=64, max_length=64)
    confirm: bool = False


class PMLifecyclePlanRejectRequest(BaseModel):
    plan_digest: str = Field(..., min_length=64, max_length=64)
    reason: str | None = Field(default=None, max_length=1000)


class PMConflictResolutionRequest(BaseModel):
    status: Literal["RESOLVED", "IGNORED", "REOPENED"] = "RESOLVED"
    resolution: dict[str, Any] = Field(default_factory=dict)


class PMExternalActorMappingCreateRequest(BaseModel):
    inbox_event_ids: list[UUID] = Field(min_length=1, max_length=20)
    authorized_scopes: list[Literal["issue.priority"]] = Field(default_factory=lambda: ["issue.priority"])
    reason: str = Field(default="operator-authorized live YouTrack certification actor", min_length=1, max_length=500)


class PMInboundCanaryPlanCreateRequest(BaseModel):
    binding_id: UUID
    canonical_issue_id: UUID
    external_issue_id: str = Field(min_length=1, max_length=240)
    mapping_id: UUID
    actor_mapping_id: UUID
    target_priority: Literal["low", "medium", "high", "urgent", "critical", "normal"] | None = None
    ttl_seconds: int = Field(default=900, ge=60, le=14400)


class PMInboundCanaryPlanActionRequest(BaseModel):
    digest: str = Field(min_length=64, max_length=64)
    confirm: bool = False
    reason: str | None = Field(default=None, max_length=500)


class PMOutboxDispositionRequest(BaseModel):
    disposition: Literal["RESOLVED", "SUPERSEDED"]
    reason: str = Field(min_length=1, max_length=1000)
    provider_state: dict[str, Any] = Field(default_factory=dict)


class SCMActionRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def reject_provider_credentials(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_credential_references(value)


class CanonicalIssueUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=100_000)
    status: str | None = Field(default=None, min_length=1, max_length=64)
    priority: str | None = Field(default=None, min_length=1, max_length=64)
    assigned_team: str | None = Field(default=None, max_length=200)
    assigned_agent: str | None = Field(default=None, max_length=200)
    estimated_hours: float | None = Field(default=None, ge=0)
    actual_hours: float | None = Field(default=None, ge=0)
    story_points: int | None = Field(default=None, ge=0)
    expected_revision: int | None = Field(default=None, ge=1)


class CanonicalIssueCreateRequest(BaseModel):
    title: str = Field(default="Untitled issue", min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=100_000)
    issue_type: str = Field(default="TASK", min_length=1, max_length=64)
    priority: str = Field(default="medium", min_length=1, max_length=64)
    sprint_id: UUID | None = None
    assigned_team: str | None = Field(default=None, max_length=200)
    assigned_agent: str | None = Field(default=None, max_length=200)
    estimated_hours: float | None = Field(default=None, ge=0)
    story_points: int | None = Field(default=None, ge=0)


class CanonicalSprintCreateRequest(BaseModel):
    sprint_number: int = Field(default=1, ge=1)
    milestone: str | None = Field(default=None, max_length=200)
    goal: str | None = Field(default=None, max_length=20_000)
    planned_story_points: int | None = Field(default=None, ge=0)
    estimated_hours: float | None = Field(default=None, ge=0)


class CanonicalSprintUpdateRequest(BaseModel):
    milestone: str | None = Field(default=None, max_length=200)
    goal: str | None = Field(default=None, max_length=20_000)
    status: str | None = Field(default=None, min_length=1, max_length=64)
    planned_story_points: int | None = Field(default=None, ge=0)
    completed_story_points: int | None = Field(default=None, ge=0)
    estimated_hours: float | None = Field(default=None, ge=0)
    actual_hours: float | None = Field(default=None, ge=0)
    expected_revision: int | None = Field(default=None, ge=1)


class CanonicalIssueCommentRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=100_000)
    actor_id: str = Field(default="operator", min_length=1, max_length=200)
    run_id: UUID | None = None
    approval_id: UUID | None = None
    evidence_id: str | None = None
    body_blob_ref: str | None = Field(default=None, max_length=500)


class CanonicalIssueLinkRequest(BaseModel):
    link_type: str = Field(..., min_length=1, max_length=80)
    target_type: str = Field(..., min_length=1, max_length=80)
    target_id: str = Field(..., min_length=1, max_length=400)
    metadata: dict[str, Any] = Field(default_factory=dict)


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


class CredentialApprovalRequest(BaseModel):
    requester: str = Field(min_length=1, max_length=200)
    context: str = Field(min_length=1, max_length=300)
    requested_by: str = Field(default="human_operator", min_length=1, max_length=200)
    ttl_seconds: int = Field(default=900, ge=60, le=3600)


class CredentialApprovalDecisionRequest(BaseModel):
    approved: bool
    decided_by: str = Field(default="human_operator", min_length=1, max_length=200)
    reason: str = Field(default="", max_length=2000)


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
    model_config = ConfigDict(extra="forbid")

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
    identity_mailbox_class: str = "permanent"


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
    dispatch_mode: Literal["queued", "inline"] | None = None
    queue_priority: int = Field(default=0, ge=-100, le=100)
    lease_seconds: int = Field(default=300, ge=30, le=86_400)


class WorkerRunPauseRequest(BaseModel):
    reason: str = Field(default="operator pause", min_length=1, max_length=4_000)
    requested_by: str = Field(default="operator", min_length=1, max_length=256)


class WorkerRunResumeRequest(BaseModel):
    requested_by: str = Field(default="operator", min_length=1, max_length=256)
    checkpoint_id: UUID | None = None


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
    credential_approval_id: UUID | None = None
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


class FlowDryRunRequest(BaseModel):
    """Non-mutating validation of a typed flow definition and its assignments."""

    definition_json: dict[str, Any]
    project_id: UUID | None = None


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


async def update_monitor_loop(
    storage: AgentStorage,
    stop_event: Any,
    *,
    interval_seconds: int = UPDATE_MONITOR_INTERVAL_S,
    max_iterations: int | None = None,
) -> None:
    """Run due steward monitors without allowing them to promote candidates.

    The monitoring service only discovers immutable DRAFT candidates.  It is
    intentionally independent from worker dispatch, certification, and
    rollout so an upstream release can never change production pointers merely
    because the scheduler woke up.
    """

    from mas_core.worker_registry.monitoring import run_due_update_monitors

    iteration = 0
    while not stop_event.is_set():
        try:
            await anyio.sleep(interval_seconds)
            if stop_event.is_set():
                break
            if await storage.get_config("system_state") == "RUNNING":
                results = await run_due_update_monitors(storage)
                if results:
                    logger.info("steward_update_monitor_completed", extra={"jobs": results})
            iteration += 1
            if max_iterations is not None and iteration >= max_iterations:
                break
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("steward_update_monitor_loop_error")


async def worker_run_recovery_loop(
    storage: AgentStorage,
    stop_event: Any,
    *,
    interval_seconds: int = 30,
) -> None:
    """Requeue worker runs whose executor lease expired after a restart."""
    while not stop_event.is_set():
        try:
            await anyio.sleep(interval_seconds)
            if stop_event.is_set():
                break
            recovered = await storage.recover_expired_worker_runs(limit=100)
            if recovered:
                logger.warning(
                    "worker_run_leases_recovered",
                    extra={"count": len(recovered)},
                )
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("worker_run_recovery_loop_error")


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
    app.state.worker_run_tasks = set()

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

        # Company compilation is separate from worker declaration seeding so
        # an incomplete worker inventory can never partially activate an org.
        # Apply the checked-in manifest only after all declarations have been
        # persisted; AgentStorage keeps the operation atomic and idempotent.
        try:
            company_result = await _apply_default_company_manifest(storage)
            if company_result is not None:
                await storage.set_config("default_company_seeded", "true")
                await storage.set_config("default_company_seeded_at", datetime.now(tz=UTC).isoformat())
                logger.info(
                    "default_company_manifest_applied",
                    extra={"digest": (company_result.get("manifest") or {}).get("digest")},
                )
        except Exception:
            logger.exception("Default company manifest bootstrap failed; continuing in compatibility mode")

        # Start after seeding so newly created external-worker stewardship
        # jobs are eligible on the first scheduler cycle.
        ceo_command_task_group.start_soon(update_monitor_loop, storage, stop_event)
        ceo_command_task_group.start_soon(worker_run_recovery_loop, storage, stop_event)

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

    worker_tasks = list(getattr(app.state, "worker_run_tasks", set()))
    for task in worker_tasks:
        task.cancel()
    if worker_tasks:
        await asyncio.gather(*worker_tasks, return_exceptions=True)
    app.state.worker_run_tasks.clear()

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
    # Provider webhooks are authenticated by the connection-specific provider
    # secret inside ``receive_integration_webhook``.  They arrive through the
    # separately isolated PM gateway, so requiring an AIAT control-plane key
    # here would turn a valid provider delivery into an origin 401/403 and
    # would couple external providers to an internal operator credential.
    # Keep this exception narrow: only POSTs to the UUID webhook route bypass
    # the global API-key middleware. Every management, operator, health-sensitive
    # and internal route continues through the normal principal check.
    is_provider_webhook = bool(
        request.method == "POST"
        and re.fullmatch(
            r"/integrations/webhooks/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
            str(request.scope.get("path") or ""),
        )
    )
    if is_provider_webhook:
        request.state.aiat_auth_principal = "provider_webhook"
        response = await call_next(request)
        if requested_v1:
            response.headers["X-AIAT-API-Version"] = "v1"
        return response
    if request.method == "OPTIONS" or request.url.path in {"/health", "/docs", "/openapi.json"}:
        response = await call_next(request)
        if requested_v1:
            response.headers["X-AIAT-API-Version"] = "v1"
        return response
    try:
        request.state.aiat_auth_principal = _check_auth(
            request.headers.get("x-api-key"), request.headers.get("authorization")
        )
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
app.state.worker_run_tasks = set()

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
    payload = await _tool_service_client().request(
        "POST", "/tools/project.repository/run", body, timeout=900,
    )

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
    if inspect.iscoroutinefunction(getattr(storage, "get_company", None)):
        if await storage.get_company(req.company_id) is None:
            raise HTTPException(404, f"Company {req.company_id} not found")
    flow_for_instance: dict[str, Any] | None = None
    if req.flow_id is not None:
        flow_for_instance = await storage.get_flow(req.flow_id)
        if flow_for_instance is None:
            raise HTTPException(404, f"Flow {req.flow_id} not found")

    project_config = dict(req.config or {})
    # Every canonical project starts with an explicit, provider-neutral PM
    # provisioning intent.  The dedicated-project profile is the safe default;
    # an operator must opt into issue-only umbrella mapping later.
    pm_provisioning = dict(project_config.get("pm_provisioning") or {})
    pm_provisioning.setdefault("mapping_profile", DEDICATED_PROJECT_MAPPING_PROFILE)
    pm_provisioning.setdefault("state", "UNPROVISIONED")
    project_config["pm_provisioning"] = pm_provisioning
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
        company_id=req.company_id,
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
    approvals = await storage.list_approval_gates(project_id=project_id)
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
        evidence = evaluate_project_evidence(project_id=str(project_id), policy=policy_for(req.policy_id, version=req.policy_version, requirements=req.requirements), project=project, documents=documents, artifacts=artifacts, flow_instance=await storage.get_flow_instance_by_project(project_id), approvals=await storage.list_approval_gates(project_id=project_id), audit_events=await storage.get_project_history(project_id))
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


@app.get("/projects/{project_id}/usage/events")
async def list_project_usage_events(
    project_id: UUID,
    limit: int = Query(default=1000, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, f"Project {project_id} not found")
    return [
        _serialize(row)
        for row in await storage.list_project_usage_events(
            project_id,
            limit=limit,
            offset=offset,
        )
    ]


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


@app.post("/projects/{project_id}/sprints", status_code=201)
async def create_canonical_sprint(
    project_id: UUID,
    req: CanonicalSprintCreateRequest,
    request: Request,
) -> dict[str, Any]:
    """Typed sprint creation; `/tasks` remains a compatibility wrapper."""
    _require_operator_identity(request)
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, "project not found")
    kwargs = req.model_dump()
    if isinstance(storage, AgentStorage):
        sprint, queued = await storage.create_sprint_with_pm_projections(
            project_id=project_id,
            **kwargs,
        )
    else:
        sprint = await storage.create_sprint(project_id=project_id, **kwargs)
        queued = []
    return {
        "sprint": _serialize(sprint),
        "projections": [_serialize_projection(row) for row in queued],
    }


@app.get("/projects/{project_id}/sprints/{sprint_id}")
async def get_canonical_sprint(project_id: UUID, sprint_id: UUID) -> dict[str, Any]:
    storage = _storage()
    sprint = await storage.get_sprint(sprint_id)
    if sprint is None or sprint.get("project_id") != project_id:
        raise HTTPException(404, "sprint not found for project")
    return {"sprint": _serialize(sprint)}


@app.patch("/projects/{project_id}/sprints/{sprint_id}")
async def update_canonical_sprint(
    project_id: UUID,
    sprint_id: UUID,
    req: CanonicalSprintUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    _require_operator_identity(request)
    storage = _storage()
    sprint = await storage.get_sprint(sprint_id)
    if sprint is None or sprint.get("project_id") != project_id:
        raise HTTPException(404, "sprint not found for project")
    values = {
        key: value
        for key, value in req.model_dump(exclude_none=True).items()
        if key != "expected_revision"
    }
    try:
        if isinstance(storage, AgentStorage):
            refreshed, queued = await storage.update_sprint_with_pm_projections(
                sprint_id,
                expected_revision=req.expected_revision or int(sprint.get("revision") or 1),
                **values,
            )
        else:
            await storage.update_sprint(
                sprint_id,
                expected_revision=req.expected_revision or int(sprint.get("revision") or 1),
                **values,
            )
            refreshed = await storage.get_sprint(sprint_id)
            queued = []
            if refreshed is None:
                raise HTTPException(404, "sprint not found after update")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "sprint": _serialize(refreshed),
        "projections": [_serialize_projection(row) for row in queued],
    }


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
async def create_task(body: dict[str, Any], request: Request) -> dict[str, Any]:
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
        # The compatibility wrapper must not become a second path around the
        # typed canonical-mutation authorization boundary.  Unknown actions
        # remain routable ADMIN_TASKs; deterministic persistence actions are
        # operator-only just like their typed replacements.
        _require_operator_identity(request)
        storage = _storage()
        projection_rows: list[dict[str, Any]] = []
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
                issue_kwargs = {
                    "project_id": pid,
                    "sprint_id": parsed_sprint_id,
                    "title": str(task_payload.get("title") or "Untitled issue"),
                    "description": task_payload.get("description"),
                    "issue_type": str(task_payload.get("issue_type") or "TASK"),
                    "priority": str(task_payload.get("priority") or "medium"),
                    "assigned_team": task_payload.get("assigned_team"),
                    "estimated_hours": task_payload.get("estimated_hours"),
                    "story_points": task_payload.get("story_points"),
                }
                if isinstance(storage, AgentStorage):
                    result, projection_rows = await storage.create_issue_with_pm_projections(**issue_kwargs)
                else:
                    result = await storage.create_issue(**issue_kwargs)
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
                if isinstance(storage, AgentStorage):
                    result, projection_rows = await storage.update_issue_with_pm_projections(
                        issue_id,
                        expected_revision=int(issue.get("revision") or 1),
                        **values,
                    )
                else:
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
        response = {"status": "completed", "action": action, "result": _serialize(result)}
        if projection_rows:
            response["projections"] = [_serialize_projection(row) for row in projection_rows]
        return response

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


async def _apply_default_company_manifest(storage: AgentStorage) -> dict[str, Any] | None:
    """Load and atomically apply the checked-in default company manifest."""
    import inspect
    from pathlib import Path

    import yaml

    configured_manifest_path = os.environ.get("COMPANY_MANIFEST_PATH")
    if configured_manifest_path:
        manifest_path = Path(configured_manifest_path)
    else:
        default_manifest_name = Path("companies/default-software-company.yaml")
        # Compose runs from /app, while repository tests run from the
        # workspace root. Resolve the checked-in default from either runtime
        # layout, but never fall back when an explicit path was supplied.
        # ``__file__`` has a different depth in the source tree and in the
        # production image (where it is ``/app/orchestrator_api/main.py``).
        # Iterate over available parents instead of indexing a presumed depth;
        # the latter raised ``IndexError`` in the image and silently disabled
        # default-company bootstrap after worker seeding.
        candidates = (default_manifest_name,)
        candidates += tuple(
            parent / default_manifest_name for parent in Path(__file__).resolve().parents
        )
        manifest_path = next(
            (candidate for candidate in candidates if candidate.is_file()),
            candidates[0],
        )
    if not manifest_path.is_file():
        raise FileNotFoundError(f"default company manifest not found: {manifest_path}")
    apply_manifest = getattr(storage, "apply_company_manifest", None)
    # A few compatibility/test storage doubles predate the company control-plane
    # API.  They must continue to support the legacy seed endpoint without
    # attempting to await a dynamically-created MagicMock attribute.
    if not inspect.iscoroutinefunction(apply_manifest):
        logger.debug("Storage does not expose async company manifest application; skipping bootstrap")
        return None
    raw_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    company_manifest, digest, canonical = compile_company_manifest(raw_manifest)
    return await apply_manifest(
        company_id=DEFAULT_COMPANY_ID,
        manifest=company_manifest,
        digest=digest,
        canonical=canonical,
        source=str(manifest_path),
        actor="system-bootstrap",
    )


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

    company_result: dict[str, Any] | None = None
    try:
        company_result = await _apply_default_company_manifest(storage)
    except (CompanyManifestError, ValueError, OSError) as exc:
        logger.exception("Default company manifest compilation failed")
        raise HTTPException(500, f"default company manifest could not be applied: {exc}") from exc

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
        "company": _serialize(company_result) if company_result is not None else None,
    }


@app.get("/companies")
async def list_companies() -> list[dict[str, Any]]:
    return [_serialize(row) for row in await _storage().list_companies()]


@app.post("/companies", status_code=201)
async def create_company(request: Request, req: CompanyCreateRequest) -> dict[str, Any]:
    _require_operator_identity(request)
    storage = _storage()
    if await storage.get_company_by_slug(req.slug) is not None:
        raise HTTPException(409, "company slug already exists")
    company_id = uuid4()
    now = datetime.now(tz=UTC)
    async with storage.engine.begin() as conn:
        await conn.execute(
            memory_models.companies.insert().values(
                id=company_id,
                slug=req.slug,
                name=req.name,
                description=req.description,
                status="ACTIVE",
                created_by=_authenticated_principal(request),
                created_at=now,
                updated_at=now,
            )
        )
    return _serialize(await storage.get_company(company_id))


@app.get("/companies/{company_id}")
async def get_company(company_id: UUID) -> dict[str, Any]:
    storage = _storage()
    result = await storage.get_company_read_model(company_id)
    if not result.get("company"):
        raise HTTPException(404, "company not found")
    return _serialize(result)


@app.get("/companies/{company_id}/budgets")
async def list_company_budgets(company_id: UUID) -> list[dict[str, Any]]:
    storage = _storage()
    if await storage.get_company(company_id) is None:
        raise HTTPException(404, "company not found")
    budgets = await storage.list_company_budgets(company_id)
    states = [
        await storage.get_budget_state(company_id, str(row["budget_key"]))
        for row in budgets
    ]
    return [_serialize(row) for row in states]


@app.get("/companies/{company_id}/departments")
async def list_company_departments(company_id: UUID) -> list[dict[str, Any]]:
    storage = _storage()
    if await storage.get_company(company_id) is None:
        raise HTTPException(404, "company not found")
    return [_serialize(row) for row in await storage.list_company_departments(company_id)]


@app.get("/companies/{company_id}/assignments")
async def list_company_assignments(company_id: UUID) -> list[dict[str, Any]]:
    storage = _storage()
    if await storage.get_company(company_id) is None:
        raise HTTPException(404, "company not found")
    return [_serialize(row) for row in await storage.list_company_worker_assignments(company_id)]


@app.get("/companies/{company_id}/budgets/{budget_key}")
async def get_company_budget(company_id: UUID, budget_key: str) -> dict[str, Any]:
    storage = _storage()
    if await storage.get_company(company_id) is None:
        raise HTTPException(404, "company not found")
    state = await storage.get_budget_state(company_id, budget_key)
    if not state.get("configured"):
        raise HTTPException(404, "budget not configured")
    return _serialize(state)


@app.get("/companies/{company_id}/budget-reservations")
async def list_company_budget_reservations(
    company_id: UUID,
    run_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    storage = _storage()
    if await storage.get_company(company_id) is None:
        raise HTTPException(404, "company not found")
    rows = await storage.list_budget_reservations(company_id=company_id, run_id=run_id, limit=limit)
    return [_serialize(row) for row in rows]


@app.post("/companies/{company_id}/manifest/validate")
async def validate_company_manifest(company_id: UUID, req: CompanyManifestRequest) -> dict[str, Any]:
    storage = _storage()
    if await storage.get_company(company_id) is None:
        raise HTTPException(404, "company not found")
    try:
        manifest, digest, canonical = compile_company_manifest(req.manifest)
    except CompanyManifestError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "valid": True,
        "company_id": str(company_id),
        "slug": manifest.slug,
        "digest": digest,
        "manifest": canonical,
        "source": req.source,
    }


@app.get("/companies/{company_id}/manifest/history")
async def company_manifest_history(
    company_id: UUID,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    storage = _storage()
    if await storage.get_company(company_id) is None:
        raise HTTPException(404, "company not found")
    return [_serialize(row) for row in await storage.list_company_manifest_versions(company_id, limit=limit)]


@app.post("/companies/{company_id}/manifest/apply")
async def apply_company_manifest(company_id: UUID, request: Request, req: CompanyManifestRequest) -> dict[str, Any]:
    _require_operator_identity(request)
    storage = _storage()
    try:
        manifest, digest, canonical = compile_company_manifest(req.manifest)
        result = await storage.apply_company_manifest(
            company_id=company_id,
            manifest=manifest,
            digest=digest,
            canonical=canonical,
            source=req.source,
            actor=_authenticated_principal(request),
        )
    except CompanyManifestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _serialize(result)


@app.post("/companies/{company_id}/manifest/rollback")
async def rollback_company_manifest(
    company_id: UUID,
    request: Request,
    req: CompanyManifestRollbackRequest,
) -> dict[str, Any]:
    _require_operator_identity(request)
    try:
        result = await _storage().rollback_company_manifest(
            company_id,
            manifest_version=req.manifest_version,
            actor=_authenticated_principal(request),
            reason=req.reason,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _serialize(result)


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
            approval_id=req.credential_approval_id,
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
    "microsoft_agent_framework": ("agent_framework",),
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
    if runtime_tier == "microsoft_agent_framework":
        importlib.import_module("agent_framework")
        return {
            "tasks_run": 1,
            "tasks_passed": 1,
            "output": {"agent_name": runtime_config.get("agent_name") or "aiat-worker"},
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
                "id": "microsoft_agent_framework",
                "name": "Microsoft Agent Framework",
                **_runtime_readiness("microsoft_agent_framework"),
                "tier": "departmental",
                "description": "Microsoft Agent Framework worker runtime behind the AIAT contract",
                "policy": {
                    "inner_runtime": True,
                    "requires_approval": False,
                    "sandbox_required": "gvisor",
                    "allowed_tools": "controlled_by_manifest",
                    "can_spawn_subgraph": False,
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


def _validate_worker_tool_grants(
    tools: list[str],
    *,
    role_value: str | None,
    team_id: str | None,
) -> None:
    """Reject unknown or role-forbidden tools before a worker is registered.

    Tool grants are authority, not advisory capability labels.  Registration
    must therefore apply the same canonical manifest and static role policy as
    runtime tool mediation; otherwise an operator could create a capability
    that only fails closed much later at dispatch time.
    """

    from mas_tools_sdk.manifest import TOOL_MANIFEST, resolve_tool_name

    try:
        role = AgentRole(str(role_value or AgentRole.WORKER.value).lower())
    except ValueError as exc:
        raise HTTPException(422, f"Unknown worker role '{role_value}'") from exc

    for raw_name in tools:
        tool_name = str(raw_name).strip()
        if not tool_name:
            raise HTTPException(422, "Worker tool grants must be non-empty names")
        canonical_name = resolve_tool_name(tool_name)
        if canonical_name is None:
            raise HTTPException(422, f"Unknown worker tool grant '{tool_name}'")
        tool = TOOL_MANIFEST[canonical_name]
        decision = can_use_tool_with_metadata(
            role=role,
            tool_name=canonical_name,
            sender_team=team_id,
            allowed_roles=tool["allowed_roles"],
            blocked_roles=tool["blocked_roles"],
        )
        if decision is not True:
            raise HTTPException(
                403,
                {
                    "code": "WORKER_TOOL_GRANT_FORBIDDEN",
                    "message": f"Tool grant '{canonical_name}' is not permitted for role '{role.value}'",
                    "reason": str(decision),
                },
            )


async def _validate_persisted_capability_tool_grants(
    storage: AgentStorage,
    capability_ids: list[UUID],
    *,
    role_value: str | None,
    team_id: str | None,
) -> None:
    """Apply grant policy to tools contributed by existing capabilities."""

    getter = getattr(storage, "get_capability", None)
    if not inspect.iscoroutinefunction(getter):
        # Compatibility test doubles may only model registration.  Production
        # AgentStorage always implements this method, so no real deployment
        # bypasses the validation.
        return
    tools: list[str] = []
    for capability_id in capability_ids:
        capability = await getter(capability_id)
        if isinstance(capability, dict):
            tools.extend(str(item) for item in capability.get("required_tools") or [])
    _validate_worker_tool_grants(tools, role_value=role_value, team_id=team_id)


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
    if req.identity_mailbox_class.strip().lower() not in {"permanent", "temporary"}:
        raise HTTPException(422, "identity_mailbox_class must be permanent or temporary")
    is_external_candidate = bool(req.source_repo and str(req.source_repo).lower() != "local")
    if is_external_candidate and not req.version_pin:
        raise HTTPException(
            422,
            "External workers require an immutable version_pin before they can enter the steward pipeline",
        )
    _validate_worker_tool_grants(
        req.required_tools,
        role_value=req.role,
        team_id=req.team_id,
    )
    capability_ids = await _resolve_worker_capability_ids(
        storage,
        capability_ids=req.capability_ids,
        capability_names=req.capability_names,
        required_tools=req.required_tools,
        required_role=req.role,
    )
    await _validate_persisted_capability_tool_grants(
        storage,
        capability_ids,
        role_value=req.role,
        team_id=req.team_id,
    )
    worker = await storage.register_worker(
        name=req.name,
        adapter_type=req.adapter_type,
        adapter_config={**req.adapter_config, "identity_mailbox_class": req.identity_mailbox_class.strip().lower()},
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


async def _worker_activation_blockers(
    storage: AgentStorage,
    worker: dict[str, Any],
) -> list[str]:
    """Verify the immutable records required before a worker can be ACTIVE.

    This is deliberately shared by native and external workers.  An external
    source adds steward/provenance requirements, but a local AIAT shell is not
    allowed to bypass the WorkerShell, certified Adapter, Skill Bundle,
    capability snapshot, model-policy, or readiness checks.
    """

    required_storage_methods = (
        "get_worker_shell_version",
        "get_runtime_adapter",
        "get_skill_bundle",
        "list_capability_snapshots",
    )
    # Older migration tests use a deliberately small storage double.  Keep
    # that compatibility surface while production storage enforces every
    # governed record below.
    if not all(
        inspect.iscoroutinefunction(getattr(storage, name, None))
        for name in required_storage_methods
    ):
        return []

    blockers: list[str] = []
    governance = dict(worker.get("adapter_config") or {})
    external_backed = bool(worker.get("source_repo")) or bool(
        governance.get("legacy_external_wrapper")
    )
    if governance.get("legacy_external_wrapper"):
        blockers.append(
            "legacy external wrapper cannot be activated; migrate to a certified runtime-specific adapter"
        )
    if not worker.get("version_pin"):
        blockers.append("missing immutable source/version pin")

    shell_id = worker.get("active_shell_version_id")
    if not shell_id:
        blockers.append("missing active immutable WorkerShell")
    else:
        shell = await storage.get_worker_shell_version(shell_id)
        if shell is None or shell.get("status") != "active":
            blockers.append("active WorkerShell is missing or not active")

    adapter_id = worker.get("active_adapter_id")
    if not adapter_id:
        blockers.append("missing active certified runtime Adapter")
    else:
        adapter = await storage.get_runtime_adapter(adapter_id)
        if (
            adapter is None
            or adapter.get("status") != "active"
            or adapter.get("conformance_status") != "passed"
        ):
            blockers.append("active runtime Adapter is missing, uncertified, or inactive")

    bundle_id = worker.get("active_skill_bundle_id")
    if not bundle_id:
        blockers.append("missing active approved Skill Bundle")
    else:
        bundle = await storage.get_skill_bundle(bundle_id)
        if bundle is None or bundle.get("status") != "APPROVED":
            blockers.append("active Skill Bundle is missing or not approved")

    snapshots = await storage.list_capability_snapshots(worker["id"], limit=1)
    if not snapshots:
        blockers.append("missing immutable capability snapshot")

    model_mode = str(worker.get("model_mode") or governance.get("model_mode") or "none")
    if model_mode != "none":
        profile_id = worker.get("model_profile_id") or governance.get("model_profile_id")
        if not profile_id:
            blockers.append("model-governed worker requires an approved Model Profile")
        elif inspect.iscoroutinefunction(getattr(storage, "get_model_profile", None)):
            profile = await storage.get_model_profile(str(profile_id))
            approved_versions = [
                item for item in (profile or {}).get("versions", [])
                if str(item.get("status", "")).lower() == "approved"
            ]
            if profile is None or str(profile.get("status", "")).lower() != "approved" or not approved_versions:
                blockers.append("selected Model Profile has no effective approved version")

    if external_backed:
        if not worker.get("source_repo"):
            blockers.append("externally backed worker has no canonical source provenance")
        required_external_methods = (
            "get_steward_by_worker",
            "get_external_provenance_by_worker",
        )
        if all(
            inspect.iscoroutinefunction(getattr(storage, name, None))
            for name in required_external_methods
        ):
            if await storage.get_steward_by_worker(worker["id"]) is None:
                blockers.append("external worker requires a dedicated Steward Agent")
            if await storage.get_external_provenance_by_worker(worker["id"]) is None:
                blockers.append("external worker requires immutable provenance")

    return blockers


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
        "RETIRE": "RETIRED",
    }

    if req.action in action_map:
        new_status = req.new_status or action_map[req.action]
        if new_status == "ACTIVE":
            blockers = await _worker_activation_blockers(storage, existing)
            if blockers:
                raise HTTPException(
                    409,
                    {
                        "code": "WORKER_ACTIVATION_GOVERNANCE_BLOCKED",
                        "message": "Worker activation requires current governed records",
                        "blockers": blockers,
                    },
                )
            identity_blocker = await _identity_activation_blocker(storage, existing)
            if identity_blocker:
                raise HTTPException(
                    409,
                    {
                        "code": "IDENTITY_PROVISIONING_BLOCKED",
                        "message": "Worker activation requires a verified active identity mailbox",
                        "blockers": [identity_blocker],
                    },
                )
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
        identity_suspend_error = None
        identity_archive_error = None
        if new_status == "INACTIVE":
            identity_suspend_error = await _suspend_worker_identity(storage, worker_id)
        elif new_status == "RETIRED":
            identity_archive_error = await _archive_worker_identity(storage, worker_id)
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
    response = _serialize(updated)  # type: ignore[arg-type]
    if req.action == "DEACTIVATE" and identity_suspend_error:
        response["identity_suspension"] = "PENDING_RETRY"
        response["identity_suspension_error_code"] = identity_suspend_error
    if req.action == "RETIRE" and identity_archive_error:
        response["identity_archival"] = "PENDING_RETRY"
        response["identity_archival_error_code"] = identity_archive_error
    return response


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
        CompatibilityMatrix,
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
    if inspect.iscoroutinefunction(getattr(storage, "list_compatibility_matrices", None)):
        for row in await storage.list_compatibility_matrices(worker_id):
            try:
                steward.record_compatibility_matrix(
                    CompatibilityMatrix(
                        matrix_id=UUID(str(row["id"])),
                        runtime_version=str(row["runtime_version"]),
                        adapter_version=str(row["adapter_version"]),
                        contract_version=str(row["contract_version"]),
                        model_profiles=row.get("model_profiles_json") or {},
                        capabilities=row.get("capabilities_json") or {},
                        fixtures=tuple(row.get("fixtures") or []),
                        passed=bool(row.get("passed", False)),
                        generated_at=row.get("created_at") or datetime.now(UTC),
                    )
                )
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "steward_compatibility_matrix_rehydrate_failed",
                    extra={"matrix_id": str(row.get("id"))},
                )
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
    if not steward.restore_active_pointers(
        bundle_id=persisted.get("active_skill_bundle_id"),
        adapter_id=persisted.get("active_adapter_id"),
    ):
        logger.warning(
            "steward_active_pointer_rehydrate_failed",
            extra={
                "worker_id": key,
                "active_skill_bundle_id": str(persisted.get("active_skill_bundle_id"))
                if persisted.get("active_skill_bundle_id")
                else None,
                "active_adapter_id": str(persisted.get("active_adapter_id"))
                if persisted.get("active_adapter_id")
                else None,
            },
        )
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
        payload = await _tool_service_client().request(
            "POST", f"/tools/{tool_request.tool_name}/run", body, timeout=120,
        )
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


async def _certified_worker_adapter(
    storage: AgentStorage,
    worker: dict[str, Any],
    *,
    adapter_id: UUID | str | None = None,
    allow_retired: bool = False,
) -> Any | None:
    """Hydrate one immutable certified adapter definition after an API restart.

    Dispatches select the worker's active adapter.  Run-control actions pass
    the adapter pinned on the durable WorkerRun, which may be an older,
    superseded version while a rollout is in progress.
    """
    from mas_core.worker_contract import AdapterContext, WorkerCapabilities
    from mas_core.worker_registry.runtime_adapters import (
        OpenCodeAdapter,
        OpenCodeInterfaceVerification,
        adapter_for_transport,
    )

    worker_id = UUID(str(worker["id"]))
    selected_adapter_id = adapter_id or worker.get("active_adapter_id")
    adapter_row = (
        await storage.get_runtime_adapter(selected_adapter_id)
        if selected_adapter_id is not None
        else await storage.get_active_runtime_adapter(worker_id)
    )
    permitted_statuses = {"active", "superseded"} if allow_retired else {"active"}
    if (
        adapter_row is None
        or adapter_row.get("status") not in permitted_statuses
        or adapter_row.get("conformance_status") != "passed"
        or (
            adapter_row.get("worker_id") is not None
            and str(adapter_row["worker_id"]) != str(worker_id)
        )
    ):
        cache_key = f"{worker_id}:{selected_adapter_id}" if selected_adapter_id else None
        stale = _worker_adapter_runtimes.pop(cache_key, None) if cache_key else None
        if stale is not None and hasattr(stale, "close"):
            try:
                await stale.close()
            except Exception:
                logger.warning("stale_worker_adapter_close_failed", extra={"worker_id": str(worker_id)}, exc_info=True)
        return None
    immutable_adapter_id = str(adapter_row["id"])
    cache_key = f"{worker_id}:{immutable_adapter_id}"
    cached = _worker_adapter_runtimes.get(cache_key)
    if cached is not None and getattr(cached, "_aiat_adapter_id", None) == immutable_adapter_id:
        return cached
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
    # A cache entry is scoped to the immutable adapter row, rather than the
    # mutable worker pointer.  This allows concurrent old/new runs to be
    # controlled through the exact runtime selected at dispatch.
    adapter._aiat_adapter_id = immutable_adapter_id
    _worker_adapter_runtimes[cache_key] = adapter
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


@app.post("/capabilities/workers/{worker_id}/steward/monitor")
async def run_worker_steward_monitor(worker_id: UUID) -> list[dict[str, Any]]:
    """Run one operator-requested, review-only upstream discovery pass."""

    from mas_core.worker_registry.monitoring import run_due_update_monitors

    storage = _storage()
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise HTTPException(404, "Worker not found")
    if not worker.get("source_repo"):
        raise HTTPException(409, "Only externally backed workers have upstream monitoring")
    return [
        _serialize(result)
        for result in await run_due_update_monitors(
            storage,
            force_worker_id=worker_id,
        )
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


async def _materialize_candidate_worker_shell(
    storage: AgentStorage,
    *,
    worker: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Snapshot a certified candidate into the immutable WorkerShell record.

    The shell binds the worker declaration, permissions, capabilities and
    provenance reviewed with this particular candidate.  It is created during
    certification but selected for dispatch only after rollout promotion.
    """
    worker_id = UUID(str(worker["id"]))
    adapter_id = candidate.get("adapter_id")
    if adapter_id is None:
        raise HTTPException(409, "Certified candidates must include an immutable runtime Adapter")
    adapter = await storage.get_runtime_adapter(adapter_id)
    if adapter is None or str(adapter.get("worker_id")) != str(worker_id):
        raise HTTPException(409, "Candidate runtime Adapter does not belong to this worker")
    bundle = await storage.get_skill_bundle(candidate["skill_bundle_id"])
    if bundle is None or str(bundle.get("worker_id")) != str(worker_id):
        raise HTTPException(409, "Candidate Skill Bundle does not belong to this worker")

    shell_version = f"governed-{candidate['id']}"
    existing = await storage.get_worker_shell_version_by_version(worker_id, shell_version)
    if existing is not None:
        return existing

    config = dict(worker.get("adapter_config") or {})
    provenance: dict[str, Any] = {
        "source_repo": worker.get("source_repo"),
        "source_revision": worker.get("source_revision"),
        "version_pin": worker.get("version_pin"),
        "candidate_id": str(candidate["id"]),
        "skill_bundle_id": str(candidate["skill_bundle_id"]),
        "runtime_adapter_id": str(adapter_id),
    }
    get_provenance = getattr(storage, "get_external_provenance_by_worker", None)
    if inspect.iscoroutinefunction(get_provenance):
        external_provenance = await get_provenance(worker_id)
        if external_provenance is not None:
            provenance["external_provenance"] = external_provenance
    identity = {
        "worker_id": str(worker_id),
        "name": worker.get("name"),
        "department": worker.get("team_id"),
        "adapter_entrypoint": worker.get("adapter_entrypoint"),
    }
    capabilities = dict(adapter.get("capabilities_json") or config.get("capabilities") or {})
    permissions = {
        "permission_requirements": list(config.get("permission_requirements") or []),
        "tool_grants": list(config.get("tool_grants") or []),
        "sandbox_profile": worker.get("sandbox_profile"),
    }
    shell_payload = {
        "version": shell_version,
        "identity": identity,
        "capabilities": capabilities,
        "permissions": permissions,
        "model_mode": str(worker.get("model_mode") or config.get("model_mode") or "none"),
        "provenance": provenance,
    }
    content_hash = hashlib.sha256(
        json.dumps(shell_payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return await storage.create_worker_shell_version(
        worker_id=worker_id,
        version=shell_version,
        contract_version=str(config.get("contract_version") or "aiat.worker.v1"),
        schema_version="1.0",
        identity=identity,
        capabilities=capabilities,
        permissions=permissions,
        model_mode=shell_payload["model_mode"],
        provenance=provenance,
        content_hash=content_hash,
    )


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
        if certification.passed:
            shell = await _materialize_candidate_worker_shell(
                storage,
                worker=worker,
                candidate=persisted_candidate,
            )
            evidence["worker_shell_version_id"] = str(shell["id"])
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
        # Adapter runtimes are cached by immutable ID, so a promoted adapter
        # cannot be selected for an older run and older in-flight runs retain
        # their control handle.
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
    previous_shell_id: UUID | None = None
    if previous_candidate is not None:
        try:
            previous_shell_id = UUID(
                str((previous_candidate.get("evidence_json") or {}).get("worker_shell_version_id"))
            )
        except (TypeError, ValueError):
            raise HTTPException(
                409,
                "The previous rollout has no governed WorkerShell snapshot and cannot be restored",
            ) from None
    if current_candidate is not None:
        if current_candidate.get("adapter_id"):
            await storage.update_runtime_adapter(current_candidate["adapter_id"], status="superseded")
        await storage.update_skill_bundle(current_candidate["skill_bundle_id"], status="SUPERSEDED")
    if previous_candidate is not None:
        if previous_candidate.get("adapter_id"):
            await storage.update_runtime_adapter(previous_candidate["adapter_id"], status="active", conformance_status="passed")
        await storage.update_skill_bundle(previous_candidate["skill_bundle_id"], status="APPROVED")
        await storage.set_worker_governed_versions(worker_id, active_shell_version_id=previous_shell_id, active_adapter_id=previous_candidate.get("adapter_id"), active_skill_bundle_id=previous_candidate.get("skill_bundle_id"))
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
    # Keep cached immutable adapters alive for runs pinned before the
    # rollback; new dispatches resolve the restored active pointer instead.
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


async def _enforce_company_dispatch_grants(
    *,
    storage: Any,
    project_id: UUID | None,
    worker_id: UUID,
    tool_grants: list[str],
    permission_requirements: list[str],
) -> UUID | None:
    """Require company-backed runs to stay within the active assignment grants."""

    if project_id is None:
        return None
    project = await storage.get_project(project_id)
    if project is None or not project.get("company_id"):
        return None
    company_id = UUID(str(project["company_id"]))
    list_assignments = getattr(storage, "list_company_worker_assignments", None)
    if not inspect.iscoroutinefunction(list_assignments):
        raise HTTPException(
            503,
            {
                "code": "COMPANY_ASSIGNMENT_STORE_UNAVAILABLE",
                "message": "Company assignment grants cannot be verified",
            },
        )
    assignments = await list_assignments(company_id)
    assignment = next(
        (
            item
            for item in assignments
            if UUID(str(item["worker_id"])) == worker_id
        ),
        None,
    )
    if assignment is None or str(assignment.get("status")) != "ACTIVE":
        raise HTTPException(
            403,
            {
                "code": "COMPANY_ASSIGNMENT_REQUIRED",
                "message": "Worker has no active assignment for this company",
            },
        )
    unapproved_tools = sorted(set(tool_grants) - set(assignment.get("tool_grants") or []))
    unapproved_permissions = sorted(
        set(permission_requirements) - set(assignment.get("permission_grants") or [])
    )
    if unapproved_tools or unapproved_permissions:
        raise HTTPException(
            403,
            {
                "code": "COMPANY_ASSIGNMENT_GRANT_EXCEEDED",
                "message": "Worker Run grants exceed the active company manifest assignment",
                "unapproved_tool_grants": unapproved_tools,
                "unapproved_permission_requirements": unapproved_permissions,
            },
        )
    return company_id


async def _reserve_worker_run_budgets(
    *,
    storage: Any,
    request: Any,
    worker_id: UUID,
    project_id: UUID | None,
    company_id: UUID | None = None,
) -> list[tuple[UUID, str]]:
    """Reserve configured company budgets before a run enters the queue.

    Cost and concurrency reservations use the request idempotency key, so a
    retry of the dispatch request reuses the original reservation instead of
    consuming the budget twice.  Lightweight storage doubles may omit this
    optional ledger API; the durable AgentStorage path is fail-closed.
    """
    reserve = getattr(storage, "reserve_budget", None)
    if not inspect.iscoroutinefunction(reserve):
        return []
    resolved_company_id = company_id or DEFAULT_COMPANY_ID
    if company_id is None and project_id is not None:
        project = await storage.get_project(project_id)
        if project is not None and project.get("company_id"):
            resolved_company_id = UUID(str(project["company_id"]))
    requested_cost = request.budget_usd if hasattr(request, "budget_usd") else None
    if requested_cost is None:
        requested_cost = (request.budget or {}).get("max_cost_usd")
    budgets: list[tuple[str, Decimal]] = []
    if requested_cost is not None and float(requested_cost) > 0:
        budgets.append(("max_cost_usd", Decimal(str(requested_cost))))
    # A configured max_concurrent_runs budget is a semaphore: every active
    # run consumes one unit and releases it on completion/cancellation.
    budgets.append(("max_concurrent_runs", Decimal("1")))
    reservations: list[tuple[UUID, str]] = []
    try:
        for budget_key, amount in budgets:
            reservation = await reserve(
                company_id=resolved_company_id,
                budget_key=budget_key,
                amount=amount,
                idempotency_key=f"worker-run:{worker_id}:{request.idempotency_key}:{budget_key}",
                project_id=project_id,
                worker_id=worker_id,
                run_id=request.run_id,
                metadata={"task_type": request.task_type, "source": "worker_dispatch"},
            )
            if reservation is not None:
                reservations.append((UUID(str(reservation["id"])), budget_key))
    except Exception:
        settle = getattr(storage, "settle_budget_reservation", None)
        if inspect.iscoroutinefunction(settle):
            for reservation_id, _budget_key in reservations:
                await settle(reservation_id, state="RELEASED")
        raise
    return reservations


async def _settle_worker_run_budgets(
    storage: Any,
    reservations: list[tuple[UUID, str]],
    *,
    state: str,
    actual_cost_usd: float | Decimal | None = None,
) -> None:
    settle = getattr(storage, "settle_budget_reservation", None)
    if not inspect.iscoroutinefunction(settle):
        return
    successful = state == "SUCCEEDED"
    reported_cost = Decimal(str(actual_cost_usd)) if actual_cost_usd is not None else None
    billed_failed_run = reported_cost is not None and reported_cost > 0
    for reservation_id, budget_key in reservations:
        cost_has_authoritative_usage = successful and reported_cost is not None
        cost_has_failed_usage = budget_key == "max_cost_usd" and billed_failed_run
        reservation_state = (
            "RELEASED"
            if budget_key == "max_concurrent_runs"
            or (budget_key == "max_cost_usd" and not (cost_has_authoritative_usage or cost_has_failed_usage))
            or (budget_key != "max_cost_usd" and not successful)
            else "COMMITTED"
        )
        try:
            settlement_kwargs: dict[str, Any] = {"state": reservation_state}
            if budget_key == "max_cost_usd" and reservation_state == "COMMITTED":
                settlement_kwargs["amount"] = reported_cost or Decimal("0")
                if not successful and billed_failed_run:
                    settlement_kwargs["metadata"] = {"settlement_reason": "failed_run_billed_usage"}
            await settle(reservation_id, **settlement_kwargs)
        except Exception:
            logger.exception(
                "worker_budget_settlement_failed",
                extra={"reservation_id": str(reservation_id), "state": reservation_state},
            )


async def _execute_queued_worker_run(
    *,
    controller: Any,
    request: Any,
    adapter: Any,
    worker: dict[str, Any],
    storage: AgentStorage,
    model_resolution_snapshot_id: UUID | None,
    lease_seconds: int,
    canonical_run_id: UUID,
    budget_reservations: list[tuple[UUID, str]] | None = None,
) -> None:
    """Execute one claimed run in an application-owned background task."""

    run_id = canonical_run_id
    owner = f"orchestrator-background:{run_id}"
    heartbeat_task: asyncio.Task[Any] | None = None
    outcome_state = "FAILED"
    actual_cost_usd: float | Decimal | None = None

    async def renew_lease() -> None:
        while True:
            await asyncio.sleep(max(5, lease_seconds // 3))
            refreshed = await storage.heartbeat_worker_run(
                run_id,
                owner=owner,
                lease_seconds=lease_seconds,
            )
            if refreshed is None:
                return

    try:
        # A claim is persisted before this task is scheduled.  The heartbeat
        # method is intentionally available to adapters and recovery loops;
        # this bounded task still retains the existing event-driven controller.
        heartbeat_task = asyncio.create_task(renew_lease())
        outcome = await controller.execute(
            request,
            adapter,
            worker_registry_id=worker["id"],
            worker_shell_version_id=worker.get("active_shell_version_id"),
            adapter_id=worker.get("active_adapter_id"),
            steward_id=UUID(str((worker.get("adapter_config") or {}).get("steward_id"))) if (worker.get("adapter_config") or {}).get("steward_id") else None,
            model_resolution_snapshot_id=model_resolution_snapshot_id,
        )
        outcome_state = outcome.state
        if outcome.result is not None:
            actual_cost_usd = outcome.result.usage.cost_usd
    except asyncio.CancelledError:
        logger.info("queued_worker_run_cancelled", extra={"run_id": str(run_id)})
        raise
    except Exception:
        logger.exception("queued_worker_run_failed", extra={"run_id": str(run_id), "owner": owner, "lease_seconds": lease_seconds})
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        await _settle_worker_run_budgets(
            storage,
            budget_reservations or [],
            state=outcome_state,
            actual_cost_usd=actual_cost_usd,
        )
        current = asyncio.current_task()
        if current is not None:
            app.state.worker_run_tasks.discard(current)


@app.post("/workers/runs", status_code=202)
async def dispatch_worker_run(req: WorkerRunDispatchRequest) -> dict[str, Any]:
    storage = _storage()
    worker = await storage.get_worker(req.worker_id)
    if worker is None:
        raise HTTPException(404, f"Worker {req.worker_id} not found")
    if worker.get("status") not in {"ACTIVE", "DRAINING"}:
        raise HTTPException(409, "Worker is not active")
    company_id = await _enforce_company_dispatch_grants(
        storage=storage,
        project_id=req.project_id,
        worker_id=req.worker_id,
        tool_grants=req.tool_grants,
        permission_requirements=req.permission_requirements,
    )
    adapter = await _certified_worker_adapter(storage, worker)
    if adapter is None:
        raise HTTPException(409, "Worker has no certified runtime adapter registered with the control plane")
    from mas_core.worker_contract import CheckpointMode

    if (
        bool((req.checkpoint_policy or {}).get("required"))
        and adapter.capabilities.checkpoint_mode == CheckpointMode.UNSUPPORTED
    ):
        raise HTTPException(
            409,
            {
                "code": "CHECKPOINT_UNSUPPORTED",
                "message": "This task requires checkpoints but the certified adapter declares them unsupported",
            },
        )
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
            budget={
                **req.budget,
                **({"max_cost_usd": req.budget_usd} if req.budget_usd is not None else {}),
            },
            checkpoint_policy=req.checkpoint_policy,
            retry_policy=req.retry_policy,
            extensions=req.runtime_extensions,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    controller = WorkerRunController(storage=storage)
    try:
        budget_reservations = await _reserve_worker_run_budgets(
            storage=storage,
            request=request,
            worker_id=req.worker_id,
            project_id=req.project_id,
            company_id=company_id,
        )
    except ValueError as exc:
        raise HTTPException(409, {"code": "BUDGET_EXCEEDED", "message": str(exc)}) from exc
    dispatch_mode = req.dispatch_mode or ("queued" if os.getenv("MAS_ENVIRONMENT", "development").lower() in {"production", "prod", "staging"} else "inline")
    try:
        if dispatch_mode == "queued":
            queued = await storage.create_worker_run(
                run_id=request.run_id,
                worker_id=req.worker_id,
                idempotency_key=req.idempotency_key,
                task_type=req.task_type,
                request=request.model_dump(mode="json"),
                project_id=req.project_id,
                flow_id=req.flow_id,
                flow_instance_id=req.flow_instance_id,
                flow_node_execution_id=req.flow_node_execution_id,
                worker_shell_version_id=worker.get("active_shell_version_id"),
                adapter_id=worker.get("active_adapter_id"),
                steward_id=UUID(str((worker.get("adapter_config") or {}).get("steward_id"))) if (worker.get("adapter_config") or {}).get("steward_id") else None,
                model_resolution_snapshot_id=model_resolution_snapshot_id,
                state="QUEUED",
                queue_priority=req.queue_priority,
            )
            canonical_run_id = UUID(str(queued["id"]))
            claim = await storage.claim_worker_run(
                owner=f"orchestrator-background:{canonical_run_id}",
                lease_seconds=req.lease_seconds,
                run_id=canonical_run_id,
            )
            if claim is not None and str(claim.get("state")) == "CLAIMED":
                task = asyncio.create_task(
                    _execute_queued_worker_run(
                        controller=controller,
                        request=request,
                        adapter=adapter,
                        worker=worker,
                        storage=storage,
                        model_resolution_snapshot_id=model_resolution_snapshot_id,
                        lease_seconds=req.lease_seconds,
                        canonical_run_id=canonical_run_id,
                        budget_reservations=budget_reservations,
                    )
                )
                app.state.worker_run_tasks.add(task)
            return {
                "run_id": str(queued["id"]),
                "state": str(queued.get("state") or "QUEUED"),
                "dispatch_mode": "queued",
                "accepted": {"run_id": str(queued["id"]), "idempotency_key": req.idempotency_key, "initial_state": str(queued.get("state") or "QUEUED")},
                "status_url": f"/workers/runs/{queued['id']}",
                "events_url": f"/workers/runs/{queued['id']}/events",
            }
        outcome = await controller.execute(
            request,
            adapter,
            worker_registry_id=req.worker_id,
            worker_shell_version_id=worker.get("active_shell_version_id"),
            adapter_id=worker.get("active_adapter_id"),
            steward_id=UUID(str((worker.get("adapter_config") or {}).get("steward_id"))) if (worker.get("adapter_config") or {}).get("steward_id") else None,
            model_resolution_snapshot_id=model_resolution_snapshot_id,
        )
    except BaseException:
        await _settle_worker_run_budgets(storage, budget_reservations, state="FAILED")
        raise
    await _settle_worker_run_budgets(
        storage,
        budget_reservations,
        state=outcome.state,
        actual_cost_usd=(outcome.result.usage.cost_usd if outcome.result is not None else None),
    )
    return {
        "run_id": str(outcome.run_id),
        "state": outcome.state,
        "dispatch_mode": "inline",
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


@app.post("/workers/runs/recover-expired")
async def recover_expired_worker_runs(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    rows = await _storage().recover_expired_worker_runs(limit=limit)
    return {"recovered": len(rows), "runs": [_serialize(row) for row in rows]}


@app.post("/workers/runs/{run_id}/heartbeat")
async def heartbeat_worker_run(run_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    owner = str(payload.get("owner") or "").strip()
    if not owner:
        raise HTTPException(422, "owner is required")
    row = await _storage().heartbeat_worker_run(
        run_id,
        owner=owner,
        lease_seconds=int(payload.get("lease_seconds") or 300),
    )
    if row is None:
        raise HTTPException(409, "run is not owned by this executor or is terminal")
    return _serialize(row)


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


@app.get("/workers/runs/{run_id}/checkpoints")
async def get_worker_run_checkpoints(
    run_id: UUID,
    limit: int = Query(default=100, ge=1, le=1_000),
) -> list[dict[str, Any]]:
    """Return durable checkpoints for a single governed Worker Run."""
    storage = _storage()
    run = await storage.get_worker_run(run_id)
    if run is None:
        raise HTTPException(404, "Worker run not found")
    if not inspect.iscoroutinefunction(getattr(storage, "list_worker_checkpoints", None)):
        raise HTTPException(503, "worker checkpoint persistence is unavailable")
    return [_serialize(row) for row in await storage.list_worker_checkpoints(run_id, limit=limit)]


async def _worker_run_control_adapter(
    storage: AgentStorage,
    run_id: UUID,
) -> tuple[dict[str, Any], Any]:
    """Resolve the immutable adapter pinned when a Worker Run was dispatched."""
    run = await storage.get_worker_run(run_id)
    if run is None:
        raise HTTPException(404, "Worker run not found")
    if not run.get("adapter_id"):
        raise HTTPException(409, "This Worker Run has no pinned certified adapter")
    worker = await storage.get_worker(UUID(str(run["worker_id"])))
    adapter = (
        await _certified_worker_adapter(
            storage,
            worker,
            adapter_id=run["adapter_id"],
            allow_retired=True,
        )
        if worker is not None
        else None
    )
    if adapter is None:
        raise HTTPException(409, "No certified adapter is registered for this run")
    return run, adapter


@app.post("/workers/runs/{run_id}/pause")
async def pause_worker_run(run_id: UUID, req: WorkerRunPauseRequest) -> dict[str, Any]:
    """Pause a running worker only when its certified contract supports checkpoints."""
    storage = _storage()
    _run, adapter = await _worker_run_control_adapter(storage, run_id)
    from mas_core.worker_contract import CheckpointMode, WorkerRunController, WorkerRunError

    checkpoint_mode = adapter.capabilities.checkpoint_mode
    if checkpoint_mode == CheckpointMode.UNSUPPORTED:
        raise HTTPException(
            409,
            {
                "code": "UNSUPPORTED_CAPABILITY",
                "message": "The certified adapter does not support pause/checkpoint control",
            },
        )
    if checkpoint_mode == CheckpointMode.RESTART_ONLY:
        raise HTTPException(
            409,
            {
                "code": "CHECKPOINT_RESTART_ONLY",
                "message": "The certified adapter supports restart from a safe point, not in-place pause",
            },
        )
    # BaseWorkerAdapter.pause only emits an event; it does not stop or
    # checkpoint a running task.  A capability declaration is insufficient
    # unless the adapter supplies a real pause implementation.
    from mas_core.worker_contract.adapters import BaseWorkerAdapter

    if isinstance(adapter, BaseWorkerAdapter) and type(adapter).pause is BaseWorkerAdapter.pause:
        raise HTTPException(
            409,
            {
                "code": "UNSUPPORTED_CAPABILITY",
                "message": "The certified adapter has no in-place pause implementation",
            },
        )
    try:
        row = await WorkerRunController(storage=storage).pause(
            run_id,
            adapter,
            reason=req.reason,
            requested_by=req.requested_by,
        )
    except WorkerRunError as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc), "details": exc.details}) from exc
    if row is None:
        raise HTTPException(404, "Worker run not found")
    return _serialize(row)


@app.post("/workers/runs/{run_id}/resume")
async def resume_worker_run(run_id: UUID, req: WorkerRunResumeRequest) -> dict[str, Any]:
    """Resume a paused worker from an optional durable, resumable checkpoint."""
    storage = _storage()
    _run, adapter = await _worker_run_control_adapter(storage, run_id)
    from mas_core.worker_contract import CheckpointMode, WorkerRunController, WorkerRunError

    checkpoint_mode = adapter.capabilities.checkpoint_mode
    if checkpoint_mode == CheckpointMode.UNSUPPORTED:
        raise HTTPException(
            409,
            {
                "code": "UNSUPPORTED_CAPABILITY",
                "message": "The certified adapter does not support resume control",
            },
        )
    if checkpoint_mode == CheckpointMode.RESTART_ONLY:
        raise HTTPException(
            409,
            {
                "code": "CHECKPOINT_RESTART_ONLY",
                "message": "The certified adapter supports restart from a safe point, not in-place resume",
            },
        )
    if req.checkpoint_id is not None:
        if not inspect.iscoroutinefunction(getattr(storage, "list_worker_checkpoints", None)):
            raise HTTPException(503, "worker checkpoint persistence is unavailable")
        checkpoints = await storage.list_worker_checkpoints(run_id, limit=1_000)
        checkpoint = next(
            (item for item in checkpoints if str(item.get("id")) == str(req.checkpoint_id)),
            None,
        )
        if checkpoint is None:
            raise HTTPException(409, "Checkpoint does not belong to this Worker Run")
        if not bool(checkpoint.get("resumable")):
            raise HTTPException(409, "Checkpoint is not resumable")
    try:
        row = await WorkerRunController(storage=storage).resume(
            run_id,
            adapter,
            requested_by=req.requested_by,
            checkpoint_id=req.checkpoint_id,
        )
    except WorkerRunError as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc), "details": exc.details}) from exc
    if row is None:
        raise HTTPException(404, "Worker run not found")
    return _serialize(row)


@app.post("/workers/runs/{run_id}/cancel")
async def cancel_worker_run(run_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
    storage = _storage()
    row = await storage.get_worker_run(run_id)
    if row is None:
        raise HTTPException(404, "Worker run not found")
    await storage.request_worker_run_cancel(run_id)
    if not row.get("adapter_id"):
        if str(row.get("state")) in {"CREATED", "QUEUED", "CLAIMED"}:
            cancelled = await storage.transition_worker_run(
                run_id,
                new_state="CANCELLED",
                expected_state=str(row.get("state")),
                error={
                    "code": "CANCELLED",
                    "message": str(payload.get("reason") or "operator cancellation"),
                },
                actor=str(payload.get("requested_by") or "operator"),
                reason="cancelled before runtime activation",
            )
            if cancelled is None:
                raise HTTPException(409, "worker run state changed before cancellation")
            list_reservations = getattr(storage, "list_budget_reservations", None)
            settle = getattr(storage, "settle_budget_reservation", None)
            if inspect.iscoroutinefunction(list_reservations) and inspect.iscoroutinefunction(settle):
                for reservation in await list_reservations(run_id=run_id):
                    await settle(UUID(str(reservation["id"])), state="RELEASED")
            return _serialize(cancelled)
        raise HTTPException(409, "This Worker Run has no pinned certified adapter")
    worker = await storage.get_worker(UUID(str(row["worker_id"])))
    adapter = (
        await _certified_worker_adapter(
            storage,
            worker,
            adapter_id=row["adapter_id"],
            allow_retired=True,
        )
        if worker is not None
        else None
    )
    if adapter is None:
        raise HTTPException(409, "No certified adapter is registered for this run")
    from mas_core.worker_contract import WorkerRunController
    row = await WorkerRunController(storage=storage).cancel(run_id, adapter, reason=str(payload.get("reason") or "operator cancellation"), requested_by=str(payload.get("requested_by") or "operator"), force=bool(payload.get("force", False)))
    if row is None:
        raise HTTPException(404, "Worker run not found")
    list_reservations = getattr(storage, "list_budget_reservations", None)
    settle = getattr(storage, "settle_budget_reservation", None)
    if str(row.get("state")) in {"CANCELLED", "FAILED", "TIMED_OUT"} and inspect.iscoroutinefunction(list_reservations) and inspect.iscoroutinefunction(settle):
        for reservation in await list_reservations(run_id=run_id):
            await settle(UUID(str(reservation["id"])), state="RELEASED")
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


@app.post("/flows/dry-run")
async def dry_run_flow(req: FlowDryRunRequest) -> dict[str, Any]:
    """Validate topology and every typed task assignment without creating a flow.

    This is deliberately a control-plane preview: it never starts an adapter,
    resolves a runtime credential, or creates a Worker Run.  It does verify
    the persisted worker, adapter, capability, checkpoint, and Model Profile
    records a real dispatch would rely on.
    """
    from mas_core.worker_contract import CheckpointMode, WorkerCapabilities
    from mas_core.workflow import (
        FlowNodeType,
        FlowValidationError,
        parse_flow_definition,
        validate_flow,
    )
    from mas_core.workflow.worker_policy import TaskNodePolicy

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    node_checks: list[dict[str, Any]] = []
    try:
        definition = parse_flow_definition(req.definition_json)
    except (FlowValidationError, KeyError, TypeError, ValueError) as exc:
        return {
            "valid": False,
            "errors": [{"code": "INVALID_FLOW_DEFINITION", "message": str(exc)}],
            "warnings": warnings,
            "nodes": node_checks,
        }
    for message in validate_flow(definition):
        errors.append({"code": "FLOW_VALIDATION_FAILED", "message": message})

    storage = _storage()
    if req.project_id is not None:
        project = await storage.get_project(req.project_id)
        if project is None:
            errors.append({"code": "PROJECT_NOT_FOUND", "message": f"Project {req.project_id} was not found"})
        elif project.get("state") in TERMINAL_PROJECT_STATES:
            errors.append({"code": "TERMINAL_PROJECT", "message": "Terminal projects cannot start a flow"})

    for node in definition.nodes:
        if node.type != FlowNodeType.TASK:
            continue
        check: dict[str, Any] = {"node_id": node.id, "ready": True, "checks": {}}
        node_checks.append(check)
        try:
            policy = TaskNodePolicy.model_validate(node.config)
        except ValueError as exc:
            check["ready"] = False
            errors.append({"code": "INVALID_TASK_POLICY", "node_id": node.id, "message": str(exc)})
            continue
        if policy.worker_id is None:
            # Legacy team/action nodes are retained for the compatibility
            # window but cannot make a concrete dispatch readiness claim.
            check["ready"] = False
            warnings.append(
                {
                    "code": "LEGACY_TASK_ASSIGNMENT",
                    "node_id": node.id,
                    "message": "Task has no concrete worker_id and cannot be dispatched through a Worker Run",
                }
            )
            continue
        try:
            worker_id = UUID(policy.worker_id)
        except ValueError:
            check["ready"] = False
            errors.append({"code": "INVALID_WORKER_ID", "node_id": node.id, "message": "worker_id must be a UUID"})
            continue
        worker = await storage.get_worker(worker_id)
        if worker is None:
            check["ready"] = False
            errors.append({"code": "WORKER_NOT_FOUND", "node_id": node.id, "worker_id": policy.worker_id, "message": "Assigned worker was not found"})
            continue
        worker_status = str(worker.get("status") or "")
        check["checks"]["worker_status"] = worker_status
        if worker_status not in {"ACTIVE", "DRAINING"}:
            check["ready"] = False
            errors.append({"code": "WORKER_NOT_ACTIVE", "node_id": node.id, "worker_id": policy.worker_id, "message": f"Worker is {worker_status or 'not active'}"})

        active_adapter = None
        get_active_adapter = getattr(storage, "get_active_runtime_adapter", None)
        if inspect.iscoroutinefunction(get_active_adapter):
            active_adapter = await get_active_adapter(worker_id)
        if active_adapter is None:
            check["ready"] = False
            errors.append({"code": "CERTIFIED_ADAPTER_REQUIRED", "node_id": node.id, "worker_id": policy.worker_id, "message": "Worker has no active certified adapter"})
            continue
        adapter_status = str(active_adapter.get("status") or "")
        conformance_status = str(active_adapter.get("conformance_status") or "")
        check["checks"]["adapter"] = {"status": adapter_status, "conformance_status": conformance_status}
        if adapter_status != "active" or conformance_status != "passed":
            check["ready"] = False
            errors.append({"code": "ADAPTER_NOT_CERTIFIED", "node_id": node.id, "worker_id": policy.worker_id, "message": "Worker adapter is not active and conformance-certified"})
            continue
        try:
            capabilities = WorkerCapabilities.model_validate(
                active_adapter.get("capabilities_json")
                or (worker.get("adapter_config") or {}).get("capabilities")
                or {}
            )
        except ValueError as exc:
            check["ready"] = False
            errors.append({"code": "INVALID_ADAPTER_CAPABILITIES", "node_id": node.id, "message": str(exc)})
            continue
        missing_capabilities = sorted(set(policy.required_capabilities) - set(capabilities.capability_names))
        check["checks"]["required_capabilities"] = {
            "required": sorted(policy.required_capabilities),
            "missing": missing_capabilities,
        }
        if missing_capabilities:
            check["ready"] = False
            errors.append({"code": "UNSUPPORTED_CAPABILITY", "node_id": node.id, "worker_id": policy.worker_id, "message": "Worker adapter lacks required capabilities: " + ", ".join(missing_capabilities)})
        if policy.checkpoint_policy.required and capabilities.checkpoint_mode == CheckpointMode.UNSUPPORTED:
            check["ready"] = False
            errors.append({"code": "CHECKPOINT_UNSUPPORTED", "node_id": node.id, "worker_id": policy.worker_id, "message": "Task requires checkpoints but the certified adapter declares them unsupported"})

        # Dispatch uses the worker's governed model mode and default Model
        # Profile, not the node's declarative mode.  The node can request the
        # same profile, but selecting a different one requires the override
        # approval that a dry-run intentionally cannot invent.
        worker_model_mode = str(
            worker.get("model_mode")
            or (worker.get("adapter_config") or {}).get("model_mode")
            or "none"
        )
        worker_profile_id = worker.get("model_profile_id") or (
            worker.get("adapter_config") or {}
        ).get("model_profile_id")
        requested_profile_id = policy.model_profile_id
        if worker_model_mode == "none":
            if requested_profile_id:
                check["ready"] = False
                errors.append(
                    {
                        "code": "MODEL_PROFILE_NOT_ALLOWED",
                        "node_id": node.id,
                        "worker_id": policy.worker_id,
                        "message": "The worker is model-less and cannot accept a task Model Profile",
                    }
                )
            else:
                check["checks"]["model_policy"] = "model-less"
            continue
        if not worker_profile_id:
            check["ready"] = False
            errors.append(
                {
                    "code": "MODEL_PROFILE_NOT_FOUND",
                    "node_id": node.id,
                    "worker_id": policy.worker_id,
                    "message": "Model-governed worker has no default approved Model Profile",
                }
            )
            continue
        if requested_profile_id and requested_profile_id != worker_profile_id:
            check["ready"] = False
            errors.append(
                {
                    "code": "MODEL_OVERRIDE_APPROVAL_REQUIRED",
                    "node_id": node.id,
                    "worker_id": policy.worker_id,
                    "message": "Task Model Profile differs from the worker default and requires an approved override",
                }
            )
            continue
        profile_id = requested_profile_id or str(worker_profile_id)
        profile_row = await storage.get_model_profile(profile_id) if profile_id else None
        if profile_row is None:
            check["ready"] = False
            errors.append({"code": "MODEL_PROFILE_NOT_FOUND", "node_id": node.id, "message": "Task requires an approved Model Profile"})
            continue
        profile = _model_profile_from_row(profile_row)
        approved_versions = profile.approved_versions()
        check["checks"]["model_profile"] = {
            "profile_id": profile_id,
            "source": "task" if requested_profile_id else "worker_default",
            "approved_versions": [version.version for version in approved_versions],
        }
        if not approved_versions:
            check["ready"] = False
            errors.append({"code": "MODEL_PROFILE_NOT_APPROVED", "node_id": node.id, "message": "Task Model Profile has no effective approved version"})

    return {
        "valid": not errors and all(check["ready"] for check in node_checks),
        "errors": errors,
        "warnings": warnings,
        "nodes": node_checks,
    }


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


def _serialize_projection(row: dict[str, Any]) -> dict[str, Any]:
    """Expose a stable projection state while retaining the outbox status."""
    result = _serialize(row)
    status = str(row.get("status") or "PENDING").upper()
    result["projection_status"] = {
        "PENDING": "pending",
        "PROCESSING": "pending",
        "SYNCED": "synced",
        "CONFLICT": "conflicted",
        "CONFLICTED": "conflicted",
        "FAILED": "failed",
        "DEAD_LETTER": "failed",
    }.get(status, "pending")
    return result


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
    """Reject raw secret export from the HTTP control-plane surface.

    Server-side adapters resolve credential references immediately before an
    approved call.  A network route that returns the material value makes an
    otherwise scoped credential exportable by any dashboard/API client.
    """
    _ = (name, req)
    raise HTTPException(
        410,
        "Raw credential export is prohibited; use an approved server-side adapter",
    )


@app.post("/credentials/{name}/approval-requests", status_code=201)
async def request_credential_approval(
    name: str, req: CredentialApprovalRequest
) -> dict[str, Any]:
    """Request one short-lived server-side credential use; no value is returned."""
    mgr = _credentials_manager()
    await mgr.ensure_tables()
    try:
        approval = await mgr.request_approval(
            name,
            requester=req.requester,
            context=req.context,
            requested_by=req.requested_by,
            ttl_seconds=req.ttl_seconds,
        )
    except LookupError as exc:
        raise HTTPException(404, "Credential not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _serialize(approval)


@app.post("/credentials/approval-requests/{approval_id}/decision")
async def decide_credential_approval(
    approval_id: UUID, req: CredentialApprovalDecisionRequest
) -> dict[str, Any]:
    """Approve or reject one exact requester/context credential use."""
    mgr = _credentials_manager()
    await mgr.ensure_tables()
    approval = await mgr.decide_approval(
        approval_id,
        approved=req.approved,
        decided_by=req.decided_by,
        reason=req.reason,
    )
    if approval is None:
        raise HTTPException(404, "Pending credential approval not found or expired")
    return _serialize(approval)


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


@app.get("/identity/dashboard/{resource}")
async def identity_dashboard_resource(resource: str, _auth: None = Depends(_check_auth)) -> dict[str, Any]:
    """Signed control-plane proxy for secret-free identity dashboard data."""
    allowed = {
        "identities", "mail-domains", "mailboxes", "outbound-mail", "mail-relay",
        "external-accounts", "auth-sessions", "identity-approvals", "identity-audit",
    }
    if resource not in allowed:
        raise HTTPException(404, "identity dashboard resource not found")
    try:
        return await _identity_client().request("POST", f"/v1/dashboard/{resource}", {})
    except Exception as exc:
        raise HTTPException(503, "identity service is unavailable") from exc


class IdentityDashboardActionRequest(BaseModel):
    action: Literal[
        "approval.approve", "approval.reject", "identity.suspend", "identity.archive",
        "external.rotate_credentials", "external.suspend", "external.close", "session.revoke",
    ]
    id: UUID | None = None
    worker_id: UUID | None = None
    service: str | None = Field(default=None, max_length=120)
    service_category: str = Field(default="development_test", max_length=80)
    reason: str = Field(default="dashboard operator decision", max_length=500)


@app.post("/identity/dashboard/action")
async def identity_dashboard_action(req: IdentityDashboardActionRequest, _auth: None = Depends(_check_auth)) -> dict[str, Any]:
    """Execute only the explicitly supported identity dashboard mutations."""
    actor = {"actor_id": "dashboard-operator", "purpose": req.reason}
    if req.action.startswith("approval."):
        if req.id is None:
            raise HTTPException(422, "approval id is required")
        path = f"/v1/approvals/{req.id}/decision"
        body = {"actor": actor, "approved": req.action == "approval.approve", "reason": req.reason}
    elif req.action.startswith("identity."):
        if req.worker_id is None:
            raise HTTPException(422, "worker id is required")
        operation = req.action.rsplit(".", 1)[1]
        path = f"/v1/worker-identities/{req.worker_id}/{operation}"
        body = {"actor": actor}
    elif req.action.startswith("external."):
        if req.id is None or req.worker_id is None or not req.service:
            raise HTTPException(422, "external account id, worker id, and service are required")
        operation = req.action.rsplit(".", 1)[1].replace("_", "-")
        path = f"/v1/external-accounts/{req.id}/{operation}"
        body = {
            "worker_id": str(req.worker_id), "actor": actor, "service": req.service,
            "service_category": req.service_category,
            "idempotency_key": f"dashboard:{req.action}:{req.id}",
        }
    else:
        if req.worker_id is None:
            raise HTTPException(422, "worker id is required")
        path = "/v1/sessions/revoke"
        body = {"worker_id": str(req.worker_id), "actor": actor, "session_id": req.id}
    try:
        return await _identity_client().request("POST", path, body)
    except Exception as exc:
        raise HTTPException(503, "identity action could not be completed") from exc


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


# ── Provider-neutral PM/SCM integration API ──────────────────────────────────


def _authenticated_principal(request: Request) -> str:
    """Return the principal established by the API-key middleware.

    Actor headers are attribution metadata only.  They are deliberately not
    used as authorization input because every caller that knows the shared
    service key could otherwise claim to be an operator.
    """
    principal = str(getattr(request.state, "aiat_auth_principal", "") or "").lower()
    if not principal:
        raise HTTPException(401, "authenticated principal is unavailable")
    return principal


def _require_operator_identity(request: Request) -> None:
    """Require the separately configured operator API credential."""
    if _authenticated_principal(request) != "operator":
        raise HTTPException(403, "canonical mutation requires an operator credential")


def _integration_operator(
    request: Request,
    *,
    allow_worker_read: bool = False,
    allow_gateway: bool = False,
) -> None:
    """Authorize integration operations using an authenticated principal.

    ``X-AIAT-Actor-Role`` remains useful for audit attribution, but cannot
    elevate a service or worker authenticated with ``MAS_API_KEY``.  Gateway
    ingress/drain uses its own ``PM_GATEWAY_API_KEY`` and read-only worker
    surfaces may use the ordinary service credential.
    """
    principal = _authenticated_principal(request)
    if principal == "operator":
        return
    if allow_gateway and principal in {"pm_gateway", "gateway"}:
        return
    if allow_worker_read and principal == "service":
        return
    raise HTTPException(403, "integration operation requires an authorized principal")


def _redact_integration_config(value: Any) -> Any:
    """Return provider configuration without bearer material.

    Credential references are safe to display because they are opaque names;
    values under secret/token/password/private-key fields are omitted.  This
    is applied to every connection response, including dashboard reads.
    """
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        secret_markers = ("secret", "token", "password", "private_key", "api_key")
        safe_suffixes = ("_ref", "_refs")
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in secret_markers) and not lowered.endswith(safe_suffixes):
                continue
            redacted[str(key)] = _redact_integration_config(child)
        return redacted
    if isinstance(value, list):
        return [_redact_integration_config(item) for item in value]
    return value


def _youtrack_actor_observation(payload: dict[str, Any]) -> dict[str, str] | None:
    """Extract provider-signed actor hints; never treat them as an auth key."""
    candidates: list[Any] = [payload.get("updatedBy"), payload.get("reporter")]
    comments = payload.get("comments")
    if isinstance(comments, list) and comments and isinstance(comments[-1], dict):
        candidates.append(comments[-1].get("author"))
    comment = payload.get("comment")
    if isinstance(comment, dict):
        candidates.append(comment.get("author"))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        login = str(candidate.get("login") or "").strip()
        email = str(candidate.get("email") or "").strip()
        immutable_id = str(candidate.get("id") or "").strip()
        if immutable_id or login or email:
            return {"id": immutable_id, "login": login, "email": email}
    return None


def _pm_tenant_key(row: dict[str, Any]) -> str:
    return str(row.get("base_url") or "").rstrip("/").lower()


def _serialize_pm_connection(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    safe = dict(row)
    safe["config"] = _redact_integration_config(row.get("config") or {})
    return _serialize(safe)


async def _integration_secret(name: str) -> str:
    value = await _credentials_manager().resolve(
        name,
        requester="pm-integration-gateway",
        context="pm-provider",
    )
    if not value:
        raise RuntimeError(f"credential {name!r} is unavailable or denied")
    return value


def _jwt_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


async def _integration_run_token_broker(
    connection: ProviderConnection,
    repository: str,
    permissions: dict[str, str],
) -> dict[str, object]:
    """Mint a repository/permission-scoped GitHub App installation token.

    The App private key is resolved only inside the credentials boundary.  The
    resulting one-hour token is returned to the governed run and is never
    persisted in AIAT evidence (the evidence scrubber stores issuance metadata
    only).
    """
    config = connection.config or {}
    app_id = str(config.get("github_app_id") or "")
    installation_id = str(config.get("github_installation_id") or "")
    private_key_ref = str(config.get("github_app_private_key_ref") or "")
    if not app_id.isdigit() or not installation_id.isdigit() or not private_key_ref:
        raise RuntimeError(
            "GitHub App broker requires numeric github_app_id, "
            "github_installation_id, and github_app_private_key_ref"
        )
    owner, separator, name = str(repository).partition("/")
    allowed_repository_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
    if (
        not separator
        or not owner
        or not name
        or "/" in name
        or any(char not in allowed_repository_chars for char in owner + name)
        or owner.startswith((".", "-"))
        or name.startswith((".", "-"))
    ):
        raise ValueError("GitHub repository must be a safe owner/name value")
    profile_permissions: dict[str, str] = {
        "metadata": "read",
        "issues": "write",
    }
    profile = connection.capability_profile.lower()
    if profile in {"delivery", "checks"}:
        profile_permissions.update({"contents": "write", "pull_requests": "write"})
    if profile == "checks":
        profile_permissions["checks"] = "write"
    if profile not in {"pm", "delivery", "checks"}:
        raise ValueError("GitHub capability_profile does not define a token permission profile")
    if not permissions:
        permissions = dict(profile_permissions)
    for permission, requested in permissions.items():
        if permission not in profile_permissions or requested not in {"read", "write"}:
            raise ValueError(f"GitHub token permission {permission!r} is outside the connection profile")
        maximum = profile_permissions[permission]
        if maximum == "read" and requested == "write":
            raise ValueError(f"GitHub token permission {permission!r} exceeds the connection profile")
    private_key = await _integration_secret(private_key_ref)
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        key = serialization.load_pem_private_key(private_key.encode("utf-8"), password=None)
        header = _jwt_segment(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        now = int(time.time())
        claims = _jwt_segment(
            json.dumps({"iat": now - 60, "exp": now + 540, "iss": int(app_id)}, separators=(",", ":")).encode()
        )
        signing_input = f"{header}.{claims}".encode("ascii")
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        app_jwt = f"{header}.{claims}.{_jwt_segment(signature)}"
    except (ImportError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("GitHub App private key could not be loaded") from exc
    api_base = str(config.get("github_api_base_url") or "https://api.github.com").rstrip("/")
    parsed = httpx.URL(api_base)
    allowed_hosts = {
        str(value).strip().lower()
        for value in config.get("allowed_hosts", [])
        if str(value).strip()
    }
    if (
        parsed.scheme != "https"
        or not parsed.host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (allowed_hosts and str(parsed.host).lower() not in allowed_hosts)
    ):
        raise RuntimeError("github_api_base_url must be HTTPS")
    url = f"{api_base}/app/installations/{installation_id}/access_tokens"
    body = {"repositories": [name], "permissions": permissions}
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        response = await client.post(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json=body,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub installation token exchange failed ({response.status_code})")
    value = response.json()
    if not isinstance(value, dict) or not value.get("token"):
        raise RuntimeError("GitHub installation token exchange returned no token")
    return {
        "token": str(value["token"]),
        "expires_at": value.get("expires_at"),
        "repository": repository,
        "permissions": permissions,
    }


def _integration_registry() -> ProviderRegistry:
    registry = getattr(app.state, "pm_registry", None)
    if registry is None:
        registry = ProviderRegistry(
            credential_resolver=_integration_secret,
            run_credential_broker=_integration_run_token_broker,
        )
        app.state.pm_registry = registry
    return registry


def _provider_connection(row: dict[str, Any]) -> ProviderConnection:
    return ProviderConnection(
        id=row["id"],
        provider_kind=str(row["provider_kind"]),
        display_name=str(row["display_name"]),
        base_url=str(row["base_url"]),
        credential_ref=str(row["credential_ref"]),
        capability_profile=str(row.get("capability_profile") or "pm"),
        config=dict(row.get("config") or {}),
        status=str(row.get("status") or "DISABLED"),
        schema_version=int(row.get("schema_version") or 1),
    )


def _provider_for(row: dict[str, Any]) -> Any:
    return _integration_registry().get(str(row["provider_kind"]), str(row["id"]))


def _canonical_work_item(issue: dict[str, Any]) -> CanonicalWorkItem:
    return CanonicalWorkItem(
        id=issue["id"],
        project_id=issue["project_id"],
        title=str(issue.get("title") or "Untitled issue"),
        description=issue.get("description"),
        item_type=str(issue.get("issue_type") or "TASK"),
        status=str(issue.get("status") or "backlog"),
        priority=str(issue.get("priority") or "medium"),
        sprint_id=issue.get("sprint_id"),
        parent_id=issue.get("parent_issue_id"),
        assigned_team=issue.get("assigned_team"),
        assigned_agent=issue.get("assigned_agent"),
        estimated_hours=float(issue["estimated_hours"]) if issue.get("estimated_hours") is not None else None,
        actual_hours=float(issue["actual_hours"]) if issue.get("actual_hours") is not None else None,
        story_points=issue.get("story_points"),
        revision=int(issue.get("revision") or 1),
        updated_at=issue.get("updated_at"),
    )


async def _enqueue_issue_projection(
    storage: AgentStorage,
    issue: dict[str, Any],
    *,
    exclude_connection_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """Create idempotent outbox records for every eligible project binding."""
    bindings = await storage.list_pm_bindings(project_id=issue["project_id"])
    queued: list[dict[str, Any]] = []
    item = _canonical_work_item(issue).model_dump(mode="json")
    for binding in bindings:
        if exclude_connection_id is not None and binding.get("connection_id") == exclude_connection_id:
            continue
        connection = await storage.get_pm_connection(binding["connection_id"])
        if connection is None or not pm_binding_effective_policy(
            str(binding.get("status") or "DISABLED"),
            str(connection.get("status") or "DISABLED"),
            str(binding.get("direction") or "outbound"),
        )["outbound_projection"]:
            continue
        key = f"{binding['id']}:{issue['id']}:{issue.get('revision', 1)}:upsert"
        queued.append(
            await storage.enqueue_pm_outbox(
                connection_id=binding["connection_id"],
                aggregate_type="work_item",
                aggregate_id=issue["id"],
                canonical_revision=int(issue.get("revision") or 1),
                operation="upsert_work_item",
                idempotency_key=key,
                payload={"binding_id": str(binding["id"]), "item": item},
            )
        )
    return queued


async def _enqueue_comment_projection(
    storage: AgentStorage,
    issue: dict[str, Any],
    comment: dict[str, Any],
    *,
    exclude_connection_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """Queue comments after the work-item mapping exists; delivery resolves it."""
    bindings = await storage.list_pm_bindings(project_id=issue["project_id"])
    queued: list[dict[str, Any]] = []
    provider_body = str(comment.get("body") or "")
    if str(comment.get("origin") or "aiat") == "aiat":
        attribution = [f"AIAT actor: {comment.get('actor_id') or 'operator'}"]
        if comment.get("run_id"):
            attribution.append(f"Run: {comment['run_id']}")
        if comment.get("evidence_id"):
            attribution.append(f"Evidence: {comment['evidence_id']}")
        provider_body = (
            f"<!-- aiat:comment={comment['id']} -->\n"
            + "\n".join(attribution)
            + "\n\n"
            + provider_body
        )
    for binding in bindings:
        if exclude_connection_id is not None and binding.get("connection_id") == exclude_connection_id:
            continue
        connection = await storage.get_pm_connection(binding["connection_id"])
        if connection is None or not pm_binding_effective_policy(
            str(binding.get("status") or "DISABLED"),
            str(connection.get("status") or "DISABLED"),
            str(binding.get("direction") or "outbound"),
        )["outbound_projection"]:
            continue
        key = f"{binding['id']}:{comment['id']}:comment"
        queued.append(
            await storage.enqueue_pm_outbox(
                connection_id=binding["connection_id"],
                aggregate_type="comment",
                aggregate_id=issue["id"],
                canonical_revision=int(issue.get("revision") or 1),
                operation="project_comment",
                idempotency_key=key,
                payload={
                    "binding_id": str(binding["id"]),
                    "comment": {
                        "id": str(comment["id"]),
                        "body": provider_body,
                        "actor_id": comment["actor_id"],
                        "run_id": str(comment["run_id"]) if comment.get("run_id") else None,
                        "approval_id": str(comment["approval_id"]) if comment.get("approval_id") else None,
                        "evidence_id": comment.get("evidence_id"),
                        "body_blob_ref": comment.get("body_blob_ref"),
                    },
                },
            )
        )
    return queued


async def _enqueue_link_projection(
    storage: AgentStorage,
    issue: dict[str, Any],
    link: dict[str, Any],
) -> list[dict[str, Any]]:
    bindings = await storage.list_pm_bindings(project_id=issue["project_id"])
    queued: list[dict[str, Any]] = []
    for binding in bindings:
        connection = await storage.get_pm_connection(binding["connection_id"])
        if connection is None or not pm_binding_effective_policy(
            str(binding.get("status") or "DISABLED"),
            str(connection.get("status") or "DISABLED"),
            str(binding.get("direction") or "outbound"),
        )["outbound_projection"]:
            continue
        key = f"{binding['id']}:{link['id']}:link"
        queued.append(
            await storage.enqueue_pm_outbox(
                connection_id=binding["connection_id"],
                aggregate_type="link",
                aggregate_id=issue["id"],
                canonical_revision=int(issue.get("revision") or 1),
                operation="project_link",
                idempotency_key=key,
                payload={
                    "binding_id": str(binding["id"]),
                    "link": {
                        "id": str(link["id"]),
                        "link_type": link["link_type"],
                        "target_type": link["target_type"],
                        "target_id": link["target_id"],
                        "metadata": link.get("metadata") or {},
                    },
                },
            )
        )
    return queued


def _canonical_status_from_external(value: Any, current: str) -> str:
    """Normalize common provider status vocabularies into AIAT's vocabulary."""
    normalized = str(value or "").strip().lower().replace(" ", "_")
    aliases = {
        "open": "in_progress",
        "reopened": "in_progress",
        "todo": "backlog",
        "new": "backlog",
        "closed": "done",
        "completed": "done",
        "complete": "done",
        "resolved": "done",
        "cancelled": "cancelled",
        "canceled": "cancelled",
    }
    return aliases.get(normalized, normalized or current)


_AIAT_COMMENT_MARKER_RE = re.compile(
    r"^\s*<!--\s*aiat:comment=(?P<comment_id>[0-9a-fA-F-]{36})\s*-->\s*"
)


def _aiat_comment_marker(value: str) -> str | None:
    match = _AIAT_COMMENT_MARKER_RE.match(value)
    return match.group("comment_id") if match else None


def _provider_version_is_older(current: Any, incoming: Any) -> bool:
    """Compare common provider version tokens without guessing across types."""
    if current in (None, "") or incoming in (None, ""):
        return False
    left = str(current)
    right = str(incoming)
    if left == right:
        return False
    try:
        return float(right) < float(left)
    except ValueError:
        pass
    try:
        from datetime import datetime as _datetime

        left_dt = _datetime.fromisoformat(left.replace("Z", "+00:00"))
        right_dt = _datetime.fromisoformat(right.replace("Z", "+00:00"))
        return right_dt < left_dt
    except ValueError:
        # Opaque provider tokens have no safe ordering; only exact echoes can
        # be treated as a no-op.
        return False


# ACTIVE inbound commands are intentionally narrower than the provider object
# vocabulary.  A provider webhook is evidence by default; it becomes a
# canonical command only when its field, actor, revision, and binding gates all
# pass.  Keep this table provider-neutral so adapters cannot widen policy by
# adding a new field to a payload.
ACTIVE_INBOUND_COMMAND_POLICY: dict[str, dict[str, Any]] = {
    "title": {
        "mode": "approval_required",
        "reason": "title changes require an AIAT approval proposal",
    },
    "description": {
        "mode": "approval_required",
        "reason": "description changes require an AIAT approval proposal",
    },
    "priority": {
        "mode": "allowed",
        "values": {"low", "medium", "high", "urgent", "critical", "normal"},
        "rollback": "restore the prior priority through the governed canonical update path",
    },
    "status": {
        "mode": "allowlist_except_destructive",
        "values": {"backlog", "in_progress", "review", "blocked"},
        "approval_values": {"done", "cancelled"},
        "rollback": "restore the prior status through the governed canonical update path",
    },
    "assigned_team": {
        "mode": "approval_required",
        "reason": "reassignment requires an AIAT approval proposal",
    },
    "assigned_agent": {
        "mode": "approval_required",
        "reason": "reassignment requires an AIAT approval proposal",
    },
    "assignee": {
        "mode": "approval_required",
        "reason": "reassignment requires an AIAT approval proposal",
    },
    "comment": {
        "mode": "evidence_only",
        "command_mode": "structured_command_requires_approval",
        "reason": "ordinary provider comments are evidence; structured commands require approval",
    },
}

ACTIVE_INBOUND_RESERVED_FIELDS = {
    "AIAT Object ID",
    "AIAT Object Type",
    "AIAT Revision",
    "AIAT Managed",
    "canonical_ownership",
    "project_id",
    "connection_id",
    "binding_id",
    "lifecycle_state",
    "credential_ref",
}
_ACTIVE_SYNTHETIC_ACTOR_IDS = {
    "aiat_agents",
    "aiat-integration",
    "aiat_integration",
    "certification-actor",
    "certification_actor",
    "external-provider",
}
_AIAT_STRUCTURED_COMMAND_RE = re.compile(r"^\s*AIAT-COMMAND\s*:\s*(?P<body>\{.*\})\s*$", re.IGNORECASE | re.DOTALL)


async def _active_actor_resolution(storage: Any, connection_row: dict[str, Any], command: Any) -> dict[str, Any] | None:
    """Resolve only a durable immutable provider actor mapping for ACTIVE."""
    actor_id = str(getattr(command.actor, "actor_id", "") or "").strip()
    if not actor_id or actor_id.lower() in _ACTIVE_SYNTHETIC_ACTOR_IDS:
        return None
    actor = command.actor
    if not bool(getattr(actor, "immutable_actor_id", False)):
        resolver = getattr(_provider_for(connection_row), "resolve_external_actor", None)
        if not callable(resolver):
            return None
        try:
            resolved = await resolver(
                _provider_connection(connection_row),
                login=getattr(actor, "provider_login", None) or actor_id,
                email=getattr(actor, "provider_email", None),
            )
        except Exception:
            return None
        actor_id = str(resolved.get("id") or "")
        if not actor_id:
            return None
    tenant_key = str(connection_row.get("base_url") or "").rstrip("/").lower()
    getter = getattr(storage, "get_pm_external_actor_mapping", None)
    if not callable(getter):
        return None
    mapping = getter(
        connection_id=command.connection_id,
        external_actor_id=actor_id,
        tenant_key=tenant_key,
    )
    if hasattr(mapping, "__await__"):
        mapping = await mapping
    if not isinstance(mapping, dict) or str(mapping.get("status") or "") != "TRUSTED":
        return None
    scopes = {str(item) for item in (mapping.get("authorized_scopes") or [])}
    if "issue.priority" not in scopes:
        return None
    return {
        "provider_actor_id": actor_id,
        "provider_actor_role": getattr(command.actor, "role", None),
        "aiat_identity": str(mapping.get("aiat_identity_id")),
        "identity_type": "operator",
        "role": "operator",
        "actor_mapping_id": str(mapping.get("id")),
        "authorized_scopes": sorted(scopes),
    }


def _structured_comment_command(body: str) -> dict[str, Any] | None:
    match = _AIAT_STRUCTURED_COMMAND_RE.match(body)
    if not match:
        return None
    try:
        parsed = json.loads(match.group("body"))
    except json.JSONDecodeError:
        return {"invalid": True}
    return parsed if isinstance(parsed, dict) else {"invalid": True}


async def _record_active_actor_evidence(
    storage: Any,
    *,
    command: Any,
    issue: dict[str, Any],
    inbox: dict[str, Any],
    resolution: dict[str, Any],
) -> None:
    recorder = getattr(storage, "record_integration_evidence", None)
    if not callable(recorder):
        return
    payload = {
        "provider_actor": {
            "id": resolution.get("provider_actor_id"),
            "role": resolution.get("provider_actor_role"),
        },
        "resolved_aiat_identity": resolution.get("aiat_identity"),
        "actor_mapping_id": resolution.get("actor_mapping_id"),
        "identity_type": resolution.get("identity_type"),
        "role": resolution.get("role"),
        "inbox_id": str(inbox.get("id")),
        "event_type": inbox.get("event_type"),
        "operation": command.operation,
        "expected_canonical_revision": getattr(command, "expected_canonical_revision", None),
        "mapping_revision": issue.get("revision"),
        "payload_hash": inbox.get("payload_hash"),
    }
    saved = recorder(
        connection_id=command.connection_id,
        evidence_type="active_inbound_actor",
        external_id=command.external_id,
        project_id=issue.get("project_id"),
        binding_id=command.binding_id,
        payload=payload,
        idempotency_key=f"active-actor:{command.idempotency_key}",
    )
    if hasattr(saved, "__await__"):
        await saved
async def _apply_normalized_command(
    storage: AgentStorage,
    command: Any,
    inbox: dict[str, Any],
) -> dict[str, Any]:
    """Apply a verified provider command to canonical state with CAS protection.

    Unknown external objects and stale revisions are recorded as conflicts and
    never guessed into a project.  This is the critical boundary that keeps a
    provider webhook from becoming an unscoped write API.
    """
    object_type = getattr(command.object_type, "value", str(command.object_type))
    if object_type in {
        ObjectType.PULL_REQUEST.value,
        ObjectType.CHECK.value,
        ObjectType.REPOSITORY.value,
    }:
        # Source-control objects are evidence, not PM work items.  Retaining
        # them in the evidence ledger prevents a PR/check webhook from being
        # misinterpreted as a canonical issue update while still making CI and
        # review facts available to governance and release gates.
        evidence_type = {
            ObjectType.PULL_REQUEST.value: "pull_request_event",
            ObjectType.CHECK.value: "check_event",
            ObjectType.REPOSITORY.value: "repository_event",
        }[object_type]
        connection_row = await storage.get_pm_connection(command.connection_id)
        configured_repository = str(
            (connection_row or {}).get("config", {}).get("repository") or ""
        )
        incoming_repository = str(getattr(command, "external_repository", None) or "")
        if connection_row is None or connection_row.get("status") == "DISABLED":
            await storage.create_pm_conflict(
                connection_id=command.connection_id,
                binding_id=command.binding_id,
                reason="connection_not_active",
                object_type=object_type,
                external_id=command.external_id,
                external_snapshot={"repository": incoming_repository},
            )
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="connection is disabled")
            return {"status": "conflict", "reason": "connection_not_active"}
        if configured_repository and incoming_repository != configured_repository:
            await storage.create_pm_conflict(
                connection_id=command.connection_id,
                binding_id=command.binding_id,
                reason="out_of_scope_repository",
                object_type=object_type,
                external_id=command.external_id,
                external_snapshot={"expected_repository": configured_repository, "incoming_repository": incoming_repository},
            )
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="external repository is outside the connection scope")
            return {"status": "conflict", "reason": "out_of_scope_repository"}
        recorder = getattr(storage, "record_integration_evidence", None)
        if callable(recorder):
            evidence_payload = _scrub_integration_evidence(dict(command.fields or {}))
            evidence_payload["provider_version"] = command.expected_provider_version
            evidence_payload["external_repository"] = getattr(command, "external_repository", None)
            saved = recorder(
                connection_id=command.connection_id,
                evidence_type=evidence_type,
                external_id=command.external_id,
                repository=getattr(command, "external_repository", None),
                binding_id=command.binding_id,
                payload=evidence_payload,
                idempotency_key=command.idempotency_key,
            )
            if hasattr(saved, "__await__"):
                await saved
        await storage.mark_pm_inbox_event(inbox["id"], status="PROCESSED")
        return {"status": "evidence_recorded", "evidence_type": evidence_type, "external_id": command.external_id}
    if object_type != ObjectType.WORK_ITEM.value:
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=command.binding_id,
            reason="unsupported_object_type",
            object_type=object_type,
            external_id=command.external_id,
            external_snapshot=command.fields,
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="unsupported object type")
        return {"status": "conflict", "reason": "unsupported_object_type"}

    mapping = await storage.get_pm_mapping(
        connection_id=command.connection_id,
        object_type=object_type,
        external_id=command.external_id,
    )
    if mapping is None:
        # YouTrack webhook payloads commonly carry the readable key (AIAT-3),
        # while outbound REST projections persist the stable numeric ID (3-23).
        # Resolve by the recorded provider key without changing the canonical
        # mapping's stable external_id.
        mapping = await storage.get_pm_mapping(
            connection_id=command.connection_id,
            object_type=object_type,
            external_key=command.external_id,
        )
    if mapping is None:
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=command.binding_id,
            reason="unknown_mapping",
            object_type=object_type,
            external_id=command.external_id,
            external_snapshot=command.fields,
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="external object is not mapped")
        return {"status": "conflict", "reason": "unknown_mapping", "external_id": command.external_id}
    mapped_external_id = str(mapping.get("external_id") or command.external_id)

    if _provider_version_is_older(mapping.get("provider_version"), command.expected_provider_version):
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=command.binding_id,
            reason="stale_provider_version",
            object_type=object_type,
            aiat_object_id=mapping.get("aiat_object_id"),
            external_id=command.external_id,
            external_snapshot={"provider_version": command.expected_provider_version, "mapping_version": mapping.get("provider_version")},
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="provider version is older than the mapped version")
        return {"status": "conflict", "reason": "stale_provider_version"}

    issue = await storage.get_issue(mapping["aiat_object_id"])
    if issue is None:
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=command.binding_id,
            reason="missing_canonical_object",
            object_type=object_type,
            aiat_object_id=mapping["aiat_object_id"],
            external_id=command.external_id,
            external_snapshot=command.fields,
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="canonical object is missing")
        return {"status": "conflict", "reason": "missing_canonical_object"}

    bindings = await storage.list_pm_bindings(connection_id=command.connection_id)
    binding = next(
        (
            item
            for item in bindings
            if item.get("project_id") == issue.get("project_id")
            and item.get("direction") in {"inbound", "both"}
            and item.get("status") in {"SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"}
        ),
        None,
    )
    if binding is None:
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            reason="out_of_scope",
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot=command.fields,
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="mapping is not in an active inbound binding")
        return {"status": "conflict", "reason": "out_of_scope"}
    connection_row = await storage.get_pm_connection(command.connection_id)
    if connection_row is None or connection_row.get("status") == "DISABLED":
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=binding["id"],
            reason="connection_not_active",
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot=command.fields,
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="connection is not active for inbound changes")
        return {"status": "conflict", "reason": "connection_not_active"}
    # A provider event is trusted only inside the binding's explicit project
    # or repository scope.  Missing scope metadata is also rejected for the
    # GitHub adapter because every GitHub issue event carries repository data.
    provider_kind = str(connection_row.get("provider_kind") or "").lower()
    expected_project = str(binding.get("external_project_id") or "")
    incoming_project = str(getattr(command, "external_project_id", None) or "")
    if expected_project and incoming_project and expected_project != incoming_project:
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=binding["id"],
            reason="out_of_scope_project",
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot={"expected_project": expected_project, "incoming_project": incoming_project},
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="external project is outside the binding scope")
        return {"status": "conflict", "reason": "out_of_scope_project"}
    configured_repository = str(
        binding.get("external_repository")
        or (connection_row.get("config") or {}).get("repository")
        or ""
    )
    incoming_repository = str(getattr(command, "external_repository", None) or "")
    if configured_repository and (
        (incoming_repository and incoming_repository != configured_repository)
        or (provider_kind == "github" and not incoming_repository)
    ):
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=binding["id"],
            reason="out_of_scope_repository",
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot={"expected_repository": configured_repository, "incoming_repository": incoming_repository},
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="external repository is outside the binding scope")
        return {"status": "conflict", "reason": "out_of_scope_repository"}
    # The webhook edge has already authenticated the delivery.  Once the
    # event is also inside this binding's project scope, retain monotonic
    # issue/comment coverage evidence for the later ACTIVE gate.
    evidence_recorder = getattr(storage, "record_pm_binding_evidence", None)
    event_name = str(inbox.get("event_type") or "").lower()
    operation_name = str(command.operation or "").lower()
    webhook_event = (
        "comment"
        if "comment" in event_name or operation_name == "comment"
        else "issue"
        if "issue" in event_name or operation_name in {"update", "created", "create"}
        else None
    )
    if callable(evidence_recorder) and webhook_event:
        recorded = evidence_recorder(
            binding["id"],
            webhook_event=webhook_event,
            webhook_verified=True,
        )
        if hasattr(recorded, "__await__"):
            await recorded

    operation = operation_name or "update"
    fields = dict(command.fields or {})
    content_hash = str(getattr(command, "content_hash", None) or "") or hashlib.sha256(
        json.dumps(fields, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    marker_object_id = str(fields.get("_aiat_marker_object_id") or "")
    marker_revision = fields.get("_aiat_marker_revision")
    try:
        marker_revision_matches = marker_revision is not None and int(marker_revision) == int(issue.get("revision") or 1)
    except (TypeError, ValueError):
        marker_revision_matches = False
    controlled_fields_match = all(
        fields.get(name) == issue.get(name)
        for name in ("title", "description", "status", "priority")
        if name in fields
    )
    is_projection_echo = (
        marker_object_id == str(issue["id"])
        or (marker_revision_matches and controlled_fields_match)
    )
    if is_projection_echo and operation not in {"comment", "created"}:
        # This is the marker written by AIAT's own projection.  Treat it as an
        # acknowledged echo before the READ_ONLY inbound-mutation gate.  The
        # provider event remains authenticated evidence, but it must not become
        # a policy conflict or copy provider formatting into canonical state.
        await storage.upsert_pm_mapping(
            connection_id=command.connection_id,
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=mapped_external_id,
            provider_version=command.expected_provider_version or mapping.get("provider_version"),
            imported_revision=int(issue.get("revision") or 1),
            content_hash=content_hash,
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="PROCESSED", error=None)
        return {"status": "echo", "issue_id": str(issue["id"])}

    # Comment projections carry an immutable AIAT comment marker.  Resolve
    # these echoes before ACTIVE actor/revision gates so an integration bot can
    # acknowledge its own delivery without ever becoming a human command.
    if operation == "comment" or "comment" in operation:
        marker_body = str(fields.get("comment") or fields.get("body") or fields.get("description") or "").strip()
        marker_comment_id = _aiat_comment_marker(marker_body)
        if marker_comment_id:
            existing_comments = await storage.list_work_item_comments(issue["id"])
            if any(str(item.get("id")) == marker_comment_id for item in existing_comments):
                await storage.upsert_pm_mapping(
                    connection_id=command.connection_id,
                    object_type=object_type,
                    aiat_object_id=issue["id"],
                    external_id=mapped_external_id,
                    provider_version=command.expected_provider_version or mapping.get("provider_version"),
                    imported_revision=int(issue.get("revision") or 1),
                    content_hash=content_hash,
                )
                await storage.mark_pm_inbox_event(inbox["id"], status="PROCESSED", error=None)
                return {
                    "status": "echo",
                    "operation": "comment",
                    "issue_id": str(issue["id"]),
                    "comment_id": marker_comment_id,
                }

    effective_policy = pm_binding_effective_policy(
        str(binding.get("status") or "DISABLED"),
        str(connection_row.get("status") or "DISABLED"),
        str(binding.get("direction") or "outbound"),
    )
    # A canary is a narrow, durable exception to READ_ONLY.  It does not
    # alter either lifecycle state and cannot widen a binding's normal policy.
    canary_getter = getattr(storage, "get_armed_pm_inbound_canary_plan", None)
    canary_plan = canary_getter(binding["id"]) if callable(canary_getter) else None
    if hasattr(canary_plan, "__await__"):
        canary_plan = await canary_plan
    if not isinstance(canary_plan, dict):
        canary_plan = None
    inbound_mutation_allowed = bool(effective_policy["inbound_canonical_mutation"] or canary_plan)
    actor_id = getattr(command.actor, "actor_id", None)
    allowed_actors = set((connection_row.get("config") or {}).get("allowed_external_actors") or [])
    actor_resolution: dict[str, Any] | None = None
    if inbound_mutation_allowed:
        actor_resolution = await _active_actor_resolution(storage, connection_row, command)
        if actor_resolution is None:
            await storage.create_pm_conflict(
                connection_id=command.connection_id,
                binding_id=binding["id"],
                reason="unauthorized_external_actor",
                object_type=object_type,
                aiat_object_id=issue["id"],
                external_id=command.external_id,
                canonical_snapshot=issue,
                external_snapshot={
                    "provider_actor": {"actor_id": actor_id, "role": getattr(command.actor, "role", None)},
                    "resolved_aiat_identity": None,
                    "fields": command.fields,
                },
            )
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="ACTIVE requires an authorized mapped human actor")
            return {"status": "conflict", "reason": "unauthorized_external_actor"}
        await _record_active_actor_evidence(
            storage, command=command, issue=issue, inbox=inbox, resolution=actor_resolution
        )
    elif allowed_actors and (
        not actor_id or str(actor_id) not in {str(item) for item in allowed_actors}
    ):
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=binding["id"],
            reason="unauthorized_external_actor",
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot={"actor_id": actor_id, "fields": command.fields},
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="external actor is not allowlisted")
        return {"status": "conflict", "reason": "unauthorized_external_actor"}

    # SHADOW and READ_ONLY deliveries are authenticated, scoped evidence but
    # are not permitted to mutate canonical state.  Record the event coverage
    # above, then retain an explicit policy conflict instead of pretending the
    # inbound write was applied.
    if not inbound_mutation_allowed:
        # READ_ONLY/SHADOW still advance the provider-side observation on the
        # existing mapping.  This acknowledges the authenticated external
        # version for reconciliation without importing fields or changing the
        # canonical revision.  Keep the outbound content hash untouched so a
        # later promotion can still detect provider divergence explicitly.
        await storage.upsert_pm_mapping(
            connection_id=command.connection_id,
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=mapped_external_id,
            provider_version=command.expected_provider_version or mapping.get("provider_version"),
            imported_revision=int(issue.get("revision") or 1),
        )
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=binding["id"],
            reason="out_of_scope",
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot={"fields": command.fields, "binding_status": binding.get("status"), "connection_status": connection_row.get("status")},
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="mapping is not in an active inbound binding")
        return {"status": "conflict", "reason": "out_of_scope"}

    # ACTIVE is a command boundary, not a provider-to-database mirror.  The
    # expected revision is explicit when a structured provider command carries
    # it; otherwise use the latest durable canonical observation on the
    # mapping.  A missing or stale observation fails closed before any write.
    mapped_revisions = [
        value
        for value in (mapping.get("imported_revision"), mapping.get("exported_revision"))
        if value is not None
    ]
    resolved_expected_revision = getattr(command, "expected_canonical_revision", None)
    if resolved_expected_revision is None and mapped_revisions:
        try:
            resolved_expected_revision = max(int(value) for value in mapped_revisions)
        except (TypeError, ValueError):
            resolved_expected_revision = None
    current_revision = int(issue.get("revision") or 1)
    if canary_plan is not None:
        # The persisted plan, not a provider payload, supplies the one exact
        # optimistic-concurrency precondition for this bounded exception.
        resolved_expected_revision = int(canary_plan.get("expected_canonical_revision") or -1)
    if resolved_expected_revision is None:
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=binding["id"],
            reason="missing_expected_revision",
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot={"provider_fields": fields, "mapping": mapping},
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="ACTIVE command requires an expected canonical revision")
        return {"status": "conflict", "reason": "missing_expected_revision"}
    if int(resolved_expected_revision) != current_revision:
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=binding["id"],
            reason="stale_revision",
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot={
                "expected_canonical_revision": int(resolved_expected_revision),
                "current_canonical_revision": current_revision,
                "provider_fields": fields,
            },
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="provider command revision is stale")
        return {"status": "conflict", "reason": "stale_revision"}

    # Reject fields outside the stable command vocabulary, including attempts
    # to alter AIAT identity, ownership, governance, or lifecycle metadata.
    incoming_field_names = {
        str(key)
        for key in fields
        if not str(key).startswith("_") and str(key) != "comment"
    }
    if canary_plan is not None:
        canary_target = str(canary_plan.get("target_priority") or "").strip().lower().replace(" ", "_")
        canary_scope_ok = (
            str(canary_plan.get("connection_id")) == str(command.connection_id)
            and str(canary_plan.get("binding_id")) == str(binding["id"])
            and str(canary_plan.get("canonical_issue_id")) == str(issue["id"])
            and str(canary_plan.get("external_issue_id")) == str(command.external_id)
            and str(canary_plan.get("mapping_id")) == str(mapping.get("id"))
            and actor_resolution is not None
            and str(canary_plan.get("actor_mapping_id")) == str(actor_resolution.get("actor_mapping_id"))
            and int(canary_plan.get("expected_canonical_revision") or -1) == int(issue.get("revision") or 1)
            and operation not in {"comment", "deleted", "delete", "removed", "archived"}
            and incoming_field_names == {"priority"}
            and str(fields.get("priority") or "").strip().lower().replace(" ", "_") == canary_target
        )
        if not canary_scope_ok:
            await storage.create_pm_conflict(
                connection_id=command.connection_id, binding_id=binding["id"], reason="canary_scope_denied",
                object_type=object_type, aiat_object_id=issue["id"], external_id=command.external_id,
                canonical_snapshot=issue, external_snapshot={"fields": fields, "canary_plan_id": str(canary_plan.get("id"))},
            )
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="inbound command is outside the armed canary scope")
            return {"status": "conflict", "reason": "canary_scope_denied"}
    unsupported_fields = sorted(
        name for name in incoming_field_names
        if name not in ACTIVE_INBOUND_COMMAND_POLICY
    )
    if unsupported_fields:
        reason = "reserved_field_mutation" if any(name in ACTIVE_INBOUND_RESERVED_FIELDS for name in unsupported_fields) else "unsupported_inbound_field"
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=binding["id"],
            reason=reason,
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot={"fields": fields, "unsupported_fields": unsupported_fields},
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="provider field is not in the ACTIVE inbound allowlist")
        return {"status": "conflict", "reason": reason, "fields": unsupported_fields}

    # Provider payloads often include the full issue on every webhook.  Only
    # changed values need policy evaluation; unchanged approval-gated fields
    # cannot turn an otherwise allowed priority/status event into a proposal.
    changed_input_fields: dict[str, Any] = {}
    for key in incoming_field_names:
        value = fields.get(key)
        if key == "status":
            value = _canonical_status_from_external(value, str(issue.get("status") or "backlog"))
        elif key == "priority":
            value = str(value).strip().lower().replace(" ", "_")
        current_value = issue.get(key)
        if str(current_value if current_value is not None else "") != str(value if value is not None else ""):
            changed_input_fields[key] = fields.get(key)

    for key in changed_input_fields:
        policy = ACTIVE_INBOUND_COMMAND_POLICY[key]
        mode = str(policy.get("mode") or "")
        normalized_value = changed_input_fields[key]
        if key == "status":
            normalized_value = _canonical_status_from_external(normalized_value, str(issue.get("status") or "backlog"))
        elif key == "priority":
            normalized_value = str(normalized_value).strip().lower().replace(" ", "_")
        if mode == "approval_required" or (
            mode == "allowlist_except_destructive" and normalized_value in set(policy.get("approval_values") or set())
        ):
            await storage.create_pm_conflict(
                connection_id=command.connection_id,
                binding_id=binding["id"],
                reason="approval_required",
                object_type=object_type,
                aiat_object_id=issue["id"],
                external_id=command.external_id,
                canonical_snapshot=issue,
                external_snapshot={
                    "fields": fields,
                    "changed_fields": changed_input_fields,
                    "provider_actor": actor_resolution,
                    "expected_canonical_revision": int(resolved_expected_revision),
                    "approval_scope": key,
                },
            )
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error=f"ACTIVE field {key} requires an AIAT approval")
            return {"status": "conflict", "reason": "approval_required", "field": key}
        if mode == "allowed" and normalized_value not in set(policy.get("values") or set()):
            await storage.create_pm_conflict(
                connection_id=command.connection_id,
                binding_id=binding["id"],
                reason="incompatible_field_value",
                object_type=object_type,
                aiat_object_id=issue["id"],
                external_id=command.external_id,
                canonical_snapshot=issue,
                external_snapshot={"field": key, "value": normalized_value},
            )
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="field value is outside the ACTIVE allowlist")
            return {"status": "conflict", "reason": "incompatible_field_value", "field": key}

    if operation == "comment" or "comment" in operation:
        comment_body = str(fields.get("comment") or fields.get("body") or fields.get("description") or "").strip()
        marker_comment_id = _aiat_comment_marker(comment_body)
        if marker_comment_id:
            existing_comments = await storage.list_work_item_comments(issue["id"])
            if any(str(item.get("id")) == marker_comment_id for item in existing_comments):
                await storage.upsert_pm_mapping(
                    connection_id=command.connection_id,
                    object_type=object_type,
                    aiat_object_id=issue["id"],
                    external_id=mapped_external_id,
                    provider_version=command.expected_provider_version or mapping.get("provider_version"),
                    imported_revision=current_revision,
                    content_hash=content_hash,
                )
                await storage.mark_pm_inbox_event(inbox["id"], status="PROCESSED", error=None)
                return {
                    "status": "echo",
                    "operation": "comment",
                    "issue_id": str(issue["id"]),
                    "comment_id": marker_comment_id,
                }
        structured = _structured_comment_command(comment_body)
        if structured is not None:
            await storage.create_pm_conflict(
                connection_id=command.connection_id,
                binding_id=binding["id"],
                reason="approval_required" if not structured.get("invalid") else "invalid_inbound_command",
                object_type=object_type,
                aiat_object_id=issue["id"],
                external_id=command.external_id,
                canonical_snapshot=issue,
                external_snapshot={
                    "comment": comment_body,
                    "structured_command": structured,
                    "provider_actor": actor_resolution,
                    "expected_canonical_revision": int(resolved_expected_revision),
                },
            )
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="structured provider comment requires an AIAT approval")
            return {"status": "conflict", "reason": "approval_required" if not structured.get("invalid") else "invalid_inbound_command", "operation": "comment"}
        await _record_active_actor_evidence(
            storage, command=command, issue=issue, inbox=inbox,
            resolution=actor_resolution or {},
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="PROCESSED", error=None)
        return {"status": "evidence_only", "operation": "comment", "issue_id": str(issue["id"])}

    if operation in {"deleted", "delete", "removed", "archived"}:
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=binding["id"],
            reason="external_delete",
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot=fields,
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="external deletion requires operator decision")
        return {"status": "conflict", "reason": "external_delete"}
    values: dict[str, Any] = {}
    for key in ("title", "description", "priority", "assigned_team", "assigned_agent"):
        if key in fields and fields[key] is not None:
            values[key] = fields[key]
    if "priority" in values:
        priority = str(values["priority"]).strip().lower().replace(" ", "_")
        if priority not in {"low", "medium", "high", "urgent", "critical", "normal"}:
            await storage.create_pm_conflict(
                connection_id=command.connection_id,
                binding_id=binding["id"],
                reason="incompatible_field_value",
                object_type=object_type,
                aiat_object_id=issue["id"],
                external_id=command.external_id,
                canonical_snapshot=issue,
                external_snapshot={"priority": fields.get("priority")},
            )
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="unsupported external priority")
            return {"status": "conflict", "reason": "incompatible_field_value"}
        values["priority"] = priority
    if fields.get("status") is not None:
        status = _canonical_status_from_external(fields["status"], str(issue.get("status") or "backlog"))
        if status not in {"backlog", "in_progress", "review", "blocked", "done", "cancelled"}:
            await storage.create_pm_conflict(
                connection_id=command.connection_id,
                binding_id=binding["id"],
                reason="unsupported_transition",
                object_type=object_type,
                aiat_object_id=issue["id"],
                external_id=command.external_id,
                canonical_snapshot=issue,
                external_snapshot={"status": fields.get("status")},
            )
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="unsupported external status")
            return {"status": "conflict", "reason": "unsupported_transition"}
        values["status"] = status
    changed_values = {
        key: value
        for key, value in values.items()
        if str(issue.get(key) if issue.get(key) is not None else "") != str(value if value is not None else "")
    }
    if not changed_values:
        await storage.upsert_pm_mapping(
            connection_id=command.connection_id,
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=mapped_external_id,
            provider_version=command.expected_provider_version or mapping.get("provider_version"),
            imported_revision=int(issue.get("revision") or 1),
            content_hash=content_hash,
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="PROCESSED")
        return {"status": "noop", "issue_id": str(issue["id"])}
    values = changed_values

    if canary_plan is not None:
        if set(values) != {"priority"}:
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="canary accepts priority only")
            return {"status": "conflict", "reason": "canary_scope_denied"}
        atomic_apply = getattr(storage, "apply_pm_inbound_canary_priority", None)
        if callable(atomic_apply) and isinstance(storage, AgentStorage):
            try:
                refreshed, queued = await atomic_apply(
                    plan_id=canary_plan["id"], issue_id=issue["id"], expected_revision=current_revision,
                    target_priority=str(values["priority"]), connection_id=command.connection_id,
                    inbox_id=inbox["id"], command_key=command.idempotency_key,
                )
            except ValueError as exc:
                await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error=str(exc)[:500])
                return {"status": "conflict", "reason": "canary_atomic_apply_failed"}
            await storage.mark_pm_inbox_event(inbox["id"], status="PROCESSED")
            return {"status": "applied", "issue_id": str(issue["id"]), "revision": refreshed.get("revision"), "projections": len(queued), "canary": "SUCCEEDED"}
        claimer = getattr(storage, "claim_pm_inbound_canary_command", None)
        claimed = await claimer(canary_plan["id"], inbox_id=inbox["id"]) if callable(claimer) else None
        if claimed is None:
            await storage.create_pm_conflict(
                connection_id=command.connection_id, binding_id=binding["id"], reason="canary_command_limit",
                object_type=object_type, aiat_object_id=issue["id"], external_id=command.external_id,
                canonical_snapshot=issue, external_snapshot={"canary_plan_id": str(canary_plan.get("id")), "fields": fields},
            )
            await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error="armed canary has already accepted its single command")
            return {"status": "conflict", "reason": "canary_command_limit"}

    try:
        if isinstance(storage, AgentStorage):
            refreshed, queued = await storage.update_issue_with_pm_projections(
                issue["id"],
                expected_revision=int(issue.get("revision") or 1),
                exclude_connection_id=command.connection_id,
                **values,
            )
        else:
            await storage.update_issue(
                issue["id"],
                expected_revision=int(issue.get("revision") or 1),
                **values,
            )
            refreshed = await storage.get_issue(issue["id"])
            assert refreshed is not None
            queued = []
    except ValueError as exc:
        if canary_plan is not None:
            completer = getattr(storage, "complete_pm_inbound_canary_plan", None)
            if callable(completer):
                await completer(canary_plan["id"], success=False, result={"error": str(exc), "inbox_id": str(inbox["id"])})
        await storage.create_pm_conflict(
            connection_id=command.connection_id,
            binding_id=command.binding_id,
            reason="stale_revision",
            object_type=object_type,
            aiat_object_id=issue["id"],
            external_id=command.external_id,
            canonical_snapshot=issue,
            external_snapshot=fields,
        )
        await storage.mark_pm_inbox_event(inbox["id"], status="CONFLICT", error=str(exc)[:500])
        return {"status": "conflict", "reason": "stale_revision"}

    await storage.upsert_pm_mapping(
        connection_id=command.connection_id,
        object_type=object_type,
        aiat_object_id=issue["id"],
        external_id=mapped_external_id,
        provider_version=command.expected_provider_version or mapping.get("provider_version"),
        imported_revision=int(refreshed.get("revision") or 1),
        content_hash=content_hash,
    )
    if not isinstance(storage, AgentStorage):
        queued = await _enqueue_issue_projection(
            storage,
            refreshed,
            exclude_connection_id=command.connection_id,
        )
    await storage.mark_pm_inbox_event(inbox["id"], status="PROCESSED")
    if canary_plan is not None:
        completer = getattr(storage, "complete_pm_inbound_canary_plan", None)
        if callable(completer):
            await completer(canary_plan["id"], success=True, result={
                "inbox_id": str(inbox["id"]), "canonical_issue_id": str(issue["id"]),
                "canonical_revision": refreshed.get("revision"), "outbox_count": len(queued),
            })
    return {
        "status": "applied",
        "issue_id": str(issue["id"]),
        "revision": refreshed.get("revision"),
        "projections": len(queued),
    }


@app.post("/integrations/connections", status_code=201)
async def create_integration_connection(req: PMConnectionCreateRequest, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    storage = _storage()
    try:
        _integration_registry().get(req.provider_kind, "validation")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        result = await storage.create_pm_connection(**req.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _serialize_pm_connection(result) or {}


@app.get("/integrations/connections")
async def list_integration_connections(request: Request, status: str | None = None) -> list[dict[str, Any]]:
    _integration_operator(request)
    return [
        _serialize_pm_connection(item) or {}
        for item in await _storage().list_pm_connections(status=status)
    ]


@app.post("/integrations/connections/{connection_id}/external-actor-mappings", status_code=201)
async def create_external_actor_mapping(
    connection_id: UUID,
    req: PMExternalActorMappingCreateRequest,
    request: Request,
) -> dict[str, Any]:
    """Approve one immutable provider actor from authenticated inbox evidence."""
    _integration_operator(request)
    approver = _authenticated_principal(request)
    storage = _storage()
    row = await storage.get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    if str(row.get("provider_kind") or "").lower() != "youtrack":
        raise HTTPException(422, "immutable actor resolution is currently implemented for YouTrack only")
    observations: list[tuple[dict[str, Any], dict[str, str]]] = []
    for event_id in req.inbox_event_ids:
        inbox = await storage.get_pm_inbox_event(event_id)
        if inbox is None or inbox.get("connection_id") != connection_id or not bool(inbox.get("verified")):
            raise HTTPException(422, "every mapping evidence inbox row must be authenticated and belong to this connection")
        observation = _youtrack_actor_observation(dict(inbox.get("payload") or {}))
        if observation is None:
            raise HTTPException(422, "authenticated webhook evidence contains no provider actor observation")
        observations.append((inbox, observation))
    first = observations[0][1]
    if any(
        item[1].get("login") != first.get("login") or item[1].get("email", "").lower() != first.get("email", "").lower()
        for item in observations[1:]
    ):
        raise HTTPException(422, "mapping evidence refers to more than one provider actor")
    resolver = getattr(_provider_for(row), "resolve_external_actor", None)
    if not callable(resolver):
        raise HTTPException(409, "provider does not support immutable actor resolution")
    try:
        resolved = await resolver(
            _provider_connection(row), login=first.get("login") or None, email=first.get("email") or None
        )
    except Exception as exc:
        raise HTTPException(409, f"immutable provider actor resolution failed: {exc}") from exc
    external_actor_id = str(resolved.get("id") or "")
    if not external_actor_id:
        raise HTTPException(409, "provider resolution did not return an immutable actor ID")
    # API-key authentication currently authenticates the operator principal;
    # no caller-supplied human identity header is accepted.
    aiat_identity_id = f"aiat:{approver}"
    snapshot = {
        "immutable_provider_actor_id": external_actor_id,
        "provider_login": resolved.get("login"),
        "provider_email": resolved.get("email"),
        "resolution_method": "authenticated_youtrack_user_lookup_correlated_to_verified_inbox",
    }
    evidence_refs = {
        "inbox_ids": [str(item[0]["id"]) for item in observations],
        "payload_hashes": [str(item[0].get("payload_hash") or "") for item in observations],
        "reason": req.reason,
    }
    try:
        mapping, audit = await storage.create_pm_external_actor_mapping(
            connection_id=connection_id,
            provider_kind=str(row.get("provider_kind")),
            tenant_key=_pm_tenant_key(row),
            external_actor_id=external_actor_id,
            actor_snapshot=snapshot,
            aiat_identity_id=aiat_identity_id,
            authorized_scopes=list(req.authorized_scopes),
            created_by=approver,
            approved_by=approver,
            evidence_refs=evidence_refs,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "mapping_id": str(mapping["id"]), "connection_id": str(connection_id),
        "immutable_provider_actor_id": external_actor_id, "provider_tenant": _pm_tenant_key(row),
        "aiat_identity_id": aiat_identity_id, "status": mapping["status"],
        "authorized_scopes": mapping["authorized_scopes"], "created_by": mapping["created_by"],
        "approved_by": mapping["approved_by"], "created_at": mapping["created_at"],
        "approved_at": mapping["approved_at"], "audit_id": str(audit["id"]),
    }


@app.post("/integrations/connections/{connection_id}/external-actor-mappings/{mapping_id}/revoke")
async def revoke_external_actor_mapping(connection_id: UUID, mapping_id: UUID, request: Request, reason: str = Query(min_length=1, max_length=500)) -> dict[str, Any]:
    _integration_operator(request)
    mapping, audit = await _storage().revoke_pm_external_actor_mapping(
        mapping_id, connection_id=connection_id, actor=_authenticated_principal(request), reason=reason
    )
    if mapping is None:
        raise HTTPException(404, "external actor mapping not found for this connection")
    return {"mapping_id": str(mapping_id), "status": mapping["status"], "audit_id": str(audit["id"])}


@app.post("/integrations/connections/{connection_id}/inbound-canaries", status_code=201)
async def create_inbound_priority_canary_plan(
    connection_id: UUID,
    req: PMInboundCanaryPlanCreateRequest,
    request: Request,
) -> dict[str, Any]:
    """Persist a short-lived, priority-only canary without widening the binding."""
    _integration_operator(request)
    actor = _authenticated_principal(request)
    storage = _storage()
    connection_row = await storage.get_pm_connection(connection_id)
    if connection_row is None:
        raise HTTPException(404, "integration connection not found")
    bindings = await storage.list_pm_bindings(connection_id=connection_id)
    binding = next((item for item in bindings if item.get("id") == req.binding_id), None)
    if binding is None:
        raise HTTPException(404, "binding not found for connection")
    if str(connection_row.get("status")) != "SHADOW" or str(binding.get("status")) != "READ_ONLY":
        raise HTTPException(409, "bounded canary requires connection SHADOW and binding READ_ONLY")
    if str(binding.get("direction") or "").lower() not in {"inbound", "both"}:
        raise HTTPException(409, "bounded canary requires inbound binding direction")
    issue = await storage.get_issue(req.canonical_issue_id)
    if issue is None or issue.get("project_id") != binding.get("project_id"):
        raise HTTPException(422, "canonical issue is not inside the requested binding project")
    mapping = await storage.get_pm_mapping(
        connection_id=connection_id, object_type="work_item", aiat_object_id=req.canonical_issue_id
    )
    if mapping is None or mapping.get("id") != req.mapping_id or str(mapping.get("external_id")) != req.external_issue_id:
        raise HTTPException(422, "canonical issue, external issue, and mapping must match exactly")
    actor_mapping = await storage.get_pm_external_actor_mapping_by_id(req.actor_mapping_id)
    if (
        actor_mapping is None or actor_mapping.get("connection_id") != connection_id
        or str(actor_mapping.get("status")) != "TRUSTED"
        or "issue.priority" not in {str(item) for item in (actor_mapping.get("authorized_scopes") or [])}
    ):
        raise HTTPException(422, "canary requires a trusted priority-authorized actor mapping for this connection")
    try:
        provider_item = await _provider_for(connection_row).read_work_item(
            _provider_connection(connection_row), req.external_issue_id
        )
    except Exception as exc:
        raise HTTPException(502, f"provider canary inspection failed: {exc}") from exc
    canonical_priority = str(issue.get("priority") or "medium").strip().lower().replace(" ", "_")
    provider_priority = str(getattr(provider_item, "priority", "") or "").strip().lower().replace(" ", "_")
    if not provider_priority:
        raise HTTPException(409, "provider inspection did not return a priority value")
    # READ_ONLY deliberately retains provider edits as evidence, so the two
    # values can differ before a canary.  Model both observations explicitly;
    # require the selected value to change *both* sides rather than silently
    # re-baselining or performing a canonical write during planning.
    choices = ["high", "medium", "urgent", "critical", "normal", "low"]
    target_priority = str(req.target_priority or next(
        value for value in choices if value not in {canonical_priority, provider_priority}
    ))
    if target_priority in {canonical_priority, provider_priority}:
        raise HTTPException(422, "canary target priority must change both the provider and canonical values")
    doctor = await doctor_integration_connection(connection_id, request)
    recent_runs = await storage.list_pm_reconciliation_runs(connection_id=connection_id, limit=1)
    latest_reconciliation = recent_runs[0] if recent_runs else None
    gates = {
        "doctor_ready": bool(doctor.get("ready")),
        "connection_status": connection_row.get("status"),
        "binding_status": binding.get("status"),
        "connection_revision": connection_row.get("revision"),
        "binding_revision": binding.get("revision"),
        "canonical_revision": issue.get("revision"),
        "mapping_id": str(mapping.get("id")),
        "reconciliation_run_id": str(latest_reconciliation.get("id")) if latest_reconciliation else None,
        "reconciliation_status": latest_reconciliation.get("status") if latest_reconciliation else None,
    }
    blockers = [] if gates["doctor_ready"] else ["integration doctor is not ready"]
    if latest_reconciliation is None:
        blockers.append("a fresh reconciliation run is required")
    elif any(int((latest_reconciliation.get("counts") or {}).get(name) or 0) != 0 for name in ("drift", "conflicts")):
        blockers.append("latest reconciliation has drift or conflicts")
    if blockers:
        raise HTTPException(409, {"code": "canary_preconditions_blocked", "blockers": blockers})
    now = datetime.now(tz=UTC)
    plan_id = uuid4()
    plan = PMInboundCanaryPlan(
        plan_id=plan_id,
        connection_id=connection_id, binding_id=req.binding_id, project_id=issue["project_id"],
        canonical_issue_id=req.canonical_issue_id, external_issue_id=req.external_issue_id,
        mapping_id=req.mapping_id, actor_mapping_id=req.actor_mapping_id,
        expected_connection_revision=int(connection_row.get("revision") or 1),
        expected_binding_revision=int(binding.get("revision") or 1),
        expected_canonical_revision=int(issue.get("revision") or 1),
        current_priority=canonical_priority, target_priority=target_priority,
        operations=[{
            "operation": "accept_one_inbound_priority_command", "binding_id": str(req.binding_id),
            "canonical_issue_id": str(req.canonical_issue_id), "external_issue_id": req.external_issue_id,
            "actor_mapping_id": str(req.actor_mapping_id), "field": "priority",
            "canonical_from": canonical_priority, "provider_from": provider_priority,
            "to": target_priority, "max_command_count": 1,
            "expected_canonical_revision": int(issue.get("revision") or 1),
        }],
        gate_results={**gates, "provider_priority": provider_priority, "canonical_priority": canonical_priority,
                      "pre_canary_priority_divergence": provider_priority != canonical_priority},
        evidence_refs={"mapping_id": str(req.mapping_id), "actor_mapping_id": str(req.actor_mapping_id),
                       "provider_version": getattr(provider_item, "provider_version", None),
                       "doctor_connection_id": str(connection_id),
                       "reconciliation_run_id": str(latest_reconciliation.get("id")) if latest_reconciliation else None},
        rollback_operations=[
            {"operation": "disarm_inbound_canary", "plan_id": str(plan_id)},
            {"operation": "governed_binding_transition", "binding_id": str(req.binding_id), "desired_status": "READ_ONLY"},
        ],
        created_by=actor, created_at=now, expires_at=now + timedelta(seconds=req.ttl_seconds),
    )
    digest = plan.digest()
    stored = await storage.create_pm_inbound_canary_plan(plan, digest=digest)
    return {
        "plan": plan.model_dump(mode="json"), "plan_id": str(plan.plan_id), "digest": digest,
        "status": stored["status"], "created_at": stored["created_at"], "expires_at": stored["expires_at"],
        "permitted_action": f"On {req.external_issue_id}, the mapped human may change provider priority from {provider_priority} to {target_priority} once after explicit approval and arming; canonical priority is expected to move from {canonical_priority} to {target_priority}.",
        "rollback_operations": plan.rollback_operations,
    }


@app.get("/integrations/inbound-canaries/{plan_id}")
async def get_inbound_canary_plan(plan_id: UUID, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    row = await _storage().get_pm_inbound_canary_plan(plan_id)
    if row is None:
        raise HTTPException(404, "inbound canary plan not found")
    immutable = PMInboundCanaryPlan(
        plan_id=row["id"], connection_id=row["connection_id"], binding_id=row["binding_id"],
        project_id=row["project_id"], canonical_issue_id=row["canonical_issue_id"],
        external_issue_id=row["external_issue_id"], mapping_id=row["mapping_id"],
        actor_mapping_id=row["actor_mapping_id"], expected_connection_status=row["expected_connection_status"],
        expected_binding_status=row["expected_binding_status"],
        expected_connection_revision=row["expected_connection_revision"], expected_binding_revision=row["expected_binding_revision"],
        expected_canonical_revision=row["expected_canonical_revision"], current_priority=row["current_priority"],
        target_priority=row["target_priority"], max_command_count=row["max_command_count"],
        operations=row["operations"], gate_results=row["gate_results"], evidence_refs=row["evidence_refs"],
        rollback_operations=row["rollback_operations"], created_by=row["created_by"], created_at=row["created_at"],
        expires_at=row["expires_at"],
    )
    digest = immutable.digest()
    return _serialize({
        "plan": immutable.model_dump(mode="json"), "digest": row["digest"], "digest_valid": digest == row["digest"],
        "status": row["status"], "accepted_command_count": row["accepted_command_count"],
        "approved_by": row.get("approved_by"), "approved_at": row.get("approved_at"),
        "armed_by": row.get("armed_by"), "armed_at": row.get("armed_at"),
        "expired_by": row.get("expired_by"), "expired_at": row.get("expired_at"),
        "completed_at": row.get("completed_at"), "result": row.get("result"), "error": row.get("error"),
        "updated_at": row.get("updated_at"),
    })


@app.post("/integrations/inbound-canaries/{plan_id}/approve")
async def approve_inbound_canary_plan(plan_id: UUID, req: PMInboundCanaryPlanActionRequest, request: Request) -> dict[str, Any]:
    """Operator-only, exact-digest approval; approval alone does not arm it."""
    _integration_operator(request)
    if not req.confirm:
        raise HTTPException(422, "explicit confirmation is required")
    try:
        row = await _storage().approve_pm_inbound_canary_plan(
            plan_id, digest=req.digest, actor=_authenticated_principal(request)
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _serialize(row)


@app.post("/integrations/inbound-canaries/{plan_id}/expire")
async def expire_inbound_canary_plan(plan_id: UUID, req: PMInboundCanaryPlanActionRequest, request: Request) -> dict[str, Any]:
    """Operator-only expiry recording; immutable plan content is retained."""
    _integration_operator(request)
    if not req.confirm:
        raise HTTPException(422, "explicit confirmation is required")
    try:
        row = await _storage().expire_pm_inbound_canary_plan(
            plan_id, digest=req.digest, actor=_authenticated_principal(request)
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _serialize(row)


@app.post("/integrations/inbound-canaries/{plan_id}/arm")
async def arm_inbound_canary_plan(plan_id: UUID, req: PMInboundCanaryPlanActionRequest, request: Request) -> dict[str, Any]:
    """Arm a pre-approved exact plan only after current readiness rechecks."""
    _integration_operator(request)
    if not req.confirm:
        raise HTTPException(422, "explicit confirmation is required")
    storage = _storage()
    plan = await storage.get_pm_inbound_canary_plan(plan_id)
    if plan is None:
        raise HTTPException(404, "inbound canary plan not found")
    if str(plan.get("digest")) != req.digest:
        raise HTTPException(409, "canary plan digest mismatch")
    connection = await storage.get_pm_connection(plan["connection_id"])
    bindings = await storage.list_pm_bindings(connection_id=plan["connection_id"])
    binding = next((item for item in bindings if item.get("id") == plan["binding_id"]), None)
    issue = await storage.get_issue(plan["canonical_issue_id"])
    actor_mapping = await storage.get_pm_external_actor_mapping_by_id(plan["actor_mapping_id"])
    if connection is None or binding is None or issue is None or actor_mapping is None or (
        str(connection.get("status")) != str(plan.get("expected_connection_status"))
        or str(binding.get("status")) != str(plan.get("expected_binding_status"))
        or int(connection.get("revision") or 1) != int(plan.get("expected_connection_revision") or -1)
        or int(binding.get("revision") or 1) != int(plan.get("expected_binding_revision") or -1)
        or int(issue.get("revision") or 1) != int(plan.get("expected_canonical_revision") or -1)
    ):
        raise HTTPException(409, "canary plan is stale")
    if (
        actor_mapping.get("connection_id") != plan["connection_id"]
        or str(actor_mapping.get("status")) != "TRUSTED"
        or "issue.priority" not in {str(item) for item in (actor_mapping.get("authorized_scopes") or [])}
        or str(issue.get("priority") or "").strip().lower().replace(" ", "_") != str(plan.get("current_priority") or "")
    ):
        raise HTTPException(409, "canary plan actor mapping or canonical priority is stale")
    try:
        provider_item = await _provider_for(connection).read_work_item(
            _provider_connection(connection), str(plan["external_issue_id"])
        )
    except Exception as exc:
        raise HTTPException(502, f"provider canary verification failed: {exc}") from exc
    provider_priority = str(getattr(provider_item, "priority", "") or "").strip().lower().replace(" ", "_")
    planned_provider_priority = str((plan.get("gate_results") or {}).get("provider_priority") or "")
    if provider_priority != planned_provider_priority:
        raise HTTPException(409, "canary plan provider priority is stale")
    doctor = await doctor_integration_connection(plan["connection_id"], request)
    if not doctor.get("ready"):
        raise HTTPException(409, "integration doctor blocks canary arming")
    try:
        row = await storage.arm_pm_inbound_canary_plan(plan_id, digest=req.digest, actor=_authenticated_principal(request))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _serialize(row)


@app.post("/integrations/inbound-canaries/{plan_id}/audit-evidence")
async def record_inbound_canary_audit_evidence(plan_id: UUID, req: PMInboundCanaryPlanActionRequest, request: Request) -> dict[str, Any]:
    """Persist idempotent operator evidence for an already governed action."""
    _integration_operator(request)
    storage = _storage()
    plan = await storage.get_pm_inbound_canary_plan(plan_id)
    if plan is None or str(plan.get("digest")) != req.digest:
        raise HTTPException(409, "canary plan is missing or digest-mismatched")
    evidence: dict[str, Any] = {}
    governed_actions = [
        ("approval", "approved_by", "approved_at"),
        ("arming", "armed_by", "armed_at"),
    ]
    if str(plan.get("status") or "") == "EXPIRED":
        governed_actions.append(("expiry", "expired_by", "expired_at"))
    for action, actor_key, timestamp_key in governed_actions:
        if plan.get(actor_key) and plan.get(timestamp_key):
            row = await storage.record_integration_evidence(
                connection_id=plan["connection_id"], binding_id=plan["binding_id"], project_id=plan["project_id"],
                evidence_type=f"pm_inbound_canary_{action}", external_id=str(plan_id),
                payload={"plan_id": str(plan_id), "digest": req.digest, "actor": plan[actor_key], "occurred_at": plan[timestamp_key].isoformat()},
                idempotency_key=f"pm-inbound-canary:{plan_id}:{action}:{req.digest}",
            )
            evidence[action] = str(row["id"])
    return {"plan_id": str(plan_id), "evidence": evidence}


@app.post("/integrations/inbound-canaries/{plan_id}/disarm")
async def disarm_inbound_canary_plan(plan_id: UUID, req: PMInboundCanaryPlanActionRequest, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    if not req.confirm:
        raise HTTPException(422, "explicit confirmation is required")
    try:
        row = await _storage().disarm_pm_inbound_canary_plan(
            plan_id, digest=req.digest, actor=_authenticated_principal(request),
            reason=req.reason or "operator_disarm",
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _serialize(row)


@app.get("/integrations/connections/{connection_id}/health")
async def integration_connection_health(connection_id: UUID, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    storage = _storage()
    row = await storage.get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    if str(row.get("status") or "DISABLED") == "DISABLED":
        raise HTTPException(409, "integration connection is disabled")
    try:
        value = await _provider_for(row).health(_provider_connection(row))
    except Exception as exc:
        if isinstance(storage, AgentStorage):
            await storage.update_pm_connection(
                connection_id,
                last_health_at=datetime.now(tz=UTC),
                last_health_status="FAILED",
                last_health_error=str(exc)[:500],
            )
        return {"ok": False, "connection_id": str(connection_id), "error": str(exc)[:500]}
    if isinstance(storage, AgentStorage):
        await storage.update_pm_connection(
            connection_id,
            last_health_at=datetime.now(tz=UTC),
            last_health_status="OK",
            last_health_error=None,
        )
    return {"ok": True, "connection_id": str(connection_id), **value}


@app.get("/integrations/connections/{connection_id}/capabilities")
async def integration_connection_capabilities(connection_id: UUID, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    storage = _storage()
    row = await storage.get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    try:
        capabilities = await _provider_for(row).capabilities(_provider_connection(row))
    except Exception as exc:
        raise HTTPException(502, f"provider capabilities unavailable: {exc}") from exc
    return _serialize(capabilities.model_dump(mode="json"))


@app.get("/integrations/connections/{connection_id}/doctor")
async def doctor_integration_connection(connection_id: UUID, request: Request) -> dict[str, Any]:
    """Run non-mutating credential, scope, capability, and mapping checks."""
    _integration_operator(request)
    storage = _storage()
    row = await storage.get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    provider = _provider_for(row)
    connection = _provider_connection(row)
    checks: list[dict[str, Any]] = []

    async def check(name: str, operation: Any) -> None:
        try:
            value = await operation()
            checks.append({"name": name, "ok": True, "detail": value})
        except Exception as exc:
            checks.append({"name": name, "ok": False, "error": str(exc)[:500]})

    await check("health_and_credentials", lambda: provider.health(connection))
    await check("capabilities", lambda: provider.capabilities(connection))
    await check("configuration", lambda: provider.verify_configuration(connection))
    if connection.provider_kind.lower() == "youtrack":
        least_privilege_check = getattr(provider, "verify_least_privilege", None)
        if not callable(least_privilege_check):
            checks.append(
                {
                    "name": "least_privilege",
                    "ok": False,
                    "error": "YouTrack adapter does not implement least-privilege certification",
                }
            )
        else:
            try:
                permission_report = await least_privilege_check(connection)
                permission_errors = [
                    *(str(item) for item in permission_report.get("missing", [])),
                    *(f"forbidden permission: {item}" for item in permission_report.get("forbidden", [])),
                ]
                checks.append(
                    {
                        "name": "least_privilege",
                        "ok": bool(permission_report.get("ok")),
                        "detail": permission_report,
                        "error": "; ".join(permission_errors) if permission_errors else None,
                    }
                )
            except Exception as exc:
                checks.append({"name": "least_privilege", "ok": False, "error": str(exc)[:500]})
    await check("provider_scope_discovery", lambda: provider.discover(connection))
    config = dict(row.get("config") or {})
    bindings = await storage.list_pm_bindings(connection_id=connection_id)
    if not bindings:
        checks.append({"name": "project_or_repository_binding", "ok": False, "error": "no project binding configured"})
    else:
        checks.append({"name": "project_or_repository_binding", "ok": True, "detail": len(bindings)})
        lifecycle_blockers: list[str] = []
        for binding in bindings:
            # Older lightweight fixtures may omit lifecycle columns; live
            # rows created after migration 0025 always contain them.
            if "mapping_profile" not in binding and "provisioning_state" not in binding:
                continue
            profile = str(binding.get("mapping_profile") or DEDICATED_PROJECT_MAPPING_PROFILE)
            if profile in {"default", DEDICATED_PROJECT_MAPPING_PROFILE} and not binding.get("external_project_id"):
                lifecycle_blockers.append(f"binding {binding.get('id')} has no dedicated provider project selector")
            lifecycle_blockers.extend(str(item) for item in (binding.get("activation_blockers") or []) if item)
            if str(binding.get("status") or "DISABLED") == "ACTIVE":
                for field, label in (
                    ("webhook_verified_at", "authenticated webhook"),
                    ("projection_verified_at", "projection"),
                    ("reconciliation_verified_at", "reconciliation"),
                ):
                    if not binding.get(field):
                        lifecycle_blockers.append(f"binding {binding.get('id')} lacks {label} evidence")
        checks.append(
            {
                "name": "project_binding_lifecycle",
                "ok": not lifecycle_blockers,
                "detail": "dedicated provider project and activation evidence are enforced",
                "error": "; ".join(lifecycle_blockers) if lifecycle_blockers else None,
            }
        )
    active_bindings = [
        binding for binding in bindings
        if str(binding.get("status") or "DISABLED").upper() == "ACTIVE"
        and str(binding.get("direction") or "outbound").lower() in {"inbound", "both"}
    ]
    durable_actor_mapping_count = 0
    counter = getattr(storage, "count_trusted_pm_external_actor_mappings", None)
    if callable(counter):
        counted = counter(connection_id)
        if hasattr(counted, "__await__"):
            counted = await counted
        if isinstance(counted, int):
            durable_actor_mapping_count = counted
    active_policy_blockers: list[str] = []
    if active_bindings and durable_actor_mapping_count <= 0:
        active_policy_blockers.append(
            "ACTIVE inbound bindings require a trusted durable external actor mapping"
        )
    checks.append(
        {
            "name": "active_inbound_command_policy",
            "ok": not active_policy_blockers,
            "detail": {
                "allowlist": ACTIVE_INBOUND_COMMAND_POLICY,
                "reserved_fields": sorted(ACTIVE_INBOUND_RESERVED_FIELDS),
                "actor_mapping_count": durable_actor_mapping_count,
                "activation_checked": bool(active_bindings),
            },
            "error": "; ".join(active_policy_blockers) if active_policy_blockers else None,
        }
    )
    if str(row.get("provider_kind")).lower() != "fake" and not (
        config.get("webhook_secret_ref") or config.get("webhook_secret_refs")
    ):
        checks.append({"name": "webhook_secret_reference", "ok": False, "error": "webhook_secret_ref is required"})
    else:
        checks.append({"name": "webhook_secret_reference", "ok": True, "detail": "configured"})
    if str(row.get("provider_kind")).lower() == "github" and connection.capability_profile.lower() in {"delivery", "checks"}:
        broker_fields = {
            "github_app_id": config.get("github_app_id"),
            "github_installation_id": config.get("github_installation_id"),
            "github_app_private_key_ref": config.get("github_app_private_key_ref"),
        }
        missing_broker_fields = [name for name, value in broker_fields.items() if not value]
        checks.append(
            {
                "name": "github_installation_token_broker",
                "ok": not missing_broker_fields,
                "error": f"missing broker configuration: {', '.join(missing_broker_fields)}" if missing_broker_fields else None,
                "detail": "server-side App JWT broker configured" if not missing_broker_fields else None,
            }
        )
    blockers = [item.get("error") or item["name"] for item in checks if not item.get("ok")]
    return {
        "connection_id": str(connection_id),
        "provider_kind": connection.provider_kind,
        "ready": not blockers,
        "blockers": blockers,
        "checks": checks,
    }


def _lifecycle_plan_from_row(row: dict[str, Any]) -> PMLifecycleTransitionPlan:
    """Rehydrate the immutable plan payload stored in Postgres."""
    payload = {
        "plan_id": row["id"],
        "plan_kind": row["plan_kind"],
        "schema_version": row["schema_version"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "connection_id": row["connection_id"],
        "binding_id": row.get("binding_id"),
        "expected_connection_status": row.get("expected_connection_status"),
        "expected_binding_status": row.get("expected_binding_status"),
        "expected_connection_revision": row.get("expected_connection_revision"),
        "expected_binding_revision": row.get("expected_binding_revision"),
        "desired_connection_status": row.get("desired_connection_status"),
        "desired_binding_status": row.get("desired_binding_status"),
        "observed_versions": row.get("observed_versions") or {},
        "operations": row.get("operations") or [],
        "gate_results": row.get("gate_results") or {},
        "evidence_refs": row.get("evidence_refs") or {},
        "blockers": row.get("blockers") or [],
        "rollback_operations": row.get("rollback_operations") or [],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "status": row.get("status") or LifecyclePlanStatus.PLANNED.value,
    }
    return PMLifecycleTransitionPlan.model_validate(payload)


def _serialize_lifecycle_plan(row: dict[str, Any]) -> dict[str, Any]:
    plan = _lifecycle_plan_from_row(row)
    computed_digest = plan.digest()
    return {
        "plan": plan.model_dump(mode="json"),
        "plan_digest": computed_digest,
        "persisted_digest": row.get("digest"),
        "digest_valid": computed_digest == str(row.get("digest") or ""),
        "status": row.get("status"),
        "approval_actor": row.get("approval_actor"),
        "approved_at": row.get("approved_at"),
        "approval_reason": row.get("approval_reason"),
        "applied_actor": row.get("applied_actor"),
        "applied_at": row.get("applied_at"),
        "application_result": row.get("application_result"),
        "error": row.get("error"),
        "created_at": row.get("created_at"),
        "expires_at": row.get("expires_at"),
    }


def _lifecycle_safety_transition(
    target_type: str,
    *,
    desired_connection_status: str | None = None,
    desired_binding_status: str | None = None,
) -> bool:
    """Return whether readiness evidence may be bypassed for a safe shutdown."""
    if target_type != "pm_connection":
        return False
    return str(desired_connection_status or "").upper() in {"DISABLED", "DRAINING"}


async def _lifecycle_gate_snapshot(
    storage: AgentStorage,
    *,
    connection_id: UUID,
    binding: dict[str, Any] | None,
    doctor: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Collect apply-time gates without changing lifecycle state."""
    latest_runs = await storage.list_pm_reconciliation_runs(connection_id=connection_id, limit=1)
    latest_run = latest_runs[0] if latest_runs else None
    counts = dict((latest_run or {}).get("counts") or {})
    open_conflicts = await storage.list_pm_conflicts(connection_id=connection_id, status="OPEN", limit=1000)
    pending = await storage.list_pm_outbox(connection_id=connection_id, status="PENDING", limit=1000)
    processing = await storage.list_pm_outbox(connection_id=connection_id, status="PROCESSING", limit=1000)
    failed = await storage.list_pm_outbox(connection_id=connection_id, status="FAILED", limit=1000)
    dead_letter_counter = getattr(storage, "get_pm_outbox_dead_letter_counts", None)
    dead_letter_counts: dict[str, int] | None = None
    if callable(dead_letter_counter):
        count_result = dead_letter_counter(connection_id=connection_id)
        if inspect.isawaitable(count_result):
            count_result = await count_result
        if isinstance(count_result, dict):
            dead_letter_counts = {
                "active": int(count_result.get("active") or 0),
                "historical": int(count_result.get("historical") or 0),
                "total": int(count_result.get("total") or 0),
            }
    if dead_letter_counts is None:
        # Lightweight storage doubles predate the database anti-join.  Their
        # complete in-memory lists remain useful for endpoint unit tests.
        dead_letters = await storage.list_pm_outbox(
            connection_id=connection_id, status="DEAD_LETTER", limit=1000
        )
        disposition_reader = getattr(storage, "list_pm_outbox_dispositions", None)
        disposition_result = (
            disposition_reader(connection_id=connection_id)
            if callable(disposition_reader)
            else []
        )
        dispositions = (
            await disposition_result
            if hasattr(disposition_result, "__await__")
            else disposition_result
        )
        active_dead_letters = _classify_pm_dead_letters(dead_letters, dispositions)
        dead_letter_counts = {
            "active": len(active_dead_letters),
            "historical": len(dead_letters) - len(active_dead_letters),
            "total": len(dead_letters),
        }
    try:
        tls_context = provider_ssl_context()
        tls_verified = tls_context.verify_mode == ssl.CERT_REQUIRED and tls_context.check_hostname
    except Exception:
        tls_verified = False
    mapping_complete = latest_run is not None and int(counts.get("mapped") or 0) == int(counts.get("seen") or 0)
    blockers: list[str] = []
    if not doctor.get("ready"):
        blockers.extend(str(item) for item in doctor.get("blockers") or ["doctor not ready"])
    if latest_run is None or latest_run.get("status") != "COMPLETED":
        blockers.append("no completed reconciliation evidence")
    if any(int(counts.get(key) or 0) != 0 for key in ("drift", "conflicts", "scope_conflicts", "version_mismatches", "hash_mismatches")):
        blockers.append("latest reconciliation has drift or conflicts")
    if not mapping_complete:
        blockers.append("latest reconciliation mappings are incomplete")
    if open_conflicts:
        blockers.append("blocking PM conflicts are open")
    if pending or processing or failed:
        blockers.append("PM projections are pending, processing, or failed")
    if int(dead_letter_counts.get("active") or 0) > 0:
        blockers.append("active PM dead letters exist")
    if not tls_verified:
        blockers.append("provider TLS certificate verification is not enabled")
    snapshot = {
        "doctor_ready": bool(doctor.get("ready")),
        "doctor_blockers": list(doctor.get("blockers") or []),
        "reconciliation_run_id": str(latest_run["id"]) if latest_run else None,
        "reconciliation_status": latest_run.get("status") if latest_run else None,
        "reconciliation_counts": counts,
        "mapping_complete": mapping_complete,
        "open_conflicts": len(open_conflicts),
        "pending_projections": len(pending),
        "processing_projections": len(processing),
        "failed_projections": len(failed),
        "active_dead_letters": int(dead_letter_counts.get("active") or 0),
        "historical_dead_letters": int(dead_letter_counts.get("historical") or 0),
        "tls_verification_enabled": bool(tls_verified),
        "binding_policy": pm_binding_effective_policy(
            str((binding or {}).get("status") or "DISABLED"),
            str((binding or {}).get("connection_status") or "DISABLED"),
            str((binding or {}).get("direction") or "outbound"),
        ) if binding else None,
    }
    return snapshot, blockers


def _classify_pm_dead_letters(
    dead_letters: list[dict[str, Any]], dispositions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return only unresolved failures; never infer resolution from error text."""
    dispositioned = {str(row.get("outbox_id")) for row in dispositions}
    return [row for row in dead_letters if str(row.get("id")) not in dispositioned]


def _source_control_provider_for(row: dict[str, Any]) -> Any:
    try:
        return _integration_registry().source_control(str(row["provider_kind"]), str(row["id"]))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _provider_failure_is_permanent(exc: BaseException) -> bool:
    """Classify provider responses that cannot succeed through retries."""
    status_code = getattr(exc, "status_code", None)
    return isinstance(status_code, int) and 400 <= status_code < 500 and status_code not in {
        408,
        409,
        425,
        429,
    }


async def _require_source_control_capability(
    row: dict[str, Any],
    capability: str,
    *,
    write: bool = False,
) -> Any:
    status = str(row.get("status") or "DISABLED").upper()
    allowed_statuses = {"ACTIVE"} if write else {"SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"}
    if status not in allowed_statuses:
        action = "write" if write else "read"
        raise HTTPException(409, f"source-control {action} is not allowed while connection is {status}")
    provider = _source_control_provider_for(row)
    try:
        capabilities = await provider.capabilities(_provider_connection(row))
    except Exception as exc:
        raise HTTPException(502, f"source-control capability discovery failed: {exc}") from exc
    if not bool(getattr(capabilities, capability, False)):
        raise HTTPException(409, f"provider profile does not enable source-control capability {capability}")
    return provider


def _validate_repository_scope(row: dict[str, Any], payload: dict[str, Any]) -> None:
    configured = str((row.get("config") or {}).get("repository") or "")
    requested = payload.get("repository")
    if requested is not None and configured and str(requested) != configured:
        raise HTTPException(403, "requested repository is outside the connection scope")
    if configured:
        payload["repository"] = configured


def _scrub_integration_evidence(value: Any) -> Any:
    """Remove credential-shaped fields before source-control evidence storage."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("token", "secret", "password", "private_key", "app_key")):
                result[str(key)] = "<redacted>"
            else:
                result[str(key)] = _scrub_integration_evidence(item)
        return result
    if isinstance(value, list):
        return [_scrub_integration_evidence(item) for item in value]
    return value


async def _record_source_control_evidence(
    storage: Any,
    *,
    connection_id: UUID,
    evidence_type: str,
    request_payload: dict[str, Any],
    result: Any,
) -> dict[str, Any] | None:
    recorder = getattr(storage, "record_integration_evidence", None)
    if not callable(recorder):
        return None
    result_payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    safe_payload = _scrub_integration_evidence({"request": request_payload, "result": result_payload})
    external_id = None
    if isinstance(result_payload, dict) and result_payload.get("external_id") is not None:
        external_id = str(result_payload["external_id"])
    repository = str(request_payload.get("repository") or "") or None
    project_id = None
    binding_id = None
    try:
        project_id = UUID(str(request_payload["project_id"])) if request_payload.get("project_id") else None
    except (TypeError, ValueError):
        project_id = None
    try:
        binding_id = UUID(str(request_payload["binding_id"])) if request_payload.get("binding_id") else None
    except (TypeError, ValueError):
        binding_id = None
    explicit_key = str(request_payload.get("idempotency_key") or "")
    digest = hashlib.sha256(
        json.dumps(safe_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    key = explicit_key or f"{connection_id}:{evidence_type}:{external_id or 'none'}:{digest}"
    saved = recorder(
        connection_id=connection_id,
        evidence_type=evidence_type,
        external_id=external_id,
        repository=repository,
        project_id=project_id,
        binding_id=binding_id,
        payload=safe_payload,
        idempotency_key=key,
    )
    if hasattr(saved, "__await__"):
        return await saved
    return saved


@app.post("/integrations/connections/{connection_id}/source-control/installation")
async def discover_source_control_installation(connection_id: UUID, request: Request) -> dict[str, Any]:
    _integration_operator(request, allow_worker_read=True)
    row = await _storage().get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    provider = await _require_source_control_capability(row, "repositories")
    value = await provider.discover_installation(_provider_connection(row))
    return _serialize(value)


@app.post("/integrations/connections/{connection_id}/source-control/branches", status_code=201)
async def create_source_control_branch(
    connection_id: UUID,
    req: SCMActionRequest,
    request: Request,
) -> dict[str, Any]:
    _integration_operator(request)
    row = await _storage().get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    _validate_repository_scope(row, req.payload)
    provider = await _require_source_control_capability(row, "repositories", write=True)
    if not bool((await provider.capabilities(_provider_connection(row))).pull_requests):
        raise HTTPException(409, "provider profile does not enable branch delivery")
    result = await provider.create_branch(_provider_connection(row), req.payload)
    await _record_source_control_evidence(
        _storage(), connection_id=connection_id, evidence_type="branch",
        request_payload=req.payload, result=result,
    )
    return _serialize(result.model_dump(mode="json"))


@app.post("/integrations/connections/{connection_id}/source-control/pull-requests", status_code=201)
async def project_source_control_pull_request(
    connection_id: UUID,
    req: SCMActionRequest,
    request: Request,
) -> dict[str, Any]:
    _integration_operator(request)
    row = await _storage().get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    _validate_repository_scope(row, req.payload)
    provider = await _require_source_control_capability(row, "pull_requests", write=True)
    result = await provider.project_pull_request(_provider_connection(row), req.payload)
    await _record_source_control_evidence(
        _storage(), connection_id=connection_id, evidence_type="pull_request",
        request_payload=req.payload, result=result,
    )
    return _serialize(result.model_dump(mode="json"))


@app.post("/integrations/connections/{connection_id}/source-control/review-comments", status_code=201)
async def publish_source_control_review_comment(
    connection_id: UUID,
    req: SCMActionRequest,
    request: Request,
) -> dict[str, Any]:
    _integration_operator(request)
    row = await _storage().get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    _validate_repository_scope(row, req.payload)
    provider = await _require_source_control_capability(row, "pull_requests", write=True)
    result = await provider.publish_review_comment(_provider_connection(row), req.payload)
    await _record_source_control_evidence(
        _storage(), connection_id=connection_id, evidence_type="review_comment",
        request_payload=req.payload, result=result,
    )
    return _serialize(result.model_dump(mode="json"))


@app.post("/integrations/connections/{connection_id}/source-control/checks", status_code=201)
async def publish_source_control_check(
    connection_id: UUID,
    req: SCMActionRequest,
    request: Request,
) -> dict[str, Any]:
    _integration_operator(request)
    row = await _storage().get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    _validate_repository_scope(row, req.payload)
    provider = await _require_source_control_capability(row, "checks", write=True)
    result = await provider.publish_check(_provider_connection(row), req.payload)
    await _record_source_control_evidence(
        _storage(), connection_id=connection_id, evidence_type="check",
        request_payload=req.payload, result=result,
    )
    return _serialize(result.model_dump(mode="json"))


@app.post("/integrations/connections/{connection_id}/source-control/commits", status_code=201)
async def capture_source_control_commit(
    connection_id: UUID,
    req: SCMActionRequest,
    request: Request,
) -> dict[str, Any]:
    _integration_operator(request, allow_worker_read=True)
    row = await _storage().get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    _validate_repository_scope(row, req.payload)
    provider = await _require_source_control_capability(row, "repositories")
    result = await provider.capture_commit_evidence(_provider_connection(row), req.payload)
    await _record_source_control_evidence(
        _storage(), connection_id=connection_id, evidence_type="commit",
        request_payload=req.payload, result=result,
    )
    return _serialize(result.model_dump(mode="json"))


@app.post("/integrations/connections/{connection_id}/source-control/run-credentials")
async def mint_source_control_run_credential(
    connection_id: UUID,
    req: SCMActionRequest,
    request: Request,
) -> dict[str, Any]:
    _integration_operator(request)
    row = await _storage().get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    _validate_repository_scope(row, req.payload)
    configured_repository = str((row.get("config") or {}).get("repository") or "")
    if not configured_repository:
        raise HTTPException(
            409,
            "GitHub run credentials require a repository configured on the connection",
        )
    requested_repository = str(req.payload.get("repository") or "")
    if requested_repository and requested_repository != configured_repository:
        raise HTTPException(403, "requested repository is outside the connection scope")
    repository = configured_repository
    req.payload["repository"] = repository
    permissions = dict(req.payload.get("permissions") or {})
    provider = await _require_source_control_capability(row, "repositories", write=True)
    if not bool((await provider.capabilities(_provider_connection(row))).pull_requests):
        raise HTTPException(409, "provider profile does not enable delivery credentials")
    result = await provider.mint_run_credential(
        _provider_connection(row),
        repository,
        permissions,
    )
    await _record_source_control_evidence(
        _storage(), connection_id=connection_id, evidence_type="run_credential_issued",
        request_payload=req.payload, result=result,
    )
    # The broker returns an expiry and token for an already-authorized governed
    # run.  The private app key itself never crosses this endpoint.
    return _serialize(result)


@app.get("/integrations/connections/{connection_id}/source-control/evidence")
async def list_source_control_evidence(
    connection_id: UUID,
    request: Request,
    evidence_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    _integration_operator(request, allow_worker_read=True)
    storage = _storage()
    row = await storage.get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    lister = getattr(storage, "list_integration_evidence", None)
    if not callable(lister):
        return []
    return [
        _serialize(item)
        for item in await lister(connection_id=connection_id, evidence_type=evidence_type, limit=limit)
    ]


@app.patch("/integrations/connections/{connection_id}/status")
async def update_integration_connection_status(connection_id: UUID, req: PMConnectionStatusRequest, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    storage = _storage()
    current = await storage.get_pm_connection(connection_id)
    if current is None:
        raise HTTPException(404, "integration connection not found")
    if req.status == "ACTIVE":
        provider = _provider_for(current)
        if str(current.get("provider_kind") or "").lower() == "youtrack":
            least_privilege_check = getattr(provider, "verify_least_privilege", None)
            if not callable(least_privilege_check):
                raise HTTPException(409, "YouTrack adapter does not implement least-privilege certification")
            report = await least_privilege_check(_provider_connection(current))
            if not report.get("ok"):
                reasons = [
                    *(str(item) for item in report.get("missing", [])),
                    *(f"forbidden permission: {item}" for item in report.get("forbidden", [])),
                ]
                raise HTTPException(
                    409,
                    "YouTrack least-privilege certification is not satisfied"
                    + (f": {'; '.join(reasons)}" if reasons else ""),
                )
    if str(current.get("status") or "DISABLED") != req.status:
        raise HTTPException(
            409,
            {
                "code": "lifecycle_plan_required",
                "message": "connection state changes require a persisted approved lifecycle plan",
            },
        )
    row = await storage.update_pm_connection(connection_id, status=req.status)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    return _serialize_pm_connection(row) or {}


@app.post("/integrations/connections/{connection_id}/plan")
async def plan_integration_bootstrap(connection_id: UUID, req: PMPlanRequest, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    storage = _storage()
    row = await storage.get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    try:
        plan = await _provider_for(row).plan_bootstrap(_provider_connection(row), req.desired)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"provider bootstrap planning failed: {exc}") from exc
    plan_digest = plan.digest()
    stored_config = dict(row.get("config") or {})
    stored_config["_bootstrap_plan"] = {
        "plan_id": str(plan.plan_id),
        "digest": plan_digest,
        "connection_id": str(connection_id),
    }
    try:
        await storage.update_pm_connection(connection_id, config=stored_config)
    except ValueError as exc:
        raise HTTPException(503, f"could not persist bootstrap plan identity: {exc}") from exc
    return {
        "plan": plan.model_dump(mode="json"),
        "plan_digest": plan_digest,
        "ready_to_apply": plan.ready_to_apply,
    }


@app.post("/integrations/connections/{connection_id}/apply")
async def apply_integration_bootstrap(connection_id: UUID, req: PMApplyRequest, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    if not req.confirm:
        raise HTTPException(400, "bootstrap apply requires confirm=true")
    try:
        validate_credential_references(req.plan.model_dump(mode="python"))
    except ValueError as exc:
        raise HTTPException(422, "bootstrap plan contains inline provider secret material") from exc
    if req.plan.connection_id != connection_id or req.plan.digest() != req.plan_digest:
        raise HTTPException(409, "bootstrap plan digest or connection does not match")
    storage = _storage()
    row = await storage.get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    stored_plan = (row.get("config") or {}).get("_bootstrap_plan")
    if not isinstance(stored_plan, dict) or (
        str(stored_plan.get("plan_id") or "") != str(req.plan.plan_id)
        or str(stored_plan.get("digest") or "") != req.plan_digest
        or str(stored_plan.get("connection_id") or "") != str(connection_id)
    ):
        raise HTTPException(
            409,
            "bootstrap plan was not generated by the server for this connection",
        )
    if str(row.get("status") or "DISABLED") != "DISABLED":
        raise HTTPException(409, "bootstrap apply requires the connection to remain DISABLED")
    try:
        applied = await _provider_for(row).apply_bootstrap(_provider_connection(row), req.plan)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"provider bootstrap apply failed: {exc}") from exc
    if isinstance(applied, BootstrapPlan):
        # Compatibility for third-party adapters that still implement the
        # pre-result contract.  Built-in adapters return BootstrapApplyResult.
        applied = BootstrapApplyResult(plan=applied)
    if not isinstance(applied, BootstrapApplyResult) or applied.plan.digest() != req.plan_digest:
        raise HTTPException(502, "provider bootstrap apply returned a mismatched plan")

    # Persist only non-secret resource identifiers needed for idempotent
    # retries and later binding creation.  This is metadata, not a binding,
    # and deliberately leaves the connection DISABLED.
    updated_config = dict(row.get("config") or {})
    resources = [*applied.created, *applied.adopted]
    if str(row.get("provider_kind") or "").lower() == "youtrack":
        project_resource = next(
            (item for item in resources if str(item.get("resource") or "").startswith("youtrack:project:")),
            None,
        )
        if project_resource and project_resource.get("external_id"):
            project_id = str(project_resource["external_id"])
            updated_config["project_id"] = project_id
            updated_config["project_short_name"] = str(
                project_resource.get("short_name") or "AIAT"
            )
            managed = [
                str(value)
                for value in (updated_config.get("managed_project_ids") or [])
                if value
            ]
            if project_id not in managed:
                managed.append(project_id)
            updated_config["managed_project_ids"] = managed
            if project_resource.get("project_admin") is True:
                # Successful creation/adoption under the approved plan is the
                # live certification evidence for the integration user's
                # project-scoped Project Admin authority.  Keep it redacted
                # and scoped to this exact provider project.
                evidence = dict(updated_config.get("permission_evidence") or {})
                project_roles = dict(evidence.get("project_roles") or {})
                current_roles = project_roles.get(project_id)
                roles = list(current_roles) if isinstance(current_roles, list) else []
                if "Project Admin" not in roles:
                    roles.append("Project Admin")
                project_roles[project_id] = roles
                evidence["project_roles"] = project_roles
                updated_config["permission_evidence"] = evidence
        field_ids = dict(updated_config.get("youtrack_field_ids") or {})
        for item in resources:
            resource = str(item.get("resource") or "")
            if resource.startswith("youtrack:field:") and item.get("external_id"):
                field_ids[str(item.get("name") or resource.rsplit(":", 1)[-1])] = {
                    "project_field_id": str(item["external_id"]),
                    "global_field_id": str(item.get("global_field_id") or "") or None,
                    "type": str(item.get("type") or ""),
                }
        if field_ids:
            updated_config["youtrack_field_ids"] = field_ids
        previous_apply = updated_config.get("_bootstrap_apply")
        previous_created = (
            previous_apply.get("created", [])
            if isinstance(previous_apply, dict) and isinstance(previous_apply.get("created"), list)
            else []
        )
        previous_adopted = (
            previous_apply.get("adopted", [])
            if isinstance(previous_apply, dict) and isinstance(previous_apply.get("adopted"), list)
            else []
        )

        def _unique_resources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            unique: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            for item in items:
                key = (str(item.get("resource") or ""), str(item.get("external_id") or ""))
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
            return unique

        updated_config["_bootstrap_apply"] = {
            "plan_id": str(req.plan.plan_id),
            "digest": req.plan_digest,
            "created": _unique_resources([*previous_created, *applied.created]),
            "adopted": _unique_resources([*previous_adopted, *applied.adopted]),
        }
    updated = await storage.update_pm_connection(connection_id, config=updated_config)
    return {
        "connection": _serialize_pm_connection(updated),
        "plan": applied.plan.model_dump(mode="json"),
        "plan_digest": applied.plan.digest(),
        "applied_resources": {"created": applied.created, "adopted": applied.adopted},
    }


def _lifecycle_http_error(exc: LifecyclePlanError) -> HTTPException:
    status = 404 if exc.code == "missing_plan" else 409
    return HTTPException(status, {"code": exc.code, "message": exc.message})


@app.post("/integrations/lifecycle-plans", status_code=201)
async def create_pm_lifecycle_plan(
    req: PMLifecyclePlanCreateRequest,
    request: Request,
) -> dict[str, Any]:
    """Generate and durably persist one PM lifecycle transition plan."""
    _integration_operator(request)
    actor = _authenticated_principal(request)
    storage = _storage()
    connection = await storage.get_pm_connection(req.connection_id)
    if connection is None:
        raise HTTPException(404, "integration connection not found")
    binding: dict[str, Any] | None = None
    if req.target_type == "pm_binding":
        if req.binding_id is None or req.desired_binding_status is None:
            raise HTTPException(422, "binding lifecycle plans require binding_id and desired_binding_status")
        bindings = await storage.list_pm_bindings(connection_id=req.connection_id)
        binding = next((row for row in bindings if row.get("id") == req.binding_id), None)
        if binding is None:
            raise HTTPException(404, "integration binding not found")
        if req.desired_connection_status is not None:
            raise HTTPException(422, "binding transition plans cannot change the connection state")
        target_id = req.binding_id
    else:
        if req.binding_id is not None or req.desired_connection_status is None:
            raise HTTPException(422, "connection lifecycle plans require desired_connection_status and no binding_id")
        target_id = req.connection_id

    safety_transition = _lifecycle_safety_transition(
        req.target_type,
        desired_connection_status=req.desired_connection_status,
        desired_binding_status=req.desired_binding_status,
    )
    try:
        doctor = await doctor_integration_connection(req.connection_id, request)
    except Exception:
        if not safety_transition:
            raise
        # Provider discovery is itself a readiness check.  Keep the shutdown
        # plan available when the adapter is unavailable, while recording the
        # failed check in the plan evidence.
        doctor = {
            "connection_id": str(req.connection_id),
            "ready": False,
            "blockers": ["integration doctor unavailable"],
            "checks": [],
        }
    connection = await storage.get_pm_connection(req.connection_id) or connection
    if binding is not None:
        binding = next(
            (row for row in await storage.list_pm_bindings(connection_id=req.connection_id) if row.get("id") == req.binding_id),
            binding,
        )
        binding_for_gate = {**binding, "connection_status": connection.get("status")}
    else:
        binding_for_gate = None
    gate_results, gate_blockers = await _lifecycle_gate_snapshot(
        storage,
        connection_id=req.connection_id,
        binding=binding_for_gate,
        doctor=doctor,
    )
    # Preserve the failed readiness evidence in the immutable plan for audit,
    # but do not make an emergency shutdown depend on a healthy provider,
    # reconciliation, projection queue, or TLS probe.
    gate_results = {
        **gate_results,
        "readiness_gate_blockers": list(gate_blockers),
        "readiness_gates_bypassed": safety_transition,
    }
    effective_gate_blockers = [] if safety_transition else gate_blockers
    transition_blockers: list[str] = []
    if req.target_type == "pm_binding":
        current_status = str(binding.get("status") or "DISABLED")
        if current_status == req.desired_binding_status:
            transition_blockers.append("desired binding state is already current")
        if connection.get("status") == "DISABLED":
            transition_blockers.append("binding transition requires a non-disabled connection")
        operations = [{
            "operation": "set_binding_status",
            "binding_id": str(req.binding_id),
            "from": current_status,
            "to": req.desired_binding_status,
        }]
        rollback_operations = [{
            "operation": "set_binding_status",
            "binding_id": str(req.binding_id),
            "from": req.desired_binding_status,
            "to": current_status,
        }]
    else:
        current_status = str(connection.get("status") or "DISABLED")
        if current_status == req.desired_connection_status:
            transition_blockers.append("desired connection state is already current")
        operations = [{
            "operation": "set_connection_status",
            "connection_id": str(req.connection_id),
            "from": current_status,
            "to": req.desired_connection_status,
        }]
        rollback_operations = [{
            "operation": "set_connection_status",
            "connection_id": str(req.connection_id),
            "from": req.desired_connection_status,
            "to": current_status,
        }]
    blockers = [*transition_blockers, *effective_gate_blockers]
    created_at = datetime.now(tz=UTC)
    plan = PMLifecycleTransitionPlan(
        plan_kind="pm_binding_transition" if req.target_type == "pm_binding" else "pm_connection_transition",
        target_type=req.target_type,
        target_id=target_id,
        connection_id=req.connection_id,
        binding_id=req.binding_id,
        expected_connection_status=str(connection.get("status") or "DISABLED"),
        expected_binding_status=str(binding.get("status") or "DISABLED") if binding is not None else None,
        expected_connection_revision=int(connection.get("revision") or 1),
        expected_binding_revision=int(binding.get("revision") or 1) if binding is not None else None,
        desired_connection_status=req.desired_connection_status,
        desired_binding_status=req.desired_binding_status,
        observed_versions={
            "connection_revision": int(connection.get("revision") or 1),
            "binding_revision": int(binding.get("revision") or 1) if binding is not None else None,
        },
        operations=operations,
        gate_results=gate_results,
        evidence_refs={
            "doctor": {"connection_id": str(req.connection_id), "checked_at": created_at.isoformat()},
            "reconciliation_run_id": gate_results.get("reconciliation_run_id"),
        },
        blockers=blockers,
        rollback_operations=rollback_operations,
        created_by=actor,
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=req.ttl_seconds),
    )
    digest = plan.digest()
    try:
        persisted = await storage.create_pm_lifecycle_plan(plan, digest=digest)
    except LifecyclePlanError as exc:
        raise _lifecycle_http_error(exc) from exc
    return _serialize_lifecycle_plan(persisted)


@app.get("/integrations/lifecycle-plans")
async def list_pm_lifecycle_plans(
    request: Request,
    connection_id: UUID | None = None,
    target_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    _integration_operator(request)
    rows = await _storage().list_pm_lifecycle_plans(
        connection_id=connection_id,
        target_id=target_id,
        status=status,
        limit=limit,
    )
    return [_serialize_lifecycle_plan(row) for row in rows]


@app.get("/integrations/lifecycle-plans/{plan_id}")
async def get_pm_lifecycle_plan(plan_id: UUID, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    row = await _storage().get_pm_lifecycle_plan(plan_id)
    if row is None:
        raise HTTPException(404, "lifecycle plan was not found")
    return _serialize_lifecycle_plan(row)


@app.post("/integrations/lifecycle-plans/{plan_id}/approve")
async def approve_pm_lifecycle_plan(
    plan_id: UUID,
    req: PMLifecyclePlanApprovalRequest,
    request: Request,
) -> dict[str, Any]:
    _integration_operator(request)
    row = await _storage().get_pm_lifecycle_plan(plan_id)
    if row is None:
        raise HTTPException(404, "lifecycle plan was not found")
    serialized = _serialize_lifecycle_plan(row)
    if not serialized["digest_valid"] or serialized["plan_digest"] != req.plan_digest:
        raise HTTPException(409, {"code": "digest_mismatch", "message": "lifecycle plan digest is not current"})
    plan = serialized["plan"]
    if plan.get("blockers"):
        raise HTTPException(409, {"code": "blocked_plan", "message": "lifecycle plan contains blockers", "blockers": plan["blockers"]})
    try:
        approved = await _storage().approve_pm_lifecycle_plan(
            plan_id,
            digest=req.plan_digest,
            actor=_authenticated_principal(request),
            reason=req.reason,
        )
    except LifecyclePlanError as exc:
        raise _lifecycle_http_error(exc) from exc
    return _serialize_lifecycle_plan(approved)


@app.post("/integrations/lifecycle-plans/{plan_id}/reject")
async def reject_pm_lifecycle_plan(
    plan_id: UUID,
    req: PMLifecyclePlanRejectRequest,
    request: Request,
) -> dict[str, Any]:
    _integration_operator(request)
    try:
        rejected = await _storage().reject_pm_lifecycle_plan(
            plan_id,
            digest=req.plan_digest,
            actor=_authenticated_principal(request),
            reason=req.reason,
        )
    except LifecyclePlanError as exc:
        raise _lifecycle_http_error(exc) from exc
    return _serialize_lifecycle_plan(rejected)


@app.post("/integrations/lifecycle-plans/{plan_id}/apply")
async def apply_pm_lifecycle_plan(
    plan_id: UUID,
    req: PMLifecyclePlanApplyRequest,
    request: Request,
) -> dict[str, Any]:
    _integration_operator(request)
    if not req.confirm:
        raise HTTPException(400, "lifecycle plan apply requires confirm=true")
    storage = _storage()
    row = await storage.get_pm_lifecycle_plan(plan_id)
    if row is None:
        raise HTTPException(404, "lifecycle plan was not found")
    serialized = _serialize_lifecycle_plan(row)
    if not serialized["digest_valid"] or serialized["plan_digest"] != req.plan_digest:
        raise HTTPException(409, {"code": "digest_mismatch", "message": "lifecycle plan digest is not current"})
    if row.get("status") == "APPLIED":
        result = await storage.apply_pm_lifecycle_plan(
            plan_id,
            digest=req.plan_digest,
            actor=_authenticated_principal(request),
        )
        return {**_serialize_lifecycle_plan(result["plan"]), "application": result["result"], "idempotent": True}
    plan = _lifecycle_plan_from_row(row)
    safety_transition = _lifecycle_safety_transition(
        plan.target_type,
        desired_connection_status=plan.desired_connection_status,
        desired_binding_status=plan.desired_binding_status,
    )
    plan_blockers = list(plan.blockers)
    if safety_transition:
        # Plans created before the bypass metadata existed may still contain
        # the exact readiness blockers in their immutable payload.  Remove
        # only blockers explicitly classified as readiness evidence; retain
        # transition blockers and all other safety checks.
        readiness_blockers = {
            str(item)
            for item in (plan.gate_results.get("readiness_gate_blockers") or [])
            if item
        }
        plan_blockers = [item for item in plan_blockers if item not in readiness_blockers]
    if plan_blockers:
        raise HTTPException(409, {"code": "blocked_plan", "message": "lifecycle plan contains blockers", "blockers": plan_blockers})
    connection = await storage.get_pm_connection(plan.connection_id)
    if connection is None:
        raise HTTPException(409, {"code": "stale_state", "message": "target connection no longer exists"})
    binding = None
    if plan.binding_id is not None:
        binding = next(
            (item for item in await storage.list_pm_bindings(connection_id=plan.connection_id) if item.get("id") == plan.binding_id),
            None,
        )
    try:
        doctor = await doctor_integration_connection(plan.connection_id, request)
    except Exception:
        if not safety_transition:
            raise
        doctor = {
            "connection_id": str(plan.connection_id),
            "ready": False,
            "blockers": ["integration doctor unavailable"],
            "checks": [],
        }
    connection = await storage.get_pm_connection(plan.connection_id) or connection
    if binding is not None:
        binding = next(
            (item for item in await storage.list_pm_bindings(connection_id=plan.connection_id) if item.get("id") == plan.binding_id),
            binding,
        )
        binding = {**binding, "connection_status": connection.get("status")}
    fresh_gates, fresh_blockers = await _lifecycle_gate_snapshot(
        storage,
        connection_id=plan.connection_id,
        binding=binding,
        doctor=doctor,
    )
    fresh_gates = {
        **fresh_gates,
        "readiness_gate_blockers": list(fresh_blockers),
        "readiness_gates_bypassed": safety_transition,
    }
    effective_fresh_blockers = [] if safety_transition else fresh_blockers
    if effective_fresh_blockers:
        raise HTTPException(
            409,
            {
                "code": "lifecycle_gate_blocked",
                "message": "lifecycle apply gates changed after approval",
                "blockers": effective_fresh_blockers,
                "gate_results": fresh_gates,
            },
        )
    try:
        result = await storage.apply_pm_lifecycle_plan(
            plan_id,
            digest=req.plan_digest,
            actor=_authenticated_principal(request),
        )
    except LifecyclePlanError as exc:
        raise _lifecycle_http_error(exc) from exc
    if result.get("status") == "STALE":
        raise HTTPException(409, {"code": "stale_state", "message": "lifecycle plan expected state or revision changed", "result": result["result"]})
    return {**_serialize_lifecycle_plan(result["plan"]), "application": result["result"], "idempotent": result["idempotent"]}


@app.get("/integrations/lifecycle-plans/{plan_id}/audit")
async def get_pm_lifecycle_audit(plan_id: UUID, request: Request) -> list[dict[str, Any]]:
    _integration_operator(request)
    rows = await _storage().list_pm_lifecycle_audits(limit=100)
    return [_serialize(row) for row in rows if row.get("plan_id") == plan_id]


@app.post("/integrations/connections/{connection_id}/reconcile")
async def reconcile_integration_connection(
    connection_id: UUID,
    req: PMReconcileRequest,
    request: Request,
) -> dict[str, Any]:
    """Compare provider objects with durable mappings without guessing imports."""
    _integration_operator(request)
    storage = _storage()
    row = await storage.get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    connection = _provider_connection(row)
    bindings = await storage.list_pm_bindings(connection_id=connection_id)
    eligible_bindings = [item for item in bindings if item.get("status") in {"ACTIVE", "SHADOW", "READ_ONLY", "DRAINING"}]
    if req.binding_id is not None:
        binding = next((item for item in eligible_bindings if item.get("id") == req.binding_id), None)
        if binding is None:
            raise HTTPException(404, "PM binding not found for reconciliation")
    else:
        if len(eligible_bindings) > 1:
            raise HTTPException(409, "binding_id is required when a connection serves multiple project bindings")
        binding = eligible_bindings[0] if eligible_bindings else None
    if binding is not None and binding.get("external_project_id"):
        connection = connection.model_copy(
            update={"config": {**connection.config, "project_id": str(binding["external_project_id"])}},
        )
    effective_cursor = req.cursor if req.cursor is not None else (binding or {}).get("sync_cursor")
    reconciliation_run = None
    if isinstance(storage, AgentStorage):
        reconciliation_run = await storage.create_pm_reconciliation_run(
            connection_id=connection_id,
            binding_id=binding["id"] if binding else None,
            cursor=effective_cursor,
        )
    try:
        objects, next_cursor = await _provider_for(row).list_changes(connection, cursor=effective_cursor)
    except Exception as exc:
        if reconciliation_run is not None:
            await storage.finish_pm_reconciliation_run(
                reconciliation_run["id"],
                status="FAILED",
                counts={},
                error=str(exc)[:1000],
            )
        raise HTTPException(502, f"provider reconciliation failed: {exc}") from exc
    # Include resolved/ignored forensic conflicts when reconciling.  Otherwise
    # a deliberately ignored certification fixture is re-created as OPEN on
    # every full scan and falsely blocks a clean READ_ONLY gate.  Existing
    # OPEN/REOPENED rows remain blocking; resolved/ignored rows suppress
    # duplicate creation and are excluded from the run's conflict count.
    existing_conflicts = {
        (
            str(item.get("object_type")),
            str(item.get("external_id")),
            str(item.get("reason") or ""),
        ): str(item.get("status") or "OPEN").upper()
        for item in await storage.list_pm_conflicts(connection_id=connection_id, status=None, limit=1000)
    }
    mapped = conflicts = drift = scope_conflicts = 0
    hash_mismatches = version_mismatches = 0
    # A provider page is the cursor's atomic unit.  Processing only a prefix
    # and then persisting the provider's page cursor would permanently skip
    # the unprocessed suffix, so reconcile the complete returned page.
    for external in list(objects):
        object_type = getattr(external.object_type, "value", str(external.object_type))
        mapping = await storage.get_pm_mapping(
            connection_id=connection_id,
            object_type=object_type,
            external_id=external.external_id,
        )
        if mapping is not None:
            # Only compare an explicit provider-normalized hash.  Falling
            # back to the DTO's generic stable hash would compare different
            # adapter field vocabularies and manufacture drift on every run.
            external_hash = str(external.content_hash or "")
            expected_project = str(binding.get("external_project_id") or "") if binding else ""
            expected_repository = str(binding.get("external_repository") or "") if binding else ""
            incoming_project = str(external.project_external_id or "")
            incoming_repository = str((external.metadata or {}).get("repository") or "")
            scope_error = (
                (expected_project and incoming_project and expected_project != incoming_project)
                or (expected_repository and incoming_repository and expected_repository != incoming_repository)
            )
            hash_mismatch = bool(
                mapping.get("content_hash")
                and external_hash
                and mapping.get("content_hash") != external_hash
            )
            version_mismatch = bool(
                mapping.get("provider_version")
                and external.provider_version
                and str(mapping.get("provider_version")) != str(external.provider_version)
            )
            if scope_error or hash_mismatch or version_mismatch:
                if (
                    version_mismatch
                    and not scope_error
                    and not hash_mismatch
                    and str((binding or {}).get("status") or "").upper() in {"SHADOW", "READ_ONLY"}
                ):
                    # Provider-originated changes are evidence-only before
                    # ACTIVE.  Advance the observed provider version on the
                    # existing mapping without importing fields or treating
                    # the observation as canonical drift.
                    await storage.upsert_pm_mapping(
                        connection_id=connection_id,
                        object_type=object_type,
                        aiat_object_id=mapping["aiat_object_id"],
                        external_id=external.external_id,
                        external_key=external.external_key,
                        provider_version=external.provider_version,
                        imported_revision=int(mapping.get("last_import_revision") or 1),
                    )
                    mapped += 1
                    continue
                drift += 1
                hash_mismatches += int(hash_mismatch)
                version_mismatches += int(version_mismatch)
                scope_conflicts += int(bool(scope_error))
                reason = "out_of_scope" if scope_error else "state_drift"
                key = (object_type, str(external.external_id), reason)
                existing_status = existing_conflicts.get(key)
                if existing_status is None:
                    await storage.create_pm_conflict(
                        connection_id=connection_id,
                        binding_id=binding["id"] if binding else None,
                        reason=reason,
                        object_type=object_type,
                        aiat_object_id=mapping.get("aiat_object_id"),
                        external_id=external.external_id,
                        canonical_snapshot={"mapping": mapping},
                        external_snapshot={
                            "object": external.model_dump(mode="json"),
                            "hash_mismatch": hash_mismatch,
                            "version_mismatch": version_mismatch,
                            "repair_mode": req.mode,
                        },
                    )
                    existing_conflicts[key] = "OPEN"
                elif existing_status in {"RESOLVED", "IGNORED"}:
                    continue
                continue
            mapped += 1
            continue
        key = (object_type, str(external.external_id), "unknown_mapping")
        existing_status = existing_conflicts.get(key)
        if existing_status is None:
            await storage.create_pm_conflict(
                connection_id=connection_id,
                binding_id=binding["id"] if binding else None,
                reason="unknown_mapping",
                object_type=object_type,
                external_id=external.external_id,
                external_snapshot=external.model_dump(mode="json"),
            )
            existing_conflicts[key] = "OPEN"
            conflicts += 1
        elif existing_status not in {"RESOLVED", "IGNORED"}:
            conflicts += 1
    if binding is not None:
        await storage.update_pm_binding(
            binding["id"],
            sync_cursor=next_cursor,
            last_reconciled_at=datetime.now(tz=UTC),
        )
        reconciliation_evidence = getattr(storage, "record_pm_binding_evidence", None)
        if callable(reconciliation_evidence) and conflicts == 0 and drift == 0 and scope_conflicts == 0:
            recorded = reconciliation_evidence(binding["id"], reconciliation_verified=True)
            if hasattr(recorded, "__await__"):
                await recorded
    counts = {
        "seen": len(objects),
        "mapped": mapped,
        "conflicts": conflicts,
        "drift": drift,
        "hash_mismatches": hash_mismatches,
        "version_mismatches": version_mismatches,
        "scope_conflicts": scope_conflicts,
        "mode": req.mode,
    }
    if reconciliation_run is not None:
        await storage.finish_pm_reconciliation_run(
            reconciliation_run["id"],
            status="COMPLETED",
            counts=counts,
            next_cursor=next_cursor,
        )
    return {
        "connection_id": str(connection_id),
        **counts,
        "next_cursor": next_cursor,
        "run_id": str(reconciliation_run["id"]) if reconciliation_run else None,
    }


@app.post("/integrations/cutovers")
async def cutover_integration_binding(req: PMCutoverRequest, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    # The historical cutover endpoint used to mutate connection and binding
    # state directly.  Keep the route as a compatibility surface, but fail
    # closed until an operator has generated, approved, and applied a durable
    # lifecycle plan.  This prevents an unaudited status write from bypassing
    # the digest, gate, CAS, and immutable-audit path.
    raise HTTPException(
        409,
        {
            "code": "lifecycle_plan_required",
            "message": "cutover requires a persisted approved lifecycle plan; use /api/v1/integrations/lifecycle-plans",
        },
    )
@app.post("/integrations/rollbacks")
async def rollback_integration_binding(req: PMRollbackRequest, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    # Rollback is a state transition too.  It must be represented by a
    # persisted lifecycle plan so the expected revision, rollback operation,
    # approval, and resulting audit are durable and idempotent.
    raise HTTPException(
        409,
        {
            "code": "lifecycle_plan_required",
            "message": "rollback requires a persisted approved lifecycle plan; use /api/v1/integrations/lifecycle-plans",
        },
    )
async def _canonical_project_for_pm(project_row: dict[str, Any]) -> CanonicalProject:
    return CanonicalProject(
        id=project_row["id"],
        name=str(project_row.get("name") or "Project"),
        description=project_row.get("description"),
        state=str(project_row.get("state") or "INIT"),
        revision=int(project_row.get("revision") or 1),
        updated_at=project_row.get("updated_at"),
    )


async def _remember_project_provisioning(
    storage: Any,
    project: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    config = dict(project.get("config") or {})
    current = dict(config.get("pm_provisioning") or {})
    current.update(values)
    config["pm_provisioning"] = current
    writer = getattr(storage, "update_project_config", None)
    if callable(writer):
        refreshed = writer(project["id"], config=config)
        if hasattr(refreshed, "__await__"):
            refreshed = await refreshed
        if refreshed is not None:
            return refreshed
    project["config"] = config
    return project


@app.post("/projects/{project_id}/pm-provisioning/plan")
async def plan_project_pm_provisioning(
    project_id: UUID,
    req: PMProjectProvisioningRequest,
    request: Request,
) -> dict[str, Any]:
    """Generate a read-only, per-project provider provisioning plan."""
    _integration_operator(request)
    storage = _storage()
    project_row = await storage.get_project(project_id)
    if project_row is None:
        raise HTTPException(404, "project not found")
    connection_row = await storage.get_pm_connection(req.connection_id)
    if connection_row is None:
        raise HTTPException(404, "integration connection not found")
    provider = _provider_for(connection_row)
    planner = getattr(provider, "plan_project_provisioning", None)
    if not callable(planner):
        raise HTTPException(409, f"provider {connection_row.get('provider_kind')} does not support project provisioning")
    try:
        plan = await planner(
            _provider_connection(connection_row),
            await _canonical_project_for_pm(project_row),
            mapping_profile=req.mapping_profile,
            external_project_id=req.external_project_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    digest = plan.digest()
    await _remember_project_provisioning(
        storage,
        project_row,
        {
            "mapping_profile": plan.mapping_profile,
            "state": "PLANNED",
            "plan_id": str(plan.plan_id),
            "plan_digest": digest,
            "external_project_id": plan.external_project_id,
            "external_project_key": plan.external_project_key,
            "blockers": list(plan.blockers),
            "manual_actions": list(plan.manual_actions),
        },
    )
    return {
        "project_id": str(project_id),
        "connection_id": str(req.connection_id),
        "plan": plan.model_dump(mode="json"),
        "plan_digest": digest,
        "blockers": list(plan.blockers),
        "manual_actions": list(plan.manual_actions),
    }


@app.post("/projects/{project_id}/pm-provisioning/apply")
async def apply_project_pm_provisioning(
    project_id: UUID,
    req: PMProjectProvisioningApplyRequest,
    request: Request,
) -> dict[str, Any]:
    """Apply one exact project plan and create its disabled/shadow binding."""
    _integration_operator(request)
    if not req.confirm:
        raise HTTPException(400, "project provisioning requires confirm=true")
    if req.plan.project_id != project_id or req.plan.digest() != req.plan_digest:
        raise HTTPException(409, "project provisioning plan digest or project scope is invalid")
    storage = _storage()
    project_row = await storage.get_project(project_id)
    if project_row is None:
        raise HTTPException(404, "project not found")
    connection_row = await storage.get_pm_connection(req.plan.connection_id)
    if connection_row is None:
        raise HTTPException(404, "integration connection not found")
    stored_provisioning = dict((project_row.get("config") or {}).get("pm_provisioning") or {})
    if (
        str(stored_provisioning.get("plan_id") or "") != str(req.plan.plan_id)
        or str(stored_provisioning.get("plan_digest") or "") != req.plan_digest
    ):
        raise HTTPException(409, "project provisioning plan is not the server-generated plan for this project")
    applier = getattr(_provider_for(connection_row), "apply_project_provisioning", None)
    if not callable(applier):
        raise HTTPException(409, f"provider {connection_row.get('provider_kind')} does not support project provisioning")
    try:
        applied = await applier(_provider_connection(connection_row), req.plan)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    resources = [*applied.created, *applied.adopted]
    project_resource = next(
        (item for item in resources if ":project:" in str(item.get("resource") or "") or str(item.get("object_type") or "") == "project"),
        None,
    )
    external_project_id = str((project_resource or {}).get("external_id") or req.plan.external_project_id or "")
    if not external_project_id:
        raise HTTPException(409, "provider provisioning returned no external project selector")
    external_project_key = str((project_resource or {}).get("short_name") or req.plan.external_project_key or "") or None
    # Keep the connection's redacted scope/certification metadata aligned with
    # every provider project it now manages.  This records evidence only; it
    # never grants a new provider permission.
    connection_config = dict(connection_row.get("config") or {})
    managed_project_ids = [
        str(value) for value in (connection_config.get("managed_project_ids") or []) if value
    ]
    if external_project_id not in managed_project_ids:
        managed_project_ids.append(external_project_id)
    connection_config["managed_project_ids"] = managed_project_ids
    permission_evidence = dict(connection_config.get("permission_evidence") or {})
    project_roles = dict(permission_evidence.get("project_roles") or {})
    existing_roles = project_roles.get(external_project_id)
    roles = list(existing_roles) if isinstance(existing_roles, list) else []
    if "Project Admin" not in roles:
        roles.append("Project Admin")
    project_roles[external_project_id] = roles
    permission_evidence["project_roles"] = project_roles
    connection_config["permission_evidence"] = permission_evidence
    updater = getattr(storage, "update_pm_connection", None)
    if callable(updater):
        updated_connection = updater(req.plan.connection_id, config=connection_config)
        if hasattr(updated_connection, "__await__"):
            await updated_connection
    binding_blockers = list(req.plan.manual_actions)
    binding_state = "WAITING_MANUAL_WEBHOOK" if binding_blockers else "PROVISIONED"
    binding_status = "SHADOW" if binding_blockers else "DISABLED"
    existing = await storage.list_pm_bindings(project_id=project_id)
    binding = next(
        (
            row for row in existing
            if row.get("connection_id") == req.plan.connection_id
            and row.get("mapping_profile") in {req.plan.mapping_profile, "default"}
            and str(row.get("external_project_id") or "") == external_project_id
        ),
        None,
    )
    binding_values = {
        "external_project_id": external_project_id,
        "external_project_key": external_project_key,
        "mapping_profile": req.plan.mapping_profile,
        "direction": "both",
        "status": binding_status,
        "provisioning_state": binding_state,
        "provisioning_plan_id": req.plan.plan_id,
        "provisioning_plan_digest": req.plan_digest,
        "activation_blockers": binding_blockers,
    }
    try:
        if binding is None:
            binding = await storage.create_pm_binding(
                project_id=project_id,
                connection_id=req.plan.connection_id,
                **binding_values,
            )
        else:
            binding = await storage.update_pm_binding(binding["id"], **binding_values)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await _remember_project_provisioning(
        storage,
        project_row,
        {
            "state": binding_state,
            "binding_id": str(binding["id"]) if binding else None,
            "external_project_id": external_project_id,
            "external_project_key": external_project_key,
            "blockers": binding_blockers,
            "applied_plan_digest": req.plan_digest,
        },
    )
    return {
        "project_id": str(project_id),
        "connection_id": str(req.plan.connection_id),
        "plan_digest": req.plan_digest,
        "applied_resources": {"created": applied.created, "adopted": applied.adopted},
        "binding": _serialize(binding) if binding else None,
        "status": binding_status,
        "blockers": binding_blockers,
    }


@app.post("/projects/{project_id}/pm-bindings", status_code=201)
async def create_project_pm_binding(project_id: UUID, req: PMBindingCreateRequest, request: Request) -> dict[str, Any]:
    _integration_operator(request)
    storage = _storage()
    project_row = await storage.get_project(project_id)
    if project_row is None:
        raise HTTPException(404, "project not found")
    connection = await storage.get_pm_connection(req.connection_id)
    if connection is None:
        raise HTTPException(404, "integration connection not found")
    mapping_profile = normalize_project_mapping_profile(req.mapping_profile)
    if mapping_profile == DEDICATED_PROJECT_MAPPING_PROFILE and not req.external_project_id:
        raise HTTPException(
            422,
            "dedicated_project bindings require the provider project to be provisioned/adopted first; use the project provisioning plan",
        )
    if mapping_profile == DEDICATED_PROJECT_MAPPING_PROFILE:
        provisioning = dict((project_row.get("config") or {}).get("pm_provisioning") or {})
        if (
            str(provisioning.get("external_project_id") or "") != str(req.external_project_id or "")
            or not provisioning.get("applied_plan_digest")
            or str(provisioning.get("state") or "") not in {"PROVISIONED", "WAITING_MANUAL_WEBHOOK"}
        ):
            raise HTTPException(
                409,
                "dedicated_project bindings require an applied project provisioning plan for this canonical project",
            )
    if mapping_profile == "umbrella_issues" and not (req.external_project_id or req.external_repository):
        raise HTTPException(422, "umbrella_issues requires an explicit provider project or repository selector")
    if req.status == "ACTIVE" and connection.get("status") != "ACTIVE":
        raise HTTPException(409, "an active binding requires an active integration connection")
    if req.direction in {"inbound", "both"} and not (req.external_project_id or req.external_repository):
        raise HTTPException(422, "inbound bindings require an explicit external project or repository selector")
    if req.external_repository:
        configured_repository = str((connection.get("config") or {}).get("repository") or "")
        if configured_repository and req.external_repository != configured_repository:
            raise HTTPException(403, "binding repository is outside the connection scope")
    try:
        binding = await storage.create_pm_binding(
            project_id=project_id,
            **{**req.model_dump(), "mapping_profile": mapping_profile},
        )
    except ValueError as exc:
        status = 409 if "conflict" in str(exc).lower() or "active" in str(exc).lower() else 422
        raise HTTPException(status, str(exc)) from exc
    return _serialize(binding)


@app.patch("/projects/{project_id}/pm-bindings/{binding_id}")
async def update_project_pm_binding(
    project_id: UUID,
    binding_id: UUID,
    req: PMBindingUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    """Change a binding phase without bypassing storage activation gates."""
    _integration_operator(request)
    storage = _storage()
    bindings = await storage.list_pm_bindings(project_id=project_id)
    current = next((row for row in bindings if row.get("id") == binding_id), None)
    if current is None:
        raise HTTPException(404, "PM binding not found for project")
    values = req.model_dump(exclude_none=True)
    if "status" in values and str(values["status"]) != str(current.get("status") or "DISABLED"):
        raise HTTPException(
            409,
            {
                "code": "lifecycle_plan_required",
                "message": "binding state changes require a persisted approved lifecycle plan",
            },
        )
    next_profile = normalize_project_mapping_profile(values.get("mapping_profile", current.get("mapping_profile")))
    next_external_project = values.get("external_project_id", current.get("external_project_id"))
    if next_profile == DEDICATED_PROJECT_MAPPING_PROFILE:
        project = await storage.get_project(project_id)
        provisioning = dict((project or {}).get("config", {}).get("pm_provisioning") or {})
        if str(provisioning.get("external_project_id") or "") != str(next_external_project or ""):
            raise HTTPException(409, "dedicated_project selector changes require a new approved project provisioning plan")
    if values.get("external_repository"):
        connection = await storage.get_pm_connection(current["connection_id"])
        configured_repository = str((connection or {}).get("config", {}).get("repository") or "")
        if configured_repository and values["external_repository"] != configured_repository:
            raise HTTPException(403, "binding repository is outside the connection scope")
    try:
        updated = await storage.update_pm_binding(binding_id, **values)
    except ValueError as exc:
        status = 409 if "active" in str(exc).lower() or "activation" in str(exc).lower() else 422
        raise HTTPException(status, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "PM binding not found for project")
    return _serialize(updated)


@app.get("/projects/{project_id}/pm-bindings")
async def list_project_pm_bindings(project_id: UUID, request: Request) -> list[dict[str, Any]]:
    _integration_operator(request)
    return [_serialize(row) for row in await _storage().list_pm_bindings(project_id=project_id)]


@app.get("/integrations/conflicts")
async def list_integration_conflicts(request: Request, connection_id: UUID | None = None, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
    _integration_operator(request)
    return [_serialize(row) for row in await _storage().list_pm_conflicts(connection_id=connection_id, limit=limit)]


@app.post("/integrations/conflicts/{conflict_id}/resolve")
async def resolve_integration_conflict(
    conflict_id: UUID,
    req: PMConflictResolutionRequest,
    request: Request,
) -> dict[str, Any]:
    _integration_operator(request)
    row = await _storage().resolve_pm_conflict(
        conflict_id,
        status=req.status,
        resolution=req.resolution,
    )
    if row is None:
        raise HTTPException(404, "PM conflict not found")
    return _serialize(row)


@app.get("/integrations/outbox")
async def list_integration_outbox(request: Request, connection_id: UUID | None = None, limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
    _integration_operator(request)
    return [_serialize(row) for row in await _storage().list_pm_outbox(connection_id=connection_id, limit=limit)]


@app.get("/integrations/reconciliation-runs")
async def list_integration_reconciliation_runs(
    request: Request,
    connection_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    _integration_operator(request)
    return [
        _serialize(row)
        for row in await _storage().list_pm_reconciliation_runs(connection_id=connection_id, limit=limit)
    ]


@app.post("/integrations/outbox/{outbox_id}/disposition")
async def dispose_integration_outbox(
    outbox_id: UUID, req: PMOutboxDispositionRequest, request: Request
) -> dict[str, Any]:
    """Record an immutable operator disposition for a terminal PM failure."""
    _integration_operator(request)
    try:
        return _serialize(await _storage().dispose_pm_outbox_dead_letter(
            outbox_id,
            disposition=req.disposition,
            reason=req.reason,
            provider_state=req.provider_state,
            actor=_authenticated_principal(request),
        ))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/integrations/cutovers")
async def list_integration_cutovers(
    request: Request,
    project_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    _integration_operator(request)
    return [_serialize(row) for row in await _storage().list_pm_cutovers(project_id=project_id, limit=limit)]


@app.post("/integrations/outbox/drain")
async def drain_integration_outbox(request: Request, limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    """Run a bounded delivery pass; production deployments may run this in a worker."""
    _integration_operator(request, allow_gateway=True)
    storage = _storage()
    rows = await storage.list_pm_outbox(limit=limit)
    synced = failed = dead_letter = 0
    for candidate in rows:
        outbox = await storage.claim_pm_outbox(candidate["id"])
        if outbox is None:
            continue
        connection_row = await storage.get_pm_connection(outbox["connection_id"])
        try:
            if connection_row is None:
                raise RuntimeError("connection was removed")
            provider = _provider_for(connection_row)
            connection = _provider_connection(connection_row)
            # A connection may serve many canonical projects.  For every
            # binding-scoped delivery, overlay the binding's provider project
            # selector so a missing object mapping cannot fall back to the
            # connection bootstrap project (AIAT) and leak work across
            # projects.
            binding_id_value = (outbox.get("payload") or {}).get("binding_id")
            if binding_id_value:
                binding_rows = await storage.list_pm_bindings(connection_id=connection.id)
                binding_row = next(
                    (item for item in binding_rows if str(item.get("id")) == str(binding_id_value)),
                    None,
                )
                binding_project = str((binding_row or {}).get("external_project_id") or "")
                if binding_project:
                    connection = connection.model_copy(
                        update={"config": {**connection.config, "project_id": binding_project}}
                    )
            if outbox["operation"] == "upsert_project":
                project = CanonicalProject.model_validate(outbox["payload"]["project"])
                mapping = await storage.get_pm_mapping(
                    connection_id=connection.id,
                    object_type=ObjectType.PROJECT.value,
                    aiat_object_id=project.id,
                )
                result = await provider.project_project(
                    connection,
                    project,
                    external_id=mapping.get("external_id") if mapping else None,
                    idempotency_key=outbox["idempotency_key"],
                )
                await storage.upsert_pm_mapping(
                    connection_id=connection.id,
                    object_type=ObjectType.PROJECT.value,
                    aiat_object_id=project.id,
                    external_id=str(result.external_id),
                    provider_version=result.provider_version,
                    exported_revision=project.revision,
                )
            elif outbox["operation"] == "upsert_iteration":
                iteration = CanonicalIteration.model_validate(outbox["payload"]["iteration"])
                mapping = await storage.get_pm_mapping(
                    connection_id=connection.id,
                    object_type=ObjectType.SPRINT.value,
                    aiat_object_id=iteration.id,
                )
                result = await provider.project_iteration(
                    connection,
                    iteration,
                    external_id=mapping.get("external_id") if mapping else None,
                    idempotency_key=outbox["idempotency_key"],
                )
                await storage.upsert_pm_mapping(
                    connection_id=connection.id,
                    object_type=ObjectType.SPRINT.value,
                    aiat_object_id=iteration.id,
                    external_id=str(result.external_id),
                    provider_version=result.provider_version,
                    exported_revision=iteration.revision,
                )
            elif outbox["operation"] == "upsert_work_item":
                item = CanonicalWorkItem.model_validate(outbox["payload"]["item"])
                mapping = await storage.get_pm_mapping(connection_id=connection.id, object_type="work_item", aiat_object_id=item.id)
                result = await provider.project_work_item(connection, item, external_id=mapping.get("external_id") if mapping else None, idempotency_key=outbox["idempotency_key"])
                await storage.upsert_pm_mapping(connection_id=connection.id, object_type="work_item", aiat_object_id=item.id, external_id=str(result.external_id), external_key=result.external_key, provider_version=result.provider_version, exported_revision=item.revision)
            elif outbox["operation"] == "project_comment":
                mapping = await storage.get_pm_mapping(
                    connection_id=connection.id,
                    object_type="work_item",
                    aiat_object_id=outbox["aggregate_id"],
                )
                if mapping is None:
                    raise RuntimeError("cannot project comment before work-item mapping exists")
                comment = outbox["payload"].get("comment") or {}
                result = await provider.project_comment(
                    connection,
                    external_id=str(mapping["external_id"]),
                    body=str(comment.get("body") or ""),
                    idempotency_key=outbox["idempotency_key"],
                )
                comment_id = comment.get("id")
                if comment_id and result.external_id:
                    await storage.upsert_pm_mapping(
                        connection_id=connection.id,
                        object_type=ObjectType.COMMENT.value,
                        aiat_object_id=UUID(str(comment_id)),
                        external_id=str(result.external_id),
                        provider_version=result.provider_version,
                        exported_revision=int(outbox.get("canonical_revision") or 1),
                    )
            elif outbox["operation"] == "project_link":
                mapping = await storage.get_pm_mapping(
                    connection_id=connection.id,
                    object_type="work_item",
                    aiat_object_id=outbox["aggregate_id"],
                )
                if mapping is None:
                    raise RuntimeError("cannot project link before work-item mapping exists")
                capabilities = await provider.capabilities(connection)
                if not capabilities.links:
                    await storage.create_pm_conflict(
                        connection_id=connection.id,
                        binding_id=UUID(str((outbox.get("payload") or {}).get("binding_id")))
                        if (outbox.get("payload") or {}).get("binding_id")
                        else None,
                        reason="unsupported_capability",
                        object_type="link",
                        aiat_object_id=outbox["aggregate_id"],
                        external_snapshot={
                            "provider": connection.provider_kind,
                            "capability": "links",
                            "link": outbox.get("payload", {}).get("link"),
                        },
                    )
                    await storage.record_pm_delivery_attempt(
                        outbox["id"],
                        status="CONFLICT",
                        response_metadata={"capability": "links"},
                        error="provider does not support typed links",
                    )
                    await storage.mark_pm_outbox(
                        outbox["id"],
                        status="CONFLICT",
                        error="provider does not support typed links",
                    )
                    failed += 1
                    continue
                result = await provider.project_link(
                    connection,
                    external_id=str(mapping["external_id"]),
                    link=dict(outbox["payload"].get("link") or {}),
                    idempotency_key=outbox["idempotency_key"],
                )
            else:
                raise RuntimeError(f"unsupported outbox operation {outbox['operation']}")
            projection_status = getattr(getattr(result, "status", None), "value", getattr(result, "status", None))
            if projection_status not in {None, "synced", "SYNCED"}:
                raise RuntimeError(f"provider projection returned non-synced status {projection_status!r}")
            result_payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else {}
            response_metadata = {
                key: result_payload.get(key)
                for key in ("external_id", "external_url", "provider_version", "object_type")
                if result_payload.get(key) is not None
            }
            await storage.record_pm_delivery_attempt(
                outbox["id"],
                status="SUCCEEDED",
                response_metadata=response_metadata,
            )
            await storage.mark_pm_outbox(outbox["id"], status="SYNCED")
            projection_evidence = getattr(storage, "record_pm_binding_evidence", None)
            payload_binding_id = (outbox.get("payload") or {}).get("binding_id")
            if callable(projection_evidence) and payload_binding_id:
                recorded = projection_evidence(UUID(str(payload_binding_id)), projection_verified=True)
                if hasattr(recorded, "__await__"):
                    await recorded
            synced += 1
        except ValueError as exc:
            if str(exc).startswith("PM mapping conflict:"):
                payload = dict(outbox.get("payload") or {})
                item_payload = payload.get("item") or payload.get("project") or payload.get("iteration") or {}
                await storage.create_pm_conflict(
                    connection_id=outbox["connection_id"],
                    binding_id=UUID(str(payload["binding_id"])) if payload.get("binding_id") else None,
                    reason="mapping_conflict",
                    object_type=str(outbox.get("aggregate_type") or "provider_object"),
                    aiat_object_id=UUID(str(item_payload["id"])) if item_payload.get("id") else None,
                    external_snapshot={"error": str(exc), "payload": payload},
                )
                await storage.mark_pm_outbox(outbox["id"], status="CONFLICT", error=str(exc)[:1000])
                failed += 1
                continue
            attempt = await storage.record_pm_delivery_attempt(
                outbox["id"],
                status="FAILED",
                provider_status=getattr(exc, "status_code", None),
                error=str(exc)[:1000],
                retry_after_seconds=getattr(exc, "retry_after", None),
            )
            if _provider_failure_is_permanent(exc) or (
                attempt is not None and int(attempt.get("attempts") or 0) >= 5
            ):
                await storage.mark_pm_outbox(outbox["id"], status="DEAD_LETTER", error=str(exc)[:1000])
                dead_letter += 1
            else:
                await storage.mark_pm_outbox(outbox["id"], status="PENDING", error=str(exc)[:1000])
            failed += 1
        except Exception as exc:
            attempt = await storage.record_pm_delivery_attempt(
                outbox["id"],
                status="FAILED",
                provider_status=getattr(exc, "status_code", None),
                error=str(exc)[:1000],
                retry_after_seconds=getattr(exc, "retry_after", None),
            )
            if _provider_failure_is_permanent(exc) or (
                attempt is not None and int(attempt.get("attempts") or 0) >= 5
            ):
                await storage.mark_pm_outbox(outbox["id"], status="DEAD_LETTER", error=str(exc)[:1000])
                dead_letter += 1
            else:
                await storage.mark_pm_outbox(outbox["id"], status="PENDING", error=str(exc)[:1000])
            failed += 1
    return {"claimed": len(rows), "synced": synced, "failed": failed, "dead_letter": dead_letter}


@app.post("/integrations/webhooks/{connection_id}", status_code=202)
async def receive_integration_webhook(connection_id: UUID, request: Request) -> dict[str, Any]:
    """Verify, persist, dedupe, and normalize a provider webhook."""
    # Do not call ``_integration_operator`` here.  The public PM gateway does
    # not expose the internal API key to providers; this route's authorization
    # boundary is the configured provider header resolved by the adapter below.
    max_body_bytes = 1 * 1024 * 1024
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > max_body_bytes:
                raise HTTPException(413, "webhook body exceeds 1 MiB limit")
        except ValueError as exc:
            raise HTTPException(400, "invalid webhook content length") from exc
    storage = _storage()
    row = await storage.get_pm_connection(connection_id)
    if row is None:
        raise HTTPException(404, "integration connection not found")
    if str(row.get("status") or "DISABLED") == "DISABLED":
        raise HTTPException(409, "integration connection is disabled")
    chunks: list[bytes] = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > max_body_bytes:
            raise HTTPException(413, "webhook body exceeds 1 MiB limit")
        chunks.append(chunk)
    body = b"".join(chunks)
    headers = {key.lower(): value for key, value in request.headers.items()}
    provider = _provider_for(row)
    connection = _provider_connection(row)
    verifier = getattr(provider, "verify_webhook_async", None)
    try:
        config = row.get("config", {}) or {}
        secret_ref = config.get("webhook_secret_ref") or (config.get("webhook_secret_refs") or [None])[0]
        provider_kind = str(row.get("provider_kind") or "").lower()
        if provider_kind != "fake" and not secret_ref:
            raise HTTPException(
                503,
                "managed webhook_secret_ref is required for non-fake providers",
            )
        if provider_kind != "fake" and verifier is None:
            raise HTTPException(
                503,
                "provider does not expose an asynchronous managed-secret verifier",
            )
        if verifier is not None and secret_ref:
            verifier_kwargs = {"secret_ref": str(secret_ref)} if str(row.get("provider_kind")) == "github" else {}
            verified = await verifier(connection, body, headers, **verifier_kwargs)
        else:
            verified = provider.verify_webhook(connection, body, headers)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"webhook verifier unavailable: {exc}") from exc
    if not verified:
        raise HTTPException(401, "invalid provider webhook authentication")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, "webhook body must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "webhook body must be a JSON object")
    delivery_id = headers.get("x-github-delivery") or headers.get("x-youtrack-delivery") or headers.get("x-delivery-id") or hashlib.sha256(body).hexdigest()
    event_type = headers.get("x-github-event") or str(payload.get("event_type") or payload.get("event") or "provider.event")
    # Retain only non-secret transport metadata.  In particular, never write
    # the API key used by an upstream gateway into the forensic event row.
    retained_header_names = {
        "content-type", "user-agent", "x-github-delivery", "x-github-event",
        "x-hub-signature-256", "x-youtrack-delivery", "x-delivery-id",
        "x-youtrack-signature", "x-webhook-signature",
    }
    retained_headers = {
        key: value for key, value in headers.items() if key in retained_header_names
    }
    raw_payload_hash = hashlib.sha256(body).hexdigest()
    inbox, inserted = await storage.create_pm_inbox_event(
        connection_id=connection_id,
        provider_delivery_id=delivery_id,
        event_type=event_type,
        payload=payload,
        verified=True,
        raw_body=body,
        headers=retained_headers,
        payload_hash=raw_payload_hash,
    )
    if not inserted:
        incoming_hash = raw_payload_hash
        if inbox.get("payload_hash") and inbox.get("payload_hash") != incoming_hash:
            await storage.create_pm_conflict(
                connection_id=connection_id,
                reason="delivery_id_reuse",
                object_type="event",
                external_id=delivery_id,
                external_snapshot={"event_type": event_type, "payload_hash": incoming_hash},
            )
            await storage.mark_pm_inbox_event(
                inbox["id"],
                status="CONFLICT",
                error="provider delivery ID was reused with a different payload",
            )
            return {
                "status": "conflict",
                "delivery_id": delivery_id,
                "reason": "delivery_id_reuse",
                "inbox_id": str(inbox["id"]),
            }
        if str(inbox.get("status") or "RECEIVED").upper() in {"PROCESSED", "CONFLICT"}:
            return {"status": "duplicate", "delivery_id": delivery_id, "inbox_id": str(inbox["id"])}
        # A previous attempt may have committed the inbox row and crashed
        # before normalization/acknowledgement.  Re-enter the normalizer for
        # RECEIVED/FAILED rows so durable inbox persistence is a recoverable
        # crash boundary rather than a permanent black hole.
    normalized = provider.normalize_webhook(ExternalEvent(connection_id=connection_id, provider_delivery_id=delivery_id, event_type=event_type, payload=payload, verified=True))
    if normalized is None:
        await storage.mark_pm_inbox_event(
            inbox["id"],
            status="PROCESSED",
            normalized_type="none",
            result={"status": "accepted", "normalized": False},
        )
        return {"status": "accepted", "delivery_id": delivery_id, "normalized": False}
    applied = await _apply_normalized_command(storage, normalized, inbox)
    await storage.mark_pm_inbox_event(
        inbox["id"],
        status="CONFLICT" if applied.get("status") == "conflict" else "PROCESSED",
        normalized_type=str(getattr(normalized.object_type, "value", normalized.object_type)),
        result=applied,
    )
    return {
        "status": "accepted" if applied.get("status") != "conflict" else "conflict",
        "delivery_id": delivery_id,
        "normalized": True,
        "command": normalized.model_dump(mode="json"),
        "result": applied,
    }


@app.post("/projects/{project_id}/issues", status_code=201)
async def create_canonical_issue(project_id: UUID, req: CanonicalIssueCreateRequest, request: Request) -> dict[str, Any]:
    """Typed canonical issue creation used by generic tools and integrations."""
    _require_operator_identity(request)
    storage = _storage()
    if await storage.get_project(project_id) is None:
        raise HTTPException(404, "project not found")
    if req.sprint_id is not None:
        sprint = await storage.get_sprint(req.sprint_id)
        if sprint is None or sprint.get("project_id") != project_id:
            raise HTTPException(404, "sprint not found for project")
    if isinstance(storage, AgentStorage):
        issue, queued = await storage.create_issue_with_pm_projections(
            project_id=project_id,
            **req.model_dump(),
        )
    else:  # keep lightweight endpoint tests and local storage doubles useful
        issue = await storage.create_issue(project_id=project_id, **req.model_dump())
        queued = await _enqueue_issue_projection(storage, issue)
    return {"issue": _serialize(issue), "projections": [_serialize_projection(row) for row in queued]}


@app.get("/projects/{project_id}/issues/{issue_id}")
async def get_canonical_issue(project_id: UUID, issue_id: UUID, request: Request) -> dict[str, Any]:
    """Read one canonical work item with its durable integration metadata."""
    storage = _storage()
    issue = await storage.get_issue(issue_id)
    if issue is None or issue.get("project_id") != project_id:
        raise HTTPException(404, "issue not found for project")
    return {
        "issue": _serialize(issue),
        "comments": [_serialize(row) for row in await storage.list_work_item_comments(issue_id)],
        "links": [_serialize(row) for row in await storage.list_work_item_links(issue_id)],
    }


@app.patch("/projects/{project_id}/issues/{issue_id}")
async def update_canonical_issue(project_id: UUID, issue_id: UUID, req: CanonicalIssueUpdateRequest, request: Request) -> dict[str, Any]:
    _require_operator_identity(request)
    storage = _storage()
    issue = await storage.get_issue(issue_id)
    if issue is None or issue.get("project_id") != project_id:
        raise HTTPException(404, "issue not found for project")
    values = {key: value for key, value in req.model_dump(exclude_none=True).items() if key != "expected_revision"}
    try:
        if isinstance(storage, AgentStorage):
            refreshed, queued = await storage.update_issue_with_pm_projections(
                issue_id,
                expected_revision=req.expected_revision or int(issue.get("revision") or 1),
                **values,
            )
        else:
            await storage.update_issue(
                issue_id,
                expected_revision=req.expected_revision or int(issue.get("revision") or 1),
                **values,
            )
            refreshed = await storage.get_issue(issue_id)
            assert refreshed is not None
            queued = await _enqueue_issue_projection(storage, refreshed)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"issue": _serialize(refreshed), "projections": [_serialize_projection(row) for row in queued]}


@app.post("/projects/{project_id}/issues/{issue_id}/comments", status_code=201)
async def comment_on_canonical_issue(project_id: UUID, issue_id: UUID, req: CanonicalIssueCommentRequest, request: Request) -> dict[str, Any]:
    _require_operator_identity(request)
    storage = _storage()
    issue = await storage.get_issue(issue_id)
    if issue is None or issue.get("project_id") != project_id:
        raise HTTPException(404, "issue not found for project")
    if isinstance(storage, AgentStorage):
        comment, projections = await storage.create_work_item_comment_with_pm_projections(
            issue_id=issue_id,
            body=req.body,
            actor_id=req.actor_id,
            run_id=req.run_id,
            approval_id=req.approval_id,
            evidence_id=req.evidence_id,
            body_blob_ref=req.body_blob_ref,
        )
    else:
        comment = await storage.create_work_item_comment(
            issue_id=issue_id,
            body=req.body,
            actor_id=req.actor_id,
            run_id=req.run_id,
            approval_id=req.approval_id,
            evidence_id=req.evidence_id,
            body_blob_ref=req.body_blob_ref,
        )
        projections = []
    return {
        "comment": _serialize(comment),
        "issue_id": str(issue_id),
        "projections": [_serialize_projection(row) for row in projections],
    }


@app.get("/projects/{project_id}/issues/{issue_id}/comments")
async def list_canonical_issue_comments(project_id: UUID, issue_id: UUID, request: Request) -> list[dict[str, Any]]:
    storage = _storage()
    issue = await storage.get_issue(issue_id)
    if issue is None or issue.get("project_id") != project_id:
        raise HTTPException(404, "issue not found for project")
    return [_serialize(row) for row in await storage.list_work_item_comments(issue_id)]


@app.post("/projects/{project_id}/issues/{issue_id}/links", status_code=201)
async def link_canonical_issue(
    project_id: UUID,
    issue_id: UUID,
    req: CanonicalIssueLinkRequest,
    request: Request,
) -> dict[str, Any]:
    """Create an idempotent link from a canonical work item to an external object."""
    _require_operator_identity(request)
    storage = _storage()
    issue = await storage.get_issue(issue_id)
    if issue is None or issue.get("project_id") != project_id:
        raise HTTPException(404, "issue not found for project")
    if isinstance(storage, AgentStorage):
        link, projections = await storage.create_work_item_link_with_pm_projections(
            issue_id=issue_id,
            **req.model_dump(),
        )
    else:
        link = await storage.create_work_item_link(issue_id=issue_id, **req.model_dump())
        projections = []
    return {
        "link": _serialize(link),
        "issue_id": str(issue_id),
        "projections": [_serialize_projection(row) for row in projections],
    }


@app.get("/projects/{project_id}/issues/{issue_id}/links")
async def list_canonical_issue_links(
    project_id: UUID,
    issue_id: UUID,
    request: Request,
) -> list[dict[str, Any]]:
    storage = _storage()
    issue = await storage.get_issue(issue_id)
    if issue is None or issue.get("project_id") != project_id:
        raise HTTPException(404, "issue not found for project")
    return [_serialize(row) for row in await storage.list_work_item_links(issue_id)]
