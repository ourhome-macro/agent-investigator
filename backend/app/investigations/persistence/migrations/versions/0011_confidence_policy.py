"""Source-aware claim confidence and approved research requirements."""

import sqlalchemy as sa
from alembic import op

revision = "ci_0011"
down_revision = "ci_0010"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for table, name, kind, default in (
        ("ci_scopes", "required_dimensions", sa.JSON(), "[]"),
        ("ci_competitors", "official_repositories", sa.JSON(), "[]"),
        ("ci_claims", "support_basis", sa.String(32), "unverified"),
    ):
        if name not in {column["name"] for column in sa.inspect(connection).get_columns(table)}:
            op.add_column(table, sa.Column(name, kind, nullable=False, server_default=default))
    op.execute("UPDATE ci_claims SET support_basis = CASE WHEN independent_source_count >= 2 THEN 'corroborated' ELSE 'legacy_unverified' END WHERE status = 'supported' AND support_basis = 'unverified'")


def downgrade():
    op.drop_column("ci_claims", "support_basis")
    op.drop_column("ci_competitors", "official_repositories")
    op.drop_column("ci_scopes", "required_dimensions")
