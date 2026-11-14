# ADR-0003: procrastinate for jobs, with approval state as an explicit state machine

**Status:** Accepted
**Date:** 2026-11-08

## Context

Mera needs two things from its asynchronous layer.

**Ordinary background work:** fetch feeds, run agents, publish posts, sync metrics. Needs
retries, backoff, scheduling, and per-social-account serialization so platform rate limits
and post ordering hold.

**Long-lived human-in-the-loop workflows:** an approval request that waits hours, reminds,
escalates to a backup approver, and expires, surviving process restarts throughout.

The second is the harder requirement and the one that distinguishes the candidates.
[ADR-0002](0002-single-postgres-datastore.md) also removed Redis from the table, which
rules out the usual Celery and BullMQ configurations.

## Decision

**procrastinate** for job execution: Postgres-native, async-first, `LISTEN/NOTIFY` for
wakeups, `SELECT ... FOR UPDATE SKIP LOCKED` for dispatch. It provides retries with
configurable exponential backoff, periodic tasks, and **queueing locks**, which serialize
jobs sharing a lock key. Setting the lock to the social account ID makes per-account
publish serialization a property of the queue rather than hand-rolled coordination.

**Approval lifecycles are an explicit state machine in Postgres**, not a workflow-engine
construct. `approval_request` carries `state`, `remind_at`, `escalate_at`, and
`expires_at`. A periodic sweeper selects rows whose timers are due and advances them.

## Consequences

**Good**

- No second datastore, consistent with ADR-0002.
- Transactional enqueue: deferring a job inside the request transaction means a rollback
  un-defers it.
- Queueing locks solve per-account serialization directly, which would otherwise be
  advisory-lock plumbing we wrote ourselves.
- **Approval state is queryable.** "Show every approval expiring in the next two hours"
  is a `SELECT`. With a workflow engine that state lives in engine internals and needs an
  API call or a projection to see.
- The sweeper is trivially testable: set timers in the past, run it, assert transitions.
  No engine, no time-skipping harness.
- Partial indexes on `(state, expires_at)` keep the sweep proportional to pending work
  rather than to total history.

**Bad**

- **The sweeper is polling.** It runs every minute, so reminder and expiry latency is up
  to a minute. Fine for a product whose approval latency tolerance is measured in hours;
  it would not be fine for a second-granularity deadline.
- **Timer logic is ours to get right.** Quiet-hours shifting, escalation levels, and
  supersession are hand-written code where Temporal would have given durable timers for
  free. Mitigated by keeping the state machine pure and testing it exhaustively, including
  property-based tests.
- **No free replay or time-travel debugging.** Reconstructing why a run behaved a certain
  way means reading `agent_step` rows rather than replaying history.
- procrastinate is a smaller project than Celery, with a correspondingly smaller
  community.

## Alternatives considered

**Celery with a SQLAlchemy broker.** Would keep the familiar API. Celery's non-Redis,
non-AMQP brokers are experimental and poorly maintained; running Celery properly means
running Redis, which ADR-0002 rules out.

**Temporal.** The honest best technical fit. Approval lifecycles are precisely the
long-running, timer-driven, signal-receiving workflows it exists for, and a button tap is
exactly a signal. Rejected on operational weight: a Temporal cluster is several more
services plus its own datastore, which contradicts the single-datastore direction for a
system this size.

The mitigation is deliberate and worth stating. Because approval state is an **explicit
state machine in our own tables** rather than implicit in queue internals, a later Temporal
migration replaces the drivers — the sweeper becomes durable timers, decisions become
signals — without touching the state model, the data, or the API. The decision was made to
be reversible.

**`pgqueuer`.** Same architectural idea, newer and smaller. Retained as the documented
fallback if procrastinate proves limiting.

**Hand-rolled `SKIP LOCKED` queue.** Tempting at first, and reliably becomes a worse
version of procrastinate once retries, backoff, periodic tasks, and locks are needed.

## Revisit when

- Approval timer accuracy of better than a minute becomes a requirement.
- Workflows grow beyond roughly ten steps with branching, at which point hand-written
  orchestration starts to cost more than an engine.
- Debugging a production approval failure requires replay rather than log reading, more
  than once.
- Job throughput approaches the ceiling noted in ADR-0002.

If Temporal is adopted, adopt it for approval and agent workflows only, and leave
ingestion, publishing, and metrics on procrastinate. Those are simple queue work and would
gain nothing from a workflow engine.
