"""Add first-class AIAT company manifests and assignments."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0031_company_control_plane"
down_revision = "0030_pm_canary_expiry_attribution"
branch_labels = None
depends_on = None

DEFAULT_COMPANY_ID = "00000000-0000-4000-8000-000000000001"


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column("active_manifest_version_id", sa.UUID()),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'ARCHIVED')", name="ck_companies_status"),
    )
    op.create_table(
        "company_manifest_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("company_id", sa.UUID(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("manifest_version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("manifest_json", JSONB(), nullable=False),
        sa.Column("compiler_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="APPLIED"),
        sa.Column("compiled_by", sa.Text(), nullable=False),
        sa.Column("compiled_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("company_id", "manifest_version", name="uq_company_manifest_version"),
        sa.UniqueConstraint("company_id", "digest", name="uq_company_manifest_digest"),
        sa.CheckConstraint("status IN ('VALIDATED', 'APPLIED', 'ROLLED_BACK', 'FAILED')", name="ck_company_manifest_status"),
    )
    op.create_table(
        "company_departments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("company_id", sa.UUID(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("department_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("chief_worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="SET NULL")),
        sa.Column("approval_policy", JSONB(), nullable=False, server_default="{}"),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("company_id", "department_key", name="uq_company_department_key"),
    )
    op.create_foreign_key(
        "fk_companies_active_manifest",
        "companies",
        "company_manifest_versions",
        ["active_manifest_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "company_worker_assignments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("company_id", sa.UUID(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("worker_id", sa.UUID(), sa.ForeignKey("worker_registry.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("department_id", sa.UUID(), sa.ForeignKey("company_departments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("manifest_version_id", sa.UUID(), sa.ForeignKey("company_manifest_versions.id", ondelete="RESTRICT")),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column("tool_grants", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("permission_grants", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("model_profile_id", sa.Text()),
        sa.Column("budget", JSONB(), nullable=False, server_default="{}"),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("company_id", "worker_id", name="uq_company_worker_assignment"),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE', 'DRAINING')", name="ck_company_worker_assignment_status"),
    )
    op.create_table(
        "company_budgets",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("company_id", sa.UUID(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("budget_key", sa.Text(), nullable=False),
        sa.Column("limit_value", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("period", sa.Text(), nullable=False, server_default="lifetime"),
        sa.Column("metadata", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("company_id", "budget_key", name="uq_company_budget_key"),
        sa.CheckConstraint("limit_value >= 0", name="ck_company_budget_limit"),
    )
    op.execute(
        sa.text(
            "INSERT INTO companies (id, slug, name, description, created_by) "
            "VALUES (:id, 'aiat-default', 'AIAT Default Software Company', "
            "'Bootstrap company for existing AIAT installations', 'migration')"
        ).bindparams(id=DEFAULT_COMPANY_ID)
    )
    op.add_column(
        "projects",
        sa.Column("company_id", sa.UUID(), nullable=False, server_default=sa.text(f"'{DEFAULT_COMPANY_ID}'::uuid")),
    )
    op.create_foreign_key("fk_projects_company", "projects", "companies", ["company_id"], ["id"], ondelete="RESTRICT")
    op.create_index("ix_projects_company_state", "projects", ["company_id", "state"])


def downgrade() -> None:
    op.drop_index("ix_projects_company_state", table_name="projects")
    op.drop_constraint("fk_projects_company", "projects", type_="foreignkey")
    op.drop_column("projects", "company_id")
    op.drop_constraint("fk_companies_active_manifest", "companies", type_="foreignkey")
    op.drop_table("company_budgets")
    op.drop_table("company_worker_assignments")
    op.drop_table("company_departments")
    op.drop_table("company_manifest_versions")
    op.drop_table("companies")
