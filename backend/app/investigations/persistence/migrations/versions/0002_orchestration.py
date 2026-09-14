"""Add persistent orchestration items.

Revision ID: ci_0002
Revises: ci_0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "ci_0002"
down_revision = "ci_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("ci_stage_items"):
        return
    op.create_table(
        "ci_stage_items",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=64), sa.ForeignKey("ci_workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage_attempt_id", sa.String(length=64), sa.ForeignKey("ci_stage_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(length=48), nullable=False),
        sa.Column("item_key", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("task_envelope", sa.JSON(), nullable=False),
        sa.Column("submission", sa.JSON(), nullable=True),
        sa.Column("receipt", sa.JSON(), nullable=True),
        sa.Column("durable_batch_id", sa.String(length=128), nullable=True),
        sa.Column("durable_batch_item_id", sa.String(length=128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("stage_attempt_id", "item_key", name="uq_ci_stage_item_key"),
    )
    op.create_index("ix_ci_stage_items_workflow_run_id", "ci_stage_items", ["workflow_run_id"])
    op.create_index("ix_ci_stage_items_task_id", "ci_stage_items", ["task_id"])
    op.create_index("ix_ci_stage_items_stage_attempt_id", "ci_stage_items", ["stage_attempt_id"])
    op.create_index("ix_ci_stage_items_stage", "ci_stage_items", ["stage"])
    op.create_index("ix_ci_stage_items_status", "ci_stage_items", ["status"])
    op.create_index("ix_ci_stage_items_durable_batch_id", "ci_stage_items", ["durable_batch_id"])
    op.create_index("ix_ci_stage_item_workflow_stage", "ci_stage_items", ["workflow_run_id", "stage", "status"])


def downgrade() -> None:
    op.drop_table("ci_stage_items")
