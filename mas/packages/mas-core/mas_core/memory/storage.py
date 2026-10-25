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

import hashlib
import json
import logging
import math
import random
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ..company_manifest import (
    DEFAULT_COMPANY_ID,
    validate_external_worker_assignment_approvals,
)
from ..observability.native_spans import build_native_trace_span
from ..observability.tracing import current_span_id, current_trace_id, new_span_id
from ..sandbox_policy import canonical_sandbox_class
from ..worker_registry.worker_run_readiness import worker_run_project_scope_blocker
from ..workflow.states import ProjectState
from . import models as t

if TYPE_CHECKING:
    from ..workflow.self_improvement import ImprovementOpportunity

logger = logging.getLogger(__name__)

_AGENT_PROFILE_NUMERIC_MIN = Decimal("-9.9999")
_AGENT_PROFILE_NUMERIC_MAX = Decimal("9.9999")

# Provider payloads are retained as forensic evidence, but must not become an
# unbounded database/blob sink.  The gateway applies the same limit before the
# request reaches this service; keeping the invariant here protects direct
# callers and tests as well.
_PM_RAW_BODY_MAX_BYTES = 1 * 1024 * 1024
TERMINAL_WORKER_RUN_STATES = ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT")


def _flow_instance_matches_snapshot(
    instance: Mapping[str, Any], snapshot: Mapping[str, Any] | None
) -> bool:
    """Compare the state fields used by a caller's precomputed transition."""
    if snapshot is None:
        return True
    for key, expected in snapshot.items():
        actual = instance.get(key)
        if key == "active_node_ids":
            actual = list(actual or [])
            expected = list(expected or [])
        elif key == "context_json":
            actual = dict(actual or {})
            expected = dict(expected or {})
        if actual != expected:
            return False
    return True


_ROLLOUT_WORKER_IDENTITY_FIELDS = (
    "id",
    "name",
    "adapter_type",
    "adapter_config",
    "sandbox_profile",
    "capability_ids",
    "team_id",
    "status",
    "version",
    "source_repo",
    "source_revision",
    "version_pin",
    "update_policy",
    "evaluation_status",
    "adapter_entrypoint",
    "adapter_module",
    "wrapper_config",
    "isolation_mode",
    "active_shell_version_id",
    "active_adapter_id",
    "active_skill_bundle_id",
    "model_profile_id",
    "model_mode",
    "upstream_commit_sha",
)


def worker_identity_snapshot_for_rollout(worker: Mapping[str, Any]) -> dict[str, Any]:
    """Capture the worker fields that must remain unchanged during promotion.

    Health counters and timestamps are intentionally omitted: routine health
    probes must not invalidate a rollout. The adapter configuration and
    immutable-version pointers are compared in full so sandbox, image,
    capability, or provenance-relevant changes fail closed.
    """

    snapshot = {
        field: worker.get(field) for field in _ROLLOUT_WORKER_IDENTITY_FIELDS
    }
    return json.loads(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    )


_ROLLOUT_PROVENANCE_IDENTITY_FIELDS = (
    "canonical_source_repository",
    "source_provider",
    "exact_release",
    "commit_sha",
    "package_version",
    "oci_image_digest",
    "dependency_lock_hash",
    "protocol_api_version",
    "adapter_version",
    "transport_type",
    "runtime_fingerprint",
)


def _rollout_provenance_identity_matches(
    provenance: Mapping[str, Any],
    candidate_record: Mapping[str, Any],
    worker: Mapping[str, Any],
) -> bool:
    """Ensure the locked provenance, reviewed candidate, and worker still bind."""

    candidate_provenance = candidate_record.get("source_provenance")
    if not isinstance(candidate_provenance, Mapping):
        return False
    if any(
        candidate_provenance.get(field) != provenance.get(field)
        for field in _ROLLOUT_PROVENANCE_IDENTITY_FIELDS
    ):
        return False

    repository = str(provenance.get("canonical_source_repository") or "")
    worker_repository = str(worker.get("source_repo") or "")
    if not repository or repository != worker_repository:
        return False
    transport = str(provenance.get("transport_type") or "").strip().lower()
    if not transport or transport != str(worker.get("adapter_type") or "").strip().lower():
        return False
    commit_sha = str(provenance.get("commit_sha") or "")
    worker_revision = str(worker.get("source_revision") or "")
    if commit_sha and worker_revision and commit_sha != worker_revision:
        return False
    exact_release = str(provenance.get("exact_release") or "")
    worker_version = str(worker.get("version") or "")
    if exact_release and worker_version and exact_release != worker_version:
        return False

    image_digest = str(provenance.get("oci_image_digest") or "")
    if transport == "oci":
        config = worker.get("adapter_config")
        image = str(config.get("image") or "") if isinstance(config, Mapping) else ""
        if not image_digest or not image.endswith(f"@{image_digest}"):
            return False
    return True


def _governance_json_snapshot(value: Any) -> Any:
    """Normalize governance snapshots the same way JSONB persists them."""

    return json.loads(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    )


def _skill_bundle_snapshot_content_hash(
    bundle_snapshot: Mapping[str, Any],
) -> str | None:
    """Recompute SkillBundle.content_hash from its serialized model snapshot.

    ``content_hash`` is a computed property, not a serialized field. The model
    hashes its JSON dump with the bundle ID and lifecycle status excluded.
    """
    payload = {
        key: value
        for key, value in bundle_snapshot.items()
        if key not in {"bundle_id", "status"}
    }
    try:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode()).hexdigest()


def _candidate_governance_snapshot_matches(
    *,
    worker: Mapping[str, Any],
    provenance: Mapping[str, Any],
    steward: Mapping[str, Any],
    candidate: Mapping[str, Any],
    expected_worker_identity: Mapping[str, Any],
    expected_provenance_hash: str,
    expected_scan_report_sha256: str,
    expected_candidate_snapshot: Mapping[str, Any],
) -> bool:
    """Check locked first-party governance rows against the API precheck."""

    from mas_core.worker_registry.source_bound_security_scan import (
        FIRST_PARTY_SOURCE_REPOSITORY,
        FIRST_PARTY_WORKER_ID,
    )

    if worker_identity_snapshot_for_rollout(worker) != _governance_json_snapshot(
        expected_worker_identity
    ):
        return False
    if (
        str(worker.get("name") or "") != FIRST_PARTY_WORKER_ID
        or str(provenance.get("source_provider") or "").lower() != "aiat"
        or str(provenance.get("canonical_source_repository") or "")
        != FIRST_PARTY_SOURCE_REPOSITORY
        or str(provenance.get("security_scan_status") or "").lower() != "passed"
        or str(provenance.get("provenance_hash") or "")
        != expected_provenance_hash
    ):
        return False

    metadata = steward.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    provenance_extra = metadata.get("source_provenance_extra")
    provenance_extra = provenance_extra if isinstance(provenance_extra, Mapping) else {}
    scan_report = provenance_extra.get("source_bound_security_scan")
    if (
        not isinstance(scan_report, Mapping)
        or str(scan_report.get("status") or "").lower() != "passed"
        or str(scan_report.get("report_sha256") or "")
        != expected_scan_report_sha256
    ):
        return False

    expected = _governance_json_snapshot(expected_candidate_snapshot)
    evidence = candidate.get("evidence_json")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    candidate_record = evidence.get("candidate_record")
    return (
        str(candidate.get("id") or "") == str(expected.get("id") or "")
        and str(candidate.get("worker_id") or "")
        == str(expected.get("worker_id") or "")
        and str(candidate.get("skill_bundle_id") or "")
        == str(expected.get("skill_bundle_id") or "")
        and (
            str(candidate.get("adapter_id") or "") or None
        )
        == (str(expected.get("adapter_id") or "") or None)
        and str(candidate.get("intake_status") or "")
        == str(expected.get("intake_status") or "")
        and str(candidate.get("certification_run_id") or "")
        == str(expected.get("certification_run_id") or "")
        and str(candidate.get("approval_record_id") or "")
        == str(expected.get("approval_record_id") or "")
        and _governance_json_snapshot(dict(evidence))
        == expected.get("evidence_json")
        and isinstance(candidate_record, Mapping)
        and _governance_json_snapshot(dict(candidate_record))
        == expected.get("candidate_record")
        and _rollout_provenance_identity_matches(
            provenance,
            candidate_record,
            worker,
        )
    )


def document_to_context_item(document: dict[str, Any]) -> dict[str, Any]:
    """Project a canonical document row into the project-context shape.

    Documents intentionally remain the source of truth for lifecycle and
    revision state.  This read-model projection lets project workspaces expose
    generated documents alongside uploaded files and notes without copying
    document rows into ``project_context_items`` or making them deletable as
    ordinary context attachments.
    """
    document_id = document.get("id")
    doc_type = str(document.get("doc_type") or "DOCUMENT").upper()
    try:
        version = int(document.get("version") or 1)
    except (TypeError, ValueError):
        version = 1
    status = str(document.get("status") or "DRAFT").upper()
    blob_key = document.get("blob_key")
    blob_key_text = str(blob_key or "").lower()
    mime_type = {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".html": "text/html",
        ".txt": "text/plain",
    }.get(
        next(
            (suffix for suffix in (".md", ".markdown", ".json", ".pdf", ".html", ".txt") if blob_key_text.endswith(suffix)),
            "",
        ),
        "application/octet-stream",
    )
    lineage_id = document.get("lineage_id") or document_id

    return {
        "id": document_id,
        "project_id": document.get("project_id"),
        "item_type": "DOCUMENT",
        "name": f"{doc_type} v{version}",
        "description": (
            f"Generated {doc_type} document · revision {version} · "
            f"{status.replace('_', ' ').lower()}"
        ),
        "mime_type": mime_type,
        "size_bytes": None,
        "blob_bucket": document.get("blob_bucket"),
        "blob_key": blob_key,
        "blob_sha256": document.get("blob_sha256"),
        "url": None,
        "content_text": None,
        "metadata": {
            "source": "document",
            "document_id": document_id,
            "lineage_id": lineage_id,
            "doc_type": doc_type,
            "version": version,
            "status": status,
        },
        "tags": [doc_type.lower(), "generated", "document"],
        "created_by": document.get("created_by") or "orchestrator",
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at") or document.get("created_at"),
        "source": "document",
        "read_only": True,
        "document_id": document_id,
        "doc_type": doc_type,
        "version": version,
        "status": status,
        "lineage_id": lineage_id,
    }


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
        human_requester: str | None = None,
        config: dict | None = None,
        initial_context: list[dict[str, Any]] | None = None,
        project_id: UUID | None = None,
        company_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Insert a project and optional starter context atomically.

        Keeping the initial brief/links in the same transaction means the CEO
        feasibility directive can immediately see the project context and a
        failed context insert cannot leave a half-initialized project behind.
        """
        pid = project_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": pid,
            "name": name,
            "description": description,
            "state": state,
            "created_by": created_by,
            "human_requester": human_requester,
            "company_id": company_id or DEFAULT_COMPANY_ID,
            "config": config,
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.projects.insert().values(**values))
            for seed in initial_context or []:
                context_values = {
                    "id": seed.get("id") or uuid4(),
                    "project_id": pid,
                    "item_type": seed.get("item_type") or "TEXT",
                    "name": seed.get("name") or "Project context",
                    "description": seed.get("description"),
                    "mime_type": seed.get("mime_type"),
                    "size_bytes": seed.get("size_bytes"),
                    "blob_bucket": seed.get("blob_bucket"),
                    "blob_key": seed.get("blob_key"),
                    "blob_sha256": seed.get("blob_sha256"),
                    "url": seed.get("url"),
                    "content_text": seed.get("content_text"),
                    "metadata": seed.get("metadata"),
                    "tags": seed.get("tags") or [],
                    "created_by": seed.get("created_by") or created_by,
                    "created_at": now,
                }
                await conn.execute(t.project_context_items.insert().values(**context_values))
            await self._enqueue_project_projections_tx(conn, values)
        return {**values, "failure_reason": None, "failed_from_state": None}

    async def create_self_improvement_project(
        self,
        opportunity: ImprovementOpportunity,
        *,
        project_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Persist an improvement opportunity through the canonical project writer.

        The opportunity model is a request projection, not a second project
        store.  This method delegates to :meth:`create_project`, preserving
        the normal project transaction, projection enqueue, company scope, and
        lifecycle ownership while retaining risk/budget/evidence metadata in
        the project config.
        """

        from ..workflow.self_improvement import SelfImprovementLifecycle

        request = opportunity.canonical_project_request(project_id=project_id)
        lifecycle = SelfImprovementLifecycle.create(opportunity)
        lifecycle.bind_project(
            request["project_id"],
            actor=opportunity.created_by,
            actor_kind=opportunity.created_by_kind,
        )
        request_config = dict(request["config"])
        self_improvement_config = dict(request_config["self_improvement"])
        self_improvement_config["lifecycle"] = lifecycle.as_dict()
        request_config["self_improvement"] = self_improvement_config
        return await self.create_project(
            name=str(request["name"]),
            description=str(request["description"]),
            state=str(request["state"]),
            created_by=str(request["created_by"]),
            human_requester=(
                str(request["human_requester"])
                if request.get("human_requester") is not None
                else None
            ),
            config=request_config,
            project_id=request["project_id"],
            company_id=request.get("company_id"),
        )

    async def get_self_improvement_lifecycle(
        self,
        project_id: UUID,
    ) -> dict[str, Any] | None:
        """Read the validated lifecycle snapshot from the canonical project.

        Self-improvement state deliberately lives inside the project config
        rather than in a second project database.  Rehydrating through the
        typed contract catches stale or malformed snapshots before API clients
        observe them.
        """

        from ..workflow.self_improvement import SelfImprovementLifecycle

        project = await self.get_project(project_id)
        if project is None:
            return None
        config = project.get("config") or {}
        self_improvement = config.get("self_improvement") or {}
        snapshot = self_improvement.get("lifecycle")
        if not isinstance(snapshot, Mapping):
            return None
        lifecycle = SelfImprovementLifecycle.from_dict(snapshot)
        if lifecycle.project_id != project_id:
            raise ValueError("self-improvement lifecycle project binding does not match project")
        return lifecycle.as_dict()

    async def update_self_improvement_lifecycle(
        self,
        project_id: UUID,
        lifecycle: Any,
        *,
        actor: str,
    ) -> dict[str, Any] | None:
        """Persist a lifecycle snapshot with project-row locking and CAS.

        The caller supplies the snapshot it read.  The current lifecycle
        revision must match; the method then increments it, updates the
        canonical project configuration, and appends a project-history record
        in the same transaction.  ``None`` signals a stale revision or a
        missing project, allowing the API to return a conflict without
        overwriting newer gate/reference evidence.
        """

        from ..workflow.self_improvement import SelfImprovementLifecycle

        if not isinstance(lifecycle, SelfImprovementLifecycle):
            lifecycle = SelfImprovementLifecycle.from_dict(lifecycle)
        lifecycle.assert_invariants()
        if lifecycle.project_id != project_id:
            raise ValueError("self-improvement lifecycle project binding does not match project")
        if not actor.strip():
            raise ValueError("self-improvement lifecycle updates require an actor")

        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            current_row = (
                await conn.execute(
                    t.projects.select().where(t.projects.c.id == project_id).with_for_update()
                )
            ).mappings().first()
            if current_row is None:
                return None
            current = dict(current_row)
            current_config = dict(current.get("config") or {})
            current_self_improvement = dict(current_config.get("self_improvement") or {})
            current_snapshot = current_self_improvement.get("lifecycle")
            if not isinstance(current_snapshot, Mapping):
                raise ValueError("project has no self-improvement lifecycle snapshot")
            from ..workflow.self_improvement import SelfImprovementLifecycle

            current_lifecycle = SelfImprovementLifecycle.from_dict(current_snapshot)
            if lifecycle.revision != current_lifecycle.revision:
                return None
            lifecycle.revision = current_lifecycle.revision + 1
            persisted_snapshot = lifecycle.as_dict()
            current_self_improvement["lifecycle"] = persisted_snapshot
            current_config["self_improvement"] = current_self_improvement
            await conn.execute(
                t.projects.update()
                .where(t.projects.c.id == project_id)
                .values(
                    config=current_config,
                    revision=t.projects.c.revision + 1,
                    updated_at=now,
                )
            )
            from_status = str(current_lifecycle.status)
            to_status = str(lifecycle.status)
            await conn.execute(
                t.project_state_history.insert().values(
                    project_id=project_id,
                    from_state=from_status,
                    to_state=to_status,
                    event=(
                        "SELF_IMPROVEMENT_LIFECYCLE"
                        if from_status != to_status
                        else "SELF_IMPROVEMENT_METADATA"
                    ),
                    triggered_by=actor.strip(),
                    payload={
                        "schema_version": lifecycle.schema_version,
                        "lifecycle_revision": lifecycle.revision,
                        "from_status": from_status,
                        "to_status": to_status,
                        "integration_ref_kinds": sorted(lifecycle.integration_refs),
                    },
                    transitioned_at=now,
                )
            )
            refreshed_row = (
                await conn.execute(t.projects.select().where(t.projects.c.id == project_id))
            ).mappings().first()
            if refreshed_row is not None:
                refreshed = dict(refreshed_row)
                await self._enqueue_project_projections_tx(conn, refreshed)
        return await self.get_project(project_id)

    async def get_project(self, project_id: UUID) -> dict[str, Any] | None:
        """Fetch a project by ID."""
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(t.projects.select().where(t.projects.c.id == project_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    # ═══════════════════════════════════════════════════════════════════════════
    # Company manifests and company-scoped assignments
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_company(self, company_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.companies, t.companies.c.id, company_id)

    async def get_company_by_slug(self, slug: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(t.companies.select().where(t.companies.c.slug == slug))).mappings().first()
        return dict(row) if row else None

    async def list_companies(self, *, status: str | None = None) -> list[dict[str, Any]]:
        query = t.companies.select().order_by(t.companies.c.slug)
        if status is not None:
            query = query.where(t.companies.c.status == status)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_company_manifest(self, company_id: UUID, *, version: int | None = None) -> dict[str, Any] | None:
        query = t.company_manifest_versions.select().where(t.company_manifest_versions.c.company_id == company_id)
        if version is None:
            company = await self.get_company(company_id)
            active_id = company.get("active_manifest_version_id") if company else None
            if active_id:
                query = query.where(t.company_manifest_versions.c.id == active_id)
            else:
                query = query.order_by(t.company_manifest_versions.c.manifest_version.desc())
        else:
            query = query.where(t.company_manifest_versions.c.manifest_version == version)
        async with self.engine.connect() as conn:
            row = (await conn.execute(query.limit(1))).mappings().first()
        return dict(row) if row else None

    async def list_company_manifest_versions(
        self,
        company_id: UUID,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return immutable manifest history newest first."""
        query = (
            t.company_manifest_versions.select()
            .where(t.company_manifest_versions.c.company_id == company_id)
            .order_by(t.company_manifest_versions.c.manifest_version.desc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_company_departments(self, company_id: UUID) -> list[dict[str, Any]]:
        query = t.company_departments.select().where(t.company_departments.c.company_id == company_id).order_by(t.company_departments.c.department_key)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_company_worker_assignments(self, company_id: UUID) -> list[dict[str, Any]]:
        query = t.company_worker_assignments.select().where(t.company_worker_assignments.c.company_id == company_id).order_by(t.company_worker_assignments.c.created_at)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_company_budgets(self, company_id: UUID) -> list[dict[str, Any]]:
        query = (
            t.company_budgets.select()
            .where(t.company_budgets.c.company_id == company_id)
            .order_by(t.company_budgets.c.budget_key)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_company_read_model(self, company_id: UUID) -> dict[str, Any]:
        async with self.engine.connect() as conn:
            return await self._company_manifest_read_model(company_id, conn=conn)

    async def apply_company_manifest(
        self,
        *,
        company_id: UUID,
        manifest: Any,
        digest: str,
        canonical: dict[str, Any],
        source: str,
        actor: str,
        expected_manifest_version: int | None = None,
        compiler_version: str = "aiat-company-compiler/1",
    ) -> dict[str, Any]:
        """Compile one validated manifest atomically and idempotently."""

        async with self.engine.begin() as conn:
            company = (await conn.execute(t.companies.select().where(t.companies.c.id == company_id).with_for_update())).mappings().first()
            if company is None:
                raise ValueError(f"company {company_id} does not exist")
            existing = (
                await conn.execute(
                    t.company_manifest_versions.select().where(
                        sa.and_(
                            t.company_manifest_versions.c.company_id == company_id,
                            t.company_manifest_versions.c.digest == digest,
                        )
                    )
                )
            ).mappings().first()
            if existing is not None:
                return await self._company_manifest_read_model(company_id, conn=conn)

            if expected_manifest_version is not None:
                active_manifest = None
                active_manifest_id = company.get("active_manifest_version_id")
                if active_manifest_id is not None:
                    active_manifest = (
                        await conn.execute(
                            t.company_manifest_versions.select().where(
                                t.company_manifest_versions.c.id == active_manifest_id
                            )
                        )
                    ).mappings().first()
                current_manifest_version = int(
                    active_manifest["manifest_version"] if active_manifest is not None else 0
                )
                if current_manifest_version != expected_manifest_version:
                    raise ValueError(
                        "COMPANY_MANIFEST_VERSION_CONFLICT: "
                        f"expected {expected_manifest_version}, current {current_manifest_version}"
                    )

            prior = (
                await conn.execute(
                    sa.select(sa.func.max(t.company_manifest_versions.c.manifest_version)).where(
                        t.company_manifest_versions.c.company_id == company_id
                    )
                )
            ).scalar()
            manifest_version = int(prior or 0) + 1
            manifest_id = uuid4()
            await conn.execute(
                t.company_manifest_versions.insert().values(
                    id=manifest_id,
                    company_id=company_id,
                    schema_version=manifest.schema_version,
                    manifest_version=manifest_version,
                    digest=digest,
                    source=source,
                    manifest_json=canonical,
                    compiler_version=compiler_version,
                    status="APPLIED",
                    compiled_by=actor,
                )
            )
            await self._materialize_company_manifest_tx(
                conn,
                company_id=company_id,
                manifest=manifest,
                manifest_id=manifest_id,
                actor=actor,
            )
            await conn.execute(
                t.companies.update().where(t.companies.c.id == company_id).values(
                    slug=manifest.slug,
                    name=manifest.name,
                    description=manifest.description,
                    active_manifest_version_id=manifest_id,
                    updated_at=datetime.now(tz=UTC),
                )
            )
            return await self._company_manifest_read_model(company_id, conn=conn)

    async def _company_manifest_read_model(self, company_id: UUID, *, conn: Any) -> dict[str, Any]:
        company = (await conn.execute(t.companies.select().where(t.companies.c.id == company_id))).mappings().first()
        manifest = (await conn.execute(t.company_manifest_versions.select().where(t.company_manifest_versions.c.id == company["active_manifest_version_id"]))).mappings().first() if company and company.get("active_manifest_version_id") else None
        departments = (await conn.execute(t.company_departments.select().where(t.company_departments.c.company_id == company_id).order_by(t.company_departments.c.department_key))).mappings().all()
        assignments = (await conn.execute(t.company_worker_assignments.select().where(t.company_worker_assignments.c.company_id == company_id).order_by(t.company_worker_assignments.c.created_at))).mappings().all()
        budgets = (await conn.execute(t.company_budgets.select().where(t.company_budgets.c.company_id == company_id).order_by(t.company_budgets.c.budget_key))).mappings().all()
        return {
            "company": dict(company) if company else None,
            "manifest": dict(manifest) if manifest else None,
            "departments": [dict(row) for row in departments],
            "assignments": [dict(row) for row in assignments],
            "budgets": [dict(row) for row in budgets],
        }

    async def _materialize_company_manifest_tx(
        self,
        conn: Any,
        *,
        company_id: UUID,
        manifest: Any,
        manifest_id: UUID,
        actor: str | None = None,
    ) -> None:
        """Reconcile compiled departments, assignments, and budgets in one transaction."""
        workers: dict[str, Any] = {}
        worker_names = {manifest.ceo_worker_id}
        worker_names.update(item.worker_id for item in manifest.worker_assignments)
        for worker_name in worker_names:
            row = (
                await conn.execute(
                    t.worker_registry.select().where(t.worker_registry.c.name == worker_name)
                )
            ).mappings().first()
            if row is None:
                raise ValueError(f"manifest references unknown worker {worker_name!r}")
            workers[worker_name] = row

        # Recheck inside the same transaction that materializes assignments.
        # The API validation is useful feedback, while this protects direct
        # storage callers and closes a provenance-change race before commit.
        validate_external_worker_assignment_approvals(
            manifest,
            workers,
            approving_actor=actor,
            require_operator_approval=True,
        )

        now = datetime.now(tz=UTC)
        await conn.execute(
            t.company_departments.update()
            .where(t.company_departments.c.company_id == company_id)
            .values(status="INACTIVE", updated_at=now)
        )
        await conn.execute(
            t.company_worker_assignments.update()
            .where(t.company_worker_assignments.c.company_id == company_id)
            .values(status="INACTIVE", updated_at=now)
        )
        department_ids: dict[str, UUID] = {}
        for department in manifest.departments:
            department_id = uuid5(company_id, f"department:{department.id}")
            chief = workers.get(department.chief_worker_id) if department.chief_worker_id else None
            await conn.execute(
                pg_insert(t.company_departments)
                .values(
                    id=department_id,
                    company_id=company_id,
                    department_key=department.id,
                    name=department.name,
                    chief_worker_id=chief.get("id") if chief else None,
                    approval_policy=department.approval_policy,
                    metadata=department.metadata,
                    status="ACTIVE",
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_company_department_key",
                    set_={
                        "name": department.name,
                        "chief_worker_id": chief.get("id") if chief else None,
                        "approval_policy": department.approval_policy,
                        "metadata": department.metadata,
                        "status": "ACTIVE",
                        "updated_at": now,
                    },
                )
            )

            department_ids[department.id] = department_id
        for assignment in manifest.worker_assignments:
            worker = workers[assignment.worker_id]
            assignment_id = uuid5(company_id, f"worker:{assignment.worker_id}")
            await conn.execute(
                pg_insert(t.company_worker_assignments)
                .values(
                    id=assignment_id,
                    company_id=company_id,
                    worker_id=worker["id"],
                    department_id=department_ids[assignment.department_id],
                    manifest_version_id=manifest_id,
                    status=assignment.status,
                    tool_grants=assignment.tool_grants,
                    permission_grants=assignment.permission_grants,
                    model_profile_id=assignment.model_profile_id,
                    budget=assignment.budget,
                    approval_required=assignment.approval_required,
                    metadata=assignment.metadata,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_company_worker_assignment",
                    set_={
                        "department_id": department_ids[assignment.department_id],
                        "manifest_version_id": manifest_id,
                        "status": assignment.status,
                        "tool_grants": assignment.tool_grants,
                        "permission_grants": assignment.permission_grants,
                        "model_profile_id": assignment.model_profile_id,
                        "budget": assignment.budget,
                        "approval_required": assignment.approval_required,
                        "metadata": assignment.metadata,
                        "updated_at": now,
                    },
                )
            )
        if manifest.budgets:
            await conn.execute(
                t.company_budgets.delete().where(
                    sa.and_(
                        t.company_budgets.c.company_id == company_id,
                        t.company_budgets.c.budget_key.not_in(list(manifest.budgets)),
                    )
                )
            )
        else:
            await conn.execute(
                t.company_budgets.delete().where(t.company_budgets.c.company_id == company_id)
            )
        for budget_key, limit_value in manifest.budgets.items():
            await conn.execute(
                pg_insert(t.company_budgets)
                .values(
                    id=uuid5(company_id, f"budget:{budget_key}"),
                    company_id=company_id,
                    budget_key=budget_key,
                    limit_value=limit_value,
                    currency="USD",
                    period="lifetime",
                    metadata={},
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_company_budget_key",
                    set_={"limit_value": limit_value, "updated_at": now},
                )
            )

    async def rollback_company_manifest(
        self,
        company_id: UUID,
        *,
        manifest_version: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Activate a previously compiled manifest and reconcile its snapshot."""
        from mas_core.company_manifest import compile_company_manifest

        async with self.engine.begin() as conn:
            company = (
                await conn.execute(
                    t.companies.select()
                    .where(t.companies.c.id == company_id)
                    .with_for_update()
                )
            ).mappings().first()
            if company is None:
                raise ValueError(f"company {company_id} does not exist")
            target = (
                await conn.execute(
                    t.company_manifest_versions.select().where(
                        sa.and_(
                            t.company_manifest_versions.c.company_id == company_id,
                            t.company_manifest_versions.c.manifest_version == manifest_version,
                        )
                    )
                )
            ).mappings().first()
            if target is None:
                raise ValueError(f"manifest version {manifest_version} does not exist")
            manifest, _digest, _canonical = compile_company_manifest(target["manifest_json"])
            active_id = company.get("active_manifest_version_id")
            await self._materialize_company_manifest_tx(
                conn,
                company_id=company_id,
                manifest=manifest,
                manifest_id=target["id"],
                actor=actor,
            )
            now = datetime.now(tz=UTC)
            if active_id and active_id != target["id"]:
                await conn.execute(
                    t.company_manifest_versions.update()
                    .where(t.company_manifest_versions.c.id == active_id)
                    .values(status="ROLLED_BACK")
                )
            await conn.execute(
                t.company_manifest_versions.update()
                .where(t.company_manifest_versions.c.id == target["id"])
                .values(status="APPLIED", error=None)
            )
            await conn.execute(
                t.companies.update()
                .where(t.companies.c.id == company_id)
                .values(
                    slug=manifest.slug,
                    name=manifest.name,
                    description=manifest.description,
                    active_manifest_version_id=target["id"],
                    updated_at=now,
                )
            )
            result = await self._company_manifest_read_model(company_id, conn=conn)
            result["rollback"] = {
                "manifest_version": manifest_version,
                "actor": actor,
                "reason": reason,
                "at": now,
            }
            return result

    async def update_project_config(
        self,
        project_id: UUID,
        *,
        config: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Replace project configuration and return the refreshed project row."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.projects.update()
                .where(t.projects.c.id == project_id)
                .values(
                    config=config,
                    revision=t.projects.c.revision + 1,
                    updated_at=datetime.now(tz=UTC),
                )
            )
            if result.rowcount == 0:
                return None
            refreshed = (
                await conn.execute(t.projects.select().where(t.projects.c.id == project_id))
            ).mappings().first()
            if refreshed is not None:
                await self._enqueue_project_projections_tx(conn, dict(refreshed))
        return await self.get_project(project_id)

    async def delete_project(self, project_id: UUID) -> bool:
        """Delete a project and project-owned records.

        Most project-owned tables have ``ON DELETE CASCADE`` constraints. A few
        older tables keep project IDs without foreign keys, so clear them here
        before deleting the project row.
        """
        async with self.engine.begin() as conn:
            flow_instance_ids = sa.select(t.flow_instances.c.id).where(
                t.flow_instances.c.project_id == project_id
            )
            await conn.execute(
                t.flow_node_executions.delete().where(
                    t.flow_node_executions.c.instance_id.in_(flow_instance_ids)
                )
            )
            for table in (
                t.review_comments,
                t.dead_letters,
                t.project_context_chunks,
                t.project_context_tags,
                t.project_context_relations,
                t.agent_checkpoints,
            ):
                await conn.execute(table.delete().where(table.c.project_id == project_id))

            result = await conn.execute(t.projects.delete().where(t.projects.c.id == project_id))
            return bool(result.rowcount)

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

    async def list_project_states(self) -> list[str]:
        """Return every persisted project state for aggregate metrics.

        This intentionally selects only the bounded state column rather than
        applying the page limit used by the project-list API. Observability
        startup reconciliation must not silently undercount a large instance.
        """

        query = t.projects.select().with_only_columns(t.projects.c.state)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).all()
        return [str(row[0]) for row in rows]

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
        expected_state: str | None = None,
        transition_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        """Atomically update project state and append a history row.

        Parameters
        ----------
        expected_state : str | None
            If provided, the transition is only applied when the current DB row
            state matches *expected_state* (CAS guard).  When the guard fails
            ``None`` is returned — callers should treat this as a stale-state
            rejection and re-read before retrying.

        Returns the updated project row, or None if the project was not found
        (or the expected_state guard failed).
        """
        transition_id = transition_id or uuid4()
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            # Read current state
            current = (
                (
                    await conn.execute(
                        t.projects.select().where(t.projects.c.id == project_id).with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if not current:
                return None

            from_state = current["state"]

            # CAS guard — reject if the row state has moved since the caller
            # last read it.
            if expected_state is not None and from_state != expected_state:
                return None

            # Update project
            update_values: dict[str, Any] = {
                "state": new_state,
                "revision": t.projects.c.revision + 1,
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
                t.projects.update().where(t.projects.c.id == project_id).values(**update_values)
            )

            # A terminal project cannot continue waiting for a human decision.
            # Close any gate that was left pending by a timeout or another
            # terminal failure in the same transaction as the state change.
            # This keeps operator backlog counts and project workspaces from
            # advertising approvals that can no longer be acted on.
            if new_state in {"FAILED", "COMPLETED", "ARCHIVED"}:
                await conn.execute(
                    t.approval_gates.update()
                    .where(t.approval_gates.c.project_id == project_id)
                    .where(t.approval_gates.c.status == "PENDING")
                    .values(
                        status="CANCELLED",
                        decided_by=triggered_by,
                        justification=(
                            f"Automatically cancelled because the project entered {new_state}."
                        ),
                        decided_at=now,
                    )
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
            # The router notification intent is part of the same transaction
            # as canonical state/history.  Publication remains a separate,
            # retryable operation; this row is deliberately scoped to project
            # transitions rather than introducing a global event platform.
            await conn.execute(
                t.project_transition_outbox.insert().values(
                    id=transition_id,
                    project_id=project_id,
                    from_state=from_state,
                    to_state=new_state,
                    event=event,
                    triggered_by=triggered_by,
                    payload=payload,
                    created_at=now,
                )
            )
            refreshed = (
                await conn.execute(t.projects.select().where(t.projects.c.id == project_id))
            ).mappings().first()
            if refreshed is not None:
                await self._enqueue_project_projections_tx(conn, dict(refreshed))

        return await self.get_project(project_id)

    async def list_project_transition_outbox(
        self,
        *,
        status: str = "PENDING",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return due project-transition notifications in creation order.

        Only project-transition notifications use this queue.  PM and
        identity synchronization retain their existing outbox implementations
        and delivery contracts.
        """
        if status == "PENDING":
            await self.recover_stale_project_transition_outbox()
        now = datetime.now(tz=UTC)
        query = (
            t.project_transition_outbox.select()
            .where(t.project_transition_outbox.c.status == status)
            .where(t.project_transition_outbox.c.next_attempt_at <= now)
            .order_by(t.project_transition_outbox.c.created_at)
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def recover_stale_project_transition_outbox(
        self,
        *,
        lease_seconds: int = 300,
    ) -> int:
        """Return abandoned outbox claims to the pending queue."""
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=max(30, lease_seconds))
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.project_transition_outbox.update()
                .where(t.project_transition_outbox.c.status == "PROCESSING")
                .where(t.project_transition_outbox.c.claimed_at.is_not(None))
                .where(t.project_transition_outbox.c.claimed_at <= cutoff)
                .values(
                    status="PENDING",
                    claimed_at=None,
                    next_attempt_at=now,
                )
            )
        return int(result.rowcount or 0)

    async def claim_project_transition_outbox(
        self,
        outbox_id: UUID,
    ) -> dict[str, Any] | None:
        """Claim one due project-transition notification."""
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    t.project_transition_outbox.select()
                    .where(t.project_transition_outbox.c.id == outbox_id)
                    .where(t.project_transition_outbox.c.status == "PENDING")
                    .where(t.project_transition_outbox.c.next_attempt_at <= now)
                    .with_for_update()
                )
            ).mappings().first()
            if row is None:
                return None
            attempts = int(row.get("attempts") or 0) + 1
            await conn.execute(
                t.project_transition_outbox.update()
                .where(t.project_transition_outbox.c.id == outbox_id)
                .where(t.project_transition_outbox.c.status == "PENDING")
                .values(
                    status="PROCESSING",
                    attempts=attempts,
                    claimed_at=now,
                )
            )
            claimed = dict(row)
            claimed.update(
                status="PROCESSING",
                attempts=attempts,
                claimed_at=now,
            )
            return claimed

    async def mark_project_transition_outbox(
        self,
        outbox_id: UUID,
        *,
        status: str,
        error: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> dict[str, Any] | None:
        """Mark a project-transition notification published or retryable."""
        if status not in {"PENDING", "PUBLISHED"}:
            raise ValueError("invalid project transition outbox status")
        now = datetime.now(tz=UTC)
        values: dict[str, Any] = {
            "status": status,
            "claimed_at": None,
            "last_error": error[:1000] if error else None,
        }
        if status == "PUBLISHED":
            values.update(
                published_at=now,
                next_attempt_at=now,
                last_error=None,
            )
            where_status = t.project_transition_outbox.c.status != "PUBLISHED"
        else:
            # The caller may have published successfully and lost the
            # bookkeeping response.  Never move an already-published row back
            # to pending during a stale retry.
            async with self.engine.connect() as conn:
                row = (
                    await conn.execute(
                        t.project_transition_outbox.select().where(
                            t.project_transition_outbox.c.id == outbox_id
                        )
                    )
                ).mappings().first()
            attempts = int(row.get("attempts") or 0) if row is not None else 0
            delay = (
                max(0, retry_after_seconds)
                if retry_after_seconds is not None
                else min(300, max(1, 2 ** min(attempts, 8)))
            )
            values["next_attempt_at"] = now + timedelta(seconds=delay)
            where_status = t.project_transition_outbox.c.status != "PUBLISHED"

        async with self.engine.begin() as conn:
            await conn.execute(
                t.project_transition_outbox.update()
                .where(t.project_transition_outbox.c.id == outbox_id)
                .where(where_status)
                .values(**values)
            )
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.project_transition_outbox.select().where(
                        t.project_transition_outbox.c.id == outbox_id
                    )
                )
            ).mappings().first()
        return dict(row) if row else None

    async def mark_project_transition_published(self, transition_id: UUID) -> None:
        """Mark an immediately delivered transition notification complete."""
        await self.mark_project_transition_outbox(transition_id, status="PUBLISHED")

    async def get_project_history(
        self,
        project_id: UUID,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch transition history for a project, newest first."""
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        t.project_state_history.select()
                        .where(t.project_state_history.c.project_id == project_id)
                        .order_by(t.project_state_history.c.transitioned_at.desc())
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
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
        now = datetime.now(tz=UTC)
        values = {
            "id": did,
            "project_id": project_id,
            "lineage_id": did,
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

    async def create_document_revision(
        self,
        document_id: UUID,
        *,
        created_by: str,
        blob_bucket: str | None = None,
        blob_key: str | None = None,
        blob_sha256: str | None = None,
        revision_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create the next immutable document version.

        Document rows are append-only versions.  The previous latest version
        is marked ``SUPERSEDED`` in the same transaction as the new draft so a
        reader can never observe two current versions for one project/type.
        """
        rid = revision_id or uuid4()
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            source = (
                (
                    await conn.execute(
                        t.documents.select()
                        .where(t.documents.c.id == document_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if source is None:
                raise ValueError(f"Document {document_id} not found")

            lineage_id = source.get("lineage_id") or source["id"]
            latest = (
                (
                    await conn.execute(
                        t.documents.select()
                        .where(t.documents.c.lineage_id == lineage_id)
                        .order_by(t.documents.c.version.desc())
                        .limit(1)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if latest is None:
                raise ValueError(f"No current version for document {document_id}")

            version = int(latest["version"]) + 1
            await conn.execute(
                t.documents.update()
                .where(t.documents.c.id == latest["id"])
                .values(status="SUPERSEDED", updated_at=now)
            )
            values = {
                "id": rid,
                "project_id": source["project_id"],
                "lineage_id": lineage_id,
                "doc_type": source["doc_type"],
                "version": version,
                "status": "DRAFT",
                "blob_bucket": blob_bucket,
                "blob_key": blob_key,
                "blob_sha256": blob_sha256,
                "created_by": created_by,
                "created_at": now,
                "updated_at": now,
            }
            await conn.execute(t.documents.insert().values(**values))
        return values

    async def get_document(self, document_id: UUID) -> dict[str, Any] | None:
        """Fetch a document by ID."""
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(t.documents.select().where(t.documents.c.id == document_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def get_latest_document(self, project_id: UUID, doc_type: str) -> dict[str, Any] | None:
        """Fetch the latest version of a document by project + type."""
        async with self.engine.connect() as conn:
            row = (
                (
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
                )
                .mappings()
                .first()
            )
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
                .values(status=status, updated_at=datetime.now(tz=UTC))
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
    # Project Context Items
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_context_item(
        self,
        *,
        project_id: UUID,
        item_type: str,
        name: str,
        description: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        blob_bucket: str | None = None,
        blob_key: str | None = None,
        blob_sha256: str | None = None,
        url: str | None = None,
        content_text: str | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
        created_by: str,
        item_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a new project context item (file attachment, URL, text, etc.)."""
        iid = item_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": iid,
            "project_id": project_id,
            "item_type": item_type,
            "name": name,
            "description": description,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "blob_bucket": blob_bucket,
            "blob_key": blob_key,
            "blob_sha256": blob_sha256,
            "url": url,
            "content_text": content_text,
            "metadata": metadata,
            "tags": tags or [],
            "created_by": created_by,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.project_context_items.insert().values(**values))
        return values

    async def get_context_item(self, item_id: UUID) -> dict[str, Any] | None:
        """Fetch a context item by ID."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        t.project_context_items.select().where(
                            t.project_context_items.c.id == item_id
                        )
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def list_context_items(
        self,
        project_id: UUID,
        *,
        item_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List context items for a project with optional filters."""
        q = t.project_context_items.select().where(
            t.project_context_items.c.project_id == project_id
        )
        if item_type:
            q = q.where(t.project_context_items.c.item_type == item_type)
        if tags:
            q = q.where(t.project_context_items.c.tags.overlap(tags))
        q = q.order_by(t.project_context_items.c.created_at.desc())
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def list_project_context(
        self,
        project_id: UUID,
        *,
        item_type: str | None = None,
        tags: list[str] | None = None,
        include_document_revisions: bool = False,
    ) -> list[dict[str, Any]]:
        """List user context plus generated document read models.

        Formal documents use the ``documents`` table because their lifecycle
        is versioned and approval-controlled.  They are still project context
        from an operator/agent perspective, so this method joins both sources
        at the storage boundary.  By default only the latest row in each
        document lineage is shown; callers can request historical revisions
        when they need the complete audit trail.
        """
        items = await self.list_context_items(project_id, item_type=item_type, tags=tags)

        # A non-document filter cannot match formal document projections.
        if item_type and item_type.upper() != "DOCUMENT":
            return items

        documents = await self.list_documents(project_id)
        if not include_document_revisions:
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
            documents = list(latest_by_lineage.values())

        document_items = [document_to_context_item(document) for document in documents]
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

    async def delete_context_item(self, item_id: UUID) -> bool:
        """Delete a context item. Returns True if deleted, False if not found."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.project_context_items.delete().where(t.project_context_items.c.id == item_id)
            )
            return result.rowcount > 0

    # ═══════════════════════════════════════════════════════════════════════════
    # Project Context Chunks (RAG)
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_context_chunk(
        self,
        *,
        context_item_id: UUID,
        project_id: UUID,
        chunk_index: int,
        content_text: str,
        content_vector: list[float] | None = None,
        source_location: str | None = None,
        metadata: dict | None = None,
        token_count: int | None = None,
        chunk_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a chunk from a context item for RAG."""
        cid = chunk_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": cid,
            "context_item_id": context_item_id,
            "project_id": project_id,
            "chunk_index": chunk_index,
            "content_text": content_text,
            "source_location": source_location,
            "metadata": metadata,
            "token_count": token_count,
            "created_at": now,
        }
        if content_vector is not None:
            values["content_vector"] = content_vector
        async with self.engine.begin() as conn:
            await conn.execute(t.project_context_chunks.insert().values(**values))
        return values

    async def list_context_chunks(
        self,
        project_id: UUID,
        *,
        context_item_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        """List chunks for a project, optionally filtered by context item."""
        q = t.project_context_chunks.select().where(
            t.project_context_chunks.c.project_id == project_id
        )
        if context_item_id:
            q = q.where(t.project_context_chunks.c.context_item_id == context_item_id)
        q = q.order_by(t.project_context_chunks.c.chunk_index)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def search_context_chunks_keyword(
        self,
        project_id: UUID,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Keyword search over context chunks."""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        q = (
            t.project_context_chunks.select()
            .where(t.project_context_chunks.c.project_id == project_id)
            .where(t.project_context_chunks.c.content_text.ilike(f"%{escaped}%"))
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def search_context_chunks_semantic(
        self,
        project_id: UUID,
        query_vector: list[float],
        *,
        limit: int = 10,
        filters: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search over context chunks using cosine similarity.

        Requires pgvector extension and content_vector to be populated.
        """
        q = (
            t.project_context_chunks.select()
            .where(t.project_context_chunks.c.project_id == project_id)
            .where(t.project_context_chunks.c.content_vector.isnot(None))
        )

        if filters:
            if tag_ids := filters.get("tag_ids"):
                q = q.where(t.project_context_chunks.c.metadata["tags"].has_any(tag_ids))
            if source_types := filters.get("source_types"):
                q = q.where(t.project_context_chunks.c.metadata["source_type"].in_(source_types))

        q = q.order_by(t.project_context_chunks.c.content_vector.op("<=>")(query_vector)).limit(
            limit
        )

        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def search_context_hybrid(
        self,
        project_id: UUID,
        query: str,
        query_vector: list[float] | None = None,
        *,
        limit: int = 10,
        filters: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search combining keyword, metadata, and semantic search.

        Strategy:
        1. Always filter by project_id first
        2. Run keyword search to get baseline results
        3. If query_vector provided, compute semantic similarity
        4. Combine and rank results by hybrid scoring
        5. Apply metadata filters
        """
        keyword_results = await self.search_context_chunks_keyword(
            project_id=project_id,
            query=query,
            limit=limit * 3,
        )

        if not query_vector:
            return keyword_results[:limit]

        semantic_results = await self.search_context_chunks_semantic(
            project_id=project_id,
            query_vector=query_vector,
            limit=limit * 3,
            filters=filters,
        )

        scored: dict[str, dict[str, Any]] = {}

        for rank, chunk in enumerate(keyword_results):
            chunk_id = str(chunk["id"])
            scored[chunk_id] = {
                **chunk,
                "keyword_rank": rank + 1,
                "semantic_rank": None,
                "hybrid_score": 1.0 - (rank / (limit * 3)),
                "match_types": ["keyword"],
            }

        for rank, chunk in enumerate(semantic_results):
            chunk_id = str(chunk["id"])
            if chunk_id in scored:
                scored[chunk_id]["semantic_rank"] = rank + 1
                scored[chunk_id]["hybrid_score"] = (
                    scored[chunk_id]["hybrid_score"] + (1.0 - (rank / (limit * 3)))
                ) / 2
                scored[chunk_id]["match_types"].append("semantic")
            else:
                scored[chunk_id] = {
                    **chunk,
                    "keyword_rank": None,
                    "semantic_rank": rank + 1,
                    "hybrid_score": 1.0 - (rank / (limit * 3)),
                    "match_types": ["semantic"],
                }

        if filters:
            for chunk_id in list(scored.keys()):
                chunk = scored[chunk_id]
                metadata = chunk.get("metadata", {}) or {}
                if date_range := filters.get("date_range"):
                    chunk_date = metadata.get("created_at") or chunk.get("created_at")
                    if chunk_date:
                        if date_range.get("from") and chunk_date < date_range["from"]:
                            del scored[chunk_id]
                            continue
                        if date_range.get("to") and chunk_date > date_range["to"]:
                            del scored[chunk_id]
                            continue

        sorted_results = sorted(
            scored.values(),
            key=lambda x: x["hybrid_score"],
            reverse=True,
        )

        return sorted_results[:limit]

    async def delete_context_chunks(self, context_item_id: UUID) -> None:
        """Delete all chunks for a context item."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.project_context_chunks.delete().where(
                    t.project_context_chunks.c.context_item_id == context_item_id
                )
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Project Context Tags
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_context_tag(
        self,
        *,
        project_id: UUID,
        name: str,
        color: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a tag for project context items."""
        tid = uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": tid,
            "project_id": project_id,
            "name": name,
            "color": color,
            "description": description,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.project_context_tags.insert().values(**values))
        return values

    async def list_context_tags(self, project_id: UUID) -> list[dict[str, Any]]:
        """List all tags for a project."""
        q = t.project_context_tags.select().where(t.project_context_tags.c.project_id == project_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def delete_context_tag(self, tag_id: UUID) -> None:
        """Delete a context tag."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.project_context_tags.delete().where(t.project_context_tags.c.id == tag_id)
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Project Context Relations
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_context_relation(
        self,
        *,
        project_id: UUID,
        source_item_id: UUID,
        target_item_id: UUID,
        relation_type: str,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Create a relation between two context items."""
        rid = uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": rid,
            "project_id": project_id,
            "source_item_id": source_item_id,
            "target_item_id": target_item_id,
            "relation_type": relation_type,
            "metadata": metadata,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.project_context_relations.insert().values(**values))
        return values

    async def list_context_relations(
        self,
        project_id: UUID,
        *,
        source_item_id: UUID | None = None,
        target_item_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        """List relations for a project."""
        q = t.project_context_relations.select().where(
            t.project_context_relations.c.project_id == project_id
        )
        if source_item_id:
            q = q.where(t.project_context_relations.c.source_item_id == source_item_id)
        if target_item_id:
            q = q.where(t.project_context_relations.c.target_item_id == target_item_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def delete_context_relation(self, relation_id: UUID) -> None:
        """Delete a context relation."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.project_context_relations.delete().where(
                    t.project_context_relations.c.id == relation_id
                )
            )

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
        now = datetime.now(tz=UTC)
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
                (
                    await conn.execute(
                        t.review_sessions.select().where(t.review_sessions.c.id == session_id)
                    )
                )
                .mappings()
                .first()
            )
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
        now = datetime.now(tz=UTC)
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

    async def get_review_comments(self, session_id: UUID) -> list[dict[str, Any]]:
        """Fetch all comments for a review session."""
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        t.review_comments.select()
                        .where(t.review_comments.c.session_id == session_id)
                        .order_by(t.review_comments.c.submitted_at)
                    )
                )
                .mappings()
                .all()
            )
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
        flow_node_execution_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a pending approval gate, optionally bound to one flow node."""
        gid = gate_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": gid,
            "project_id": project_id,
            "gate_type": gate_type,
            "status": "PENDING",
            "flow_node_execution_id": flow_node_execution_id,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.approval_gates.insert().values(**values))
        return values

    async def create_flow_approval_gate(
        self,
        *,
        project_id: UUID,
        instance_id: UUID,
        node_id: str,
        node_label: str,
        gate_type: str,
        input_json: dict | None = None,
        gate_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create one flow approval execution and its bound gate atomically."""
        now = datetime.now(tz=UTC)
        gid = gate_id or uuid4()
        execution_values = {
            "instance_id": instance_id,
            "node_id": node_id,
            "node_type": "approval",
            "node_label": node_label,
            "status": "RUNNING",
            "input_json": input_json,
            "started_at": now,
        }
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.flow_node_executions.insert()
                .values(**execution_values)
                .returning(t.flow_node_executions.c.id)
            )
            row = result.first()
            if row is None:
                raise RuntimeError("flow approval execution insert returned no identity")
            execution_id = int(row[0])
            gate_values = {
                "id": gid,
                "project_id": project_id,
                "gate_type": gate_type,
                "status": "PENDING",
                "flow_node_execution_id": execution_id,
                "created_at": now,
            }
            await conn.execute(t.approval_gates.insert().values(**gate_values))
        return {
            "execution": {**execution_values, "id": execution_id},
            "gate": gate_values,
        }

    async def activate_flow_nodes(
        self,
        instance_id: UUID,
        *,
        expected_statuses: set[str],
        expected_active_node_ids: list[str] | None = None,
        expected_instance_snapshot: dict[str, Any] | None = None,
        instance_updates: dict[str, Any],
        activations: list[dict[str, Any]],
        supersede_existing: bool = False,
        supersede_reason: str = "Superseded by explicit flow retry",
    ) -> dict[str, Any]:
        """Atomically activate flow nodes and persist their execution records.

        This is used when an activation includes approval nodes: the flow
        instance must never advertise an active approval without its durable
        execution and bound operator gate. All sibling nodes in that same
        activation are written in the same transaction.
        """
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            execution_ids: list[int] = []
            if supersede_existing:
                # Retry lock order matches approval resolution:
                # pending gate -> execution -> flow instance. This makes the
                # terminal-state check and replacement activation one atomic
                # winner among overlapping retries.
                execution_id_rows = await conn.execute(
                    t.flow_node_executions.select()
                    .with_only_columns(t.flow_node_executions.c.id)
                    .where(t.flow_node_executions.c.instance_id == instance_id)
                    .order_by(t.flow_node_executions.c.id.asc())
                )
                execution_ids = [int(row[0]) for row in execution_id_rows.all()]
                if execution_ids:
                    await conn.execute(
                        t.approval_gates.select()
                        .where(t.approval_gates.c.flow_node_execution_id.in_(execution_ids))
                        .where(t.approval_gates.c.status == "PENDING")
                        .order_by(t.approval_gates.c.id.asc())
                        .with_for_update()
                    )
                    await conn.execute(
                        t.flow_node_executions.select()
                        .where(t.flow_node_executions.c.id.in_(execution_ids))
                        .order_by(t.flow_node_executions.c.id.asc())
                        .with_for_update()
                    )
            instance_row = (
                await conn.execute(
                    t.flow_instances.select()
                    .where(t.flow_instances.c.id == instance_id)
                    .with_for_update()
                )
            ).mappings().first()
            if instance_row is None:
                return {"outcome": "not_found"}
            instance = dict(instance_row)
            if str(instance.get("status", "")).upper() not in {
                str(status).upper() for status in expected_statuses
            }:
                return {"outcome": "conflict", "instance": instance}
            if not _flow_instance_matches_snapshot(instance, expected_instance_snapshot):
                return {"outcome": "stale_instance", "instance": instance}
            if expected_active_node_ids is not None and set(
                str(value) for value in (instance.get("active_node_ids") or [])
            ) != set(str(value) for value in expected_active_node_ids):
                return {"outcome": "conflict", "instance": instance}

            if supersede_existing and execution_ids:
                await conn.execute(
                    t.approval_gates.update()
                    .where(t.approval_gates.c.flow_node_execution_id.in_(execution_ids))
                    .where(t.approval_gates.c.status == "PENDING")
                    .values(
                        status="CANCELLED",
                        decided_by="system:flow_retry",
                        justification=supersede_reason,
                        decided_at=now,
                    )
                )
                await conn.execute(
                    t.flow_node_executions.update()
                    .where(t.flow_node_executions.c.id.in_(execution_ids))
                    .where(t.flow_node_executions.c.status != "SUPERSEDED")
                    .values(
                        status="SUPERSEDED",
                        error=sa.func.coalesce(
                            t.flow_node_executions.c.error, supersede_reason
                        ),
                        completed_at=sa.func.coalesce(
                            t.flow_node_executions.c.completed_at, now
                        ),
                    )
                )

            updates = {**instance_updates, "updated_at": now}
            changed = await conn.execute(
                t.flow_instances.update()
                .where(t.flow_instances.c.id == instance_id)
                .values(**updates)
            )
            if getattr(changed, "rowcount", 1) != 1:
                return {"outcome": "conflict", "instance": instance}

            created_executions: list[dict[str, Any]] = []
            for item in activations:
                execution_values = {
                    "instance_id": instance_id,
                    "node_id": item["node_id"],
                    "node_type": item["node_type"],
                    "node_label": item["node_label"],
                    "status": item.get("status", "RUNNING"),
                    "input_json": item.get("input_json"),
                    "output_json": item.get("output_json"),
                    "error": item.get("error"),
                    "started_at": now,
                    "completed_at": now
                    if item.get("status") in {"COMPLETED", "FAILED"}
                    else None,
                }
                inserted = await conn.execute(
                    t.flow_node_executions.insert()
                    .values(**execution_values)
                    .returning(t.flow_node_executions.c.id)
                )
                row = inserted.first()
                if row is None:
                    raise RuntimeError("flow node activation insert returned no identity")
                execution_id = int(row[0])
                created_execution = {**execution_values, "id": execution_id}
                created_executions.append(created_execution)
                if item.get("gate_type") is not None:
                    await conn.execute(
                        t.approval_gates.insert().values(
                            id=item.get("gate_id") or uuid4(),
                            project_id=instance["project_id"],
                            gate_type=item["gate_type"],
                            status="PENDING",
                            flow_node_execution_id=execution_id,
                            created_at=now,
                        )
                    )

            updated_row = (
                await conn.execute(
                    t.flow_instances.select().where(t.flow_instances.c.id == instance_id)
                )
            ).mappings().first()
            return {
                "outcome": "applied",
                "instance": dict(updated_row) if updated_row else None,
                "executions": created_executions,
            }

    async def bind_legacy_flow_approval_gate(
        self,
        gate_id: UUID,
        *,
        project_id: UUID,
    ) -> dict[str, Any]:
        """Safely recover a pre-binding pending flow approval gate.

        A gate is bound only when its project and exact gate type identify one
        active approval execution and one unbound pending gate. A selected
        gate with no matching active flow node is returned as ``generic`` so
        the existing project approval path remains available. Ambiguous
        legacy state is left untouched.
        """
        from mas_core.workflow import parse_flow_definition

        async with self.engine.begin() as conn:
            selected_row = (
                await conn.execute(
                    t.approval_gates.select()
                    .where(t.approval_gates.c.id == gate_id)
                    .where(t.approval_gates.c.project_id == project_id)
                    .with_for_update()
                )
            ).mappings().first()
            if selected_row is None:
                return {"outcome": "not_found"}
            selected = dict(selected_row)
            if selected.get("flow_node_execution_id") is not None:
                return {"outcome": "already_bound", "gate": selected}
            if str(selected.get("status", "")).upper() != "PENDING":
                return {"outcome": "conflict", "gate": selected}

            gate_type = str(selected.get("gate_type") or "")
            gate_rows = (
                await conn.execute(
                    t.approval_gates.select()
                    .where(t.approval_gates.c.project_id == project_id)
                    .where(t.approval_gates.c.status == "PENDING")
                    .where(t.approval_gates.c.flow_node_execution_id.is_(None))
                    .where(t.approval_gates.c.gate_type == gate_type)
                )
            ).mappings().all()

            instances = (
                await conn.execute(
                    t.flow_instances.select()
                    .where(t.flow_instances.c.project_id == project_id)
                    .where(t.flow_instances.c.status.in_(["RUNNING", "WAITING_APPROVAL"]))
                )
            ).mappings().all()
            candidate_executions: list[dict[str, Any]] = []
            for instance_row in instances:
                instance = dict(instance_row)
                active_ids = [str(value) for value in (instance.get("active_node_ids") or [])]
                if not active_ids:
                    continue
                execution_rows = (
                    await conn.execute(
                        t.flow_node_executions.select()
                        .where(t.flow_node_executions.c.instance_id == instance["id"])
                        .where(t.flow_node_executions.c.node_type == "approval")
                        .where(t.flow_node_executions.c.status == "RUNNING")
                        .where(t.flow_node_executions.c.node_id.in_(active_ids))
                    )
                ).mappings().all()
                if not execution_rows:
                    continue
                flow_row = (
                    await conn.execute(
                        t.flows.select()
                        .where(t.flows.c.id == instance["flow_id"])
                    )
                ).mappings().first()
                if flow_row is None:
                    continue
                flow = dict(flow_row)
                if int(flow.get("version") or 0) != int(instance.get("flow_version") or 0):
                    # Current code cannot prove the original gate type for an
                    # instance whose referenced flow definition has changed.
                    # Preserve it as a possible match so a same-type gate is
                    # never misclassified as generic.
                    candidate_executions.extend(
                        {**dict(row), "_legacy_flow_definition_unknown": True}
                        for row in execution_rows
                    )
                    continue
                try:
                    definition = parse_flow_definition(flow["definition_json"])
                except (KeyError, TypeError, ValueError):
                    candidate_executions.extend(
                        {**dict(row), "_legacy_flow_definition_unknown": True}
                        for row in execution_rows
                    )
                    continue
                for execution_row in execution_rows:
                    execution = dict(execution_row)
                    node = definition.get_node(str(execution.get("node_id") or ""))
                    if node is None:
                        continue
                    expected_gate_type = (
                        node.config.get("approver_role")
                        or node.config.get("approver_user")
                        or node.label
                    )
                    if str(expected_gate_type) == gate_type:
                        candidate_executions.append(execution)

            if any(
                candidate.get("_legacy_flow_definition_unknown")
                for candidate in candidate_executions
            ):
                return {"outcome": "unresolved"}
            if not candidate_executions:
                return {"outcome": "generic", "gate": selected}
            if (
                len(candidate_executions) != 1
                or len(gate_rows) != 1
                or dict(gate_rows[0]).get("id") != gate_id
            ):
                return {"outcome": "ambiguous"}

            execution = candidate_executions[0]
            execution_id = int(execution["id"])
            # Follow the decision resolver's lock order: selected gate first,
            # then its candidate execution, then the owning flow instance.
            # The broad candidate scans above are reads only, so concurrent
            # same-type gate decisions cannot deadlock by locking each other's
            # gates or by taking instance -> execution in reverse order.
            locked_execution_row = (
                await conn.execute(
                    t.flow_node_executions.select()
                    .where(t.flow_node_executions.c.id == execution_id)
                    .with_for_update()
                )
            ).mappings().first()
            if locked_execution_row is None:
                return {"outcome": "conflict"}
            execution = dict(locked_execution_row)
            instance_row = (
                await conn.execute(
                    t.flow_instances.select()
                    .where(t.flow_instances.c.id == execution["instance_id"])
                    .with_for_update()
                )
            ).mappings().first()
            if instance_row is None:
                return {"outcome": "conflict"}
            instance = dict(instance_row)
            if (
                instance.get("project_id") != project_id
                or str(instance.get("status", "")).upper()
                not in {"RUNNING", "WAITING_APPROVAL"}
                or execution.get("node_type") != "approval"
                or str(execution.get("status", "")).upper() != "RUNNING"
                or str(execution.get("node_id") or "")
                not in {str(value) for value in (instance.get("active_node_ids") or [])}
            ):
                return {"outcome": "conflict"}
            flow_row = (
                await conn.execute(
                    t.flows.select()
                    .where(t.flows.c.id == instance["flow_id"])
                    .with_for_update()
                )
            ).mappings().first()
            if flow_row is None:
                return {"outcome": "unresolved"}
            flow = dict(flow_row)
            if int(flow.get("version") or 0) != int(instance.get("flow_version") or 0):
                return {"outcome": "unresolved"}
            try:
                definition = parse_flow_definition(flow["definition_json"])
            except (KeyError, TypeError, ValueError):
                return {"outcome": "unresolved"}
            node = definition.get_node(str(execution.get("node_id") or ""))
            expected_gate_type = (
                node.config.get("approver_role")
                or node.config.get("approver_user")
                or node.label
                if node is not None
                else None
            )
            if node is None or str(expected_gate_type) != gate_type:
                return {"outcome": "conflict"}

            # Recheck uniqueness after acquiring the candidate locks. A new
            # gate inserted after the initial read is treated as ambiguity if
            # it committed before this final binding decision.
            current_gate_rows = (
                await conn.execute(
                    t.approval_gates.select()
                    .where(t.approval_gates.c.project_id == project_id)
                    .where(t.approval_gates.c.status == "PENDING")
                    .where(t.approval_gates.c.flow_node_execution_id.is_(None))
                    .where(t.approval_gates.c.gate_type == gate_type)
                )
            ).mappings().all()
            if len(current_gate_rows) != 1 or dict(current_gate_rows[0]).get("id") != gate_id:
                return {"outcome": "ambiguous"}

            existing_binding = (
                await conn.execute(
                    t.approval_gates.select().where(
                        t.approval_gates.c.flow_node_execution_id == execution_id
                    )
                )
            ).mappings().first()
            if existing_binding is not None:
                return {"outcome": "conflict"}

            update = await conn.execute(
                t.approval_gates.update()
                .where(t.approval_gates.c.id == gate_id)
                .where(t.approval_gates.c.project_id == project_id)
                .where(t.approval_gates.c.status == "PENDING")
                .where(t.approval_gates.c.flow_node_execution_id.is_(None))
                .values(flow_node_execution_id=execution_id)
                .returning(t.approval_gates)
            )
            bound_row = update.mappings().first()
            if bound_row is None:
                return {"outcome": "conflict"}
            return {
                "outcome": "bound",
                "gate": dict(bound_row),
                "execution": execution,
            }

    async def resolve_flow_approval_gate(
        self,
        gate_id: UUID,
        *,
        project_id: UUID,
        instance_id: UUID,
        execution_id: int,
        node_id: str,
        status: str,
        decided_by: str,
        justification: str | None,
        human_input: dict | None,
        execution_output: dict,
        instance_updates: dict[str, Any],
        activated_executions: list[dict[str, Any]],
        expected_instance_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically decide one bound gate and advance its active flow node.

        Replaying the identical decision returns ``already_applied`` after
        confirming the execution contains this gate's resolution marker. A
        different decision, stale execution, or mismatched flow is a conflict.
        """
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            gate_row = (
                await conn.execute(
                    t.approval_gates.select()
                    .where(t.approval_gates.c.id == gate_id)
                    .with_for_update()
                )
            ).mappings().first()
            if gate_row is None:
                return {"outcome": "not_found"}
            gate = dict(gate_row)
            if (
                gate.get("project_id") != project_id
                or gate.get("flow_node_execution_id") != execution_id
            ):
                return {"outcome": "conflict"}

            execution_row = (
                await conn.execute(
                    t.flow_node_executions.select()
                    .where(t.flow_node_executions.c.id == execution_id)
                    .with_for_update()
                )
            ).mappings().first()
            if execution_row is None:
                return {"outcome": "conflict"}
            execution = dict(execution_row)

            instance_row = (
                await conn.execute(
                    t.flow_instances.select()
                    .where(t.flow_instances.c.id == instance_id)
                    .with_for_update()
                )
            ).mappings().first()
            if instance_row is None:
                return {"outcome": "conflict"}
            instance = dict(instance_row)

            if (
                execution.get("instance_id") != instance_id
                or execution.get("node_id") != node_id
                or execution.get("node_type") != "approval"
                or instance.get("project_id") != project_id
            ):
                return {"outcome": "conflict"}

            if gate.get("status") != "PENDING":
                replay_matches = (
                    gate.get("status") == status
                    and gate.get("decided_by") == decided_by
                    and gate.get("justification") == justification
                    and gate.get("human_input") == human_input
                    and execution.get("status") == "COMPLETED"
                    and (execution.get("output_json") or {}).get("approval_gate_id")
                    == str(gate_id)
                )
                if replay_matches:
                    return {
                        "outcome": "already_applied",
                        "gate": gate,
                        "execution": execution,
                        "instance": instance,
                        "activated_node_ids": list(
                            (execution.get("output_json") or {}).get(
                                "activated_node_ids", []
                            )
                        ),
                    }
                return {"outcome": "conflict"}

            if not _flow_instance_matches_snapshot(instance, expected_instance_snapshot):
                return {"outcome": "stale_instance", "instance": instance}

            active_node_ids = list(instance.get("active_node_ids") or [])
            if (
                execution.get("status") != "RUNNING"
                or node_id not in active_node_ids
                or instance.get("status") not in {"RUNNING", "WAITING_APPROVAL"}
            ):
                return {"outcome": "conflict"}

            changed = await conn.execute(
                t.approval_gates.update()
                .where(t.approval_gates.c.id == gate_id)
                .where(t.approval_gates.c.status == "PENDING")
                .values(
                    status=status,
                    decided_by=decided_by,
                    justification=justification,
                    human_input=human_input,
                    decided_at=now,
                )
            )
            if getattr(changed, "rowcount", 1) != 1:
                return {"outcome": "conflict"}

            resolved_output = {
                **execution_output,
                "approval_gate_id": str(gate_id),
                "activated_node_ids": [
                    str(item["node_id"]) for item in activated_executions
                ],
            }
            await conn.execute(
                t.flow_node_executions.update()
                .where(t.flow_node_executions.c.id == execution_id)
                .where(t.flow_node_executions.c.status == "RUNNING")
                .values(
                    status="COMPLETED",
                    output_json=resolved_output,
                    completed_at=now,
                )
            )

            update_values = {**instance_updates, "updated_at": now}
            await conn.execute(
                t.flow_instances.update()
                .where(t.flow_instances.c.id == instance_id)
                .values(**update_values)
            )

            created_execution_ids: list[int] = []
            for item in activated_executions:
                execution_values = {
                    "instance_id": instance_id,
                    "node_id": item["node_id"],
                    "node_type": item["node_type"],
                    "node_label": item["node_label"],
                    "status": item.get("status", "RUNNING"),
                    "input_json": item.get("input_json"),
                    "output_json": item.get("output_json"),
                    "error": item.get("error"),
                    "started_at": now,
                    "completed_at": item.get("completed_at", now)
                    if item.get("status") in {"COMPLETED", "FAILED"}
                    else None,
                }
                result = await conn.execute(
                    t.flow_node_executions.insert()
                    .values(**execution_values)
                    .returning(t.flow_node_executions.c.id)
                )
                row = result.first()
                if row is None:
                    raise RuntimeError("flow node execution insert returned no identity")
                created_id = int(row[0])
                created_execution_ids.append(created_id)
                if item.get("gate_type") is not None:
                    await conn.execute(
                        t.approval_gates.insert().values(
                            id=item.get("gate_id") or uuid4(),
                            project_id=project_id,
                            gate_type=item["gate_type"],
                            status="PENDING",
                            flow_node_execution_id=created_id,
                            created_at=now,
                        )
                    )

            updated_instance_row = (
                await conn.execute(
                    t.flow_instances.select().where(t.flow_instances.c.id == instance_id)
                )
            ).mappings().first()
            updated_execution_row = (
                await conn.execute(
                    t.flow_node_executions.select().where(
                        t.flow_node_executions.c.id == execution_id
                    )
                )
            ).mappings().first()
            return {
                "outcome": "applied",
                "gate": {**gate, "status": status, "decided_by": decided_by, "decided_at": now},
                "execution": dict(updated_execution_row) if updated_execution_row else None,
                "instance": dict(updated_instance_row) if updated_instance_row else None,
                "activated_node_ids": [
                    str(item["node_id"]) for item in activated_executions
                ],
                "created_execution_ids": created_execution_ids,
            }

    async def decide_approval_gate(
        self,
        gate_id: UUID,
        *,
        status: str,
        decided_by: str,
        justification: str | None = None,
        human_input: dict | None = None,
    ) -> bool:
        """Record a decision only while an approval gate is still pending.

        Returns ``False`` when another transaction already decided or
        cancelled the gate. The status predicate makes terminal-state cleanup
        and human decisions safe against late-arriving requests.
        """
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.approval_gates.update()
                .where(t.approval_gates.c.id == gate_id)
                .where(t.approval_gates.c.status == "PENDING")
                .values(
                    status=status,
                    decided_by=decided_by,
                    justification=justification,
                    human_input=human_input,
                    decided_at=now,
                )
            )
            rowcount = getattr(result, "rowcount", None)
            return rowcount > 0 if isinstance(rowcount, int) else True

    async def list_approval_gates(
        self,
        *,
        project_id: UUID | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List approval gates for operator read models."""
        q = t.approval_gates.select()
        if project_id is not None:
            q = q.where(t.approval_gates.c.project_id == project_id)
        if status is not None:
            q = q.where(t.approval_gates.c.status == status)
        q = q.order_by(t.approval_gates.c.created_at.desc()).limit(limit)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def get_approval_gate_for_flow_execution(
        self,
        execution_id: int,
    ) -> dict[str, Any] | None:
        """Resolve the unique approval gate durably bound to a flow execution."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.approval_gates.select().where(
                        t.approval_gates.c.flow_node_execution_id == execution_id
                    )
                )
            ).mappings().first()
        return dict(row) if row else None

    async def list_pending_project_decisions(
        self,
        *,
        limit: int,
        cursor: tuple[datetime, UUID] | None = None,
    ) -> list[dict[str, Any]]:
        """Read one bounded, globally ordered page of actionable project gates.

        This query deliberately joins each gate to its owning project in SQL;
        it never fans out through per-project reads. The status/created/id
        index supports the pending filter and keyset traversal. Payload fields
        such as ``human_input`` and ``justification`` are excluded from this
        operator inbox projection.
        """
        if not 1 <= limit <= 101:
            raise ValueError("approval inbox query limit must be between 1 and 101")

        gates = t.approval_gates
        projects = t.projects
        query = (
            sa.select(
                gates.c.id,
                gates.c.project_id,
                gates.c.gate_type,
                gates.c.status,
                gates.c.created_at,
                projects.c.name.label("project_name"),
                projects.c.state.label("project_state"),
                projects.c.company_id,
            )
            .select_from(gates.join(projects, projects.c.id == gates.c.project_id))
            .where(gates.c.status == "PENDING")
            .where(
                projects.c.state.notin_(
                    [
                        ProjectState.COMPLETED.value,
                        ProjectState.ARCHIVED.value,
                        ProjectState.FAILED.value,
                    ]
                )
            )
        )
        if cursor is not None:
            cursor_created_at, cursor_id = cursor
            if cursor_created_at.tzinfo is None or cursor_created_at.utcoffset() is None:
                raise ValueError("approval inbox cursor timestamp must include a timezone")
            query = query.where(
                sa.tuple_(gates.c.created_at, gates.c.id)
                < sa.tuple_(cursor_created_at, cursor_id)
            )
        query = query.order_by(gates.c.created_at.desc(), gates.c.id.desc()).limit(limit)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

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
        now = datetime.now(tz=UTC)
        values = {
            "id": sid,
            "project_id": project_id,
            "sprint_number": sprint_number,
            "milestone": milestone,
            "goal": goal,
            "status": "PLANNED",
            "planned_story_points": planned_story_points,
            "estimated_hours": estimated_hours,
            "revision": 1,
            "updated_at": now,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.sprints.insert().values(**values))
            await self._enqueue_iteration_projections_tx(conn, values)
        return values

    async def create_sprint_with_pm_projections(
        self,
        *,
        project_id: UUID,
        sprint_number: int,
        milestone: str | None = None,
        goal: str | None = None,
        planned_story_points: int | None = None,
        estimated_hours: Decimal | None = None,
        sprint_id: UUID | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Create an iteration and return its same-transaction projection intents."""
        sid = sprint_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": sid,
            "project_id": project_id,
            "sprint_number": sprint_number,
            "milestone": milestone,
            "goal": goal,
            "status": "PLANNED",
            "planned_story_points": planned_story_points,
            "estimated_hours": estimated_hours,
            "revision": 1,
            "updated_at": now,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.sprints.insert().values(**values))
            queued = await self._enqueue_iteration_projections_tx(conn, values)
        return values, queued

    async def get_sprint(self, sprint_id: UUID) -> dict[str, Any] | None:
        """Fetch a sprint by ID."""
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(t.sprints.select().where(t.sprints.c.id == sprint_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def list_sprints(self, project_id: UUID) -> list[dict[str, Any]]:
        """List sprints for a project ordered by sprint number."""
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        t.sprints.select()
                        .where(t.sprints.c.project_id == project_id)
                        .order_by(t.sprints.c.sprint_number)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def update_sprint(self, sprint_id: UUID, **kwargs: Any) -> None:
        """Update sprint fields and atomically advance its canonical revision."""
        expected_revision = kwargs.pop("expected_revision", None)
        values = dict(kwargs)
        values["revision"] = t.sprints.c.revision + 1
        values["updated_at"] = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            current = (
                await conn.execute(
                    t.sprints.select().where(t.sprints.c.id == sprint_id).with_for_update()
                )
            ).mappings().first()
            if current is None or (
                expected_revision is not None
                and int(current.get("revision") or 1) != int(expected_revision)
            ):
                raise ValueError("sprint not found or revision conflict")
            await conn.execute(t.sprints.update().where(t.sprints.c.id == sprint_id).values(**values))
            refreshed = (
                await conn.execute(t.sprints.select().where(t.sprints.c.id == sprint_id))
            ).mappings().first()
            if refreshed is not None:
                await self._enqueue_iteration_projections_tx(conn, dict(refreshed))

    async def update_sprint_with_pm_projections(
        self,
        sprint_id: UUID,
        *,
        expected_revision: int | None = None,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """CAS-update an iteration and return durable projection intents."""
        values = dict(kwargs)
        values["revision"] = t.sprints.c.revision + 1
        values["updated_at"] = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            current = await self._mapping_first(
                await conn.execute(t.sprints.select().where(t.sprints.c.id == sprint_id).with_for_update())
            )
            if current is None or (
                expected_revision is not None
                and int(current.get("revision") or 1) != int(expected_revision)
            ):
                raise ValueError("sprint not found or revision conflict")
            await conn.execute(t.sprints.update().where(t.sprints.c.id == sprint_id).values(**values))
            refreshed = await self._mapping_first(
                await conn.execute(t.sprints.select().where(t.sprints.c.id == sprint_id))
            )
            if refreshed is None:
                raise ValueError("sprint disappeared during update")
            refreshed_dict = dict(refreshed)
            queued = await self._enqueue_iteration_projections_tx(conn, refreshed_dict)
        return refreshed_dict, queued

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
        priority: str = "medium",
        assigned_team: str | None = None,
        assigned_agent: str | None = None,
        estimated_hours: Decimal | None = None,
        story_points: int | None = None,
        dependencies: list[UUID] | None = None,
        issue_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create an issue/work-item."""
        iid = issue_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": iid,
            "project_id": project_id,
            "sprint_id": sprint_id,
            "parent_issue_id": parent_issue_id,
            "title": title,
            "description": description,
            "issue_type": issue_type,
            "status": "backlog",
            "priority": priority,
            "assigned_team": assigned_team,
            "assigned_agent": assigned_agent,
            "estimated_hours": estimated_hours,
            "story_points": story_points,
            "dependencies": dependencies,
            "revision": 1,
            "updated_at": now,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.issues.insert().values(**values))
            await self._enqueue_issue_projections_tx(conn, values)
        return values

    async def get_issue(self, issue_id: UUID) -> dict[str, Any] | None:
        """Fetch an issue by ID."""
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(t.issues.select().where(t.issues.c.id == issue_id)))
                .mappings()
                .first()
            )
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

    async def update_issue(self, issue_id: UUID, **kwargs: Any) -> None:
        """Update issue fields and atomically advance its canonical revision."""
        expected_revision = kwargs.pop("expected_revision", None)
        values = dict(kwargs)
        values["revision"] = t.issues.c.revision + 1
        values["updated_at"] = datetime.now(tz=UTC)
        query = t.issues.update().where(t.issues.c.id == issue_id)
        if expected_revision is not None:
            query = query.where(t.issues.c.revision == int(expected_revision))
        async with self.engine.begin() as conn:
            result = await conn.execute(query.values(**values))
            if result.rowcount == 0:
                raise ValueError("issue not found or revision conflict")
            refreshed = await self._mapping_first(
                await conn.execute(t.issues.select().where(t.issues.c.id == issue_id))
            )
            if refreshed is not None:
                await self._enqueue_issue_projections_tx(conn, dict(refreshed))

    @staticmethod
    def _pm_json_safe(value: Any) -> Any:
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, dict):
            return {str(key): AgentStorage._pm_json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [AgentStorage._pm_json_safe(item) for item in value]
        return value

    @staticmethod
    async def _mapping_rows(result: Any) -> list[Any]:
        """Read SQLAlchemy mapping rows while tolerating lightweight test doubles.

        Real ``AsyncConnection`` results expose synchronous ``mappings().all``
        methods.  Some storage unit tests use ``AsyncMock`` connections whose
        result methods are awaitable; accepting both keeps the transaction
        helpers testable without weakening the production query path.
        """
        mappings = result.mappings()
        if hasattr(mappings, "__await__"):
            mappings = await mappings
        rows = mappings.all()
        if hasattr(rows, "__await__"):
            rows = await rows
        if isinstance(rows, list):
            return rows
        if isinstance(rows, tuple):
            return list(rows)
        try:
            return list(rows)
        except TypeError:
            return []

    @staticmethod
    async def _mapping_first(result: Any) -> Any | None:
        """Return the first mapping row for real or mocked SQL results."""
        mappings = result.mappings()
        if hasattr(mappings, "__await__"):
            mappings = await mappings
        row = mappings.first()
        if hasattr(row, "__await__"):
            row = await row
        return row if isinstance(row, Mapping) else None

    async def _enqueue_binding_backfill_tx(
        self,
        conn: Any,
        binding: dict[str, Any],
    ) -> int:
        """Queue canonical records that predate a newly-created binding."""
        binding_direction = str(binding.get("direction") or "outbound")
        binding_status = str(binding.get("status") or "DISABLED")
        if binding_direction not in {"outbound", "both"} or binding_status not in {
            "SHADOW",
            "READ_ONLY",
            "ACTIVE",
            "DRAINING",
        }:
            return 0

        connection = await self._mapping_first(
            await conn.execute(
                t.pm_connections.select().where(t.pm_connections.c.id == binding["connection_id"])
            )
        )
        if connection is None or connection.get("status") == "DISABLED":
            return 0

        # YouTrack's connection selector is the documented default for a
        # binding.  Materialize it onto the binding before using the shared
        # project/iteration queue helpers so an initial backfill cannot omit
        # those aggregates merely because the request omitted a duplicate
        # selector.
        selector_project = str((connection.get("config") or {}).get("project_id") or "")
        if not binding.get("external_project_id") and selector_project:
            await conn.execute(
                t.pm_project_bindings.update()
                .where(t.pm_project_bindings.c.id == binding["id"])
                .values(
                    external_project_id=selector_project,
                    revision=t.pm_project_bindings.c.revision + 1,
                    updated_at=datetime.now(tz=UTC),
                )
            )
            binding["external_project_id"] = selector_project

        queued = 0
        project = await self._mapping_first(
            await conn.execute(t.projects.select().where(t.projects.c.id == binding["project_id"]))
        )
        if project is not None and binding.get("external_project_id"):
            queued += len(await self._enqueue_project_projections_tx(conn, dict(project)))
            sprint_rows = await self._mapping_rows(
                await conn.execute(
                    t.sprints.select()
                    .where(t.sprints.c.project_id == binding["project_id"])
                    .order_by(t.sprints.c.sprint_number)
                )
            )
            for sprint in sprint_rows:
                queued += len(await self._enqueue_iteration_projections_tx(conn, dict(sprint)))

        issue_rows = await self._mapping_rows(
            await conn.execute(
                t.issues.select()
                .where(t.issues.c.project_id == binding["project_id"])
                .order_by(t.issues.c.created_at)
            )
        )
        for issue in issue_rows:
            issue_dict = dict(issue)
            issue_events = await self._enqueue_issue_projections_tx(conn, issue_dict)
            queued += len(issue_events)
            comment_rows = await self._mapping_rows(
                await conn.execute(
                    t.work_item_comments.select()
                    .where(t.work_item_comments.c.issue_id == issue_dict["id"])
                    .order_by(t.work_item_comments.c.created_at)
                )
            )
            for comment in comment_rows:
                queued += len(
                    await self._enqueue_comment_projections_tx(conn, issue_dict, dict(comment))
                )
        return queued

    async def _enqueue_project_projections_tx(
        self,
        conn: Any,
        project: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Queue a project projection in the canonical project transaction."""
        bindings = await self._mapping_rows(
            await conn.execute(
                t.pm_project_bindings.select()
                .where(t.pm_project_bindings.c.project_id == project["id"])
                .where(t.pm_project_bindings.c.external_project_id.is_not(None))
                .where(t.pm_project_bindings.c.direction.in_(["outbound", "both"]))
                .where(t.pm_project_bindings.c.status.in_(["SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"]))
            )
        )
        if not bindings:
            return []
        connection_ids = [binding["connection_id"] for binding in bindings]
        enabled = {
            row["id"]
            for row in await self._mapping_rows(
                await conn.execute(
                    t.pm_connections.select()
                    .where(t.pm_connections.c.id.in_(connection_ids))
                    .where(t.pm_connections.c.status != "DISABLED")
                )
            )
        }
        safe_project = self._pm_json_safe(
            {
                "id": project["id"],
                "name": project.get("name") or "",
                "description": project.get("description"),
                "state": project.get("state") or "INIT",
                "revision": project.get("revision") or 1,
                "updated_at": project.get("updated_at"),
            }
        )
        revision = int(project.get("revision") or 1)
        queued: list[dict[str, Any]] = []
        for binding in bindings:
            if binding["connection_id"] not in enabled:
                continue
            key = f"{binding['id']}:{project['id']}:{revision}:upsert_project"
            values = {
                "id": uuid4(),
                "connection_id": binding["connection_id"],
                "aggregate_type": "project",
                "aggregate_id": project["id"],
                "canonical_revision": revision,
                "operation": "upsert_project",
                "idempotency_key": key,
                "payload": {"binding_id": str(binding["id"]), "project": safe_project},
                "created_at": datetime.now(tz=UTC),
            }
            stmt = (
                pg_insert(t.pm_outbox_events)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[t.pm_outbox_events.c.idempotency_key])
                .returning(t.pm_outbox_events)
            )
            row = await self._mapping_first(await conn.execute(stmt))
            queued.append(dict(row) if row else values)
        return queued

    async def _enqueue_iteration_projections_tx(
        self,
        conn: Any,
        iteration: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Queue a sprint/iteration projection in the canonical transaction."""
        bindings = await self._mapping_rows(
            await conn.execute(
                t.pm_project_bindings.select()
                .where(t.pm_project_bindings.c.project_id == iteration["project_id"])
                .where(t.pm_project_bindings.c.external_project_id.is_not(None))
                .where(t.pm_project_bindings.c.direction.in_(["outbound", "both"]))
                .where(t.pm_project_bindings.c.status.in_(["SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"]))
            )
        )
        if not bindings:
            return []
        connection_ids = [binding["connection_id"] for binding in bindings]
        enabled = {
            row["id"]
            for row in await self._mapping_rows(
                await conn.execute(
                    t.pm_connections.select()
                    .where(t.pm_connections.c.id.in_(connection_ids))
                    .where(t.pm_connections.c.status != "DISABLED")
                )
            )
        }
        safe_iteration = self._pm_json_safe(
            {
                "id": iteration["id"],
                "project_id": iteration["project_id"],
                "number": iteration.get("sprint_number") or iteration.get("number") or 1,
                "name": iteration.get("milestone") or iteration.get("name"),
                "goal": iteration.get("goal"),
                "status": iteration.get("status") or "PLANNED",
                "revision": iteration.get("revision") or 1,
                "updated_at": iteration.get("updated_at"),
            }
        )
        revision = int(iteration.get("revision") or 1)
        queued: list[dict[str, Any]] = []
        for binding in bindings:
            if binding["connection_id"] not in enabled:
                continue
            key = f"{binding['id']}:{iteration['id']}:{revision}:upsert_iteration"
            values = {
                "id": uuid4(),
                "connection_id": binding["connection_id"],
                "aggregate_type": "iteration",
                "aggregate_id": iteration["id"],
                "canonical_revision": revision,
                "operation": "upsert_iteration",
                "idempotency_key": key,
                "payload": {"binding_id": str(binding["id"]), "iteration": safe_iteration},
                "created_at": datetime.now(tz=UTC),
            }
            stmt = (
                pg_insert(t.pm_outbox_events)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[t.pm_outbox_events.c.idempotency_key])
                .returning(t.pm_outbox_events)
            )
            row = await self._mapping_first(await conn.execute(stmt))
            queued.append(dict(row) if row else values)
        return queued

    async def _enqueue_issue_projections_tx(
        self,
        conn: Any,
        issue: dict[str, Any],
        *,
        exclude_connection_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Insert projection events on the same transaction as the issue write."""
        bindings = await self._mapping_rows(
            await conn.execute(
                t.pm_project_bindings.select()
                .where(t.pm_project_bindings.c.project_id == issue["project_id"])
                .where(t.pm_project_bindings.c.direction.in_(["outbound", "both"]))
                .where(t.pm_project_bindings.c.status.in_(["SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"]))
            )
        )
        if not bindings:
            return []
        connection_ids = [binding["connection_id"] for binding in bindings]
        connections = await self._mapping_rows(
            await conn.execute(
                t.pm_connections.select()
                .where(t.pm_connections.c.id.in_(connection_ids))
                .where(t.pm_connections.c.status != "DISABLED")
            )
        )
        enabled = {row["id"] for row in connections}
        # Keep the outbox payload on the provider-neutral work-item contract.
        # The database row also contains persistence-only columns (for
        # example ``created_at``, ``dependencies`` and ``issue_type``) that
        # CanonicalWorkItem intentionally rejects.  Passing the raw row made
        # every live work-item projection fail validation before it reached a
        # provider.  Normalize the row here, at the transactional boundary,
        # just as the HTTP fallback path does in the orchestrator.
        safe_issue = self._pm_json_safe(
            {
                "id": issue["id"],
                "project_id": issue["project_id"],
                "title": issue.get("title") or "Untitled issue",
                "description": issue.get("description"),
                "item_type": issue.get("issue_type") or "TASK",
                "status": issue.get("status") or "backlog",
                "priority": issue.get("priority") or "medium",
                "sprint_id": issue.get("sprint_id"),
                "parent_id": issue.get("parent_issue_id"),
                "assigned_team": issue.get("assigned_team"),
                "assigned_agent": issue.get("assigned_agent"),
                "estimated_hours": issue.get("estimated_hours"),
                "actual_hours": issue.get("actual_hours"),
                "story_points": issue.get("story_points"),
                "revision": issue.get("revision") or 1,
                "updated_at": issue.get("updated_at"),
            }
        )
        queued: list[dict[str, Any]] = []
        for binding in bindings:
            if (
                binding["connection_id"] not in enabled
                or (
                    exclude_connection_id is not None
                    and binding["connection_id"] == exclude_connection_id
                )
            ):
                continue
            revision = int(issue.get("revision") or 1)
            key = f"{binding['id']}:{issue['id']}:{revision}:upsert"
            values = {
                "id": uuid4(),
                "connection_id": binding["connection_id"],
                "aggregate_type": "work_item",
                "aggregate_id": issue["id"],
                "canonical_revision": revision,
                "operation": "upsert_work_item",
                "idempotency_key": key,
                "payload": {"binding_id": str(binding["id"]), "item": safe_issue},
                "created_at": datetime.now(tz=UTC),
            }
            stmt = (
                pg_insert(t.pm_outbox_events)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[t.pm_outbox_events.c.idempotency_key])
                .returning(t.pm_outbox_events)
            )
            row = await self._mapping_first(await conn.execute(stmt))
            queued.append(dict(row) if row else values)
        return queued

    async def _eligible_pm_bindings_tx(
        self,
        conn: Any,
        project_id: UUID,
        *,
        exclude_connection_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Return outbound bindings whose connection is not disabled.

        Keeping this query in the storage transaction prevents a canonical
        comment/link from being committed without the corresponding durable
        projection intent.
        """
        bindings = await self._mapping_rows(
            await conn.execute(
                t.pm_project_bindings.select()
                .where(t.pm_project_bindings.c.project_id == project_id)
                .where(t.pm_project_bindings.c.direction.in_(["outbound", "both"]))
                .where(t.pm_project_bindings.c.status.in_(["SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"]))
            )
        )
        if not bindings:
            return []
        connection_ids = [binding["connection_id"] for binding in bindings]
        enabled = {
            row["id"]
            for row in await self._mapping_rows(
                await conn.execute(
                    t.pm_connections.select()
                    .where(t.pm_connections.c.id.in_(connection_ids))
                    .where(t.pm_connections.c.status != "DISABLED")
                )
            )
        }
        return [
            dict(binding)
            for binding in bindings
            if binding["connection_id"] in enabled
            and (exclude_connection_id is None or binding["connection_id"] != exclude_connection_id)
        ]

    async def _enqueue_comment_projections_tx(
        self,
        conn: Any,
        issue: dict[str, Any],
        comment: dict[str, Any],
        *,
        exclude_connection_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        bindings = await self._eligible_pm_bindings_tx(
            conn,
            issue["project_id"],
            exclude_connection_id=exclude_connection_id,
        )
        body = str(comment.get("body") or "")
        if str(comment.get("origin") or "aiat") == "aiat":
            attribution = [f"AIAT actor: {comment.get('actor_id') or 'operator'}"]
            if comment.get("run_id"):
                attribution.append(f"Run: {comment['run_id']}")
            if comment.get("evidence_id"):
                attribution.append(f"Evidence: {comment['evidence_id']}")
            body = (
                f"<!-- aiat:comment={comment['id']} -->\n"
                + "\n".join(attribution)
                + "\n\n"
                + body
            )
        queued: list[dict[str, Any]] = []
        for binding in bindings:
            key = f"{binding['id']}:{comment['id']}:comment"
            values = {
                "id": uuid4(),
                "connection_id": binding["connection_id"],
                "aggregate_type": "comment",
                "aggregate_id": issue["id"],
                "canonical_revision": int(issue.get("revision") or 1),
                "operation": "project_comment",
                "idempotency_key": key,
                "payload": {
                    "binding_id": str(binding["id"]),
                    "comment": {
                        "id": str(comment["id"]),
                        "body": body,
                        "actor_id": comment.get("actor_id"),
                        "run_id": str(comment["run_id"]) if comment.get("run_id") else None,
                        "approval_id": str(comment["approval_id"]) if comment.get("approval_id") else None,
                        "evidence_id": comment.get("evidence_id"),
                        "body_blob_ref": comment.get("body_blob_ref"),
                    },
                },
                "created_at": datetime.now(tz=UTC),
            }
            stmt = (
                pg_insert(t.pm_outbox_events)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[t.pm_outbox_events.c.idempotency_key])
                .returning(t.pm_outbox_events)
            )
            row = (await conn.execute(stmt)).mappings().first()
            queued.append(dict(row) if row else values)
        return queued

    async def _enqueue_link_projections_tx(
        self,
        conn: Any,
        issue: dict[str, Any],
        link: dict[str, Any],
        *,
        exclude_connection_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        bindings = await self._eligible_pm_bindings_tx(
            conn,
            issue["project_id"],
            exclude_connection_id=exclude_connection_id,
        )
        queued: list[dict[str, Any]] = []
        for binding in bindings:
            key = f"{binding['id']}:{link['id']}:link"
            values = {
                "id": uuid4(),
                "connection_id": binding["connection_id"],
                "aggregate_type": "link",
                "aggregate_id": issue["id"],
                "canonical_revision": int(issue.get("revision") or 1),
                "operation": "project_link",
                "idempotency_key": key,
                "payload": {
                    "binding_id": str(binding["id"]),
                    "link": {
                        "id": str(link["id"]),
                        "link_type": link["link_type"],
                        "target_type": link["target_type"],
                        "target_id": link["target_id"],
                        "metadata": link.get("metadata") or {},
                    },
                },
                "created_at": datetime.now(tz=UTC),
            }
            stmt = (
                pg_insert(t.pm_outbox_events)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[t.pm_outbox_events.c.idempotency_key])
                .returning(t.pm_outbox_events)
            )
            row = (await conn.execute(stmt)).mappings().first()
            queued.append(dict(row) if row else values)
        return queued

    async def create_issue_with_pm_projections(
        self,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Create an issue and its provider projections atomically."""
        iid = kwargs.pop("issue_id", None) or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": iid,
            "project_id": kwargs["project_id"],
            "sprint_id": kwargs.get("sprint_id"),
            "parent_issue_id": kwargs.get("parent_issue_id"),
            "title": kwargs["title"],
            "description": kwargs.get("description"),
            "issue_type": kwargs["issue_type"],
            "status": "backlog",
            "priority": kwargs.get("priority", "medium"),
            "assigned_team": kwargs.get("assigned_team"),
            "assigned_agent": kwargs.get("assigned_agent"),
            "estimated_hours": kwargs.get("estimated_hours"),
            "story_points": kwargs.get("story_points"),
            "dependencies": kwargs.get("dependencies"),
            "revision": 1,
            "updated_at": now,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.issues.insert().values(**values))
            queued = await self._enqueue_issue_projections_tx(conn, values)
        return values, queued

    async def update_issue_with_pm_projections(
        self,
        issue_id: UUID,
        *,
        expected_revision: int | None = None,
        exclude_connection_id: UUID | None = None,
        evidence_records: list[dict[str, Any]] | None = None,
        projection_evidence: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]] | tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        """CAS-update an issue, projections, and optional command evidence atomically.

        ACTIVE inbound commands pass sanitized actor/command evidence here.
        Evidence-write failure raises inside the same transaction, rolling back
        the canonical CAS and any projection intents together.
        """
        atomic_evidence = list(evidence_records or [])
        transaction_id = uuid4() if atomic_evidence or projection_evidence else None
        async with self.engine.begin() as conn:
            current = (
                await conn.execute(
                    t.issues.select().where(t.issues.c.id == issue_id).with_for_update()
                )
            ).mappings().first()
            if current is None or (
                expected_revision is not None
                and int(current.get("revision") or 1) != int(expected_revision)
            ):
                raise ValueError("issue not found or revision conflict")
            values = dict(kwargs)
            values["revision"] = t.issues.c.revision + 1
            values["updated_at"] = datetime.now(tz=UTC)
            await conn.execute(t.issues.update().where(t.issues.c.id == issue_id).values(**values))
            refreshed = (
                await conn.execute(t.issues.select().where(t.issues.c.id == issue_id))
            ).mappings().first()
            assert refreshed is not None
            refreshed_dict = dict(refreshed)
            queued = await self._enqueue_issue_projections_tx(
                conn,
                refreshed_dict,
                exclude_connection_id=exclude_connection_id,
            )
            if projection_evidence is not None:
                atomic_evidence.append(
                    {
                        "evidence_type": "active_inbound_projection",
                        "connection_id": projection_evidence["connection_id"],
                        "external_id": str(projection_evidence.get("external_id") or ""),
                        "project_id": refreshed_dict.get("project_id"),
                        "binding_id": projection_evidence.get("binding_id"),
                        "payload": {
                            **projection_evidence,
                            "transaction_id": str(transaction_id),
                            "queued_outbox_ids": [str(item.get("id")) for item in queued],
                            "queued_count": len(queued),
                            "disposition": (
                                "projected_to_other_connections_and_origin_suppressed"
                                if queued
                                else "originating_connection_suppressed"
                            ),
                        },
                        "idempotency_key": f"active-projection:{projection_evidence['command_key']}",
                    }
                )
            evidence_ids: dict[str, Any] = {}
            if atomic_evidence:
                for record in atomic_evidence:
                    evidence_id = uuid4()
                    values = {
                        "id": evidence_id,
                        "connection_id": record["connection_id"],
                        "binding_id": record.get("binding_id"),
                        "project_id": record.get("project_id") or refreshed_dict.get("project_id"),
                        "evidence_type": record["evidence_type"],
                        "external_id": record.get("external_id"),
                        "repository": record.get("repository"),
                        "payload": self._pm_json_safe({
                            **(record.get("payload") or {}),
                            "transaction_id": str(transaction_id),
                        }),
                        "idempotency_key": record["idempotency_key"],
                        "created_at": datetime.now(tz=UTC),
                    }
                    stmt = (
                        pg_insert(t.integration_evidence_records)
                        .values(**values)
                        .on_conflict_do_update(
                            constraint="uq_integration_evidence_idempotency",
                            set_={
                                "payload": values["payload"],
                                "external_id": values["external_id"],
                                "binding_id": values["binding_id"],
                                "project_id": values["project_id"],
                            },
                        )
                        .returning(t.integration_evidence_records.c.id)
                    )
                    row = await self._mapping_first(await conn.execute(stmt))
                    if row is None:
                        raise RuntimeError("integration evidence write returned no row")
                    evidence_ids[record["evidence_type"]] = str(row["id"])
        if atomic_evidence:
            return refreshed_dict, queued, {
                "transaction_id": str(transaction_id),
                "evidence_ids": evidence_ids,
            }
        return refreshed_dict, queued

    async def create_work_item_comment_with_pm_projections(
        self,
        *,
        issue_id: UUID,
        body: str,
        actor_id: str,
        run_id: UUID | None = None,
        approval_id: UUID | None = None,
        evidence_id: str | None = None,
        body_blob_ref: str | None = None,
        origin: str = "aiat",
        exclude_connection_id: UUID | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Commit a comment and its projection intents atomically."""
        now = datetime.now(tz=UTC)
        values = {
            "id": uuid4(),
            "issue_id": issue_id,
            "body": body,
            "actor_id": actor_id,
            "run_id": run_id,
            "approval_id": approval_id,
            "evidence_id": evidence_id,
            "body_blob_ref": body_blob_ref,
            "origin": origin,
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            issue = (
                await conn.execute(
                    t.issues.select().where(t.issues.c.id == issue_id).with_for_update()
                )
            ).mappings().first()
            if issue is None:
                raise ValueError("issue not found")
            await conn.execute(t.work_item_comments.insert().values(**values))
            queued = await self._enqueue_comment_projections_tx(
                conn,
                dict(issue),
                values,
                exclude_connection_id=exclude_connection_id,
            )
        return values, queued

    async def create_work_item_link_with_pm_projections(
        self,
        *,
        issue_id: UUID,
        link_type: str,
        target_type: str,
        target_id: str,
        metadata: dict[str, Any] | None = None,
        exclude_connection_id: UUID | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Commit an idempotent link and its projection intents atomically."""
        values = {
            "id": uuid4(),
            "issue_id": issue_id,
            "link_type": link_type,
            "target_type": target_type,
            "target_id": target_id,
            "metadata": metadata or {},
            "created_at": datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            issue = (
                await conn.execute(
                    t.issues.select().where(t.issues.c.id == issue_id).with_for_update()
                )
            ).mappings().first()
            if issue is None:
                raise ValueError("issue not found")
            stmt = (
                pg_insert(t.work_item_links)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_work_item_link")
                .returning(t.work_item_links)
            )
            row = (await conn.execute(stmt)).mappings().first()
            link = dict(row) if row else None
            if link is None:
                link = dict(
                    (
                        await conn.execute(
                            t.work_item_links.select()
                            .where(t.work_item_links.c.issue_id == issue_id)
                            .where(t.work_item_links.c.link_type == link_type)
                            .where(t.work_item_links.c.target_type == target_type)
                            .where(t.work_item_links.c.target_id == target_id)
                        )
                    ).mappings().first()
                    or values
                )
            queued = await self._enqueue_link_projections_tx(
                conn,
                dict(issue),
                link,
                exclude_connection_id=exclude_connection_id,
            )
        return link, queued

    # ═══════════════════════════════════════════════════════════════════════════
    # Provider-neutral PM integration control plane
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_pm_connection(
        self,
        *,
        provider_kind: str,
        display_name: str,
        base_url: str,
        credential_ref: str,
        capability_profile: str = "pm",
        config: dict[str, Any] | None = None,
        created_by: str = "operator",
        connection_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a provider connection without resolving or storing secrets."""
        from mas_core.integrations.contracts import (
            ProviderConnection,
            validate_credential_references,
        )

        safe_config = validate_credential_references(config or {})
        if provider_kind.lower() != "fake" and any(
            key in safe_config
            for key in ("webhook_secret_test_only", "webhook_token_test_only")
        ):
            raise ValueError("test-only webhook credentials are permitted only for fake connections")
        validated = ProviderConnection(
            id=connection_id or uuid4(),
            provider_kind=provider_kind,
            display_name=display_name,
            base_url=base_url,
            credential_ref=credential_ref,
            capability_profile=capability_profile,
            config=safe_config,
        )
        from urllib.parse import urlsplit

        parsed_url = urlsplit(validated.base_url)
        if validated.provider_kind.lower() != "fake" and parsed_url.scheme != "https" and parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("non-fake provider connections must use HTTPS")
        allowed_hosts = validated.config.get("allowed_hosts")
        if allowed_hosts:
            from urllib.parse import urlsplit

            allowed = {str(host).strip().lower() for host in allowed_hosts if str(host).strip()}
            hostname = str(urlsplit(validated.base_url).hostname or "").lower()
            if allowed and hostname not in allowed:
                raise ValueError(f"provider host {hostname!r} is not in the connection allowlist")
        now = datetime.now(tz=UTC)
        values = {
            "id": validated.id,
            "provider_kind": validated.provider_kind,
            "display_name": validated.display_name,
            "base_url": validated.base_url,
            "credential_ref": validated.credential_ref,
            "capability_profile": validated.capability_profile,
            "config": validated.config,
            "schema_version": validated.schema_version,
            "status": validated.status.value,
            "revision": 1,
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.pm_connections.insert().values(**values))
        return values

    async def get_pm_connection(self, connection_id: UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(t.pm_connections.select().where(t.pm_connections.c.id == connection_id))
            ).mappings().first()
        return dict(row) if row else None

    async def list_pm_connections(self, *, status: str | None = None) -> list[dict[str, Any]]:
        query = t.pm_connections.select().order_by(t.pm_connections.c.created_at)
        if status:
            query = query.where(t.pm_connections.c.status == status)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def update_pm_connection(self, connection_id: UUID, **kwargs: Any) -> dict[str, Any] | None:
        allowed = {"display_name", "base_url", "credential_ref", "capability_profile", "config", "status", "schema_version", "last_health_at", "last_health_status", "last_health_error"}
        values = {key: value for key, value in kwargs.items() if key in allowed}
        if "status" in values and values["status"] not in {
            "DISABLED",
            "SHADOW",
            "READ_ONLY",
            "ACTIVE",
            "DRAINING",
        }:
            raise ValueError("invalid PM connection status")
        if not values:
            return await self.get_pm_connection(connection_id)
        if any(key in values for key in {"base_url", "credential_ref", "capability_profile", "config"}):
            from urllib.parse import urlsplit

            from mas_core.integrations.contracts import (
                ProviderConnection,
                validate_credential_references,
            )

            current = await self.get_pm_connection(connection_id)
            if current is None:
                return None
            merged_config = validate_credential_references(values.get("config", current.get("config") or {}))
            if str(current.get("provider_kind") or "").lower() != "fake" and any(
                key in merged_config
                for key in ("webhook_secret_test_only", "webhook_token_test_only")
            ):
                raise ValueError("test-only webhook credentials are permitted only for fake connections")
            validated = ProviderConnection(
                id=current["id"],
                provider_kind=str(current["provider_kind"]),
                display_name=str(values.get("display_name", current["display_name"])),
                base_url=str(values.get("base_url", current["base_url"])),
                credential_ref=str(values.get("credential_ref", current["credential_ref"])),
                capability_profile=str(values.get("capability_profile", current.get("capability_profile") or "pm")),
                config=merged_config,
                schema_version=int(values.get("schema_version", current.get("schema_version") or 1)),
            )
            parsed_url = urlsplit(validated.base_url)
            if validated.provider_kind.lower() != "fake" and parsed_url.scheme != "https" and parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("non-fake provider connections must use HTTPS")
            allowed_hosts = merged_config.get("allowed_hosts")
            if allowed_hosts:
                allowed = {str(host).strip().lower() for host in allowed_hosts if str(host).strip()}
                hostname = str(parsed_url.hostname or "").lower()
                if allowed and hostname not in allowed:
                    raise ValueError(f"provider host {hostname!r} is not in the connection allowlist")
            values.update(
                {
                    "base_url": validated.base_url,
                    "credential_ref": validated.credential_ref,
                    "capability_profile": validated.capability_profile,
                    "config": validated.config,
                }
            )
        values["updated_at"] = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            if any(
                key in values
                for key in {"display_name", "base_url", "credential_ref", "capability_profile", "config", "status", "schema_version"}
            ):
                values["revision"] = t.pm_connections.c.revision + 1
            result = await conn.execute(
                t.pm_connections.update().where(t.pm_connections.c.id == connection_id).values(**values)
            )
            if result.rowcount == 0:
                return None
        return await self.get_pm_connection(connection_id)

    async def get_pm_inbox_event(self, event_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.pm_inbox_events, t.pm_inbox_events.c.id, event_id)

    async def create_pm_external_actor_mapping(
        self,
        *,
        connection_id: UUID,
        provider_kind: str,
        tenant_key: str,
        external_actor_id: str,
        actor_snapshot: dict[str, Any],
        aiat_identity_id: str,
        authorized_scopes: list[str],
        created_by: str,
        approved_by: str,
        evidence_refs: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create one trusted immutable actor mapping and its audit atomically."""
        if not external_actor_id or not aiat_identity_id:
            raise ValueError("external actor and AIAT identity IDs are required")
        if not authorized_scopes:
            raise ValueError("actor mapping requires at least one authorized command scope")
        now = datetime.now(tz=UTC)
        values = {
            "id": uuid4(), "connection_id": connection_id, "provider_kind": provider_kind,
            "tenant_key": tenant_key, "external_actor_id": external_actor_id,
            "actor_snapshot": self._pm_json_safe(actor_snapshot), "aiat_identity_id": aiat_identity_id,
            "status": "TRUSTED", "authorized_scopes": sorted({str(item) for item in authorized_scopes}),
            "created_by": created_by, "approved_by": approved_by, "created_at": now, "approved_at": now,
            "revision": 1, "updated_at": now,
        }
        async with self.engine.begin() as conn:
            existing = (await conn.execute(
                t.pm_external_actor_mappings.select()
                .where(t.pm_external_actor_mappings.c.connection_id == connection_id)
                .where(t.pm_external_actor_mappings.c.tenant_key == tenant_key)
                .where(t.pm_external_actor_mappings.c.external_actor_id == external_actor_id)
                .with_for_update()
            )).mappings().first()
            if existing is not None:
                if str(existing.get("status")) == "TRUSTED" and str(existing.get("aiat_identity_id")) == aiat_identity_id:
                    audit = {
                        "id": uuid4(), "mapping_id": existing["id"], "action": "IDEMPOTENT_RECONFIRM",
                        "actor": approved_by, "before_state": self._pm_json_safe(dict(existing)),
                        "after_state": self._pm_json_safe(dict(existing)), "evidence_refs": self._pm_json_safe(evidence_refs),
                        "occurred_at": now,
                    }
                    await conn.execute(t.pm_external_actor_mapping_audits.insert().values(**audit))
                    return dict(existing), audit
                raise ValueError("an actor mapping already exists for this immutable provider identity")
            await conn.execute(t.pm_external_actor_mappings.insert().values(**values))
            audit = {
                "id": uuid4(), "mapping_id": values["id"], "action": "CREATED_AND_APPROVED",
                "actor": approved_by, "before_state": {}, "after_state": self._pm_json_safe(values),
                "evidence_refs": self._pm_json_safe(evidence_refs), "occurred_at": now,
            }
            await conn.execute(t.pm_external_actor_mapping_audits.insert().values(**audit))
        return values, audit

    async def get_pm_external_actor_mapping(
        self, *, connection_id: UUID, external_actor_id: str, tenant_key: str | None = None
    ) -> dict[str, Any] | None:
        query = t.pm_external_actor_mappings.select().where(
            t.pm_external_actor_mappings.c.connection_id == connection_id
        ).where(t.pm_external_actor_mappings.c.external_actor_id == external_actor_id)
        if tenant_key is not None:
            query = query.where(t.pm_external_actor_mappings.c.tenant_key == tenant_key)
        async with self.engine.connect() as conn:
            row = (await conn.execute(query)).mappings().first()
        return dict(row) if row else None

    async def get_pm_external_actor_mapping_by_id(self, mapping_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.pm_external_actor_mappings, t.pm_external_actor_mappings.c.id, mapping_id)

    async def list_pm_external_actor_mappings(
        self, *, connection_id: UUID, status: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """List durable actor mappings for lifecycle policy snapshots.

        Lifecycle plans must record the exact trusted actor scope that will be
        exposed by an ACTIVE binding.  Keep this read path bounded and expose
        only persisted mapping rows; callers remain responsible for redacting
        sensitive actor snapshots from evidence.
        """
        query = (
            t.pm_external_actor_mappings.select()
            .where(t.pm_external_actor_mappings.c.connection_id == connection_id)
            .order_by(t.pm_external_actor_mappings.c.created_at)
            .limit(limit)
        )
        if status is not None:
            query = query.where(t.pm_external_actor_mappings.c.status == status)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def count_trusted_pm_external_actor_mappings(self, connection_id: UUID) -> int:
        async with self.engine.connect() as conn:
            value = await conn.scalar(
                sa.select(sa.func.count()).select_from(t.pm_external_actor_mappings).where(
                    t.pm_external_actor_mappings.c.connection_id == connection_id
                ).where(t.pm_external_actor_mappings.c.status == "TRUSTED")
            )
        return int(value or 0)

    async def revoke_pm_external_actor_mapping(
        self, mapping_id: UUID, *, connection_id: UUID, actor: str, reason: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            current = (await conn.execute(
                t.pm_external_actor_mappings.select()
                .where(t.pm_external_actor_mappings.c.id == mapping_id)
                .where(t.pm_external_actor_mappings.c.connection_id == connection_id)
                .with_for_update()
            )).mappings().first()
            if current is None:
                return None, None
            values = {
                "status": "REVOKED", "revoked_by": actor, "revoked_at": now,
                "revocation_reason": reason, "revision": t.pm_external_actor_mappings.c.revision + 1, "updated_at": now,
            }
            await conn.execute(t.pm_external_actor_mappings.update().where(t.pm_external_actor_mappings.c.id == mapping_id).values(**values))
            updated = (await conn.execute(t.pm_external_actor_mappings.select().where(t.pm_external_actor_mappings.c.id == mapping_id))).mappings().first()
            audit = {"id": uuid4(), "mapping_id": mapping_id, "action": "REVOKED", "actor": actor,
                     "before_state": self._pm_json_safe(dict(current)), "after_state": self._pm_json_safe(dict(updated)),
                     "evidence_refs": {"reason": reason}, "occurred_at": now}
            await conn.execute(t.pm_external_actor_mapping_audits.insert().values(**audit))
        return dict(updated), audit

    async def create_pm_inbound_canary_plan(self, plan: Any, *, digest: str) -> dict[str, Any]:
        """Persist a bounded canary before it can be reviewed or armed."""
        now = datetime.now(tz=UTC)
        values = {
            "id": plan.plan_id, "connection_id": plan.connection_id, "binding_id": plan.binding_id,
            "project_id": plan.project_id, "canonical_issue_id": plan.canonical_issue_id,
            "external_issue_id": plan.external_issue_id, "mapping_id": plan.mapping_id,
            "actor_mapping_id": plan.actor_mapping_id,
            "expected_connection_status": plan.expected_connection_status,
            "expected_binding_status": plan.expected_binding_status,
            "expected_connection_revision": plan.expected_connection_revision,
            "expected_binding_revision": plan.expected_binding_revision,
            "expected_canonical_revision": plan.expected_canonical_revision,
            "current_priority": plan.current_priority, "target_priority": plan.target_priority,
            "max_command_count": plan.max_command_count, "accepted_command_count": 0,
            "operations": self._pm_json_safe(plan.operations), "gate_results": self._pm_json_safe(plan.gate_results),
            "evidence_refs": self._pm_json_safe(plan.evidence_refs),
            "rollback_operations": self._pm_json_safe(plan.rollback_operations),
            "created_by": plan.created_by, "created_at": plan.created_at, "expires_at": plan.expires_at,
            "digest": digest, "status": "PLANNED", "updated_at": now,
        }
        async with self.engine.begin() as conn:
            # Expiry is terminal evidence, not a rollback.  Record it before
            # considering a successor so a new plan can never rewrite an old
            # pending plan's history as if an operator had disarmed it.
            await conn.execute(
                t.pm_inbound_canary_plans.update()
                .where(t.pm_inbound_canary_plans.c.binding_id == plan.binding_id)
                .where(t.pm_inbound_canary_plans.c.status.in_(["PLANNED", "APPROVED", "ARMED"]))
                .where(t.pm_inbound_canary_plans.c.expires_at <= now)
                .values(status="EXPIRED", error="expired before command acceptance", updated_at=now)
            )
            await conn.execute(
                t.pm_inbound_canary_plans.update()
                .where(t.pm_inbound_canary_plans.c.binding_id == plan.binding_id)
                .where(t.pm_inbound_canary_plans.c.status.in_(["PLANNED", "APPROVED", "ARMED", "RUNNING"]))
                .values(status="ROLLED_BACK", error="superseded by a newer canary plan", updated_at=now)
            )
            await conn.execute(t.pm_inbound_canary_plans.insert().values(**values))
        return values

    async def get_pm_inbound_canary_plan(self, plan_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.pm_inbound_canary_plans, t.pm_inbound_canary_plans.c.id, plan_id)

    async def list_pm_inbound_canary_plans(
        self, *, connection_id: UUID | None = None, binding_id: UUID | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = t.pm_inbound_canary_plans.select().order_by(t.pm_inbound_canary_plans.c.created_at.desc()).limit(limit)
        if connection_id is not None:
            query = query.where(t.pm_inbound_canary_plans.c.connection_id == connection_id)
        if binding_id is not None:
            query = query.where(t.pm_inbound_canary_plans.c.binding_id == binding_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def approve_pm_inbound_canary_plan(self, plan_id: UUID, *, digest: str, actor: str) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            row = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).with_for_update())).mappings().first()
            if row is None:
                raise ValueError("canary plan not found")
            if str(row["digest"]) != digest:
                raise ValueError("canary plan digest mismatch")
            if row["expires_at"] <= now:
                raise ValueError("canary plan has expired")
            if str(row["status"]) != "PLANNED":
                raise ValueError(f"canary plan is not approvable from {row['status']}")
            await conn.execute(t.integration_evidence_records.insert().values(
                id=uuid4(), connection_id=row["connection_id"], binding_id=row["binding_id"], project_id=row["project_id"],
                evidence_type="pm_inbound_canary_approval", external_id=str(plan_id), repository=None,
                payload=self._pm_json_safe({"plan_id": str(plan_id), "digest": digest, "actor": actor, "occurred_at": now.isoformat()}),
                idempotency_key=f"pm-inbound-canary:{plan_id}:approval:{digest}", created_at=now,
            ))
            await conn.execute(t.pm_inbound_canary_plans.update().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).values(status="APPROVED", approved_by=actor, approved_at=now, updated_at=now))
            updated = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ))).mappings().one()
        return dict(updated)

    async def expire_pm_inbound_canary_plan(self, plan_id: UUID, *, digest: str, actor: str) -> dict[str, Any]:
        """Record expiry without altering immutable plan inputs or evidence."""
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            row = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).with_for_update())).mappings().first()
            if row is None:
                raise ValueError("canary plan not found")
            if str(row["digest"]) != digest:
                raise ValueError("canary plan digest mismatch")
            if str(row["status"]) == "EXPIRED":
                return dict(row)
            if row["expires_at"] > now:
                raise ValueError("canary plan has not expired")
            if str(row["status"]) not in {"PLANNED", "APPROVED", "ARMED"}:
                raise ValueError(f"canary plan cannot expire from {row['status']}")
            await conn.execute(t.pm_inbound_canary_plans.update().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).values(
                status="EXPIRED",
                expired_by=actor,
                expired_at=now,
                error="expired before command acceptance",
                updated_at=now,
            ))
            updated = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ))).mappings().one()
        return dict(updated)

    async def arm_pm_inbound_canary_plan(self, plan_id: UUID, *, digest: str, actor: str) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            row = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).with_for_update())).mappings().first()
            if row is None:
                raise ValueError("canary plan not found")
            if str(row["digest"]) != digest or str(row["status"]) != "APPROVED" or row["expires_at"] <= now:
                raise ValueError("canary plan is not an unexpired approved exact plan")
            await conn.execute(t.integration_evidence_records.insert().values(
                id=uuid4(), connection_id=row["connection_id"], binding_id=row["binding_id"], project_id=row["project_id"],
                evidence_type="pm_inbound_canary_arming", external_id=str(plan_id), repository=None,
                payload=self._pm_json_safe({"plan_id": str(plan_id), "digest": digest, "actor": actor, "occurred_at": now.isoformat()}),
                idempotency_key=f"pm-inbound-canary:{plan_id}:arming:{digest}", created_at=now,
            ))
            await conn.execute(t.pm_inbound_canary_plans.update().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).values(status="ARMED", armed_by=actor, armed_at=now, updated_at=now))
            updated = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ))).mappings().one()
        return dict(updated)

    async def get_armed_pm_inbound_canary_plan(self, binding_id: UUID) -> dict[str, Any] | None:
        now = datetime.now(tz=UTC)
        async with self.engine.connect() as conn:
            row = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.binding_id == binding_id
            ).where(t.pm_inbound_canary_plans.c.status.in_(["ARMED", "RUNNING"])).where(
                t.pm_inbound_canary_plans.c.expires_at > now
            ).order_by(t.pm_inbound_canary_plans.c.created_at.desc()).limit(1))).mappings().first()
        return dict(row) if row else None

    async def disarm_pm_inbound_canary_plan(self, plan_id: UUID, *, digest: str, actor: str, reason: str) -> dict[str, Any]:
        """Atomically fail-closed a canary and write its immutable evidence."""
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            row = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).with_for_update())).mappings().first()
            if row is None or str(row["digest"]) != digest:
                raise ValueError("canary plan not found or digest mismatch")
            if str(row["status"]) in {"SUCCEEDED", "ROLLED_BACK", "FAILED"}:
                return dict(row)
            evidence = {
                "id": uuid4(), "connection_id": row["connection_id"], "binding_id": row["binding_id"],
                "project_id": row["project_id"], "evidence_type": "pm_inbound_canary_disarm",
                "external_id": str(plan_id), "repository": None,
                "payload": self._pm_json_safe({"plan_id": str(plan_id), "digest": digest, "actor": actor, "reason": reason, "occurred_at": now.isoformat()}),
                "idempotency_key": f"pm-inbound-canary:{plan_id}:disarm:{digest}", "created_at": now,
            }
            await conn.execute(
                pg_insert(t.integration_evidence_records)
                .values(**evidence)
                .on_conflict_do_nothing(index_elements=[t.integration_evidence_records.c.idempotency_key])
            )
            if str(row["status"]) == "EXPIRED":
                return dict(row)
            await conn.execute(t.pm_inbound_canary_plans.update().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).values(status="FAILED", error=reason, completed_at=now, updated_at=now))
            updated = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ))).mappings().one()
        return dict(updated)

    async def claim_pm_inbound_canary_command(self, plan_id: UUID, *, inbox_id: UUID) -> dict[str, Any] | None:
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            row = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).with_for_update())).mappings().first()
            if row is None or str(row["status"]) != "ARMED" or row["expires_at"] <= now or int(row["accepted_command_count"]) >= int(row["max_command_count"]):
                return None
            result = {"inbox_id": str(inbox_id), "claimed_at": now.isoformat()}
            await conn.execute(t.pm_inbound_canary_plans.update().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).values(status="RUNNING", accepted_command_count=int(row["accepted_command_count"]) + 1, result=result, updated_at=now))
            updated = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ))).mappings().one()
        return dict(updated)

    async def complete_pm_inbound_canary_plan(self, plan_id: UUID, *, success: bool, result: dict[str, Any]) -> dict[str, Any] | None:
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            row = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).with_for_update())).mappings().first()
            if row is None:
                return None
            if str(row["status"]) not in {"RUNNING", "ARMED"}:
                return dict(row)
            await conn.execute(t.pm_inbound_canary_plans.update().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).values(status="SUCCEEDED" if success else "FAILED", completed_at=now,
                     result=self._pm_json_safe(result), error=None if success else str(result.get("error") or "canary command failed")[:1000], updated_at=now))
            updated = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ))).mappings().one()
        return dict(updated)

    async def apply_pm_inbound_canary_priority(
        self, *, plan_id: UUID, issue_id: UUID, expected_revision: int, target_priority: str,
        connection_id: UUID, inbox_id: UUID, command_key: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Atomically apply the single canary command, evidence, outbox, and disarm."""
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            plan = (await conn.execute(t.pm_inbound_canary_plans.select().where(
                t.pm_inbound_canary_plans.c.id == plan_id
            ).with_for_update())).mappings().first()
            if plan is None or str(plan["status"]) != "ARMED" or int(plan["accepted_command_count"]) >= int(plan["max_command_count"]) or plan["expires_at"] <= now:
                raise ValueError("canary is not armed or has already accepted a command")
            issue = (await conn.execute(t.issues.select().where(t.issues.c.id == issue_id).with_for_update())).mappings().first()
            if issue is None or int(issue.get("revision") or 1) != expected_revision or str(issue.get("priority") or "") == target_priority:
                raise ValueError("canary issue revision or target priority is stale")
            evidence = {
                "id": uuid4(), "connection_id": connection_id, "binding_id": plan["binding_id"], "project_id": plan["project_id"],
                "evidence_type": "pm_inbound_canary_command", "external_id": str(issue_id), "repository": None,
                "payload": self._pm_json_safe({"plan_id": str(plan_id), "inbox_id": str(inbox_id), "command_key": command_key, "from": issue.get("priority"), "to": target_priority}),
                "idempotency_key": f"pm-inbound-canary:command:{command_key}", "created_at": now,
            }
            await conn.execute(t.integration_evidence_records.insert().values(**evidence))
            await conn.execute(t.issues.update().where(t.issues.c.id == issue_id).values(
                priority=target_priority, revision=t.issues.c.revision + 1, updated_at=now
            ))
            refreshed = (await conn.execute(t.issues.select().where(t.issues.c.id == issue_id))).mappings().one()
            queued = await self._enqueue_issue_projections_tx(conn, dict(refreshed), exclude_connection_id=connection_id)
            await conn.execute(t.pm_inbound_canary_plans.update().where(t.pm_inbound_canary_plans.c.id == plan_id).values(
                status="SUCCEEDED", accepted_command_count=1, completed_at=now,
                result=self._pm_json_safe({"inbox_id": str(inbox_id), "canonical_revision": refreshed["revision"], "outbox_count": len(queued)}), updated_at=now
            ))
        return dict(refreshed), queued

    async def create_pm_binding(
        self,
        *,
        project_id: UUID,
        connection_id: UUID,
        external_project_id: str | None = None,
        external_project_key: str | None = None,
        external_repository: str | None = None,
        mapping_profile: str = "default",
        direction: str = "outbound",
        status: str = "DISABLED",
        binding_id: UUID | None = None,
        provisioning_state: str = "UNPROVISIONED",
        provisioning_plan_id: UUID | None = None,
        provisioning_plan_digest: str | None = None,
        activation_blockers: list[str] | None = None,
    ) -> dict[str, Any]:
        from mas_core.integrations.contracts import normalize_project_mapping_profile

        mapping_profile = normalize_project_mapping_profile(mapping_profile)
        if mapping_profile == "dedicated_project" and not external_project_id:
            raise ValueError("dedicated_project bindings require an explicit external project selector")
        if mapping_profile == "umbrella_issues" and not (external_project_id or external_repository):
            raise ValueError("umbrella_issues bindings require an explicit provider project or repository selector")
        now = datetime.now(tz=UTC)
        values = {
            "id": binding_id or uuid4(),
            "project_id": project_id,
            "connection_id": connection_id,
            "external_project_id": external_project_id,
            "external_project_key": external_project_key,
            "external_repository": external_repository,
            "mapping_profile": mapping_profile,
            "direction": direction,
            "status": status,
            "revision": 1,
            "provisioning_state": provisioning_state,
            "provisioning_plan_id": provisioning_plan_id,
            "provisioning_plan_digest": provisioning_plan_digest,
            "activation_blockers": list(activation_blockers or []),
            "webhook_events": [],
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            connection = await self._mapping_first(
                await conn.execute(
                    t.pm_connections.select().where(t.pm_connections.c.id == connection_id)
                )
            )
            if connection is None:
                raise ValueError("integration connection not found")
            if status == "ACTIVE":
                self._assert_pm_binding_activation_ready(values, connection)
            if direction not in {"outbound", "inbound", "both"}:
                raise ValueError("invalid PM binding direction")
            if status not in {"DISABLED", "SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"}:
                raise ValueError("invalid PM binding status")
            if mapping_profile == "dedicated_project" and external_project_id:
                duplicate = await self._mapping_first(
                    await conn.execute(
                        t.pm_project_bindings.select()
                        .where(t.pm_project_bindings.c.connection_id == connection_id)
                        .where(t.pm_project_bindings.c.external_project_id == external_project_id)
                        .where(t.pm_project_bindings.c.mapping_profile.in_(["default", "dedicated_project"]))
                    )
                )
                if duplicate is not None:
                    raise ValueError("a dedicated provider project is already bound to another canonical project")
            if direction in {"inbound", "both"} and status == "ACTIVE":
                await conn.execute(
                    t.pm_project_bindings.update()
                    .where(t.pm_project_bindings.c.project_id == project_id)
                    .where(t.pm_project_bindings.c.direction.in_(["inbound", "both"]))
                    .where(t.pm_project_bindings.c.status == "ACTIVE")
                    .values(
                        status="DRAINING",
                        revision=t.pm_project_bindings.c.revision + 1,
                        updated_at=now,
                    )
                )
            await conn.execute(t.pm_project_bindings.insert().values(**values))
            # A newly-created outbound binding must be immediately usable for
            # existing canonical work. Queue its current project, iterations,
            # issues, and comments in the same transaction so a one-shot
            # provider setup cannot silently omit pre-existing records.
            await self._enqueue_binding_backfill_tx(conn, values)
        return values

    async def list_pm_bindings(
        self, *, project_id: UUID | None = None, connection_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        query = t.pm_project_bindings.select().order_by(t.pm_project_bindings.c.created_at)
        if project_id is not None:
            query = query.where(t.pm_project_bindings.c.project_id == project_id)
        if connection_id is not None:
            query = query.where(t.pm_project_bindings.c.connection_id == connection_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def update_pm_binding(self, binding_id: UUID, **kwargs: Any) -> dict[str, Any] | None:
        from mas_core.integrations.contracts import normalize_project_mapping_profile

        requested_keys = set(kwargs)
        allowed = {
            "external_project_id", "external_project_key", "external_repository", "mapping_profile", "direction",
            "sync_cursor", "status", "last_reconciled_at", "provisioning_state", "provisioning_plan_id",
            "provisioning_plan_digest", "activation_blockers", "webhook_verified_at", "projection_verified_at",
            "reconciliation_verified_at", "webhook_events",
        }
        values = {key: value for key, value in kwargs.items() if key in allowed}
        values["updated_at"] = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            current = await self._mapping_first(
                await conn.execute(
                    t.pm_project_bindings.select().where(t.pm_project_bindings.c.id == binding_id).with_for_update()
                )
            )
            if current is None:
                return None
            connection = await self._mapping_first(
                await conn.execute(
                    t.pm_connections.select().where(t.pm_connections.c.id == current["connection_id"])
                )
            )
            next_direction = str(values.get("direction", current["direction"]))
            next_status = str(values.get("status", current["status"]))
            next_profile = normalize_project_mapping_profile(values.get("mapping_profile", current.get("mapping_profile")))
            values["mapping_profile"] = next_profile
            next_selector = values.get("external_project_id", current.get("external_project_id"))
            next_repository = values.get("external_repository", current.get("external_repository"))
            if next_profile == "dedicated_project" and not next_selector:
                raise ValueError("dedicated_project bindings require an explicit external project selector")
            if next_profile == "umbrella_issues" and not (next_selector or next_repository):
                raise ValueError("umbrella_issues bindings require an explicit provider project or repository selector")
            if next_direction not in {"outbound", "inbound", "both"}:
                raise ValueError("invalid PM binding direction")
            if next_status not in {"DISABLED", "SHADOW", "READ_ONLY", "ACTIVE", "DRAINING"}:
                raise ValueError("invalid PM binding status")
            next_external_project_id = values.get("external_project_id", current.get("external_project_id"))
            if next_profile == "dedicated_project" and next_external_project_id:
                duplicate = await self._mapping_first(
                    await conn.execute(
                        t.pm_project_bindings.select()
                        .where(t.pm_project_bindings.c.connection_id == current["connection_id"])
                        .where(t.pm_project_bindings.c.external_project_id == next_external_project_id)
                        .where(t.pm_project_bindings.c.mapping_profile.in_(["default", "dedicated_project"]))
                        .where(t.pm_project_bindings.c.id != binding_id)
                    )
                )
                if duplicate is not None:
                    raise ValueError("a dedicated provider project is already bound to another canonical project")
            if next_status == "ACTIVE":
                candidate = {**current, **values}
                self._assert_pm_binding_activation_ready(candidate, connection)
            if next_status == "ACTIVE" and next_direction in {"inbound", "both"}:
                await conn.execute(
                    t.pm_project_bindings.update()
                    .where(t.pm_project_bindings.c.project_id == current["project_id"])
                    .where(t.pm_project_bindings.c.id != binding_id)
                    .where(t.pm_project_bindings.c.direction.in_(["inbound", "both"]))
                    .where(t.pm_project_bindings.c.status == "ACTIVE")
                    .values(
                        status="DRAINING",
                        revision=t.pm_project_bindings.c.revision + 1,
                        updated_at=values["updated_at"],
                    )
                )
            # Cursor/evidence timestamps are operational synchronization
            # metadata, not lifecycle identity.  They must not make an
            # already-reviewed transition plan stale merely because a gate
            # reconciliation ran.  Lifecycle-relevant selector, direction,
            # provisioning, blocker, or status changes still advance the CAS
            # revision.
            revision_fields = {
                "external_project_id",
                "external_project_key",
                "external_repository",
                "mapping_profile",
                "direction",
                "status",
                "provisioning_state",
                "provisioning_plan_id",
                "provisioning_plan_digest",
                "activation_blockers",
            }
            if requested_keys.intersection(revision_fields):
                values["revision"] = t.pm_project_bindings.c.revision + 1
            result = await conn.execute(
                t.pm_project_bindings.update().where(t.pm_project_bindings.c.id == binding_id).values(**values)
            )
            if result.rowcount == 0:
                return None
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(t.pm_project_bindings.select().where(t.pm_project_bindings.c.id == binding_id))
            ).mappings().first()
        return dict(row) if row else None

    @staticmethod
    def _assert_pm_binding_activation_ready(
        binding: Mapping[str, Any],
        connection: Mapping[str, Any] | None,
    ) -> None:
        """Fail closed until provider setup and all three runtime gates pass."""
        if connection is None or connection.get("status") != "ACTIVE":
            raise ValueError("an active binding requires an active integration connection")
        mapping_profile = str(binding.get("mapping_profile") or "default").strip().lower()
        if mapping_profile in {"umbrella_issues", "single_project_issues"}:
            if not (binding.get("external_project_id") or binding.get("external_repository")):
                raise ValueError("an active umbrella_issues binding requires an explicit provider project or repository selector")
        elif not binding.get("external_project_id"):
            raise ValueError("an active binding requires an explicit provider project selector")
        blockers = [str(item) for item in (binding.get("activation_blockers") or []) if item]
        if blockers:
            raise ValueError("binding activation is blocked: " + "; ".join(blockers))
        events = {str(item).lower() for item in (binding.get("webhook_events") or [])}
        missing_events = sorted({"issue", "comment"} - events)
        if not binding.get("webhook_verified_at") or missing_events:
            suffix = f" (missing events: {', '.join(missing_events)})" if missing_events else ""
            raise ValueError("binding activation requires authenticated issue/comment webhook evidence" + suffix)
        if not binding.get("projection_verified_at"):
            raise ValueError("binding activation requires a successful projection evidence")
        if not binding.get("reconciliation_verified_at"):
            raise ValueError("binding activation requires a successful reconciliation evidence")

    async def record_pm_binding_evidence(
        self,
        binding_id: UUID,
        *,
        webhook_event: str | None = None,
        webhook_verified: bool = False,
        projection_verified: bool = False,
        reconciliation_verified: bool = False,
        activation_blockers: list[str] | None = None,
        provisioning_state: str | None = None,
    ) -> dict[str, Any] | None:
        """Record monotonic activation evidence without granting activation."""
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            current = (
                await conn.execute(
                    t.pm_project_bindings.select().where(t.pm_project_bindings.c.id == binding_id).with_for_update()
                )
            ).mappings().first()
            if current is None:
                return None
            events = {str(item).lower() for item in (current.get("webhook_events") or []) if item}
            if webhook_event:
                events.add(str(webhook_event).lower())
            values: dict[str, Any] = {"webhook_events": sorted(events), "updated_at": now}
            if webhook_verified:
                values["webhook_verified_at"] = current.get("webhook_verified_at") or now
                if {"issue", "comment"}.issubset(events) and activation_blockers is None:
                    # The persisted manual-action blocker is cleared only by
                    # two authenticated, in-scope event classes.  A caller
                    # cannot clear it by submitting metadata alone.
                    values["activation_blockers"] = []
                    if str(current.get("provisioning_state") or "") == "WAITING_MANUAL_WEBHOOK":
                        values["provisioning_state"] = "WEBHOOK_VERIFIED"
            if projection_verified:
                values["projection_verified_at"] = current.get("projection_verified_at") or now
                if str(current.get("provisioning_state") or "") in {"WEBHOOK_VERIFIED", "PROVISIONED"}:
                    values["provisioning_state"] = "PROJECTED"
            if reconciliation_verified:
                values["reconciliation_verified_at"] = current.get("reconciliation_verified_at") or now
                if str(current.get("provisioning_state") or "") in {"PROJECTED", "WEBHOOK_VERIFIED", "PROVISIONED"}:
                    values["provisioning_state"] = "VERIFIED"
            if activation_blockers is not None:
                values["activation_blockers"] = list(activation_blockers)
            if provisioning_state is not None:
                values["provisioning_state"] = provisioning_state
            await conn.execute(
                t.pm_project_bindings.update()
                .where(t.pm_project_bindings.c.id == binding_id)
                .values(**values)
            )
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(t.pm_project_bindings.select().where(t.pm_project_bindings.c.id == binding_id))
            ).mappings().first()
        return dict(row) if row else None

    async def upsert_pm_mapping(
        self,
        *,
        connection_id: UUID,
        object_type: str,
        aiat_object_id: UUID,
        external_id: str,
        external_key: str | None = None,
        provider_version: str | None = None,
        content_hash: str | None = None,
        imported_revision: int | None = None,
        exported_revision: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        values = {
            "id": uuid4(),
            "connection_id": connection_id,
            "object_type": object_type,
            "aiat_object_id": aiat_object_id,
            "external_id": external_id,
            "external_key": external_key,
            "provider_version": provider_version,
            "content_hash": content_hash,
            "last_import_revision": imported_revision,
            "last_export_revision": exported_revision,
            "last_imported_at": now if imported_revision is not None else None,
            "last_exported_at": now if exported_revision is not None else None,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            existing_external = await self._mapping_first(
                await conn.execute(
                    t.pm_object_mappings.select()
                    .where(t.pm_object_mappings.c.connection_id == connection_id)
                    .where(t.pm_object_mappings.c.object_type == object_type)
                    .where(t.pm_object_mappings.c.external_id == external_id)
                    .where(t.pm_object_mappings.c.aiat_object_id != aiat_object_id)
                )
            )
            if existing_external is not None:
                raise ValueError(
                    f"PM mapping conflict: external object {external_id!r} is already mapped"
                )
            # Do not overwrite metadata owned by the opposite synchronization
            # direction with ``None``.  An outbound projection normally only
            # knows export state; an inbound event normally only knows import
            # state.  Preserve the other side's revision/hash/version history.
            update_values: dict[str, Any] = {"updated_at": now}
            if external_key is not None:
                update_values["external_key"] = external_key
            if provider_version is not None:
                update_values["provider_version"] = provider_version
            if content_hash is not None:
                update_values["content_hash"] = content_hash
            if imported_revision is not None:
                update_values["last_import_revision"] = imported_revision
                update_values["last_imported_at"] = now
            if exported_revision is not None:
                update_values["last_export_revision"] = exported_revision
                update_values["last_exported_at"] = now
            if metadata is not None:
                update_values["metadata"] = metadata
            stmt = pg_insert(t.pm_object_mappings).values(**values).on_conflict_do_update(
                constraint="uq_pm_mapping_aiat",
                set_=update_values,
            ).returning(t.pm_object_mappings)
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else values

    async def create_pm_inbox_event(
        self,
        *,
        connection_id: UUID,
        provider_delivery_id: str,
        event_type: str,
        payload: dict[str, Any],
        verified: bool,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
        payload_hash: str | None = None,
        normalized_type: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if raw_body is not None and len(raw_body) > _PM_RAW_BODY_MAX_BYTES:
            raise ValueError("provider webhook body exceeds 1 MiB retention limit")
        computed_hash = hashlib.sha256(
            raw_body
            if raw_body is not None
            else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        values = {
            "id": uuid4(),
            "connection_id": connection_id,
            "provider_delivery_id": provider_delivery_id,
            "event_type": event_type,
            "payload": payload,
            "raw_body": raw_body,
            "headers": self._pm_json_safe(headers or {}),
            "payload_hash": payload_hash or computed_hash,
            "verified": verified,
            "normalized_type": normalized_type,
            "result": self._pm_json_safe(result) if result is not None else None,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "received_at": datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            stmt = (
                pg_insert(t.pm_inbox_events)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_pm_inbox_delivery")
                .returning(t.pm_inbox_events)
            )
            row = (await conn.execute(stmt)).mappings().first()
        if row:
            return dict(row), True
        async with self.engine.connect() as conn:
            existing = (
                await conn.execute(
                    t.pm_inbox_events.select()
                    .where(t.pm_inbox_events.c.connection_id == connection_id)
                    .where(t.pm_inbox_events.c.provider_delivery_id == provider_delivery_id)
                )
            ).mappings().first()
        return (dict(existing) if existing else values), False

    async def enqueue_pm_outbox(
        self,
        *,
        connection_id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        canonical_revision: int,
        operation: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        values = {
            "id": uuid4(),
            "connection_id": connection_id,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "canonical_revision": canonical_revision,
            "operation": operation,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "created_at": datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            stmt = (
                pg_insert(t.pm_outbox_events)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[t.pm_outbox_events.c.idempotency_key])
                .returning(t.pm_outbox_events)
            )
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else values

    async def list_pm_outbox(
        self, *, connection_id: UUID | None = None, status: str = "PENDING", limit: int = 100
    ) -> list[dict[str, Any]]:
        if status == "PENDING":
            await self.recover_stale_pm_outbox()
        query = (
            t.pm_outbox_events.select()
            .where(t.pm_outbox_events.c.status == status)
            .where(
                sa.or_(
                    t.pm_outbox_events.c.next_attempt_at.is_(None),
                    t.pm_outbox_events.c.next_attempt_at <= datetime.now(tz=UTC),
                )
            )
            .order_by(t.pm_outbox_events.c.created_at)
            .limit(limit)
        )
        if connection_id is not None:
            query = query.where(t.pm_outbox_events.c.connection_id == connection_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_pm_outbox_dispositions(
        self, *, connection_id: UUID | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        query = t.pm_outbox_dispositions.select().order_by(t.pm_outbox_dispositions.c.created_at.desc()).limit(limit)
        if connection_id is not None:
            query = query.where(t.pm_outbox_dispositions.c.connection_id == connection_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_pm_outbox_dead_letter_counts(
        self, *, connection_id: UUID
    ) -> dict[str, int]:
        """Return exhaustive active and historical dead-letter counts.

        The lifecycle gate must not infer safety from capped API pages.  This
        anti-join counts the entire connection-scoped terminal set in the
        database, where a disposition is guaranteed to be unique per outbox
        event.
        """
        outbox = t.pm_outbox_events
        dispositions = t.pm_outbox_dispositions
        statement = (
            sa.select(
                sa.func.count(outbox.c.id).label("total"),
                sa.func.count(outbox.c.id)
                .filter(dispositions.c.outbox_id.is_(None))
                .label("active"),
            )
            .select_from(
                outbox.outerjoin(
                    dispositions,
                    dispositions.c.outbox_id == outbox.c.id,
                )
            )
            .where(
                outbox.c.connection_id == connection_id,
                outbox.c.status == "DEAD_LETTER",
            )
        )
        async with self.engine.connect() as conn:
            row = (await conn.execute(statement)).mappings().one()
        total = int(row.get("total") or 0)
        active = int(row.get("active") or 0)
        return {"active": active, "historical": total - active, "total": total}

    async def dispose_pm_outbox_dead_letter(
        self,
        outbox_id: UUID,
        *,
        disposition: str,
        reason: str,
        provider_state: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        """Persist a governed, immutable disposition without rewriting forensics."""
        if disposition not in {"RESOLVED", "SUPERSEDED"}:
            raise ValueError("invalid PM outbox disposition")
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            outbox = (await conn.execute(
                t.pm_outbox_events.select().where(t.pm_outbox_events.c.id == outbox_id).with_for_update()
            )).mappings().first()
            if outbox is None:
                raise ValueError("PM outbox event not found")
            if str(outbox["status"]) != "DEAD_LETTER":
                raise ValueError("only terminal DEAD_LETTER events may be dispositioned")
            existing = (await conn.execute(
                t.pm_outbox_dispositions.select().where(t.pm_outbox_dispositions.c.outbox_id == outbox_id)
            )).mappings().first()
            if existing is not None:
                return {"disposition": dict(existing), "evidence_id": str(existing["evidence_id"])}
            payload = outbox.get("payload") or {}
            binding_id = payload.get("binding_id")
            item = payload.get("item") or {}
            project_id = item.get("project_id")
            evidence = {
                "id": uuid4(),
                "connection_id": outbox["connection_id"],
                "binding_id": binding_id,
                "project_id": project_id,
                "evidence_type": "pm_outbox_disposition",
                "external_id": str(outbox_id),
                "repository": None,
                "payload": self._pm_json_safe({
                    "outbox_id": str(outbox_id), "disposition": disposition,
                    "reason": reason, "actor": actor, "provider_state": provider_state,
                    "occurred_at": now.isoformat(),
                }),
                "idempotency_key": f"pm-outbox-disposition:{outbox_id}:{disposition}",
                "created_at": now,
            }
            await conn.execute(t.integration_evidence_records.insert().values(**evidence))
            row = {
                "id": uuid4(), "outbox_id": outbox_id, "connection_id": outbox["connection_id"],
                "binding_id": binding_id, "disposition": disposition, "reason": reason,
                "actor": actor, "provider_state": self._pm_json_safe(provider_state),
                "evidence_id": evidence["id"], "created_at": now,
            }
            await conn.execute(t.pm_outbox_dispositions.insert().values(**row))
        return {"disposition": row, "evidence_id": str(evidence["id"])}

    async def recover_stale_pm_outbox(self, *, lease_seconds: int = 300) -> int:
        """Return abandoned PROCESSING deliveries to the retry queue.

        A gateway crash after claiming an event must not strand it forever.
        The lease is deliberately bounded and the provider call remains
        idempotency-keyed, so replay after an uncertain crash window is safe.
        """
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=max(30, lease_seconds))
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.pm_outbox_events.update()
                .where(t.pm_outbox_events.c.status == "PROCESSING")
                .where(t.pm_outbox_events.c.claimed_at <= cutoff)
                .values(status="PENDING", claimed_at=None, next_attempt_at=datetime.now(tz=UTC))
            )
        return int(result.rowcount or 0)

    async def claim_pm_outbox(self, outbox_id: UUID) -> dict[str, Any] | None:
        """Claim one pending delivery so two drainers cannot send it twice."""
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    t.pm_outbox_events.select()
                    .where(t.pm_outbox_events.c.id == outbox_id)
                    .where(t.pm_outbox_events.c.status == "PENDING")
                    .with_for_update()
                )
            ).mappings().first()
            if row is None:
                return None
            now = datetime.now(tz=UTC)
            await conn.execute(
                t.pm_outbox_events.update()
                .where(t.pm_outbox_events.c.id == outbox_id)
                .values(status="PROCESSING", claimed_at=now)
            )
            claimed = dict(row)
            claimed["status"] = "PROCESSING"
            claimed["claimed_at"] = now
            return claimed

    async def record_pm_delivery_attempt(
        self,
        outbox_id: UUID,
        *,
        status: str,
        provider_status: int | None = None,
        response_metadata: dict[str, Any] | None = None,
        error: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> dict[str, Any] | None:
        """Append an attempt ledger row and schedule bounded exponential retry."""
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    t.pm_outbox_events.select().where(t.pm_outbox_events.c.id == outbox_id).with_for_update()
                )
            ).mappings().first()
            if row is None:
                return None
            attempt = int(row.get("attempts") or 0) + 1
            delay = (
                retry_after_seconds
                if retry_after_seconds is not None
                else max(1, int(min(3600, (2 ** min(attempt, 10)) * random.uniform(0.8, 1.2))))
            )
            await conn.execute(
                t.pm_delivery_attempts.insert().values(
                    outbox_id=outbox_id,
                    attempt=attempt,
                    status=status,
                    provider_status=provider_status,
                    response_metadata=response_metadata or {},
                    error=error,
                    attempted_at=now,
                )
            )
            await conn.execute(
                t.pm_outbox_events.update()
                .where(t.pm_outbox_events.c.id == outbox_id)
                .values(
                    attempts=attempt,
                    next_attempt_at=now + timedelta(seconds=delay),
                    last_error=error,
                    status="PENDING" if status in {"FAILED", "RETRYING"} else row["status"],
                    claimed_at=None if status in {"FAILED", "RETRYING"} else row.get("claimed_at"),
                )
            )
        # The caller owns the terminal transition (SYNCED, PENDING, or
        # DEAD_LETTER).  Returning the updated row here must not accidentally
        # re-claim a failed delivery as PROCESSING.
        async with self.engine.connect() as conn:
            updated = (
                await conn.execute(
                    t.pm_outbox_events.select().where(t.pm_outbox_events.c.id == outbox_id)
                )
            ).mappings().first()
        return dict(updated) if updated else None

    async def mark_pm_outbox(
        self, outbox_id: UUID, *, status: str, error: str | None = None
    ) -> dict[str, Any] | None:
        values: dict[str, Any] = {"status": status, "last_error": error}
        if status in {"SYNCED", "FAILED", "DEAD_LETTER"}:
            values["processed_at"] = datetime.now(tz=UTC)
        if status != "PROCESSING":
            values["claimed_at"] = None
        async with self.engine.begin() as conn:
            await conn.execute(
                t.pm_outbox_events.update().where(t.pm_outbox_events.c.id == outbox_id).values(**values)
            )
        async with self.engine.connect() as conn:
            row = (await conn.execute(t.pm_outbox_events.select().where(t.pm_outbox_events.c.id == outbox_id))).mappings().first()
        return dict(row) if row else None

    async def create_pm_conflict(
        self,
        *,
        connection_id: UUID,
        reason: str,
        object_type: str,
        aiat_object_id: UUID | None = None,
        external_id: str | None = None,
        binding_id: UUID | None = None,
        canonical_snapshot: dict[str, Any] | None = None,
        external_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = {
            "id": uuid4(),
            "connection_id": connection_id,
            "binding_id": binding_id,
            "object_type": object_type,
            "aiat_object_id": aiat_object_id,
            "external_id": external_id,
            "reason": reason,
            # JSONB cannot encode UUID/datetime objects that come directly
            # from canonical storage rows or normalized provider DTOs.  Keep
            # conflict recording a durable failure boundary instead of
            # turning an otherwise valid webhook into a 500 response.
            "canonical_snapshot": self._pm_json_safe(canonical_snapshot) if canonical_snapshot is not None else None,
            "external_snapshot": self._pm_json_safe(external_snapshot) if external_snapshot is not None else None,
            "created_at": datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.pm_conflicts.insert().values(**values))
        return values

    async def list_pm_conflicts(
        self, *, connection_id: UUID | None = None, status: str | None = "OPEN", limit: int = 100
    ) -> list[dict[str, Any]]:
        query = t.pm_conflicts.select().order_by(t.pm_conflicts.c.created_at.desc()).limit(limit)
        if status is not None:
            query = query.where(t.pm_conflicts.c.status == status)
        if connection_id is not None:
            query = query.where(t.pm_conflicts.c.connection_id == connection_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def resolve_pm_conflict(
        self,
        conflict_id: UUID,
        *,
        resolution: dict[str, Any],
        status: str = "RESOLVED",
    ) -> dict[str, Any] | None:
        """Record an operator decision without deleting forensic snapshots."""
        if status not in {"RESOLVED", "IGNORED", "REOPENED"}:
            raise ValueError("invalid PM conflict resolution status")
        values: dict[str, Any] = {"status": status, "resolution": resolution}
        values["resolved_at"] = None if status == "REOPENED" else datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.pm_conflicts.update()
                .where(t.pm_conflicts.c.id == conflict_id)
                .values(**values)
            )
            if result.rowcount == 0:
                return None
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(t.pm_conflicts.select().where(t.pm_conflicts.c.id == conflict_id))
            ).mappings().first()
        return dict(row) if row else None

    async def cutover_pm_binding(self, project_id: UUID, binding_id: UUID) -> dict[str, Any] | None:
        """Atomically promote one inbound binding and drain its predecessor."""
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            target = (
                await conn.execute(
                    t.pm_project_bindings.select()
                    .where(t.pm_project_bindings.c.id == binding_id)
                    .where(t.pm_project_bindings.c.project_id == project_id)
                    .with_for_update()
                )
            ).mappings().first()
            if target is None:
                return None
            if target["direction"] not in {"inbound", "both"}:
                raise ValueError("only inbound or bidirectional bindings can be cut over")
            target_connection = (
                await conn.execute(
                    t.pm_connections.select().where(t.pm_connections.c.id == target["connection_id"])
                )
            ).mappings().first()
            # Cutover promotes the target connection in the same transaction;
            # validate all binding evidence against that intended ACTIVE
            # state before committing either side of the transition.
            gate_connection = dict(target_connection or {})
            if gate_connection.get("status") in {"SHADOW", "READ_ONLY"}:
                gate_connection["status"] = "ACTIVE"
            self._assert_pm_binding_activation_ready(target, gate_connection)
            prior_bindings = (
                await conn.execute(
                    t.pm_project_bindings.select()
                    .where(t.pm_project_bindings.c.project_id == project_id)
                    .where(t.pm_project_bindings.c.direction.in_(["inbound", "both"]))
                    .where(t.pm_project_bindings.c.status == "ACTIVE")
                    .where(t.pm_project_bindings.c.id != binding_id)
                )
            ).mappings().all()
            await conn.execute(
                t.pm_project_bindings.update()
                .where(t.pm_project_bindings.c.project_id == project_id)
                .where(t.pm_project_bindings.c.direction.in_(["inbound", "both"]))
                .where(t.pm_project_bindings.c.status == "ACTIVE")
                .where(t.pm_project_bindings.c.id != binding_id)
                .values(
                    status="DRAINING",
                    revision=t.pm_project_bindings.c.revision + 1,
                    updated_at=now,
                )
            )
            await conn.execute(
                t.pm_project_bindings.update()
                .where(t.pm_project_bindings.c.id == binding_id)
                .values(
                    status="ACTIVE",
                    revision=t.pm_project_bindings.c.revision + 1,
                    updated_at=now,
                )
            )
            await conn.execute(
                t.pm_connections.update()
                .where(t.pm_connections.c.id == target["connection_id"])
                .values(
                    status="ACTIVE",
                    revision=t.pm_connections.c.revision + 1,
                    updated_at=now,
                )
            )
            prior_connection_ids = [row["connection_id"] for row in prior_bindings if row["connection_id"] != target["connection_id"]]
            if prior_connection_ids:
                await conn.execute(
                    t.pm_connections.update()
                    .where(t.pm_connections.c.id.in_(prior_connection_ids))
                    .values(
                        status="DRAINING",
                        revision=t.pm_connections.c.revision + 1,
                        updated_at=now,
                    )
                )
        return await self.get_pm_connection(target["connection_id"])

    async def create_pm_reconciliation_run(
        self,
        *,
        connection_id: UUID,
        binding_id: UUID | None = None,
        mode: str = "audit",
        cursor: str | None = None,
    ) -> dict[str, Any]:
        values = {
            "id": uuid4(),
            "connection_id": connection_id,
            "binding_id": binding_id,
            "mode": mode,
            "status": "RUNNING",
            "cursor": cursor,
            "counts": {},
            "started_at": datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.pm_reconciliation_runs.insert().values(**values))
        return values

    async def finish_pm_reconciliation_run(
        self,
        run_id: UUID,
        *,
        status: str,
        counts: dict[str, Any],
        next_cursor: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        values = {
            "status": status,
            "counts": counts,
            "next_cursor": next_cursor,
            "error": error,
            "completed_at": datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.pm_reconciliation_runs.update().where(t.pm_reconciliation_runs.c.id == run_id).values(**values)
            )
            if result.rowcount == 0:
                return None
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(t.pm_reconciliation_runs.select().where(t.pm_reconciliation_runs.c.id == run_id))
            ).mappings().first()
        return dict(row) if row else None

    async def list_pm_reconciliation_runs(
        self,
        *,
        connection_id: UUID | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = t.pm_reconciliation_runs.select().order_by(t.pm_reconciliation_runs.c.started_at.desc()).limit(limit)
        if connection_id is not None:
            query = query.where(t.pm_reconciliation_runs.c.connection_id == connection_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def create_pm_cutover(
        self,
        *,
        project_id: UUID,
        from_binding_id: UUID | None,
        to_binding_id: UUID,
        confirmation: dict[str, Any],
    ) -> dict[str, Any]:
        values = {
            "id": uuid4(),
            "project_id": project_id,
            "from_binding_id": from_binding_id,
            "to_binding_id": to_binding_id,
            "status": "RUNNING",
            "confirmation": confirmation,
            "rollback_ready": True,
            "created_at": datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.pm_cutovers.insert().values(**values))
        return values

    async def finish_pm_cutover(
        self,
        cutover_id: UUID,
        *,
        status: str,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        values = {"status": status, "error": error, "completed_at": datetime.now(tz=UTC)}
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.pm_cutovers.update().where(t.pm_cutovers.c.id == cutover_id).values(**values)
            )
            if result.rowcount == 0:
                return None
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(t.pm_cutovers.select().where(t.pm_cutovers.c.id == cutover_id))
            ).mappings().first()
        return dict(row) if row else None

    async def list_pm_cutovers(self, *, project_id: UUID | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = t.pm_cutovers.select().order_by(t.pm_cutovers.c.created_at.desc()).limit(limit)
        if project_id is not None:
            query = query.where(t.pm_cutovers.c.project_id == project_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def create_pm_lifecycle_plan(
        self,
        plan: Any,
        *,
        digest: str,
    ) -> dict[str, Any]:
        """Persist one immutable lifecycle plan and supersede older previews."""
        from mas_core.integrations.contracts import LifecyclePlanError

        if plan.digest() != digest:
            raise LifecyclePlanError("digest_mismatch", "lifecycle plan digest does not match its canonical payload")
        values = {
            "id": plan.plan_id,
            "plan_kind": plan.plan_kind,
            "schema_version": plan.schema_version,
            "target_type": plan.target_type,
            "target_id": plan.target_id,
            "connection_id": plan.connection_id,
            "binding_id": plan.binding_id,
            "expected_connection_status": plan.expected_connection_status,
            "expected_binding_status": plan.expected_binding_status,
            "expected_connection_revision": plan.expected_connection_revision,
            "expected_binding_revision": plan.expected_binding_revision,
            "desired_connection_status": plan.desired_connection_status,
            "desired_binding_status": plan.desired_binding_status,
            "observed_versions": self._pm_json_safe(plan.observed_versions),
            "operations": self._pm_json_safe(plan.operations),
            "gate_results": self._pm_json_safe(plan.gate_results),
            "evidence_refs": self._pm_json_safe(plan.evidence_refs),
            "blockers": self._pm_json_safe(plan.blockers),
            "rollback_operations": self._pm_json_safe(plan.rollback_operations),
            "created_by": plan.created_by,
            "created_at": plan.created_at,
            "expires_at": plan.expires_at,
            "digest": digest,
            "status": "PLANNED",
            "updated_at": plan.created_at,
        }
        async with self.engine.begin() as conn:
            await conn.execute(
                t.pm_lifecycle_plans.update()
                .where(t.pm_lifecycle_plans.c.target_type == plan.target_type)
                .where(t.pm_lifecycle_plans.c.target_id == plan.target_id)
                .where(t.pm_lifecycle_plans.c.status.in_(["PLANNED", "APPROVED"]))
                .values(status="SUPERSEDED", error="superseded by a newer lifecycle plan", updated_at=plan.created_at)
            )
            try:
                await conn.execute(t.pm_lifecycle_plans.insert().values(**values))
            except Exception as exc:
                # Do not expose database details or accidentally return a
                # second plan when a UUID/digest was already persisted.
                raise LifecyclePlanError("persistence_failed", "could not persist lifecycle plan") from exc
        return values

    async def get_pm_lifecycle_plan(self, plan_id: UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(t.pm_lifecycle_plans.select().where(t.pm_lifecycle_plans.c.id == plan_id))
            ).mappings().first()
        return dict(row) if row else None

    async def list_pm_lifecycle_plans(
        self,
        *,
        connection_id: UUID | None = None,
        target_id: UUID | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = t.pm_lifecycle_plans.select().order_by(t.pm_lifecycle_plans.c.created_at.desc()).limit(limit)
        if connection_id is not None:
            query = query.where(t.pm_lifecycle_plans.c.connection_id == connection_id)
        if target_id is not None:
            query = query.where(t.pm_lifecycle_plans.c.target_id == target_id)
        if status is not None:
            query = query.where(t.pm_lifecycle_plans.c.status == status)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def approve_pm_lifecycle_plan(
        self,
        plan_id: UUID,
        *,
        digest: str,
        actor: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        from mas_core.integrations.contracts import LifecyclePlanError

        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    t.pm_lifecycle_plans.select().where(t.pm_lifecycle_plans.c.id == plan_id).with_for_update()
                )
            ).mappings().first()
            if row is None:
                raise LifecyclePlanError("missing_plan", "lifecycle plan was not found")
            current = dict(row)
            if str(current.get("digest")) != digest:
                raise LifecyclePlanError("digest_mismatch", "lifecycle plan digest does not match the persisted plan")
            if current.get("status") == "APPROVED":
                return current
            if current.get("status") != "PLANNED":
                raise LifecyclePlanError("invalid_status", f"lifecycle plan is {current.get('status')}, not PLANNED")
            if current.get("expires_at") and current["expires_at"] <= now:
                await conn.execute(
                    t.pm_lifecycle_plans.update()
                    .where(t.pm_lifecycle_plans.c.id == plan_id)
                    .values(status="EXPIRED", error="lifecycle plan expired before approval", updated_at=now)
                )
                raise LifecyclePlanError("expired_plan", "lifecycle plan has expired")

            binding = None
            if current.get("binding_id") is not None:
                binding = (
                    await conn.execute(
                        t.pm_project_bindings.select()
                        .where(t.pm_project_bindings.c.id == current["binding_id"])
                    )
                ).mappings().first()

            # Approval is itself an immutable governed transition.  Keep the
            # evidence and lifecycle history in this transaction with the
            # APPROVED state so an evidence write failure cannot authorize a
            # plan without a durable approval record.
            transaction_id = str(uuid4())
            approval_evidence_id = uuid4()
            approval_evidence = {
                "id": approval_evidence_id,
                "connection_id": current["connection_id"],
                "binding_id": current.get("binding_id"),
                "project_id": binding.get("project_id") if binding is not None else None,
                "evidence_type": "pm_lifecycle_approval",
                "external_id": str(plan_id),
                "repository": None,
                "payload": self._pm_json_safe(
                    {
                        "plan_id": str(plan_id),
                        "digest": digest,
                        "plan_kind": current.get("plan_kind"),
                        "target_type": current.get("target_type"),
                        "target_id": str(current.get("target_id")),
                        "expected_connection_status": current.get("expected_connection_status"),
                        "expected_binding_status": current.get("expected_binding_status"),
                        "desired_connection_status": current.get("desired_connection_status"),
                        "desired_binding_status": current.get("desired_binding_status"),
                        "actor": actor,
                        "reason": reason,
                    }
                ),
                "idempotency_key": f"pm-lifecycle:approval:{plan_id}:{digest}",
                "created_at": now,
            }
            evidence_stmt = (
                pg_insert(t.integration_evidence_records)
                .values(**approval_evidence)
                .on_conflict_do_nothing(constraint="uq_integration_evidence_idempotency")
                .returning(t.integration_evidence_records)
            )
            evidence_row = await self._mapping_first(await conn.execute(evidence_stmt))
            if evidence_row is None:
                evidence_row = (
                    await conn.execute(
                        t.integration_evidence_records.select().where(
                            t.integration_evidence_records.c.idempotency_key
                            == approval_evidence["idempotency_key"]
                        )
                    )
                ).mappings().first()
            if evidence_row is None:
                raise LifecyclePlanError("approval_evidence_failed", "could not persist lifecycle approval evidence")

            before_state = {
                "plan_status": "PLANNED",
                "connection_status": current.get("expected_connection_status"),
                "binding_status": current.get("expected_binding_status"),
            }
            after_state = {**before_state, "plan_status": "APPROVED"}
            await conn.execute(
                t.pm_lifecycle_audits.insert().values(
                    id=uuid4(),
                    plan_id=plan_id,
                    connection_id=current["connection_id"],
                    binding_id=current.get("binding_id"),
                    action="APPROVE",
                    before_state=self._pm_json_safe(before_state),
                    after_state=self._pm_json_safe(after_state),
                    actor=actor,
                    approval_reference=self._pm_json_safe(
                        {
                            "plan_id": str(plan_id),
                            "digest": digest,
                            "approval_actor": actor,
                            "approval_reason": reason,
                        }
                    ),
                    evidence_refs={"approval_evidence_id": str(evidence_row["id"])},
                    transaction_id=transaction_id,
                    rollback_operations=self._pm_json_safe(current.get("rollback_operations") or []),
                    occurred_at=now,
                )
            )
            await conn.execute(
                t.pm_lifecycle_plans.update()
                .where(t.pm_lifecycle_plans.c.id == plan_id)
                .values(
                    status="APPROVED",
                    approval_actor=actor,
                    approved_at=now,
                    approval_reason=reason,
                    updated_at=now,
                    error=None,
                )
            )
        refreshed = await self.get_pm_lifecycle_plan(plan_id)
        if refreshed is None:
            raise LifecyclePlanError("missing_plan", "lifecycle plan disappeared after approval")
        return refreshed

    async def reject_pm_lifecycle_plan(
        self,
        plan_id: UUID,
        *,
        digest: str,
        actor: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        from mas_core.integrations.contracts import LifecyclePlanError

        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    t.pm_lifecycle_plans.select().where(t.pm_lifecycle_plans.c.id == plan_id).with_for_update()
                )
            ).mappings().first()
            if row is None:
                raise LifecyclePlanError("missing_plan", "lifecycle plan was not found")
            current = dict(row)
            if str(current.get("digest")) != digest:
                raise LifecyclePlanError("digest_mismatch", "lifecycle plan digest does not match the persisted plan")
            if current.get("status") == "REJECTED":
                return current
            if current.get("status") not in {"PLANNED", "APPROVED"}:
                raise LifecyclePlanError("invalid_status", f"lifecycle plan is {current.get('status')} and cannot be rejected")
            if current.get("expires_at") and current["expires_at"] <= now:
                await conn.execute(
                    t.pm_lifecycle_plans.update()
                    .where(t.pm_lifecycle_plans.c.id == plan_id)
                    .values(status="EXPIRED", error="lifecycle plan expired before rejection", updated_at=now)
                )
                raise LifecyclePlanError("expired_plan", "lifecycle plan has expired")
            await conn.execute(
                t.pm_lifecycle_plans.update()
                .where(t.pm_lifecycle_plans.c.id == plan_id)
                .values(
                    status="REJECTED",
                    approval_actor=actor,
                    approved_at=now,
                    approval_reason=reason or "rejected by operator",
                    updated_at=now,
                )
            )
        refreshed = await self.get_pm_lifecycle_plan(plan_id)
        if refreshed is None:
            raise LifecyclePlanError("missing_plan", "lifecycle plan disappeared after rejection")
        return refreshed

    async def apply_pm_lifecycle_plan(
        self,
        plan_id: UUID,
        *,
        digest: str,
        actor: str,
    ) -> dict[str, Any]:
        """Apply an approved plan with row locks, CAS, and one audit insert."""
        from mas_core.integrations.contracts import LifecyclePlanError

        now = datetime.now(tz=UTC)
        transaction_id = str(uuid4())
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    t.pm_lifecycle_plans.select().where(t.pm_lifecycle_plans.c.id == plan_id).with_for_update()
                )
            ).mappings().first()
            if row is None:
                raise LifecyclePlanError("missing_plan", "lifecycle plan was not found")
            plan = dict(row)
            if str(plan.get("digest")) != digest:
                raise LifecyclePlanError("digest_mismatch", "lifecycle plan digest does not match the persisted plan")
            if plan.get("status") == "APPLIED":
                return {
                    "status": "APPLIED",
                    "plan": plan,
                    "result": plan.get("application_result") or {},
                    "idempotent": True,
                }
            if plan.get("status") != "APPROVED":
                raise LifecyclePlanError("not_approved", "lifecycle plan must be APPROVED before apply")
            if plan.get("expires_at") and plan["expires_at"] <= now:
                await conn.execute(
                    t.pm_lifecycle_plans.update()
                    .where(t.pm_lifecycle_plans.c.id == plan_id)
                    .values(status="EXPIRED", error="lifecycle plan expired before apply", updated_at=now)
                )
                raise LifecyclePlanError("expired_plan", "lifecycle plan has expired")

            connection = (
                await conn.execute(
                    t.pm_connections.select().where(t.pm_connections.c.id == plan["connection_id"]).with_for_update()
                )
            ).mappings().first()
            if connection is None:
                raise LifecyclePlanError("stale_state", "target connection no longer exists")
            binding = None
            if plan.get("binding_id") is not None:
                binding = (
                    await conn.execute(
                        t.pm_project_bindings.select()
                        .where(t.pm_project_bindings.c.id == plan["binding_id"])
                        .with_for_update()
                    )
                ).mappings().first()
                if binding is None:
                    raise LifecyclePlanError("stale_state", "target binding no longer exists")

            stale_reasons: list[str] = []
            if plan.get("expected_connection_status") is not None and connection["status"] != plan["expected_connection_status"]:
                stale_reasons.append("connection state changed")
            if plan.get("expected_connection_revision") is not None and int(connection.get("revision") or 1) != int(plan["expected_connection_revision"]):
                stale_reasons.append("connection revision changed")
            if binding is not None:
                if plan.get("expected_binding_status") is not None and binding["status"] != plan["expected_binding_status"]:
                    stale_reasons.append("binding state changed")
                if plan.get("expected_binding_revision") is not None and int(binding.get("revision") or 1) != int(plan["expected_binding_revision"]):
                    stale_reasons.append("binding revision changed")
            if stale_reasons:
                await conn.execute(
                    t.pm_lifecycle_plans.update()
                    .where(t.pm_lifecycle_plans.c.id == plan_id)
                    .values(status="STALE", error="; ".join(stale_reasons), updated_at=now)
                )
                return {
                    "status": "STALE",
                    "plan": {**plan, "status": "STALE", "error": "; ".join(stale_reasons)},
                    "result": {"code": "stale_state", "reasons": stale_reasons},
                    "idempotent": False,
                }

            before_state = {
                "connection_status": connection.get("status"),
                "connection_revision": int(connection.get("revision") or 1),
                "binding_status": binding.get("status") if binding is not None else None,
                "binding_revision": int(binding.get("revision") or 1) if binding is not None else None,
            }
            if plan.get("target_type") == "pm_binding":
                if (
                    binding is None
                    or plan.get("desired_binding_status") is None
                    or plan.get("expected_binding_status") is None
                    or plan.get("expected_binding_revision") is None
                ):
                    raise LifecyclePlanError(
                        "invalid_plan",
                        "binding lifecycle plan is missing its expected or desired state",
                    )
                if str(plan.get("desired_binding_status") or "").upper() == "ACTIVE":
                    try:
                        self._assert_pm_binding_activation_ready(
                            {**dict(binding), "status": "ACTIVE"},
                            connection,
                        )
                    except ValueError as exc:
                        raise LifecyclePlanError("activation_not_ready", str(exc)) from exc
                binding_update = await conn.execute(
                    t.pm_project_bindings.update()
                    .where(t.pm_project_bindings.c.id == binding["id"])
                    .where(t.pm_project_bindings.c.status == plan.get("expected_binding_status"))
                    .where(t.pm_project_bindings.c.revision == plan.get("expected_binding_revision"))
                    .values(
                        status=plan["desired_binding_status"],
                        revision=t.pm_project_bindings.c.revision + 1,
                        updated_at=now,
                    )
                )
                if binding_update.rowcount != 1:
                    raise LifecyclePlanError("stale_state", "target binding changed during compare-and-swap")
                after_binding_status = plan["desired_binding_status"]
                after_binding_revision = before_state["binding_revision"] + 1
            elif plan.get("target_type") == "pm_connection":
                if (
                    plan.get("desired_connection_status") is None
                    or plan.get("expected_connection_status") is None
                    or plan.get("expected_connection_revision") is None
                ):
                    raise LifecyclePlanError(
                        "invalid_plan",
                        "connection lifecycle plan is missing its expected or desired state",
                    )
                connection_update = await conn.execute(
                    t.pm_connections.update()
                    .where(t.pm_connections.c.id == connection["id"])
                    .where(t.pm_connections.c.status == plan.get("expected_connection_status"))
                    .where(t.pm_connections.c.revision == plan.get("expected_connection_revision"))
                    .values(
                        status=plan["desired_connection_status"],
                        revision=t.pm_connections.c.revision + 1,
                        updated_at=now,
                    )
                )
                if connection_update.rowcount != 1:
                    raise LifecyclePlanError("stale_state", "target connection changed during compare-and-swap")
                after_binding_status = before_state["binding_status"]
                after_binding_revision = before_state["binding_revision"]
            else:
                raise LifecyclePlanError("invalid_plan", "unsupported lifecycle plan target type")

            after_state = {
                "connection_status": plan.get("desired_connection_status") or before_state["connection_status"],
                "connection_revision": before_state["connection_revision"] + (1 if plan.get("target_type") == "pm_connection" else 0),
                "binding_status": after_binding_status,
                "binding_revision": after_binding_revision,
            }
            audit_id = uuid4()
            application_result = {
                "audit_id": str(audit_id),
                "transaction_id": transaction_id,
                "before_state": before_state,
                "after_state": after_state,
                "actor": actor,
                "applied_at": now.isoformat(),
                "rollback_operations": plan.get("rollback_operations") or [],
            }
            await conn.execute(
                t.pm_lifecycle_audits.insert().values(
                    id=audit_id,
                    plan_id=plan_id,
                    connection_id=plan["connection_id"],
                    binding_id=plan.get("binding_id"),
                    action=plan["plan_kind"],
                    before_state=self._pm_json_safe(before_state),
                    after_state=self._pm_json_safe(after_state),
                    actor=actor,
                    approval_reference={
                        "plan_id": str(plan_id),
                        "digest": digest,
                        "approval_actor": plan.get("approval_actor"),
                        "approved_at": plan.get("approved_at").isoformat() if plan.get("approved_at") else None,
                    },
                    evidence_refs=self._pm_json_safe(plan.get("evidence_refs") or {}),
                    transaction_id=transaction_id,
                    rollback_operations=self._pm_json_safe(plan.get("rollback_operations") or []),
                    occurred_at=now,
                )
            )
            await conn.execute(
                t.pm_lifecycle_plans.update()
                .where(t.pm_lifecycle_plans.c.id == plan_id)
                .values(
                    status="APPLIED",
                    applied_actor=actor,
                    applied_at=now,
                    application_result=self._pm_json_safe(application_result),
                    updated_at=now,
                    error=None,
                )
            )
        applied_plan = await self.get_pm_lifecycle_plan(plan_id)
        return {
            "status": "APPLIED",
            "plan": applied_plan or plan,
            "result": application_result,
            "idempotent": False,
        }

    async def get_pm_lifecycle_audit(self, audit_id: UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(t.pm_lifecycle_audits.select().where(t.pm_lifecycle_audits.c.id == audit_id))
            ).mappings().first()
        return dict(row) if row else None

    async def list_pm_lifecycle_audits(
        self,
        *,
        connection_id: UUID | None = None,
        binding_id: UUID | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = t.pm_lifecycle_audits.select().order_by(t.pm_lifecycle_audits.c.occurred_at.desc()).limit(limit)
        if connection_id is not None:
            query = query.where(t.pm_lifecycle_audits.c.connection_id == connection_id)
        if binding_id is not None:
            query = query.where(t.pm_lifecycle_audits.c.binding_id == binding_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_pm_mapping(
        self,
        *,
        connection_id: UUID,
        object_type: str,
        aiat_object_id: UUID | None = None,
        external_id: str | None = None,
        external_key: str | None = None,
    ) -> dict[str, Any] | None:
        query = (
            t.pm_object_mappings.select()
            .where(t.pm_object_mappings.c.connection_id == connection_id)
            .where(t.pm_object_mappings.c.object_type == object_type)
        )
        if aiat_object_id is not None:
            query = query.where(t.pm_object_mappings.c.aiat_object_id == aiat_object_id)
        if external_id is not None:
            query = query.where(t.pm_object_mappings.c.external_id == external_id)
        if external_key is not None:
            query = query.where(t.pm_object_mappings.c.external_key == external_key)
        async with self.engine.connect() as conn:
            row = (await conn.execute(query)).mappings().first()
        return dict(row) if row else None

    async def mark_pm_inbox_event(
        self,
        event_id: UUID,
        *,
        status: str,
        error: str | None = None,
        normalized_type: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status, "error": error}
        if normalized_type is not None:
            values["normalized_type"] = normalized_type
        if result is not None:
            values["result"] = self._pm_json_safe(result)
        if status in {"PROCESSED", "CONFLICT", "FAILED"}:
            values["processed_at"] = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            await conn.execute(t.pm_inbox_events.update().where(t.pm_inbox_events.c.id == event_id).values(**values))

    async def create_work_item_comment(
        self,
        *,
        issue_id: UUID,
        body: str,
        actor_id: str,
        run_id: UUID | None = None,
        approval_id: UUID | None = None,
        evidence_id: str | None = None,
        body_blob_ref: str | None = None,
        origin: str = "aiat",
    ) -> dict[str, Any]:
        now = datetime.now(tz=UTC)
        values = {"id": uuid4(), "issue_id": issue_id, "body": body, "actor_id": actor_id, "run_id": run_id, "approval_id": approval_id, "evidence_id": evidence_id, "body_blob_ref": body_blob_ref, "origin": origin, "created_at": now, "updated_at": now}
        async with self.engine.begin() as conn:
            await conn.execute(t.work_item_comments.insert().values(**values))
            issue = await self._mapping_first(
                await conn.execute(t.issues.select().where(t.issues.c.id == issue_id))
            )
            if issue is not None:
                await self._enqueue_comment_projections_tx(conn, dict(issue), values)
        return values

    async def list_work_item_comments(self, issue_id: UUID, *, limit: int = 100) -> list[dict[str, Any]]:
        query = t.work_item_comments.select().where(t.work_item_comments.c.issue_id == issue_id).order_by(t.work_item_comments.c.created_at).limit(limit)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def create_work_item_link(
        self,
        *,
        issue_id: UUID,
        link_type: str,
        target_type: str,
        target_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = {"id": uuid4(), "issue_id": issue_id, "link_type": link_type, "target_type": target_type, "target_id": target_id, "metadata": metadata or {}, "created_at": datetime.now(tz=UTC)}
        async with self.engine.begin() as conn:
            stmt = pg_insert(t.work_item_links).values(**values).on_conflict_do_nothing(constraint="uq_work_item_link").returning(t.work_item_links)
            row = await self._mapping_first(await conn.execute(stmt))
            if row is None:
                return values
            issue = await self._mapping_first(
                await conn.execute(t.issues.select().where(t.issues.c.id == issue_id))
            )
            if issue is not None:
                await self._enqueue_link_projections_tx(conn, dict(issue), dict(row))
        return dict(row) if row else values

    async def list_work_item_links(self, issue_id: UUID, *, limit: int = 100) -> list[dict[str, Any]]:
        query = t.work_item_links.select().where(t.work_item_links.c.issue_id == issue_id).order_by(t.work_item_links.c.created_at).limit(limit)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def record_integration_evidence(
        self,
        *,
        connection_id: UUID,
        evidence_type: str,
        external_id: str | None = None,
        repository: str | None = None,
        project_id: UUID | None = None,
        binding_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
        idempotency_key: str,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist source-control facts without making them canonical state.

        PRs, checks, reviews, and commit metadata are evidence consumed by
        governance and release gates.  They intentionally remain separate
        from PM object mappings so a source-control provider can be replaced
        without rewriting canonical work items.
        """
        trace_id = trace_id or current_trace_id()
        effective_span_id = (span_id or current_span_id() or new_span_id()) if trace_id else span_id
        created_at_value = datetime.now(tz=UTC)
        values = {
            "id": uuid4(),
            "connection_id": connection_id,
            "binding_id": binding_id,
            "project_id": project_id,
            "evidence_type": evidence_type,
            "external_id": external_id,
            "repository": repository,
            "payload": self._pm_json_safe(payload or {}),
            "idempotency_key": idempotency_key,
            "trace_id": trace_id,
            "span_id": effective_span_id,
            "created_at": created_at_value,
        }
        async with self.engine.begin() as conn:
            stmt = (
                pg_insert(t.integration_evidence_records)
                .values(**values)
                .on_conflict_do_update(
                    constraint="uq_integration_evidence_idempotency",
                    set_={
                        "payload": values["payload"],
                        "external_id": values["external_id"],
                        "repository": values["repository"],
                        "trace_id": sa.func.coalesce(
                            values["trace_id"],
                            t.integration_evidence_records.c.trace_id,
                        ),
                        "span_id": sa.func.coalesce(
                            values["span_id"],
                            t.integration_evidence_records.c.span_id,
                        ),
                    },
                )
                .returning(t.integration_evidence_records)
            )
            row = await self._mapping_first(await conn.execute(stmt))
            if trace_id:
                await self._insert_native_trace_span_tx(
                    conn,
                    trace_id=trace_id,
                    span_id=new_span_id(),
                    parent_span_id=effective_span_id,
                    source_kind="integration",
                    operation=evidence_type,
                    service="integration_evidence",
                    status="success",
                    started_at=created_at_value,
                    ended_at=created_at_value,
                    attributes={
                        "evidence_type": evidence_type,
                        "repository": repository,
                    },
                )
        return dict(row) if row else values

    async def list_integration_evidence(
        self,
        *,
        connection_id: UUID | None = None,
        project_id: UUID | None = None,
        evidence_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = t.integration_evidence_records.select().order_by(
            t.integration_evidence_records.c.created_at.desc()
        ).limit(limit)
        if connection_id is not None:
            query = query.where(t.integration_evidence_records.c.connection_id == connection_id)
        if project_id is not None:
            query = query.where(t.integration_evidence_records.c.project_id == project_id)
        if evidence_type is not None:
            query = query.where(t.integration_evidence_records.c.evidence_type == evidence_type)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

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
        now = datetime.now(tz=UTC)
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
        now = datetime.now(tz=UTC)
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
                set_={k: v for k, v in values.items() if k != "agent_id"},
            )
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)
        return values

    async def get_agent_profile(self, agent_id: str) -> dict[str, Any] | None:
        """Fetch an agent's profile."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        t.agent_profiles.select().where(t.agent_profiles.c.agent_id == agent_id)
                    )
                )
                .mappings()
                .first()
            )
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
                (await conn.execute(t.system_config.select().where(t.system_config.c.key == key)))
                .mappings()
                .first()
            )
        return row["value"] if row else None

    async def set_config(self, key: str, value: str) -> None:
        """Insert-or-update a system_config key."""
        now = datetime.now(tz=UTC)
        stmt = (
            pg_insert(t.system_config)
            .values(key=key, value=value, updated_at=now)
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": value, "updated_at": now},
            )
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    async def set_config_if_absent(self, key: str, value: str) -> bool:
        """Insert a configuration record only when its key does not exist."""
        stmt = (
            pg_insert(t.system_config)
            .values(key=key, value=value, updated_at=datetime.now(tz=UTC))
            .on_conflict_do_nothing(index_elements=["key"])
            .returning(t.system_config.c.key)
        )
        async with self.engine.begin() as conn:
            return (await conn.execute(stmt)).scalar_one_or_none() is not None

    async def compare_and_set_config(self, key: str, expected: str, value: str) -> bool:
        """Atomically replace a configuration value when it still matches *expected*."""
        stmt = (
            t.system_config.update()
            .where(t.system_config.c.key == key, t.system_config.c.value == expected)
            .values(value=value, updated_at=datetime.now(tz=UTC))
            .returning(t.system_config.c.key)
        )
        async with self.engine.begin() as conn:
            return (await conn.execute(stmt)).scalar_one_or_none() is not None

    async def get_all_config(self) -> dict[str, str]:
        """Fetch all system_config rows as a dict."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(t.system_config.select())).mappings().all()
        return {r["key"]: r["value"] for r in rows}

    # ═══════════════════════════════════════════════════════════════════════════
    # Memory (per-agent key/value store)
    # ═══════════════════════════════════════════════════════════════════════════

    async def memory_set(
        self,
        *,
        agent_id: str,
        key: str,
        value: Any,
    ) -> None:
        """Set a key/value pair in the per-agent memory store (upsert)."""
        stmt = (
            pg_insert(t.memory)
            .values(
                agent_id=agent_id,
                key=key,
                value=value,
                updated_at=datetime.now(tz=UTC),
            )
            .on_conflict_do_update(
                constraint="uq_memory_agent_key",
                set_={
                    "value": value,
                    "updated_at": datetime.now(tz=UTC),
                },
            )
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    async def memory_get(self, agent_id: str, key: str) -> Any | None:
        """Fetch a single value from the per-agent memory store."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        t.memory.select().where(
                            sa.and_(
                                t.memory.c.agent_id == agent_id,
                                t.memory.c.key == key,
                            )
                        )
                    )
                )
                .mappings()
                .first()
            )
        return row["value"] if row else None

    async def memory_list(self, agent_id: str) -> dict[str, Any]:
        """Fetch all key/value pairs for an agent."""
        async with self.engine.connect() as conn:
            rows = (
                (await conn.execute(t.memory.select().where(t.memory.c.agent_id == agent_id)))
                .mappings()
                .all()
            )
        return {r["key"]: r["value"] for r in rows}

    async def memory_delete(self, agent_id: str, key: str) -> None:
        """Delete a single key from the per-agent memory store."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.memory.delete().where(
                    sa.and_(
                        t.memory.c.agent_id == agent_id,
                        t.memory.c.key == key,
                    )
                )
            )

    async def memory_delete_all(self, agent_id: str) -> None:
        """Delete all memory entries for an agent."""
        async with self.engine.begin() as conn:
            await conn.execute(t.memory.delete().where(t.memory.c.agent_id == agent_id))

    # ═══════════════════════════════════════════════════════════════════════════
    # Durable project usage telemetry
    # ═══════════════════════════════════════════════════════════════════════════

    async def record_project_usage(
        self,
        *,
        project_id: UUID | str,
        event_type: str,
        agent_id: str | None = None,
        team_id: str | None = None,
        model: str | None = None,
        tool_name: str | None = None,
        status: str = "success",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: float | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        details: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
        event_id: UUID | None = None,
        company_id: UUID | None = None,
        run_id: UUID | None = None,
        worker_id: UUID | None = None,
        provider_id: str | None = None,
        claim_owner: str | None = None,
        attempt_count: int | None = None,
        billing_code: str | None = None,
        pricing_snapshot: dict[str, Any] | None = None,
        resource_json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Append one immutable, project-scoped LLM or tool usage event.

        Usage events are deliberately idempotent.  Worker retries, gateway
        reconnects, and queue recovery must not double-charge a project.  The
        database uniqueness constraint is the final arbiter; the read-before-
        write below also makes the common replay path cheap and deterministic.
        """
        trace_id = trace_id or current_trace_id()
        if event_type not in {"llm", "tool"}:
            raise ValueError("event_type must be 'llm' or 'tool'")
        normalized_project_id = (
            project_id if isinstance(project_id, UUID) else UUID(str(project_id))
        )
        if company_id is None:
            async with self.engine.connect() as conn:
                company_id = (
                    await conn.execute(
                        sa.select(t.projects.c.company_id).where(
                            t.projects.c.id == normalized_project_id
                        )
                    )
                ).scalar_one_or_none()
        occurred_at_value = occurred_at or datetime.now(tz=UTC)
        effective_span_id = (span_id or current_span_id() or new_span_id()) if trace_id else span_id
        values = {
            "id": event_id or uuid4(),
            "project_id": normalized_project_id,
            "company_id": company_id,
            "run_id": run_id,
            "worker_id": worker_id,
            "event_type": event_type,
            "agent_id": agent_id,
            "team_id": team_id,
            "model": model,
            "provider_id": provider_id,
            "tool_name": tool_name,
            "billing_code": billing_code,
            "pricing_snapshot": pricing_snapshot,
            "resource_json": resource_json,
            "idempotency_key": idempotency_key,
            "status": status,
            "prompt_tokens": max(0, int(prompt_tokens or 0)),
            "completion_tokens": max(0, int(completion_tokens or 0)),
            "cost_usd": max(0.0, float(cost_usd or 0.0)),
            "duration_ms": duration_ms,
            "trace_id": trace_id,
            "span_id": effective_span_id,
            "details": details,
            "occurred_at": occurred_at_value,
        }
        async with self.engine.begin() as conn:
            if claim_owner is not None or attempt_count is not None:
                if run_id is None or attempt_count is None:
                    return None
                run = (
                    await conn.execute(
                        t.worker_runs.select()
                        .where(t.worker_runs.c.id == run_id)
                        .with_for_update()
                    )
                ).mappings().first()
                if not self._worker_run_claim_matches(
                    run,
                    claim_owner=claim_owner,
                    attempt_count=int(attempt_count),
                    now=datetime.now(tz=UTC),
                ):
                    return None
            if idempotency_key:
                existing = (
                    await conn.execute(
                        t.project_usage_events.select().where(
                            t.project_usage_events.c.idempotency_key == idempotency_key
                        )
                    )
                ).mappings().first()
                if existing is not None:
                    return dict(existing)
                try:
                    await conn.execute(t.project_usage_events.insert().values(**values))
                except sa.exc.IntegrityError:
                    # A concurrent writer won the unique idempotency race.
                    existing = (
                        await conn.execute(
                            t.project_usage_events.select().where(
                                t.project_usage_events.c.idempotency_key == idempotency_key
                            )
                        )
                    ).mappings().first()
                    if existing is None:
                        raise
                    return dict(existing)
            else:
                await conn.execute(t.project_usage_events.insert().values(**values))
            if trace_id:
                await self._insert_native_trace_span_tx(
                    conn,
                    trace_id=trace_id,
                    span_id=new_span_id(),
                    parent_span_id=effective_span_id,
                    source_kind="model" if event_type == "llm" else "tool",
                    operation=model or tool_name or event_type,
                    service="project_usage",
                    status=status,
                    started_at=occurred_at_value - timedelta(milliseconds=float(duration_ms or 0)),
                    ended_at=occurred_at_value,
                    duration_ms=duration_ms or 0,
                    attributes={
                        "event_type": event_type,
                        "agent_id": agent_id,
                        "team_id": team_id,
                    },
                )
        return values

    async def get_project_usage(self, project_id: UUID) -> dict[str, Any]:
        """Aggregate durable LLM/tool usage for one project."""
        event_type = t.project_usage_events.c.event_type
        status = t.project_usage_events.c.status
        q = sa.select(
            sa.func.count().filter(event_type == "llm").label("llm_calls"),
            sa.func.count().filter(event_type == "tool").label("tool_calls"),
            sa.func.count().filter(status != "success").label("failed_calls"),
            sa.func.coalesce(sa.func.sum(t.project_usage_events.c.prompt_tokens), 0).label(
                "prompt_tokens"
            ),
            sa.func.coalesce(sa.func.sum(t.project_usage_events.c.completion_tokens), 0).label(
                "completion_tokens"
            ),
            sa.func.coalesce(sa.func.sum(t.project_usage_events.c.cost_usd), 0).label(
                "total_cost_usd"
            ),
            sa.func.min(t.project_usage_events.c.occurred_at).label("first_event_at"),
            sa.func.max(t.project_usage_events.c.occurred_at).label("last_event_at"),
        ).where(t.project_usage_events.c.project_id == project_id)
        async with self.engine.connect() as conn:
            row = (await conn.execute(q)).mappings().one()
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

    async def list_project_usage_aggregates(
        self,
        *,
        company_id: UUID | None = None,
        since: datetime | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        """Return bounded per-project usage aggregates for operations forecasts.

        The query deliberately returns aggregates rather than raw provider or
        tool payloads.  ``project_usage_events`` remains the accounting
        authority while this read model supplies enough counts, cost, token,
        duration, and time-span data for SLO/capacity projections.
        """

        events = t.project_usage_events
        projects = t.projects
        event_type = events.c.event_type
        status = events.c.status
        project_join = events.join(projects, events.c.project_id == projects.c.id)
        clauses = []
        if company_id is not None:
            clauses.append(projects.c.company_id == company_id)
        if since is not None:
            clauses.append(events.c.occurred_at >= since)
        query = sa.select(
            events.c.project_id.label("project_id"),
            projects.c.company_id.label("company_id"),
            sa.func.count().label("event_count"),
            sa.func.count().filter(event_type == "llm").label("llm_calls"),
            sa.func.count().filter(event_type == "tool").label("tool_calls"),
            sa.func.count().filter(status != "success").label("failed_calls"),
            sa.func.count().filter(sa.and_(event_type == "llm", status != "success")).label("llm_failed_calls"),
            sa.func.count().filter(sa.and_(event_type == "tool", status != "success")).label("tool_failed_calls"),
            sa.func.coalesce(sa.func.sum(events.c.prompt_tokens), 0).label("prompt_tokens"),
            sa.func.coalesce(sa.func.sum(events.c.completion_tokens), 0).label("completion_tokens"),
            sa.func.coalesce(sa.func.sum(events.c.cost_usd), 0).label("total_cost_usd"),
            sa.func.coalesce(sa.func.sum(events.c.duration_ms).filter(event_type == "llm"), 0).label("llm_duration_sum_ms"),
            sa.func.coalesce(sa.func.sum(events.c.duration_ms).filter(event_type == "tool"), 0).label("tool_duration_sum_ms"),
            sa.func.count().filter(sa.and_(event_type == "llm", events.c.duration_ms.is_not(None))).label("llm_duration_count"),
            sa.func.count().filter(sa.and_(event_type == "tool", events.c.duration_ms.is_not(None))).label("tool_duration_count"),
            sa.func.min(events.c.occurred_at).label("first_event_at"),
            sa.func.max(events.c.occurred_at).label("last_event_at"),
        ).select_from(project_join)
        if clauses:
            query = query.where(sa.and_(*clauses))
        query = query.group_by(events.c.project_id, projects.c.company_id).order_by(
            sa.func.max(events.c.occurred_at).desc()
        ).limit(max(1, min(int(limit), 10_000)))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        aggregates: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["event_count"] = int(item.get("event_count") or 0)
            item["llm_calls"] = int(item.get("llm_calls") or 0)
            item["tool_calls"] = int(item.get("tool_calls") or 0)
            item["failed_calls"] = int(item.get("failed_calls") or 0)
            item["llm_failed_calls"] = int(item.get("llm_failed_calls") or 0)
            item["tool_failed_calls"] = int(item.get("tool_failed_calls") or 0)
            item["prompt_tokens"] = int(item.get("prompt_tokens") or 0)
            item["completion_tokens"] = int(item.get("completion_tokens") or 0)
            item["total_tokens"] = item["prompt_tokens"] + item["completion_tokens"]
            item["total_cost_usd"] = float(item.get("total_cost_usd") or 0.0)
            for kind in ("llm", "tool"):
                sum_key = f"{kind}_duration_sum_ms"
                count_key = f"{kind}_duration_count"
                item[f"{kind}_duration_avg_ms"] = (
                    float(item.get(sum_key) or 0.0) / int(item.get(count_key) or 1)
                    if int(item.get(count_key) or 0) > 0
                    else None
                )
            aggregates.append(item)
        return aggregates

    async def list_project_usage_events(
        self,
        project_id: UUID,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = (
            t.project_usage_events.select()
            .where(t.project_usage_events.c.project_id == project_id)
            .order_by(t.project_usage_events.c.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_project_usage_event(self, event_id: UUID) -> dict[str, Any] | None:
        """Fetch one durable usage event by its canonical evidence ID."""
        query = t.project_usage_events.select().where(t.project_usage_events.c.id == event_id)
        async with self.engine.connect() as conn:
            row = (await conn.execute(query)).mappings().first()
        return dict(row) if row else None

    async def list_pm_slo_observations(
        self,
        *,
        company_id: UUID | None = None,
        since: datetime | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        """Return safe PM/SCM delivery observations for the SLO read model.

        The projection reads delivery state and bounded timestamps only. It
        never returns provider payloads, headers, credentials, or error
        bodies. A company filter is applied through project bindings when the
        connection is associated with a project; unscoped operator reads may
        aggregate all configured PM connections.
        """

        connections = t.pm_connections
        bindings = t.pm_project_bindings
        projects = t.projects
        connection_company_match = sa.exists(
            sa.select(1)
            .select_from(bindings.join(projects, bindings.c.project_id == projects.c.id))
            .where(bindings.c.connection_id == connections.c.id)
            .where(projects.c.company_id == company_id)
        ) if company_id is not None else None

        observations: list[dict[str, Any]] = []
        inbox = t.pm_inbox_events
        since_clause = inbox.c.received_at >= since if since is not None else None
        inbox_clauses: list[Any] = []
        if since_clause is not None:
            inbox_clauses.append(since_clause)
        if connection_company_match is not None:
            inbox_clauses.append(connection_company_match)
        inbox_query = sa.select(
            inbox.c.id.label("id"),
            inbox.c.status.label("status"),
            inbox.c.received_at.label("occurred_at"),
            inbox.c.processed_at.label("completed_at"),
            sa.literal("pm_inbox_events").label("source"),
        ).select_from(inbox.join(connections, inbox.c.connection_id == connections.c.id))
        if inbox_clauses:
            inbox_query = inbox_query.where(sa.and_(*inbox_clauses))

        outbox = t.pm_outbox_events
        outbox_since_clause = outbox.c.created_at >= since if since is not None else None
        outbox_clauses: list[Any] = []
        if outbox_since_clause is not None:
            outbox_clauses.append(outbox_since_clause)
        if connection_company_match is not None:
            outbox_clauses.append(connection_company_match)
        outbox_query = sa.select(
            outbox.c.id.label("id"),
            outbox.c.status.label("status"),
            outbox.c.created_at.label("occurred_at"),
            outbox.c.processed_at.label("completed_at"),
            sa.literal("pm_outbox_events").label("source"),
        ).select_from(outbox.join(connections, outbox.c.connection_id == connections.c.id))
        if outbox_clauses:
            outbox_query = outbox_query.where(sa.and_(*outbox_clauses))

        combined = sa.union_all(inbox_query, outbox_query).subquery("pm_slo_observations")
        query = sa.select(combined).order_by(combined.c.occurred_at.asc()).limit(max(1, min(int(limit), 10_000)))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        for row in rows:
            item = dict(row)
            occurred_at = item.get("occurred_at")
            completed_at = item.get("completed_at")
            duration_ms = None
            if isinstance(occurred_at, datetime) and isinstance(completed_at, datetime):
                duration_ms = max(0.0, (completed_at - occurred_at).total_seconds() * 1000)
            status = str(item.get("status") or "unknown").strip().upper()
            item["status"] = "success" if status in {"PROCESSED", "SENT", "DELIVERED", "SUCCESS", "RESOLVED"} else "failed"
            item["duration_ms"] = duration_ms
            item.pop("completed_at", None)
            observations.append(item)
        return observations

    async def list_recovery_slo_observations(
        self,
        *,
        company_id: UUID | None = None,
        since: datetime | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        """Return bounded worker-run recovery transition observations."""

        transitions = t.worker_run_transitions
        runs = t.worker_runs
        projects = t.projects
        clauses: list[Any] = [
            sa.or_(
                transitions.c.actor == "worker-run-recovery",
                transitions.c.reason.ilike("%recover%"),
                transitions.c.reason.ilike("%lease expired%"),
            )
        ]
        if since is not None:
            clauses.append(transitions.c.created_at >= since)
        if company_id is not None:
            clauses.append(projects.c.company_id == company_id)
        query = sa.select(
            transitions.c.id.label("id"),
            transitions.c.from_state.label("from_state"),
            transitions.c.to_state.label("to_state"),
            transitions.c.actor.label("actor"),
            transitions.c.reason.label("reason"),
            transitions.c.created_at.label("occurred_at"),
        ).select_from(
            transitions.join(runs, transitions.c.run_id == runs.c.id).outerjoin(
                projects, runs.c.project_id == projects.c.id
            )
        ).where(sa.and_(*clauses)).order_by(transitions.c.created_at.asc()).limit(max(1, min(int(limit), 10_000)))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [
            {
                "id": str(row.get("id")),
                "status": "success" if str(row.get("to_state") or "").upper() == "QUEUED" else "failed",
                "occurred_at": row.get("occurred_at"),
                "source": "worker_run_transitions",
            }
            for row in rows
        ]

    async def list_project_usage_events_by_trace(
        self,
        trace_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """List bounded project usage rows for one trace ID."""

        query = (
            t.project_usage_events.select()
            .where(t.project_usage_events.c.trace_id == trace_id)
            .order_by(t.project_usage_events.c.occurred_at.asc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_worker_usage_records_by_trace(
        self,
        trace_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """List direct worker model-usage rows with a legacy run fallback."""

        usage = t.worker_usage_records
        events = t.project_usage_events
        # A run can emit multiple model/tool usage events.  Use an EXISTS
        # correlation instead of a join so one durable worker usage row is
        # never duplicated merely because its run has several spans.
        trace_match = sa.exists(
            sa.select(1)
            .select_from(events)
            .where(events.c.run_id == usage.c.run_id)
            .where(events.c.trace_id == trace_id)
        )
        query = (
            sa.select(
                usage.c.id,
                usage.c.run_id,
                usage.c.prompt_tokens,
                usage.c.completion_tokens,
                usage.c.total_tokens,
                usage.c.cost_usd,
                usage.c.duration_ms,
                usage.c.provider_id,
                usage.c.exact_model_id,
                usage.c.created_at,
                usage.c.trace_id,
                usage.c.span_id,
            )
            .where(sa.or_(usage.c.trace_id == trace_id, trace_match))
            .order_by(usage.c.created_at.asc())
            .limit(max(1, min(int(limit), 1000)))
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_worker_artifacts_by_trace(
        self,
        trace_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """List direct worker artifact metadata with a legacy run fallback."""

        artifacts = t.worker_artifacts
        events = t.project_usage_events
        trace_match = sa.exists(
            sa.select(1)
            .select_from(events)
            .where(events.c.run_id == artifacts.c.run_id)
            .where(events.c.trace_id == trace_id)
        )
        query = (
            sa.select(
                artifacts.c.id,
                artifacts.c.run_id,
                artifacts.c.artifact_id,
                artifacts.c.kind,
                artifacts.c.sha256,
                artifacts.c.size_bytes,
                artifacts.c.created_at,
                artifacts.c.trace_id,
                artifacts.c.span_id,
            )
            .where(sa.or_(artifacts.c.trace_id == trace_id, trace_match))
            .order_by(artifacts.c.created_at.asc())
            .limit(max(1, min(int(limit), 1000)))
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_pm_inbox_events_by_correlation(
        self,
        correlation_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """List bounded PM inbound metadata correlated to a trace/message ID."""

        query = (
            t.pm_inbox_events.select()
            .where(t.pm_inbox_events.c.correlation_id == correlation_id)
            .order_by(t.pm_inbox_events.c.received_at.asc())
            .limit(max(1, min(int(limit), 1000)))
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_integration_evidence_by_trace(
        self,
        trace_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """List payload-free integration evidence rows for one trace."""

        query = (
            sa.select(
                t.integration_evidence_records.c.id,
                t.integration_evidence_records.c.connection_id,
                t.integration_evidence_records.c.project_id,
                t.integration_evidence_records.c.evidence_type,
                t.integration_evidence_records.c.created_at,
                t.integration_evidence_records.c.trace_id,
                t.integration_evidence_records.c.span_id,
            )
            .where(t.integration_evidence_records.c.trace_id == trace_id)
            .order_by(t.integration_evidence_records.c.created_at.asc())
            .limit(max(1, min(int(limit), 1000)))
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def reserve_budget(
        self,
        *,
        company_id: UUID,
        budget_key: str,
        amount: Decimal | float | int,
        idempotency_key: str,
        project_id: UUID | None = None,
        worker_id: UUID | None = None,
        run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Reserve a company budget atomically.

        ``None`` means the company has no configured limit for this key.  A
        configured limit is fail-closed: reservations are locked and summed
        inside one transaction so concurrent workers cannot oversubscribe it.
        """
        normalized_amount = Decimal(str(amount))
        if not normalized_amount.is_finite() or normalized_amount < 0:
            raise ValueError("budget reservation amount must be finite and non-negative")
        if not budget_key.strip() or not idempotency_key.strip():
            raise ValueError("budget_key and idempotency_key are required")
        async with self.engine.begin() as conn:
            existing = (
                await conn.execute(
                    t.budget_reservations.select().where(
                        t.budget_reservations.c.idempotency_key == idempotency_key
                    )
                )
            ).mappings().first()
            if existing is not None:
                return dict(existing)
            budget = (
                await conn.execute(
                    t.company_budgets.select()
                    .where(
                        sa.and_(
                            t.company_budgets.c.company_id == company_id,
                            t.company_budgets.c.budget_key == budget_key,
                        )
                    )
                    .with_for_update()
                )
            ).mappings().first()
            if budget is None:
                return None
            reserved = (
                await conn.execute(
                    sa.select(sa.func.coalesce(sa.func.sum(t.budget_reservations.c.amount), 0))
                    .where(
                        sa.and_(
                            t.budget_reservations.c.company_id == company_id,
                            t.budget_reservations.c.budget_key == budget_key,
                            t.budget_reservations.c.state.in_(("RESERVED", "COMMITTED")),
                        )
                    )
                )
            ).scalar_one()
            limit_value = Decimal(str(budget["limit_value"]))
            if Decimal(str(reserved or 0)) + normalized_amount > limit_value:
                raise ValueError(
                    f"BUDGET_EXCEEDED:{budget_key}:limit={limit_value}:"
                    f"used={reserved}:requested={normalized_amount}"
                )
            values = {
                "id": uuid4(),
                "company_id": company_id,
                "project_id": project_id,
                "worker_id": worker_id,
                "run_id": run_id,
                "budget_key": budget_key,
                "amount": normalized_amount,
                "currency": str(budget.get("currency") or "USD"),
                "state": "RESERVED",
                "idempotency_key": idempotency_key,
                "metadata": metadata or {},
            }
            await conn.execute(t.budget_reservations.insert().values(**values))
        return values

    async def settle_budget_reservation(
        self,
        reservation_id: UUID,
        *,
        state: str,
        amount: Decimal | float | int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Commit actual usage or release a reservation exactly once.

        Cost settlement is serialized with new reservations by locking the
        company budget row before summing every other active reservation.  If
        reported usage exceeds the remaining cap, only the remaining amount is
        committed; the full billed amount and capped overage stay in metadata
        for audit/reconciliation without ever making the budget ledger exceed
        its configured limit.
        """
        if state not in {"COMMITTED", "RELEASED"}:
            raise ValueError("budget reservation state must be COMMITTED or RELEASED")
        committed_amount = Decimal(str(amount)) if amount is not None else None
        if committed_amount is not None and (
            state != "COMMITTED" or not committed_amount.is_finite() or committed_amount < 0
        ):
            raise ValueError("committed budget amount must be finite and non-negative")
        async with self.engine.begin() as conn:
            reservation_snapshot = (
                await conn.execute(
                    t.budget_reservations.select().where(t.budget_reservations.c.id == reservation_id)
                )
            ).mappings().first()
            if reservation_snapshot is None:
                return None
            if reservation_snapshot["state"] in {"COMMITTED", "RELEASED"}:
                return dict(reservation_snapshot)

            # Reserve and settle take this lock first, which prevents a
            # concurrent reservation from passing its cap check while this
            # settlement is reducing or committing the current amount.
            budget = (
                await conn.execute(
                    t.company_budgets.select()
                    .where(
                        sa.and_(
                            t.company_budgets.c.company_id == reservation_snapshot["company_id"],
                            t.company_budgets.c.budget_key == reservation_snapshot["budget_key"],
                        )
                    )
                    .with_for_update()
                )
            ).mappings().first()
            if budget is None and state == "COMMITTED":
                raise ValueError(
                    f"BUDGET_EXCEEDED:{reservation_snapshot['budget_key']}:configured budget is missing"
                )

            row = (
                await conn.execute(
                    t.budget_reservations.select()
                    .where(t.budget_reservations.c.id == reservation_id)
                    .with_for_update()
                )
            ).mappings().first()
            if row is None:
                return None
            if row["state"] in {"COMMITTED", "RELEASED"}:
                return dict(row)

            now = datetime.now(tz=UTC)
            values: dict[str, Any] = {"state": state}
            if committed_amount is not None:
                # The current reservation is excluded because its original
                # requested amount is being replaced by actual usage.
                other_reserved = (
                    await conn.execute(
                        sa.select(sa.func.coalesce(sa.func.sum(t.budget_reservations.c.amount), 0))
                        .where(
                            sa.and_(
                                t.budget_reservations.c.company_id == row["company_id"],
                                t.budget_reservations.c.budget_key == row["budget_key"],
                                t.budget_reservations.c.id != reservation_id,
                                t.budget_reservations.c.state.in_(
                                    ("RESERVED", "COMMITTED")
                                ),
                            )
                        )
                    )
                ).scalar_one()
                limit_value = Decimal(str(budget["limit_value"]))
                available = max(Decimal("0"), limit_value - Decimal(str(other_reserved or 0)))
                settled_amount = min(committed_amount, available)
                values["amount"] = settled_amount
                settlement_metadata = dict(row.get("metadata") or {})
                settlement_metadata.update(metadata or {})
                settlement_metadata["actual_cost_usd"] = str(committed_amount)
                if committed_amount > settled_amount:
                    settlement_metadata["budget_overage_usd"] = str(
                        committed_amount - settled_amount
                    )
                    settlement_metadata["budget_settlement"] = "CAP_EXCEEDED"
                values["metadata"] = settlement_metadata
            elif metadata:
                values["metadata"] = {**dict(row.get("metadata") or {}), **metadata}
            values["committed_at" if state == "COMMITTED" else "released_at"] = now
            await conn.execute(
                t.budget_reservations.update()
                .where(t.budget_reservations.c.id == reservation_id)
                .values(**values)
            )
            refreshed = (
                await conn.execute(
                    t.budget_reservations.select().where(t.budget_reservations.c.id == reservation_id)
                )
            ).mappings().first()
        return dict(refreshed) if refreshed is not None else None

    async def list_budget_reservations(
        self,
        *,
        run_id: UUID | None = None,
        company_id: UUID | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        query = (
            t.budget_reservations.select()
            .order_by(t.budget_reservations.c.created_at.asc())
            .limit(limit)
        )
        clauses = []
        if run_id is not None:
            clauses.append(t.budget_reservations.c.run_id == run_id)
        if company_id is not None:
            clauses.append(t.budget_reservations.c.company_id == company_id)
        if clauses:
            query = query.where(sa.and_(*clauses))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def list_terminal_worker_budget_reservations(
        self,
        *,
        limit: int = 100,
        after_created_at: datetime | None = None,
        after_reservation_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Read a bounded page of unsettled reservations for terminal runs.

        The keyset cursor lets a reconciler drain past an individual
        reservation whose settlement repeatedly fails. Only the scalar usage
        amount needed for canonical budget settlement is projected from the
        run result; task and provider payloads are never returned.
        """
        if not 1 <= limit <= 1000:
            raise ValueError("terminal budget reconciliation limit must be between 1 and 1000")
        if (after_created_at is None) != (after_reservation_id is None):
            raise ValueError("terminal budget reconciliation cursor is incomplete")

        reservations = t.budget_reservations
        runs = t.worker_runs
        query = (
            sa.select(
                reservations.c.id.label("reservation_id"),
                reservations.c.run_id,
                reservations.c.budget_key,
                reservations.c.created_at.label("reservation_created_at"),
                runs.c.state.label("run_state"),
                runs.c.result_json["usage"]["cost_usd"].astext.label("actual_cost_usd"),
            )
            .select_from(reservations.join(runs, reservations.c.run_id == runs.c.id))
            .where(
                sa.and_(
                    reservations.c.state == "RESERVED",
                    runs.c.state.in_(TERMINAL_WORKER_RUN_STATES),
                )
            )
            .order_by(reservations.c.created_at.asc(), reservations.c.id.asc())
            .limit(limit)
        )
        if after_created_at is not None and after_reservation_id is not None:
            query = query.where(
                sa.or_(
                    reservations.c.created_at > after_created_at,
                    sa.and_(
                        reservations.c.created_at == after_created_at,
                        reservations.c.id > after_reservation_id,
                    ),
                )
            )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_budget_state(self, company_id: UUID, budget_key: str) -> dict[str, Any]:
        """Return the authoritative limit, usage, and available balance."""
        async with self.engine.connect() as conn:
            budget = (
                await conn.execute(
                    t.company_budgets.select().where(
                        sa.and_(
                            t.company_budgets.c.company_id == company_id,
                            t.company_budgets.c.budget_key == budget_key,
                        )
                    )
                )
            ).mappings().first()
            if budget is None:
                return {"configured": False, "company_id": company_id, "budget_key": budget_key}
            total = (
                await conn.execute(
                    sa.select(sa.func.coalesce(sa.func.sum(t.budget_reservations.c.amount), 0)).where(
                        sa.and_(
                            t.budget_reservations.c.company_id == company_id,
                            t.budget_reservations.c.budget_key == budget_key,
                            t.budget_reservations.c.state.in_(("RESERVED", "COMMITTED")),
                        )
                    )
                )
            ).scalar_one()
        limit_value = Decimal(str(budget["limit_value"]))
        used = Decimal(str(total or 0))
        return {
            "configured": True,
            "company_id": company_id,
            "budget_key": budget_key,
            "limit": limit_value,
            "used": used,
            "available": max(Decimal("0"), limit_value - used),
            "currency": budget.get("currency") or "USD",
            "period": budget.get("period") or "lifetime",
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Task log (task execution audit trail)
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_task_log(
        self,
        *,
        task_id: UUID,
        agent_id: str,
        team_id: str,
        status: str,
        parent_task_id: UUID | None = None,
        input_data: dict | None = None,
        output_data: dict | None = None,
        budget_snapshot: dict | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a task log entry."""
        now = datetime.now(tz=UTC)
        values = {
            "task_id": task_id,
            "agent_id": agent_id,
            "parent_task_id": parent_task_id,
            "team_id": team_id,
            "status": status,
            "input": input_data,
            "output": output_data,
            "budget_snapshot": budget_snapshot,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.task_log.insert().values(**values))
        return values

    async def get_task_log(self, task_id: UUID) -> dict[str, Any] | None:
        """Fetch a task log entry by task ID."""
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(t.task_log.select().where(t.task_log.c.task_id == task_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def list_task_logs(
        self,
        *,
        agent_id: str | None = None,
        team_id: str | None = None,
        status: str | None = None,
        trace_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List task log entries with optional filters."""
        q = t.task_log.select()
        if agent_id:
            q = q.where(t.task_log.c.agent_id == agent_id)
        if team_id:
            q = q.where(t.task_log.c.team_id == team_id)
        if status:
            q = q.where(t.task_log.c.status == status)
        if trace_id:
            q = q.where(t.task_log.c.trace_id == trace_id)
        q = q.order_by(t.task_log.c.created_at.desc()).limit(limit)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def update_task_log(
        self,
        task_id: UUID,
        **kwargs: Any,
    ) -> None:
        """Update task log fields (status, output, budget_snapshot, etc.)."""
        kwargs["updated_at"] = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            await conn.execute(
                t.task_log.update().where(t.task_log.c.task_id == task_id).values(**kwargs)
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Native payload-free trace spans
    # ═══════════════════════════════════════════════════════════════════════════

    async def _insert_native_trace_span_tx(
        self,
        conn: Any,
        **fields: Any,
    ) -> dict[str, Any]:
        """Insert or finish one native span inside an existing transaction."""

        values = build_native_trace_span(**fields)
        values["attributes_json"] = values.pop("attributes", {})
        statement = (
            pg_insert(t.native_trace_spans)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_native_trace_span",
                set_={
                    "parent_span_id": values["parent_span_id"],
                    "source_kind": values["source_kind"],
                    "operation": values["operation"],
                    "service": values["service"],
                    "status": values["status"],
                    "started_at": values["started_at"],
                    "ended_at": values["ended_at"],
                    "duration_ms": values["duration_ms"],
                    "sampled": values["sampled"],
                    "retention_until": values["retention_until"],
                    "attributes_json": values["attributes_json"],
                },
            )
        )
        await conn.execute(statement)
        return values

    async def create_native_trace_span(self, **fields: Any) -> dict[str, Any]:
        """Persist one normalized native span without accepting payload data."""

        async with self.engine.begin() as conn:
            return await self._insert_native_trace_span_tx(conn, **fields)

    async def list_native_trace_spans_by_trace(
        self,
        trace_id: str,
        *,
        limit: int = 1_000,
    ) -> list[dict[str, Any]]:
        """List bounded native spans for one trace in causal time order."""

        query = (
            t.native_trace_spans.select()
            .where(t.native_trace_spans.c.trace_id == str(trace_id).strip())
            .order_by(
                t.native_trace_spans.c.started_at.asc(),
                t.native_trace_spans.c.span_id.asc(),
            )
            .limit(max(1, min(int(limit), 2_000)))
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    # ═══════════════════════════════════════════════════════════════════════════
    # Bounded API request observations
    # ═══════════════════════════════════════════════════════════════════════════

    async def record_api_request_observation(self, **fields: Any) -> dict[str, Any]:
        """Persist one payload-free API request observation.

        The observability normalizer is the single boundary for route, status,
        duration, trace, and principal bounds.  No request/response payload,
        headers, query strings, credentials, or exception text can enter this
        table through this method.
        """

        from mas_core.observability.api_observations import build_api_observation

        values = build_api_observation(**fields)
        async with self.engine.begin() as conn:
            await conn.execute(t.api_request_observations.insert().values(**values))
            if values.get("trace_id"):
                await self._insert_native_trace_span_tx(
                    conn,
                    trace_id=values["trace_id"],
                    span_id=new_span_id(),
                    parent_span_id=current_span_id(),
                    source_kind="transport",
                    operation=values["route"],
                    service=values.get("source") or "orchestrator_api",
                    status=values["outcome"],
                    started_at=values["occurred_at"] - timedelta(milliseconds=float(values["duration_ms"])),
                    ended_at=values["occurred_at"],
                    duration_ms=values["duration_ms"],
                    attributes={
                        "method": values["method"],
                        "route": values["route"],
                        "status_code": values["status_code"],
                        "outcome": values["outcome"],
                    },
                )
        return values

    async def list_api_request_observations(
        self,
        *,
        trace_id: str | None = None,
        since: datetime | None = None,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        """List bounded API observations for trace and operational read models."""

        query = t.api_request_observations.select()
        if trace_id:
            query = query.where(t.api_request_observations.c.trace_id == trace_id)
        if since is not None:
            query = query.where(t.api_request_observations.c.occurred_at >= since)
        query = query.order_by(t.api_request_observations.c.occurred_at.asc()).limit(
            max(1, min(int(limit), 20_000))
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    # ═══════════════════════════════════════════════════════════════════════════
    # Artifacts (blob metadata)
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_artifact(
        self,
        *,
        agent_id: str,
        path: str,
        metadata: dict | None = None,
        sha256: str | None = None,
        size_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Register a new artifact (blob metadata pointer)."""
        now = datetime.now(tz=UTC)
        values = {
            "agent_id": agent_id,
            "path": path,
            "metadata": metadata,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.artifacts.insert().values(**values).returning(t.artifacts.c.id)
            )
            row = result.first()
            if row is not None:
                values["id"] = row[0]
        return values

    async def get_artifact(self, artifact_id: int) -> dict[str, Any] | None:
        """Fetch an artifact by ID."""
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(t.artifacts.select().where(t.artifacts.c.id == artifact_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def list_artifacts(
        self,
        *,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List artifacts, optionally filtered by agent."""
        q = t.artifacts.select()
        if agent_id:
            q = q.where(t.artifacts.c.agent_id == agent_id)
        q = q.order_by(t.artifacts.c.created_at.desc()).limit(limit)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def delete_artifact(self, artifact_id: int) -> None:
        """Delete an artifact entry."""
        async with self.engine.begin() as conn:
            await conn.execute(t.artifacts.delete().where(t.artifacts.c.id == artifact_id))

    # ═══════════════════════════════════════════════════════════════════════════
    # Infrastructure events (DevOps tracking)
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_infra_event(
        self,
        *,
        project_id: UUID,
        event_type: str,
        sprint_id: UUID | None = None,
        details: dict | None = None,
        event_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Record an infrastructure event."""
        eid = event_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": eid,
            "project_id": project_id,
            "sprint_id": sprint_id,
            "event_type": event_type,
            "details": details,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.infra_events.insert().values(**values))
        return values

    async def list_infra_events(
        self,
        *,
        project_id: UUID | None = None,
        sprint_id: UUID | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List infra events with optional filters."""
        q = t.infra_events.select()
        if project_id:
            q = q.where(t.infra_events.c.project_id == project_id)
        if sprint_id:
            q = q.where(t.infra_events.c.sprint_id == sprint_id)
        if event_type:
            q = q.where(t.infra_events.c.event_type == event_type)
        q = q.order_by(t.infra_events.c.created_at.desc()).limit(limit)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════════
    # Capabilities (capability catalog)
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_capability(
        self,
        *,
        name: str,
        version: str = "1.0",
        description: str | None = None,
        input_schema: dict | None = None,
        output_schema: dict | None = None,
        risk_level: str = "low",
        cost_model: dict | None = None,
        required_tools: list[str] | None = None,
        required_role: str | None = None,
        capability_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Register a capability in the catalog."""
        cid = capability_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": cid,
            "name": name,
            "version": version,
            "description": description,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "risk_level": risk_level,
            "cost_model": cost_model,
            "required_tools": required_tools or [],
            "required_role": required_role,
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.capabilities.insert().values(**values))
        return values

    async def get_capability(self, capability_id: UUID) -> dict[str, Any] | None:
        """Fetch a capability by ID."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        t.capabilities.select().where(t.capabilities.c.id == capability_id)
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def get_capability_by_name(self, name: str) -> dict[str, Any] | None:
        """Fetch a capability by unique name."""
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(t.capabilities.select().where(t.capabilities.c.name == name)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def list_capabilities(
        self,
        *,
        risk_level: str | None = None,
        required_role: str | None = None,
    ) -> list[dict[str, Any]]:
        """List capabilities with optional filters."""
        q = t.capabilities.select()
        if risk_level:
            q = q.where(t.capabilities.c.risk_level == risk_level)
        if required_role:
            q = q.where(t.capabilities.c.required_role == required_role)
        q = q.order_by(t.capabilities.c.name)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def delete_capability(self, capability_id: UUID) -> None:
        """Delete a capability from the catalog."""
        async with self.engine.begin() as conn:
            await conn.execute(t.capabilities.delete().where(t.capabilities.c.id == capability_id))

    # ═══════════════════════════════════════════════════════════════════════════
    # Worker registry
    # ═══════════════════════════════════════════════════════════════════════════

    async def register_worker(
        self,
        *,
        name: str,
        adapter_type: str,
        adapter_config: dict | None = None,
        sandbox_profile: str = "trusted",
        capability_ids: list[UUID] | None = None,
        team_id: str | None = None,
        status: str = "ACTIVE",
        worker_id: UUID | None = None,
        version: str | None = None,
        source_repo: str | None = None,
        source_revision: str | None = None,
        version_pin: str | None = None,
        update_policy: str = "manual",
        evaluation_status: str | None = None,
        adapter_entrypoint: str = "WorkerAgent",
        adapter_module: str | None = None,
        wrapper_config: dict | None = None,
        isolation_mode: str = "native",
        active_shell_version_id: UUID | None = None,
        active_adapter_id: UUID | None = None,
        active_skill_bundle_id: UUID | None = None,
        model_profile_id: str | None = None,
        model_mode: str | None = None,
    ) -> dict[str, Any]:
        """Register or re-register a worker (upsert on name)."""
        wid = worker_id or uuid4()
        now = datetime.now(tz=UTC)
        canonical_profile = canonical_sandbox_class(sandbox_profile)
        values = {
            "id": wid,
            "name": name,
            "adapter_type": adapter_type,
            "adapter_config": adapter_config or {},
            "sandbox_profile": canonical_profile,
            "capability_ids": capability_ids or [],
            "team_id": team_id,
            "status": status,
            "version": version,
            "source_repo": source_repo,
            "source_revision": source_revision,
            "version_pin": version_pin,
            "update_policy": update_policy,
            "evaluation_status": evaluation_status,
            "adapter_entrypoint": adapter_entrypoint,
            "adapter_module": adapter_module,
            "wrapper_config": wrapper_config or {},
            "isolation_mode": isolation_mode,
            "active_shell_version_id": active_shell_version_id,
            "active_adapter_id": active_adapter_id,
            "active_skill_bundle_id": active_skill_bundle_id,
            "model_profile_id": model_profile_id,
            "model_mode": model_mode or "none",
            "created_at": now,
            "updated_at": now,
        }
        updates = {
            "adapter_type": adapter_type,
            "adapter_config": adapter_config or {},
            "sandbox_profile": canonical_profile,
            "capability_ids": capability_ids or [],
            "team_id": team_id,
            "status": status,
            "version": version,
            "source_repo": source_repo,
            "source_revision": source_revision,
            "version_pin": version_pin,
            "update_policy": update_policy,
            "evaluation_status": evaluation_status,
            "adapter_entrypoint": adapter_entrypoint,
            "adapter_module": adapter_module,
            "wrapper_config": wrapper_config or {},
            "isolation_mode": isolation_mode,
            "updated_at": now,
        }
        # Re-registration must not silently unpin a governed worker.  The
        # control plane changes these pointers explicitly during approval and
        # rollout; startup registration only writes them when supplied.
        for key, value in (
            ("active_shell_version_id", active_shell_version_id),
            ("active_adapter_id", active_adapter_id),
            ("active_skill_bundle_id", active_skill_bundle_id),
            ("model_profile_id", model_profile_id),
            ("model_mode", model_mode),
        ):
            if value is not None:
                updates[key] = value
        stmt = (
            pg_insert(t.worker_registry)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_worker_registry_name",
                set_=updates,
            )
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)
        # The insert may have conflicted on the worker name. Return the
        # canonical persisted row so callers cannot attach governance records
        # to the throw-away UUID generated for a re-registration.
        persisted = await self.get_worker_by_name(name)
        return persisted or values

    async def get_worker(self, worker_id: UUID) -> dict[str, Any] | None:
        """Fetch a worker by ID."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        t.worker_registry.select().where(t.worker_registry.c.id == worker_id)
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def get_worker_by_name(self, name: str) -> dict[str, Any] | None:
        """Fetch a worker by unique name."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        t.worker_registry.select().where(t.worker_registry.c.name == name)
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def observe_agent_profile(
        self,
        *,
        agent_id: str,
        team_id: str | None = None,
        role: str | None = None,
        estimated_hours: Decimal | float | int | None = None,
        actual_hours: Decimal | float | int | None = None,
        tasks_completed: int = 1,
        alpha: Decimal | float | int = Decimal("0.5"),
    ) -> dict[str, Any]:
        """Apply one completed-work observation to an agent's profile.

        The profile uses a deliberately small, deterministic EMA rather than a
        hidden model: ``observed_ratio = actual / estimate`` and
        ``new_factor = alpha * observed_ratio + (1-alpha) * old_factor``.
        The additive estimation bias is updated with the same EMA.  This keeps
        the learning signal explainable and lets a later estimate use the
        durable profile without consulting process memory.
        """
        if not agent_id:
            raise ValueError("agent_id is required")
        if tasks_completed < 0:
            raise ValueError("tasks_completed must be non-negative")
        a = Decimal(str(alpha))
        if a <= 0 or a > 1:
            raise ValueError("alpha must be > 0 and <= 1")

        estimate = Decimal(str(estimated_hours if estimated_hours is not None else 0))
        actual = Decimal(str(actual_hours if actual_hours is not None else 0))
        if estimate < 0 or actual < 0:
            raise ValueError("estimated_hours and actual_hours must be non-negative")

        existing = await self.get_agent_profile(agent_id)
        old_factor = Decimal(str((existing or {}).get("correction_factor", "1.0")))
        old_bias = Decimal(str((existing or {}).get("estimation_bias", "0.0")))
        old_confidence = Decimal(str((existing or {}).get("confidence", "0.5")))
        prior_tasks = int((existing or {}).get("total_tasks_completed") or 0)
        prior_estimated = Decimal(str((existing or {}).get("total_estimated_hours", "0") or 0))
        prior_actual = Decimal(str((existing or {}).get("total_actual_hours", "0") or 0))

        if estimate > 0:
            observed_ratio = actual / estimate
            observed_bias = actual - estimate
            factor = (a * observed_ratio) + ((Decimal("1") - a) * old_factor)
            bias = (a * observed_bias) + ((Decimal("1") - a) * old_bias)
        else:
            observed_ratio = old_factor
            observed_bias = Decimal("0")
            factor = old_factor
            bias = old_bias

        # Keep values inside the AgentProfile contract and the database
        # numeric bounds while retaining four decimal places of evidence.
        factor = min(_AGENT_PROFILE_NUMERIC_MAX, max(Decimal("0.1"), factor)).quantize(
            Decimal("0.0001")
        )
        bias = min(_AGENT_PROFILE_NUMERIC_MAX, max(_AGENT_PROFILE_NUMERIC_MIN, bias)).quantize(
            Decimal("0.0001")
        )
        confidence = min(
            Decimal("1"),
            old_confidence + ((Decimal("1") - old_confidence) * a if tasks_completed else Decimal("0")),
        ).quantize(Decimal("0.0001"))
        completed = prior_tasks + tasks_completed
        profile = await self.upsert_agent_profile(
            agent_id=agent_id,
            team_id=team_id or (existing or {}).get("team_id") or "unassigned",
            role=role or (existing or {}).get("role") or "worker",
            correction_factor=factor,
            estimation_bias=bias,
            confidence=confidence,
            total_tasks_completed=completed,
            total_estimated_hours=prior_estimated + estimate,
            total_actual_hours=prior_actual + actual,
        )
        profile.update(
            {
                "observed_ratio": observed_ratio,
                "observed_bias": observed_bias,
                "alpha": a,
                "previous_correction_factor": old_factor,
                "previous_estimation_bias": old_bias,
            }
        )
        return profile

    async def list_review_sessions(
        self,
        project_id: UUID,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List review sessions for a project, newest first."""
        async with self.engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        t.review_sessions.select()
                        .where(t.review_sessions.c.project_id == project_id)
                        .order_by(t.review_sessions.c.created_at.desc())
                        .limit(limit)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(row) for row in rows]

    async def delete_worker(self, worker_id: UUID) -> bool:
        """Permanently delete a worker registry row and owned evaluation reports."""
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.worker_registry.delete().where(t.worker_registry.c.id == worker_id)
            )
            return bool(result.rowcount)

    async def list_workers(
        self,
        *,
        team_id: str | None = None,
        status: str | None = None,
        adapter_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """List workers with optional filters."""
        q = t.worker_registry.select()
        if team_id:
            q = q.where(t.worker_registry.c.team_id == team_id)
        if status:
            q = q.where(t.worker_registry.c.status == status)
        if adapter_type:
            q = q.where(t.worker_registry.c.adapter_type == adapter_type)
        q = q.order_by(t.worker_registry.c.name)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def update_worker_status(
        self,
        worker_id: UUID,
        *,
        status: str,
    ) -> None:
        """Update a worker's status."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.worker_registry.update()
                .where(t.worker_registry.c.id == worker_id)
                .values(
                    status=status,
                    updated_at=datetime.now(tz=UTC),
                )
            )

    async def update_worker_config(
        self,
        worker_id: UUID,
        *,
        adapter_type: str | None = None,
        adapter_config: dict | None = None,
        sandbox_profile: str | None = None,
        capability_ids: list[UUID] | None = None,
        team_id: str | None = None,
        version: str | None = None,
        version_pin: str | None = None,
        update_policy: str | None = None,
        evaluation_status: str | None = None,
        adapter_entrypoint: str | None = None,
        adapter_module: str | None = None,
        wrapper_config: dict | None = None,
        isolation_mode: str | None = None,
        active_shell_version_id: UUID | None = None,
        active_adapter_id: UUID | None = None,
        active_skill_bundle_id: UUID | None = None,
        model_profile_id: str | None = None,
        model_mode: str | None = None,
    ) -> None:
        """Update a worker's configuration fields (partial update)."""
        values: dict[str, Any] = {"updated_at": datetime.now(tz=UTC)}
        if adapter_type is not None:
            values["adapter_type"] = adapter_type
        if adapter_config is not None:
            values["adapter_config"] = adapter_config
        if sandbox_profile is not None:
            values["sandbox_profile"] = canonical_sandbox_class(sandbox_profile)
        if capability_ids is not None:
            values["capability_ids"] = capability_ids
        if team_id is not None:
            values["team_id"] = team_id
        if version is not None:
            values["version"] = version
        if version_pin is not None:
            values["version_pin"] = version_pin
        if update_policy is not None:
            values["update_policy"] = update_policy
        if evaluation_status is not None:
            values["evaluation_status"] = evaluation_status
        if adapter_entrypoint is not None:
            values["adapter_entrypoint"] = adapter_entrypoint
        if adapter_module is not None:
            values["adapter_module"] = adapter_module
        if wrapper_config is not None:
            values["wrapper_config"] = wrapper_config
        if isolation_mode is not None:
            values["isolation_mode"] = isolation_mode
        if active_shell_version_id is not None:
            values["active_shell_version_id"] = active_shell_version_id
        if active_adapter_id is not None:
            values["active_adapter_id"] = active_adapter_id
        if active_skill_bundle_id is not None:
            values["active_skill_bundle_id"] = active_skill_bundle_id
        if model_profile_id is not None:
            values["model_profile_id"] = model_profile_id
        if model_mode is not None:
            values["model_mode"] = model_mode
        if len(values) == 1:
            return
        async with self.engine.begin() as conn:
            await conn.execute(
                t.worker_registry.update()
                .where(t.worker_registry.c.id == worker_id)
                .values(**values)
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Identity-service laptop reconciliation and lifecycle mirror
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_identity_reconciliation_cursor(self, client_id: str) -> int:
        """Return the durable event cursor for the signed laptop client."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text("SELECT last_sequence FROM identity_reconciliation_cursors WHERE client_id = :client_id"),
                    {"client_id": client_id},
                )
            ).mappings().first()
        return int(row["last_sequence"]) if row else 0

    async def set_identity_reconciliation_cursor(self, client_id: str, cursor: int) -> None:
        """Advance the local cursor only after complete event processing."""
        if cursor < 0:
            raise ValueError("identity reconciliation cursor must be non-negative")
        async with self.engine.begin() as conn:
            await conn.execute(
                sa.text(
                    """INSERT INTO identity_reconciliation_cursors (client_id, last_sequence, updated_at)
                       VALUES (:client_id, :cursor, now())
                       ON CONFLICT (client_id) DO UPDATE
                       SET last_sequence = GREATEST(identity_reconciliation_cursors.last_sequence, EXCLUDED.last_sequence),
                           updated_at = now()"""
                ),
                {"client_id": client_id, "cursor": cursor},
            )

    async def get_worker_identity_lifecycle(self, worker_id: UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    sa.text("SELECT * FROM worker_identity_lifecycle WHERE worker_id = :worker_id"),
                    {"worker_id": worker_id},
                )
            ).mappings().first()
        return dict(row) if row else None

    async def upsert_worker_identity_lifecycle(
        self,
        *,
        worker_id: UUID,
        state: str,
        provisioning_key: str | None = None,
        identity_address: str | None = None,
        identity_service_id: UUID | None = None,
        last_event_sequence: int | None = None,
        failure_code: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Mirror safe identity state without copying secrets from the VPS."""
        values = {
            "worker_id": worker_id,
            "state": state,
            "provisioning_key": provisioning_key,
            "identity_address": identity_address,
            "identity_service_id": identity_service_id,
            "last_event_sequence": last_event_sequence or 0,
            "failure_code": failure_code,
            "evidence": evidence or {},
        }
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.text(
                    """INSERT INTO worker_identity_lifecycle
                         (worker_id, state, provisioning_key, identity_address, identity_service_id, last_event_sequence, failure_code, evidence_json)
                       VALUES (:worker_id, :state, :provisioning_key, :identity_address, :identity_service_id, :last_event_sequence, :failure_code, CAST(:evidence AS jsonb))
                       ON CONFLICT (worker_id) DO UPDATE SET
                         state = EXCLUDED.state,
                         provisioning_key = COALESCE(EXCLUDED.provisioning_key, worker_identity_lifecycle.provisioning_key),
                         identity_address = COALESCE(EXCLUDED.identity_address, worker_identity_lifecycle.identity_address),
                         identity_service_id = COALESCE(EXCLUDED.identity_service_id, worker_identity_lifecycle.identity_service_id),
                         last_event_sequence = GREATEST(worker_identity_lifecycle.last_event_sequence, EXCLUDED.last_event_sequence),
                         failure_code = EXCLUDED.failure_code,
                         evidence_json = EXCLUDED.evidence_json,
                         updated_at = now()
                       RETURNING *"""
                ),
                {**values, "evidence": json.dumps(evidence or {}, default=str)},
            )
            row = result.mappings().first()
        return dict(row)  # type: ignore[arg-type]

    async def set_worker_governed_versions(
        self,
        worker_id: UUID,
        *,
        active_shell_version_id: UUID | None,
        active_adapter_id: UUID | None,
        active_skill_bundle_id: UUID | None,
    ) -> None:
        """Atomically replace the mutable active-version pointers."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.worker_registry.update()
                .where(t.worker_registry.c.id == worker_id)
                .values(
                    active_shell_version_id=active_shell_version_id,
                    active_adapter_id=active_adapter_id,
                    active_skill_bundle_id=active_skill_bundle_id,
                    updated_at=datetime.now(tz=UTC),
                )
            )

    async def update_worker_health(
        self,
        worker_id: UUID,
        *,
        health_status: str | None = None,
        error_count: int | None = None,
    ) -> None:
        """Update a worker's health status and/or error count."""
        values: dict[str, Any] = {
            "last_seen_at": datetime.now(tz=UTC),
            "updated_at": datetime.now(tz=UTC),
        }
        if health_status is not None:
            values["health_status"] = health_status
        if error_count is not None:
            values["error_count"] = error_count
        async with self.engine.begin() as conn:
            await conn.execute(
                t.worker_registry.update()
                .where(t.worker_registry.c.id == worker_id)
                .values(**values)
            )

    async def update_worker_upstream(
        self,
        worker_id: UUID,
        *,
        last_upstream_sync: datetime | None = None,
        upstream_commit_sha: str | None = None,
        source_revision: str | None = None,
    ) -> None:
        """Update a worker's upstream tracking fields."""
        values: dict[str, Any] = {"updated_at": datetime.now(tz=UTC)}
        if last_upstream_sync is not None:
            values["last_upstream_sync"] = last_upstream_sync
        if upstream_commit_sha is not None:
            values["upstream_commit_sha"] = upstream_commit_sha
        if source_revision is not None:
            values["source_revision"] = source_revision
        async with self.engine.begin() as conn:
            await conn.execute(
                t.worker_registry.update()
                .where(t.worker_registry.c.id == worker_id)
                .values(**values)
            )

    async def create_evaluation_report(
        self,
        *,
        worker_id: UUID,
        checks: dict[str, Any],
        overall_score: float | None = None,
        verdict: str = "PENDING",
        evaluator_version: str | None = None,
        risk_tier: str = "unknown",
        blocked_reasons: list[str] | None = None,
        recommended_status: str = "PENDING_EVALUATION",
        requires_human_approval: bool = False,
        notes: str | None = None,
        report_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Store an evaluation report for a worker."""
        rid = report_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": rid,
            "worker_id": worker_id,
            "evaluated_at": now,
            "checks": checks,
            "overall_score": overall_score,
            "verdict": verdict,
            "evaluator_version": evaluator_version,
            "risk_tier": risk_tier,
            "blocked_reasons": blocked_reasons or [],
            "recommended_status": recommended_status,
            "requires_human_approval": requires_human_approval,
            "notes": notes,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.evaluation_reports.insert().values(**values))
        return values

    async def get_evaluation_reports(
        self,
        worker_id: UUID,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get evaluation reports for a worker, newest first."""
        q = (
            t.evaluation_reports.select()
            .where(t.evaluation_reports.c.worker_id == worker_id)
            .order_by(t.evaluation_reports.c.evaluated_at.desc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════════════════════════════════
    # Role-capability map
    # ═══════════════════════════════════════════════════════════════════════════

    async def bind_role_capability(
        self,
        *,
        role: str,
        capability_id: UUID,
        priority: int = 0,
        constraints: dict | None = None,
    ) -> dict[str, Any]:
        """Bind a role to a capability (upsert on unique role+capability)."""
        stmt = (
            pg_insert(t.role_capability_map)
            .values(
                role=role,
                capability_id=capability_id,
                priority=priority,
                constraints=constraints,
            )
            .on_conflict_do_update(
                constraint="uq_role_capability",
                set_={
                    "priority": priority,
                    "constraints": constraints,
                },
            )
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)
        return {
            "role": role,
            "capability_id": capability_id,
            "priority": priority,
            "constraints": constraints,
        }

    async def list_role_capabilities(
        self,
        role: str,
    ) -> list[dict[str, Any]]:
        """List all capabilities bound to a role, ordered by priority (desc)."""
        q = (
            t.role_capability_map.select()
            .where(t.role_capability_map.c.role == role)
            .order_by(t.role_capability_map.c.priority.desc())
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def list_capability_roles(
        self,
        capability_id: UUID,
    ) -> list[dict[str, Any]]:
        """List all roles that have a given capability."""
        q = (
            t.role_capability_map.select()
            .where(t.role_capability_map.c.capability_id == capability_id)
            .order_by(t.role_capability_map.c.priority.desc())
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def unbind_role_capability(
        self,
        *,
        role: str,
        capability_id: UUID,
    ) -> None:
        """Remove a role-to-capability binding."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.role_capability_map.delete().where(
                    sa.and_(
                        t.role_capability_map.c.role == role,
                        t.role_capability_map.c.capability_id == capability_id,
                    )
                )
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # Flows
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_flow(
        self,
        *,
        name: str,
        description: str | None = None,
        definition_json: dict,
        created_by: str = "system",
        is_active: bool = False,
        version: int = 1,
    ) -> dict[str, Any]:
        """Create a new flow definition."""
        now = datetime.now(tz=UTC)
        values = {
            "id": uuid4(),
            "name": name,
            "description": description,
            "definition_json": definition_json,
            "version": version,
            "created_by": created_by,
            "is_active": is_active,
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.flows.insert().values(**values))
        return values

    async def get_flow(self, flow_id: UUID) -> dict[str, Any] | None:
        """Fetch a flow by ID."""
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(t.flows.select().where(t.flows.c.id == flow_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def update_flow(
        self,
        flow_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        definition_json: dict | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update a flow definition. Increments version on definition change."""
        now = datetime.now(tz=UTC)
        flow = await self.get_flow(flow_id)
        if flow is None:
            return None

        updates: dict[str, Any] = {"updated_at": now}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if definition_json is not None:
            updates["definition_json"] = definition_json
            updates["version"] = flow["version"] + 1
        if is_active is not None:
            updates["is_active"] = is_active

        async with self.engine.begin() as conn:
            await conn.execute(t.flows.update().where(t.flows.c.id == flow_id).values(**updates))

        return await self.get_flow(flow_id)

    async def list_flows(
        self,
        *,
        is_active: bool | None = None,
        created_by: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List flows, optionally filtered."""
        q = t.flows.select().order_by(t.flows.c.updated_at.desc())
        if is_active is not None:
            q = q.where(t.flows.c.is_active == is_active)
        if created_by is not None:
            q = q.where(t.flows.c.created_by == created_by)
        q = q.limit(limit).offset(offset)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def delete_flow(self, flow_id: UUID) -> bool:
        """Delete a flow. Returns True if deleted."""
        async with self.engine.begin() as conn:
            result = await conn.execute(t.flows.delete().where(t.flows.c.id == flow_id))
        return result.rowcount > 0

    # Flow Instances

    async def create_flow_instance(
        self,
        *,
        flow_id: UUID,
        flow_version: int,
        project_id: UUID,
        task_id: UUID | None = None,
        department_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Create a new flow instance attached to a project."""
        now = datetime.now(tz=UTC)
        values = {
            "id": uuid4(),
            "flow_id": flow_id,
            "flow_version": flow_version,
            "project_id": project_id,
            "task_id": task_id,
            "department_id": department_id,
            "active_node_ids": [],
            "status": "NOT_STARTED",
            "context_json": {},
            "created_at": now,
            "updated_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.flow_instances.insert().values(**values))
        return values

    async def get_flow_instance(self, instance_id: UUID) -> dict[str, Any] | None:
        """Fetch a flow instance by ID."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        t.flow_instances.select().where(t.flow_instances.c.id == instance_id)
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def get_flow_instance_by_project(self, project_id: UUID) -> dict[str, Any] | None:
        """Fetch the latest flow instance for a project, including terminal runs."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        t.flow_instances.select()
                        .where(t.flow_instances.c.project_id == project_id)
                        .order_by(t.flow_instances.c.updated_at.desc())
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def update_flow_instance(
        self,
        instance_id: UUID,
        *,
        status: str | None = None,
        active_node_ids: list[str] | None = None,
        context_json: dict | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        retry_count: int | None = None,
        max_retries: int | None = None,
        escalated_to: str | None = None,
        escalation_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Update a flow instance."""
        now = datetime.now(tz=UTC)
        updates: dict[str, Any] = {"updated_at": now}
        if status is not None:
            updates["status"] = status
        if active_node_ids is not None:
            updates["active_node_ids"] = active_node_ids
        if context_json is not None:
            updates["context_json"] = context_json
        if started_at is not None:
            updates["started_at"] = started_at
        if completed_at is not None:
            updates["completed_at"] = completed_at
        if retry_count is not None:
            updates["retry_count"] = retry_count
        if max_retries is not None:
            updates["max_retries"] = max_retries
        if escalated_to is not None:
            updates["escalated_to"] = escalated_to
        if escalation_reason is not None:
            updates["escalation_reason"] = escalation_reason

        async with self.engine.begin() as conn:
            await conn.execute(
                t.flow_instances.update()
                .where(t.flow_instances.c.id == instance_id)
                .values(**updates)
            )

        return await self.get_flow_instance(instance_id)

    async def cancel_flow_instance(
        self,
        instance_id: UUID,
        *,
        expected_instance_snapshot: dict[str, Any] | None = None,
        decided_by: str = "system:flow_instance_cancel",
        reason: str = "Flow instance cancelled",
    ) -> dict[str, Any]:
        """Cancel an instance and its bound approval gates atomically.

        Locks are acquired in gate -> execution -> instance order so an
        operator decision racing cancellation cannot deadlock or leave an
        actionable approval attached to a cancelled flow.
        """
        from mas_core.workflow import parse_flow_definition

        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            snapshot = expected_instance_snapshot
            if not snapshot or not {
                "project_id",
                "flow_id",
                "flow_version",
                "active_node_ids",
            }.issubset(snapshot):
                current_row = (
                    await conn.execute(
                        t.flow_instances.select().where(t.flow_instances.c.id == instance_id)
                    )
                ).mappings().first()
                if current_row is None:
                    return {"outcome": "not_found"}
                snapshot = dict(current_row)

            active_node_ids = [str(node_id) for node_id in (snapshot.get("active_node_ids") or [])]
            project_id = snapshot.get("project_id")
            flow_id = snapshot.get("flow_id")
            flow_version = snapshot.get("flow_version")
            if project_id is not None and flow_id is not None:
                flow_row = (
                    await conn.execute(t.flows.select().where(t.flows.c.id == flow_id))
                ).mappings().first()
                approval_execution_rows = (
                    await conn.execute(
                        t.flow_node_executions.select()
                        .where(t.flow_node_executions.c.instance_id == instance_id)
                        .where(t.flow_node_executions.c.node_id.in_(active_node_ids))
                        .where(t.flow_node_executions.c.node_type == "approval")
                        .where(t.flow_node_executions.c.status == "RUNNING")
                    )
                ).mappings().all()
                gate_types: set[str] = set()
                definition_known = (
                    flow_row is not None
                    and int(flow_row.get("version") or 0) == int(flow_version or 0)
                )
                definition = None
                if definition_known:
                    try:
                        definition = parse_flow_definition(flow_row["definition_json"])
                    except (KeyError, TypeError, ValueError):
                        definition_known = False
                if definition_known and definition is not None:
                    possible_approval_node_ids = set(active_node_ids) | {
                        str(row.get("node_id") or "") for row in approval_execution_rows
                    }
                    for node_id in possible_approval_node_ids:
                        node = definition.get_node(node_id)
                        if node is None:
                            if node_id in active_node_ids:
                                definition_known = False
                                break
                            continue
                        if node.type.value != "approval":
                            continue
                        gate_type = (
                            node.config.get("approver_role")
                            or node.config.get("approver_user")
                            or node.label
                        )
                        gate_types.add(str(gate_type))
                if gate_types or (
                    not definition_known and (active_node_ids or approval_execution_rows)
                ):
                    legacy_gate_query = (
                        t.approval_gates.select()
                        .where(t.approval_gates.c.project_id == project_id)
                        .where(t.approval_gates.c.status == "PENDING")
                        .where(t.approval_gates.c.flow_node_execution_id.is_(None))
                    )
                    if definition_known and gate_types:
                        legacy_gate_query = legacy_gate_query.where(
                            t.approval_gates.c.gate_type.in_(sorted(gate_types))
                        )
                    legacy_rows = (
                        await conn.execute(legacy_gate_query)
                    ).mappings().all()
                    if legacy_rows:
                        # Do not terminalize the flow while a plausible
                        # pre-binding gate remains unbound. The operator can
                        # still decide it through the normal exact-gate path,
                        # whose legacy binder requires a unique match and
                        # refuses ambiguous ownership before mutation.
                        return {
                            "outcome": "legacy_gate_unresolved",
                            "pending_legacy_gate_count": len(legacy_rows),
                        }

            execution_id_rows = await conn.execute(
                t.flow_node_executions.select()
                .with_only_columns(t.flow_node_executions.c.id)
                .where(t.flow_node_executions.c.instance_id == instance_id)
                .order_by(t.flow_node_executions.c.id.asc())
            )
            execution_ids = [int(row[0]) for row in execution_id_rows.all()]
            pending_gates: list[dict[str, Any]] = []
            if execution_ids:
                pending_gates = [
                    dict(row)
                    for row in (
                        await conn.execute(
                            t.approval_gates.select()
                            .where(t.approval_gates.c.flow_node_execution_id.in_(execution_ids))
                            .where(t.approval_gates.c.status == "PENDING")
                            .order_by(t.approval_gates.c.id.asc())
                            .with_for_update()
                        )
                    ).mappings().all()
                ]
                await conn.execute(
                    t.flow_node_executions.select()
                    .where(t.flow_node_executions.c.id.in_(execution_ids))
                    .order_by(t.flow_node_executions.c.id.asc())
                    .with_for_update()
                )

            instance_row = (
                await conn.execute(
                    t.flow_instances.select()
                    .where(t.flow_instances.c.id == instance_id)
                    .with_for_update()
                )
            ).mappings().first()
            if instance_row is None:
                return {"outcome": "not_found"}
            instance = dict(instance_row)
            if str(instance.get("status", "")).upper() in {
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            }:
                return {"outcome": "conflict", "instance": instance}
            if not _flow_instance_matches_snapshot(instance, expected_instance_snapshot):
                return {"outcome": "stale_instance", "instance": instance}

            gate_ids = [gate["id"] for gate in pending_gates]
            gate_execution_ids = sorted(
                {int(gate["flow_node_execution_id"]) for gate in pending_gates}
            )
            if gate_ids:
                await conn.execute(
                    t.approval_gates.update()
                    .where(t.approval_gates.c.id.in_(gate_ids))
                    .where(t.approval_gates.c.status == "PENDING")
                    .values(
                        status="CANCELLED",
                        decided_by=decided_by,
                        justification=reason,
                        decided_at=now,
                    )
                )
            if gate_execution_ids:
                await conn.execute(
                    t.flow_node_executions.update()
                    .where(t.flow_node_executions.c.id.in_(gate_execution_ids))
                    .where(t.flow_node_executions.c.status == "RUNNING")
                    .values(
                        status="CANCELLED",
                        error=sa.func.coalesce(t.flow_node_executions.c.error, reason),
                        completed_at=sa.func.coalesce(
                            t.flow_node_executions.c.completed_at, now
                        ),
                    )
                )
            await conn.execute(
                t.flow_instances.update()
                .where(t.flow_instances.c.id == instance_id)
                .values(
                    status="CANCELLED",
                    active_node_ids=[],
                    completed_at=now,
                    updated_at=now,
                )
            )
            updated = (
                await conn.execute(
                    t.flow_instances.select().where(t.flow_instances.c.id == instance_id)
                )
            ).mappings().first()
            return {
                "outcome": "applied",
                "instance": dict(updated) if updated else None,
                "cancelled_gate_ids": gate_ids,
            }

    async def list_flow_instances(
        self,
        *,
        flow_id: UUID | None = None,
        project_id: UUID | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List flow instances, optionally filtered."""
        q = t.flow_instances.select().order_by(t.flow_instances.c.created_at.desc())
        if flow_id is not None:
            q = q.where(t.flow_instances.c.flow_id == flow_id)
        if project_id is not None:
            q = q.where(t.flow_instances.c.project_id == project_id)
        if status is not None:
            q = q.where(t.flow_instances.c.status == status)
        q = q.limit(limit).offset(offset)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    # Flow Node Executions

    async def create_flow_node_execution(
        self,
        *,
        instance_id: UUID,
        node_id: str,
        node_type: str,
        node_label: str,
        input_json: dict | None = None,
    ) -> dict[str, Any]:
        """Create a new node execution record."""
        now = datetime.now(tz=UTC)
        values = {
            "instance_id": instance_id,
            "node_id": node_id,
            "node_type": node_type,
            "node_label": node_label,
            "status": "RUNNING",
            "input_json": input_json,
            "started_at": now,
        }
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.flow_node_executions.insert()
                .values(**values)
                .returning(t.flow_node_executions.c.id)
            )
            row = result.first()
            values["id"] = row[0] if row else None
        return values

    async def get_flow_node_execution(self, execution_id: int) -> dict[str, Any] | None:
        """Fetch a node execution by ID."""
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        t.flow_node_executions.select().where(
                            t.flow_node_executions.c.id == execution_id
                        )
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def update_flow_node_execution(
        self,
        execution_id: int,
        *,
        status: str | None = None,
        output_json: dict | None = None,
        error: str | None = None,
        completed_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Update a node execution."""
        updates: dict[str, Any] = {}
        if status is not None:
            updates["status"] = status
        if output_json is not None:
            updates["output_json"] = output_json
        if error is not None:
            updates["error"] = error
        if completed_at is not None:
            updates["completed_at"] = completed_at

        if not updates:
            return await self.get_flow_node_execution(execution_id)

        async with self.engine.begin() as conn:
            await conn.execute(
                t.flow_node_executions.update()
                .where(t.flow_node_executions.c.id == execution_id)
                .values(**updates)
            )

        return await self.get_flow_node_execution(execution_id)

    async def list_flow_node_executions(
        self,
        *,
        instance_id: UUID,
        node_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List node executions for an instance."""
        q = (
            t.flow_node_executions.select()
            .where(t.flow_node_executions.c.instance_id == instance_id)
            .order_by(t.flow_node_executions.c.started_at.asc())
        )
        if node_id is not None:
            q = q.where(t.flow_node_executions.c.node_id == node_id)
        if status is not None:
            q = q.where(t.flow_node_executions.c.status == status)
        q = q.limit(limit).offset(offset)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def clear_flow_node_executions(
        self,
        instance_id: UUID,
    ) -> None:
        """Delete all node executions for a flow instance."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.flow_node_executions.delete().where(
                    t.flow_node_executions.c.instance_id == instance_id
                )
            )

    async def supersede_flow_node_executions(
        self,
        instance_id: UUID,
        *,
        reason: str = "Superseded by explicit flow retry",
    ) -> int:
        """Retain prior node attempts while removing them from traversal authority.

        Retry is an evidence-preserving operation.  Historical inputs,
        outputs, errors, and timestamps remain queryable; only the status is
        changed to ``SUPERSEDED`` so a newly created retry execution is the
        sole active attempt for that node.  ``COALESCE`` preserves an
        authoritative failure message when one already exists.
        """

        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.flow_node_executions.update()
                .where(
                    sa.and_(
                        t.flow_node_executions.c.instance_id == instance_id,
                        t.flow_node_executions.c.status != "SUPERSEDED",
                    )
                )
                .values(
                    status="SUPERSEDED",
                    error=sa.func.coalesce(t.flow_node_executions.c.error, reason),
                    completed_at=sa.func.coalesce(
                        t.flow_node_executions.c.completed_at,
                        now,
                    ),
                )
            )
        return int(result.rowcount or 0)

    async def update_flow_instance_context(
        self,
        instance_id: UUID,
        context_updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update the context_json for a flow instance (merge updates)."""
        instance = await self.get_flow_instance(instance_id)
        if instance is None:
            return None

        current_context = dict(instance.get("context_json") or {})
        current_context.update(context_updates)

        return await self.update_flow_instance(
            instance_id,
            context_json=current_context,
        )

    async def switch_flow_instance(
        self,
        instance_id: UUID,
        new_flow_id: UUID,
        preserve_context: bool = True,
    ) -> dict[str, Any] | None:
        """Switch a flow instance to a different flow definition.

        The instance will be reset to NOT_STARTED state with the new flow.
        If preserve_context is True, the existing context is kept.
        """
        instance = await self.get_flow_instance(instance_id)
        if instance is None:
            return None

        new_flow = await self.get_flow(new_flow_id)
        if new_flow is None:
            return None

        current_context = instance.get("context_json") if preserve_context else {}

        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            await conn.execute(
                t.flow_instances.update()
                .where(t.flow_instances.c.id == instance_id)
                .values(
                    flow_id=new_flow_id,
                    flow_version=new_flow["version"],
                    active_node_ids=[],
                    status="NOT_STARTED",
                    context_json=current_context,
                    started_at=None,
                    completed_at=None,
                    updated_at=now,
                )
            )
            await conn.execute(
                t.flow_node_executions.delete().where(
                    t.flow_node_executions.c.instance_id == instance_id
                )
            )

        return await self.get_flow_instance(instance_id)

    async def get_active_flow_instances(self) -> list[dict[str, Any]]:
        """List all active (non-terminal) flow instances."""
        q = (
            t.flow_instances.select()
            .where(
                t.flow_instances.c.status.in_(
                    ["NOT_STARTED", "RUNNING", "WAITING_APPROVAL", "PAUSED"]
                )
            )
            .order_by(t.flow_instances.c.created_at.desc())
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(r) for r in rows]

    async def escalate_flow_instance(
        self,
        instance_id: UUID,
        escalate_to: str,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Mark a flow instance as escalated."""
        instance = await self.get_flow_instance(instance_id)
        if instance is None:
            return None

        return await self.update_flow_instance(
            instance_id,
            escalated_to=escalate_to,
            escalation_reason=reason,
        )

    async def retry_flow_instance(
        self,
        instance_id: UUID,
        *,
        expected_instance_snapshot: dict[str, Any] | None = None,
        reason: str = "Superseded by explicit flow retry",
    ) -> dict[str, Any] | None:
        """Retry a failed flow instance without deleting execution evidence.

        The API's recorded-safe-node path creates a new active execution after
        superseding the previous attempts.  The no-safe-node fallback must keep
        the same evidence boundary: historical node rows remain queryable and
        are removed from traversal authority by the ``SUPERSEDED`` status.
        """
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            execution_id_rows = await conn.execute(
                t.flow_node_executions.select()
                .with_only_columns(t.flow_node_executions.c.id)
                .where(t.flow_node_executions.c.instance_id == instance_id)
                .order_by(t.flow_node_executions.c.id.asc())
            )
            execution_ids = [int(row[0]) for row in execution_id_rows.all()]
            pending_gates: list[dict[str, Any]] = []
            if execution_ids:
                pending_gates = [
                    dict(row)
                    for row in (
                        await conn.execute(
                            t.approval_gates.select()
                            .where(t.approval_gates.c.flow_node_execution_id.in_(execution_ids))
                            .where(t.approval_gates.c.status == "PENDING")
                            .order_by(t.approval_gates.c.id.asc())
                            .with_for_update()
                        )
                    ).mappings().all()
                ]
                await conn.execute(
                    t.flow_node_executions.select()
                    .where(t.flow_node_executions.c.id.in_(execution_ids))
                    .order_by(t.flow_node_executions.c.id.asc())
                    .with_for_update()
                )

            instance_row = (
                await conn.execute(
                    t.flow_instances.select()
                    .where(t.flow_instances.c.id == instance_id)
                    .with_for_update()
                )
            ).mappings().first()
            if instance_row is None:
                return None
            instance = dict(instance_row)
            if str(instance.get("status", "")).upper() not in {"FAILED", "CANCELLED"}:
                return None
            if not _flow_instance_matches_snapshot(instance, expected_instance_snapshot):
                return None

            gate_ids = [gate["id"] for gate in pending_gates]
            if gate_ids:
                await conn.execute(
                    t.approval_gates.update()
                    .where(t.approval_gates.c.id.in_(gate_ids))
                    .where(t.approval_gates.c.status == "PENDING")
                    .values(
                        status="CANCELLED",
                        decided_by="system:flow_retry",
                        justification=reason,
                        decided_at=now,
                    )
                )
            if execution_ids:
                await conn.execute(
                    t.flow_node_executions.update()
                    .where(t.flow_node_executions.c.id.in_(execution_ids))
                    .where(t.flow_node_executions.c.status != "SUPERSEDED")
                    .values(
                        status="SUPERSEDED",
                        error=sa.func.coalesce(t.flow_node_executions.c.error, reason),
                        completed_at=sa.func.coalesce(
                            t.flow_node_executions.c.completed_at, now
                        ),
                    )
                )
            await conn.execute(
                t.flow_instances.update()
                .where(t.flow_instances.c.id == instance_id)
                .values(
                    status="NOT_STARTED",
                    active_node_ids=[],
                    retry_count=int(instance.get("retry_count") or 0) + 1,
                    started_at=None,
                    completed_at=None,
                    updated_at=now,
                )
            )
            updated = (
                await conn.execute(
                    t.flow_instances.select().where(t.flow_instances.c.id == instance_id)
                )
            ).mappings().first()
            return dict(updated) if updated else None

    async def override_flow_instance(
        self,
        instance_id: UUID,
        *,
        target_node_id: str,
        node_type: str,
        node_label: str,
        actor_id: str,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Force a running instance onto a specific node and record the override context."""
        instance = await self.get_flow_instance(instance_id)
        if instance is None:
            return None

        now = datetime.now(tz=UTC)
        context_json = dict(instance.get("context_json") or {})
        context_json["last_override"] = {
            "target_node_id": target_node_id,
            "actor_id": actor_id,
            "reason": reason,
            "overridden_at": now.isoformat(),
        }

        active_node_ids = list(instance.get("active_node_ids") or [])
        for node_id in active_node_ids:
            executions = await self.list_flow_node_executions(
                instance_id=instance_id,
                node_id=node_id,
                status="RUNNING",
                limit=100,
            )
            for execution in executions:
                await self.update_flow_node_execution(
                    execution["id"],
                    status="SKIPPED",
                    error=f"Overridden by {actor_id}" if actor_id else "Overridden",
                    completed_at=now,
                )

        await self.update_flow_instance(
            instance_id,
            status="RUNNING",
            active_node_ids=[target_node_id],
            context_json=context_json,
            completed_at=None,
        )
        await self.create_flow_node_execution(
            instance_id=instance_id,
            node_id=target_node_id,
            node_type=node_type,
            node_label=node_label,
            input_json=context_json,
        )
        return await self.get_flow_instance(instance_id)

    async def migrate_flow_instance(
        self,
        instance_id: UUID,
        new_flow_id: UUID,
        *,
        active_node_ids: list[str],
        preserve_context: bool = True,
        migration_record: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Migrate a running instance while retaining compatible executions.

        The caller performs compatibility validation against both definitions.
        This storage operation only changes the pinned flow/version and records
        a bounded migration marker in context; unlike ``switch_flow_instance``
        it never deletes historical node executions.
        """

        instance = await self.get_flow_instance(instance_id)
        if instance is None:
            return None
        new_flow = await self.get_flow(new_flow_id)
        if new_flow is None:
            return None

        context_json = dict(instance.get("context_json") or {}) if preserve_context else {}
        if migration_record:
            context_json["last_flow_migration"] = migration_record
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            await conn.execute(
                t.flow_instances.update()
                .where(t.flow_instances.c.id == instance_id)
                .values(
                    flow_id=new_flow_id,
                    flow_version=new_flow["version"],
                    active_node_ids=active_node_ids,
                    context_json=context_json,
                    updated_at=now,
                )
            )
        return await self.get_flow_instance(instance_id)

    # ═══════════════════════════════════════════════════════════════════════════
    # Universal worker contract, steward, model, and durable run records
    # ═══════════════════════════════════════════════════════════════════════════

    async def _get_table_row(self, table: sa.Table, key_column: sa.Column, key: Any) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(table.select().where(key_column == key))).mappings().first()
        return dict(row) if row else None

    async def create_worker_shell_version(self, *, worker_id: UUID, version: str, contract_version: str, schema_version: str, identity: dict[str, Any], capabilities: dict[str, Any], permissions: dict[str, Any] | None = None, model_mode: str = "none", provenance: dict[str, Any] | None = None, content_hash: str, shell_version_id: UUID | None = None) -> dict[str, Any]:
        values = {
            "id": shell_version_id or uuid4(),
            "worker_id": worker_id,
            "version": version,
            "contract_version": contract_version,
            "schema_version": schema_version,
            "identity_json": identity,
            "capabilities_json": capabilities,
            "permissions_json": permissions or {},
            "model_mode": model_mode,
            "provenance_json": provenance or {},
            "content_hash": content_hash,
            "status": "active",
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.worker_shell_versions.insert().values(**values))
        return values

    async def get_worker_shell_version(self, shell_version_id: UUID) -> dict[str, Any] | None:
        """Return an immutable Specialist Shell version by its selected ID."""
        return await self._get_table_row(
            t.worker_shell_versions,
            t.worker_shell_versions.c.id,
            shell_version_id,
        )

    async def get_worker_shell_version_by_version(
        self,
        worker_id: UUID,
        version: str,
    ) -> dict[str, Any] | None:
        """Return one immutable shell using its worker-scoped version."""
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.worker_shell_versions.select().where(
                        sa.and_(
                            t.worker_shell_versions.c.worker_id == worker_id,
                            t.worker_shell_versions.c.version == version,
                        )
                    )
                )
            ).mappings().first()
        return dict(row) if row else None

    async def create_runtime_adapter(self, *, worker_id: UUID, version: str, adapter_type: str, transport_type: str, content_hash: str, runtime_api_version: str | None = None, implementation_ref: str | None = None, capabilities: dict[str, Any] | None = None, conformance_status: str = "pending", conformance: dict[str, Any] | None = None, status: str = "candidate", adapter_id: UUID | None = None) -> dict[str, Any]:
        values = {
            "id": adapter_id or uuid4(),
            "worker_id": worker_id,
            "version": version,
            "adapter_type": adapter_type,
            "transport_type": transport_type,
            "runtime_api_version": runtime_api_version,
            "implementation_ref": implementation_ref,
            "content_hash": content_hash,
            "capabilities_json": capabilities or {},
            "conformance_status": conformance_status,
            "conformance_json": conformance,
            "status": status,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.runtime_adapters.insert().values(**values))
        return values

    async def get_runtime_adapter(self, adapter_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.runtime_adapters, t.runtime_adapters.c.id, adapter_id)

    async def list_runtime_adapters(self, worker_id: UUID, *, status: str | None = None) -> list[dict[str, Any]]:
        query = t.runtime_adapters.select().where(t.runtime_adapters.c.worker_id == worker_id)
        if status is not None:
            query = query.where(t.runtime_adapters.c.status == status)
        query = query.order_by(t.runtime_adapters.c.created_at.desc())
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_active_runtime_adapter(self, worker_id: UUID) -> dict[str, Any] | None:
        query = (
            t.runtime_adapters.select()
            .where(
                sa.and_(
                    t.runtime_adapters.c.worker_id == worker_id,
                    t.runtime_adapters.c.status == "active",
                    t.runtime_adapters.c.conformance_status == "passed",
                )
            )
            .order_by(t.runtime_adapters.c.created_at.desc())
        )
        async with self.engine.connect() as conn:
            row = (await conn.execute(query)).mappings().first()
        return dict(row) if row else None

    async def create_external_provenance(self, *, worker_id: UUID, provenance: dict[str, Any], provenance_hash: str) -> dict[str, Any]:
        values = {"id": uuid4(), "worker_id": worker_id, **provenance, "provenance_hash": provenance_hash}
        async with self.engine.begin() as conn:
            stmt = (
                pg_insert(t.external_runtime_provenance)
                .values(**values)
                .on_conflict_do_update(
                    constraint="uq_external_provenance_worker",
                    set_={key: value for key, value in values.items() if key not in {"id", "worker_id", "created_at"}},
                )
                .returning(t.external_runtime_provenance)
            )
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else values

    async def get_external_provenance_by_worker(self, worker_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.external_runtime_provenance, t.external_runtime_provenance.c.worker_id, worker_id)

    async def persist_source_bound_worker_security_scan(
        self,
        *,
        worker_id: UUID,
        report: dict[str, Any],
        expected_provenance_hash: str,
    ) -> dict[str, Any] | None:
        """Persist a server-generated candidate scan beside steward provenance.

        The external provenance table predates a JSONB evidence column. Its
        related Steward row already carries the JSONB ``source_provenance_extra``
        read model, so the source-bound report is stored there while the
        normalized provenance status/hash are updated in the same transaction.
        The caller must provide the row hash observed before scanning; a
        concurrent provenance change prevents stale evidence from being saved.
        """
        serialized = json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        if len(serialized.encode("utf-8")) > 1 * 1024 * 1024:
            from ..worker_registry.source_bound_security_scan import (
                SourceBoundSecurityScanPersistenceError,
            )

            raise SourceBoundSecurityScanPersistenceError(
                "SECURITY_SCAN_REPORT_TOO_LARGE"
            )

        from mas_core.worker_registry.source_bound_security_scan import (
            FIRST_PARTY_DOCKERFILE_SHA256,
            FIRST_PARTY_IMAGE_DIGEST,
            FIRST_PARTY_IMAGE_SOURCE_BINDING_REQUIRED,
            FIRST_PARTY_RUNTIME_SHA256,
            FIRST_PARTY_SOURCE_REPOSITORY,
            FIRST_PARTY_SOURCE_REVISION,
            FIRST_PARTY_WORKER_ID,
            FIRST_PARTY_WORKER_VERSION,
            SourceBoundSecurityScanPersistenceError,
            source_bound_security_scan_is_valid,
            source_bound_security_scan_report_integrity_is_valid,
        )

        async with self.engine.begin() as conn:
            worker_row = (
                await conn.execute(
                    t.worker_registry.select()
                    .where(t.worker_registry.c.id == worker_id)
                    .with_for_update()
                )
            ).mappings().first()
            provenance_row = (
                await conn.execute(
                    t.external_runtime_provenance.select()
                    .where(t.external_runtime_provenance.c.worker_id == worker_id)
                    .with_for_update()
                )
            ).mappings().first()
            steward_row = (
                await conn.execute(
                    t.steward_agents.select()
                    .where(t.steward_agents.c.worker_id == worker_id)
                    .with_for_update()
                )
            ).mappings().first()
            if worker_row is None or provenance_row is None or steward_row is None:
                return None
            if str(provenance_row.get("provenance_hash") or "") != expected_provenance_hash:
                raise SourceBoundSecurityScanPersistenceError(
                    "SECURITY_SCAN_PROVENANCE_CHANGED"
                )

            worker = dict(worker_row)
            provenance = dict(provenance_row)
            candidate = report.get("candidate") if isinstance(report, dict) else None
            candidate = candidate if isinstance(candidate, dict) else {}
            worker_config = worker.get("adapter_config") or {}
            if not isinstance(worker_config, dict):
                worker_config = {}
            expected_version_pin = (
                f"{FIRST_PARTY_WORKER_VERSION}+"
                f"{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}"
            )
            identity_matches = (
                str(candidate.get("worker_registry_id") or "") == str(worker_id)
                and str(worker.get("name") or "") == FIRST_PARTY_WORKER_ID
                and str(worker.get("version") or "") == FIRST_PARTY_WORKER_VERSION
                and str(worker.get("version_pin") or "") == expected_version_pin
                and str(worker.get("source_revision") or "") == FIRST_PARTY_SOURCE_REVISION
                and str(worker.get("source_repo") or "") == FIRST_PARTY_SOURCE_REPOSITORY
                and str(worker.get("adapter_type") or "").lower() == "oci"
                and str(worker.get("sandbox_profile") or "") == "sandboxed"
                and str(worker.get("adapter_entrypoint") or "") == "ProcessAdapter"
                and str(worker_config.get("image") or "")
                == f"aiat-deterministic-file-worker@{FIRST_PARTY_IMAGE_DIGEST}"
                and str(provenance.get("canonical_source_repository") or "")
                == FIRST_PARTY_SOURCE_REPOSITORY
                and str(provenance.get("source_provider") or "").lower() == "aiat"
                and str(provenance.get("exact_release") or "") == FIRST_PARTY_WORKER_VERSION
                and str(provenance.get("transport_type") or "").lower() == "oci"
                and str(provenance.get("commit_sha") or "") == FIRST_PARTY_SOURCE_REVISION
                and str(provenance.get("oci_image_digest") or "") == FIRST_PARTY_IMAGE_DIGEST
                and str(provenance.get("runtime_fingerprint") or "")
                == f"sha256:{FIRST_PARTY_RUNTIME_SHA256}"
                and candidate.get("dockerfile_sha256") == FIRST_PARTY_DOCKERFILE_SHA256
                and candidate.get("runtime_sha256") == FIRST_PARTY_RUNTIME_SHA256
            )
            if not identity_matches:
                raise SourceBoundSecurityScanPersistenceError(
                    "SECURITY_SCAN_IDENTITY_INVALID"
                )
            if not source_bound_security_scan_report_integrity_is_valid(
                report,
                expected_worker_id=str(worker_id),
                provenance={**provenance, "source_bound_security_scan": report},
                current_worker=worker,
            ):
                raise SourceBoundSecurityScanPersistenceError(
                    "SECURITY_SCAN_REPORT_INVALID"
                )

            steward = dict(steward_row)
            metadata = dict(steward.get("metadata") or {})
            provenance_extra = dict(metadata.get("source_provenance_extra") or {})
            provenance_extra["source_bound_security_scan"] = report
            provenance_extra["source_bound_security_scan_status"] = report.get("status")
            prior_history = provenance_extra.get("source_bound_security_scan_history")
            history = list(prior_history) if isinstance(prior_history, list) else []
            history.append(
                {
                    "report_sha256": report.get("report_sha256"),
                    "status": report.get("status"),
                    "source_scan_status": report.get("status"),
                    "finding_count": report.get("finding_count"),
                    "scanner_error_count": report.get("scanner_error_count"),
                    "scan_completed_at": report.get("scan_completed_at"),
                }
            )
            provenance_extra["source_bound_security_scan_history"] = history[-20:]
            metadata["source_provenance_extra"] = provenance_extra

            current_status = str(provenance.get("security_scan_status") or "pending").lower()
            if report.get("status") == "findings_review_required":
                new_status = "findings_review_required"
            elif report.get("status") == "blocked":
                new_status = "blocked"
            elif current_status in {"findings_review_required", "requires_human_review"}:
                # A clean source-only scan cannot erase a human review decision.
                new_status = current_status
            elif current_status == "blocked":
                new_status = "blocked"
            elif source_bound_security_scan_is_valid(
                report,
                expected_worker_id=str(worker_id),
                provenance={**provenance, "source_bound_security_scan": report},
                current_worker=worker,
            ):
                new_status = "passed"
            else:
                # A source-only scan without the exact retained BuildKit record
                # is not sufficient to set the overall security gate PASS.
                new_status = FIRST_PARTY_IMAGE_SOURCE_BINDING_REQUIRED

            provenance_for_hash = {
                key: value
                for key, value in provenance.items()
                if key not in {"id", "worker_id", "created_at", "provenance_hash"}
            }
            provenance_for_hash["security_scan_status"] = new_status
            provenance_hash = hashlib.sha256(
                json.dumps(
                    {
                        "provenance": provenance_for_hash,
                        "source_bound_security_scan": report,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            await conn.execute(
                t.steward_agents.update()
                .where(t.steward_agents.c.id == steward["id"])
                .values(metadata=metadata, updated_at=datetime.now(tz=UTC))
            )
            await conn.execute(
                t.external_runtime_provenance.update()
                .where(t.external_runtime_provenance.c.worker_id == worker_id)
                .values(
                    security_scan_status=new_status,
                    provenance_hash=provenance_hash,
                )
            )
            result = {
                **provenance,
                "security_scan_status": new_status,
                "source_bound_security_scan_status": report.get("status"),
                "provenance_hash": provenance_hash,
                "source_bound_security_scan": report,
            }
            return result

    async def create_steward(self, *, worker_id: UUID, provenance_id: UUID | None = None, steward_id: UUID | None = None, status: str = "PROVISIONING", steward_version: str = "1.0.0", monitoring_cadence: str = "daily", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        values = {"id": steward_id or uuid4(), "worker_id": worker_id, "status": status, "steward_version": steward_version, "provenance_id": provenance_id, "monitoring_cadence": monitoring_cadence, "metadata": metadata or {}}
        async with self.engine.begin() as conn:
            await conn.execute(
                pg_insert(t.steward_agents)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_steward_worker")
            )
            row = (
                await conn.execute(
                    t.steward_agents.select().where(t.steward_agents.c.worker_id == worker_id)
                )
            ).mappings().first()
        return dict(row) if row else values

    async def get_steward(self, steward_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.steward_agents, t.steward_agents.c.id, steward_id)

    async def get_steward_by_worker(self, worker_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.steward_agents, t.steward_agents.c.worker_id, worker_id)

    async def list_stewards(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = t.steward_agents.select().order_by(t.steward_agents.c.updated_at.desc()).limit(limit)
        if status is not None:
            query = query.where(t.steward_agents.c.status == status)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def update_steward(self, steward_id: UUID, *, status: str | None = None, active_skill_bundle_id: UUID | None = None, active_adapter_id: UUID | None = None, last_monitor_at: datetime | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any] | None:
        values: dict[str, Any] = {"updated_at": datetime.now(tz=UTC)}
        for name, value in (("status", status), ("active_skill_bundle_id", active_skill_bundle_id), ("active_adapter_id", active_adapter_id), ("last_monitor_at", last_monitor_at), ("metadata", metadata)):
            if value is not None:
                values[name] = value
        async with self.engine.begin() as conn:
            result = await conn.execute(t.steward_agents.update().where(t.steward_agents.c.id == steward_id).values(**values))
            if result.rowcount == 0:
                return None
        return await self.get_steward(steward_id)

    async def transition_steward(
        self,
        steward_id: UUID,
        *,
        to_status: str,
        actor: str,
        reason: str | None = None,
        correlation_id: str | None = None,
        evidence: dict[str, Any] | None = None,
        expected_status: str | None = None,
    ) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            current = (
                await conn.execute(
                    t.steward_agents.select()
                    .where(t.steward_agents.c.id == steward_id)
                    .with_for_update()
                )
            ).mappings().first()
            if current is None or (expected_status is not None and current["status"] != expected_status):
                return None
            await conn.execute(
                t.steward_agents.update()
                .where(t.steward_agents.c.id == steward_id)
                .values(status=to_status, updated_at=datetime.now(tz=UTC))
            )
            await conn.execute(
                t.steward_transitions.insert().values(
                    id=uuid4(),
                    steward_id=steward_id,
                    from_status=current["status"],
                    to_status=to_status,
                    actor=actor,
                    reason=reason,
                    correlation_id=correlation_id,
                    evidence=evidence or {},
                )
            )
        return await self.get_steward(steward_id)

    async def set_steward_active_versions(self, steward_id: UUID, *, active_skill_bundle_id: UUID | None, active_adapter_id: UUID | None) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.steward_agents.update()
                .where(t.steward_agents.c.id == steward_id)
                .values(active_skill_bundle_id=active_skill_bundle_id, active_adapter_id=active_adapter_id, updated_at=datetime.now(tz=UTC))
            )
            if result.rowcount == 0:
                return None
        return await self.get_steward(steward_id)

    async def create_documentation_source(
        self,
        *,
        steward_id: UUID,
        uri: str,
        source_type: str = "official",
        trusted_for_provenance: bool = False,
        allowed_domains: list[str] | None = None,
        source_id: UUID | None = None,
    ) -> dict[str, Any]:
        values = {
            "id": source_id or uuid4(),
            "steward_id": steward_id,
            "uri": uri,
            "source_type": source_type,
            "trusted_for_provenance": trusted_for_provenance,
            "allowed_domains": allowed_domains or [],
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.documentation_sources.insert().values(**values))
        return values

    async def get_documentation_source(self, *, steward_id: UUID, uri: str) -> dict[str, Any] | None:
        query = t.documentation_sources.select().where(
            sa.and_(t.documentation_sources.c.steward_id == steward_id, t.documentation_sources.c.uri == uri)
        )
        async with self.engine.connect() as conn:
            row = (await conn.execute(query)).mappings().first()
        return dict(row) if row else None

    async def create_documentation_snapshot(
        self,
        *,
        source_id: UUID,
        version: str,
        content_sha256: str,
        content_ref: str | None = None,
        extracted_interfaces: dict[str, Any] | None = None,
        security_findings: list[str] | None = None,
        untrusted: bool = True,
        snapshot_id: UUID | None = None,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        values = {
            "id": snapshot_id or uuid4(),
            "source_id": source_id,
            "version": version,
            "content_sha256": content_sha256,
            "content_ref": content_ref,
            "extracted_interfaces": extracted_interfaces or {},
            "security_findings": security_findings or [],
            "untrusted": untrusted,
            "captured_at": captured_at or datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.documentation_snapshots.insert().values(**values))
        return values

    async def list_documentation_snapshots(self, steward_id: UUID, *, limit: int = 100) -> list[dict[str, Any]]:
        query = (
            sa.select(
                t.documentation_snapshots,
                t.documentation_sources.c.uri.label("source_uri"),
                t.documentation_sources.c.source_type.label("source_type"),
                t.documentation_sources.c.trusted_for_provenance.label("source_trusted"),
                t.documentation_sources.c.allowed_domains.label("source_allowed_domains"),
            )
            .join(
                t.documentation_sources,
                t.documentation_sources.c.id == t.documentation_snapshots.c.source_id,
            )
            .where(t.documentation_sources.c.steward_id == steward_id)
            .order_by(t.documentation_snapshots.c.captured_at.asc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def create_capability_snapshot(
        self,
        *,
        worker_id: UUID,
        version: str,
        capabilities: dict[str, Any],
        steward_id: UUID | None = None,
        evidence_refs: list[str] | None = None,
        snapshot_id: UUID | None = None,
        discovered_at: datetime | None = None,
    ) -> dict[str, Any]:
        values = {
            "id": snapshot_id or uuid4(),
            "worker_id": worker_id,
            "steward_id": steward_id,
            "version": version,
            "capabilities_json": capabilities,
            "evidence_refs": evidence_refs or [],
            "created_at": discovered_at or datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.capability_snapshots.insert().values(**values))
        return values

    async def list_capability_snapshots(self, worker_id: UUID, *, steward_id: UUID | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = t.capability_snapshots.select().where(t.capability_snapshots.c.worker_id == worker_id)
        if steward_id is not None:
            query = query.where(t.capability_snapshots.c.steward_id == steward_id)
        query = query.order_by(t.capability_snapshots.c.created_at.asc()).limit(limit)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def create_compatibility_matrix(
        self,
        *,
        worker_id: UUID,
        runtime_version: str,
        adapter_version: str,
        contract_version: str,
        model_profiles: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
        fixtures: list[str] | None = None,
        passed: bool = False,
        matrix_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Persist one steward-owned runtime/adapter compatibility result."""
        values = {
            "id": matrix_id or uuid4(),
            "worker_id": worker_id,
            "runtime_version": runtime_version,
            "adapter_version": adapter_version,
            "contract_version": contract_version,
            "model_profiles_json": model_profiles or {},
            "capabilities_json": capabilities or {},
            "fixtures": fixtures or [],
            "passed": passed,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.compatibility_matrices.insert().values(**values))
        return values

    async def list_compatibility_matrices(
        self,
        worker_id: UUID,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List compatibility evidence newest-first for operator inspection."""
        query = (
            t.compatibility_matrices.select()
            .where(t.compatibility_matrices.c.worker_id == worker_id)
            .order_by(t.compatibility_matrices.c.created_at.desc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def create_skill_bundle(self, *, worker_id: UUID, steward_id: UUID, semantic_version: str, format_version: str, upstream_compatibility_range: str, provenance: dict[str, Any], bundle: dict[str, Any], content_hash: str, status: str = "DRAFT", bundle_id: UUID | None = None) -> dict[str, Any]:
        values = {"id": bundle_id or uuid4(), "worker_id": worker_id, "steward_id": steward_id, "semantic_version": semantic_version, "format_version": format_version, "upstream_compatibility_range": upstream_compatibility_range, "provenance_json": provenance, "bundle_json": bundle, "content_hash": content_hash, "status": status}
        async with self.engine.begin() as conn:
            await conn.execute(t.skill_bundles.insert().values(**values))
        return values

    async def get_skill_bundle(self, bundle_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.skill_bundles, t.skill_bundles.c.id, bundle_id)

    async def get_active_skill_bundle(self, worker_id: UUID) -> dict[str, Any] | None:
        query = (
            t.skill_bundles.select()
            .where(sa.and_(t.skill_bundles.c.worker_id == worker_id, t.skill_bundles.c.status == "APPROVED"))
            .order_by(t.skill_bundles.c.created_at.desc())
        )
        async with self.engine.connect() as conn:
            row = (await conn.execute(query)).mappings().first()
        return dict(row) if row else None

    async def list_skill_bundles(
        self,
        worker_id: UUID,
        *,
        steward_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        """List immutable bundle versions for controlled candidate recovery."""
        query = t.skill_bundles.select().where(t.skill_bundles.c.worker_id == worker_id)
        if steward_id is not None:
            query = query.where(t.skill_bundles.c.steward_id == steward_id)
        query = query.order_by(t.skill_bundles.c.created_at.desc())
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def create_skill_bundle_candidate(self, *, candidate_id: UUID, skill_bundle_id: UUID, worker_id: UUID, adapter_id: UUID | None, intake_status: str, diff: dict[str, Any], evidence: dict[str, Any], certification_run_id: UUID | None = None, approval_record_id: UUID | None = None, candidate_json: dict[str, Any] | None = None) -> dict[str, Any]:
        evidence_json = dict(evidence)
        if candidate_json is not None:
            evidence_json["candidate_record"] = candidate_json
        values = {"id": candidate_id, "skill_bundle_id": skill_bundle_id, "adapter_id": adapter_id, "worker_id": worker_id, "intake_status": intake_status, "diff_json": diff, "evidence_json": evidence_json, "certification_run_id": certification_run_id, "approval_record_id": approval_record_id}
        async with self.engine.begin() as conn:
            await conn.execute(t.skill_bundle_candidates.insert().values(**values))
        return values

    async def get_skill_bundle_candidate(self, candidate_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.skill_bundle_candidates, t.skill_bundle_candidates.c.id, candidate_id)

    async def list_skill_bundle_candidates(self, worker_id: UUID) -> list[dict[str, Any]]:
        query = t.skill_bundle_candidates.select().where(t.skill_bundle_candidates.c.worker_id == worker_id).order_by(t.skill_bundle_candidates.c.created_at.asc())
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def update_skill_bundle_candidate(
        self,
        candidate_id: UUID,
        *,
        intake_status: str | None = None,
        evidence: dict[str, Any] | None = None,
        certification_run_id: UUID | None = None,
        approval_record_id: UUID | None = None,
        expected_intake_status: str | None = None,
    ) -> dict[str, Any] | None:
        values: dict[str, Any] = {}
        if intake_status is not None:
            values["intake_status"] = intake_status
        if evidence is not None:
            values["evidence_json"] = evidence
        if certification_run_id is not None:
            values["certification_run_id"] = certification_run_id
        if approval_record_id is not None:
            values["approval_record_id"] = approval_record_id
        if not values:
            return await self.get_skill_bundle_candidate(candidate_id)
        async with self.engine.begin() as conn:
            predicate = t.skill_bundle_candidates.c.id == candidate_id
            if expected_intake_status is not None:
                predicate = sa.and_(
                    predicate,
                    t.skill_bundle_candidates.c.intake_status == expected_intake_status,
                )
            result = await conn.execute(
                t.skill_bundle_candidates.update()
                .where(predicate)
                .values(**values)
            )
            if result.rowcount == 0:
                return None
        return await self.get_skill_bundle_candidate(candidate_id)

    async def _lock_first_party_candidate_governance_rows(
        self,
        conn: Any,
        *,
        worker_id: UUID,
        candidate_id: UUID,
        expected_worker_identity: Mapping[str, Any],
        expected_provenance_hash: str,
        expected_scan_report_sha256: str,
        expected_candidate_snapshot: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        """Lock and compare first-party governance state before any writes."""
        worker = (
            await conn.execute(
                t.worker_registry.select()
                .where(t.worker_registry.c.id == worker_id)
                .with_for_update()
            )
        ).mappings().first()
        provenance = (
            await conn.execute(
                t.external_runtime_provenance.select()
                .where(t.external_runtime_provenance.c.worker_id == worker_id)
                .with_for_update()
            )
        ).mappings().first()
        steward = (
            await conn.execute(
                t.steward_agents.select()
                .where(t.steward_agents.c.worker_id == worker_id)
                .with_for_update()
            )
        ).mappings().first()
        candidate = (
            await conn.execute(
                t.skill_bundle_candidates.select()
                .where(t.skill_bundle_candidates.c.id == candidate_id)
                .with_for_update()
            )
        ).mappings().first()
        if (
            worker is None
            or provenance is None
            or steward is None
            or candidate is None
            or provenance.get("worker_id") != worker_id
            or steward.get("worker_id") != worker_id
            or not _candidate_governance_snapshot_matches(
                worker=worker,
                provenance=provenance,
                steward=steward,
                candidate=candidate,
                expected_worker_identity=expected_worker_identity,
                expected_provenance_hash=expected_provenance_hash,
                expected_scan_report_sha256=expected_scan_report_sha256,
                expected_candidate_snapshot=expected_candidate_snapshot,
            )
        ):
            return None
        return dict(worker), dict(provenance), dict(steward), dict(candidate)

    async def persist_first_party_candidate_certification_atomically(
        self,
        *,
        worker_id: UUID,
        candidate_id: UUID,
        expected_worker_identity: Mapping[str, Any],
        expected_provenance_hash: str,
        expected_scan_report_sha256: str,
        expected_source_identity_fingerprint: str,
        expected_candidate_snapshot: Mapping[str, Any],
        certification: Mapping[str, Any],
        candidate_record_after: Mapping[str, Any],
        shell_values: Mapping[str, Any] | None = None,
        compatibility_matrix: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Persist a first-party certification only against its locked precheck.

        Certification, candidate lifecycle/evidence, adapter conformance, and
        optional shell/matrix records commit together. A concurrent reject or
        source change returns ``None`` without leaving partial certification
        evidence.
        """
        required = (
            "id",
            "worker_id",
            "candidate_id",
            "steward_id",
            "status",
            "conformance_json",
            "checks_json",
            "evidence_json",
            "failure_reasons",
            "completed_at",
        )
        if (
            not isinstance(expected_worker_identity, Mapping)
            or not isinstance(expected_candidate_snapshot, Mapping)
            or not isinstance(candidate_record_after, Mapping)
            or not all(key in certification for key in required)
            or str(certification.get("worker_id")) != str(worker_id)
            or str(certification.get("candidate_id")) != str(candidate_id)
            or not expected_source_identity_fingerprint
            or not expected_scan_report_sha256
        ):
            return None
        certification_values = dict(certification)
        certification_snapshot = _governance_json_snapshot(certification_values)
        candidate_after = _governance_json_snapshot(dict(candidate_record_after))
        status = str(certification_snapshot.get("status") or "").lower()
        target_candidate_status = (
            "CERTIFYING" if status == "passed" else "REJECTED"
        )
        certification_id = UUID(str(certification_values["id"]))
        if (
            status not in {"passed", "rejected"}
            or str(candidate_after.get("candidate_id") or "") != str(candidate_id)
            or str(candidate_after.get("worker_id") or "") != str(worker_id)
            or str(candidate_after.get("intake_status") or "")
            != target_candidate_status
            or str(candidate_after.get("certification_id") or "")
            != str(certification_id)
            or candidate_after.get("approval_record_id")
            != _governance_json_snapshot(expected_candidate_snapshot).get(
                "approval_record_id"
            )
        ):
            return None
        certification_evidence = certification_values.get("evidence_json")
        certification_evidence = (
            certification_evidence
            if isinstance(certification_evidence, Mapping)
            else {}
        )
        if (
            certification_evidence.get("source_identity_fingerprint")
            != expected_source_identity_fingerprint
            or certification_evidence.get("source_bound_security_scan_report_sha256")
            != expected_scan_report_sha256
        ):
            return None
        if compatibility_matrix is not None and str(
            compatibility_matrix.get("worker_id")
        ) != str(worker_id):
            return None
        if status == "passed" and shell_values is None:
            return None
        if shell_values is not None and str(shell_values.get("worker_id")) != str(
            worker_id
        ):
            return None

        async with self.engine.begin() as conn:
            locked = await self._lock_first_party_candidate_governance_rows(
                conn,
                worker_id=worker_id,
                candidate_id=candidate_id,
                expected_worker_identity=expected_worker_identity,
                expected_provenance_hash=expected_provenance_hash,
                expected_scan_report_sha256=expected_scan_report_sha256,
                expected_candidate_snapshot=expected_candidate_snapshot,
            )
            if locked is None:
                return None
            worker, provenance, _steward, candidate = locked
            if (
                candidate["intake_status"] != "CERTIFYING"
                or candidate.get("approval_record_id") is not None
                or candidate.get("worker_id") != worker_id
            ):
                return None

            candidate_record_before = (
                candidate.get("evidence_json") or {}
            ).get("candidate_record")
            candidate_record_before = (
                candidate_record_before
                if isinstance(candidate_record_before, Mapping)
                else {}
            )
            adapter_record = candidate_record_before.get("adapter")
            bundle_record = candidate_record_before.get("bundle")
            adapter_record = adapter_record if isinstance(adapter_record, Mapping) else {}
            bundle_record = bundle_record if isinstance(bundle_record, Mapping) else {}
            bundle_id = candidate.get("skill_bundle_id")
            adapter_id = candidate.get("adapter_id")
            if (
                not adapter_id
                or str(bundle_record.get("bundle_id") or "") != str(bundle_id)
                or str(adapter_record.get("adapter_id") or "") != str(adapter_id)
                or str(candidate_record_before.get("steward_id") or "")
                != str(certification_snapshot.get("steward_id") or "")
            ):
                return None

            bundle = (
                await conn.execute(
                    t.skill_bundles.select()
                    .where(t.skill_bundles.c.id == bundle_id)
                    .with_for_update()
                )
            ).mappings().first()
            adapter = (
                await conn.execute(
                    t.runtime_adapters.select()
                    .where(t.runtime_adapters.c.id == adapter_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                bundle is None
                or adapter is None
                or bundle.get("worker_id") != worker_id
                or str(bundle.get("steward_id"))
                != str(certification_snapshot.get("steward_id") or "")
                or adapter.get("worker_id") != worker_id
                or str(adapter.get("version") or "")
                != str(adapter_record.get("version") or "")
                or str(adapter.get("transport_type") or "").lower()
                != str(adapter_record.get("transport_type") or "").lower()
                or str(adapter.get("implementation_ref") or "")
                != str(adapter_record.get("implementation_ref") or "")
                or str(adapter.get("content_hash") or "")
                != str(adapter_record.get("content_hash") or "")
            ):
                return None

            effective_shell: dict[str, Any] | None = None
            if shell_values is not None:
                shell = _governance_json_snapshot(dict(shell_values))
                shell["id"] = UUID(str(shell["id"]))
                shell["worker_id"] = worker_id
                shell_row = (
                    await conn.execute(
                        t.worker_shell_versions.select()
                        .where(
                            sa.and_(
                                t.worker_shell_versions.c.worker_id == worker_id,
                                t.worker_shell_versions.c.version == shell["version"],
                            )
                        )
                        .with_for_update()
                    )
                ).mappings().first()
                if shell_row is not None:
                    if str(shell_row.get("content_hash") or "") != str(
                        shell.get("content_hash") or ""
                    ):
                        return None
                    effective_shell = dict(shell_row)
                else:
                    await conn.execute(t.worker_shell_versions.insert().values(**shell))
                    effective_shell = shell

            effective_matrix: dict[str, Any] | None = None
            if compatibility_matrix is not None:
                matrix = _governance_json_snapshot(dict(compatibility_matrix))
                matrix["id"] = UUID(str(matrix["id"]))
                matrix["worker_id"] = worker_id
                await conn.execute(
                    t.compatibility_matrices.insert().values(**matrix)
                )
                effective_matrix = matrix

            await conn.execute(
                t.certification_runs.insert().values(**certification_values)
            )
            evidence = dict(candidate.get("evidence_json") or {})
            evidence["candidate_record"] = candidate_after
            if effective_shell is not None:
                evidence["worker_shell_version_id"] = str(effective_shell["id"])
            if effective_matrix is not None:
                evidence["compatibility_matrix_id"] = str(effective_matrix["id"])
            update = (
                t.skill_bundle_candidates.update()
                .where(
                    sa.and_(
                        t.skill_bundle_candidates.c.id == candidate_id,
                        t.skill_bundle_candidates.c.worker_id == worker_id,
                        t.skill_bundle_candidates.c.intake_status == "CERTIFYING",
                        t.skill_bundle_candidates.c.certification_run_id
                        == candidate.get("certification_run_id"),
                        t.skill_bundle_candidates.c.approval_record_id.is_(None),
                    )
                )
                .values(
                    intake_status=target_candidate_status,
                    evidence_json=evidence,
                    certification_run_id=certification_id,
                )
            )
            result = await conn.execute(update)
            if result.rowcount != 1:
                raise RuntimeError("first-party candidate certification CAS failed")
            await conn.execute(
                t.runtime_adapters.update()
                .where(t.runtime_adapters.c.id == adapter_id)
                .values(
                    conformance_status=("passed" if status == "passed" else "failed"),
                    conformance_json=certification_values["conformance_json"],
                )
            )
            persisted_certification = {
                **certification_values,
                "evidence_json": certification_values["evidence_json"],
            }
            persisted_candidate = {
                **candidate,
                "intake_status": target_candidate_status,
                "evidence_json": evidence,
                "certification_run_id": certification_id,
            }
            return {
                "certification": persisted_certification,
                "candidate": persisted_candidate,
                "worker_shell_version": effective_shell,
                "compatibility_matrix": effective_matrix,
            }

    async def approve_first_party_candidate_atomically(
        self,
        *,
        worker_id: UUID,
        candidate_id: UUID,
        expected_worker_identity: Mapping[str, Any],
        expected_provenance_hash: str,
        expected_scan_report_sha256: str,
        expected_source_identity_fingerprint: str,
        expected_candidate_snapshot: Mapping[str, Any],
        candidate_record_after: Mapping[str, Any] | None = None,
        approval: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Approve a first-party candidate and its worker projection atomically."""
        if (
            not isinstance(expected_worker_identity, Mapping)
            or not isinstance(expected_candidate_snapshot, Mapping)
            or not expected_source_identity_fingerprint
            or not expected_scan_report_sha256
        ):
            return None
        expected = _governance_json_snapshot(dict(expected_candidate_snapshot))
        expected_status = str(expected.get("intake_status") or "")
        if expected_status not in {"CERTIFYING", "APPROVED"}:
            return None
        if expected_status == "CERTIFYING" and (
            not isinstance(candidate_record_after, Mapping)
            or not isinstance(approval, Mapping)
        ):
            return None

        async with self.engine.begin() as conn:
            locked = await self._lock_first_party_candidate_governance_rows(
                conn,
                worker_id=worker_id,
                candidate_id=candidate_id,
                expected_worker_identity=expected_worker_identity,
                expected_provenance_hash=expected_provenance_hash,
                expected_scan_report_sha256=expected_scan_report_sha256,
                expected_candidate_snapshot=expected_candidate_snapshot,
            )
            if locked is None:
                return None
            _worker, _provenance, steward, candidate = locked
            if (
                candidate.get("intake_status") != expected_status
                or candidate.get("worker_id") != worker_id
                or candidate.get("certification_run_id") is None
            ):
                return None
            candidate_record = (
                candidate.get("evidence_json") or {}
            ).get("candidate_record")
            candidate_record = (
                candidate_record if isinstance(candidate_record, Mapping) else {}
            )
            if (
                str(candidate_record.get("steward_id") or "")
                != str(steward.get("id") or "")
                or str(candidate_record.get("certification_id") or "")
                != str(candidate.get("certification_run_id") or "")
            ):
                return None
            certification = (
                await conn.execute(
                    t.certification_runs.select()
                    .where(
                        t.certification_runs.c.id
                        == candidate.get("certification_run_id")
                    )
                    .with_for_update()
                )
            ).mappings().first()
            cert_evidence = (
                certification.get("evidence_json") or {}
                if certification is not None
                else {}
            )
            if (
                certification is None
                or certification.get("worker_id") != worker_id
                or certification.get("candidate_id") != candidate_id
                or str(certification.get("status") or "").lower() != "passed"
                or cert_evidence.get("source_identity_fingerprint")
                != expected_source_identity_fingerprint
                or cert_evidence.get("source_bound_security_scan_report_sha256")
                != expected_scan_report_sha256
            ):
                return None

            bundle_id = candidate.get("skill_bundle_id")
            adapter_id = candidate.get("adapter_id")
            bundle = (
                await conn.execute(
                    t.skill_bundles.select()
                    .where(t.skill_bundles.c.id == bundle_id)
                    .with_for_update()
                )
            ).mappings().first()
            adapter = (
                await conn.execute(
                    t.runtime_adapters.select()
                    .where(t.runtime_adapters.c.id == adapter_id)
                    .with_for_update()
                )
            ).mappings().first()
            adapter_record = candidate_record.get("adapter")
            bundle_record = candidate_record.get("bundle")
            adapter_record = adapter_record if isinstance(adapter_record, Mapping) else {}
            bundle_record = bundle_record if isinstance(bundle_record, Mapping) else {}
            if (
                bundle is None
                or adapter is None
                or bundle.get("worker_id") != worker_id
                or bundle.get("steward_id") != steward.get("id")
                or str(bundle_record.get("bundle_id") or "") != str(bundle_id)
                or adapter.get("worker_id") != worker_id
                or str(adapter_record.get("adapter_id") or "") != str(adapter_id)
                or str(adapter.get("conformance_status") or "").lower() != "passed"
                or str(adapter.get("content_hash") or "")
                != str(adapter_record.get("content_hash") or "")
            ):
                return None

            if expected_status == "APPROVED":
                approval_id = candidate.get("approval_record_id")
                if approval_id is None:
                    return None
                approval_row = (
                    await conn.execute(
                        t.approval_records.select()
                        .where(t.approval_records.c.id == approval_id)
                        .with_for_update()
                    )
                ).mappings().first()
                if (
                    approval_row is None
                    or approval_row.get("scope_type")
                    != "worker_skill_bundle_candidate"
                    or approval_row.get("scope_id") != candidate_id
                    or approval_row.get("decision") != "APPROVED"
                ):
                    return None
                return {
                    "candidate": dict(candidate),
                    "approval": dict(approval_row),
                    "idempotent": True,
                }

            approval_values = dict(approval or {})
            candidate_after = _governance_json_snapshot(
                dict(candidate_record_after or {})
            )
            approval_id = UUID(str(approval_values.get("id") or ""))
            if (
                candidate.get("approval_record_id") is not None
                or str(approval_values.get("scope_type") or "")
                != "worker_skill_bundle_candidate"
                or str(approval_values.get("scope_id") or "")
                != str(candidate_id)
                or str(approval_values.get("decision") or "") != "APPROVED"
                or str(candidate_after.get("candidate_id") or "")
                != str(candidate_id)
                or str(candidate_after.get("worker_id") or "")
                != str(worker_id)
                or str(candidate_after.get("intake_status") or "") != "APPROVED"
                or str(candidate_after.get("certification_id") or "")
                != str(candidate.get("certification_run_id") or "")
                or str(candidate_after.get("approval_record_id") or "")
                != str(approval_id)
            ):
                return None
            approval_values["id"] = approval_id
            approval_values["scope_id"] = candidate_id

            evidence = dict(candidate.get("evidence_json") or {})
            evidence["candidate_record"] = candidate_after
            await conn.execute(t.approval_records.insert().values(**approval_values))
            candidate_update = await conn.execute(
                t.skill_bundle_candidates.update()
                .where(
                    sa.and_(
                        t.skill_bundle_candidates.c.id == candidate_id,
                        t.skill_bundle_candidates.c.worker_id == worker_id,
                        t.skill_bundle_candidates.c.intake_status == "CERTIFYING",
                        t.skill_bundle_candidates.c.certification_run_id
                        == candidate.get("certification_run_id"),
                        t.skill_bundle_candidates.c.approval_record_id.is_(None),
                    )
                )
                .values(
                    intake_status="APPROVED",
                    evidence_json=evidence,
                    approval_record_id=approval_id,
                )
            )
            if candidate_update.rowcount != 1:
                raise RuntimeError("first-party candidate approval CAS failed")
            await conn.execute(
                t.skill_bundles.update()
                .where(t.skill_bundles.c.id == bundle_id)
                .values(status="APPROVED")
            )
            await conn.execute(
                t.runtime_adapters.update()
                .where(t.runtime_adapters.c.id == adapter_id)
                .values(
                    status=(
                        "active"
                        if str(adapter.get("status") or "").lower() == "active"
                        else "approved"
                    ),
                    conformance_status="passed",
                )
            )
            await conn.execute(
                t.worker_registry.update()
                .where(t.worker_registry.c.id == worker_id)
                .values(evaluation_status="approved", updated_at=datetime.now(tz=UTC))
            )
            return {
                "candidate": {
                    **candidate,
                    "intake_status": "APPROVED",
                    "evidence_json": evidence,
                    "approval_record_id": approval_id,
                },
                "approval": approval_values,
                "idempotent": False,
            }

    async def approve_steward_candidate_atomically(
        self,
        *,
        worker_id: UUID,
        candidate_id: UUID,
        expected_candidate_snapshot: Mapping[str, Any],
        candidate_record_after: Mapping[str, Any] | None = None,
        approval: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Record a non-first-party candidate approval with locked CAS semantics."""
        if not isinstance(expected_candidate_snapshot, Mapping):
            return None
        expected = _governance_json_snapshot(dict(expected_candidate_snapshot))
        expected_status = str(expected.get("intake_status") or "")
        if expected_status not in {"CERTIFYING", "APPROVED"}:
            return None
        if expected_status == "CERTIFYING" and (
            not isinstance(candidate_record_after, Mapping)
            or not isinstance(approval, Mapping)
        ):
            return None

        async with self.engine.begin() as conn:
            worker = (
                await conn.execute(
                    t.worker_registry.select()
                    .where(t.worker_registry.c.id == worker_id)
                    .with_for_update()
                )
            ).mappings().first()
            steward = (
                await conn.execute(
                    t.steward_agents.select()
                    .where(t.steward_agents.c.worker_id == worker_id)
                    .with_for_update()
                )
            ).mappings().first()
            candidate = (
                await conn.execute(
                    t.skill_bundle_candidates.select()
                    .where(t.skill_bundle_candidates.c.id == candidate_id)
                    .with_for_update()
                )
            ).mappings().first()
            if worker is None or steward is None or candidate is None:
                return None

            current_snapshot = _governance_json_snapshot(
                {
                    key: candidate.get(key)
                    for key in (
                        "id",
                        "worker_id",
                        "skill_bundle_id",
                        "adapter_id",
                        "intake_status",
                        "diff_json",
                        "evidence_json",
                        "certification_run_id",
                        "approval_record_id",
                    )
                }
            )
            current_status = str(candidate.get("intake_status") or "")
            concurrent_approval = False
            if expected_status == "CERTIFYING" and current_status == "APPROVED":
                expected_evidence = expected.get("evidence_json")
                expected_evidence = (
                    expected_evidence if isinstance(expected_evidence, Mapping) else {}
                )
                expected_record = expected_evidence.get("candidate_record")
                expected_record = (
                    dict(expected_record)
                    if isinstance(expected_record, Mapping)
                    else None
                )
                current_evidence = current_snapshot.get("evidence_json")
                current_evidence = (
                    current_evidence if isinstance(current_evidence, Mapping) else {}
                )
                current_record = current_evidence.get("candidate_record")
                current_record = (
                    dict(current_record)
                    if isinstance(current_record, Mapping)
                    else None
                )
                expected_record_matches = (
                    expected_record is not None
                    and str(expected_record.get("candidate_id") or "")
                    == str(candidate_id)
                    and str(expected_record.get("worker_id") or "")
                    == str(worker_id)
                    and str(expected_record.get("steward_id") or "")
                    == str(steward.get("id") or "")
                    and str(expected_record.get("intake_status") or "")
                    == "CERTIFYING"
                    and expected_record.get("approval_record_id") is None
                    and str(expected_record.get("certification_id") or "")
                    == str(expected.get("certification_run_id") or "")
                )
                if expected_record is not None and current_record is not None:
                    for record in (expected_record, current_record):
                        record.pop("intake_status", None)
                        record.pop("approval_record_id", None)
                        for field in ("bundle", "adapter"):
                            nested = record.get(field)
                            if isinstance(nested, Mapping):
                                nested = dict(nested)
                                nested.pop("status", None)
                                record[field] = nested
                    expected_stable = {
                        key: expected.get(key)
                        for key in (
                            "id",
                            "worker_id",
                            "skill_bundle_id",
                            "adapter_id",
                            "diff_json",
                            "certification_run_id",
                        )
                    }
                    current_stable = {
                        key: current_snapshot.get(key)
                        for key in expected_stable
                    }
                    concurrent_approval = (
                        current_stable == expected_stable
                        and expected_record_matches
                        and current_snapshot.get("approval_record_id") is not None
                        and current_record == expected_record
                    )
            if (
                candidate.get("worker_id") != worker_id
                or str(candidate.get("id") or "") != str(candidate_id)
                or (
                    current_snapshot != expected
                    and not concurrent_approval
                )
                or (
                    current_status != expected_status
                    and not concurrent_approval
                )
            ):
                return None

            evidence = candidate.get("evidence_json")
            evidence = evidence if isinstance(evidence, Mapping) else {}
            candidate_record = evidence.get("candidate_record")
            candidate_record = (
                candidate_record if isinstance(candidate_record, Mapping) else {}
            )
            certification_id = candidate.get("certification_run_id")
            if (
                not certification_id
                or str(candidate_record.get("candidate_id") or "")
                != str(candidate_id)
                or str(candidate_record.get("worker_id") or "")
                != str(worker_id)
                or str(candidate_record.get("steward_id") or "")
                != str(steward.get("id") or "")
                or str(candidate_record.get("intake_status") or "")
                != current_status
                or str(candidate_record.get("certification_id") or "")
                != str(certification_id)
                or str(candidate_record.get("approval_record_id") or "")
                != str(candidate.get("approval_record_id") or "")
            ):
                return None

            certification = (
                await conn.execute(
                    t.certification_runs.select()
                    .where(t.certification_runs.c.id == certification_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                certification is None
                or certification.get("worker_id") != worker_id
                or certification.get("candidate_id") != candidate_id
                or certification.get("steward_id") != steward.get("id")
                or str(certification.get("status") or "").lower() != "passed"
            ):
                return None

            bundle_id = candidate.get("skill_bundle_id")
            adapter_id = candidate.get("adapter_id")
            if not bundle_id or not adapter_id:
                return None
            bundle = (
                await conn.execute(
                    t.skill_bundles.select()
                    .where(t.skill_bundles.c.id == bundle_id)
                    .with_for_update()
                )
            ).mappings().first()
            adapter = (
                await conn.execute(
                    t.runtime_adapters.select()
                    .where(t.runtime_adapters.c.id == adapter_id)
                    .with_for_update()
                )
            ).mappings().first()
            bundle_record = candidate_record.get("bundle")
            adapter_record = candidate_record.get("adapter")
            bundle_record = bundle_record if isinstance(bundle_record, Mapping) else {}
            adapter_record = adapter_record if isinstance(adapter_record, Mapping) else {}
            bundle_snapshot_hash = _skill_bundle_snapshot_content_hash(bundle_record)
            if (
                bundle is None
                or str(bundle.get("id") or "") != str(bundle_id)
                or bundle.get("worker_id") != worker_id
                or bundle.get("steward_id") != steward.get("id")
                or str(bundle_record.get("bundle_id") or "") != str(bundle_id)
                or bundle_snapshot_hash is None
                or str(bundle.get("content_hash") or "") != bundle_snapshot_hash
                or adapter is None
                or str(adapter.get("id") or "") != str(adapter_id)
                or adapter.get("worker_id") != worker_id
                or str(adapter_record.get("adapter_id") or "") != str(adapter_id)
                or not str(adapter_record.get("content_hash") or "").strip()
                or str(adapter.get("content_hash") or "")
                != str(adapter_record.get("content_hash") or "")
                or str(adapter.get("conformance_status") or "").lower() != "passed"
            ):
                return None

            if current_status == "APPROVED":
                approval_id = candidate.get("approval_record_id")
                if approval_id is None:
                    return None
                approval_row = (
                    await conn.execute(
                        t.approval_records.select()
                        .where(t.approval_records.c.id == approval_id)
                        .with_for_update()
                    )
                ).mappings().first()
                if (
                    approval_row is None
                    or approval_row.get("scope_type")
                    != "worker_skill_bundle_candidate"
                    or approval_row.get("scope_id") != candidate_id
                    or approval_row.get("decision") != "APPROVED"
                ):
                    return None
                return {
                    "candidate": dict(candidate),
                    "approval": dict(approval_row),
                    "idempotent": True,
                }

            approval_values = dict(approval or {})
            try:
                approval_id = UUID(str(approval_values.get("id") or ""))
            except (TypeError, ValueError):
                return None
            candidate_after = _governance_json_snapshot(
                dict(candidate_record_after or {})
            )
            candidate_before_snapshot = _governance_json_snapshot(
                dict(candidate_record)
            )
            proposed_bundle = candidate_after.get("bundle")
            proposed_adapter = candidate_after.get("adapter")
            locked_bundle_record = candidate_before_snapshot.get("bundle")
            locked_adapter_record = candidate_before_snapshot.get("adapter")
            if (
                not isinstance(proposed_bundle, Mapping)
                or not isinstance(proposed_adapter, Mapping)
                or not isinstance(locked_bundle_record, Mapping)
                or not isinstance(locked_adapter_record, Mapping)
                or str(proposed_bundle.get("status") or "").upper() != "APPROVED"
                or str(proposed_adapter.get("status") or "").upper() != "APPROVED"
                or not str(locked_bundle_record.get("status") or "").strip()
                or not str(locked_adapter_record.get("status") or "").strip()
            ):
                return None

            proposed_immutable_snapshot = _governance_json_snapshot(candidate_after)
            proposed_immutable_snapshot["intake_status"] = (
                candidate_before_snapshot.get("intake_status")
            )
            proposed_immutable_snapshot["approval_record_id"] = (
                candidate_before_snapshot.get("approval_record_id")
            )
            for field, locked_record in (
                ("bundle", locked_bundle_record),
                ("adapter", locked_adapter_record),
            ):
                proposed_record = dict(proposed_immutable_snapshot[field])
                proposed_record["status"] = locked_record.get("status")
                proposed_immutable_snapshot[field] = proposed_record
            if proposed_immutable_snapshot != candidate_before_snapshot:
                return None

            if (
                candidate.get("approval_record_id") is not None
                or str(approval_values.get("scope_type") or "")
                != "worker_skill_bundle_candidate"
                or str(approval_values.get("scope_id") or "")
                != str(candidate_id)
                or str(approval_values.get("decision") or "") != "APPROVED"
                or not str(approval_values.get("decided_by") or "").strip()
                or str(candidate_after.get("candidate_id") or "")
                != str(candidate_id)
                or str(candidate_after.get("worker_id") or "")
                != str(worker_id)
                or str(candidate_after.get("steward_id") or "")
                != str(steward.get("id") or "")
                or str(candidate_after.get("intake_status") or "") != "APPROVED"
                or str(candidate_after.get("certification_id") or "")
                != str(certification_id)
                or str(candidate_after.get("approval_record_id") or "")
                != str(approval_id)
            ):
                return None

            approval_values["id"] = approval_id
            approval_values["scope_id"] = candidate_id
            evidence_after = dict(evidence)
            evidence_after["candidate_record"] = candidate_after
            await conn.execute(t.approval_records.insert().values(**approval_values))
            candidate_update = await conn.execute(
                t.skill_bundle_candidates.update()
                .where(
                    sa.and_(
                        t.skill_bundle_candidates.c.id == candidate_id,
                        t.skill_bundle_candidates.c.worker_id == worker_id,
                        t.skill_bundle_candidates.c.intake_status == "CERTIFYING",
                        t.skill_bundle_candidates.c.certification_run_id
                        == certification_id,
                        t.skill_bundle_candidates.c.approval_record_id.is_(None),
                    )
                )
                .values(
                    intake_status="APPROVED",
                    evidence_json=evidence_after,
                    approval_record_id=approval_id,
                )
            )
            if candidate_update.rowcount != 1:
                raise RuntimeError("steward candidate approval CAS failed")
            await conn.execute(
                t.skill_bundles.update()
                .where(t.skill_bundles.c.id == bundle_id)
                .values(status="APPROVED")
            )
            await conn.execute(
                t.runtime_adapters.update()
                .where(t.runtime_adapters.c.id == adapter_id)
                .values(
                    status=(
                        "active"
                        if str(adapter.get("status") or "").lower() == "active"
                        else "approved"
                    ),
                    conformance_status="passed",
                )
            )
            await conn.execute(
                t.worker_registry.update()
                .where(t.worker_registry.c.id == worker_id)
                .values(evaluation_status="approved", updated_at=datetime.now(tz=UTC))
            )
            return {
                "candidate": {
                    **candidate,
                    "intake_status": "APPROVED",
                    "evidence_json": evidence_after,
                    "approval_record_id": approval_id,
                },
                "approval": approval_values,
                "idempotent": False,
            }

    async def update_skill_bundle(self, bundle_id: UUID, *, status: str | None = None) -> dict[str, Any] | None:
        if status is None:
            return await self.get_skill_bundle(bundle_id)
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.skill_bundles.update().where(t.skill_bundles.c.id == bundle_id).values(status=status)
            )
            if result.rowcount == 0:
                return None
        return await self.get_skill_bundle(bundle_id)

    async def update_runtime_adapter(self, adapter_id: UUID, *, status: str | None = None, conformance_status: str | None = None, conformance: dict[str, Any] | None = None) -> dict[str, Any] | None:
        values = {key: value for key, value in (("status", status), ("conformance_status", conformance_status), ("conformance_json", conformance)) if value is not None}
        if not values:
            return await self.get_runtime_adapter(adapter_id)
        async with self.engine.begin() as conn:
            result = await conn.execute(t.runtime_adapters.update().where(t.runtime_adapters.c.id == adapter_id).values(**values))
            if result.rowcount == 0:
                return None
        return await self.get_runtime_adapter(adapter_id)

    async def create_certification_run(self, *, certification_id: UUID, worker_id: UUID, candidate_id: UUID, steward_id: UUID | None, status: str, conformance: dict[str, Any], checks: dict[str, Any], evidence: dict[str, Any] | None = None, failure_reasons: list[str] | None = None, completed_at: datetime | None = None) -> dict[str, Any]:
        values = {"id": certification_id, "worker_id": worker_id, "steward_id": steward_id, "candidate_id": candidate_id, "status": status, "conformance_json": conformance, "checks_json": checks, "evidence_json": evidence or {}, "failure_reasons": failure_reasons or [], "completed_at": completed_at}
        async with self.engine.begin() as conn:
            await conn.execute(t.certification_runs.insert().values(**values))
        return values

    async def create_approval_record(
        self,
        *,
        scope_type: str,
        scope_id: UUID,
        decision: str,
        decided_by: str,
        reason: str | None = None,
        evidence: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        values = {
            "id": uuid4(),
            "scope_type": scope_type,
            "scope_id": scope_id,
            "decision": decision,
            "decided_by": decided_by,
            "reason": reason,
            "evidence": evidence or {},
            "expires_at": expires_at,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.approval_records.insert().values(**values))
        return values

    async def get_approval_record(self, approval_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.approval_records, t.approval_records.c.id, approval_id)

    async def create_update_monitoring_job(
        self,
        *,
        worker_id: UUID,
        steward_id: UUID | None,
        cadence: str = "daily",
    ) -> dict[str, Any]:
        values = {
            "id": uuid4(),
            "worker_id": worker_id,
            "steward_id": steward_id,
            "cadence": cadence,
            "status": "active",
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.update_monitoring_jobs.insert().values(**values))
        return values

    async def list_update_monitoring_jobs(self, *, worker_id: UUID | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = t.update_monitoring_jobs.select().order_by(t.update_monitoring_jobs.c.created_at.desc()).limit(limit)
        if worker_id is not None:
            query = query.where(t.update_monitoring_jobs.c.worker_id == worker_id)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def record_update_monitoring_result(
        self,
        job_id: UUID,
        *,
        last_candidate_id: UUID | None = None,
        last_error: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        values: dict[str, Any] = {"last_checked_at": datetime.now(tz=UTC)}
        if last_candidate_id is not None:
            values["last_candidate_id"] = last_candidate_id
        if last_error is not None:
            values["last_error"] = last_error
        if status is not None:
            values["status"] = status
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.update_monitoring_jobs.update()
                .where(t.update_monitoring_jobs.c.id == job_id)
                .values(**values)
            )
            if result.rowcount == 0:
                return None
        return await self._get_table_row(t.update_monitoring_jobs, t.update_monitoring_jobs.c.id, job_id)

    async def update_certification_run(self, certification_id: UUID, *, status: str | None = None, evidence: dict[str, Any] | None = None, failure_reasons: list[str] | None = None, completed_at: datetime | None = None) -> dict[str, Any] | None:
        values = {key: value for key, value in (("status", status), ("evidence_json", evidence), ("failure_reasons", failure_reasons), ("completed_at", completed_at)) if value is not None}
        if not values:
            return await self._get_table_row(t.certification_runs, t.certification_runs.c.id, certification_id)
        async with self.engine.begin() as conn:
            result = await conn.execute(t.certification_runs.update().where(t.certification_runs.c.id == certification_id).values(**values))
            if result.rowcount == 0:
                return None
        return await self._get_table_row(t.certification_runs, t.certification_runs.c.id, certification_id)

    async def list_certification_runs(self, worker_id: UUID) -> list[dict[str, Any]]:
        query = t.certification_runs.select().where(t.certification_runs.c.worker_id == worker_id).order_by(t.certification_runs.c.started_at.asc())
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def get_model_profile(self, logical_profile_id: str) -> dict[str, Any] | None:
        profile = await self._get_table_row(
            t.model_profiles,
            t.model_profiles.c.logical_profile_id,
            logical_profile_id,
        )
        if profile is None:
            return None
        async with self.engine.connect() as conn:
            versions = (
                await conn.execute(
                    t.model_profile_versions.select()
                    .where(t.model_profile_versions.c.profile_id == profile["id"])
                    .order_by(t.model_profile_versions.c.version.desc())
                )
            ).mappings().all()
        return {**profile, "versions": [dict(row) for row in versions]}

    async def list_model_profiles(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            profile_rows = (await conn.execute(t.model_profiles.select().order_by(t.model_profiles.c.logical_profile_id.asc()))).mappings().all()
            version_rows = (await conn.execute(t.model_profile_versions.select().order_by(t.model_profile_versions.c.version.desc()))).mappings().all()
        versions_by_profile: dict[UUID, list[dict[str, Any]]] = {}
        for row in version_rows:
            versions_by_profile.setdefault(row["profile_id"], []).append(dict(row))
        return [{**dict(row), "versions": versions_by_profile.get(row["id"], [])} for row in profile_rows]

    async def create_model_profile(self, *, logical_profile_id: str, purpose: str, approved_provider_ids: list[str] | None = None, required_capabilities: list[str] | None = None, fallback_profile_ids: list[str] | None = None, status: str = "draft", owner: str = "aiat") -> dict[str, Any]:
        values = {"id": uuid4(), "logical_profile_id": logical_profile_id, "purpose": purpose, "approved_provider_ids": approved_provider_ids or [], "required_capabilities": required_capabilities or [], "fallback_profile_ids": fallback_profile_ids or [], "status": status, "owner": owner}
        async with self.engine.begin() as conn:
            await conn.execute(t.model_profiles.insert().values(**values))
        return values

    async def create_model_profile_version(self, *, profile_id: UUID, version: str, provider_id: str, exact_model_id: str, capabilities: list[str] | None = None, constraints: dict[str, Any] | None = None, provider_settings: dict[str, Any] | None = None, status: str = "draft", api_version: str | None = None, effective_from: datetime | None = None, effective_until: datetime | None = None, version_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if exact_model_id.lower() in {"auto", "default", "latest"}:
            raise ValueError("raw/unmanaged model IDs are not permitted")
        values = {"id": uuid4(), "profile_id": profile_id, "version": version, "provider_id": provider_id, "exact_model_id": exact_model_id, "api_version": api_version, "capabilities": capabilities or [], "constraints_json": {**(constraints or {}), **(version_metadata or {})}, "provider_settings": provider_settings or {}, "status": status, "effective_from": effective_from, "effective_until": effective_until}
        async with self.engine.begin() as conn:
            await conn.execute(t.model_profile_versions.insert().values(**values))
        return values

    async def activate_model_profile_version(
        self,
        *,
        logical_profile_id: str,
        version_id: UUID,
        decided_by: str,
        reason: str,
    ) -> dict[str, Any] | None:
        """Atomically approve one immutable draft version and persist its audit."""
        async with self.engine.begin() as conn:
            profile_result = await conn.execute(
                t.model_profiles.select()
                .where(t.model_profiles.c.logical_profile_id == logical_profile_id)
                .with_for_update()
            )
            profile_row = profile_result.mappings().first()
            if profile_row is None:
                return None
            profile = dict(profile_row)

            version_result = await conn.execute(
                t.model_profile_versions.select()
                .where(
                    t.model_profile_versions.c.id == version_id,
                    t.model_profile_versions.c.profile_id == profile["id"],
                )
                .with_for_update()
            )
            version_row = version_result.mappings().first()
            if version_row is None:
                return None
            version = dict(version_row)

            snapshot = {
                "profile_id": profile["logical_profile_id"],
                "profile_record_id": str(profile["id"]),
                "profile_purpose": profile["purpose"],
                "approved_provider_ids": sorted(profile.get("approved_provider_ids") or []),
                "required_capabilities": sorted(profile.get("required_capabilities") or []),
                "fallback_profile_ids": sorted(profile.get("fallback_profile_ids") or []),
                "version_id": str(version["id"]),
                "version": version["version"],
                "provider_id": version["provider_id"],
                "exact_model_id": version["exact_model_id"],
                "api_version": version.get("api_version"),
                "capabilities": sorted(version.get("capabilities") or []),
                "constraints": version.get("constraints_json") or {},
                "provider_settings": version.get("provider_settings") or {},
                "effective_from": version.get("effective_from"),
                "effective_until": version.get("effective_until"),
            }
            snapshot_sha256 = hashlib.sha256(
                json.dumps(
                    snapshot,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()

            profile_status = str(profile.get("status") or "draft").lower()
            version_status = str(version.get("status") or "draft").lower()
            if version_status == "approved":
                prior_result = await conn.execute(
                    t.approval_records.select()
                    .where(
                        t.approval_records.c.scope_type == "model_profile_version",
                        t.approval_records.c.scope_id == version_id,
                        t.approval_records.c.decision == "APPROVED",
                    )
                    .order_by(t.approval_records.c.created_at.desc())
                    .limit(1)
                )
                prior_row = prior_result.mappings().first()
                if prior_row is not None:
                    approval_record = dict(prior_row)
                    if (approval_record.get("evidence") or {}).get("version_snapshot_sha256") == snapshot_sha256:
                        return {
                            "profile": profile,
                            "version": version,
                            "approval_record": approval_record,
                            "already_approved": True,
                        }
                raise ValueError("model profile version is already approved without a matching audit record")
            if version_status != "draft":
                raise ValueError("only a draft model profile version can be approved")
            if profile_status not in {"draft", "approved"}:
                raise ValueError(f"model profile status {profile_status!r} cannot be activated")

            version_update = await conn.execute(
                t.model_profile_versions.update()
                .where(
                    t.model_profile_versions.c.id == version_id,
                    t.model_profile_versions.c.status == "draft",
                )
                .values(status="approved")
            )
            if version_update.rowcount != 1:
                raise ValueError("model profile version changed before approval could be committed")
            profile_update = await conn.execute(
                t.model_profiles.update()
                .where(t.model_profiles.c.id == profile["id"])
                .values(status="approved")
            )
            if profile_update.rowcount != 1:
                raise ValueError("model profile changed before approval could be committed")

            approval_values = {
                "id": uuid4(),
                "scope_type": "model_profile_version",
                "scope_id": version_id,
                "decision": "APPROVED",
                "decided_by": decided_by,
                "reason": reason,
                "evidence": {
                    "profile_id": profile["logical_profile_id"],
                    "profile_record_id": str(profile["id"]),
                    "version_id": str(version["id"]),
                    "version": version["version"],
                    "provider_id": version["provider_id"],
                    "exact_model_id": version["exact_model_id"],
                    "version_snapshot_sha256": snapshot_sha256,
                },
            }
            approval_result = await conn.execute(
                t.approval_records.insert()
                .values(**approval_values)
                .returning(*t.approval_records.columns)
            )
            approval_row = approval_result.mappings().first()
            if approval_row is None:
                raise RuntimeError("model profile approval audit record was not persisted")

            profile["status"] = "approved"
            version["status"] = "approved"
            return {
                "profile": profile,
                "version": version,
                "approval_record": dict(approval_row),
                "already_approved": False,
            }

    async def create_model_override_request(
        self,
        *,
        project_id: UUID,
        requested_by: str,
        requested_profile_id: str,
        reason: str,
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = {
            "id": uuid4(),
            "project_id": project_id,
            "requested_by": requested_by,
            "requested_profile_id": requested_profile_id,
            "reason": reason,
            "scope": scope or {},
            "status": "PENDING",
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.model_override_requests.insert().values(**values))
        return values

    async def get_model_override_request(self, request_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.model_override_requests, t.model_override_requests.c.id, request_id)

    async def update_model_override_request(
        self,
        request_id: UUID,
        *,
        status: str,
        decided_by: str,
        decision: str,
        expires_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        values = {
            "status": status,
            "decided_by": decided_by,
            "decision": decision,
            "expires_at": expires_at,
            "decided_at": datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            result = await conn.execute(
                t.model_override_requests.update()
                .where(t.model_override_requests.c.id == request_id)
                .values(**values)
            )
            if result.rowcount == 0:
                return None
        return await self.get_model_override_request(request_id)

    async def create_rollout_record(
        self,
        *,
        rollout_id: UUID,
        worker_id: UUID,
        steward_id: UUID,
        candidate_id: UUID,
        expected_candidate_snapshot: Mapping[str, Any] | None = None,
        status: str,
        eligible_task_classes: list[str],
        sample_targets: dict[str, Any],
        comparison_metrics: dict[str, Any] | None = None,
        rollback_thresholds: dict[str, Any] | None = None,
        promotion_actor: str | None = None,
    ) -> dict[str, Any] | None:
        if status != "PENDING" or not isinstance(expected_candidate_snapshot, Mapping):
            return None
        expected_record = _governance_json_snapshot(dict(expected_candidate_snapshot))
        values = {"id": rollout_id, "worker_id": worker_id, "steward_id": steward_id, "candidate_id": candidate_id, "status": status, "eligible_task_classes": eligible_task_classes, "sample_targets": sample_targets, "comparison_metrics": comparison_metrics or {}, "rollback_thresholds": rollback_thresholds or {}, "promotion_actor": promotion_actor}
        async with self.engine.begin() as conn:
            worker = (
                await conn.execute(
                    t.worker_registry.select()
                    .where(t.worker_registry.c.id == worker_id)
                    .with_for_update()
                )
            ).mappings().first()
            steward = (
                await conn.execute(
                    t.steward_agents.select()
                    .where(
                        sa.and_(
                            t.steward_agents.c.id == steward_id,
                            t.steward_agents.c.worker_id == worker_id,
                        )
                    )
                    .with_for_update()
                )
            ).mappings().first()
            candidate = (
                await conn.execute(
                    t.skill_bundle_candidates.select()
                    .where(t.skill_bundle_candidates.c.id == candidate_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                worker is None
                or steward is None
                or candidate is None
                or candidate.get("worker_id") != worker_id
                or str(candidate.get("id") or "") != str(candidate_id)
                or str(candidate.get("intake_status") or "") != "APPROVED"
            ):
                return None
            evidence = candidate.get("evidence_json")
            evidence = evidence if isinstance(evidence, Mapping) else {}
            raw_candidate_record = evidence.get("candidate_record")
            if not isinstance(raw_candidate_record, Mapping):
                return None
            candidate_record = _governance_json_snapshot(dict(raw_candidate_record))
            if (
                candidate_record != expected_record
                or str(candidate_record.get("candidate_id") or "")
                != str(candidate_id)
                or str(candidate_record.get("worker_id") or "")
                != str(worker_id)
                or str(candidate_record.get("steward_id") or "")
                != str(steward_id)
                or str(candidate_record.get("intake_status") or "") != "APPROVED"
            ):
                return None
            certification_id = candidate.get("certification_run_id")
            approval_id = candidate.get("approval_record_id")
            if (
                certification_id is None
                or approval_id is None
                or str(candidate_record.get("candidate_id") or "")
                != str(candidate_id)
                or str(candidate_record.get("worker_id") or "")
                != str(worker_id)
                or str(candidate_record.get("steward_id") or "")
                != str(steward_id)
                or str(candidate_record.get("intake_status") or "") != "APPROVED"
                or str(candidate_record.get("certification_id") or "")
                != str(certification_id)
                or str(candidate_record.get("approval_record_id") or "")
                != str(approval_id)
            ):
                return None
            certification = (
                await conn.execute(
                    t.certification_runs.select()
                    .where(t.certification_runs.c.id == certification_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                certification is None
                or certification.get("worker_id") != worker_id
                or certification.get("candidate_id") != candidate_id
                or certification.get("steward_id") != steward_id
                or str(certification.get("status") or "").lower() != "passed"
            ):
                return None
            approval = (
                await conn.execute(
                    t.approval_records.select()
                    .where(t.approval_records.c.id == approval_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                approval is None
                or approval.get("scope_type") != "worker_skill_bundle_candidate"
                or approval.get("scope_id") != candidate_id
                or approval.get("decision") != "APPROVED"
            ):
                return None
            bundle_id = candidate.get("skill_bundle_id")
            adapter_id = candidate.get("adapter_id")
            bundle_record = candidate_record.get("bundle")
            bundle_record = (
                bundle_record if isinstance(bundle_record, Mapping) else {}
            )
            adapter_record = candidate_record.get("adapter")
            adapter_record = (
                adapter_record if isinstance(adapter_record, Mapping) else {}
            )
            if (
                str(bundle_record.get("bundle_id") or "") != str(bundle_id or "")
                or str(adapter_record.get("adapter_id") or "") != str(adapter_id or "")
                or str(bundle_record.get("steward_id") or "") != str(steward_id)
                or str(adapter_record.get("steward_id") or "") != str(steward_id)
            ):
                return None
            bundle = (
                await conn.execute(
                    t.skill_bundles.select()
                    .where(t.skill_bundles.c.id == bundle_id)
                    .with_for_update()
                )
            ).mappings().first()
            adapter = (
                await conn.execute(
                    t.runtime_adapters.select()
                    .where(t.runtime_adapters.c.id == adapter_id)
                    .with_for_update()
                )
            ).mappings().first()
            bundle_snapshot_hash = _skill_bundle_snapshot_content_hash(bundle_record)
            if (
                bundle is None
                or bundle.get("worker_id") != worker_id
                or bundle.get("steward_id") != steward_id
                or str(bundle.get("id") or "") != str(bundle_id)
                or bundle_snapshot_hash is None
                or str(bundle.get("content_hash") or "") != bundle_snapshot_hash
                or adapter is None
                or str(adapter.get("id") or "") != str(adapter_id)
                or adapter.get("worker_id") != worker_id
                or str(adapter_record.get("adapter_id") or "")
                != str(adapter_id)
                or str(adapter.get("content_hash") or "")
                != str(adapter_record.get("content_hash") or "")
                or str(adapter.get("conformance_status") or "").lower()
                != "passed"
            ):
                return None
            prior_rollouts = (
                await conn.execute(
                    t.rollout_records.select()
                    .where(t.rollout_records.c.worker_id == worker_id)
                    .with_for_update()
                )
            ).mappings().all()
            if any(
                row.get("candidate_id") == candidate_id
                or row.get("status") in {"PENDING", "SHADOW", "CANARY", "PROMOTING", "ROLLING_BACK"}
                for row in prior_rollouts
            ):
                return None
            await conn.execute(t.rollout_records.insert().values(**values))
        return values

    async def get_rollout_record(self, rollout_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.rollout_records, t.rollout_records.c.id, rollout_id)

    async def list_rollout_records(self, worker_id: UUID) -> list[dict[str, Any]]:
        query = t.rollout_records.select().where(t.rollout_records.c.worker_id == worker_id).order_by(t.rollout_records.c.started_at.asc())
        async with self.engine.connect() as conn:
            rows = (await conn.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def update_rollout_record(self, rollout_id: UUID, *, status: str | None = None, sample_count: int | None = None, comparison_metrics: dict[str, Any] | None = None, rollback_reason: str | None = None, completed_at: datetime | None = None) -> dict[str, Any] | None:
        values = {key: value for key, value in (("status", status), ("sample_count", sample_count), ("comparison_metrics", comparison_metrics), ("rollback_reason", rollback_reason), ("completed_at", completed_at)) if value is not None}
        if not values:
            return await self.get_rollout_record(rollout_id)
        async with self.engine.begin() as conn:
            result = await conn.execute(t.rollout_records.update().where(t.rollout_records.c.id == rollout_id).values(**values))
            if result.rowcount == 0:
                return None
        return await self.get_rollout_record(rollout_id)

    async def transition_rollout(
        self,
        rollout_id: UUID,
        *,
        worker_id: UUID,
        steward_id: UUID,
        candidate_id: UUID,
        to_status: str,
        actor: str,
        reason: str | None = None,
        sample_count: int | None = None,
        comparison_metrics: dict[str, Any] | None = None,
        completed_at: datetime | None = None,
        expected_status: str | None = None,
        expected_rollout_snapshot: Mapping[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(expected_rollout_snapshot, Mapping):
            return None
        expected_snapshot = _governance_json_snapshot(dict(expected_rollout_snapshot))
        async with self.engine.begin() as conn:
            current = (
                await conn.execute(
                    t.rollout_records.select()
                    .where(t.rollout_records.c.id == rollout_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                current is None
                or current["worker_id"] != worker_id
                or current["steward_id"] != steward_id
                or current["candidate_id"] != candidate_id
                or (expected_status is not None and current["status"] != expected_status)
                or _governance_json_snapshot(dict(current)) != expected_snapshot
            ):
                return None
            legal_transitions = {
                "PENDING": {"SHADOW"},
                "SHADOW": {"CANARY"},
                "CANARY": {"PROMOTING"},
            }
            if to_status not in legal_transitions.get(str(current["status"]), set()):
                return None
            current_sample_count = current.get("sample_count")
            next_sample_count = (
                current_sample_count if sample_count is None else sample_count
            )
            if (
                type(current_sample_count) is not int
                or type(next_sample_count) is not int
                or next_sample_count < current_sample_count
            ):
                return None
            if to_status in {"CANARY", "PROMOTING"}:
                sample_targets = current.get("sample_targets")
                if not isinstance(sample_targets, Mapping):
                    return None
                target_key = "shadow" if to_status == "CANARY" else "readonly_canary"
                required_samples = sample_targets.get(target_key)
                if (
                    type(required_samples) is not int
                    or next_sample_count < required_samples
                ):
                    return None
                if to_status == "CANARY" and (
                    not current.get("eligible_task_classes")
                    or any(
                        task_class not in {"read_only", "low_risk", "shadow"}
                        for task_class in current["eligible_task_classes"]
                    )
                ):
                    return None
            values: dict[str, Any] = {"status": to_status}
            values["sample_count"] = next_sample_count
            if comparison_metrics is not None:
                values["comparison_metrics"] = comparison_metrics
            if completed_at is not None:
                values["completed_at"] = completed_at
            result = await conn.execute(
                t.rollout_records.update()
                .where(
                    sa.and_(
                        t.rollout_records.c.id == rollout_id,
                        t.rollout_records.c.worker_id == worker_id,
                        t.rollout_records.c.steward_id == steward_id,
                        t.rollout_records.c.candidate_id == candidate_id,
                        t.rollout_records.c.status == current["status"],
                    )
                )
                .values(**values)
            )
            if result.rowcount != 1:
                return None
            await conn.execute(
                t.rollout_transitions.insert().values(
                    id=uuid4(),
                    rollout_id=rollout_id,
                    from_status=current["status"],
                    to_status=to_status,
                    actor=actor,
                    reason=reason,
                    evidence=evidence or {},
                )
            )
        return await self.get_rollout_record(rollout_id)

    async def activate_rollout_atomically(
        self,
        *,
        rollout_id: UUID,
        worker_id: UUID,
        steward_id: UUID,
        candidate_id: UUID,
        completed_at: datetime | None,
        expected_worker_identity: Mapping[str, Any] | None = None,
        expected_provenance_hash: str | None = None,
        expected_rollout_snapshot: Mapping[str, Any] | None = None,
        expected_candidate_snapshot: Mapping[str, Any] | None = None,
        sample_count: int | None = None,
        comparison_metrics: Mapping[str, Any] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any] | None:
        """Promote one candidate with a worker-row lock and compare-and-set.

        The Worker Run keeps immutable version references, so this method only
        moves pointers for *new* dispatches after the rollout state is still
        PROMOTING and no other candidate owns an in-flight rollout. The
        expected worker identity and provenance hash come from the API's
        promotion precheck. Missing or stale snapshots fail closed before any
        active pointer or rollout status is updated.
        """
        if (
            not isinstance(expected_worker_identity, Mapping)
            or set(expected_worker_identity) != set(_ROLLOUT_WORKER_IDENTITY_FIELDS)
            or not isinstance(expected_provenance_hash, str)
            or len(expected_provenance_hash) != 64
            or any(char not in "0123456789abcdef" for char in expected_provenance_hash)
            or not isinstance(expected_rollout_snapshot, Mapping)
            or not isinstance(expected_candidate_snapshot, Mapping)
            or type(sample_count) is not int
            or sample_count < 0
            or not isinstance(comparison_metrics, Mapping)
            or not isinstance(actor, str)
            or not actor.strip()
        ):
            return None
        expected_identity = json.loads(
            json.dumps(
                dict(expected_worker_identity),
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
        active_states = {"PENDING", "SHADOW", "CANARY", "PROMOTING", "ROLLING_BACK"}
        expected_rollout = _governance_json_snapshot(dict(expected_rollout_snapshot))
        expected_candidate = _governance_json_snapshot(dict(expected_candidate_snapshot))
        accepted_metrics = _governance_json_snapshot(dict(comparison_metrics))
        async with self.engine.begin() as conn:
            worker = (
                await conn.execute(
                    t.worker_registry.select()
                    .where(t.worker_registry.c.id == worker_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                worker is None
                or worker_identity_snapshot_for_rollout(worker) != expected_identity
            ):
                return None
            provenance = (
                await conn.execute(
                    t.external_runtime_provenance.select()
                    .where(t.external_runtime_provenance.c.worker_id == worker_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                provenance is None
                or provenance.get("worker_id") != worker_id
                or str(provenance.get("provenance_hash") or "")
                != expected_provenance_hash
            ):
                return None
            rollout = (
                await conn.execute(
                    t.rollout_records.select()
                    .where(t.rollout_records.c.id == rollout_id)
                    .with_for_update()
                )
            ).mappings().first()
            if rollout is None:
                return None
            if (
                rollout["worker_id"] != worker_id
                or rollout["steward_id"] != steward_id
                or rollout["candidate_id"] != candidate_id
                or rollout["status"] != "PROMOTING"
                or _governance_json_snapshot(dict(rollout)) != expected_rollout
            ):
                return None
            sample_targets = rollout.get("sample_targets")
            rollback_thresholds = rollout.get("rollback_thresholds")
            if not isinstance(sample_targets, Mapping) or not isinstance(
                rollback_thresholds, Mapping
            ):
                return None
            live_sample_target = sample_targets.get("live_canary")
            existing_sample_count = rollout.get("sample_count")
            try:
                regression_fraction = float(
                    accepted_metrics.get("regression_fraction", 0.0)
                )
                irreversible_side_effects = float(
                    accepted_metrics.get("irreversible_side_effects", 0.0)
                )
                regression_threshold = float(
                    rollback_thresholds.get("regression_fraction", 0.10)
                )
            except (TypeError, ValueError):
                return None
            if (
                type(live_sample_target) is not int
                or type(existing_sample_count) is not int
                or sample_count < existing_sample_count
                or sample_count < live_sample_target
                or not math.isfinite(regression_fraction)
                or not math.isfinite(irreversible_side_effects)
                or not math.isfinite(regression_threshold)
                or regression_fraction > regression_threshold
                or irreversible_side_effects != 0
            ):
                return None
            competing = (
                await conn.execute(
                    t.rollout_records.select()
                    .where(
                        sa.and_(
                            t.rollout_records.c.worker_id == worker_id,
                            t.rollout_records.c.id != rollout_id,
                            t.rollout_records.c.status.in_(active_states),
                        )
                    )
                    .with_for_update()
                )
            ).mappings().first()
            if competing is not None:
                return None
            candidate = (
                await conn.execute(
                    t.skill_bundle_candidates.select()
                    .where(t.skill_bundle_candidates.c.id == candidate_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                candidate is None
                or candidate.get("id") != candidate_id
                or candidate.get("worker_id") != worker_id
                or candidate.get("intake_status") != "APPROVED"
                or candidate.get("approval_record_id") is None
                or candidate.get("certification_run_id") is None
            ):
                return None
            candidate_evidence = candidate.get("evidence_json")
            if not isinstance(candidate_evidence, Mapping):
                return None
            candidate_record = candidate_evidence.get("candidate_record")
            if (
                not isinstance(candidate_record, Mapping)
                or _governance_json_snapshot(dict(candidate_record))
                != expected_candidate
                or str(candidate_record.get("candidate_id") or "")
                != str(candidate_id)
                or str(candidate_record.get("worker_id") or "")
                != str(worker_id)
                or str(candidate_record.get("steward_id") or "")
                != str(steward_id)
                or str(candidate_record.get("intake_status") or "")
                != "APPROVED"
                or str(candidate_record.get("certification_id") or "")
                != str(candidate.get("certification_run_id"))
                or str(candidate_record.get("approval_record_id") or "")
                != str(candidate.get("approval_record_id"))
                or not _rollout_provenance_identity_matches(
                    provenance,
                    candidate_record,
                    worker,
                )
            ):
                return None
            bundle_record = candidate_record.get("bundle")
            adapter_record = candidate_record.get("adapter")
            if (
                not isinstance(bundle_record, Mapping)
                or not isinstance(adapter_record, Mapping)
                or str(bundle_record.get("bundle_id") or "")
                != str(candidate.get("skill_bundle_id") or "")
                or str(adapter_record.get("adapter_id") or "")
                != str(candidate.get("adapter_id") or "")
                or str(bundle_record.get("steward_id") or "")
                != str(steward_id)
                or str(adapter_record.get("steward_id") or "")
                != str(steward_id)
            ):
                return None
            certification = (
                await conn.execute(
                    t.certification_runs.select()
                    .where(
                        t.certification_runs.c.id
                        == candidate.get("certification_run_id")
                    )
                    .with_for_update()
                )
            ).mappings().first()
            approval = (
                await conn.execute(
                    t.approval_records.select()
                    .where(
                        t.approval_records.c.id == candidate.get("approval_record_id")
                    )
                    .with_for_update()
                )
            ).mappings().first()
            if (
                certification is None
                or certification.get("worker_id") != worker_id
                or certification.get("candidate_id") != candidate_id
                or certification.get("steward_id") != steward_id
                or str(certification.get("status") or "").lower() != "passed"
                or approval is None
                or approval.get("scope_type") != "worker_skill_bundle_candidate"
                or approval.get("scope_id") != candidate_id
                or approval.get("decision") != "APPROVED"
            ):
                return None
            bundle_id = candidate.get("skill_bundle_id")
            adapter_id = candidate.get("adapter_id")
            bundle = (
                await conn.execute(
                    t.skill_bundles.select()
                    .where(t.skill_bundles.c.id == bundle_id)
                    .with_for_update()
                )
            ).mappings().first()
            adapter = (
                await conn.execute(
                    t.runtime_adapters.select()
                    .where(t.runtime_adapters.c.id == adapter_id)
                    .with_for_update()
                )
            ).mappings().first()
            bundle_snapshot_hash = _skill_bundle_snapshot_content_hash(bundle_record)
            if (
                bundle is None
                or bundle.get("id") != bundle_id
                or bundle.get("worker_id") != worker_id
                or bundle.get("steward_id") != steward_id
                or bundle_snapshot_hash is None
                or str(bundle.get("content_hash") or "") != bundle_snapshot_hash
                or adapter is None
                or adapter.get("id") != adapter_id
                or adapter.get("worker_id") != worker_id
                or str(adapter.get("content_hash") or "")
                != str(adapter_record.get("content_hash") or "")
                or str(adapter.get("conformance_status") or "").lower()
                != "passed"
            ):
                return None
            shell_version_raw = candidate_evidence.get("worker_shell_version_id")
            try:
                shell_version_id = UUID(str(shell_version_raw))
            except (TypeError, ValueError):
                return None
            shell = (
                await conn.execute(
                    t.worker_shell_versions.select()
                    .where(t.worker_shell_versions.c.id == shell_version_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                shell is None
                or shell["worker_id"] != worker_id
                or shell["status"] != "active"
            ):
                return None
            steward = (
                await conn.execute(
                    t.steward_agents.select()
                    .where(t.steward_agents.c.id == steward_id)
                    .with_for_update()
                )
            ).mappings().first()
            if steward is None or steward.get("worker_id") != worker_id:
                return None
            adapter_update = await conn.execute(
                t.runtime_adapters.update()
                .where(
                    sa.and_(
                        t.runtime_adapters.c.id == adapter_id,
                        t.runtime_adapters.c.worker_id == worker_id,
                    )
                )
                .values(status="active", conformance_status="passed")
            )
            bundle_update = await conn.execute(
                t.skill_bundles.update()
                .where(
                    sa.and_(
                        t.skill_bundles.c.id == bundle_id,
                        t.skill_bundles.c.worker_id == worker_id,
                        t.skill_bundles.c.steward_id == steward_id,
                    )
                )
                .values(status="APPROVED")
            )
            if adapter_update.rowcount != 1 or bundle_update.rowcount != 1:
                raise RuntimeError("rollout version binding changed before activation")
            steward_update = await conn.execute(
                t.steward_agents.update()
                .where(
                    sa.and_(
                        t.steward_agents.c.id == steward_id,
                        t.steward_agents.c.worker_id == worker_id,
                    )
                )
                .values(
                    status="READY",
                    active_skill_bundle_id=bundle_id,
                    active_adapter_id=adapter_id,
                    updated_at=datetime.now(tz=UTC),
                )
            )
            if steward_update.rowcount != 1:
                raise RuntimeError("rollout steward binding changed before activation")
            if steward["status"] != "READY":
                await conn.execute(
                    t.steward_transitions.insert().values(
                        id=uuid4(),
                        steward_id=steward_id,
                        from_status=steward["status"],
                        to_status="READY",
                        actor=actor,
                        reason="candidate activated through atomic rollout promotion",
                        evidence={"rollout_id": str(rollout_id), "candidate_id": str(candidate_id)},
                    )
                )
            worker_update = await conn.execute(
                t.worker_registry.update()
                .where(t.worker_registry.c.id == worker_id)
                .values(
                    active_shell_version_id=shell_version_id,
                    active_adapter_id=adapter_id,
                    active_skill_bundle_id=bundle_id,
                    updated_at=datetime.now(tz=UTC),
                )
            )
            if worker_update.rowcount != 1:
                raise RuntimeError("rollout worker binding changed before activation")
            result = await conn.execute(
                t.rollout_records.update()
                .where(
                    sa.and_(
                        t.rollout_records.c.id == rollout_id,
                        t.rollout_records.c.worker_id == worker_id,
                        t.rollout_records.c.steward_id == steward_id,
                        t.rollout_records.c.candidate_id == candidate_id,
                        t.rollout_records.c.status == "PROMOTING",
                    )
                )
                .values(
                    status="ACTIVE",
                    sample_count=sample_count,
                    comparison_metrics=dict(accepted_metrics),
                    completed_at=completed_at,
                )
            )
            if result.rowcount != 1:
                raise RuntimeError("rollout promotion compare-and-set failed")
            await conn.execute(
                t.rollout_transitions.insert().values(
                    id=uuid4(),
                    rollout_id=rollout_id,
                    from_status="PROMOTING",
                    to_status="ACTIVE",
                    actor=actor,
                    reason="atomic controlled promotion",
                    evidence={"candidate_id": str(candidate_id), "approval_record_id": str(candidate["approval_record_id"])},
                )
            )
        return await self.get_rollout_record(rollout_id)

    async def rollback_rollout_atomically(
        self,
        *,
        rollout_id: UUID,
        worker_id: UUID,
        steward_id: UUID,
        candidate_id: UUID,
        expected_status: str,
        reason: str,
        triggered_by: str,
        completed_at: datetime,
    ) -> dict[str, Any] | None:
        """Roll back one worker rollout and restore its last active version atomically.

        The worker row serializes rollback against promotion and other pointer
        changes. All durable bindings are checked before writes, and the rollout
        compare-and-set, pointer changes, transition history, and rollback
        evidence share one transaction.
        """
        rollbackable_states = {
            "PENDING",
            "SHADOW",
            "CANARY",
            "PROMOTING",
            "ACTIVE",
            "ROLLING_BACK",
        }
        if (
            expected_status not in rollbackable_states
            or not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(triggered_by, str)
            or not triggered_by.strip()
        ):
            return None

        async with self.engine.begin() as conn:
            worker = (
                await conn.execute(
                    t.worker_registry.select()
                    .where(t.worker_registry.c.id == worker_id)
                    .with_for_update()
                )
            ).mappings().first()
            if worker is None:
                return None
            steward = (
                await conn.execute(
                    t.steward_agents.select()
                    .where(t.steward_agents.c.id == steward_id)
                    .with_for_update()
                )
            ).mappings().first()
            if steward is None or steward.get("worker_id") != worker_id:
                return None
            rollout = (
                await conn.execute(
                    t.rollout_records.select()
                    .where(t.rollout_records.c.id == rollout_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                rollout is None
                or rollout.get("worker_id") != worker_id
                or rollout.get("steward_id") != steward_id
                or rollout.get("candidate_id") != candidate_id
                or rollout.get("status") != expected_status
            ):
                return None

            current_candidate = (
                await conn.execute(
                    t.skill_bundle_candidates.select()
                    .where(t.skill_bundle_candidates.c.id == candidate_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                current_candidate is None
                or current_candidate.get("worker_id") != worker_id
                or current_candidate.get("intake_status") != "APPROVED"
            ):
                return None
            current_evidence = current_candidate.get("evidence_json")
            if not isinstance(current_evidence, Mapping):
                return None
            current_candidate_record = current_evidence.get("candidate_record")
            if not isinstance(current_candidate_record, Mapping):
                return None
            current_bundle_id = current_candidate.get("skill_bundle_id")
            current_adapter_id = current_candidate.get("adapter_id")
            current_bundle_record = current_candidate_record.get("bundle")
            current_adapter_record = current_candidate_record.get("adapter")
            if (
                not isinstance(current_bundle_record, Mapping)
                or not isinstance(current_adapter_record, Mapping)
                or str(current_candidate_record.get("candidate_id") or "")
                != str(candidate_id)
                or str(current_candidate_record.get("worker_id") or "")
                != str(worker_id)
                or str(current_candidate_record.get("steward_id") or "")
                != str(steward_id)
                or str(current_candidate_record.get("intake_status") or "")
                != "APPROVED"
                or str(current_candidate_record.get("bundle", {}).get("bundle_id") or "")
                != str(current_bundle_id)
                or str(current_candidate_record.get("adapter", {}).get("adapter_id") or "")
                != str(current_adapter_id)
                or str(current_bundle_record.get("steward_id") or "")
                != str(steward_id)
                or str(current_adapter_record.get("steward_id") or "")
                != str(steward_id)
                or current_adapter_id is None
            ):
                return None
            if expected_status == "ACTIVE":
                try:
                    current_shell_id = UUID(
                        str(current_evidence.get("worker_shell_version_id") or "")
                    )
                except (TypeError, ValueError):
                    return None
                if (
                    worker.get("active_skill_bundle_id") != current_bundle_id
                    or worker.get("active_adapter_id") != current_adapter_id
                    or worker.get("active_shell_version_id") != current_shell_id
                ):
                    return None
            current_certification_id = current_candidate.get("certification_run_id")
            current_approval_id = current_candidate.get("approval_record_id")
            if (
                current_certification_id is None
                or current_approval_id is None
                or str(current_candidate_record.get("certification_id") or "")
                != str(current_certification_id)
                or str(current_candidate_record.get("approval_record_id") or "")
                != str(current_approval_id)
            ):
                return None
            current_certification = (
                await conn.execute(
                    t.certification_runs.select()
                    .where(t.certification_runs.c.id == current_certification_id)
                    .with_for_update()
                )
            ).mappings().first()
            current_approval = (
                await conn.execute(
                    t.approval_records.select()
                    .where(t.approval_records.c.id == current_approval_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                current_certification is None
                or current_certification.get("worker_id") != worker_id
                or current_certification.get("candidate_id") != candidate_id
                or current_certification.get("steward_id") != steward_id
                or str(current_certification.get("status") or "").lower() != "passed"
                or current_approval is None
                or current_approval.get("scope_type") != "worker_skill_bundle_candidate"
                or current_approval.get("scope_id") != candidate_id
                or current_approval.get("decision") != "APPROVED"
            ):
                return None
            current_bundle = (
                await conn.execute(
                    t.skill_bundles.select()
                    .where(t.skill_bundles.c.id == current_bundle_id)
                    .with_for_update()
                )
            ).mappings().first()
            current_adapter = (
                await conn.execute(
                    t.runtime_adapters.select()
                    .where(t.runtime_adapters.c.id == current_adapter_id)
                    .with_for_update()
                )
            ).mappings().first()
            current_bundle_snapshot_hash = _skill_bundle_snapshot_content_hash(
                current_bundle_record
            )
            if (
                current_bundle is None
                or current_bundle.get("id") != current_bundle_id
                or current_bundle.get("worker_id") != worker_id
                or current_bundle.get("steward_id") != steward_id
                or current_bundle_snapshot_hash is None
                or str(current_bundle.get("content_hash") or "")
                != current_bundle_snapshot_hash
                or current_adapter is None
                or current_adapter.get("id") != current_adapter_id
                or current_adapter.get("worker_id") != worker_id
                or str(current_adapter.get("content_hash") or "")
                != str(current_adapter_record.get("content_hash") or "")
                or str(current_adapter.get("conformance_status") or "").lower()
                != "passed"
            ):
                return None

            prior_rollouts = (
                await conn.execute(
                    t.rollout_records.select()
                    .where(
                        sa.and_(
                            t.rollout_records.c.worker_id == worker_id,
                            t.rollout_records.c.id != rollout_id,
                            t.rollout_records.c.status == "ACTIVE",
                        )
                    )
                    .order_by(t.rollout_records.c.started_at.asc())
                    .with_for_update()
                )
            ).mappings().all()
            target_candidate_id: UUID | None = None
            target_bundle_id: UUID | None = None
            target_adapter_id: UUID | None = None
            target_shell_id: UUID | None = None
            if prior_rollouts:
                prior_rollout = prior_rollouts[-1]
                if prior_rollout.get("steward_id") != steward_id:
                    return None
                target_candidate_id = prior_rollout.get("candidate_id")
                if target_candidate_id is None or target_candidate_id == candidate_id:
                    return None
                target_candidate_row = (
                    await conn.execute(
                        t.skill_bundle_candidates.select()
                        .where(t.skill_bundle_candidates.c.id == target_candidate_id)
                        .with_for_update()
                    )
                ).mappings().first()
                if (
                    target_candidate_row is None
                    or target_candidate_row.get("worker_id") != worker_id
                    or target_candidate_row.get("intake_status") != "APPROVED"
                ):
                    return None
                target_candidate_record = (target_candidate_row.get("evidence_json") or {}).get(
                    "candidate_record"
                )
                if not isinstance(target_candidate_record, Mapping):
                    return None
                target_bundle_id = target_candidate_row.get("skill_bundle_id")
                target_adapter_id = target_candidate_row.get("adapter_id")
                target_bundle_record = target_candidate_record.get("bundle")
                target_adapter_record = target_candidate_record.get("adapter")
                try:
                    target_shell_id = UUID(
                        str(
                            (target_candidate_row.get("evidence_json") or {}).get(
                                "worker_shell_version_id"
                            )
                            or ""
                        )
                    )
                except (TypeError, ValueError):
                    return None
                if (
                    not isinstance(target_bundle_record, Mapping)
                    or not isinstance(target_adapter_record, Mapping)
                    or str(target_candidate_record.get("candidate_id") or "")
                    != str(target_candidate_id)
                    or str(target_candidate_record.get("worker_id") or "")
                    != str(worker_id)
                    or str(target_candidate_record.get("steward_id") or "")
                    != str(steward_id)
                    or str(target_candidate_record.get("intake_status") or "")
                    != "APPROVED"
                    or str(target_bundle_record.get("bundle_id") or "")
                    != str(target_bundle_id)
                    or str(target_adapter_record.get("adapter_id") or "")
                    != str(target_adapter_id)
                    or str(target_bundle_record.get("steward_id") or "")
                    != str(steward_id)
                    or str(target_adapter_record.get("steward_id") or "")
                    != str(steward_id)
                    or target_adapter_id is None
                ):
                    return None
                target_bundle = (
                    await conn.execute(
                        t.skill_bundles.select()
                        .where(t.skill_bundles.c.id == target_bundle_id)
                        .with_for_update()
                    )
                ).mappings().first()
                target_adapter = (
                    await conn.execute(
                        t.runtime_adapters.select()
                        .where(t.runtime_adapters.c.id == target_adapter_id)
                        .with_for_update()
                    )
                ).mappings().first()
                shell = (
                    await conn.execute(
                        t.worker_shell_versions.select()
                        .where(t.worker_shell_versions.c.id == target_shell_id)
                        .with_for_update()
                    )
                ).mappings().first()
                target_bundle_snapshot_hash = _skill_bundle_snapshot_content_hash(
                    target_bundle_record
                )
                if (
                    target_bundle is None
                    or target_bundle.get("id") != target_bundle_id
                    or target_bundle.get("worker_id") != worker_id
                    or target_bundle.get("steward_id") != steward_id
                    or target_bundle_snapshot_hash is None
                    or str(target_bundle.get("content_hash") or "")
                    != target_bundle_snapshot_hash
                    or target_adapter is None
                    or target_adapter.get("id") != target_adapter_id
                    or target_adapter.get("worker_id") != worker_id
                    or str(target_adapter.get("content_hash") or "")
                    != str(target_adapter_record.get("content_hash") or "")
                    or str(target_adapter.get("conformance_status") or "").lower()
                    != "passed"
                    or shell is None
                    or shell.get("worker_id") != worker_id
                    or shell.get("status") != "active"
                ):
                    return None

                certification_id = target_candidate_row.get("certification_run_id")
                approval_id = target_candidate_row.get("approval_record_id")
                if certification_id is None or approval_id is None:
                    return None
                certification = (
                    await conn.execute(
                        t.certification_runs.select()
                        .where(t.certification_runs.c.id == certification_id)
                        .with_for_update()
                    )
                ).mappings().first()
                approval = (
                    await conn.execute(
                        t.approval_records.select()
                        .where(t.approval_records.c.id == approval_id)
                        .with_for_update()
                    )
                ).mappings().first()
                if (
                    certification is None
                    or certification.get("worker_id") != worker_id
                    or certification.get("candidate_id") != target_candidate_id
                    or certification.get("steward_id") != steward_id
                    or str(certification.get("status") or "").lower() != "passed"
                    or approval is None
                    or approval.get("scope_type") != "worker_skill_bundle_candidate"
                    or approval.get("scope_id") != target_candidate_id
                    or approval.get("decision") != "APPROVED"
                    or str(target_candidate_record.get("certification_id") or "")
                    != str(certification_id)
                    or str(target_candidate_record.get("approval_record_id") or "")
                    != str(approval_id)
                ):
                    return None

            # All reads and binding checks complete before the first write.
            current_adapter_update = await conn.execute(
                t.runtime_adapters.update()
                .where(
                    sa.and_(
                        t.runtime_adapters.c.id == current_adapter_id,
                        t.runtime_adapters.c.worker_id == worker_id,
                    )
                )
                .values(status="superseded")
            )
            current_bundle_update = await conn.execute(
                t.skill_bundles.update()
                .where(
                    sa.and_(
                        t.skill_bundles.c.id == current_bundle_id,
                        t.skill_bundles.c.worker_id == worker_id,
                        t.skill_bundles.c.steward_id == steward_id,
                    )
                )
                .values(status="SUPERSEDED")
            )
            if current_adapter_update.rowcount != 1 or current_bundle_update.rowcount != 1:
                raise RuntimeError("rollback current version binding changed")

            worker_values: dict[str, Any] = {
                "active_shell_version_id": target_shell_id,
                "active_adapter_id": target_adapter_id,
                "active_skill_bundle_id": target_bundle_id,
                "updated_at": datetime.now(tz=UTC),
            }
            steward_values: dict[str, Any] = {
                "active_skill_bundle_id": target_bundle_id,
                "active_adapter_id": target_adapter_id,
                "updated_at": datetime.now(tz=UTC),
            }
            if target_candidate_id is None:
                worker_values["status"] = "INACTIVE"
                steward_values["status"] = "DEGRADED"
            else:
                target_adapter_update = await conn.execute(
                    t.runtime_adapters.update()
                    .where(
                        sa.and_(
                            t.runtime_adapters.c.id == target_adapter_id,
                            t.runtime_adapters.c.worker_id == worker_id,
                        )
                    )
                    .values(status="active", conformance_status="passed")
                )
                target_bundle_update = await conn.execute(
                    t.skill_bundles.update()
                    .where(
                        sa.and_(
                            t.skill_bundles.c.id == target_bundle_id,
                            t.skill_bundles.c.worker_id == worker_id,
                            t.skill_bundles.c.steward_id == steward_id,
                        )
                    )
                    .values(status="APPROVED")
                )
                if target_adapter_update.rowcount != 1 or target_bundle_update.rowcount != 1:
                    raise RuntimeError("rollback target version binding changed")

            worker_update = await conn.execute(
                t.worker_registry.update()
                .where(t.worker_registry.c.id == worker_id)
                .values(**worker_values)
            )
            steward_update = await conn.execute(
                t.steward_agents.update()
                .where(
                    sa.and_(
                        t.steward_agents.c.id == steward_id,
                        t.steward_agents.c.worker_id == worker_id,
                    )
                )
                .values(**steward_values)
            )
            if worker_update.rowcount != 1 or steward_update.rowcount != 1:
                raise RuntimeError("rollback pointer binding changed")
            rollout_update = await conn.execute(
                t.rollout_records.update()
                .where(
                    sa.and_(
                        t.rollout_records.c.id == rollout_id,
                        t.rollout_records.c.worker_id == worker_id,
                        t.rollout_records.c.steward_id == steward_id,
                        t.rollout_records.c.candidate_id == candidate_id,
                        t.rollout_records.c.status == expected_status,
                    )
                )
                .values(
                    status="ROLLED_BACK",
                    rollback_reason=reason,
                    completed_at=completed_at,
                )
            )
            if rollout_update.rowcount != 1:
                raise RuntimeError("rollback rollout compare-and-set failed")
            if expected_status != "ROLLING_BACK":
                await conn.execute(
                    t.rollout_transitions.insert().values(
                        id=uuid4(),
                        rollout_id=rollout_id,
                        from_status=expected_status,
                        to_status="ROLLING_BACK",
                        actor=triggered_by,
                        reason=reason,
                        evidence={"candidate_id": str(candidate_id)},
                    )
                )
            await conn.execute(
                t.rollout_transitions.insert().values(
                    id=uuid4(),
                    rollout_id=rollout_id,
                    from_status="ROLLING_BACK",
                    to_status="ROLLED_BACK",
                    actor=triggered_by,
                    reason=reason,
                    evidence={
                        "candidate_id": str(candidate_id),
                        "target_candidate_id": (
                            str(target_candidate_id)
                            if target_candidate_id is not None
                            else None
                        ),
                    },
                )
            )
            await conn.execute(
                t.rollback_records.insert().values(
                    id=uuid4(),
                    rollout_id=rollout_id,
                    worker_id=worker_id,
                    from_candidate_id=candidate_id,
                    target_candidate_id=target_candidate_id,
                    reason=reason,
                    triggered_by=triggered_by,
                    evidence={
                        "steward_id": str(steward_id),
                        "from_status": expected_status,
                    },
                )
            )

        return {
            "rollout": {
                **dict(rollout),
                "status": "ROLLED_BACK",
                "rollback_reason": reason,
                "completed_at": completed_at,
            },
            "target_candidate_id": target_candidate_id,
            "target_bundle_id": target_bundle_id,
            "target_adapter_id": target_adapter_id,
            "target_shell_id": target_shell_id,
        }

    async def create_rollback_record(self, *, rollout_id: UUID, worker_id: UUID, reason: str, triggered_by: str, evidence: dict[str, Any] | None = None, from_candidate_id: UUID | None = None, target_candidate_id: UUID | None = None) -> dict[str, Any]:
        values = {"id": uuid4(), "rollout_id": rollout_id, "worker_id": worker_id, "from_candidate_id": from_candidate_id, "target_candidate_id": target_candidate_id, "reason": reason, "triggered_by": triggered_by, "evidence": evidence or {}}
        async with self.engine.begin() as conn:
            await conn.execute(t.rollback_records.insert().values(**values))
        return values

    async def create_model_resolution_snapshot(self, *, snapshot: dict[str, Any], project_id: UUID | None = None) -> dict[str, Any]:
        values = {"id": snapshot.get("snapshot_id") or uuid4(), "project_id": project_id, "requested_profile_id": snapshot.get("requested_profile_id"), "resolved_profile_id": snapshot.get("resolved_profile_id"), "resolved_profile_version": snapshot.get("resolved_profile_version"), "provider_id": snapshot.get("provider_id"), "exact_model_id": snapshot.get("exact_model_id"), "effective_constraints": snapshot.get("effective_constraints") or {}, "effective_configuration": snapshot.get("effective_configuration") or {}, "capability_checks": snapshot.get("capability_checks") or {}, "rejected_candidates": snapshot.get("rejected_candidates") or [], "fallback_chain": snapshot.get("fallback_chain") or [], "cost_estimate_usd": snapshot.get("cost_estimate_usd") or 0, "override_approval_id": snapshot.get("override_approval_id"), "selection_reason": snapshot.get("selection_reason"), "policy_failure_code": snapshot.get("policy_failure_code")}
        async with self.engine.begin() as conn:
            await conn.execute(t.model_resolution_snapshots.insert().values(**values))
        return values

    async def get_model_resolution_snapshot(
        self,
        snapshot_id: UUID,
        *,
        project_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        """Read one model decision, optionally enforcing project scope.

        A project-bound snapshot must never be exposed to an unscoped or
        different-project governance invocation.  A snapshot with no project
        is a control-plane-wide decision and may be used by a project-scoped
        invocation.
        """
        row = await self._get_table_row(
            t.model_resolution_snapshots,
            t.model_resolution_snapshots.c.id,
            snapshot_id,
        )
        if row is None:
            return None
        stored_project_id = row.get("project_id")
        if stored_project_id is not None and stored_project_id != project_id:
            return None
        return row

    async def create_worker_run(self, *, run_id: UUID, worker_id: UUID, idempotency_key: str, task_type: str, request: dict[str, Any], project_id: UUID | None = None, flow_id: UUID | None = None, flow_instance_id: UUID | None = None, flow_node_execution_id: int | None = None, worker_shell_version_id: UUID | None = None, adapter_id: UUID | None = None, skill_bundle_id: UUID | None = None, steward_id: UUID | None = None, model_resolution_snapshot_id: UUID | None = None, retry_of_run_id: UUID | None = None, retry_context_json: dict[str, Any] | None = None, state: str = "CREATED", queue_priority: int = 0, next_attempt_at: datetime | None = None) -> dict[str, Any]:
        async with self.engine.begin() as conn:
            existing = (await conn.execute(t.worker_runs.select().where(sa.and_(t.worker_runs.c.worker_id == worker_id, t.worker_runs.c.idempotency_key == idempotency_key)).with_for_update())).mappings().first()
            if existing:
                return dict(existing)
            worker = (
                await conn.execute(
                    t.worker_registry.select()
                    .where(t.worker_registry.c.id == worker_id)
                    .with_for_update()
                )
            ).mappings().first()
            if worker is None:
                raise ValueError("worker registry record is required before creating a durable run")
            if retry_of_run_id is not None:
                source = (
                    await conn.execute(
                        t.worker_runs.select()
                        .where(t.worker_runs.c.id == retry_of_run_id)
                        .with_for_update()
                    )
                ).mappings().first()
                if (
                    source is None
                    or source["worker_id"] != worker_id
                    or source["state"] not in {"FAILED", "TIMED_OUT"}
                ):
                    raise ValueError("retry source must be a failed or timed-out run for the same worker")
                existing_retry = (
                    await conn.execute(
                        t.worker_runs.select()
                        .where(t.worker_runs.c.retry_of_run_id == retry_of_run_id)
                        .with_for_update()
                    )
                ).mappings().first()
                if existing_retry is not None:
                    if existing_retry["idempotency_key"] == idempotency_key:
                        return dict(existing_retry)
                    raise ValueError("retry source already has a canonical retry run")
            effective_skill_bundle_id = skill_bundle_id or worker.get("active_skill_bundle_id")
            if effective_skill_bundle_id is not None:
                bundle = (
                    await conn.execute(
                        t.skill_bundles.select()
                        .where(
                            sa.and_(
                                t.skill_bundles.c.id == effective_skill_bundle_id,
                                t.skill_bundles.c.worker_id == worker_id,
                            )
                        )
                    )
                ).mappings().first()
                if bundle is None:
                    raise ValueError("skill bundle pin must belong to the worker registry record")
                if steward_id is not None and bundle["steward_id"] != steward_id:
                    raise ValueError("skill bundle pin must belong to the selected steward")
            # Recheck after acquiring the per-worker serialization lock: an
            # identical idempotent request may have committed while this
            # transaction waited for the worker row.
            existing = (
                await conn.execute(
                    t.worker_runs.select()
                    .where(
                        sa.and_(
                            t.worker_runs.c.worker_id == worker_id,
                            t.worker_runs.c.idempotency_key == idempotency_key,
                        )
                    )
                    .with_for_update()
                )
            ).mappings().first()
            if existing:
                return dict(existing)
            values = {"id": run_id, "worker_id": worker_id, "idempotency_key": idempotency_key, "task_type": task_type, "request_json": request, "project_id": project_id, "flow_id": flow_id, "flow_instance_id": flow_instance_id, "flow_node_execution_id": flow_node_execution_id, "worker_shell_version_id": worker_shell_version_id, "adapter_id": adapter_id, "skill_bundle_id": effective_skill_bundle_id, "steward_id": steward_id, "model_resolution_snapshot_id": model_resolution_snapshot_id, "retry_of_run_id": retry_of_run_id, "retry_context_json": retry_context_json or {}, "state": state, "queue_priority": queue_priority, "next_attempt_at": next_attempt_at}
            await conn.execute(t.worker_runs.insert().values(**values))
        return await self.get_worker_run(run_id)  # type: ignore[return-value]

    async def get_worker_run_by_idempotency_key(
        self, *, worker_id: UUID, idempotency_key: str
    ) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.worker_runs.select().where(
                        sa.and_(
                            t.worker_runs.c.worker_id == worker_id,
                            t.worker_runs.c.idempotency_key == idempotency_key,
                        )
                    )
                )
            ).mappings().first()
        return dict(row) if row is not None else None

    async def get_worker_run_retry(self, source_run_id: UUID) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.worker_runs.select()
                    .where(t.worker_runs.c.retry_of_run_id == source_run_id)
                    .order_by(t.worker_runs.c.created_at.desc())
                    .limit(1)
                )
            ).mappings().first()
        return dict(row) if row is not None else None

    async def _worker_run_project_scope_is_current(
        self,
        conn: Any,
        run: Mapping[str, Any],
    ) -> bool:
        """Check project and assignment authority while their rows are locked."""

        request = run.get("request_json")
        if not isinstance(request, Mapping) and run.get("project_id") is not None:
            return False
        if not isinstance(request, Mapping):
            request = {}

        project = None
        assignments: list[Mapping[str, Any]] = []
        project_id = run.get("project_id")
        if project_id is not None:
            project = (
                await conn.execute(
                    t.projects.select()
                    .where(t.projects.c.id == project_id)
                    .with_for_update()
                )
            ).mappings().first()
            preliminary_blocker = worker_run_project_scope_blocker(
                run=run,
                request=request,
                project=project,
                assignments=(),
            )
            if preliminary_blocker not in {None, "company_assignment_required"}:
                return False
            if project is not None and project.get("company_id") is not None:
                assignment_rows = (
                    await conn.execute(
                        t.company_worker_assignments.select()
                        .where(
                            sa.and_(
                                t.company_worker_assignments.c.company_id
                                == project["company_id"],
                                t.company_worker_assignments.c.worker_id
                                == run.get("worker_id"),
                            )
                        )
                        .limit(2)
                        .with_for_update()
                    )
                ).mappings().all()
                assignments = assignment_rows

        return (
            worker_run_project_scope_blocker(
                run=run,
                request=request,
                project=project,
                assignments=assignments,
            )
            is None
        )
    async def claim_worker_run(
        self,
        *,
        owner: str,
        lease_seconds: int = 300,
        run_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim one queued run, or a specified queued run."""

        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            clauses = [
                t.worker_runs.c.state == "QUEUED",
                # A durable cancellation request is an execution fence.  It
                # may race with lease recovery after a run is requeued, so a
                # queued row carrying the marker must never be claimed again.
                t.worker_runs.c.cancel_requested_at.is_(None),
                sa.or_(t.worker_runs.c.next_attempt_at.is_(None), t.worker_runs.c.next_attempt_at <= now),
                sa.or_(t.worker_runs.c.lease_expires_at.is_(None), t.worker_runs.c.lease_expires_at <= now),
            ]
            if run_id is not None:
                clauses.append(t.worker_runs.c.id == run_id)
            query = t.worker_runs.select().where(sa.and_(*clauses)).order_by(
                t.worker_runs.c.queue_priority.desc(),
                t.worker_runs.c.created_at.asc(),
            ).limit(1).with_for_update(skip_locked=True)
            row = (await conn.execute(query)).mappings().first()
            if row is None:
                return None
            if not await self._worker_run_project_scope_is_current(conn, row):
                return None
            now = datetime.now(tz=UTC)
            lease_until = now + timedelta(seconds=max(1, lease_seconds))
            await conn.execute(
                t.worker_runs.update().where(t.worker_runs.c.id == row["id"]).values(
                    state="CLAIMED",
                    claim_owner=owner,
                    claimed_at=now,
                    heartbeat_at=now,
                    lease_expires_at=lease_until,
                    attempt_count=int(row.get("attempt_count") or 0) + 1,
                    recovery_reason=None,
                )
            )
            await conn.execute(
                t.worker_run_transitions.insert().values(
                    id=uuid4(),
                    run_id=row["id"],
                    from_state="QUEUED",
                    to_state="CLAIMED",
                    actor=owner,
                    reason="worker run claimed",
                    metadata={"lease_expires_at": lease_until.isoformat()},
                )
            )
            updated = (await conn.execute(t.worker_runs.select().where(t.worker_runs.c.id == row["id"]))).mappings().first()
        return dict(updated) if updated else None

    async def heartbeat_worker_run(
        self,
        run_id: UUID,
        *,
        owner: str,
        attempt_count: int,
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            current = (
                await conn.execute(
                    t.worker_runs.select()
                    .where(t.worker_runs.c.id == run_id)
                    .with_for_update()
                )
            ).mappings().first()
            if (
                current is None
                or current.get("claim_owner") != owner
                or int(current.get("attempt_count") or 0) != attempt_count
                or current.get("lease_expires_at") is None
                or str(current.get("state")) in TERMINAL_WORKER_RUN_STATES
            ):
                return None
            now = datetime.now(tz=UTC)
            if current["lease_expires_at"] <= now:
                return None
            result = await conn.execute(
                t.worker_runs.update()
                .where(
                    sa.and_(
                        t.worker_runs.c.id == run_id,
                        t.worker_runs.c.claim_owner == owner,
                        t.worker_runs.c.attempt_count == attempt_count,
                        t.worker_runs.c.lease_expires_at > now,
                        t.worker_runs.c.state.notin_(TERMINAL_WORKER_RUN_STATES),
                    )
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=max(1, lease_seconds)),
                )
            )
        return await self.get_worker_run(run_id) if result.rowcount else None

    async def request_worker_run_cancel(self, run_id: UUID) -> dict[str, Any] | None:
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            await conn.execute(t.worker_runs.update().where(t.worker_runs.c.id == run_id).values(cancel_requested_at=now))
        return await self.get_worker_run(run_id)

    async def recover_expired_worker_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = datetime.now(tz=UTC)
        recovered: list[dict[str, Any]] = []
        async with self.engine.begin() as conn:
            rows = (
                await conn.execute(
                    t.worker_runs.select().where(
                        sa.or_(
                            sa.and_(
                                t.worker_runs.c.state.in_(("CLAIMED", "VALIDATING", "READY", "DISPATCHING", "RUNNING", "PAUSING", "RESUMING")),
                                t.worker_runs.c.lease_expires_at.is_not(None),
                                t.worker_runs.c.lease_expires_at <= now,
                            ),
                            sa.and_(
                                t.worker_runs.c.state == "QUEUED",
                                t.worker_runs.c.cancel_requested_at.is_not(None),
                            ),
                        )
                    ).order_by(t.worker_runs.c.lease_expires_at.asc()).limit(limit).with_for_update(skip_locked=True)
                )
            ).mappings().all()
            for row in rows:
                if row.get("cancel_requested_at") is not None:
                    cancellation_reason = (
                        "worker run cancellation was requested before lease expiry; "
                        "expired execution was fenced and not requeued"
                    )
                    await conn.execute(
                        t.worker_runs.update()
                        .where(t.worker_runs.c.id == row["id"])
                        .values(
                            state="CANCELLED",
                            claim_owner=None,
                            claimed_at=None,
                            heartbeat_at=None,
                            lease_expires_at=None,
                            next_attempt_at=None,
                            completed_at=now,
                            recovery_reason=cancellation_reason,
                            error_json={
                                "code": "CANCELLED",
                                "message": cancellation_reason,
                            },
                        )
                    )
                    await conn.execute(
                        t.worker_run_transitions.insert().values(
                            id=uuid4(),
                            run_id=row["id"],
                            from_state=str(row["state"]),
                            to_state="CANCELLED",
                            actor="worker-run-recovery",
                            reason="durable cancellation request honored after lease expiry",
                            metadata={"attempt_count": int(row.get("attempt_count") or 0)},
                        )
                    )
                    recovered.append(
                        {
                            **dict(row),
                            "state": "CANCELLED",
                            "claim_owner": None,
                            "claimed_at": None,
                            "heartbeat_at": None,
                            "lease_expires_at": None,
                            "next_attempt_at": None,
                            "completed_at": now,
                            "error_json": {
                                "code": "CANCELLED",
                                "message": cancellation_reason,
                            },
                            "recovery_reason": cancellation_reason,
                        }
                    )
                    continue
                await conn.execute(
                    t.worker_runs.update().where(t.worker_runs.c.id == row["id"]).values(
                        state="QUEUED",
                        claim_owner=None,
                        claimed_at=None,
                        heartbeat_at=None,
                        lease_expires_at=None,
                        next_attempt_at=now,
                        recovery_reason="worker run lease expired; requeued by recovery loop",
                    )
                )
                await conn.execute(
                    t.worker_run_transitions.insert().values(
                        id=uuid4(),
                        run_id=row["id"],
                        from_state=str(row["state"]),
                        to_state="QUEUED",
                        actor="worker-run-recovery",
                        reason="lease expired",
                        metadata={"attempt_count": int(row.get("attempt_count") or 0)},
                    )
                )
                recovered.append({**dict(row), "state": "QUEUED", "recovery_reason": "worker run lease expired; requeued by recovery loop"})
        return recovered

    async def get_worker_run(self, run_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.worker_runs, t.worker_runs.c.id, run_id)

    @staticmethod
    def _worker_run_claim_matches(
        row: Mapping[str, Any] | None,
        *,
        claim_owner: str | None,
        attempt_count: int,
        now: datetime,
        reject_cancel_requested: bool = False,
        allowed_states: tuple[str, ...] = ("RUNNING",),
    ) -> bool:
        if row is None or str(row.get("state") or "") not in allowed_states:
            return False
        # A durable cancellation request closes the execution capability
        # before the adapter has necessarily acknowledged cancellation.  The
        # row lock held by callers serializes this check with the request write.
        if reject_cancel_requested and row.get("cancel_requested_at") is not None:
            return False
        current_owner = row.get("claim_owner")
        current_attempt = int(row.get("attempt_count") or 0)
        if current_owner is None and current_attempt == 0:
            return claim_owner is None and attempt_count == 0
        lease_expires_at = row.get("lease_expires_at")
        return (
            claim_owner is not None
            and claim_owner == current_owner
            and attempt_count == current_attempt
            and lease_expires_at is not None
            and lease_expires_at > now
        )

    async def validate_worker_run_claim(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
    ) -> bool:
        """Check one execution fence under the canonical WorkerRun row lock."""

        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    t.worker_runs.select()
                    .where(t.worker_runs.c.id == run_id)
                    .with_for_update()
                )
            ).mappings().first()
            return self._worker_run_claim_matches(
                row,
                claim_owner=claim_owner,
                attempt_count=attempt_count,
                now=datetime.now(tz=UTC),
                reject_cancel_requested=True,
            )

    async def _insert_worker_result_evidence_tx(
        self,
        conn: Any,
        *,
        run_id: UUID,
        artifacts: list[dict[str, Any]],
        usage: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        """Insert worker artifacts, usage, and evidence spans in an open transaction."""

        stored_artifacts: list[dict[str, Any]] = []
        trace_id = usage.get("trace_id") or current_trace_id()
        span_id = (
            (usage.get("span_id") or current_span_id() or new_span_id())
            if trace_id
            else usage.get("span_id")
        )
        usage_values = {
            "id": uuid4(),
            "run_id": run_id,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "cost_usd": usage.get("cost_usd", 0),
            "duration_ms": usage.get("duration_ms", 0),
            "resource_json": usage.get("resource_json") or {},
            "provider_id": usage.get("provider_id"),
            "exact_model_id": usage.get("exact_model_id"),
            "trace_id": trace_id,
            "span_id": span_id,
            "created_at": now,
        }

        for artifact in artifacts:
            artifact_values = {
                "agent_id": artifact["agent_id"],
                "path": artifact["path"],
                "metadata": artifact.get("metadata"),
                "sha256": artifact.get("sha256"),
                "size_bytes": artifact.get("size_bytes"),
                "created_at": now,
            }
            artifact_id = (
                await conn.execute(
                    t.artifacts.insert()
                    .values(**artifact_values)
                    .returning(t.artifacts.c.id)
                )
            ).scalar_one()
            worker_artifact_values = {
                "id": uuid4(),
                "run_id": run_id,
                "artifact_id": artifact_id,
                "kind": artifact["kind"],
                "uri": artifact["uri"],
                "sha256": artifact["sha256"],
                "size_bytes": artifact.get("size_bytes"),
                "metadata": artifact.get("metadata") or {},
                "trace_id": artifact.get("trace_id"),
                "span_id": artifact.get("span_id"),
                "created_at": now,
            }
            await conn.execute(t.worker_artifacts.insert().values(**worker_artifact_values))
            stored_artifacts.append(
                {
                    "artifact": {**artifact_values, "id": artifact_id},
                    "worker_artifact": worker_artifact_values,
                }
            )
            if artifact.get("trace_id"):
                await self._insert_native_trace_span_tx(
                    conn,
                    trace_id=str(artifact["trace_id"]),
                    span_id=new_span_id(),
                    parent_span_id=artifact.get("span_id"),
                    source_kind="audit",
                    operation=f"artifact.{artifact['kind']}",
                    service="worker_evidence",
                    status="success",
                    started_at=now,
                    ended_at=now,
                    attributes={
                        "kind": artifact["kind"],
                        "size_bytes": artifact.get("size_bytes") or 0,
                    },
                )

        await conn.execute(t.worker_usage_records.insert().values(**usage_values))
        if trace_id:
            duration_ms = usage.get("duration_ms") or 0
            await self._insert_native_trace_span_tx(
                conn,
                trace_id=trace_id,
                span_id=new_span_id(),
                parent_span_id=span_id,
                source_kind="model",
                operation=usage.get("exact_model_id") or "worker.model",
                service="worker_evidence",
                status="success",
                started_at=now - timedelta(milliseconds=float(duration_ms or 0)),
                ended_at=now,
                duration_ms=duration_ms,
                attributes={
                    "run_id": str(run_id),
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                },
            )
        return {"artifacts": stored_artifacts, "usage": usage_values}

    async def persist_worker_result_evidence_claim_fenced(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, Any]],
        usage: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Insert worker artifact links and usage while holding the live claim.

        The row lock serializes these evidence writes against lease recovery.
        Object-store bytes are external and cannot share this SQL transaction;
        callers use attempt-scoped keys and delete them if this fence rejects.
        """

        async with self.engine.begin() as conn:
            run = (
                await conn.execute(
                    t.worker_runs.select()
                    .where(t.worker_runs.c.id == run_id)
                    .with_for_update()
                )
            ).mappings().first()
            # Sample lease validity only after this transaction has acquired
            # the canonical WorkerRun row lock. Otherwise a lock wait can
            # carry a pre-expiry timestamp past the lease boundary.
            now = datetime.now(tz=UTC)
            if not self._worker_run_claim_matches(
                run,
                claim_owner=claim_owner,
                attempt_count=attempt_count,
                now=now,
            ):
                return None
            return await self._insert_worker_result_evidence_tx(
                conn,
                run_id=run_id,
                artifacts=artifacts,
                usage=usage,
                now=now,
            )

    async def complete_worker_run_success_claim_fenced(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, Any]],
        usage: dict[str, Any],
        result: dict[str, Any],
        replay_metadata: dict[str, Any] | None = None,
        project_usage: dict[str, Any] | None = None,
        reason: str = "worker emitted a normalized terminal result",
        correlation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Commit success only when the claimed run has no unresolved tool effects."""
        return await self._complete_worker_run_terminal_claim_fenced(
            run_id=run_id,
            claim_owner=claim_owner,
            attempt_count=attempt_count,
            artifacts=artifacts,
            usage=usage,
            result=result,
            terminal_state="SUCCEEDED",
            replay_metadata=replay_metadata,
            project_usage=project_usage,
            reason=reason,
            correlation_id=correlation_id,
        )

    async def complete_worker_run_failure_claim_fenced(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, Any]],
        usage: dict[str, Any],
        result: dict[str, Any],
        error: dict[str, Any] | None,
        replay_metadata: dict[str, Any] | None = None,
        project_usage: dict[str, Any] | None = None,
        reason: str = "worker emitted a normalized terminal result",
        correlation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically persist the evidence and terminal state for failure."""
        return await self._complete_worker_run_terminal_claim_fenced(
            run_id=run_id,
            claim_owner=claim_owner,
            attempt_count=attempt_count,
            artifacts=artifacts,
            usage=usage,
            result=result,
            terminal_state="FAILED",
            error=error,
            replay_metadata=replay_metadata,
            project_usage=project_usage,
            reason=reason,
            correlation_id=correlation_id,
        )

    async def _complete_worker_run_terminal_claim_fenced(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, Any]],
        usage: dict[str, Any],
        result: dict[str, Any],
        terminal_state: str,
        error: dict[str, Any] | None = None,
        replay_metadata: dict[str, Any] | None = None,
        project_usage: dict[str, Any] | None = None,
        reason: str = "worker emitted a normalized terminal result",
        correlation_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist a claimed run's result evidence and terminal state atomically.

        The WorkerRun lock is held from the claim check through the evidence
        inserts and terminal transition. Recovery therefore either reclaims
        the lease before this operation (and nothing is written), or observes
        a completed run after this transaction commits. If a requested success
        still has an in-flight or ambiguous mediated tool effect, the same
        transaction records a failed terminal result and keeps that evidence.
        """

        if terminal_state not in {"SUCCEEDED", "FAILED"}:
            raise ValueError("claimed terminal state must be SUCCEEDED or FAILED")

        async with self.engine.begin() as conn:
            run = (
                await conn.execute(
                    t.worker_runs.select()
                    .where(t.worker_runs.c.id == run_id)
                    .with_for_update()
                )
            ).mappings().first()
            now = datetime.now(tz=UTC)
            if not self._worker_run_claim_matches(
                run,
                claim_owner=claim_owner,
                attempt_count=attempt_count,
                now=now,
                reject_cancel_requested=True,
                allowed_states=("RUNNING", "PAUSING"),
            ):
                return None

            assert run is not None
            from_state = str(run["state"])
            if terminal_state == "SUCCEEDED":
                unresolved_effect = (
                    await conn.execute(
                        t.worker_tool_effects.select()
                        .with_only_columns(t.worker_tool_effects.c.id)
                        .where(
                            sa.and_(
                                t.worker_tool_effects.c.run_id == run_id,
                                t.worker_tool_effects.c.state.in_(
                                    ("IN_FLIGHT", "AMBIGUOUS")
                                ),
                            )
                        )
                        .order_by(t.worker_tool_effects.c.id.asc())
                        .limit(1)
                        .with_for_update()
                    )
                ).first()
                if unresolved_effect is not None:
                    terminal_state = "FAILED"
                    error = {
                        "code": "WORKER_TOOL_EFFECT_UNRESOLVED",
                        "message": (
                            "worker success cannot be committed while a mediated "
                            "tool effect is unresolved"
                        ),
                        "retryable": False,
                        "terminal": True,
                        "category": "recovery",
                        "details": {"effect_state": "IN_FLIGHT_OR_AMBIGUOUS"},
                    }
                    result = {
                        **result,
                        "success": False,
                        "error": error,
                    }
                    reason = (
                        "worker success blocked because a mediated tool effect "
                        "remains unresolved"
                    )

            values: dict[str, Any] = {
                "state": terminal_state,
                "completed_at": now,
                "claim_owner": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "result_json": result,
            }
            if terminal_state == "FAILED":
                values["error_json"] = error
            if replay_metadata is not None:
                values["replay_metadata"] = replay_metadata
            updated = await conn.execute(
                t.worker_runs.update()
                .where(
                    sa.and_(
                        t.worker_runs.c.id == run_id,
                        t.worker_runs.c.state == from_state,
                        t.worker_runs.c.claim_owner == claim_owner,
                        t.worker_runs.c.attempt_count == attempt_count,
                        t.worker_runs.c.lease_expires_at > now,
                        t.worker_runs.c.cancel_requested_at.is_(None),
                    )
                )
                .values(**values)
            )
            if not updated.rowcount:
                return None

            await self._insert_worker_result_evidence_tx(
                conn,
                run_id=run_id,
                artifacts=artifacts,
                usage=usage,
                now=now,
            )

            if project_usage is not None:
                event_type = project_usage.get("event_type")
                if event_type not in {"llm", "tool"}:
                    raise ValueError("project usage event_type must be 'llm' or 'tool'")
                project_id = project_usage["project_id"]
                normalized_project_id = (
                    project_id if isinstance(project_id, UUID) else UUID(str(project_id))
                )
                company_id = project_usage.get("company_id")
                if company_id is None:
                    company_id = (
                        await conn.execute(
                            sa.select(t.projects.c.company_id).where(
                                t.projects.c.id == normalized_project_id
                            )
                        )
                    ).scalar_one_or_none()
                project_trace_id = project_usage.get("trace_id") or current_trace_id()
                project_span_id = (
                    (project_usage.get("span_id") or current_span_id() or new_span_id())
                    if project_trace_id
                    else project_usage.get("span_id")
                )
                project_usage_values = {
                    "id": project_usage.get("event_id") or uuid4(),
                    "project_id": normalized_project_id,
                    "company_id": company_id,
                    "run_id": run_id,
                    "worker_id": project_usage.get("worker_id"),
                    "event_type": event_type,
                    "agent_id": project_usage.get("agent_id"),
                    "team_id": project_usage.get("team_id"),
                    "model": project_usage.get("model"),
                    "provider_id": project_usage.get("provider_id"),
                    "tool_name": project_usage.get("tool_name"),
                    "billing_code": project_usage.get("billing_code"),
                    "pricing_snapshot": project_usage.get("pricing_snapshot"),
                    "resource_json": project_usage.get("resource_json"),
                    "idempotency_key": project_usage.get("idempotency_key"),
                    "status": project_usage.get("status") or "success",
                    "prompt_tokens": max(0, int(project_usage.get("prompt_tokens") or 0)),
                    "completion_tokens": max(
                        0, int(project_usage.get("completion_tokens") or 0)
                    ),
                    "cost_usd": max(0.0, float(project_usage.get("cost_usd") or 0.0)),
                    "duration_ms": project_usage.get("duration_ms"),
                    "trace_id": project_trace_id,
                    "span_id": project_span_id,
                    "details": project_usage.get("details"),
                    "occurred_at": project_usage.get("occurred_at") or now,
                }
                idempotency_key = project_usage_values["idempotency_key"]
                existing_usage = None
                if idempotency_key:
                    existing_usage = (
                        await conn.execute(
                            t.project_usage_events.select().where(
                                t.project_usage_events.c.idempotency_key == idempotency_key
                            )
                        )
                    ).mappings().first()
                if existing_usage is None:
                    await conn.execute(
                        t.project_usage_events.insert().values(**project_usage_values)
                    )
                    if project_trace_id:
                        occurred_at = project_usage_values["occurred_at"]
                        duration_ms = project_usage_values["duration_ms"] or 0
                        await self._insert_native_trace_span_tx(
                            conn,
                            trace_id=project_trace_id,
                            span_id=new_span_id(),
                            parent_span_id=project_span_id,
                            source_kind="model" if event_type == "llm" else "tool",
                            operation=project_usage_values["model"]
                            or project_usage_values["tool_name"]
                            or event_type,
                            service="project_usage",
                            status=project_usage_values["status"],
                            started_at=occurred_at
                            - timedelta(milliseconds=float(duration_ms or 0)),
                            ended_at=occurred_at,
                            duration_ms=duration_ms,
                            attributes={
                                "event_type": event_type,
                                "agent_id": project_usage_values["agent_id"],
                                "team_id": project_usage_values["team_id"],
                            },
                        )

            await conn.execute(
                t.worker_run_runtime_bindings.update()
                .where(
                    sa.and_(
                        t.worker_run_runtime_bindings.c.run_id == run_id,
                        t.worker_run_runtime_bindings.c.attempt_count == attempt_count,
                    )
                )
                .values(status=terminal_state, updated_at=now)
            )
            await conn.execute(
                t.worker_run_transitions.insert().values(
                    id=uuid4(),
                    run_id=run_id,
                    from_state=from_state,
                    to_state=terminal_state,
                    actor="worker-run-controller",
                    reason=reason,
                    correlation_id=correlation_id,
                    metadata={},
                )
            )

        completed = dict(run)
        completed.update(values)
        return completed

    async def list_worker_runs(self, *, project_id: UUID | None = None, worker_id: UUID | None = None, flow_instance_id: UUID | None = None, state: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        q = t.worker_runs.select().order_by(t.worker_runs.c.created_at.desc()).limit(limit).offset(offset)
        clauses = []
        if project_id is not None:
            clauses.append(t.worker_runs.c.project_id == project_id)
        if worker_id is not None:
            clauses.append(t.worker_runs.c.worker_id == worker_id)
        if flow_instance_id is not None:
            clauses.append(t.worker_runs.c.flow_instance_id == flow_instance_id)
        if state is not None:
            clauses.append(t.worker_runs.c.state == state)
        if clauses:
            q = q.where(sa.and_(*clauses))
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(row) for row in rows]

    async def transition_worker_run(
        self,
        run_id: UUID,
        *,
        new_state: str,
        expected_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        negotiation: dict[str, Any] | None = None,
        replay_metadata: dict[str, Any] | None = None,
        actor: str = "worker-run-controller",
        reason: str | None = None,
        correlation_id: str | None = None,
        transition_metadata: dict[str, Any] | None = None,
        claim_owner: str | None = None,
        attempt_count: int | None = None,
        allow_unfenced_cancel: bool = False,
    ) -> dict[str, Any] | None:
        allowed = {"CREATED": {"QUEUED", "VALIDATING", "CANCELLED", "FAILED"}, "QUEUED": {"VALIDATING", "CANCELLED", "FAILED"}, "CLAIMED": {"VALIDATING", "QUEUED", "CANCELLED", "FAILED"}, "VALIDATING": {"READY", "FAILED", "CANCELLED", "QUEUED"}, "READY": {"DISPATCHING", "FAILED", "CANCELLED", "QUEUED"}, "DISPATCHING": {"RUNNING", "FAILED", "CANCELLED", "TIMED_OUT", "QUEUED"}, "RUNNING": {"PAUSING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "QUEUED"}, "PAUSING": {"PAUSED", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "QUEUED"}, "PAUSED": {"RESUMING", "CANCELLED", "FAILED", "QUEUED"}, "RESUMING": {"RUNNING", "FAILED", "CANCELLED", "QUEUED"}, "SUCCEEDED": set(), "FAILED": set(), "CANCELLED": set(), "TIMED_OUT": set()}
        async with self.engine.begin() as conn:
            current = (await conn.execute(t.worker_runs.select().where(t.worker_runs.c.id == run_id).with_for_update())).mappings().first()
            if not current:
                return None
            now = datetime.now(tz=UTC)
            if expected_state is not None and current["state"] != expected_state:
                return None
            current_owner = current.get("claim_owner")
            current_attempt = int(current.get("attempt_count") or 0)
            control_cancel = new_state == "CANCELLED" and allow_unfenced_cancel
            if not control_cancel and (current_owner is not None or current_attempt > 0):
                lease_expires_at = current.get("lease_expires_at")
                if (
                    claim_owner != current_owner
                    or attempt_count != current_attempt
                    or lease_expires_at is None
                    or lease_expires_at <= now
                ):
                    return None
            elif not control_cancel and (claim_owner is not None or attempt_count is not None):
                return None
            if new_state not in allowed.get(str(current["state"]), set()):
                raise ValueError(f"invalid worker run transition {current['state']} -> {new_state}")
            values: dict[str, Any] = {"state": new_state}
            if new_state == "RUNNING" and current.get("started_at") is None:
                values["started_at"] = now
            if new_state in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
                values["completed_at"] = now
                values.update({"claim_owner": None, "lease_expires_at": None, "heartbeat_at": None})
            if result is not None:
                values["result_json"] = result
            if error is not None:
                values["error_json"] = error
            if negotiation is not None:
                values["negotiation_json"] = negotiation
            if replay_metadata is not None:
                values["replay_metadata"] = replay_metadata
            runtime_binding = (
                transition_metadata.get("runtime_binding")
                if isinstance(transition_metadata, Mapping)
                else None
            )
            if isinstance(runtime_binding, Mapping):
                runtime_run_id = runtime_binding.get("runtime_run_id")
                runtime_type = str(runtime_binding.get("runtime_type") or "unknown")[:200]
                runtime_status = str(
                    runtime_binding.get("status")
                    or ("RUNNING" if new_state == "RUNNING" else "ACCEPTED")
                ).upper()
                allowed_binding_statuses = {
                    "ACCEPTED",
                    "RUNNING",
                    "PAUSED",
                    "SUCCEEDED",
                    "FAILED",
                    "CANCELLED",
                    "TIMED_OUT",
                    "UNKNOWN",
                }
                if runtime_status not in allowed_binding_statuses:
                    runtime_status = "UNKNOWN"
                binding_values = {
                    "id": uuid4(),
                    "run_id": run_id,
                    "attempt_count": int(
                        runtime_binding.get("attempt_count")
                        or current.get("attempt_count")
                        or 0
                    ),
                    "adapter_id": runtime_binding.get("adapter_id"),
                    "runtime_type": runtime_type,
                    "runtime_run_id": str(runtime_run_id) if runtime_run_id else None,
                    "status": runtime_status,
                    "metadata": dict(runtime_binding.get("metadata") or {}),
                    "updated_at": now,
                }
                binding_insert = pg_insert(t.worker_run_runtime_bindings).values(
                    **binding_values
                )
                await conn.execute(
                    binding_insert.on_conflict_do_update(
                        index_elements=["run_id", "attempt_count"],
                        set_={
                            "adapter_id": binding_values["adapter_id"],
                            "runtime_type": binding_values["runtime_type"],
                            "runtime_run_id": binding_values["runtime_run_id"],
                            "status": binding_values["status"],
                            "metadata": binding_values["metadata"],
                            "updated_at": now,
                        },
                    )
                )
            elif new_state in {"PAUSED", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
                # A terminal transition may not carry a fresh acceptance
                # payload.  Keep the durable subordinate binding in step with
                # the canonical run without changing ownership of that state.
                await conn.execute(
                    t.worker_run_runtime_bindings.update()
                    .where(
                        sa.and_(
                            t.worker_run_runtime_bindings.c.run_id == run_id,
                            t.worker_run_runtime_bindings.c.attempt_count
                            == int(current.get("attempt_count") or 0),
                        )
                    )
                    .values(status=new_state, updated_at=now)
                )
            update_query = t.worker_runs.update().where(t.worker_runs.c.id == run_id)
            if control_cancel:
                # Operator cancellation is authorized by the API boundary and
                # remains available even when an executor lease has expired.
                # All worker/controller transitions continue to require the
                # active owner and attempt fence.
                pass
            elif current_owner is not None or current_attempt > 0:
                update_query = update_query.where(
                    sa.and_(
                        t.worker_runs.c.claim_owner == claim_owner,
                        t.worker_runs.c.attempt_count == attempt_count,
                        t.worker_runs.c.lease_expires_at > now,
                    )
                )
            else:
                update_query = update_query.where(
                    sa.and_(
                        t.worker_runs.c.claim_owner.is_(None),
                        t.worker_runs.c.attempt_count <= 0,
                    )
                )
            updated = await conn.execute(update_query.values(**values))
            if not updated.rowcount:
                return None
            await conn.execute(
                t.worker_run_transitions.insert().values(
                    id=uuid4(),
                    run_id=run_id,
                    from_state=str(current["state"]),
                    to_state=new_state,
                    actor=actor,
                    reason=reason,
                    correlation_id=correlation_id,
                    metadata=transition_metadata or {},
                )
            )
        return await self.get_worker_run(run_id)

    async def get_worker_runtime_binding(
        self,
        run_id: UUID,
        *,
        attempt_count: int | None = None,
    ) -> dict[str, Any] | None:
        """Return the newest durable subordinate runtime binding for a run."""

        query = (
            t.worker_run_runtime_bindings.select()
            .where(t.worker_run_runtime_bindings.c.run_id == run_id)
            .order_by(t.worker_run_runtime_bindings.c.attempt_count.desc())
            .limit(1)
        )
        if attempt_count is not None:
            query = query.where(
                t.worker_run_runtime_bindings.c.attempt_count == attempt_count
            )
        async with self.engine.connect() as conn:
            row = (await conn.execute(query)).mappings().first()
        return dict(row) if row else None

    async def record_worker_runtime_observation(
        self,
        run_id: UUID,
        *,
        status: str,
        runtime_run_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist a subordinate runtime observation without changing run state.

        Reconciliation is deliberately not a worker-run transition.  This
        method updates only the newest per-attempt runtime binding, preserving
        the binding as an AIAT-owned audit of what the external runtime
        reported.  Canonical result/evidence settlement still goes through
        ``WorkerRunController``.
        """

        normalized_status = str(status or "UNKNOWN").strip().upper()
        allowed_statuses = {
            "ACCEPTED",
            "RUNNING",
            "PAUSED",
            "SUCCEEDED",
            "FAILED",
            "CANCELLED",
            "TIMED_OUT",
            "UNKNOWN",
        }
        if normalized_status not in allowed_statuses:
            normalized_status = "UNKNOWN"
        now = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            query = (
                t.worker_run_runtime_bindings.select()
                .where(t.worker_run_runtime_bindings.c.run_id == run_id)
                .order_by(t.worker_run_runtime_bindings.c.attempt_count.desc())
                .limit(1)
                .with_for_update()
            )
            row = (await conn.execute(query)).mappings().first()
            if row is None:
                return None
            values: dict[str, Any] = {
                "status": normalized_status,
                "last_reconciled_at": now,
                "updated_at": now,
            }
            if runtime_run_id is not None:
                values["runtime_run_id"] = str(runtime_run_id)
            await conn.execute(
                t.worker_run_runtime_bindings.update()
                .where(t.worker_run_runtime_bindings.c.id == row["id"])
                .values(**values)
            )
            updated = dict(row)
            updated.update(values)
            return updated

    async def begin_worker_tool_effect(
        self,
        *,
        run_id: UUID,
        request_id: UUID,
        idempotency_key: str,
        tool_name: str,
        request_sha256: str,
        claim_owner: str | None = None,
        attempt_count: int | None = None,
    ) -> dict[str, Any]:
        """Reserve or recover one mediated tool effect for a worker run.

        A worker may receive the same tool request again after the controller
        loses the response.  The effect row is created before dispatching the
        tool, so a later controller can replay a known response or fail closed
        when the earlier attempt is still ambiguous.  This method never calls
        the tool and never changes canonical worker-run state.
        """

        values = {
            "id": uuid4(),
            "run_id": run_id,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "tool_name": tool_name,
            "request_sha256": request_sha256,
            "state": "IN_FLIGHT",
        }
        async with self.engine.begin() as conn:
            # Serialize effect admission with claim recovery/reassignment.  A
            # replay is also denied to an expired owner: the run row is checked
            # before looking up the idempotency row.
            run = (
                await conn.execute(
                    t.worker_runs.select()
                    .where(t.worker_runs.c.id == run_id)
                    .with_for_update()
                )
            ).mappings().first()
            now = datetime.now(tz=UTC)
            if run is None:
                return {"disposition": "CLAIM_LOST"}
            supplied_attempt = int(attempt_count) if attempt_count is not None else -1
            current_claim = self._worker_run_claim_matches(
                run,
                claim_owner=claim_owner,
                attempt_count=supplied_attempt,
                now=now,
                reject_cancel_requested=True,
            )
            if not current_claim:
                return {"disposition": "CLAIM_LOST"}

            unresolved = (
                await conn.execute(
                    t.worker_tool_effects.select()
                    .with_only_columns(
                        t.worker_tool_effects.c.idempotency_key,
                        t.worker_tool_effects.c.request_sha256,
                    )
                    .where(
                        sa.and_(
                            t.worker_tool_effects.c.run_id == run_id,
                            t.worker_tool_effects.c.state.in_(
                                ("IN_FLIGHT", "AMBIGUOUS")
                            ),
                        )
                    )
                    .order_by(t.worker_tool_effects.c.id.asc())
                    .limit(1)
                    .with_for_update()
                )
            ).mappings().first()
            if unresolved is not None:
                if unresolved["idempotency_key"] == idempotency_key:
                    return {
                        "disposition": (
                            "CONFLICT"
                            if unresolved["request_sha256"] != request_sha256
                            else "AMBIGUOUS"
                        )
                    }
                # A runtime retry can establish a new MCP request ID after a
                # session reset. Keep unresolved prior effects from being
                # bypassed by that new idempotency key.
                return {"disposition": "AMBIGUOUS"}

            insert = pg_insert(t.worker_tool_effects).values(**values).on_conflict_do_nothing(
                index_elements=[
                    t.worker_tool_effects.c.run_id,
                    t.worker_tool_effects.c.idempotency_key,
                ]
            )
            await conn.execute(insert)
            row = (
                await conn.execute(
                    t.worker_tool_effects.select()
                    .where(
                        sa.and_(
                            t.worker_tool_effects.c.run_id == run_id,
                            t.worker_tool_effects.c.idempotency_key == idempotency_key,
                        )
                    )
                    .with_for_update()
                )
            ).mappings().first()
        if row is None:
            raise RuntimeError("worker tool effect reservation was not persisted")
        result = dict(row)
        if result["request_sha256"] != request_sha256:
            result["disposition"] = "CONFLICT"
        elif result["state"] == "COMPLETED" and result.get("response_json") is not None:
            result["disposition"] = "REPLAY"
        elif result["state"] == "AMBIGUOUS":
            result["disposition"] = "AMBIGUOUS"
        elif result["state"] == "IN_FLIGHT" and result["id"] == values["id"]:
            result["disposition"] = "NEW"
        else:
            # An existing in-flight row may have been created by a controller
            # that died before dispatch or response persistence.  Never rerun
            # it automatically; the effect is ambiguous until reconciled.
            result["disposition"] = "AMBIGUOUS"
        return result

    async def has_worker_tool_effects(self, run_id: UUID) -> bool:
        """Return whether a run has any durable mediated-tool effect record."""

        async with self.engine.connect() as conn:
            row = (
                await conn.execute(
                    t.worker_tool_effects.select()
                    .with_only_columns(t.worker_tool_effects.c.id)
                    .where(t.worker_tool_effects.c.run_id == run_id)
                    .limit(1)
                )
            ).first()
        return row is not None

    async def list_worker_tool_effects(
        self,
        run_id: UUID,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return bounded, payload-free effect evidence for one Worker Run.

        Request arguments and stored tool responses may contain project data,
        so this read model deliberately selects only effect identity,
        disposition, integrity digests, and timestamps. Operator views can
        prove that mediation happened without exposing tool payloads.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        columns = t.worker_tool_effects.c
        query = (
            sa.select(
                columns.id,
                columns.run_id,
                columns.request_id,
                columns.tool_name,
                columns.request_sha256,
                columns.state,
                columns.response_json["success"].as_boolean().label("response_success"),
                columns.created_at,
                columns.updated_at,
                columns.completed_at,
            )
            .where(columns.run_id == run_id)
            .order_by(columns.created_at.asc(), columns.id.asc())
            .limit(limit)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(query)).mappings().all()
        return [dict(row) for row in rows]

    async def complete_worker_tool_effect(
        self,
        effect_id: UUID,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        state: str,
        response_json: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Persist a mediated-tool response, fencing successful results by claim."""

        normalized_state = str(state).strip().upper()
        if normalized_state not in {"COMPLETED", "AMBIGUOUS"}:
            raise ValueError("worker tool effect completion state is invalid")
        if (
            not isinstance(effect_id, UUID)
            or not isinstance(run_id, UUID)
            or not isinstance(response_json, dict)
            or isinstance(attempt_count, bool)
            or not isinstance(attempt_count, int)
            or attempt_count < 0
        ):
            raise ValueError("worker tool effect completion payload is invalid")
        if normalized_state == "COMPLETED" and (
            not isinstance(claim_owner, str) or not claim_owner.strip() or attempt_count < 1
        ):
            return None
        if normalized_state == "AMBIGUOUS" and response_json.get("success") is True:
            return None
        async with self.engine.begin() as conn:
            if normalized_state == "COMPLETED":
                worker_run = (
                    await conn.execute(
                        t.worker_runs.select()
                        .where(t.worker_runs.c.id == run_id)
                        .with_for_update()
                    )
                ).mappings().first()
                now = datetime.now(tz=UTC)
                if not self._worker_run_claim_matches(
                    worker_run,
                    claim_owner=claim_owner,
                    attempt_count=attempt_count,
                    now=now,
                    reject_cancel_requested=True,
                ):
                    return None
            else:
                now = datetime.now(tz=UTC)
            result = await conn.execute(
                t.worker_tool_effects.update()
                .where(
                    sa.and_(
                        t.worker_tool_effects.c.id == effect_id,
                        t.worker_tool_effects.c.run_id == run_id,
                        t.worker_tool_effects.c.state == "IN_FLIGHT",
                    )
                )
                .values(
                    state=normalized_state,
                    response_json=response_json,
                    updated_at=now,
                    completed_at=now if normalized_state == "COMPLETED" else None,
                )
                .returning(t.worker_tool_effects)
            )
            row = result.mappings().first()
        return dict(row) if row else None

    async def append_worker_event(self, *, run_id: UUID, sequence: int, event_type: str, event: dict[str, Any], event_sha256: str, max_event_count: int | None = None) -> dict[str, Any]:
        values = {"id": uuid4(), "run_id": run_id, "sequence": sequence, "event_type": event_type, "event_json": event, "event_sha256": event_sha256}
        async with self.engine.begin() as conn:
            existing = (await conn.execute(t.worker_events.select().where(sa.and_(t.worker_events.c.run_id == run_id, t.worker_events.c.sequence == sequence)))).mappings().first()
            if existing:
                if existing["event_sha256"] != event_sha256:
                    raise ValueError("duplicate worker event sequence has different content")
                return dict(existing)
            if max_event_count is not None:
                current_count = int(
                    (
                        await conn.execute(
                            sa.select(sa.func.count())
                            .select_from(t.worker_events)
                            .where(t.worker_events.c.run_id == run_id)
                        )
                    ).scalar_one()
                )
                if current_count >= max_event_count:
                    raise ValueError("worker event limit exceeded")
            await conn.execute(t.worker_events.insert().values(**values))
        return values

    async def list_worker_events(self, run_id: UUID, *, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        q = t.worker_events.select().where(t.worker_events.c.run_id == run_id).order_by(t.worker_events.c.sequence.asc()).limit(limit).offset(offset)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(row) for row in rows]

    async def count_worker_events(self, run_id: UUID) -> int:
        query = sa.select(sa.func.count()).select_from(t.worker_events).where(t.worker_events.c.run_id == run_id)
        async with self.engine.connect() as conn:
            return int((await conn.execute(query)).scalar_one())

    async def create_worker_checkpoint(self, *, run_id: UUID, sequence: int, state: dict[str, Any], artifact_id: int | None = None, resumable: bool = True) -> dict[str, Any]:
        values = {"id": uuid4(), "run_id": run_id, "sequence": sequence, "state_json": state, "artifact_id": artifact_id, "resumable": resumable}
        async with self.engine.begin() as conn:
            await conn.execute(t.worker_checkpoints.insert().values(**values))
        return values

    async def list_worker_checkpoints(self, run_id: UUID, *, limit: int = 100) -> list[dict[str, Any]]:
        q = t.worker_checkpoints.select().where(t.worker_checkpoints.c.run_id == run_id).order_by(t.worker_checkpoints.c.sequence.desc()).limit(limit)
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(row) for row in rows]

    async def list_worker_run_transitions(self, run_id: UUID, *, limit: int = 1_000) -> list[dict[str, Any]]:
        q = (
            t.worker_run_transitions.select()
            .where(t.worker_run_transitions.c.run_id == run_id)
            .order_by(t.worker_run_transitions.c.created_at.asc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(row) for row in rows]

    async def list_worker_run_transitions_by_correlation(
        self,
        correlation_id: str,
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """List worker-run transitions correlated to one trace/message ID."""

        q = (
            t.worker_run_transitions.select()
            .where(t.worker_run_transitions.c.correlation_id == correlation_id)
            .order_by(t.worker_run_transitions.c.created_at.asc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(row) for row in rows]

    async def create_worker_artifact(
        self,
        *,
        run_id: UUID,
        artifact_id: int,
        kind: str,
        uri: str,
        sha256: str,
        size_bytes: int | None,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> dict[str, Any]:
        trace_id = trace_id or current_trace_id()
        effective_span_id = (span_id or current_span_id() or new_span_id()) if trace_id else span_id
        created_at_value = datetime.now(tz=UTC)
        values = {
            "id": uuid4(),
            "run_id": run_id,
            "artifact_id": artifact_id,
            "kind": kind,
            "uri": uri,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "metadata": metadata or {},
            "trace_id": trace_id,
            "span_id": effective_span_id,
            "created_at": created_at_value,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.worker_artifacts.insert().values(**values))
            if trace_id:
                await self._insert_native_trace_span_tx(
                    conn,
                    trace_id=trace_id,
                    span_id=new_span_id(),
                    parent_span_id=effective_span_id,
                    source_kind="audit",
                    operation=f"artifact.{kind}",
                    service="worker_evidence",
                    status="success",
                    started_at=created_at_value,
                    ended_at=created_at_value,
                    attributes={
                        "kind": kind,
                        "size_bytes": size_bytes or 0,
                    },
                )
        return values

    async def list_worker_artifacts(self, run_id: UUID, *, limit: int = 1_000) -> list[dict[str, Any]]:
        q = (
            t.worker_artifacts.select()
            .where(t.worker_artifacts.c.run_id == run_id)
            .order_by(t.worker_artifacts.c.created_at.asc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(row) for row in rows]

    async def create_worker_usage(self, *, run_id: UUID, usage: dict[str, Any]) -> dict[str, Any]:
        trace_id = usage.get("trace_id") or current_trace_id()
        effective_span_id = (usage.get("span_id") or current_span_id() or new_span_id()) if trace_id else usage.get("span_id")
        created_at_value = datetime.now(tz=UTC)
        values = {"id": uuid4(), "run_id": run_id, "prompt_tokens": usage.get("prompt_tokens", 0), "completion_tokens": usage.get("completion_tokens", 0), "total_tokens": usage.get("total_tokens", 0), "cost_usd": usage.get("cost_usd", 0), "duration_ms": usage.get("duration_ms", 0), "resource_json": usage.get("resource_json") or {}, "provider_id": usage.get("provider_id"), "exact_model_id": usage.get("exact_model_id"), "trace_id": trace_id, "span_id": effective_span_id, "created_at": created_at_value}
        async with self.engine.begin() as conn:
            await conn.execute(t.worker_usage_records.insert().values(**values))
            if trace_id:
                duration_ms = usage.get("duration_ms") or 0
                await self._insert_native_trace_span_tx(
                    conn,
                    trace_id=trace_id,
                    span_id=new_span_id(),
                    parent_span_id=effective_span_id,
                    source_kind="model",
                    operation=usage.get("exact_model_id") or "worker.model",
                    service="worker_evidence",
                    status="success",
                    started_at=created_at_value - timedelta(milliseconds=float(duration_ms or 0)),
                    ended_at=created_at_value,
                    duration_ms=duration_ms,
                    attributes={
                        "run_id": str(run_id),
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                    },
                )
        return values

    async def list_worker_usage(self, run_id: UUID, *, limit: int = 1_000) -> list[dict[str, Any]]:
        q = (
            t.worker_usage_records.select()
            .where(t.worker_usage_records.c.run_id == run_id)
            .order_by(t.worker_usage_records.c.created_at.asc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(q)).mappings().all()
        return [dict(row) for row in rows]

    async def create_project_repository_record(self, *, project_id: UUID, workspace_path: str, repository_mode: str, remote_url: str | None = None, branch: str | None = None, initialized: bool = False, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        values = {"id": uuid4(), "project_id": project_id, "workspace_path": workspace_path, "repository_mode": repository_mode, "remote_url": remote_url, "branch": branch, "initialized": initialized, "metadata": metadata or {}}
        async with self.engine.begin() as conn:
            await conn.execute(t.project_repository_records.insert().values(**values))
        return values

    async def get_project_repository_record(self, project_id: UUID) -> dict[str, Any] | None:
        return await self._get_table_row(t.project_repository_records, t.project_repository_records.c.project_id, project_id)

    async def update_project_repository_record(self, project_id: UUID, **kwargs: Any) -> dict[str, Any] | None:
        allowed = {"workspace_path", "repository_mode", "remote_url", "branch", "head_commit", "dirty", "last_sync_at", "adapter_health", "initialized", "metadata"}
        values = {key: value for key, value in kwargs.items() if key in allowed}
        values["updated_at"] = datetime.now(tz=UTC)
        async with self.engine.begin() as conn:
            result = await conn.execute(t.project_repository_records.update().where(t.project_repository_records.c.project_id == project_id).values(**values))
            if result.rowcount == 0:
                return None
        return await self.get_project_repository_record(project_id)

    async def create_project_evidence_package(self, *, project_id: UUID, policy_id: str, policy_version: str, status: str, checks: dict[str, Any], evidence_refs: dict[str, Any], completeness_score: float) -> dict[str, Any]:
        """Upsert the latest operator-generated package for one policy version."""

        values = {
            "id": uuid4(),
            "project_id": project_id,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "status": status,
            "checks": checks,
            "evidence_refs": evidence_refs,
            "completeness_score": completeness_score,
            "generated_at": datetime.now(tz=UTC),
        }
        async with self.engine.begin() as conn:
            stmt = (
                pg_insert(t.project_evidence_packages)
                .values(**values)
                .on_conflict_do_update(
                    constraint="uq_project_evidence_policy",
                    set_={
                        "status": values["status"],
                        "checks": values["checks"],
                        "evidence_refs": values["evidence_refs"],
                        "completeness_score": values["completeness_score"],
                        "generated_at": values["generated_at"],
                    },
                )
                .returning(t.project_evidence_packages)
            )
            row = await self._mapping_first(await conn.execute(stmt))
        return dict(row) if row else values

    async def get_project_evidence_package(self, project_id: UUID, *, policy_id: str | None = None) -> dict[str, Any] | None:
        q = t.project_evidence_packages.select().where(t.project_evidence_packages.c.project_id == project_id).order_by(t.project_evidence_packages.c.generated_at.desc()).limit(1)
        if policy_id:
            q = q.where(t.project_evidence_packages.c.policy_id == policy_id)
        async with self.engine.connect() as conn:
            row = (await conn.execute(q)).mappings().first()
        return dict(row) if row else None
