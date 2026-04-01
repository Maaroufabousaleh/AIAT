"""capability registry tables: capabilities, worker_registry, role_capability_map

Revision ID: 0003_capability_registry
Revises: 0002_missing_tables
Create Date: 2026-03-06 00:00:00.000000

Tables added
------------
18. capabilities        — canonical capability catalog (name, schema, risk level)
19. worker_registry     — registered worker adapters with capability bindings
20. role_capability_map — which roles may invoke which capabilities (priority + constraints)

This completes the 20-table schema described in the architecture plan §10 / §17.2.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB  # noqa: F401 — sa.JSONB() resolved at runtime

revision: str = "0003_capability_registry"
down_revision = "0002_missing_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 18. capabilities ──────────────────────────────────────────────────────
    op.create_table(
        "capabilities",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "name",
            sa.Text(),
            nullable=False,
            comment="Canonical capability name, e.g. 'implement_feature', 'write_test'",
        ),
        sa.UniqueConstraint("name", name="uq_capabilities_name"),
        sa.Column("version", sa.Text(), nullable=False, server_default="'1.0'"),
        sa.Column("description", sa.Text()),
        sa.Column("input_schema", sa.JSONB(), comment="JSON Schema for expected input"),
        sa.Column("output_schema", sa.JSONB(), comment="JSON Schema for expected output"),
        sa.Column(
            "risk_level",
            sa.Text(),
            nullable=False,
            server_default="'low'",
            comment="low | medium | high | critical",
        ),
        sa.Column(
            "cost_model",
            sa.JSONB(),
            comment='Per-invocation cost estimate, e.g. {"per_invocation": 0.05, "currency": "USD"}',
        ),
        sa.Column("required_tools", sa.ARRAY(sa.Text()), server_default="'{}'"),
        sa.Column(
            "required_role",
            sa.Text(),
            comment="Minimum AgentRole value required (orchestrator/executive/c_suite/admin/worker/sub_agent)",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Note: idx_capabilities_name is NOT needed — uq_capabilities_name already creates a unique index.
    op.create_index("idx_capabilities_risk", "capabilities", ["risk_level"])

    # ── 19. worker_registry ───────────────────────────────────────────────────
    op.create_table(
        "worker_registry",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "name",
            sa.Text(),
            nullable=False,
            comment="Worker identifier, e.g. 'devops_eng_1', 'code_reviewer_ast'",
        ),
        sa.UniqueConstraint("name", name="uq_worker_registry_name"),
        sa.Column(
            "adapter_type",
            sa.Text(),
            nullable=False,
            comment="process | http | oci | mcp | human",
        ),
        sa.Column(
            "adapter_config",
            sa.JSONB(),
            nullable=False,
            server_default="'{}'",
            comment="Per-adapter connection config (cmd/args, URL, image, mcp_url, etc.)",
        ),
        sa.Column(
            "sandbox_profile",
            sa.Text(),
            nullable=False,
            server_default="'standard'",
            comment="standard | restricted | gvisor | firecracker",
        ),
        sa.Column(
            "capability_ids",
            sa.ARRAY(sa.UUID()),
            nullable=False,
            server_default="'{}'",
            comment="UUIDs referencing capabilities(id)",
        ),
        sa.Column(
            "team_id",
            sa.Text(),
            comment="Team stream this worker belongs to, e.g. 'dept_production'",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="'ACTIVE'",
            comment="ACTIVE | INACTIVE | DRAINING",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("idx_worker_registry_adapter", "worker_registry", ["adapter_type"])
    op.create_index("idx_worker_registry_status", "worker_registry", ["status"])
    op.create_index("idx_worker_registry_team", "worker_registry", ["team_id"])

    # ── 20. role_capability_map ───────────────────────────────────────────────
    op.create_table(
        "role_capability_map",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "role",
            sa.Text(),
            nullable=False,
            comment="AgentRole value: orchestrator | executive | c_suite | admin | worker | sub_agent",
        ),
        sa.Column(
            "capability_id",
            sa.UUID(),
            sa.ForeignKey("capabilities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Higher = preferred worker selection order",
        ),
        sa.Column(
            "constraints",
            sa.JSONB(),
            comment='Role-specific constraints, e.g. {"max_concurrent": 2, "requires_approval": false}',
        ),
        sa.UniqueConstraint("role", "capability_id", name="uq_role_capability"),
    )
    op.create_index("idx_role_capability_role", "role_capability_map", ["role"])
    op.create_index("idx_role_capability_cap", "role_capability_map", ["capability_id"])


def downgrade() -> None:
    op.drop_table("role_capability_map")
    op.drop_table("worker_registry")
    op.drop_table("capabilities")
