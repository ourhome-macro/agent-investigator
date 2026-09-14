"""Add idempotency keys for replay-safe Claim submissions.

Revision ID: ci_0005
Revises: ci_0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "ci_0005"
down_revision = "ci_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("ci_claims")}
    if "idempotency_key" not in columns:
        op.add_column("ci_claims", sa.Column("idempotency_key", sa.String(length=180), nullable=True))
    indexes = {index["name"] for index in inspect(bind).get_indexes("ci_claims")}
    if "ix_ci_claims_idempotency_key" not in indexes:
        op.create_index("ix_ci_claims_idempotency_key", "ci_claims", ["idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_ci_claims_idempotency_key", table_name="ci_claims")
    op.drop_column("ci_claims", "idempotency_key")
