from __future__ import annotations

from alembic import context

from app.investigations.persistence.models import InvestigationBase

config = context.config
target_metadata = InvestigationBase.metadata


def run_migrations() -> None:
    connection = config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("Competitive Research migrations require the Gateway database connection")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="alembic_version_ci",
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations()
