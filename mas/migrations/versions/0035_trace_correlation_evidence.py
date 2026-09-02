"""Add direct trace correlation to model, artifact, and integration evidence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_trace_correlation_evidence"
down_revision = "0034_api_request_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in (
        "worker_usage_records",
        "worker_artifacts",
        "integration_evidence_records",
    ):
        op.add_column(table_name, sa.Column("trace_id", sa.Text()))
        op.add_column(table_name, sa.Column("span_id", sa.Text()))

    op.create_index(
        "ix_worker_usage_trace_id",
        "worker_usage_records",
        ["trace_id"],
    )
    op.create_index(
        "ix_worker_artifacts_trace_id",
        "worker_artifacts",
        ["trace_id"],
    )
    op.create_index(
        "ix_integration_evidence_trace_id",
        "integration_evidence_records",
        ["trace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integration_evidence_trace_id",
        table_name="integration_evidence_records",
    )
    op.drop_index("ix_worker_artifacts_trace_id", table_name="worker_artifacts")
    op.drop_index("ix_worker_usage_trace_id", table_name="worker_usage_records")
    for table_name in (
        "integration_evidence_records",
        "worker_artifacts",
        "worker_usage_records",
    ):
        op.drop_column(table_name, "span_id")
        op.drop_column(table_name, "trace_id")
