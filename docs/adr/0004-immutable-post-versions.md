# ADR-0004: Immutable post versions anchored by a content hash

**Status:** Accepted, amended by [ADR-0011](0011-post-sets-and-per-account-variants.md)
**Date:** 2026-11-08

> **Amendment note.** This ADR was written when a post targeted *platforms* and one
> version covered all of them. Multi-account posting moved the version down a level: a
> `post_version` now belongs to one `post_variant`, which is one **target account**, and
> its hash includes `social_account_id`. The mutable container is `post_set` rather than
> `draft`. Every principle below still holds and is strengthened — the hash now binds
> content *and* destination — but read
> [ADR-0011](0011-post-sets-and-per-account-variants.md) alongside it for the current
> shape.

## Context

Mera's central promise is that nothing publishes without human approval. That promise is
only worth something if "approval" refers to specific content.

The failure this prevents is subtle and would be easy to ship. Suppose a post is approved
at 09:00 and scheduled for 17:00. At 11:00 someone edits it, by hand in the Draft Studio,
by asking an agent to rewrite it, or by swapping an attached image. At 17:00 it publishes
under an approval that was granted for different words. Nobody did anything obviously
wrong, no error was raised, and the audit log says it was approved.

The naive data model makes this nearly inevitable: a `post` row with a `body` column and
an `approved` boolean. The boolean survives an `UPDATE` to the body.

## Decision

Post content is **immutable and versioned**, and approvals bind to a content hash.

1. `draft` is the mutable container. It holds metadata and a pointer,
   `current_version_id`.
2. `post_version` and its `post_variant` children are **insert-only**, enforced by a
   database trigger rather than by repository convention. The repository layer is the
   thing most likely to acquire a well-meaning bug; the trigger is not.
3. Each version carries `content_hash`: SHA-256 over the canonical JSON of all variants,
   with sorted keys, sorted variants, sorted hashtags, fixed separators, and **media
   hashed by content checksum rather than by ID**.
4. `approval_request` copies the hash at creation. `approval_token` carries a prefix of it.
5. Editing creates a new version with `parent_version_id` set. Any pending approval for the
   prior version moves to `SUPERSEDED` and its tokens are revoked.
6. `assert_publishable` **recomputes** the hash from the stored variants and compares it
   to the stored value, then compares that to the approval's hash. The recomputation is
   what catches direct database tampering rather than only application-level edits.

## Consequences

**Good**

- The guarantee becomes structural. There is no code path that mutates approved content,
  because there is no code path that mutates content at all.
- Editing after approval fails safely and specifically: `approval_content_mismatch`, not
  a silent publish.
- A stale bot button tapped the next morning fails for a precise, logged reason.
- Full version history for free, which makes the diff view in the edit flow possible and
  gives the audit log something exact to point at.
- A/B variants and evergreen recycling are natural: they are versions, not special cases.
- The audit log can answer "who approved this exact text", which is the question that
  actually gets asked after an incident.

**Bad**

- **More rows.** Five revisions means five versions and their variants. Acceptable: text
  is small, and the retention policy ages out old content bodies.
- **The hash function is load-bearing and brittle to change.** Any change to the canonical
  JSON invalidates every live approval. Mitigated by a format version, property-based
  tests for determinism, and a rule that changing it requires a migration that
  re-hashes and re-approves.
- **Media must be hashed by content.** Hashing a `media_asset` ID would leave a hole where
  swapping the bytes behind the ID changes the post without changing the hash. Requires
  checksums on every asset.
- **A re-save that changes nothing must not invalidate.** The hash therefore excludes
  timestamps, IDs, and confidence scores. Easy to get wrong by including a field that
  seems semantic but is not.
- Editing is heavier than an `UPDATE`: create a version, create variants, recompute,
  supersede, revoke tokens, create a new request, re-dispatch.

## Alternatives considered

**Mutable post with an `approved_at` timestamp, cleared on edit.** The obvious design. It
depends on every write path remembering to clear the flag. One forgotten path, one bulk
update, one migration, and the guarantee is gone with no visible symptom.

**Mutable post with an `updated_at` compared against `approved_at`.** Better, and still
fails on any write that does not touch `updated_at`, including a trigger-less bulk
operation. It also cannot detect an edit-and-revert, which should arguably still require
re-approval and under a hash does not.

**Event sourcing the whole post.** Gives immutability and history, at the cost of
projections, rebuild logic, and eventual-consistency reasoning across the entire model.
Immutable versions deliver the needed property at a fraction of the complexity.

**Signing the content at approval time instead of hashing.** A signature over the content
would also work and would additionally prove *who* approved. Rejected because the
signature would be made by the server on the approver's behalf, which proves nothing a
database row does not already prove, and because key rotation would then invalidate
historical approvals.

## Revisit when

Honestly: do not. This is the decision the product rests on, and the alternatives are all
strictly weaker.

The only legitimate reason to reopen is if the version table's growth becomes a storage
problem at a scale far beyond the current posture, and even then the answer is to prune
old unapproved versions rather than to make content mutable.

Changes to the hash function itself are a different matter and must go through a
migration that re-hashes existing versions, invalidates live approvals, and re-requests
them. Plan that as a release, never as a patch.
