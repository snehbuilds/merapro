# ADR-0016: How Phase 2 was delivered

**Status:** Accepted
**Date:** 2026-11-10 onward (Phase 2, docs/13)

## Context

Phase 2 (docs/13) adds breadth on top of the Phase 1 core. Where the design documents
leave a choice open, or where building showed a different shape was needed, the choice is
recorded here, one section per decision, grouped by milestone. Later sections are added as
each milestone lands.

## P2-M1 — Phase 1 gaps

### 1. Approval validity runs from the slot the approval placed

An approval used to be valid for its window (default 24 hours) counted from the decision,
so a post approved now for next week always dead-lettered `approval_stale`.

Validity now runs from the later of the decision and **the time the approval placed the
post for**, stored once on the slot as `placement.approved_slot`. Consequences:

- A post approved today for next Tuesday publishes next Tuesday.
- A post held past its slot (kill switch, reconnect, outage) still goes stale once the
  window after its slot passes, and has to be approved again. That is the point of the
  window: an approval is consent for a moment, not forever.
- Moving a post later on the Calendar does **not** extend the window: `approved_slot`
  never changes after placement. Moving it far enough means approving it again.

Refusing far-out requests at submission was the alternative. It was rejected because
scheduling a week ahead is a normal thing to want.

### 2. Content policy is checked again at publish

`assert_publishable` gains check 11, `banned_content`: the post's text and hashtags are
checked against the account's current banned words and the workspace's banned hashtags
just before the platform call. Banning a word or tag after approval stops a queued post
(dead-lettered with the reason, like every other permanent refusal). Banned tags are also
enforced by the drafting guardrail (`banned_hashtags`) and by the readiness check before
submission, and are removed deterministically from the Hashtag agent's output: the model
is told the list but not trusted to obey it.

### 3. Posting times move the base, never the rules

