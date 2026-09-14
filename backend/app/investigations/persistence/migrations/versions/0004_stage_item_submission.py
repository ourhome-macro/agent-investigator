"""Persist validated domain submissions for crash-safe stage continuation.

Revision ID: ci_0004
Revises: ci_0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "ci_0004"
down_revision = "ci_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("ci_stage_items")}
    if "submission" not in columns:
        op.add_column("ci_stage_items", sa.Column("submission", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("ci_stage_items", "submission")
