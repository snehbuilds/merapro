# ADR-0002: PostgreSQL as the only datastore

**Status:** Accepted
**Date:** 2026-11-08

## Context

The conventional stack for this kind of system is Postgres for relational data, Redis for
the job queue and caching, and an S3-compatible service for media. That is three
datastores to run, back up, monitor, and reason about.

The operating posture here is self-hosted, personal to small-team: single digits of social
accounts, tens of posts per week, low thousands of content items per month. At that scale
the question is not whether Redis is faster — it is — but whether the throughput it buys
is worth the operational surface it costs.

## Decision

PostgreSQL 16 is the only datastore. It carries:

| Role | Mechanism |
| --- | --- |
| Relational data | Ordinary tables |
| Vectors | `pgvector` with an HNSW index |
| Search | `pg_trgm` and `tsvector` |
| Job queue and scheduling | procrastinate: `LISTEN/NOTIFY` plus `FOR UPDATE SKIP LOCKED` |
| Event bus | An append-only `event` table plus `pg_notify` |
| Cache, idempotency keys, rate-limit buckets, approval token ledger | Tables with TTL columns, swept periodically |

Media goes to a mounted filesystem volume behind an `ObjectStore` port. An S3
implementation is written and tested but unused, so production can switch by configuration.

## Consequences

**Good**

- **Transactional enqueue.** A job and the row it operates on commit together. The entire
  class of bug where a job fires for a transaction that rolled back simply does not exist.
  With Redis this requires an outbox pattern, which is more machinery than the queue.
- **Durable event bus.** The `event` table is the source of truth and `NOTIFY` is only a
  wakeup, so SSE clients replay from `Last-Event-ID` after a disconnect. Redis pub/sub is
  fire-and-forget and would have needed a durable log alongside it anyway.
- **One backup artifact.** `pg_dump` plus the media directory is the whole system state.
  The restore runbook is a page long.
- **Two containers for local development.** `make up` is genuinely fast, and the setup
  instructions fit on a screen.
- **One consistency model** to hold in your head.

**Bad**

- **Connection pressure.** Every worker and every `LISTEN` holds a real connection. The
  budget in [01 - Architecture](../01-architecture.md) section 6.3 puts a single-host
  deployment near 30 connections, against a default `max_connections` of 100, and Compose
  raises it to 200.
- **PgBouncer in transaction mode breaks `LISTEN/NOTIFY`.** The obvious scaling move is
  therefore unavailable without care. The documented order is: raise `max_connections`
  first, then PgBouncer for the API pool only while leaving listener connections direct.
- **Queue throughput ceiling.** Postgres-as-queue handles low thousands of jobs per minute
  comfortably and degrades past that. That is several orders of magnitude above the
  expected load, but it is a real ceiling where Redis's is not.
- **Rate limiting costs a write.** A token-bucket update is a row update rather than a
  Redis `INCR`. Acceptable at the documented limits; the first thing to feel pressure if
  traffic grows.
- **Vacuum pressure.** The queue and event tables are high-churn. Autovacuum needs
  per-table tuning rather than defaults.

## Alternatives considered

**Postgres plus Redis.** The default answer, and correct at a larger scale. Rejected
because at this scale it buys throughput that will not be used, in exchange for a second
service with its own persistence configuration, its own failure modes, and the loss of
transactional enqueue.

**Postgres plus MinIO for media.** Only sensible when media volume is large or multiple
nodes need shared access. A filesystem volume behind an interface covers the single-host
case, and the interface makes the upgrade a configuration change.

**SQLite.** Genuinely tempting for a single-user deployment, and it would make setup
trivial. Rejected because `pgvector` has no SQLite equivalent of comparable quality,
concurrent writers from several worker processes are awkward, and the eventual
multi-tenant path would be a rewrite rather than a scale-up.

## Revisit when

Any one of these is a reason to reopen, in roughly increasing order of urgency:

- Sustained job throughput exceeds roughly 1,000 per minute.
- Worker instances exceed about 20, at which point connection count becomes the binding
  constraint.
- `pg_stat_statements` shows queue polling or rate-limit updates among the top queries by
  total time.
- Vacuum on the queue or event tables cannot keep up with churn.
- The deployment moves to multiple hosts, which makes the filesystem `ObjectStore`
  untenable and is the natural moment to reconsider the rest.

The exit path is incremental rather than a rewrite: the queue sits behind procrastinate's
interface, the event bus behind the `event` table plus a publisher, and media behind
`ObjectStore`. Each can move independently.
