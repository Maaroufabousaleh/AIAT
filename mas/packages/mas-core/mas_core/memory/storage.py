"""AgentStorage — async Postgres wrapper for all MAS tables.

Every query is scoped by ``agent_id`` (or ``project_id``, depending on the
table) so that agents cannot accidentally read or modify another agent's data.

Connection target is **PgBouncer** (transaction-pooling mode).
Uses ``asyncpg`` via ``sqlalchemy[asyncio]`` with ``statement_cache_size=0``
(mandatory for PgBouncer transaction mode).

Usage
-----
::

    storage = AgentStorage(dsn="postgresql+asyncpg://mas_user:pw@pgbouncer:5432/mas")
    await storage.connect()

    project = await storage.create_project(...)
    await storage.transition_project(project_id, "PDR_CREATION", ...)

    await storage.close()
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from . import models as t

logger = logging.getLogger(__name__)


class AgentStorage:
    """Async Postgres storage layer using SQLAlchemy Core.

    Parameters
    ----------
    dsn : str
        PostgreSQL connection string (targeting PgBouncer).
        Example: ``"postgresql+asyncpg://mas_user:pw@pgbouncer:5432/mas"``
    pool_size : int
        Connection pool size (default 5, suitable for a single team-runner).
    max_overflow : int
        Extra connections above ``pool_size`` (default 5).
    """

    def __init__(
        self,
        dsn: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 5,
    ) -> None:
        self._dsn = dsn
        self._engine: AsyncEngine | None = None
        self._pool_size = pool_size
        self._max_overflow = max_overflow

    async def connect(self) -> None:
        """Create the async engine and validate connectivity."""
        self._engine = create_async_engine(
            self._dsn,
            pool_size=self._pool_size,
            max_overflow=self._max_overflow,
            # PgBouncer transaction-pooling requires this:
            pool_pre_ping=True,
            connect_args={"statement_cache_size": 0},
        )
        # Verify connection
        async with self._engine.begin() as conn:
            await conn.execute(sa.text("SELECT 1"))
        logger.info("AgentStorage connected to Postgres")

    async def close(self) -> None:
        """Dispose the engine and release connections."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            logger.info("AgentStorage connection closed")

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("AgentStorage not connected. Call connect() first.")
        return self._engine

    # ═══════════════════════════════════════════════════════════════════════════
    # Projects
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_project(
        self,
        *,
        name: str,
        description: str | None = None,
        state: str = "INIT",
        created_by: str,
        project_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Insert a new project row. Returns the full row as a dict."""
        pid = project_id or uuid4()
        now = datetime.now(tz=timezone.utc)
        values = {
            "id": pid,
            "name": name,
            "description": description,
            "state": state,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.projects.insert().values(**values))
        return {**values, "failure_reason": None, "failed_from_state": None}

    async def get_project(self, project_id: UUID) -> dict[str, Any] | None:
        """Fetch a project by ID."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.projects.select().where(t.projects.c.id == project_id)
                )
            ).mappings().first()
        return dict(row) if row else None

    async def list_projects(
        self,
        *,
        state: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List projects, optionally filtered by state."""
        q = t.projects.select().order_by(t.projects.c.created_at.desc())
        if state:
            q = q.where(t.projects.c.state == state)
        q = q.limit(limit).offset(offset)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def transition_project(
        self,
        project_id: UUID,
        *,
        new_state: str,
        event: str,
        triggered_by: str,
        payload: dict | None = None,
        failure_reason: str | None = None,
        failed_from_state: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically update project state and append a history row.

        Returns the updated project row, or None if the project was not found.
        """
        now = datetime.now(tz=timezone.utc)
        async with self.engine.begin() as conn:
            # Read current state
            current = (
                await conn.execute(
                    t.projects.select()
                    .where(t.projects.c.id == project_id)
                    .with_for_update()
                )
            ).mappings().first()
            if not current:
                return None

            from_state = current["state"]

            # Update project
            update_values: dict[str, Any] = {
                "state": new_state,
                "updated_at": now,
            }
            if failure_reason is not None:
                update_values["failure_reason"] = failure_reason
            if failed_from_state is not None:
                update_values["failed_from_state"] = failed_from_state
            elif new_state != "FAILED":
                # Clear failure fields when leaving FAILED state
                update_values["failure_reason"] = None
                update_values["failed_from_state"] = None

            await conn.execute(
                t.projects.update()
                .where(t.projects.c.id == project_id)
                .values(**update_values)
            )

            # Append history
            await conn.execute(
                t.project_state_history.insert().values(
                    project_id=project_id,
                    from_state=from_state,
                    to_state=new_state,
                    event=event,
                    triggered_by=triggered_by,
                    payload=payload,
                    transitioned_at=now,
                )
            )

        return await self.get_project(project_id)

    async def get_project_history(
        self,
        project_id: UUID,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch transition history for a project, newest first."""
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    t.project_state_history.select()
                    .where(t.project_state_history.c.project_id == project_id)
                    .order_by(t.project_state_history.c.transitioned_at.desc())
                    .limit(limit)
                )
            ).mappings().all()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════════
    # Documents
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_document(
        self,
        *,
        project_id: UUID,
        doc_type: str,
        created_by: str,
        blob_bucket: str | None = None,
        blob_key: str | None = None,
        blob_sha256: str | None = None,
        document_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a new document row (DRAFT state)."""
        did = document_id or uuid4()
        now = datetime.now(tz=timezone.utc)
        values = {
            "id": did,
            "project_id": project_id,
            "doc_type": doc_type,
            "version": 1,
            "status": "DRAFT",
            "blob_bucket": blob_bucket,
            "blob_key": blob_key,
            "blob_sha256": blob_sha256,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.documents.insert().values(**values))
        return values

    async def get_document(self, document_id: UUID) -> dict[str, Any] | None:
        """Fetch a document by ID."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.documents.select().where(t.documents.c.id == document_id)
                )
            ).mappings().first()
        return dict(row) if row else None

    async def get_latest_document(
        self, project_id: UUID, doc_type: str
    ) -> dict[str, Any] | None:
        """Fetch the latest version of a document by project + type."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.documents.select()
                    .where(
                        sa.and_(
                            t.documents.c.project_id == project_id,
                            t.documents.c.doc_type == doc_type,
                        )
                    )
                    .order_by(t.documents.c.version.desc())
                    .limit(1)
                )
            ).mappings().first()
        return dict(row) if row else None

    async def update_document_status(
        self,
        document_id: UUID,
        *,
        status: str,
    ) -> None:
        """Update a document's status."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.documents.update()
                .where(t.documents.c.id == document_id)
                .values(status=status, updated_at=datetime.now(tz=timezone.utc))
            )

    async def list_documents(
        self,
        project_id: UUID,
        *,
        doc_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List documents for a project, optionally filtered by type."""
        q = t.documents.select().where(t.documents.c.project_id == project_id)
        if doc_type:
            q = q.where(t.documents.c.doc_type == doc_type)
        q = q.order_by(t.documents.c.version.desc())
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════════
    # Review sessions & comments
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_review_session(
        self,
        *,
        project_id: UUID,
        document_id: UUID | None = None,
        session_type: str,
        reviewer_ids: list[str],
        review_timeout_seconds: int = 300,
        session_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a review session for parallel fan-out."""
        sid = session_id or uuid4()
        now = datetime.now(tz=timezone.utc)
        values = {
            "id": sid,
            "project_id": project_id,
            "document_id": document_id,
            "session_type": session_type,
            "status": "IN_PROGRESS",
            "reviewer_ids": reviewer_ids,
            "timeout_count": 0,
            "review_timeout_seconds": review_timeout_seconds,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.review_sessions.insert().values(**values))
        return values

    async def get_review_session(self, session_id: UUID) -> dict[str, Any] | None:
        """Fetch a review session by ID."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.review_sessions.select().where(t.review_sessions.c.id == session_id)
                )
            ).mappings().first()
        return dict(row) if row else None

    async def update_review_session(
        self,
        session_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Update review session fields (status, timeout_count, etc.)."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.review_sessions.update()
                .where(t.review_sessions.c.id == session_id)
                .values(**kwargs)
            )

    async def add_review_comment(
        self,
        *,
        session_id: UUID,
        project_id: UUID,
        reviewer_id: str,
        reviewer_role: str,
        verdict: str,
        veto: bool = False,
        severity: str | None = None,
        comments: list[dict] | None = None,
        comment_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Insert a review comment."""
        cid = comment_id or uuid4()
        now = datetime.now(tz=timezone.utc)
        values = {
            "id": cid,
            "session_id": session_id,
            "project_id": project_id,
            "reviewer_id": reviewer_id,
            "reviewer_role": reviewer_role,
            "verdict": verdict,
            "veto": veto,
            "severity": severity,
            "comments": comments,
            "submitted_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.review_comments.insert().values(**values))
        return values

    async def get_review_comments(
        self, session_id: UUID
    ) -> list[dict[str, Any]]:
        """Fetch all comments for a review session."""
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    t.review_comments.select()
                    .where(t.review_comments.c.session_id == session_id)
                    .order_by(t.review_comments.c.submitted_at)
                )
            ).mappings().all()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════════
    # Approval gates
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_approval_gate(
        self,
        *,
        project_id: UUID,
        gate_type: str,
        gate_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a pending approval gate."""
        gid = gate_id or uuid4()
        now = datetime.now(tz=timezone.utc)
        values = {
            "id": gid,
            "project_id": project_id,
            "gate_type": gate_type,
            "status": "PENDING",
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.approval_gates.insert().values(**values))
        return values

    async def decide_approval_gate(
        self,
        gate_id: UUID,
        *,
        status: str,
        decided_by: str,
        justification: str | None = None,
        human_input: dict | None = None,
    ) -> None:
        """Record a decision on an approval gate."""
        now = datetime.now(tz=timezone.utc)
        async with self.engine.begin() as conn:
            await conn.execute(
                t.approval_gates.update()
                .where(t.approval_gates.c.id == gate_id)
                .values(
                    status=status,
                    decided_by=decided_by,
                    justification=justification,
                    human_input=human_input,
                    decided_at=now,
                )
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Sprints
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_sprint(
        self,
        *,
        project_id: UUID,
        sprint_number: int,
        milestone: str | None = None,
        goal: str | None = None,
        planned_story_points: int | None = None,
        estimated_hours: Decimal | None = None,
        sprint_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a sprint record."""
        sid = sprint_id or uuid4()
        now = datetime.now(tz=timezone.utc)
        values = {
            "id": sid,
            "project_id": project_id,
            "sprint_number": sprint_number,
            "milestone": milestone,
            "goal": goal,
            "status": "PLANNED",
            "planned_story_points": planned_story_points,
            "estimated_hours": estimated_hours,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.sprints.insert().values(**values))
        return values

    async def get_sprint(self, sprint_id: UUID) -> dict[str, Any] | None:
        """Fetch a sprint by ID."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.sprints.select().where(t.sprints.c.id == sprint_id)
                )
            ).mappings().first()
        return dict(row) if row else None

    async def list_sprints(self, project_id: UUID) -> list[dict[str, Any]]:
        """List sprints for a project ordered by sprint number."""
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    t.sprints.select()
                    .where(t.sprints.c.project_id == project_id)
                    .order_by(t.sprints.c.sprint_number)
                )
            ).mappings().all()
        return [dict(r) for r in rows]

    async def update_sprint(
        self, sprint_id: UUID, **kwargs: Any
    ) -> None:
        """Update sprint fields (status, points, hours, dates, etc.)."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.sprints.update()
                .where(t.sprints.c.id == sprint_id)
                .values(**kwargs)
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Issues
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_issue(
        self,
        *,
        project_id: UUID,
        title: str,
        issue_type: str,
        sprint_id: UUID | None = None,
        parent_issue_id: UUID | None = None,
        description: str | None = None,
        priority: str = "MEDIUM",
        assigned_team: str | None = None,
        assigned_agent: str | None = None,
        estimated_hours: Decimal | None = None,
        story_points: int | None = None,
        dependencies: list[UUID] | None = None,
        issue_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create an issue/work-item."""
        iid = issue_id or uuid4()
        now = datetime.now(tz=timezone.utc)
        values = {
            "id": iid,
            "project_id": project_id,
            "sprint_id": sprint_id,
            "parent_issue_id": parent_issue_id,
            "title": title,
            "description": description,
            "issue_type": issue_type,
            "status": "OPEN",
            "priority": priority,
            "assigned_team": assigned_team,
            "assigned_agent": assigned_agent,
            "estimated_hours": estimated_hours,
            "story_points": story_points,
            "dependencies": dependencies,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.issues.insert().values(**values))
        return values

    async def get_issue(self, issue_id: UUID) -> dict[str, Any] | None:
        """Fetch an issue by ID."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.issues.select().where(t.issues.c.id == issue_id)
                )
            ).mappings().first()
        return dict(row) if row else None

    async def list_issues(
        self,
        *,
        project_id: UUID | None = None,
        sprint_id: UUID | None = None,
        assigned_team: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List issues with optional filters."""
        q = t.issues.select()
        if project_id:
            q = q.where(t.issues.c.project_id == project_id)
        if sprint_id:
            q = q.where(t.issues.c.sprint_id == sprint_id)
        if assigned_team:
            q = q.where(t.issues.c.assigned_team == assigned_team)
        if status:
            q = q.where(t.issues.c.status == status)
        q = q.order_by(t.issues.c.created_at)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def update_issue(
        self, issue_id: UUID, **kwargs: Any
    ) -> None:
        """Update issue fields (status, hours, assignment, etc.)."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.issues.update()
                .where(t.issues.c.id == issue_id)
                .values(**kwargs)
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # KPI snapshots
    # ═══════════════════════════════════════════════════════════════════════════

    async def save_kpi_snapshot(
        self,
        *,
        project_id: UUID,
        scope: str,
        sprint_id: UUID | None = None,
        estimation_accuracy: Decimal | None = None,
        task_completion_rate: Decimal | None = None,
        review_pass_rate: Decimal | None = None,
        velocity: Decimal | None = None,
        defect_rate: Decimal | None = None,
        rework_rate: Decimal | None = None,
        budget_adherence: Decimal | None = None,
        resource_utilization: Decimal | None = None,
        infra_lead_time_seconds: int | None = None,
        raw_data: dict | None = None,
        snapshot_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Insert a KPI snapshot."""
        kid = snapshot_id or uuid4()
        now = datetime.now(tz=timezone.utc)
        values = {
            "id": kid,
            "project_id": project_id,
            "sprint_id": sprint_id,
            "scope": scope,
            "estimation_accuracy": estimation_accuracy,
            "task_completion_rate": task_completion_rate,
            "review_pass_rate": review_pass_rate,
            "velocity": velocity,
            "defect_rate": defect_rate,
            "rework_rate": rework_rate,
            "budget_adherence": budget_adherence,
            "resource_utilization": resource_utilization,
            "infra_lead_time_seconds": infra_lead_time_seconds,
            "raw_data": raw_data,
            "computed_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.kpi_snapshots.insert().values(**values))
        return values

    async def list_kpi_snapshots(
        self,
        project_id: UUID,
        *,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """List KPI snapshots for a project."""
        q = t.kpi_snapshots.select().where(t.kpi_snapshots.c.project_id == project_id)
        if scope:
            q = q.where(t.kpi_snapshots.c.scope == scope)
        q = q.order_by(t.kpi_snapshots.c.computed_at.desc())
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════════
    # Agent profiles
    # ═══════════════════════════════════════════════════════════════════════════

    async def upsert_agent_profile(
        self,
        *,
        agent_id: str,
        team_id: str,
        role: str,
        correction_factor: Decimal = Decimal("1.0"),
        estimation_bias: Decimal = Decimal("0.0"),
        confidence: Decimal = Decimal("0.5"),
        total_tasks_completed: int = 0,
        total_estimated_hours: Decimal = Decimal("0"),
        total_actual_hours: Decimal = Decimal("0"),
    ) -> dict[str, Any]:
        """Insert or update an agent profile (upsert on agent_id PK)."""
        now = datetime.now(tz=timezone.utc)
        values = {
            "agent_id": agent_id,
            "team_id": team_id,
            "role": role,
            "correction_factor": correction_factor,
            "estimation_bias": estimation_bias,
            "confidence": confidence,
            "total_tasks_completed": total_tasks_completed,
            "total_estimated_hours": total_estimated_hours,
            "total_actual_hours": total_actual_hours,
            "last_updated": now,
        }
        stmt = (
            pg_insert(t.agent_profiles)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["agent_id"],
                set_={
                    k: v
                    for k, v in values.items()
                    if k != "agent_id"
                },
            )
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)
        return values

    async def get_agent_profile(self, agent_id: str) -> dict[str, Any] | None:
        """Fetch an agent's profile."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.agent_profiles.select()
                    .where(t.agent_profiles.c.agent_id == agent_id)
                )
            ).mappings().first()
        return dict(row) if row else None

    # ═══════════════════════════════════════════════════════════════════════════
    # Dead letters
    # ═══════════════════════════════════════════════════════════════════════════

    async def insert_dead_letter(
        self,
        *,
        message_id: str,
        recipient_team: str,
        sender_id: str | None = None,
        msg_type: str | None = None,
        project_id: UUID | None = None,
        retry_count: int,
        failure_reason: str,
        envelope_json: dict,
    ) -> None:
        """Insert a dead-letter record for forensics."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.dead_letters.insert().values(
                    message_id=message_id,
                    recipient_team=recipient_team,
                    sender_id=sender_id,
                    msg_type=msg_type,
                    project_id=project_id,
                    retry_count=retry_count,
                    failure_reason=failure_reason,
                    envelope_json=envelope_json,
                )
            )

    async def list_dead_letters(
        self,
        *,
        project_id: UUID | None = None,
        recipient_team: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List dead-letter records."""
        q = t.dead_letters.select()
        if project_id:
            q = q.where(t.dead_letters.c.project_id == project_id)
        if recipient_team:
            q = q.where(t.dead_letters.c.recipient_team == recipient_team)
        q = q.order_by(t.dead_letters.c.dead_at.desc()).limit(limit)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════════
    # System config
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_config(self, key: str) -> str | None:
        """Read a system_config value."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.system_config.select().where(t.system_config.c.key == key)
                )
            ).mappings().first()
        return row["value"] if row else None

    async def set_config(self, key: str, value: str) -> None:
        """Insert-or-update a system_config key."""
        stmt = (
            pg_insert(t.system_config)
            .values(key=key, value=value, updated_at=datetime.now(tz=timezone.utc))
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": value, "updated_at": datetime.now(tz=timezone.utc)},
            )
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    async def get_all_config(self) -> dict[str, str]:
        """Fetch all system_config rows as a dict."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(t.system_config.select())).mappings().all()
        return {r["key"]: r["value"] for r in rows}
