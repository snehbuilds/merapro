"""Ensure mera_app role has LOGIN and correct password (ADR-0006).

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import make_url

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    db_url = os.environ.get("DATABASE_URL")
    password = (
        os.environ.get("MERA_APP_DB_PASSWORD")
        or (make_url(db_url).password if db_url else None)
        or "mera-dev-app"
    )
    safe_pw = password.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mera_app') THEN
            CREATE ROLE mera_app WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{safe_pw}';
          ELSE
            ALTER ROLE mera_app WITH LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{safe_pw}';
          END IF;
        END $$;
        """
    )
    op.execute("GRANT ALL PRIVILEGES ON DATABASE mera TO mera_app")
    op.execute("GRANT ALL ON SCHEMA public TO mera_app")
    op.execute("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO mera_app")
    op.execute("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO mera_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO mera_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO mera_app")


def downgrade() -> None:
    pass
