"""Add task_id and department_id to flow_instances for flexible assignment.

Revision ID: 0006_flow_instance_targets
Revises: 0005_flow_execution_config
Create Date: 2026-04-04 01:00:00.000000

Tables modified
--------------
1. flow_instances  — add task_id (UUID, nullable), department_id (UUID, nullable)

Design notes
------------
- task_id and department_id allow flows to be assigned to tasks or departments
  in addition to projects (project_id remains non-nullable for backward compat)
- No foreign key constraints are added because the tasks and departments tables
  do not yet exist in the schema
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006_flow_instance_targets"
down_revision = "0005_flow_execution_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "flow_instances",
        sa.Column(
            "task_id",
            sa.UUID(),
            nullable=True,
            comment="Optional task this flow instance is assigned to",
        ),
    )
    op.add_column(
        "flow_instances",
        sa.Column(
            "department_id",
            sa.UUID(),
            nullable=True,
            comment="Optional department this flow instance is assigned to",
        ),
    )


def downgrade() -> None:
    op.drop_column("flow_instances", "department_id")
    op.drop_column("flow_instances", "task_id")
