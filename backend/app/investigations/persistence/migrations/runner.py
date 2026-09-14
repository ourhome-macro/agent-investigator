from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

_MIGRATIONS_DIR = Path(__file__).resolve().parent
_MIGRATION_LOCK = asyncio.Lock()


async def upgrade_investigation_schema(engine: AsyncEngine) -> None:
    """Upgrade the independent CI schema after DeerFlow core migrations."""

    def upgrade(sync_connection) -> None:
        config = Config()
        config.set_main_option("script_location", str(_MIGRATIONS_DIR))
        config.attributes["connection"] = sync_connection
        command.upgrade(config, "head")

    async with _MIGRATION_LOCK:
        async with engine.begin() as connection:
            await connection.run_sync(upgrade)
