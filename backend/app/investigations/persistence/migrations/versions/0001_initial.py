"""Create Competitive Research V1 schema.

Revision ID: ci_0001
"""

from __future__ import annotations

from alembic import op

from app.investigations.persistence.models import InvestigationBase

revision = "ci_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    InvestigationBase.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    InvestigationBase.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
