"""Job queue schema: procrastinate's tables, functions and triggers (M1.7, ADR-0003).

The schema SQL ships inside the pinned procrastinate package. Upgrading procrastinate means
adding a migration that applies its bundled migration files (get_migrations_path()).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-07
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from procrastinate.schema import SchemaManager
from sqlalchemy.util import await_only

from mera.infrastructure.db.rls import APP_ROLE

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The file holds many statements and dollar-quoted function bodies, which asyncpg only
    # accepts through its simple-query path (no parameters), so call the driver directly
    # inside the migration's transaction.
    driver_conn = op.get_bind().connection.driver_connection
    await_only(driver_conn.execute(SchemaManager.get_schema()))
    for table in (
        "procrastinate_jobs",
        "procrastinate_events",
        "procrastinate_periodic_defers",
        "procrastinate_workers",
    ):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {APP_ROLE}")


def downgrade() -> None:
    # Drop every procrastinate_* object (tables, then functions, then types) so that
    # downgrade followed by upgrade re-applies cleanly.
    op.execute(
        r"""
        DO $$
        DECLARE r record;
        BEGIN
          FOR r IN SELECT tablename FROM pg_tables
                   WHERE schemaname = 'public' AND tablename LIKE 'procrastinate\_%' LOOP
            EXECUTE format('DROP TABLE IF EXISTS public.%I CASCADE', r.tablename);
          END LOOP;
          FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
                   JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname = 'public' AND p.proname LIKE 'procrastinate\_%' LOOP
            EXECUTE format('DROP FUNCTION IF EXISTS %s CASCADE', r.sig);
          END LOOP;
          FOR r IN SELECT t.typname FROM pg_type t
                   JOIN pg_namespace n ON n.oid = t.typnamespace
                   WHERE n.nspname = 'public' AND t.typname LIKE 'procrastinate\_%'
                     AND t.typtype IN ('e', 'c', 'd') LOOP
            EXECUTE format('DROP TYPE IF EXISTS public.%I CASCADE', r.typname);
          END LOOP;
        END $$;
        """
    )
