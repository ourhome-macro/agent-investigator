"""Separate stage-item row identity from the protocol task identity.

Revision ID: ci_0003
Revises: ci_0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "ci_0003"
down_revision = "ci_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("ci_stage_items")}
    if "task_id" not in columns:
        op.add_column("ci_stage_items", sa.Column("task_id", sa.String(length=128), nullable=True))
        op.execute(sa.text("UPDATE ci_stage_items SET task_id = id WHERE task_id IS NULL"))
    indexes = {index["name"] for index in inspect(bind).get_indexes("ci_stage_items")}
    if "ix_ci_stage_items_task_id" not in indexes:
        op.create_index("ix_ci_stage_items_task_id", "ci_stage_items", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_ci_stage_items_task_id", table_name="ci_stage_items")
    op.drop_column("ci_stage_items", "task_id")
