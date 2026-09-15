"""Durable investigation token reservations. Revision ci_0010."""

import sqlalchemy as sa
from alembic import op

revision = "ci_0010"
down_revision = "ci_0009"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if not sa.inspect(connection).has_table("ci_claim_submissions"):
        op.create_table("ci_claim_submissions", sa.Column("id", sa.String(64), primary_key=True), sa.Column("claim_id", sa.String(64), sa.ForeignKey("ci_claims.id", ondelete="CASCADE"), nullable=False))
        op.create_index("ix_ci_claim_submissions_claim_id", "ci_claim_submissions", ["claim_id"])
    if not sa.inspect(connection).has_table("ci_candidates"):
        op.create_table(
            "ci_candidates",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("investigation_id", sa.String(64), sa.ForeignKey("ci_investigations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
        )
        op.create_index("ix_ci_candidates_investigation_id", "ci_candidates", ["investigation_id"])
    if "token_reserved" not in {column["name"] for column in sa.inspect(connection).get_columns("ci_investigations")}:
        op.add_column("ci_investigations", sa.Column("token_reserved", sa.Integer(), nullable=False, server_default="0"))
    if not sa.inspect(connection).has_table("ci_budget_reservations"):
        op.create_table(
            "ci_budget_reservations",
            sa.Column("id", sa.String(200), primary_key=True),
            sa.Column("investigation_id", sa.String(64), sa.ForeignKey("ci_investigations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("stage", sa.String(48), nullable=False),
            sa.Column("tokens", sa.Integer(), nullable=False),
            sa.Column("actual_tokens", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_ci_budget_reservations_investigation_id", "ci_budget_reservations", ["investigation_id"])


def downgrade():
    op.drop_table("ci_claim_submissions")
    op.drop_table("ci_candidates")
    op.drop_table("ci_budget_reservations")
    op.drop_column("ci_investigations", "token_reserved")
