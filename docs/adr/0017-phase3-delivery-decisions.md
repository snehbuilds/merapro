# ADR-0017: How Phase 3 is delivered

**Status:** Accepted
**Date:** 2026-11-13 onward (Phase 3, docs/14)

## Context

Phase 3 (docs/14) adds intelligence and autonomy on top of Phase 2. As in ADR-0016,
where the design documents leave a choice open, or where building shows a different
shape is needed, the choice is recorded here, one section per decision, grouped by
milestone. The autopilot authorization design (docs/14 §2.3) gets its own section before
P3-M3 starts.

## P3-M0 - Phase 2 go-live

### 1. The Scheduler agent needs evidence and waits at most a day

A post with no requested time used to move to the account's best hour of the week even
when one or two past posts favoured it, up to three days away. A proposal now needs a
slot measured on at least eight posts (`MIN_SLOT_SAMPLES`), and may move a post by at
most 24 hours (`MAX_SHIFT`). Below that the post goes out from its approval, as in
Phase 1. The slot-proposals listing applies the same minimum, so the UI never shows a
proposal that placement would not use. Both numbers are the proposals in docs/14 Q12;
change them there if Q12 is answered differently.

## P3-M1 - The analytics loop

### 2. Hashtag stats are keyed by tag text, recomputed daily, and say when they are luck

`hashtag_stat` (migration 0023) stores the normalised tag, not a key into the registry
as docs/03 drew it: most tags a post carries are never registered, and a registry alias is
folded into its canonical tag when the row is computed.

A daily job (`metrics:hashtag_stats`, 03:20 UTC) replaces each account's rows in one
transaction, from the last ninety days of published posts. A post's tags come from its
version's hashtags and any `#tag` in its text; its engagement is likes, comments and
shares at the latest snapshot, the same measure the Scheduler agent uses.

Lift is the average of each post's engagement over the account's median, capped at three
times typical so one viral post cannot carry a tag. It is stored unsmoothed with its
sample size. `GET …/hashtags/stats` returns no lift at all below ten posts
(`MIN_HASHTAG_SAMPLES`, Q12) and marks the row `not_enough_data`, so nothing downstream
can show or use a number that is luck. Rows are filtered to the accounts the caller may
read.

### 3. The Hashtag agent chooses tags; Mera supplies the numbers and the order

The agent is shown the account's measured tags: lift and post count, or "too few to
measure". It is told to put relevance first. It never supplies evidence:
`apply_evidence` overwrites every suggestion's basis, sample size and lift from
`hashtag_stat`. A tag the model calls `history` without a measurement becomes
`content_only`.

Ranking is then deterministic:

1. Tags measured at or above the account's typical post, by lift.
2. Unmeasured tags, in the model's order.
3. Tags measured below typical, last.

The same rule applies in drafting and in the Composer's Suggest. Lift is a ratio to the
account's median post (1.0 = typical); the Composer shows it as a change ("+34%" for
1.34) and shows nothing below ten posts.

### 4. Calibration counts only versions a person decided on, as written

`GET …/analytics/confidence-calibration` bands agent-written versions by the Editor's
confidence. A version counts as approved as written when that exact version was approved.
Rejected, expired and superseded-by-edit versions count as not approved; pending and
cancelled ones are not outcomes. A band shows a rate only with ten decisions.

`separates` is true when the highest measured band is approved at least 15 points more
often than the lowest. It is null until two bands are measured. Assisted autopilot
(P3-M3) stays off for a scope while its calibration does not separate (docs/14 §2.4).

### 5. Insights cite their numbers; weights move slowly and only for sources and pillars

A weekly job (`metrics:insights`, Monday 03:40 UTC) builds deterministic aggregates per
workspace from the last 28 days: by pillar, source, platform, account and group, plus
approval turnaround. A pillar, source, account or group needs ten published posts
(`MIN_POSTS`) before it can be compared. Groups are compared by the mean of each post's
engagement over its own account's median. Accounts are compared by median within their
platform, because a ratio to an account's own median is always about 1 for that account.

The Analytics agent only words the findings. `grounded()` drops any sentence that names a
number the findings do not contain, and each `insight` row stores the aggregate it came
from in `evidence`. If the model fails, a plain summary is built from the findings.

