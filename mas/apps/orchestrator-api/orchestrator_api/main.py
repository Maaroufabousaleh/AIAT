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
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import httpx
import prometheus_client
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from prometheus_client import Counter
from pydantic import BaseModel, Field

from mas_core.memory.storage import AgentStorage
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

logger = logging.getLogger(__name__)

configure_logging("orchestrator-api", json=os.getenv("LOG_FORMAT") != "console")

# ---------------------------------------------------------------------------
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
    "INFRA_PROVISIONING": "dept_devops",
    "IN_PROGRESS": "exec_coo",
    "RETROSPECTIVE": "exec_coo",
    "KPI_PERSISTENCE": "office_cfo",
}


def get_responsible_team(state: str) -> str:
    """Return the team_id responsible for progressing a project in this state."""
    return STATE_TO_TEAM.get(state, "exec_ceo")


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
    sandbox_profile: str = "standard"
    capability_ids: list[UUID] = Field(default_factory=list)
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


class CreateFlowRequest(BaseModel):
    name: str
    description: str | None = None
    definition_json: dict[str, Any]
    created_by: str = "human"
    is_active: bool = False


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
    action: str = Field(..., description="advance | complete | fail")
    output: dict[str, Any] | None = None
    error: str | None = None
    approved: bool | None = None