When an approval has no requested time, each account goes out at its next posting time
(the account's own, else its group's, else the workspace's), plus its stagger offset.
Quiet hours, the daily cap and the per-account spacing are then applied exactly as before,
so posting times can only choose when, never loosen a rule. A requested time is honoured
as asked.

### 4. Several Pages under one login: the person chooses at redemption

Meta's login can grant several Pages. The connector now returns every one; the encrypted
pending identity holds them all; redeeming the one-time connect code without a choice is
refused with `choice_required` (inside the transaction, so the code stays usable), and
`GET /social-accounts/connect/choices` lists names without tokens. Only the chosen Page's
credentials are stored on the account. The others are discarded with the pending record.

### 5. Stopping a run is cooperative

`POST /agent-runs/{id}/abort` marks the run `aborted`; the drafting loop checks between
accounts and stops before the next one. The account in progress finishes, so no
half-written version is ever stored. Retry is offered for failed or stopped **drafting**
runs only: a revision's instruction belongs to the person who gave it and is asked again
rather than replayed.

### 6. Alt text is suggested, never saved by the agent

The alt-text agent makes one vision call on a downscaled copy (at most 1024 px, no crop)
and returns a suggestion. Saving it is a person's edit and creates a version like any
other. Text inside an image is data: the prompt says so, and the suggestion is shown for
review before it can reach a post.

### Deferred within P2-M1

- Analytics CSV export as a 202-and-link job: the synchronous export is adequate at
  current volumes; it moves with the analytics loop in Phase 3.
- The Instagram carousel planner skill: folded into media generation (P2-M5), where the
  carousel is planned together with the images it needs.
- BFF refresh state shared across web instances: see P2-M10.

## P2-M3 — Bluesky and Mastodon

### 7. Bluesky connects with an app password, and only an app password

AT Protocol OAuth (DPoP-bound tokens, PAR, client metadata documents) is a substantial
client of its own; app passwords are what Bluesky offers third-party tools today. Mera
accepts a handle and an app password, calls `createSession` once, keeps only the session
(`accessJwt`, `refreshJwt`) and forgets the password. Any secret that is not shaped like
an app password (`xxxx-xxxx-xxxx-xxxx`) is refused before a request is made, so an
account password never reaches Mera. A custom PDS is allowed, after the same
public-address check as Mastodon instances. Moving to AT Protocol OAuth later changes the
connector only.

### 8. One Mastodon app per instance, registered on first use

Mastodon has no central developer portal: each server issues its own client credentials.
Mera registers itself on a server the first time anyone connects an account there
(`POST /api/v1/apps`) and keeps the registration in `mastodon_app`, instance-wide like the
Telegram bot, with the secret envelope-encrypted; the app role may read and add rows,
never rewrite them. The instance named by a person is normalised and must resolve only to
public addresses before any request (no SSRF into the deployment's network). Account ids
are stored as `id@host` so two servers can never collide. Statuses carry Mastodon's own
`Idempotency-Key`, so a retry after a lost response cannot post twice even before
reconciliation.

### 9. Both feed the existing connect-code flow

Neither platform adds a second way to attach an account. The app-password sign-in and the
Mastodon callback both end in the same encrypted pending identity and one-time connect
code as the OAuth platforms, so the initiator binding, duplicate detection, the account
limit and the audit entries are the same code path.

### 10. No vendored writing skill for either

The upstream skill collection has no Bluesky or Mastodon skills (checked 2026-11-10). The
Writer follows each platform's conventions from its capabilities (length, hashtags,
threads), as it would for any platform without a playbook.

## P2-M2 — Team, member scopes and second factors

### 11. TOTP from the standard library, and the challenge never reaches page script

TOTP is RFC 6238 in thirty lines of standard library (HMAC-SHA1, 30-second steps, one
step of skew either way), so no new dependency. A used step is stored and can never
match again, which stops replay within the window. Turning MFA on bumps the token
version, signing out sessions that never passed the second step. Ten recovery codes are
shown once and stored hashed.

When a password or OAuth sign-in passes for a user with MFA, the API answers 401
`mfa_required` with a single-use, five-minute challenge in `error.detail` (five wrong
codes burn it) and issues no tokens. The web app's server keeps that challenge in an
httpOnly, SameSite=Strict cookie and only ever sends the code from the page, so a
script on the page cannot lift the challenge.

### 12. An Admin or Owner without MFA acts as an Editor until they enrol

Where MFA is enforced (default: outside development; `MFA_REQUIRED_FOR_ADMINS`
overrides), the principal of an Admin or Owner without MFA is loaded with an Editor's
role. Nothing breaks and nobody is locked out, but no admin power is available until the
second factor exists. API keys inherit this from their creator, like the rest of their
authority.

### 13. Second factors are single-use grants bound to an action

`POST /workspaces/{id}/second-factor` checks an authenticator code, the approval PIN or
a recovery code and returns a token valid for five minutes, for one action, in one
workspace, for one user. The guarded endpoint consumes it **in its own transaction**: a
refused or failed action leaves the grant usable; a committed one burns it. Guarded today:
resuming a kill switch, disconnecting an account, approving a company or organization
account, removing an Owner. Engaging a kill switch is never guarded. Each action is on by
default; Admins can switch one on, only an Owner can switch one off, and the change is
audited.

The approval PIN is Argon2id, 6 to 8 digits (not all the same), locked for 15 minutes
after five wrong tries. Setting or changing it needs the person's current TOTP, or their
password before MFA exists. From Telegram, a tap on a company account parks the decision
for three minutes and asks for `/pin`; the right PIN replays the same single-use button
token with `pin_verified`, so the decision is exactly the one tapped.

### 14. Delegation substitutes the delegate in named-approver sets

An away member gets no new cards, and wherever a policy names them as an approver, their
delegate (who must be able to approve) is named instead when a request is created. The
substitution happens when the snapshot is taken, so a request in flight keeps the
approvers it started with. Escalation goes to the people the policies name (a union
across levels) when any are named, otherwise to the card holders as before.

### 15. Rate budgets hold, they do not fail

Each platform has a conservative 24-hour publishing allowance per account (X 100,
Instagram 50, Threads 250), overridable in `social_account.rate_budget.posts_per_24h`.
A post that would exceed it is held until the oldest post in the window ages out, like a
kill-switch hold, because the platform would refuse it anyway.

## P2-M4 — WhatsApp approvals

### 16. The same runtime, with the platform's rules in the adapter

WhatsApp runs on the Telegram bot runtime unchanged: the runtime is keyed by the
channel's `channel_type`, and `WhatsAppChannel` translates its messages. Three buttons or
fewer become reply buttons, more become one list of up to ten rows, link buttons become
text links. WhatsApp cannot edit a sent message, so only a final state ("Approved",
"Expired") becomes a short follow-up; intermediate edits are dropped, which is safe
because every tap is re-checked on the server. Inbound webhooks are normalised into the
runtime's update shape, so pairing, decisions, PINs and commands are one code path.

### 17. The window lives on the binding, the budget on a send log

The 24-hour window is the binding's `last_inbound_at`, refreshed on every inbound message,
plus an in-process note per chat, because the first message (a `/pair`) arrives before
any binding exists and its reply must not become a billed template. Outside the window the
adapter sends one of two templates (`mera_approval_waiting`, `mera_update`) with a
"Show me" quick reply that re-sends the chat's open cards. Every send is appended to
`channel_send_log` (the app role may only read and append), and a deployment-wide monthly
template cap refuses further templates once spent; the web queue still works.

### 18. Pairing codes are bound to a channel

A code is minted for one channel and redeems only there, so a Telegram code cannot pair a
WhatsApp chat. The webhook checks `X-Hub-Signature-256` over the raw body before parsing,
and queues messages (not delivery statuses) under the first message id. WhatsApp is off
until its four connection settings are all set; the API, the bots app and the worker each
build the runtime only then.

## P2-M5 — Media generation

### 19. One provider port, priced before it is called

`MediaGenerator` has a pure `estimate` and an async `generate`. The first provider is any
OpenAI-compatible images API (`MEDIA_PROVIDER=openai`), with per-image prices in settings
because they change; a deterministic fake draws a seeded gradient in sandbox and is never
used outside it. Estimates round up, never down, because the ceiling is checked against
them. The provider-reported cost is recorded on the job whether or not the image was used.

### 20. Budget is a hard stop; the ceiling is a second factor

Each workspace has a monthly budget and a per-request ceiling (defaults from settings,
changed by an Admin). A request over the budget is refused; one over the ceiling needs a
`video_generate` second factor. Both checks run, under a lock on the workspace row, in
the transaction that writes the jobs, and before any provider call. Queued and running
jobs count against the month at their estimate, so concurrent requests cannot overspend.

### 21. Generation is a job; a result is a new version, never an overwrite

Each image is a `media_job` on the `media` queue: a bounded timeout, at most three
attempts for retryable failures, none for refusals, cancel before it runs. An image for a
variant is attached through the same path as an upload, so it makes a new version and
invalidates that account's approval only. The version the request was based on is kept on
the job; if the post changed meanwhile, the image stays in the library and nothing is
replaced. A carousel is one batch, planned together by the Media agent, and attached once
every slot has succeeded, in slot order. This is where the Instagram carousel planner from
P2-M1 landed.

### 22. The Media agent plans, screens and writes alt text

Without a prompt, the Media agent writes one prompt per image from the post and the
account, with alt text, and a safety verdict; a deterministic screen runs first. From an
approval card, "🎨 Regenerate image" shows the estimated cost, and the account is asked
again once the new image lands. Above the ceiling the bot points to the web, where the
second-factor dialog is. Video stays behind `MEDIA_VIDEO_ENABLED` (off), always needs a
second factor, and has no provider wired yet: the media pipeline accepts still images
only, so a video provider comes with video ingest.

## P2-M6 — Unified inbox and Engagement agent

### 23. A reply's identity is copied, never chosen

A comment row copies its account from the published post, and a reply copies it from the
comment; no request body, rule, agent output or comment text can set it. The reply's hash
covers account id, comment id and text, the approver approves that hash, and
`assert_reply_sendable` re-checks at send that reply, comment and post name one account and
that the hash still matches. A forged account id is refused (`identity_mismatch`) before
any platform call.

### 24. Reply approval is its own small flow, not a post-set approval request

The approval core is built around post sets, variants and versions; a reply is one short
text on one account. Replies get a single decision bound to their hash (web, Telegram or
WhatsApp card), by someone with approve permission on that account, with the same audit
trail. An edit voids the approval. Every reply needs it: there is no auto-send in Phase 2.

### 25. Polling tapers per post, and not every platform is polled

Each published post carries `next_comments_at` and a cursor: every five minutes for six
hours, half-hourly to two days, three-hourly to a week, daily to thirty days, then never.
Facebook, Instagram, Threads, Mastodon and Bluesky are read. X (reply search needs a paid
API tier) and LinkedIn (comments need Community Management API approval) are not, and the
inbox says which platforms it reads.

### 26. Comments are data to the agent, and its drafts are cleaned

Rules run first (keyword rules need no model), then the Engagement agent, then rules that
depend on its classification. The comment sits in an `<untrusted_comment>` block; the
agent has no tools and cannot approve. Its draft is stripped of hashtags, links the post
did not contain and @mentions of anyone but the commenter, and checked against the voice's
banned words when it is submitted. Deleting a comment takes a `comment_delete` second
factor; hiding does not.

## P2-M7 — Scheduler agent and full orchestration

### 27. The Scheduler agent is statistics, and only ever an input

Per account, published posts of the last ninety days are bucketed by local hour of the
week and scored against the account's median engagement, with a prior of three median
posts and each post capped at three times typical, so one viral post does not move a
schedule. A proposal is used only when nobody asked for a time and the account has no
posting times. It then goes through `place`, which applies quiet hours, the daily cap and
spacing like any other time, and is kept on `scheduled_post.placement.proposal` with its
reason. A property test feeds `place` random proposals and checks every rule on the result.
The advisory model call that docs/06 allows for cross-platform judgement is not used:
the statistics are explainable, and the result would be overridden by `place` anyway.

### 28. Orchestration is a parent run with resumable stages

"Run all agents" makes one `orchestration` run with three stages, each recorded as a
`handoff` step: draft (Writer, Hashtag, Editor, as today; Research happened at ingest),
media (a generation job for each account whose platform needs an image and has none,
with every budget and ceiling still applying) and schedule (the Scheduler agent's
proposal per account; nothing is scheduled before approval). The runs these stages start
become children of the orchestration run, so the Agents screen shows the graph with each
child's steps and cost. A stage that succeeded is never run again: resuming a failed or
stopped run starts at the first stage without a success.

## P2-M8 — Mera's MCP server

### 29. One stateless dispatcher, both protocol eras, no SDK

`mera.mcp` implements JSON-RPC directly: `initialize` selects legacy semantics, a request
with modern `_meta` is served without a handshake, and `server/discover` describes the
server. No session is held, so any replica answers any request. Writing it directly keeps
the dual-era handling in one small module instead of adapting an SDK built for one era.

### 30. Tools are REST calls as the caller; the list is a security boundary

Every tool is an HTTP call to `/api/v1` with the caller's bearer, so RBAC, guardrails,
idempotency and the approval invariant apply unchanged. No tool approves, publishes,
resumes automation or reveals a credential, and a test asserts the list. Writing tools
require explicit targets. The MCP service sends `X-Mera-Service` with a shared secret;
the API then audits the call as surface `mcp`.

### 31. MCP OAuth tokens are only good at the MCP server

The authorization server accepts Client ID Metadata Documents (DCR is not offered),
requires PKCE S256 and the `resource` parameter, and returns `iss`. Tokens are bound to
the MCP resource and a single workspace. The REST API refuses an MCP token unless the MCP
service presents it, so a leaked token cannot be replayed against the REST API. Tokens
never carry `admin` or `accounts:write`. A missing scope gets `insufficient_scope` naming
it (incremental consent). Refresh tokens rotate, and a replayed code revokes what it
issued. The MCP service caches who a bearer is for a minute; every tool call is still
authenticated by the API, so a revoked token stops working at the next call.

### 32. Second factors happen on a Mera page, never in the chat

A tool that needs a second factor parks a pending action and returns a URL-mode
elicitation (modern clients) or a tool error with the link (legacy clients). The person
confirms on `/w/{slug}/confirm/{id}` with the usual second-factor dialog, and the action
runs there as them. The client's retry, carrying the sealed `requestState`, reads the
outcome and runs nothing twice. Workspace choice for multi-workspace credentials uses a
form-mode elicitation. `subscriptions/listen` is not offered yet: clients poll
`resources/read`.

## P2-M9 — MCP client registry with its controls

### 33. Registered is not usable; discovered is not trusted

Every entry point (form, `mcpServers` JSON, an agent's `propose_mcp_server`) creates a
`pending_review` registration with every tool disabled. Approval takes an Admin and a
`mcp_server_approve` second factor, and needs a successful discovery first so the tools
can be reviewed. Annotations are hints: tools from uncurated servers are `write` (or
`destructive` when they say so) and default to asking every time. A description that
reads like an instruction to the model (tool poisoning) is flagged and cannot be enabled
until acknowledged. A changed schema hash disables the tool (the rug pull). `auto` on a
write tool takes a `mcp_tool_auto` second factor.

### 34. Three levels, default deny, and writes wait for a person

An agent sees a tool only if it is enabled on an active registration, the registration's
group scope covers the run, and a binding names that agent. The Publisher and Approval
agents can never be bound (enforced in the API and by a database check). A call under
`always` is an `mcp_tool_call` whose hash covers the registration, the tool, its schema
hash and the canonical arguments. The card (bot or web) shows the exact arguments.
Rejection or expiry never executes, and execution re-checks the hash, the schema and the
server state. Results reach agents inside `<untrusted_tool_result>` and are size-capped.

### 35. stdio servers run in a sandbox whose only exit is an allowlisting proxy

`mcp-sandbox` runs pinned packages only (exact npm or PyPI version, or an OCI digest), via
fixed launchers, never a shell. It runs on an internal network with a read-only root,
dropped capabilities and process, memory and CPU limits, under gVisor where available.
Each server gets only its own credentials as environment variables, plus a proxy URL with
a per-server token. `mcp-egress` admits a CONNECT only to that server's allowlisted
hosts, only on TLS ports, and only when every resolved address is public. It connects to
the address it checked, which defeats DNS rebinding. Plain HTTP is refused. Remote
servers are reached directly after the same public-address check. The proxy's decisions
are tested in-process. The container walls come from Compose and are not exercised by
the unit suite. The subprocess relay test runs on Linux only (asyncio subprocesses on
Windows need the Proactor loop).

### 36. External OAuth is per registration, keyed by issuer

Mera discovers the authorization server from the server's Protected Resource Metadata,
identifies itself with its own Client ID Metadata Document, uses PKCE S256 and `resource`,
and checks `iss` before redeeming the code. Tokens are stored encrypted under the
issuer and sent only to that server. Dynamic Client Registration is not used, and an
authorization server without CIMD support is reported as unsupported. Tokens are not
refreshed ahead of expiry yet: an expired token surfaces as a failed call, and the Admin
re-authorizes.

### 37. Disable is the incident path

Disabling a server needs no second factor. It cancels pending calls at once, stops the
sandboxed process and revokes its egress grant. A workspace can point media generation at
an approved MCP image tool (`media.provider = mcp`). It goes through the same gate, bound
to the Media agent and set to `auto`, because an unattended job cannot wait for a card.

## P2-M10 — Release security review

The release review found four high, six medium and seven low findings (one medium was a
false positive: `rbac.require` with no subject already denies restricted members). All
others are fixed, each with a test. Migration 0022 carries the schema side.

### 38. Redirects and fetches the review closed

- **Redirects.** An OAuth client's redirect URI must be `https` with a host, loopback
  `http`, or a private-use scheme that contains a dot and is not on a deny-list
  (`javascript`, `data`, `file`, …). URIs with fragments, whitespace or backslashes are
  refused. The consent page applies the same rule before it navigates (`safeRedirect`).
- **Fetches to hosts other people name.** Registry OAuth discovery, the MCP client's HTTP
  transport, and Bluesky and Mastodon (connect, publish, engagement) all use the guarded
  transport. It connects only to the addresses it checked, so DNS rebinding cannot reach
  an internal host. Redirects and proxies from the environment are off, and answers are
  size-capped. The resource metadata must describe the server it came from, and every
  OAuth endpoint must be `https`.

### 39. The sandbox relay is authenticated and each server runs as its own user

Every relay route except `/healthz` needs `MCP_SANDBOX_TOKEN`, and the relay refuses to
start without one. Sandboxed code can reach the relay's port, so the token is what keeps it
from starting or talking to other servers. The relay runs as root with only `SETUID`,
`SETGID`, `CHOWN` and `KILL`. It starts each server under its own uid, with a private
`0700` home and `umask 077`, so a server cannot read another server's environment or
files, or the relay's own.

The relay holds no egress admin token. The API and the workers register each server's
grant with `mcp-egress` themselves, using a token derived as
`HMAC(admin token, server id)`: every process agrees on it, and it is useless for any
other server. They then hand the relay a proxy URL that works for that server's
allowlist only. An empty allowlist means no proxy at all.

`api` and `worker` reach the relay over a second internal network, `sandbox_rpc`.
Residual risk: sandboxed code can open connections to `api:8000` on that network, which is
the same surface the API already exposes publicly. Both service tokens refuse their
`change-me` Compose defaults outside `MERA_ENV=development`.

### 40. Approval surfaces never approve what they did not show

- **Cards.** A reply card's buttons carry a random nonce stored with the reply, not a
  40-bit prefix of the content hash, which an editor could grind against. A tap counts
  only for the card last sent, and only while the reply's full hash is still the one the
  card showed. Resending unchanged content keeps the nonce, so earlier cards stay valid.
- **Long content.** A reply or tool-call card that has to cut its text offers no Send or
  Allow button, only Reject or Deny, and asks the person to approve in the web. On
  WhatsApp, a card longer than an interactive body (1,024 characters) goes first, whole,
  as text, and the buttons follow in a second message.
- **Confirmation pages.** An MCP pending action's summary is written by the server from
  the record it names (for example, the comment and its account). The text a client sent
  is ignored, and the permission is checked when the action is parked.

### 41. Factors are counted per person, and enrolment needs a factor

- **Lockout.** Five wrong authenticator or recovery codes in a row lock codes for 15
  minutes. This counts across sign-in, re-authentication and second factors, because a
  password alone can open MFA challenges at will (`user.totp_failed_count`,
  `totp_locked_until`).
- **Enrolment.** Starting TOTP enrolment needs the password, because whoever enrols
  controls the next sign-in. An account that signs in only through a provider has no
  password and enrols without one; step-up through the identity provider is a follow-up.
- **Sessions.** Turning MFA on revokes every refresh token as well as access tokens, this
  session's included.
- **Commit, then refuse.** A failed re-authentication is returned, not raised, so the
  event and the count are committed before the refusal. A rejected second-factor grant is
  recorded in a transaction of its own. A replayed MCP authorization code, or a spent MCP
  refresh token presented again, revokes every grant that client holds for the person, and
  that revocation is committed before the refusal.
- **Chats and workers.** Chats and workers build the acting person from their stored row
  with the same MFA cap as a signed-in request (`acting_user`), so a chat is no way round
  enrolment.
- **MCP tools.** Lowering a tool's risk class needs the `mcp_tool_auto` second factor, as
  `auto` on a write tool already did. Marking a write "read" first was a way round it.

### 42. Smaller hardening

- **RLS on the Phase 2 tables.** `second_factor_grant` is tenant-scoped,
  `bot_second_factor` is visible only to its own chat (`channel_transaction`), and
  `security_event` is append-only. A transaction reads back only the rows it wrote, which
  `INSERT … RETURNING` needs: `created_at = now()`.
- **Webhooks.** Bodies are capped before and while they are read. Signatures are
  compared as bytes. WhatsApp templates reserve budget atomically under an advisory lock,
  so concurrent sends cannot overshoot, and a failed send still counts (fails closed).
- **Engagement rules.** A rule's scope must be an account or group in the workspace.
- **The `mcp` service.** It gets its own minimal settings (`mera.mcp.settings`): no
  database URL, no master or signing key, no provider secrets. Its sealed `requestState`
  is keyed by `MCP_SERVICE_SECRET`. The client-metadata cache is bounded at 512 entries.

### 43. What the release checks found

- **Cards are tracked per channel.** A person paired on Telegram and WhatsApp got cards
  only on Telegram. The WhatsApp runtime saw the Telegram card as "already sent", and
  refreshes, reminders and `/queue` resends had the same gap: each could act on the
  other channel's messages. Every request-scoped card lookup now filters on the runtime's
  own `channel_type`. Found by the Phase 2 Playwright journey.
- **The inbox listing is batched.** A page of comments costs one scope check per account
  and one query per related table, rather than about eight queries per comment. With 50
  accounts and 200 comments, listing 100 went from about 2.2 s to about 0.1 s. Found by
  the 50-account load check.
- **The load check runs above the default account cap.** Fifty accounts in one workspace
  exceed `MAX_ACCOUNTS_PER_WORKSPACE` (default 25), so the check raises it to 60, as a
  deployment would.