Weights move for **sources and pillars only**. docs/14 1.5 also named slot and hashtag,
but the slot minimum (§1) and the hashtag ranking (§3) already use measured evidence
directly, so a second weight would count the same evidence twice. A move is at most 15%
per run (`MAX_STEP`), is recorded in `weight_change` with before and after, and can be
reverted exactly; a revert is refused if the weight moved again since. Auto-tune is on by
default and can be turned off per workspace (`PUT …/analytics/settings`). The Analytics
screen shows insights, weights with their history and Revert, calibration, and groups,
accounts and platforms side by side.

## P3-M2 - A/B tests

### 6. Version B is its own post set; a winner needs 500 impressions a side

`POST …/variants/{vid}/ab` creates a new post set for that one account with
`ab_parent_id`, `ab_label` "B" and the hypothesis (migration 0028). The Writer drafts B
unless the caller supplies the text. B is approved and placed like any other set, so
nothing about approval or publishing changes for A/B.

`GET …/post-sets/{id}/ab` compares engagement per impression with a two-proportion
z-test. It says `too_early` until both have published and each has 500 impressions (Q12),
`not_reported` when the platform gives no impressions, `no_difference` above p = 0.05,
and otherwise names the better label. A post with no B returns an empty list.

## P3-M3 - Autopilot

### 7. Autopilot is earned, strictest-wins, and re-checked at publish time

Mode is `manual`, `assisted` or `full`, set in level settings at workspace, group and
account. The effective mode for an account is the strictest set at any level; nothing set
is manual; a company account is manual unless its own setting opts in.

Raising the **effective** mode of any account in scope (`guard_change`, §12) needs:

- an Admin, or an Owner for Full or for a company account;
- a second factor (`autopilot_enable`);
- the scope to have **earned** it: at least 30 decided approvals by people in the last 90
  days, with under 10% edited (Q14). For Assisted, the scope's confidence calibration
  must also separate (§4). Each account is checked again at every submission.

Assisted then publishes unattended only at or above `assisted_threshold` (default 0.85).
Every change emits `autopilot.changed`, which raises a security alert (§11).

When `ApprovalService.submit` finds a variant the mode allows, it:

1. writes an `autopilot_authorization` row bound to the content hash, mode, confidence
   and guardrail results (migration 0027);
2. records a system decision (`channel_type` `system`);
3. schedules the post.

`assert_publishable` check 12 (`autopilot_withdrawn`) re-resolves the mode at publish
time. The post does not go out if the mode dropped, the kill switch is engaged, or the
content changed. Each unattended publish sends a notice on Telegram and WhatsApp with a
Stop button that sets that account to manual.

Not built (follow-ups):

- an MCP tool to change the mode (docs/14 3.7); the mode changes only from the web, with
  a second factor;
- Undo by deleting the published post;
- a daily summary of unattended posts.

## P3-M4 - Digests and webhooks

### 8. Digests are per member; webhooks are signed, guarded and give up after 20 failures

Each member chooses `immediate`, `digest` or `both` and a local time
(`PUT …/members/me/digest`). Every 15 minutes the job sends due digests in the workspace's
time zone, at most once per local day. A digest lists only requests that member can
decide, grouped by account group. A digest-only member gets no per-post cards. WhatsApp
uses the template `mera_daily_digest` (see the WhatsApp runbook). The digest has no
"Approve all".

Webhooks (migration 0026) carry the events the outbox already emits:

- **Signing.** `Mera-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>">`. The secret
  (`whsec_…`) is shown once and stored encrypted.
- **Transport.** SSRF-guarded: https only outside the sandbox, no private addresses, no
  redirects.
- **Failures.** Retries with back-off; the endpoint turns off after 20 failures in a row.

## P3-M5 - Cloning and evergreen

### 9. A clone copies setup, never holdings; evergreen comes back as a draft

`POST /workspaces/{id}/clone` (Owners only) copies:

- settings, with autopilot reset to manual;
- groups, without members;
- pillars, voices, sources and their group links;
- approval policies, without named approvers;
- hashtags and templates.

It never copies accounts, credentials, content, post sets, history, approvals or other
members. A test walks every table with a `workspace_id` to prove it.

An evergreen post (`PUT …/post-sets/{id}/evergreen`, cool-down 7 to 365 days) returns
after its cool-down as a new post set with origin `recycled` and "(refresh)" in the title.
The Writer drafts it, and it needs approval like any draft.

## P3-M6 - External MCP

### 10. Catalog, budgets, breaker and the `mcp` source

