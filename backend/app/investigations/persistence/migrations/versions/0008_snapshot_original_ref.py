"""Track uploaded originals alongside extracted Evidence snapshots.

Revision ID: ci_0008
Revises: ci_0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "ci_0008"
down_revision = "ci_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("ci_evidence_snapshots")}
    if "original_ref" not in columns:
        op.add_column("ci_evidence_snapshots", sa.Column("original_ref", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ci_evidence_snapshots", "original_ref")
