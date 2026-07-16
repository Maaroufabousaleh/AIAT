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
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from . import models as t

logger = logging.getLogger(__name__)

_AGENT_PROFILE_NUMERIC_MIN = Decimal("-9.9999")
_AGENT_PROFILE_NUMERIC_MAX = Decimal("9.9999")


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
        project_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Insert a new project row. Returns the full row as a dict."""
        pid = project_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": pid,
            "name": name,
            "description": description,
            "state": state,
            "created_by": created_by,
            "human_requester": human_requester,
            "config": config,
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
                (await conn.execute(t.projects.select().where(t.projects.c.id == project_id)))
                .mappings()
                .first()
            )
        return dict(row) if row else None

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
    ) -> dict[str, Any]:
        """Create a pending approval gate."""
        gid = gate_id or uuid4()
        now = datetime.now(tz=UTC)
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
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.sprints.insert().values(**values))
        return values

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
        """Update sprint fields (status, points, hours, dates, etc.)."""
        async with self.engine.begin() as conn:
            await conn.execute(
                t.sprints.update().where(t.sprints.c.id == sprint_id).values(**kwargs)
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
            "created_at": now,
        }
        async with self.engine.begin() as conn:
            await conn.execute(t.issues.insert().values(**values))
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
        """Update issue fields (status, hours, assignment, etc.)."""
        async with self.engine.begin() as conn:
            await conn.execute(t.issues.update().where(t.issues.c.id == issue_id).values(**kwargs))

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
        sandbox_profile: str = "standard",
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
    ) -> dict[str, Any]:
        """Register or re-register a worker (upsert on name)."""
        wid = worker_id or uuid4()
        now = datetime.now(tz=UTC)
        values = {
            "id": wid,
            "name": name,
            "adapter_type": adapter_type,
            "adapter_config": adapter_config or {},
            "sandbox_profile": sandbox_profile,
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
            "created_at": now,
            "updated_at": now,
        }
        stmt = (
            pg_insert(t.worker_registry)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_worker_registry_name",
                set_={
                    "adapter_type": adapter_type,
                    "adapter_config": adapter_config or {},
                    "sandbox_profile": sandbox_profile,
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
                },
            )
        )
        async with self.engine.begin() as conn:
            await conn.execute(stmt)
        return values

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
    ) -> None:
        """Update a worker's configuration fields (partial update)."""
        values: dict[str, Any] = {"updated_at": datetime.now(tz=UTC)}
        if adapter_type is not None:
            values["adapter_type"] = adapter_type
        if adapter_config is not None:
            values["adapter_config"] = adapter_config
        if sandbox_profile is not None:
            values["sandbox_profile"] = sandbox_profile
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
        if len(values) == 1:
            return
        async with self.engine.begin() as conn:
            await conn.execute(
                t.worker_registry.update()
                .where(t.worker_registry.c.id == worker_id)
                .values(**values)
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
    ) -> dict[str, Any] | None:
        """Retry a failed flow instance by resetting to NOT_STARTED."""
        instance = await self.get_flow_instance(instance_id)
        if instance is None:
            return None

        if instance["status"] not in ("FAILED", "CANCELLED"):
            return None

        await self.update_flow_instance(
            instance_id,
            status="NOT_STARTED",
            active_node_ids=[],
            retry_count=instance.get("retry_count", 0) + 1,
            started_at=None,
            completed_at=None,
        )
        await self.clear_flow_node_executions(instance_id)

        return await self.get_flow_instance(instance_id)

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
