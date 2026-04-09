"""Add flow execution config fields: retry, timeout, escalate, switch.

Revision ID: 0005_flow_execution_config
Revises: 0004_orchestration_flows
Create Date: 2026-04-04 00:00:00.000000

Tables modified
--------------
1. flows                 — add metadata_json (JSONB) for flow-level config
2. flow_instances        — add retry_count, max_retries, escalated_to, escalation_reason
3. flow_node_executions  — add retry_count, max_retries, timeout_at

Design notes
------------
- metadata_json on flows allows flow-level settings (default timeout, default retries)
- retry_count/max_retries track retry attempts on instances and executions
- escalated_to/escalation_reason track manual escalations
- timeout_at allows tracking per-node timeouts
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005_flow_execution_config"
down_revision = "0004_orchestration_flows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flows",
        sa.Column(
            "metadata_json",
            JSONB(),
            server_default=sa.text("'{}'"),
            comment="Flow-level config: default timeout, retries, tags",
        ),
    )

    op.add_column(
        "flow_instances",
        sa.Column(
            "retry_count",
            sa.Integer(),
            server_default=sa.text("'0'"),
            comment="Current retry count for the instance",
        ),
    )
    op.add_column(
        "flow_instances",
        sa.Column(
            "max_retries",
            sa.Integer(),
            server_default=sa.text("'3'"),
            comment="Maximum retries before failing the instance",
        ),
    )
    op.add_column(
        "flow_instances",
        sa.Column(
            "escalated_to",
            sa.Text(),
            comment="Team or agent this instance was escalated to",
        ),
    )
    op.add_column(
        "flow_instances",
        sa.Column(
            "escalation_reason",
            sa.Text(),
            comment="Reason for escalation",
        ),
    )

    op.add_column(
        "flow_node_executions",
        sa.Column(
            "retry_count",
            sa.Integer(),
            server_default=sa.text("'0'"),
            comment="Current retry count for this node",
        ),
    )
    op.add_column(
        "flow_node_executions",
        sa.Column(
            "max_retries",
            sa.Integer(),
            server_default=sa.text("'3'"),
            comment="Maximum retries before failing this node",
        ),
    )
    op.add_column(
        "flow_node_executions",
        sa.Column(
            "timeout_at",
            sa.TIMESTAMP(timezone=True),
            comment="Expected timeout for this node execution",
        ),
    )


def downgrade() -> None:
    op.drop_column("flow_node_executions", "timeout_at")
    op.drop_column("flow_node_executions", "max_retries")
    op.drop_column("flow_node_executions", "retry_count")
    op.drop_column("flow_instances", "escalation_reason")
    op.drop_column("flow_instances", "escalated_to")
    op.drop_column("flow_instances", "max_retries")
    op.drop_column("flow_instances", "retry_count")
    op.drop_column("flows", "metadata_json")