**Catalog.** A short curated list in code (`mcp_catalog.CURATED`). Sandboxed packages are
pinned; re-check the pins at release. When `MCP_REGISTRY_SEARCH_URL` is set, the catalog
also searches the public MCP Registry and offers only https remotes. Installing is
registering: `pending_review`, every tool off, the usual second-factor approval.

**Budgets.** Per tool binding: calls per run and per UTC day, 20 and 200 by default.

**Circuit breaker.** Five failed calls in a row open a server's circuit for 60 seconds.
Calls then fail fast, and the server shows as degraded. The next call after the pause is
the probe.

**Re-review.** Tool and argument descriptions are screened for poisoning. Any description
change re-disables the tool, because the schema hash covers it.

**The `mcp` source.** It calls one enabled read tool with approval policy `auto` on an
active server. The result must be a list of items, or an object with `items`, each with a
title and an http(s) URL. Items are data and go through the same canonicalisation,
filters and scoring as RSS.

Not built: the Tasks extension (docs/14 6.5).

## P3-M7 - Governance

### 11. Alerts, data rights and step-up

**Alerts.** A scanner reads new `security_event` rows every minute (a dedicated RLS
policy, migration 0025) and raises `security_alert` rows. It looks for:

- a two-step or PIN lockout;
- refresh-token reuse;
- three rejected second factors for one person in ten minutes;
- autopilot changes.

Owners and Admins are emailed; personal kinds go to that person instead. The same alert
is not repeated within an hour.

**Your own data.** `GET /auth/me/export` returns what Mera holds about the caller.
`DELETE /auth/me` signs them out everywhere and erases personal data after 30 days. It
refuses a sole Owner. Erasure pseudonymises the user and rewrites their audit
`actor_label`. Revoking platform tokens at erasure is a follow-up.

**Workspace data.** Exports are zips of JSON without secret columns, downloadable through
a 15-minute presigned link for 7 days. A comment author's data can be erased on request.

**Step-up.** A person who signs in only with a provider confirms sensitive changes by
signing in with it again (`step_up=1`). The web callback hops same-origin, where the
Strict session cookie is present again, and trades the code for a `mera_ra_` proof. The
proof is single-use, valid for five minutes, and bound to that user and the PKCE
verifier. It returns in the URL fragment, which is never sent to a server.

External MCP OAuth tokens are refreshed every five minutes, ahead of expiry.

## P3-M8 - Release review

### 12. What the Phase 3 security review changed

An independent review of the Phase 3 code found these, all fixed with tests where shown:

1. **Autopilot could be released without its checks** (high). The guard compared only
   the value at the level being edited. Clearing an explicit workspace `manual` released
   a group's `full` with no Owner check, no earned check, no second factor and no alert.
   `guard_change` now compares the **effective** mode of every account in scope before
   and after, and records `autopilot.changed` for any change, including back to inherit.
   A clone no longer copies a group's autopilot settings.
   (`test_clearing_a_stricter_level_is_guarded_like_raising`)
2. **Earning was checked only when the mode changed** (medium). An account connected
   later, or added to a group on Full, published unattended with no history. `authorize`
   now checks each account's own record at every submission.
3. **A tool result could close its untrusted wrapper** (medium). Wrapper tags inside the
   result are removed. (`test_a_tool_result_cannot_close_its_wrapper`)
4. **Any content configurer could make an MCP tool a source** (medium), calling an
   Admin-approved, credentialed tool with their own arguments. Creating an `mcp` source
   now needs `MANAGE_MCP`, respects the server's group scope, refuses a changed, missing
   or unacknowledged-flag tool, and caps the arguments at 4 KB.
5. **A redefined tool kept its old trust** (medium). On a schema or description change
   the risk class and approval policy are reset from the new definition, as for a new
   tool.
6. `delete_me` failed open without a security service. It now refuses.

Recorded, not fixed (follow-ups):

- **Step-up freshness.** The provider is asked with `prompt=select_account`, so a live
  provider session confirms without a fresh sign-in, and login and step-up flows share
  one flow type. Fix: a `purpose` on the flow, `prompt=login` and `max_age=0` for
  step-up, and an `auth_time` check.
- **Withdrawn authorizations are blocked, not revoked.** If the mode is restored within
  the window, the old authorization can publish again.
- **Image results are capped in count, not size.** The budget check is check-then-insert,
  and has no per-run limit without a run id.
- **The export excludes secrets by column name.** An allow-list or a test over every
  tenant column would be safer. No column leaks today.
- **The poisoning screen is a hint.** It is a regex over top-level argument descriptions
  only.
