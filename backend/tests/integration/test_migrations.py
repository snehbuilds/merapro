"""Migrations round-trip: downgrade to base and back up must apply cleanly."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


def test_downgrade_then_upgrade_is_clean(postgres_urls: tuple[str, str]) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", postgres_urls[0])
    command.downgrade(cfg, "0001")
    command.upgrade(cfg, "head")


def test_0013_downgrade_keeps_every_audit_row(postgres_urls: tuple[str, str]) -> None:
    import psycopg

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", postgres_urls[0])
    command.upgrade(cfg, "head")
    libpq = postgres_urls[0].replace("postgresql+asyncpg://", "postgresql://")
    with psycopg.connect(libpq, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO audit_log (created_at, actor_type, surface, action) VALUES "
            "(date_trunc('month', now()) + interval '1 month 2 days', 'system', 'worker', "
            "'test.fold')"
        )
        before = conn.execute("SELECT count(*) FROM audit_log").fetchone()
    command.downgrade(cfg, "0012")
    with psycopg.connect(libpq, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone() == before
        where = conn.execute(
            "SELECT tableoid::regclass::text FROM audit_log WHERE action = 'test.fold'"
        ).fetchone()
        assert where == ("audit_log_default",)
        partitions = conn.execute(
            "SELECT count(*) FROM pg_inherits WHERE inhparent = 'audit_log'::regclass"
        ).fetchone()
        assert partitions == (1,)
    command.upgrade(cfg, "head")
    with psycopg.connect(libpq, autocommit=True) as conn:
        # Next month now has a row in the default partition, so it is skipped, not broken.
        assert conn.execute("SELECT count(*) FROM audit_log").fetchone() == before
        # Leave the session database as other tests expect it: remove the test row (as
        # the owner, past the append-only trigger) and recreate next month's partition.
        with conn.transaction():
            conn.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update")
            conn.execute("DELETE FROM audit_log WHERE action = 'test.fold'")
            conn.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update")
        assert conn.execute("SELECT mera_ensure_audit_partitions(2)").fetchone() == (1,)
