# ADR-0015: How Phase 1 delivered approvals, publishing and the bot

**Status:** Accepted
**Date:** 2026-11-09 (Sprints 9–14)

## Context

M6 part 2, M7, M8, M10 and M11 were built together. Several places in docs 01, 04 and 07
leave room for choice. Others turned out to need a different shape once the pieces met.
This records each choice and why, so later work does not redo it by accident.

## Decisions

### 1. The scheduler places each account the moment it is approved

ADR-0014 §2 says "the scheduler always places the approved variants". Placement happens
**inside the decision's transaction**, per account, under the request lock. An approval
and its slot can never disagree. One `scheduled_post` per account carries a unique
`sha256(account || version)` key, so approving twice cannot schedule twice.

The time is `requested time (or first approval + 60 s) + stored stagger offset`, then
moved forward past quiet hours, the daily cap and a 10-minute per-account spacing. That
rule is deterministic, so the "Scheduler agent" of docs/11 M8.6 is a policy, not an LLM
call. A learned slot picker belongs with the Phase 3 analytics loop.

### 2. Editing a scheduled account cancels its slot

`scheduled` joins the editable states. A new version supersedes the pending approval,
cancels any queued slot for the old version, and sends the account back to drafting.
`assert_publishable` would refuse the old version anyway. Cancelling keeps the calendar
honest.

### 3. A set may have several open approval requests, on disjoint accounts

The bot's Edit re-asks **only** the revised account while the original request stays open
for its siblings (docs/07 §3.3, §11 "approve one, edit another"). The unique
"one open request per set" index became a plain partial index. The service enforces
disjointness under the post set's row lock. A partially approved set can send only its
remaining accounts.

### 4. A held post is not a failed post

A kill switch, or an account needing reconnection, leaves the slot **queued** and
re-checks it every 5 minutes (15 minutes for reauth). Only conditions that a retry cannot
fix are dead-lettered: content refused, a changed version, a stale approval.

### 5. One bot per deployment; the worker sends

`TELEGRAM_BOT_TOKEN` is deployment configuration, because one bot serves every
workspace. `approval_channel` records whether a workspace uses it. The `bots` service
only verifies and queues webhook updates. The `approvals` worker handles them and also
sends and edits cards directly. docs/07 §8 has the worker call the bot service's internal
endpoint instead. Sending directly removes a hop and an internal credential. The `bots`
service still reads no tenant data and can restart freely.

### 6. Callback data carries the workspace

Telegram callback data is limited to 64 bytes, and it now carries
`<kind>:<workspace, 22 chars>:<…>`. The workspace is needed to find the token or card
under row-level security. It is not trusted: a decision is a token validated in that
workspace, against the chat's active binding (`identity_mismatch` otherwise). Every other
button is checked against the card's recipient. Only decisions carry tokens. Navigation,
Edit and Reschedule are tied to the card, and their effects run as the bound user with
that user's permissions.

### 7. An approval-triggered revision re-asks automatically

`POST /approval-requests/{id}/edit` (Approver or above) queues a scoped revision with
`trigger=approval_edit`. When the new versions land, the drafting job submits them for
approval again under the same person. A replacement text sent from the bot is a
person's edit (Editor or above) and follows the same path.

### 8. Live updates are SSE polling the event table

`GET {ws}/events` streams from the `event` table: replay from `Last-Event-ID`,
authorization checked per event, and a heartbeat every 20 s. The stream ends after five
minutes and the browser's `EventSource` reconnects with its cursor. Postgres
LISTEN/NOTIFY can replace the 2-second poll later without changing the contract.

### 9. Charts are SVG, not Recharts

The analytics screen draws its bars as accessible inline SVG, with a screen-reader
table, instead of adding Recharts. That keeps the build reproducible on networks that
block package downloads. Swapping in Recharts later changes one component.

### 10. Real adapters are contract-tested, not live-tested

The LinkedIn, Facebook Page, Instagram, Threads and X adapters and connectors follow
each platform's documented API. They are covered by `httpx.MockTransport` cassettes for
request shape and error classification. No live account credentials existed in this
environment. Sandbox mode always publishes through the fake adapter.

## Consequences

- docs/04 gains `edit`, `preview`, `variants`, scheduling, calendar, dead letters,
  analytics, events, approver bindings and the sandbox endpoints. The OpenAPI spec and SDK
  are regenerated from code.
- The first live publish to each platform is still to be done with real app credentials
  (docs/11 M8 acceptance names it). The runbooks list the variables and scopes.
