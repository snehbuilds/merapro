"""Alembic environment: async, runs as the owner role (never the RLS-bound app role)."""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from mera.infrastructure.db import models  # noqa: F401  (register metadata)
from mera.infrastructure.db.base import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    # An explicit URL (tests) wins; otherwise the migration URL from the environment.
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("DATABASE_MIGRATION_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise RuntimeError("Set DATABASE_MIGRATION_URL (owner role) to run migrations")
    return url


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
