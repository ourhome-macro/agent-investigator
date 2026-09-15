"""Add the investigation token budget ledger.

Revision ID: ci_0007
Revises: ci_0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "ci_0007"
down_revision = "ci_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if inspect(op.get_bind()).has_table("ci_budget_entries"):
        return
    op.create_table(
        "ci_budget_entries",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("investigation_id", sa.String(length=64), sa.ForeignKey("ci_investigations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_run_id", sa.String(length=64), sa.ForeignKey("ci_workflow_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("stage", sa.String(length=48), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("durable_batch_id", sa.String(length=128), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_ci_budget_idempotency"),
    )
    for name in ("investigation_id", "workflow_run_id", "stage", "task_id", "idempotency_key"):
        op.create_index(f"ix_ci_budget_entries_{name}", "ci_budget_entries", [name], unique=name == "idempotency_key")


def downgrade() -> None:
    op.drop_table("ci_budget_entries")
