"""Research modes, decision context and version-bound annotation requests."""

import sqlalchemy as sa
from alembic import op

revision = "ci_0012"
down_revision = "ci_0011"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    fields = (
        ("ci_investigations", "research_mode", sa.String(24), "standard", False),
        ("ci_investigations", "policy_snapshot", sa.JSON(), "{}", False),
        ("ci_investigations", "active_request_id", sa.String(64), None, True),
        ("ci_scopes", "decision_context", sa.JSON(), "{}", False),
        ("ci_claims", "statement", sa.JSON(), "{}", False),
    )
    for table, name, kind, default, nullable in fields:
        if name not in {column["name"] for column in sa.inspect(connection).get_columns(table)}:
            op.add_column(table, sa.Column(name, kind, nullable=nullable, server_default=default))
    if not sa.inspect(connection).has_table("ci_research_requests"):
        op.create_table(
            "ci_research_requests",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("investigation_id", sa.String(64), sa.ForeignKey("ci_investigations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_report_id", sa.String(64), sa.ForeignKey("ci_reports.id"), nullable=False),
            sa.Column("result_report_id", sa.String(64), sa.ForeignKey("ci_reports.id"), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("target", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("token_allowance", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_ci_research_requests_investigation_id", "ci_research_requests", ["investigation_id"])


def downgrade():
    op.drop_table("ci_research_requests")
    for table, name in (("ci_claims", "statement"), ("ci_scopes", "decision_context"), ("ci_investigations", "active_request_id"), ("ci_investigations", "policy_snapshot"), ("ci_investigations", "research_mode")):
        op.drop_column(table, name)
