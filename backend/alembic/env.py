"""Alembic environment: async, runs as the owner role (never the RLS-bound app role)."""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import text
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
    app_pw = os.environ.get("MERA_APP_DB_PASSWORD")
    if app_pw:
        async with engine.begin() as conn:
            safe_pw = app_pw.replace("'", "''")
            await conn.execute(
                text(
                    f"""
                    DO $$
                    BEGIN
                      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mera_app') THEN
                        CREATE ROLE mera_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{safe_pw}';
                      ELSE
                        ALTER ROLE mera_app WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{safe_pw}';
                      END IF;
                    END $$;
                    """
                )
            )
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
