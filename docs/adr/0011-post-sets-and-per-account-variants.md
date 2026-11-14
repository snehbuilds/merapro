# ADR-0011: Post sets with per-account variants, and approval at the variant level

**Status:** Accepted
**Date:** 2026-11-08
**Amends:** [ADR-0004](0004-immutable-post-versions.md)

## Context

The original design assumed one post targeting one or more *platforms*. A `post_version`
froze the content for every platform at once, one `content_hash` covered all of it, and
one approval covered the version.

The revised requirement is one post targeting many **accounts**, several of which may sit
on the same platform: two LinkedIn accounts, two X accounts, and a Facebook Page in one
group. That breaks the original model in four places:

1. **Approval.** "Approve both LinkedIn accounts, reject X `@tech`" cannot be expressed
   when the approvable unit covers everything.
2. **Editing.** Revising one account's wording would invalidate the approval of four
   others that did not change.
3. **Content.** Two X accounts must receive *different* text — a platform rule, not a
   preference — so "the X variant" is not a single thing.
4. **Failure.** One account's publish failing must not affect the others, which requires
   independent scheduling rows and independent retry.

A single version covering all platforms makes each of these either impossible or a special
case bolted onto the hash guarantee, and special cases around that guarantee are exactly
what [ADR-0004](0004-immutable-post-versions.md) exists to prevent.

## Decision

Three levels, replacing the previous two.

```
post_set      mutable    one per source item or idea   the editorial unit
  └── post_variant   mutable    one per TARGET ACCOUNT   the lifecycle unit
        └── post_version   IMMUTABLE   one per edit     the approval unit
```

1. **`post_set`** replaces `draft` as the mutable container. Its `state` is a summary
   derived from its variants, including `partially_approved` and `partially_published`.
2. **`post_variant`** is now the mutable per-account container, carrying that account's
   resolved brand voice, enabled flag, schedule, stagger offset, state, and current
   version. It records `via_account_group_id`, the group that routed the account in, so
   group-level settings resolve deterministically when an account belongs to several.
3. **`post_version`** is immutable as before, but is now **per variant**. Its
   `content_hash` covers one account's body, thread parts, hashtags, link, media
   checksums, **and `social_account_id`**.
4. **`approval_request` gains `approval_variant_state`**, one row per target account,
   each with its own state, quorum counters, and content hash. Quorum is counted per
   variant under a request-level row lock.
5. **`post_set.composite_hash`** is a hash over the enabled variants' hashes paired with
   their account IDs. It is what an "Approve all" button names.
6. **Approval tokens carry `variant_scope`**, a list of variant IDs, plus the matching
   hash: a variant's own hash when scoped, the composite when not.
7. **`assert_publishable` takes a `post_variant_id`.** There is no call shape that could
   check one account and publish another.

### The account is inside the hash

This is the single most consequential detail. Because `social_account_id` is hashed, a
version approved for the personal LinkedIn account is structurally incapable of publishing
to the company page. Without it, "approve for A, publish to B" would produce identical
hashes and pass every check — the most expensive failure this model can prevent, and one
that no amount of careful service-layer code would reliably stop.

### Invalidation semantics

| Action | Composite hash | That variant's approval | Sibling approvals |
| --- | --- | --- | --- |
| Edit one variant | changes | dies | **survive** |
| Disable one variant | changes | n/a | **survive** |
| Edit every variant | changes | all die | n/a |
| Reschedule a variant | unchanged | survives | survive |

Rescheduling is deliberately outside the hash: it is a `post_variant` change, not a
content change, and forcing re-approval for a time shift would train approvers to click
through.

## Consequences

**Good**

- Per-account approve, reject, edit, and reschedule are the natural operations rather than
  special cases. The bot's per-account buttons map to one token claim.
- Editing one account costs one regeneration and one re-approval, not five.
- **Partial failure falls out of the design.** Five accounts are five `scheduled_post`
  rows, five jobs, five queueing locks, five retry schedules. There is no mechanism by
  which one can affect another.
- Per-account cost attribution works, because every `agent_step` carries a
  `post_variant_id`.
- Cross-account differentiation is expressible: variants are separate rows with separate
  embeddings, so comparing them is a query rather than a parse.
- The [ADR-0004](0004-immutable-post-versions.md) guarantee is strengthened, not weakened.
  The hash now binds content *and* destination.
- A/B variants and evergreen recycling remain natural: still versions, still not special
  cases.

**Bad**

- **More rows, by a factor of the account count.** A set to five accounts with three
  revisions each is fifteen versions. Text is small and retention ages out unapproved
  versions, but the growth is real and is why the load check in Phase 1 uses ten accounts.
- **Two state machines instead of one.** Per-variant state plus a derived request-level
  summary. Both are pure and exhaustively tested, but it is genuinely more to hold in
  mind than a single machine was.
- **`PARTIALLY_APPROVED` and `partially_published` are new terminal states** that every
  consumer — UI, bot, webhooks, analytics — has to handle. Treating them as errors would
  be wrong; they are normal outcomes.
- **Two hashes to reason about.** Composite and per-variant, with different invalidation
  rules. The rules are simple but they are a thing to learn, and getting them backwards
  would produce either nuisance re-approvals or a real hole.
- **Agent cost scales linearly with account count.** Five accounts is five Writer runs and
  five Editor runs. Partly mitigated by prompt caching on the shared prefix, but
  fundamentally it is five posts being written.
- **Derived set state can drift** if a transition forgets to recompute it. Mitigated by
  computing it in one place from the variants rather than maintaining it incrementally.

## Alternatives considered

**Keep one version across all accounts; approve the whole thing.** Simplest, and it fails
the brief's central multi-account requirement. It also makes the X duplicate-content rule
unsatisfiable, since one body would go to both X accounts.

**One version per *platform*, shared by the accounts on it.** Tempting, and it halves the
row count. Fatally wrong for exactly the case that motivates the feature: two X accounts
sharing a version means identical text, which is the thing that gets accounts suspended.

**Separate post sets per account, grouped by a correlation ID.** Equivalent in power and
worse in practice. Every set-level operation — approve all, the combined performance view,
the digest — becomes a fan-out over a soft grouping with no referential integrity, and
"one story" stops being a first-class thing the system knows about.

**Set-level approval with per-account overrides recorded separately.** Keeps one hash and
bolts exceptions onto it. The exceptions are the common case, so the model would be
describing its own edge cases, and the hash would no longer mean "what was approved".

**Approve the set, publish per account, reconcile differences at publish time.** Pushes
the problem to the worst possible place: after a human has already approved, where a
mismatch has no good resolution.

## Revisit when

- Row growth becomes a storage problem at a scale far beyond the current posture. The
  answer is pruning unapproved versions, not sharing them across accounts.
- A platform introduces a genuine multi-account publish API where one call targets several
  accounts atomically. Even then the approval model should stay per account; only the
  adapter would batch.
- Account counts per set routinely exceed what a single approval card can carry, which
  would push toward approval-by-group-of-accounts as an intermediate level. The schema
  would support it: `approval_variant_state` rows are already groupable.
