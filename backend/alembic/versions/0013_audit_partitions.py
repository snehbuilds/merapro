"""Monthly audit_log partitions, created ahead with their tenancy rules (P2-M0.9).

Since 0003 every audit row has gone to ``audit_log_default``. Monthly partitions keep
the table cheap to scan by date and let old months be archived whole (docs/09 §10).

A partition queried directly does not inherit its parent's policies, so each new
partition gets the same RLS policies as the parent and loses the default DML grant the
app role would otherwise receive. Creating tables needs the owner, so the work lives in
an owner-owned ``SECURITY DEFINER`` function the app role may only execute; it takes a
single bounded integer and builds every identifier itself.

Months that already have rows in the default partition are skipped: a new partition may
not overlap rows the default holds, and moving them would rewrite an append-only log.
Those rows simply stay in the default partition.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from mera.infrastructure.db.rls import APP_ROLE

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT = "NULLIF(current_setting(''mera.workspace_id'', true), '''')::uuid"

FUNCTION = f"""
CREATE FUNCTION mera_ensure_audit_partitions(months_ahead integer DEFAULT 2)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  month_start date;
  lower_ts timestamptz;
  upper_ts timestamptz;
  part text;
  created integer := 0;
BEGIN
  IF months_ahead IS NULL OR months_ahead < 0 OR months_ahead > 12 THEN
    RAISE EXCEPTION 'months_ahead must be between 0 and 12';
  END IF;
  -- One caller at a time; a second waits and then finds the work done.
  PERFORM pg_advisory_xact_lock(hashtext('mera_ensure_audit_partitions'));
  FOR i IN 0..months_ahead LOOP
    month_start := (date_trunc('month', now() AT TIME ZONE 'UTC')
                    + make_interval(months => i))::date;
    part := format('audit_log_y%sm%s', to_char(month_start, 'YYYY'), to_char(month_start, 'MM'));
    CONTINUE WHEN to_regclass('public.' || part) IS NOT NULL;
    lower_ts := month_start::timestamp AT TIME ZONE 'UTC';
    upper_ts := (month_start + interval '1 month')::timestamp AT TIME ZONE 'UTC';
    CONTINUE WHEN EXISTS (
      SELECT 1 FROM public.audit_log_default
      WHERE created_at >= lower_ts AND created_at < upper_ts
    );
    -- All or nothing per month: a partition never exists without its policies. If the
    -- default partition holds rows for the month after all (the check above cannot see
    -- them when the owner is subject to RLS), Postgres refuses and the month is skipped.
    BEGIN
      EXECUTE format(
        'CREATE TABLE public.%I PARTITION OF public.audit_log FOR VALUES FROM (%L) TO (%L)',
        part, lower_ts, upper_ts);
      EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', part);
      EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', part);
      EXECUTE format(
        'CREATE POLICY tenant_read ON public.%I FOR SELECT USING (workspace_id = {_TENANT})',
        part);
      EXECUTE format(
        'CREATE POLICY tenant_append ON public.%I FOR INSERT '
        'WITH CHECK (workspace_id IS NULL OR workspace_id = {_TENANT})',
        part);
      -- Reads and appends go through the parent, whose grants apply; nothing direct.
      EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, {APP_ROLE}', part);
      created := created + 1;
    EXCEPTION WHEN check_violation THEN
      CONTINUE;
    END;
  END LOOP;
  RETURN created;
END $$
"""


def upgrade() -> None:
    op.execute(FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION mera_ensure_audit_partitions(integer) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mera_ensure_audit_partitions(integer) TO {APP_ROLE}")
    op.execute("SELECT mera_ensure_audit_partitions(2)")


def downgrade() -> None:
    # Detach and fold each monthly partition back into the default one, so no audit row
    # is lost. The append-only trigger blocks DELETE, so rows are copied, not moved.
    op.execute(
        """
        DO $$
        DECLARE
          part text;
        BEGIN
          FOR part IN
            SELECT c.relname FROM pg_inherits i
            JOIN pg_class c ON c.oid = i.inhrelid
            JOIN pg_class p ON p.oid = i.inhparent
            WHERE p.relname = 'audit_log' AND c.relname <> 'audit_log_default'
          LOOP
            EXECUTE format('ALTER TABLE public.audit_log DETACH PARTITION public.%I', part);
            EXECUTE format('INSERT INTO public.audit_log SELECT * FROM public.%I', part);
            EXECUTE format('DROP TABLE public.%I', part);
          END LOOP;
        END $$
        """
    )
    op.execute("DROP FUNCTION IF EXISTS mera_ensure_audit_partitions(integer)")
