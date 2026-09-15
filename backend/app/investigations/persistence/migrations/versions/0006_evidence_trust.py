"""Add immutable snapshots, chunks, quote provenance, and pricing observations.

Revision ID: ci_0006
Revises: ci_0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "ci_0006"
down_revision = "ci_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("ci_evidence_snapshots"):
        op.create_table(
            "ci_evidence_snapshots",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("investigation_id", sa.String(length=64), sa.ForeignKey("ci_investigations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("content_text", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("mime_type", sa.String(length=128), nullable=False),
            sa.Column("extraction_method", sa.String(length=64), nullable=False),
            sa.Column("language", sa.String(length=16), nullable=False),
            sa.Column("object_ref", sa.Text(), nullable=True),
            sa.Column("original_ref", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("investigation_id", "content_hash", name="uq_ci_snapshot_content"),
        )
        op.create_index("ix_ci_evidence_snapshots_investigation_id", "ci_evidence_snapshots", ["investigation_id"])
        op.create_index("ix_ci_evidence_snapshots_content_hash", "ci_evidence_snapshots", ["content_hash"])
        op.create_index("ix_ci_evidence_snapshots_extraction_method", "ci_evidence_snapshots", ["extraction_method"])

    evidence_columns = {column["name"] for column in inspect(bind).get_columns("ci_evidence")}
    if "snapshot_id" not in evidence_columns:
        op.add_column("ci_evidence", sa.Column("snapshot_id", sa.String(length=64), nullable=True))
        op.create_index("ix_ci_evidence_snapshot_id", "ci_evidence", ["snapshot_id"])
        with op.batch_alter_table("ci_evidence") as batch:
            batch.create_foreign_key("fk_ci_evidence_snapshot", "ci_evidence_snapshots", ["snapshot_id"], ["id"], ondelete="RESTRICT")

    if not inspect(bind).has_table("ci_evidence_chunks"):
        op.create_table(
            "ci_evidence_chunks",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("snapshot_id", sa.String(length=64), sa.ForeignKey("ci_evidence_snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("char_start", sa.Integer(), nullable=False),
            sa.Column("char_end", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("token_estimate", sa.Integer(), nullable=False),
            sa.Column("embedding", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("snapshot_id", "ordinal", name="uq_ci_snapshot_chunk_ordinal"),
        )
        op.create_index("ix_ci_evidence_chunks_snapshot_id", "ci_evidence_chunks", ["snapshot_id"])
        op.create_index("ix_ci_evidence_chunks_content_hash", "ci_evidence_chunks", ["content_hash"])

    claim_link_columns = {column["name"] for column in inspect(bind).get_columns("ci_claim_evidence")}
    for name, column_type in (
        ("quote_start", sa.Integer()),
        ("quote_end", sa.Integer()),
        ("snapshot_sha256", sa.String(length=64)),
        ("validation_status", sa.String(length=24)),
        ("entailment_status", sa.String(length=24)),
    ):
        if name not in claim_link_columns:
            op.add_column("ci_claim_evidence", sa.Column(name, column_type, nullable=True))

    if not inspect(bind).has_table("ci_price_observations"):
        op.create_table(
            "ci_price_observations",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("investigation_id", sa.String(length=64), sa.ForeignKey("ci_investigations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("claim_id", sa.String(length=64), sa.ForeignKey("ci_claims.id", ondelete="CASCADE"), nullable=False),
            sa.Column("evidence_id", sa.String(length=64), sa.ForeignKey("ci_evidence.id", ondelete="CASCADE"), nullable=False),
            sa.Column("plan_name", sa.String(length=200), nullable=False),
            sa.Column("amount", sa.String(length=64), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False),
            sa.Column("billing_period", sa.String(length=16), nullable=False),
            sa.Column("billing_unit", sa.String(length=128), nullable=False),
            sa.Column("seat_minimum", sa.Integer(), nullable=True),
            sa.Column("region", sa.String(length=128), nullable=True),
            sa.Column("tax_included", sa.Boolean(), nullable=True),
            sa.Column("promotion", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("official", sa.Boolean(), nullable=False),
            sa.Column("verbatim_quote", sa.Text(), nullable=False),
            sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_ci_price_observations_investigation_id", "ci_price_observations", ["investigation_id"])
        op.create_index("ix_ci_price_observations_claim_id", "ci_price_observations", ["claim_id"])
        op.create_index("ix_ci_price_observations_evidence_id", "ci_price_observations", ["evidence_id"])
        op.create_index("ix_ci_price_observations_currency", "ci_price_observations", ["currency"])
        op.create_index("ix_ci_price_observations_billing_period", "ci_price_observations", ["billing_period"])


def downgrade() -> None:
    op.drop_table("ci_price_observations")
    for column in ("entailment_status", "validation_status", "snapshot_sha256", "quote_end", "quote_start"):
        op.drop_column("ci_claim_evidence", column)
    with op.batch_alter_table("ci_evidence") as batch:
        batch.drop_constraint("fk_ci_evidence_snapshot", type_="foreignkey")
    op.drop_index("ix_ci_evidence_snapshot_id", table_name="ci_evidence")
    op.drop_column("ci_evidence", "snapshot_id")
    op.drop_table("ci_evidence_chunks")
    op.drop_table("ci_evidence_snapshots")
