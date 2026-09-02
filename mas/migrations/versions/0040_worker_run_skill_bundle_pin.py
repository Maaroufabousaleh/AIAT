"""Persist the immutable skill bundle selected by each worker run."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040_worker_run_skill_bundle_pin"
down_revision = "0039_worker_host_fencing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Legacy runs remain readable with a NULL pin.  New durable run creation
    # snapshots the worker's active bundle (when one is configured), so mutable
    # registry pointers cannot rewrite an in-flight run's execution contract.
    op.add_column("worker_runs", sa.Column("skill_bundle_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_worker_runs_skill_bundle",
        "worker_runs",
        "skill_bundles",
        ["skill_bundle_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_worker_runs_worker_skill_bundle_state",
        "worker_runs",
        ["worker_id", "skill_bundle_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_runs_worker_skill_bundle_state", table_name="worker_runs")
    op.drop_constraint("fk_worker_runs_skill_bundle", "worker_runs", type_="foreignkey")
    op.drop_column("worker_runs", "skill_bundle_id")
