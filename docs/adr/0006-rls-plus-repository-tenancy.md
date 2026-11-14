# ADR-0006: Two independent tenancy layers, RLS and repository scoping

**Status:** Accepted
**Date:** 2026-11-08

## Context

Mera is multi-tenant by workspace. Even in the near-term posture of one owner running it
for themselves, an agency workspace holding several client brands means a cross-tenant
leak would expose one client's unpublished strategy to another.

The usual approach is to filter by tenant in the data access layer and trust it. That
works until one query forgets. A forgotten filter produces no error, no test failure
unless someone wrote exactly the right test, and no symptom until the wrong data appears
on a screen.

## Decision

Two layers that fail independently.

**Layer 1 — repository scoping.** Every repository method takes a `WorkspaceContext` and
injects `workspace_id` into the `WHERE` clause. A base class provides this. A test fixture
enumerates repository methods and fails CI if any lacks a cross-tenant test.

**Layer 2 — PostgreSQL row-level security.**

```sql
ALTER TABLE content_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_item FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON content_item
  USING (workspace_id = current_setting('mera.workspace_id')::uuid);
```

The application connects as a **non-superuser** role and sets `mera.workspace_id` as a
transaction-local setting at the start of every request and every job.

Supporting decision: **`workspace_id` is denormalized onto every tenant-scoped table**,
even where it is reachable through a parent.

## Consequences

**Good**

- A query missing its tenant filter returns zero rows instead of another tenant's data.
  The failure mode changes from silent leak to visible emptiness, which is the whole
  point.
- RLS is enforced by the database, so it covers raw SQL, migrations run against the app
  role, an ORM escape hatch, and any future service that connects with the same role.
- Layer 2 limits the blast radius of a SQL injection: injected SQL still runs under the
  policy.
- The two layers are genuinely independent. Layer 1 is Python, layer 2 is Postgres; a bug
  in one is very unlikely to also be present in the other.
- Denormalized `workspace_id` keeps every policy a single index-backed predicate.

**Bad**

- **`FORCE ROW LEVEL SECURITY` is required and easy to omit.** Without it, the table owner
  bypasses its own policies, which silently defeats the mechanism in exactly the
  environment where you set it up. A migration test asserts both `ENABLE` and `FORCE` on
  every tenant-scoped table.
- **The session variable must always be set.** A job that forgets sees nothing, which is a
  loud and quickly-found failure, but still a failure. The session factory sets it; there
  is no path that opens a transaction without it.
- **Denormalization must be kept consistent.** A trigger verifies that a child's
  `workspace_id` matches its parent's on insert.
- **16 bytes per row** of storage overhead. Irrelevant at this scale.
- **Cross-workspace operations need an explicit path.** An agency owner listing work
  across clients iterates workspaces with the context set per iteration. There is
  deliberately no superuser query path, which makes some reporting queries more verbose
  than they would otherwise be.
- **Connection pooling interaction.** `SET LOCAL` is transaction-scoped, so a pooled
  connection cannot leak the setting between transactions. This is why it is `SET LOCAL`
  and not `SET`, and the distinction matters enough to call out.

## Alternatives considered

**Repository filtering alone.** Standard and sufficient until the first mistake. The
consequence of that mistake is severe enough to justify a second layer.

**RLS alone.** Would work, but every query would depend on a session variable being set
correctly with no application-level expression of intent. Repository scoping also produces
better query plans, because the planner sees the predicate directly rather than through a
policy.

**Schema per tenant.** Strong isolation, and it collapses under cross-tenant queries,
migration fan-out across hundreds of schemas, and connection pool fragmentation. Wrong
shape for a product where an agency routinely looks across clients.

**Database per tenant.** Strongest isolation, highest operational cost, and flatly
incompatible with the single-datastore posture of
[ADR-0002](0002-single-postgres-datastore.md).

## Revisit when

- Mera becomes a hosted multi-tenant SaaS with genuinely untrusted tenants, at which point
  per-tenant encryption keys and possibly schema separation become proportionate.
- A compliance requirement demands physical data separation.
- RLS policy evaluation shows up in query plans as a measurable cost, which would indicate
  the policies have grown beyond simple predicates and need simplifying rather than
  removing.

## Addendum (2026-11-08): discovering a user's workspaces

Found in the M1 review (docs/12 §1.1 C8). Login and the workspace switcher must list a
user's workspaces, but `workspace` and `workspace_member` are only visible once
`mera.workspace_id` is set, and that id is exactly what is being discovered.

**Decision:** a second transaction-local setting, `mera.user_id`, and two additional
**SELECT-only** permissive policies:

- `member_self` on `workspace_member`: `user_id = mera.user_id`, so a user sees their own
  membership rows in every workspace, and no one else's.
- `workspace_via_membership` on `workspace`: `id IN (SELECT workspace_id FROM
  workspace_member WHERE user_id = mera.user_id)`.

Writes still require `mera.workspace_id` (the `tenant_isolation` policy's `WITH CHECK`), so
`mera.user_id` can only ever widen *reads of identity rows*, never writes, and never any
content table. It is set only by `user_transaction(user_id)`, after the user has been
authenticated.

**Rejected:** `SECURITY DEFINER` functions owned by the migration role. They work, but they
move part of the tenancy boundary into functions that bypass RLS entirely, which is the
category of code ADR-0006 exists to avoid.

## Addendum 2 (2026-11-08): periodic jobs that span workspaces

Workers must find the workspaces to iterate (token refresh, health checks, later metrics
and sweeps), but with no tenant context RLS shows them none, by design.

**Decision:** one more SELECT-only policy, `workspace_system_scan` on `workspace` only,
keyed on a transaction-local `mera.system_scan = 'on'`, set solely by
`system_scan_transaction()` and used solely by periodic tasks. It exposes workspace *rows*
(id, name, archived/deleted state) and nothing inside them: every read or write of tenant
data still happens in a per-workspace `workspace_transaction`, exactly as section
"Cross-workspace operations" of docs/03 §11 describes.

**Not chosen:** a separate BYPASSRLS worker role. It would remove layer 2 from all worker
code, which is where an unscoped query is most likely to be written.