class FlowInstanceActionRequest(BaseModel):
    action: str = Field(..., description="start | pause | resume | cancel")
    node_id: str | None = None


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
    try:
        await storage.connect()
    except Exception:
        logger.warning(
            "Could not connect to Postgres at startup (may be running tests); storage will be None"
        )
        storage = None  # type: ignore[assignment]

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

    scheduler = AsyncIOScheduler(timezone=req.timezone)

    if req.auto_shutdown:
        scheduler.add_job(
            _cron_shutdown,
            CronTrigger(hour=req.end_hour, minute=0, day_of_week=dow, timezone=req.timezone),
            id="auto_shutdown",
            replace_existing=True,
        )
        logger.info("Auto-shutdown cron: hour=%d, days=%s, tz=%s", req.end_hour, dow, req.timezone)

    if req.auto_resume:
        scheduler.add_job(
            _cron_resume,
            CronTrigger(hour=req.start_hour, minute=0, day_of_week=dow, timezone=req.timezone),
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

    # Get workers for matching capabilities
    workers = await storage.list_workers(status="ACTIVE")

    results = []
    cap_ids = {c["id"] for c in caps}
    for w in workers:
        worker_caps = set(w.get("capability_ids") or [])
        if worker_caps & cap_ids:
            results.append(_serialize(w))

    return results


@app.get("/capabilities/workers")
async def list_capability_workers(
    team_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List all registered workers with their capabilities and sandbox profiles."""
    storage = _storage()
    workers = await storage.list_workers(team_id=team_id, status=status)
    return [_serialize(w) for w in workers]


@app.post("/capabilities/workers", status_code=201)
async def register_worker(req: RegisterWorkerRequest) -> dict[str, Any]:
    """Register a new worker (called by team-runner on startup)."""
    storage = _storage()
    worker = await storage.register_worker(
        name=req.name,
        adapter_type=req.adapter_type,
        adapter_config=req.adapter_config,
        sandbox_profile=req.sandbox_profile,
        capability_ids=req.capability_ids,
        team_id=req.team_id,
        source_repo=req.source_repo,
        version_pin=req.version_pin,
        update_policy=req.update_policy or "manual",
    )
    return _serialize(worker)


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

    checks = req.checks or ["architecture", "maintenance", "licensing", "security", "compatibility"]

    try:
        report = await evaluate_repository(
            worker_id=worker_id,
            source_repo=source_repo,
            storage=storage,
            checks=checks,
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
    flow = await storage.create_flow(
        name=req.name,
        description=req.description,
        definition_json=req.definition_json,
        created_by=req.created_by,
        is_active=req.is_active,
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
    from mas_core.workflow import (
        FlowDefinition,
        FlowInstanceStatus,
        FlowNodeType,
        parse_flow_definition,
        serialize_flow_definition,
    )

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

        await storage.create_flow_node_execution(
            instance_id=instance_id,
            node_id=start_nodes[0].id,
            node_type=start_nodes[0].type.value,
            node_label=start_nodes[0].label,
            input_json=context,
        )

        return _serialize(await storage.get_flow_instance(instance_id))

    elif req.action == "pause":
        if current_status != "RUNNING":
            raise HTTPException(409, f"Instance is not RUNNING (current: {current_status})")

        await storage.update_flow_instance(instance_id, status="PAUSED")
        return _serialize(await storage.get_flow_instance(instance_id))

    elif req.action == "resume":
        if current_status != "PAUSED":
            raise HTTPException(409, f"Instance is not PAUSED (current: {current_status})")

        await storage.update_flow_instance(instance_id, status="RUNNING")
        return _serialize(await storage.get_flow_instance(instance_id))

    elif req.action == "cancel":
        if current_status in ("COMPLETED", "FAILED", "CANCELLED"):
            raise HTTPException(409, f"Instance is already in terminal state: {current_status}")

        await storage.update_flow_instance(instance_id, status="CANCELLED")
        return _serialize(await storage.get_flow_instance(instance_id))

    else:
        raise HTTPException(400, f"Unknown action: {req.action}")


@app.post("/flows/instances/{instance_id}/node-action")
async def flow_node_action(instance_id: UUID, req: FlowNodeActionRequest) -> dict[str, Any]:
    """Perform an action on a node within a flow instance."""
    from datetime import UTC, datetime
    from mas_core.workflow import (
        FlowDefinition,
        FlowInstanceStatus,
        FlowNodeType,
        get_next_nodes,
        parse_flow_definition,
    )

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
    if req.node_id not in active_node_ids:
        raise HTTPException(409, f"Node {req.node_id} is not currently active")

    now = datetime.now(tz=UTC)

    if req.action == "complete":
        executions = await storage.list_flow_node_executions(
            instance_id=instance_id, node_id=req.node_id, limit=1
        )
        if executions:
            await storage.update_flow_node_execution(
                executions[0]["id"],
                status="COMPLETED",
                output_json=req.output,
                completed_at=now,
            )

        # Remove completed node from active set; track it separately for traversal
        remaining_active = set(active_node_ids)
        remaining_active.discard(req.node_id)
        new_active = list(remaining_active)

        if node.type == FlowNodeType.APPROVAL:
            if req.approved is True:
                pass
            elif req.approved is False:
                await storage.update_flow_instance(
                    instance_id, status="FAILED", active_node_ids=new_active
                )
                return _serialize(await storage.get_flow_instance(instance_id))
            else:
                await storage.update_flow_instance(
                    instance_id, status="WAITING_APPROVAL", active_node_ids=new_active
                )
                return _serialize(await storage.get_flow_instance(instance_id))

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
            definition, historically_completed, set(), context=instance.get("context_json")
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
                await storage.update_flow_instance(
                    instance_id, status="COMPLETED", active_node_ids=[], completed_at=now
                )
            elif not already_active:
                # No more nodes to run and no active work remaining — check if all end nodes done
                all_ends_done = all(n.id in historically_completed for n in end_nodes)
                if all_ends_done:
                    await storage.update_flow_instance(
                        instance_id, status="COMPLETED", active_node_ids=[], completed_at=now
                    )
                else:
                    # Still waiting for parallel branches — stay RUNNING
                    await storage.update_flow_instance(
                        instance_id, active_node_ids=list(already_active)
                    )
            else:
                # Other branches still active — just remove this node from active
                await storage.update_flow_instance(
                    instance_id, active_node_ids=list(already_active)
                )
        else:
            for nid in new_nodes:
                n = definition.get_node(nid)
                if n:
                    await storage.create_flow_node_execution(
                        instance_id=instance_id,
                        node_id=nid,
                        node_type=n.type.value,
                        node_label=n.label,
                        input_json=instance.get("context_json"),
                    )
            merged_active = list(already_active | set(new_nodes))
            await storage.update_flow_instance(instance_id, active_node_ids=merged_active)

        return _serialize(await storage.get_flow_instance(instance_id))

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
        return _serialize(await storage.get_flow_instance(instance_id))

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

    updated = await storage.switch_flow_instance(
        instance_id, new_flow_uuid, preserve_context=preserve_context
    )
    if updated is None:
        raise HTTPException(404, "Failed to switch flow instance")

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
    storage = _storage()

    instance = await storage.get_flow_instance(instance_id)
    if instance is None:
        raise HTTPException(404, f"Flow instance {instance_id} not found")

    if instance["status"] not in ("FAILED", "CANCELLED"):
        raise HTTPException(409, f"Instance is not in FAILED or CANCELLED state")

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


async def _stream_container_logs(container: str, tail: int, follow: bool):
    """Async generator that yields SSE lines from docker logs."""
    cmd = ["docker", "logs", container, f"--tail={tail}", "--timestamps"]
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
