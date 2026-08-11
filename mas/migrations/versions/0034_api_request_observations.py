"""Add bounded API request observations for operational read models."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_api_request_observations"
down_revision = "0033_usage_budget_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_request_observations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Numeric(14, 3), nullable=False),
        sa.Column("trace_id", sa.Text()),
        sa.Column("principal", sa.Text()),
        sa.Column("dashboard_section", sa.Text()),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.Text(), nullable=False, server_default="orchestrator_api"),
        sa.CheckConstraint(
            "status_code >= 100 AND status_code <= 599",
            name="ck_api_obs_status_code",
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure')",
            name="ck_api_obs_outcome",
        ),
    )
    op.create_index(
        "ix_api_obs_occurred_at",
        "api_request_observations",
        ["occurred_at"],
    )
    op.create_index(
        "ix_api_obs_trace_id",
        "api_request_observations",
        ["trace_id"],
    )
    op.create_index(
        "ix_api_obs_route_time",
        "api_request_observations",
        ["route", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_obs_route_time", table_name="api_request_observations")
    op.drop_index("ix_api_obs_trace_id", table_name="api_request_observations")
    op.drop_index("ix_api_obs_occurred_at", table_name="api_request_observations")
    op.drop_table("api_request_observations")
