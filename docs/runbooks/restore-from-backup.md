# Runbook: backup and restore

PostgreSQL holds everything (data, vectors, the job queue, the event log). The only other
state is the media volume. A backup is a `pg_dump` plus the media directory
(ADR-0002).

## Back up

```bash
docker compose exec -T postgres pg_dump -U mera -d mera -Fc > mera-$(date +%F).dump
docker run --rm -v mera_media:/media -v "$PWD":/out alpine tar czf /out/media-$(date +%F).tgz -C /media .
```

Keep both files together; a dump without its media points at images that are gone.

## Restore (drill)

1. Stop writers: `docker compose stop api worker bots web`.
2. Restore the database into an empty one (roles come from `infra/postgres/init`):

   ```bash
   docker compose exec -T postgres dropdb -U mera mera
   docker compose exec -T postgres createdb -U mera mera
   docker compose exec -T postgres pg_restore -U mera -d mera --no-owner --role=mera < mera-YYYY-MM-DD.dump
   ```

3. Restore media into the volume:

   ```bash
   docker run --rm -v mera_media:/media -v "$PWD":/in alpine sh -c "rm -rf /media/* && tar xzf /in/media-YYYY-MM-DD.tgz -C /media"
   ```

4. `docker compose up -d migrate` (a no-op at the same revision), then start the rest.

## Re-verify afterwards

- `/readyz` reports the expected migration revision.
- Row-level security is on every tenant table: `make test-integration -k rls` against a
  scratch database, or check `pg_class.relforcerowsecurity` on the restored one.
- A known published post's media opens in the Composer.
- The approval queue shows the same open requests. **Approval tokens** in the dump are
  still single use: a restore cannot revive a used button.
- Scheduled posts whose time passed while down publish on the next dispatcher minute if
  their approvals are still inside the validity window; older ones dead-letter with
  `approval_stale` and need approving again.

The master key (`MERA_MASTER_KEY`) is **not** in the backup. Without the same key, account
credentials cannot be decrypted and every account must be reconnected.

## Drill log

| Date (PT) | Source | Result |
| --- | --- | --- |
| 2026-11-10 | Sandbox verify stack at revision 0013 (2 workspaces, 25 post sets, 179 audit rows, monthly audit partitions) | ✅ `pg_dump -Fc` 2 s, 540 KB; `pg_restore --no-owner --role=mera --exit-on-error` into an empty database (roles from `infra/postgres/init`) 15 s. Revision, row counts, 45 tables with forced RLS, 59 policies, the audit partitions and their revoked grants all match the source. As `mera_app`: 0 rows with no tenant set, the workspace's rows with it. `python -m mera.preflight` database probe: RLS-bound role, at head. Media volume not part of this drill (local filesystem store) |

Repeat the drill after any migration that adds tables, policies or functions, and at least
once a quarter against a copy of production.
