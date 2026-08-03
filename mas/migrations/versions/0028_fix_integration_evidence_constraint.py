"""Repair the idempotency constraint name used by evidence storage.

Some 0024 deployments created the unnamed SQLAlchemy constraint as
``integration_evidence_records_idempotency_key_key`` while runtime storage
targets the explicit ORM name ``uq_integration_evidence_idempotency``.
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_fix_integration_evidence_constraint"
down_revision = "0027_pm_actor_mappings_inbound_canary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    constraints = sa.inspect(bind).get_unique_constraints("integration_evidence_records")
    names = {item.get("name") for item in constraints}
    if "uq_integration_evidence_idempotency" not in names:
        old = "integration_evidence_records_idempotency_key_key"
        if old in names:
            op.drop_constraint(old, "integration_evidence_records", type_="unique")
        op.create_unique_constraint(
            "uq_integration_evidence_idempotency",
            "integration_evidence_records",
            ["idempotency_key"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    constraints = sa.inspect(bind).get_unique_constraints("integration_evidence_records")
    names = {item.get("name") for item in constraints}
    if "uq_integration_evidence_idempotency" in names:
        op.drop_constraint("uq_integration_evidence_idempotency", "integration_evidence_records", type_="unique")
    if "integration_evidence_records_idempotency_key_key" not in names:
        op.create_unique_constraint(
            "integration_evidence_records_idempotency_key_key",
            "integration_evidence_records",
            ["idempotency_key"],
        )
