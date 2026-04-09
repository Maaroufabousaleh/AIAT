"""orchestration flows tables: flows, flow_instances, flow_node_executions

Revision ID: 0004_orchestration_flows
Revises: 0003_capability_registry
Create Date: 2026-04-03 00:00:00.000000

Tables added
------------
21. flows                — flow definitions (JSONB graph + versioning)
22. flow_instances       — active flow attached to a project (version-pinned)
23. flow_node_executions — per-node execution audit trail

Design notes
------------
- The entire flow graph (nodes + edges) is stored in flows.definition_json (JSONB).
  No separate flow_nodes / flow_edges tables — the graph is small and versioned.
- flow_instances pin to a specific flow_version so edits to a flow don't break
  running instances.
- active_node_ids is an array to support parallel execution (multiple nodes
  active simultaneously).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004_orchestration_flows"
down_revision = "0003_capability_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 21. flows ─────────────────────────────────────────────────────────────
    op.create_table(
        "flows",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "name",
            sa.Text(),
            nullable=False,
            comment="Human-readable flow name",
        ),
        sa.Column("description", sa.Text(), comment="Optional description"),
        sa.Column(
            "definition_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="Full graph: {nodes: [...], edges: [...]} — versioned snapshot",
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("'1'"),
            comment="Auto-incremented on each edit",
        ),
        sa.Column(
            "created_by",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'system'"),
            comment="User or role who created the flow",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("'false'"),
            comment="Whether this flow can be attached to new projects",
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
    op.create_index("idx_flows_active", "flows", ["is_active"])
    op.create_index("idx_flows_created_by", "flows", ["created_by"])

    # ── 22. flow_instances ────────────────────────────────────────────────────
    op.create_table(
        "flow_instances",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "flow_id",
            sa.UUID(),
            sa.ForeignKey("flows.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "flow_version",
            sa.Integer(),
            nullable=False,
            comment="Pinned version of the flow definition",
        ),
        sa.Column(
            "project_id",
            sa.UUID(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "active_node_ids",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="Currently executing node IDs (supports parallel)",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'NOT_STARTED'"),
            comment="NOT_STARTED | RUNNING | WAITING_APPROVAL | PAUSED | CANCELLED | COMPLETED | FAILED",
        ),
        sa.Column(
            "context_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="Accumulated execution context from node outputs",
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            comment="When the instance was first advanced",
        ),
        sa.Column(
            "completed_at",
            sa.TIMESTAMP(timezone=True),
            comment="When the instance reached an end node",
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
    op.create_index("idx_flow_instances_flow", "flow_instances", ["flow_id"])
    op.create_index("idx_flow_instances_project", "flow_instances", ["project_id"])
    op.create_index("idx_flow_instances_status", "flow_instances", ["status"])
    op.create_index(
        "idx_flow_instances_project_active",
        "flow_instances",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('NOT_STARTED', 'RUNNING', 'WAITING_APPROVAL', 'PAUSED')"
        ),
    )

    # ── 23. flow_node_executions ──────────────────────────────────────────────
    op.create_table(
        "flow_node_executions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "instance_id",
            sa.UUID(),
            sa.ForeignKey("flow_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.Text(),
            nullable=False,
            comment="Node ID from the flow definition graph",
        ),
        sa.Column(
            "node_type",
            sa.Text(),
            nullable=False,
            comment="start | end | task | approval | condition | parallel | join",
        ),
        sa.Column(
            "node_label",
            sa.Text(),
            comment="Human-readable label of the node",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'RUNNING'"),
            comment="RUNNING | COMPLETED | FAILED | SKIPPED",
        ),
        sa.Column(
            "input_json",
            JSONB(),
            comment="Input passed to this node from context",
        ),
        sa.Column(
            "output_json",
            JSONB(),
            comment="Output produced by this node",
        ),
        sa.Column(
            "error",
            sa.Text(),
            comment="Error message if status = FAILED",
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.TIMESTAMP(timezone=True),
            comment="When this node finished",
        ),
    )
    op.create_index("idx_flow_node_exec_instance", "flow_node_executions", ["instance_id"])
    op.create_index("idx_flow_node_exec_node", "flow_node_executions", ["node_id"])


def downgrade() -> None:
    op.drop_table("flow_node_executions")
    op.drop_table("flow_instances")
    op.drop_table("flows")
