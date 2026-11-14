"""Helpers migrations use to put a tenant table under RLS (ADR-0006, docs/03 §11).

Every tenant table gets ENABLE **and** FORCE: without FORCE the table owner bypasses its
own policies. The policy reads the transaction-local ``mera.workspace_id``; when it is
unset the NULLIF makes the predicate NULL, so the query sees zero rows instead of erroring
or seeing everything.
"""

from __future__ import annotations

from alembic import op

APP_ROLE = "mera_app"
_TENANT = "NULLIF(current_setting('mera.workspace_id', true), '')::uuid"
# Authenticated user, set only by user_transaction(); widens reads of identity rows only
# (ADR-0006 addendum).
USER_SETTING_SQL = "NULLIF(current_setting('mera.user_id', true), '')::uuid"


def enable_rls(table: str, *, key: str = "workspace_id") -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY tenant_isolation ON "{table}" '
        f"USING ({key} = {_TENANT}) WITH CHECK ({key} = {_TENANT})"
    )


def disable_rls(table: str) -> None:
    op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
    op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
