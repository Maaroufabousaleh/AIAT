"""CEO Privilege Separation — two-layer authority model.

Layer 1: Executive Authority (normal CEO operations)
    - Orchestration, project supervision, workflow steering
    - Communication with all teams
    - Status aggregation, alerting, operational copilot functions
    - Flow invocation, context management
    - Human notification and decision requests

Layer 2: Privileged Operations (gated, audited, optional step-up approval)
    - System control scripts (shutdown, restart, rebuild)
    - Container/service lifecycle operations
    - Team shutdown and restart commands
    - Infrastructure-level operations
    - Credential access beyond normal scope
    - Security policy overrides

Design:
    The ``PrivilegedOpsGate`` is the sole enforcement point for Layer 2.
    ALL privileged action requests from the CEO are checked here before
    execution.  Every check is logged to the ``privileged_ops_audit`` table.
    When ``require_approval=True`` for an action, the gate returns
    ``PENDING_APPROVAL`` and inserts a pending approval record instead of
    executing immediately.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

import sqlalchemy as sa

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action classification
# ---------------------------------------------------------------------------


class PrivilegeLevel(StrEnum):
    EXECUTIVE = "executive"  # Layer 1 — normal CEO permissions
    PRIVILEGED = "privileged"  # Layer 2 — gated operations


# Privileged operations that require Layer 2 approval/audit
PRIVILEGED_ACTIONS: dict[str, dict[str, Any]] = {
    # System lifecycle
    "system.shutdown": {"require_approval": True, "risk": "critical"},
    "system.restart": {"require_approval": True, "risk": "critical"},
    "system.rebuild": {"require_approval": True, "risk": "high"},
    "system.wipe": {"require_approval": True, "risk": "critical"},
    # Team control
    "team.shutdown": {"require_approval": True, "risk": "high"},
    "team.restart": {"require_approval": False, "risk": "medium"},
    "team.drain": {"require_approval": False, "risk": "medium"},
    # Container operations
    "container.stop": {"require_approval": True, "risk": "high"},
    "container.start": {"require_approval": False, "risk": "low"},
    "container.remove": {"require_approval": True, "risk": "critical"},
    # Credential access
    "credentials.resolve": {"require_approval": False, "risk": "medium"},
    "credentials.export": {"require_approval": True, "risk": "critical"},
    # Security overrides
    "security.override_cso": {"require_approval": True, "risk": "high"},
    "policy.override": {"require_approval": True, "risk": "critical"},
    # Infrastructure
    "infra.provision": {"require_approval": False, "risk": "medium"},
    "infra.deprovision": {"require_approval": True, "risk": "high"},
    # Worker management
    "worker.force_stop": {"require_approval": True, "risk": "high"},
    "worker.delete": {"require_approval": True, "risk": "high"},
}

# Normal CEO executive actions (Layer 1) — no gate needed
EXECUTIVE_ACTIONS: set[str] = {
    "project.create",
    "project.status",
    "project.transition",
    "review.aggregate",
    "approval.override_cso",
    "human.notify",
    "human.await_decision",
    "flow.invoke",
    "flow.switch",
    "flow.inspect",
    "team.query",
    "team.broadcast",
    "context.update",
    "context.read",
    "alert.send",
    "status.aggregate",
}


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


class PrivilegedOpsGate:
    """Enforcement point for CEO Layer 2 (privileged) operations.

    Parameters
    ----------
    conn_factory:
        Async callable returning an ``AsyncConnection`` (e.g. ``engine.begin``).
    """

    _CREATE_AUDIT_TABLE = sa.text("""
        CREATE TABLE IF NOT EXISTS privileged_ops_audit (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            action          TEXT NOT NULL,
            actor_id        TEXT NOT NULL,
            actor_role      TEXT NOT NULL DEFAULT 'ceo',
            payload_json    JSONB NOT NULL DEFAULT '{}',
            privilege_level TEXT NOT NULL,
            risk_level      TEXT NOT NULL,
            requires_approval BOOLEAN NOT NULL,
            decision        TEXT NOT NULL DEFAULT 'pending',
            decided_by      TEXT,
            decided_at      TIMESTAMPTZ,
            reason          TEXT,
            requested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    def __init__(self, conn_factory: Any) -> None:
        self._conn_factory = conn_factory
        # Override: set to True to require approval for ALL privileged ops
        self._require_all_approval = (
            os.getenv("CEO_REQUIRE_ALL_APPROVAL", "false").lower() == "true"
        )

    async def ensure_tables(self) -> None:
        async with self._conn_factory() as conn:
            await conn.execute(self._CREATE_AUDIT_TABLE)
            await conn.commit()

    def classify(self, action: str) -> PrivilegeLevel:
        """Classify an action as executive or privileged."""
        if action in EXECUTIVE_ACTIONS:
            return PrivilegeLevel.EXECUTIVE
        if action in PRIVILEGED_ACTIONS:
            return PrivilegeLevel.PRIVILEGED
        # Unknown actions default to executive (fail-open with audit)
        return PrivilegeLevel.EXECUTIVE

    async def check(
        self,
        action: str,
        *,
        actor_id: str = "ceo",
        actor_role: str = "ceo",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Check whether a privileged action is allowed.

        Returns a dict with:
            ``allowed``    — True if the action may proceed immediately
            ``level``      — "executive" | "privileged"
            ``decision``   — "approved" | "pending_approval" | "denied"
            ``record_id``  — UUID of the audit record (for approval callbacks)
            ``reason``     — human-readable explanation
        """
        level = self.classify(action)
        meta = PRIVILEGED_ACTIONS.get(action, {})
        risk = meta.get("risk", "unknown")
        requires_approval = meta.get("require_approval", False) or self._require_all_approval

        if level == PrivilegeLevel.EXECUTIVE:
            # Layer 1 — always allowed, no audit needed
            return {
                "allowed": True,
                "level": level,
                "decision": "approved",
                "record_id": None,
                "reason": "executive_authority",
            }

        # Layer 2 — audit + optional approval gate
        record_id = str(uuid4())
        decision = "pending_approval" if requires_approval else "approved"
        allowed = not requires_approval

        await self._audit(
            record_id=record_id,
            action=action,
            actor_id=actor_id,
            actor_role=actor_role,
            payload=payload or {},
            level=level,
            risk=risk,
            requires_approval=requires_approval,
            decision=decision,
        )

        if not allowed:
            logger.warning(
                "privileged_ops.pending_approval action=%s actor=%s risk=%s record=%s",
                action,
                actor_id,
                risk,
                record_id,
            )
        else:
            logger.info(
                "privileged_ops.approved action=%s actor=%s risk=%s record=%s",
                action,
                actor_id,
                risk,
                record_id,
            )

        return {
            "allowed": allowed,
            "level": level,
            "decision": decision,
            "record_id": record_id,
            "risk": risk,
            "reason": (
                "pending_human_approval" if requires_approval else "privileged_auto_approved"
            ),
        }

    async def approve(
        self,
        record_id: str,
        *,
        decided_by: str,
        approved: bool,
        reason: str = "",
    ) -> bool:
        """Record a human approval or rejection for a pending privileged op."""
        decision = "approved" if approved else "rejected"
        async with self._conn_factory() as conn:
            result = await conn.execute(
                sa.text("""
                    UPDATE privileged_ops_audit
                    SET decision = :decision,
                        decided_by = :decided_by,
                        decided_at = :now,
                        reason = :reason
                    WHERE id = :id AND decision = 'pending_approval'
                    RETURNING id
                """),
                {
                    "decision": decision,
                    "decided_by": decided_by,
                    "now": datetime.now(UTC),
                    "reason": reason,
                    "id": record_id,
                },
            )
            await conn.commit()
            return result.rowcount > 0

    async def list_pending(self) -> list[dict[str, Any]]:
        """List all pending privileged operation requests."""
        async with self._conn_factory() as conn:
            rows = await conn.execute(
                sa.text("""
                    SELECT id, action, actor_id, actor_role, privilege_level,
                           risk_level, payload_json, requires_approval, decision,
                           decided_by, decided_at, reason, requested_at
                    FROM privileged_ops_audit
                    WHERE decision = 'pending_approval'
                    ORDER BY requested_at DESC
                """)
            )
            return [dict(r) for r in rows.mappings().all()]

    async def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the full privileged ops audit log."""
        async with self._conn_factory() as conn:
            rows = await conn.execute(
                sa.text("""
                    SELECT id, action, actor_id, actor_role, privilege_level,
                           risk_level, decision, decided_by, decided_at, reason, requested_at
                    FROM privileged_ops_audit
                    ORDER BY requested_at DESC
                    LIMIT :lim
                """),
                {"lim": limit},
            )
            return [dict(r) for r in rows.mappings().all()]

    async def _audit(
        self,
        *,
        record_id: str,
        action: str,
        actor_id: str,
        actor_role: str,
        payload: dict[str, Any],
        level: PrivilegeLevel,
        risk: str,
        requires_approval: bool,
        decision: str,
    ) -> None:
        import json

        try:
            async with self._conn_factory() as conn:
                await conn.execute(
                    sa.text("""
                        INSERT INTO privileged_ops_audit
                            (id, action, actor_id, actor_role, payload_json,
                             privilege_level, risk_level, requires_approval,
                             decision, requested_at)
                        VALUES
                            (:id, :action, :actor_id, :actor_role, :payload,
                             :level, :risk, :requires_approval, :decision, :now)
                    """),
                    {
                        "id": record_id,
                        "action": action,
                        "actor_id": actor_id,
                        "actor_role": actor_role,
                        "payload": json.dumps(payload),
                        "level": str(level),
                        "risk": risk,
                        "requires_approval": requires_approval,
                        "decision": decision,
                        "now": datetime.now(UTC),
                    },
                )
                await conn.commit()
        except Exception:
            logger.exception("privileged_ops.audit_write_failed action=%s", action)
