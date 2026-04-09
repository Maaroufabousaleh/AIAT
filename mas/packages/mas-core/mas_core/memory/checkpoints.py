"""CheckpointStore — save / load / delete agent checkpoints in Postgres.

Agents call ``save_checkpoint()`` after each LLM call + tool result pair in the
``think()`` loop.  On resume (after reboot or container restart), the agent
calls ``load_checkpoint()`` and picks up where it left off.

After a task completes (or is DLQ'd), ``delete_checkpoint()`` removes the row.

Checkpoint cost: one Postgres INSERT per LLM call (~1–10 KB JSON).
At 10–20 LLM calls per task, negligible compared to LLM API latency.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from . import models as t

logger = logging.getLogger(__name__)


class CheckpointStore:
    """Thin wrapper around the ``agent_checkpoints`` table.

    Parameters
    ----------
    engine : AsyncEngine
        The shared SQLAlchemy async engine (from ``AgentStorage.engine``).
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def save(
        self,
        *,
        agent_id: str,
        team_id: str,
        task_message_id: str,
        iteration: int,
        messages_json: list[dict],
        project_id: UUID | None = None,
        tool_results_json: list[dict] | None = None,
        budget_state_json: dict | None = None,
        task_envelope_json: dict,
        checkpoint_id: UUID | None = None,
    ) -> UUID:
        """Save (upsert) an agent checkpoint.

        Uses ``ON CONFLICT (agent_id, task_message_id) DO UPDATE`` so that
        repeated calls from the same task overwrite the previous checkpoint
        rather than creating duplicates.

        Returns the checkpoint UUID.
        """
        cid = checkpoint_id or uuid4()
        now = datetime.now(tz=UTC)
        values: dict[str, Any] = {
            "id": cid,
            "agent_id": agent_id,
            "team_id": team_id,
            "project_id": project_id,
            "task_message_id": task_message_id,
            "iteration": iteration,
            "messages_json": messages_json,
            "tool_results_json": tool_results_json,
            "budget_state_json": budget_state_json,
            "task_envelope_json": task_envelope_json,
            "saved_at": now,
        }
        stmt = (
            pg_insert(t.agent_checkpoints)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_checkpoint_agent_task",
                set_={
                    "iteration": iteration,
                    "messages_json": messages_json,
                    "tool_results_json": tool_results_json,
                    "budget_state_json": budget_state_json,
                    "task_envelope_json": task_envelope_json,
                    "saved_at": now,
                },
            )
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
        logger.debug(
            "checkpoint_saved",
            extra={"agent_id": agent_id, "task_message_id": task_message_id, "iteration": iteration},
        )
        return cid

    async def load(
        self,
        agent_id: str,
        task_message_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Load the most recent checkpoint for an agent.

        If *task_message_id* is given, loads the exact checkpoint for that task.
        Otherwise, loads the newest checkpoint for the agent.
        """
        q = t.agent_checkpoints.select().where(
            t.agent_checkpoints.c.agent_id == agent_id
        )
        if task_message_id:
            q = q.where(t.agent_checkpoints.c.task_message_id == task_message_id)
        q = q.order_by(t.agent_checkpoints.c.saved_at.desc()).limit(1)
        async with self._engine.connect() as conn:
            row = (await conn.execute(q)).mappings().first()
        return dict(row) if row else None

    async def load_all_for_team(self, team_id: str) -> list[dict[str, Any]]:
        """Load all checkpoints for a team (used during team-runner startup)."""
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    t.agent_checkpoints.select()
                    .where(t.agent_checkpoints.c.team_id == team_id)
                    .order_by(t.agent_checkpoints.c.saved_at.desc())
                )
            ).mappings().all()
        return [dict(r) for r in rows]

    async def delete(
        self,
        agent_id: str,
        task_message_id: str,
    ) -> bool:
        """Delete a checkpoint after successful task completion.

        Returns True if a row was deleted, False if not found.
        """
        async with self._engine.begin() as conn:
            result = await conn.execute(
                t.agent_checkpoints.delete().where(
                    sa.and_(
                        t.agent_checkpoints.c.agent_id == agent_id,
                        t.agent_checkpoints.c.task_message_id == task_message_id,
                    )
                )
            )
        deleted = result.rowcount > 0
        if deleted:
            logger.debug(
                "checkpoint_deleted",
                extra={"agent_id": agent_id, "task_message_id": task_message_id},
            )
        return deleted

    async def delete_all_for_agent(self, agent_id: str) -> int:
        """Delete all checkpoints for an agent. Returns count deleted."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                t.agent_checkpoints.delete().where(
                    t.agent_checkpoints.c.agent_id == agent_id
                )
            )
        return result.rowcount

    async def count(self, agent_id: str | None = None) -> int:
        """Count checkpoints, optionally filtered by agent."""
        q = sa.select(sa.func.count()).select_from(t.agent_checkpoints)
        if agent_id:
            q = q.where(t.agent_checkpoints.c.agent_id == agent_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(q)).scalar()
        return row or 0
