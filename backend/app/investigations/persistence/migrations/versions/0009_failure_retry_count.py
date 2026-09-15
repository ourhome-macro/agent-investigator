"""Separate technical workflow retries from business rework rounds.

Revision ID: ci_0009
Revises: ci_0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "ci_0009"
down_revision = "ci_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("ci_investigations")}
    if "failure_retry_count" not in columns:
        op.add_column(
            "ci_investigations",
            sa.Column("failure_retry_count", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("ci_investigations", "failure_retry_count")
