"""Add idempotent cost events and hierarchical budget reservations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0033_usage_budget_ledger"
down_revision = "0032_worker_run_queue_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in (
        sa.Column("company_id", sa.UUID(), sa.ForeignKey("companies.id", ondelete="RESTRICT")),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="SET NULL")),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="SET NULL")),
        sa.Column("provider_id", sa.Text()),
        sa.Column("billing_code", sa.Text()),
        sa.Column("pricing_snapshot", JSONB()),
        sa.Column("resource_json", JSONB()),
        sa.Column("idempotency_key", sa.Text()),
    ):
        op.add_column("project_usage_events", column)
    op.create_index(
        "uq_project_usage_idempotency_nonnull",
        "project_usage_events",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index("ix_project_usage_company_time", "project_usage_events", ["company_id", "occurred_at"])
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("company_id", sa.UUID(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.UUID(), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="SET NULL")),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("worker_runs.id", ondelete="SET NULL")),
        sa.Column("budget_key", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("state", sa.Text(), nullable=False, server_default="RESERVED"),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("committed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True)),
        sa.CheckConstraint("amount >= 0", name="ck_budget_reservation_amount"),
        sa.CheckConstraint("state IN ('RESERVED', 'COMMITTED', 'RELEASED')", name="ck_budget_reservation_state"),
    )


def downgrade() -> None:
    op.drop_table("budget_reservations")
    op.drop_index("ix_project_usage_company_time", table_name="project_usage_events")
    op.drop_index("uq_project_usage_idempotency_nonnull", table_name="project_usage_events")
    for name in ("idempotency_key", "resource_json", "pricing_snapshot", "billing_code", "provider_id", "worker_id", "run_id", "company_id"):
        op.drop_column("project_usage_events", name)
