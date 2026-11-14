# Mera — Build Progress

What has been built, what is partly built, and what is left, against
[11 - Phase 1 Plan](docs/11-phase1-plan.md) (backend milestones M1–M11) and
[12 - Implementation Plan](docs/12-implementation-plan.md) (Sprint 0 spec fixes and the
frontend track F1–F4). Phase 2 is planned in
[13 - Phase 2 Plan](docs/13-phase2-plan.md); its first sprint (15, P2-M0) takes Phase 1
live and absorbs the go-live items under *Not yet verified* and *Carried forward*. Phase 3
is planned in [14 - Phase 3 Plan](docs/14-phase3-plan.md) and is built, except what needs live credentials (see *Phase 3*).

**Last updated:** 2026-11-13, Phase 2 complete (P2-M1 to P2-M10: built, security-reviewed, end-to-end and load tested; see *Phase 2* below). Sprint 15 (P2-M0) is unchanged.

| Mark | Meaning |
| --- | --- |
| ✅ | Done and covered by passing tests or a verified check |
| 🟡 | Partly done: the note says what is missing |
| ⬜ | Not started |

Update this file at the end of every sprint: flip the marks, add a note for anything 🟡, and
refresh the snapshot below.

---

## Snapshot

| Area | Status |
| --- | --- |
| Sprint 0 — spec fixes | 🟡 1 of 14 resolved (C8). The rest await your sign-off |
| Sprint 1 — M1 Foundation + F1 design system | ✅ Done |
| Sprint 2 — M2 identity part 1 + F2 components | ✅ Done |
| Sprint 3 — M2 identity part 2 + SDK + F4 auth wiring | ✅ Done |
| Sprint 4 — M3 Accounts and groups + their screens | ✅ Done |
| Sprint 5 — M4 Ingestion + Sources and Discovery screens | ✅ Done |
| Sprint 6 — M5 part 1: post sets, Writer, Editor, Hashtag + Composer, Brand Voice, Agents | ✅ Done (Checkpoint A passed) |
| Sprint 7 — M5 part 2: platform skills + account profiles, media and renditions, version diff, monthly cap | ✅ Done, browser-checked. A/B, templates, hashtag registry, editable agents moved to Sprint 8 |
| Sprint 8 — M6 part 1: approval core, Approval Queue, kill switch; editable agent models | ✅ Done |
| Sprint 9 — M6 part 2: sweeper, edit flow, remaining endpoints, re-asking remaining accounts | ✅ Done |
| Sprint 10 — M7 Telegram bot + Approval Channels, sandbox phone | ✅ Done (sandbox channel; no live bot token here) |
| Sprints 11–13 — M8 publishing core, LinkedIn / Facebook / Instagram / Threads / X adapters and connectors, Calendar | ✅ Done in sandbox; adapters contract-tested, first live publish needs app credentials |
| Sprint 13 — M10 analytics + Analytics screen, Audit Log viewer | ✅ Done |
| Sprint 14 — M11 hardening: seed, load check, runbooks, CI gates, security workflow | 🟡 Done locally; CI never run on a real runner, clean-machine `make up` not tried (Docker image builds blocked by the TLS proxy) |
| Sprint 15 — P2-M0 go-live (docs/13): email, browser address through the BFF, audit partitions, preflight, Playwright E2E, restore drill, images behind the proxy | 🟡 Everything that needs no outside credentials is done and verified; live platform posts, a real bot and CI need your app registrations, bot token and a GitHub remote |
| Phase 2 — P2-M1 to P2-M10 (docs/13): content and scheduling gaps, team and second factors, Bluesky/Mastodon, WhatsApp, media generation, inbox and Engagement agent, Scheduler agent and orchestration, Mera's MCP server, MCP client registry, hardening and release | ✅ Built and tested per milestone; release security review fixed; Phase 2 browser journeys and the 50-account load check pass. Live credentials (WhatsApp, image provider, platform comment APIs, a real MCP client) not tried |
| Frontend F3/F4 | ✅ Every Phase 1 screen is live against the API (Calendar, Approval Channels, Automation, Analytics, Audit Log, Sandbox inspector, Dashboard queue/upcoming/published added) |

**Tests (2026-11-09, Sprint 14; Sprint 15 figures are in its section):** backend **582 pass** (unit + integration against real Postgres
with pgvector; the property test runs 10,000 sequences), **91.5% line coverage**, and
every per-area gate in `backend/scripts/coverage_gates.py` passes: state machines, RBAC,
settings resolution, content hash, crypto and `assert_publishable` at 100% (two
defence-in-depth branches excluded and commented), policies 99%, approvals 97%,
publishing 94%, adapters 96%, bot and channels 86%, services 89%. Web: 283 tests across
19 files, typecheck, lint and production build pass; OpenAPI and SDK drift checks pass.
Ruff, ruff-format and mypy (strict on domain and application) are clean.

**Verified end to end in a browser** (Sprint 6, web + API + worker + Postgres, sandbox
LLM): six sandbox accounts connected through the full redirect chain (two X, two LinkedIn,
Bluesky, Mastodon) into Tech News; Discovery's "Draft for group" on a real PostgreSQL News
item created a post set and opened the Composer, which polled while the worker drafted six
distinct variants in seconds (**Checkpoint A**), each with confidence, stagger offset and
platform character count; the two X drafts shared a lead sentence and the set-level
guardrail flagged them (0.85, warn); a person's edit to one X account became version 2 and
cleared the warning; a workspace voice with banned phrases created on Brand Voice ("Test
this voice" sample works); an edit hiding a banned phrase behind a zero-width space was
blocked with the reason shown; the "Shorter" chip queued a revise job that came back as
an agent version with the instruction recorded; the Agents screen showed that run's steps
per account and the agent configuration; no console errors.

**Verified end to end in a browser** (Sprint 5, web + API + worker + Postgres, sandbox
LLM): an internal address (169.254.169.254) refused at the URL field; "Test feed" on
PostgreSQL News; three **real feeds** added (PostgreSQL News and LWN feeding Tech News,
the Cloudflare blog feeding the whole workspace); the worker fetched all three within a
minute (45 items) and enriched them; pillars added from Discovery; Tech News ranked the
PostgreSQL items first under "Databases" and a kernel release under "Platform
engineering", while Company Official saw only the workspace-wide feed ranked by the
workspace "Security" pillar; dismiss and undo; "Fetch now" queued, run by the worker,
answered 304 by LWN's conditional request; Last 24h / Last 7 days windows by publication
date; phone-width layout.

**Verified end to end in a browser** (Sprint 4): connect X and Bluesky through the full
redirect chain (web, API, sandbox platform, API callback, web redeeming a one-time code),
duplicate detection, a group of three with the stagger mode "All at once" blocked for two X
accounts, reorder persisted, an account flipping to Needs reauth with dashboard and rail
reflecting it, workspace defaults saved and shown.

**Verified end to end in a browser** (web + API + Postgres, Sprint 3): register (with the
backend's breached-password error inline), create workspace, verify email, invite, sign out,
register as the invitee, refused until verified, verify, accept, land as Editor with admin
controls hidden; create an API key in Settings and use it against the API (scoped read OK,
admin read 403, `/auth/me` refused); sandbox Google sign-in through the real OAuth round
trip. Every step appears in the audit log.

**Not yet verified:**
- **Live platforms.** LinkedIn, Facebook, Instagram, Threads and X adapters and OAuth
  connectors follow each platform's documented API and pass contract tests against
  recorded request/response shapes; no live credentials exist here, so the first real
  post per platform (docs/11 M8 acceptance) is still to do. See
  `docs/runbooks/connecting-social-accounts.md`.
- **A real Telegram bot.** The bot runtime is tested end to end against the recording
  channel and the sandbox phone; a live token, webhook and long polling are untested here.
- **Browser run of the Sprint 9–14 screens** (2026-11-09, seeded sandbox, web + API +
  worker + Postgres): sign-in, Dashboard, Channels (pairing code), Sandbox inspector
  (`/pair`, the waiting card arrived on pairing, **Approve all** from the phone), Calendar
  (four staggered slots), the worker publishing each slot on time with the platform call
  logged, Analytics (90 days), Automation, Audit Log; no console errors. Found and fixed:
  the Audit Log page crashed (a server page imported a constant from a client module),
  bot decisions were not written to the audit log, the sandbox phone listed a card twice,
  and **two X accounts resumed one minute apart after a workspace stop was released**
  (stagger is now also enforced at publish time: a resumed post waits 3 minutes after a
  same-platform sibling of its set; regression test added). The four-account set then
  published on its staggered slots and the phone got the per-account report.
  Not yet clicked through: Calendar drag-and-drop, the bot Edit reply, `/kill` from the
  phone, the phone-width layouts.
- `make up` on a clean machine (M11.7). Both images now build behind the corporate TLS
  proxy with `CORP_CA_BUNDLE` (Sprint 15); a full `make up` on a machine that has never
  run Mera is still to do.
- CI on a real runner (`ci.yml`, `security.yml`): the repository has no commits or GitHub
  remote yet.

---

## Sprint 0 — Spec fixes

From [docs/12 §1](docs/12-implementation-plan.md). Each needs your confirmation before the
docs are amended.

| ID | Item | Status | Blocks |
| --- | --- | --- | --- |
| C1 | `PARTIALLY_APPROVED` semantics (new ADR-0014) | ✅ ADR-0014: request state derived from variant states; `partially_approved` terminal | M6.2 |
| C2 | Token revocation scoped vs revoke-all | ✅ Scoped (ADR-0014) | M6.7 |
| C3 | Status codes for token failures | ✅ 422 content mismatch, 410 used/expired (ADR-0014) | M6.11 |
| C4 | Agent roster (10 vs 11, `hashtag` key) | ⬜ | M5.1 |
| C5 | Missing columns and tables (PIN, `workspace_secret`, `security_event`) | ⬜ | M2.11, M6 |
| C6 | Enum drift across docs | ⬜ | M3 onward |
| C7 | Endpoints used but not in 04 | ⬜ | M7 |
| C8 | Login cannot list a user's workspaces under RLS | ✅ | ADR-0006 addendum, `mera.user_id` read-only policies |
| P1 | Bluesky/Mastodon phase (00 vs 11) | ✅ | Superseded by product decision 2026-11-08: Phase 1 publishes to LinkedIn, Facebook, Instagram, Threads, then X; Bluesky and Mastodon are Phase 2 |
| P2 | MCP isolation in Phase 1 DoD | ⬜ | — |
| P3 | `member_scope` phase (09 vs 11) | ⬜ | — |
| P4 | PIN second factor in Phase 1 | ✅ No PIN in Phase 1; all second factors Phase 2 (ADR-0014) | M6 |
| P5 | Workspace cloning phase | ⬜ | — |
| P6 | Phase 1 Compose service list and webhook routing | 🟡 | Compose built to the recommendation; docs not amended |

**Open product questions** ([00 §7.2](docs/00-overview.md)):

| Q | Question | Status |
| --- | --- | --- |
| Q4 | Content pillars and brand voice | ⬜ **Blocks M4 seed data and M5 Writer evals** |
| Q5 | Timezone and posting window | ⬜ Blocks M8.6. Mockups assume `Asia/Kolkata` |
| Q2 | Platform API access (X tier, LinkedIn app, Meta) | ✅ LinkedIn and Meta apps approved: live adapters (2026-11-08) |
| Q3, Q6–Q10 | Webhook hosting, WhatsApp phase, LinkedIn analytics, account counts, media provider, MCP servers | ⬜ Not blocking yet |

---

## Backend milestones

### M1 — Foundation ✅

| # | Task | Status | Notes |
| --- | --- | --- | --- |
| 1.1 | Monorepo layout, `uv` and `pnpm` workspaces | ✅ | Backend tests live in `backend/tests`, not top-level `tests/` |
| 1.2 | Docker Compose with healthchecks | 🟡 | Services and healthchecks done; image digests not pinned; image build unverified on this network; worker has no healthcheck |
| 1.3 | `config.py`, fail-fast validation, `.env.example` | ✅ | |
| 1.4 | SQLAlchemy base, async session, transaction-scoped `mera.workspace_id` | ✅ | |
| 1.5 | Initial migration: `workspace`, `user`, `workspace_member` | ✅ | |
| 1.6 | RLS with FORCE, non-superuser app role | ✅ | Startup refuses an RLS-bypassing role outside development |
| 1.7 | procrastinate app, task registry, worker with queue selection | ✅ | |
| 1.8 | FastAPI skeleton, `/healthz`, `/readyz`, error envelope | ✅ | |
| 1.9 | Structured JSON logging, correlation IDs, OpenTelemetry scaffolding | ✅ | OTLP exporter not installed yet |
| 1.10 | Makefile, CI through typecheck | ✅ | Plus `scripts/dev.ps1` for Windows. CI not yet run on a real runner |

All five M1 acceptance checks pass.

### M2 — Identity, tenancy, RBAC ✅

| # | Task | Status | Notes |
| --- | --- | --- | --- |
| 2.1 | `user`, `refresh_token`, `oauth_identity`, `api_key` models | ✅ | Plus `email_verification`, `workspace_invitation` |
| 2.2 | Argon2id, registration, login, breached-password check | ✅ | Breached list is local; HIBP checker later. Email verification added (review H1) |
| 2.3 | JWT issue/verify, refresh rotation with reuse detection | ✅ | Plus 20 s grace window, 90-day absolute cap, `typ` claim |
| 2.4 | `Principal` resolution, `WorkspaceContext` | ✅ | Users and API keys |
| 2.5 | RBAC policy module, `policy.require(...)` | ✅ | 100% coverage, property-tested |
| 2.6 | API keys: scoped, hashed, `mera_sk_` prefix, shown once | ✅ | Capped by the creator's live role; revoked on member removal; cannot change membership |
| 2.7 | Google and Microsoft OAuth login with PKCE | ✅ | Two PKCE legs, ID-token verification tested offline; sandbox fake in development only. Not tried against real Google/Microsoft (no client credentials) |
| 2.8 | Workspace CRUD, invitations, archive, switcher contract | ✅ | Invitations need a verified email and die with the inviter's authority. No outbound email yet (accept link shown to the inviter) |
| 2.9 | `member_scope` model and enforcement in `policy.require` | ✅ | Endpoints shipped with M3 (2.9b) |
| 2.10 | `audit_log`, append-only trigger, writes | ✅ | Partitioned, RLS on parent and partition. Services write entries explicitly rather than via middleware |
| 2.11 | `KeyProvider`, envelope encryption, workspace-bound AAD | ✅ | AES-256-GCM, per-secret DEK, lazy key rotation |
| 2.12 | Rate limiting, Postgres token bucket | ✅ | Per principal plus per-IP ceiling; atomic per-account login throttle; purge job |
| 2.13 | Idempotency middleware | ✅ | Never caches `/auth/*`; bodies encrypted at rest; replay re-authenticates |

**M2 acceptance**

| Check | Status |
| --- | --- |
| Full auth cycle: register, login, refresh, logout | ✅ |
| Rotated refresh token reuse revokes the chain | ✅ |
| Each of the five roles against the 04 §2.6 matrix | ✅ |
| One user, two workspaces, different roles, no leak | ✅ |
| Scoped member denied out-of-scope, via REST | ✅ (workspace-level actions); account-level subjects arrive with M3 |
| Secret for workspace A fails to decrypt under B's AAD | ✅ |
| Idempotency replay and 409 on different body | ✅ |
| 100% coverage on `domain/policies` | ✅ |

### M3 — Accounts and account groups ✅

| # | Task | Status |
| --- | --- | --- |
| 3.1 | `social_account` model, encrypted per-account credentials | ✅ Credentials bound to workspace, purpose and row |
| 3.2 | OAuth connect, callback with signed state, reconnect, disconnect | ✅ Two-step: one-time code redeemable only by the initiator. Sandbox connector only; real ones ship with adapters in M8 |
| 3.3 | Duplicate-connection detection (409) | ✅ |
| 3.4 | `account_group`, `account_group_member`, membership CRUD | ✅ Ordered; drag and keyboard reorder |
| 3.5 | `settings_resolution`, strictest-wins for autopilot and approval policy | ✅ 100%, property-tested |
| 3.6 | Resolved-settings endpoints with provenance | ✅ |
| 3.7 | `accounts` worker queue: token refresh, health, capabilities | ✅ Periodic sweep via the system-scan policy (ADR-0006 addendum 2) |
| 3.8 | `NEEDS_REAUTH`: pause that account's jobs, notify | ✅ Since M8 its scheduled posts are held (not failed) until reconnected; siblings keep publishing |
| 3.9 | Group settings: voice, pillars, schedule, stagger | 🟡 Schedule, stagger, overrides done; voice and pillars wait on M5/M4 tables |
| 3.10 | `source_group_link` | ✅ Shipped in Sprint 5 |
| 3.11 | Workspace, account and group endpoints (04 §4.1–4.3) | ✅ Except group queue/calendar (M8) and account rate-budget (M8) |
| 2.9b | Member scope endpoints `GET/PUT members/{id}/scopes` (moved from M2) | ✅ Management UI stays Phase 2; Team shows scopes read-only |

### M4 — Ingestion ✅

| # | Task | Status |
| --- | --- | --- |
| 4.1 | `source`, `content_item`, `content_pillar`, `content_item_score`; pgvector HNSW | ✅ Migration 0007; RLS plus same-workspace triggers on every table |
| 4.2 | RSS/Atom fetcher with conditional requests | ✅ ETag / Last-Modified; DTDs refused; parsed off the event loop |
| 4.3 | Article extraction, language detection, keyword filter | ✅ trafilatura for thin feed entries, py3langid; filtered items kept so they are not re-fetched |
| 4.4 | URL canonicalisation and exact dedupe | ✅ Tracking parameters stripped; every carrying source recorded |
| 4.5 | `LiteLLMProvider`, structured output, fallbacks, pricing, `FakeLLM` | ✅ Validate-and-retry, pinned prices with effective dates, deterministic offline fake (default in Compose) |
| 4.6 | Embeddings and vector dedupe | ✅ 14-day window, threshold 0.92 (configurable) |
| 4.7 | Summarisation and topic classification (Research agent) | ✅ Untrusted content fenced; prompt uses workspace-wide pillars only |
| 4.8 | Per-group relevance scoring | ✅ Arithmetic and explainable; rescored inline on pillar, link and weight changes |
| 4.9 | Periodic fetch, SSRF protections | ✅ Minute dispatcher; guarded DNS-pinned connections, manual redirects, bounded decompression, back-off and 48-hour stop |
| 4.10 | Source CRUD, health, trending endpoints | ✅ Plus test feed, stats, pillars, dismiss. `POST content-items/{id}/post-sets` waits for M5 |
| 4.11 | `agent_run` / `agent_step` recording with cost | ✅ Daily per-workspace cost cap with notification; prompts not stored (object store arrives in M5) |

### M5 — Post sets, Writer, Editor, Hashtag ✅ (A/B is Phase 3; templates and the hashtag registry screen moved to Phase 2, see carried-forward items)

| # | Task | Status |
| --- | --- | --- |
| 5.1 | `Agent[TIn, TOut]` base with step recording | ✅ Cap checked before every call; steps carry the variant |
| 5.2 | `brand_voice` at three scopes | ✅ Slimmed to an **account profile** (Sprint 7): about, audience, banned words, real posts, story bank. The vendored platform skills now decide how a post is built |
| 5.3 | `post_set`, `post_variant`, `post_version`, `media_asset` | ✅ `media_asset` (migration 0010): master plus renditions, immutable bytes, RLS, same-workspace trigger |
| 5.4 | Immutability trigger on `post_version` | ✅ Only foreign-key cascades may null a reference; variants' identity fixed too |
| 5.5 | `compute_content_hash` including `social_account_id` | ✅ Property-tested; unchanged re-saves keep their version |
| 5.6 | `compute_composite_hash` and invalidation | ✅ Recomputed after every version and toggle |
| 5.7 | Target expansion (groups + accounts, deduplicated) | ✅ Skipped accounts reported; stagger offsets fixed at expansion |
| 5.8 | Writer agent per account with sibling drafts | ✅ |
| 5.9 | `cross_account_duplicate` guardrail and `differentiate` mode | ✅ Embedding plus word-overlap check; two retries, then the set blocks |
| 5.10 | Hashtag agent with `basis` and `sample_size` | ✅ Content-only until analytics exists; no invented lift |
| 5.11 | Editor agent: grounding, compliance, confidence | ✅ Unsupported claims block in code; confidence capped by its own issues |
| 5.12 | Deterministic guardrails | 🟡 Banned words, PII, platform limits, duplicate of own posts done; toxicity, blocked topics, daily cap with M6/M8 |
| 5.13 | Orchestrator and `draft_from_content` | ✅ Static template on the agents queue; `revise_post` too |
| 5.14 | `ObjectStore`, OG image, media upload, renditions | ✅ Filesystem store with HMAC-signed URLs; JPEG/PNG/WebP only, bomb-guarded, EXIF stripped; per-platform crops (Instagram 4:5/1:1/1.91:1, LinkedIn and Facebook 1.91:1, Threads 4:5); attach/reorder/alt text = new version; Instagram blocks without an image; article OG image via the SSRF guard |
| 5.15 | Post set, variant and version endpoints | ✅ Version diff, and `results` (combined and per-account) since M10; A/B is Phase 3 |
| 5.16 | Agent cost caps with per-account attribution | ✅ Daily drafting and research caps plus a monthly workspace cap across every agent (`WORKSPACE_MONTHLY_COST_CAP_USD`, default $100) |

### M6 — Approval core ✅ (ADR-0014, ADR-0015)

| # | Task | Status |
| --- | --- | --- |
| 6.1 | Approval models (policy, request, variant state, decision, token, binding, channel, message) | ✅ 0011 plus 0012: `approval_channel`, `approver_binding` (per user, across workspaces), `pairing_code`, `channel_message`, `channel_update` |
| 6.2 | The two pure state machines, incl. `PARTIALLY_APPROVED` | ✅ 100% covered |
| 6.3 | Approval tokens: mint, sign, validate, burn; `variant_scope`; `jti` ledger | ✅ Plus the binding check (`identity_mismatch`) for bot taps |
| 6.4 | `ApprovalChannel` port and `FakeChannel` | ✅ |
| 6.5 | `ApprovalService` with per-variant quorum under row lock | ✅ Approving an account schedules it in the same transaction |
| 6.6 | `assert_publishable` (all eleven checks) | ✅ Every refusal path has a test |
| 6.7 | Scoped token revocation | ✅ |
| 6.8 | Sweeper: remind, escalate, expire, quiet hours | ✅ Every minute; quiet hours shift all timers by the same amount; token/event retention |
| 6.9 | Policy snapshotting, strictest-wins | ✅ |
| 6.10 | Edit flow with per-variant supersession | ✅ `POST /approval-requests/{id}/edit`, reply classification (instruction / replacement / ambiguous), scoped revision, automatic re-ask of just the revised accounts |
| 6.11 | Approval endpoints | ✅ Plus `preview`, `variants`, re-request of remaining accounts (`variant_ids`) |
| 6.12 | `automation_state`, autopilot resolution, scoped kill switch | ✅ |
| 6.13 | Full approval test matrix | ✅ Bot and publish rows added (M7, M8 suites) |
| 6.14 | Property test over random event sequences | ✅ 10,000 sequences |

### M7 — Telegram bot ✅ (sandbox; no live token in this environment)

| # | Task | Status |
| --- | --- | --- |
| 7.1 | `bots` service, webhook route, secret verification | ✅ Constant-time secret check, 401 otherwise; queues and answers 200 |
| 7.2 | Telegram `ApprovalChannel` | ✅ `TelegramChannel` (Bot API) and the recording `FakeChannel` |
| 7.3 | Post set card renderer | ✅ docs/07 §3.2 layout; `fit` truncation order; one line per account above eight |
| 7.4 | Per-account detail view | ✅ Edited in place, own action row |
| 7.5 | Inline keyboard, numbered per-account buttons | ✅ `callback_data` ≤ 64 bytes |
| 7.6 | Callback handling to decisions | ✅ Cards for every recipient refresh after any decision, web or bot |
| 7.7 | `ForceReply` edit flow | ✅ Instruction or replacement; ambiguous asks once with two buttons |
| 7.8 | "Edit all" with confirmation | ✅ |
| 7.9 | Reminder, escalation, expiry, publish-confirmation messages | ✅ Expiry removes the buttons; per-account publish report with errors |
| 7.10 | Account health alerts | ✅ |
| 7.11 | Pairing: codes, `/pair`, deep link, lockout | ✅ Hashed single-use codes, 10 minutes; five failures lock the chat for an hour; waiting cards arrive on pairing |
| 7.12 | Commands | ✅ `/workspaces /switch /groups /accounts /queue /today /stats /pause /resume /kill /kill all /whoami /help` |
| 7.13 | Sticky active workspace per chat | ✅ |
| 7.14 | Update deduplication by `update_id` | ✅ |
| 7.15 | Approval dispatch task | ✅ `approvals:dispatch`, `approvals:telegram_update`, `approvals:sweep` |
| 7.16 | Long-polling mode for local dev | ✅ `python -m mera.bots.poll` |
| 7.17 | Runbook `telegram-bot-setup.md` | ✅ |

### M8 — Publishing, staggering, partial failure ✅ (sandbox; live publish per platform still to do)

| # | Task | Status |
| --- | --- | --- |
| 8.1 | `PlatformAdapter` port, registry, `FakePlatformAdapter` | ✅ Failure injection by handle: `fail`, `flaky`, `ratelimit`, `lost`, `revoked` |
| 8.2 | LinkedIn profile adapter + OAuth connector | ✅ Contract-tested |
| 8.3 | LinkedIn Page adapter | ✅ Contract-tested |
| 8.4 | `scheduled_post`, `published_post`, idempotency key | ✅ Migration 0012, unique key, immutable anchor |
| 8.5 | Publish worker with per-account queueing locks | ✅ Minute dispatcher; `lock=account:<id>` |
| 8.6 | Scheduler and stored stagger offsets | ✅ Deterministic placement (quiet hours, daily cap, 10-minute spacing), stored once (ADR-0015 §1) |
| 8.7 | `stagger_required` guardrail | ✅ Refused at Send for approval, setting named |
| 8.8 | Partial-failure handling and dead-letter per account | ✅ `partially_published`, dead letters with UI and bot visibility |
| 8.9 | Retry with backoff, dead-letter queue | ✅ 1/2/4/8 min, five attempts, `Retry-After` honoured; lost responses reconciled, never double-posted |
| 8.10 | Facebook Page adapter + Meta OAuth connector | ✅ Contract-tested |
| 8.11 | Instagram adapter | ✅ Contract-tested (containers, carousels, media required) |
| 8.11b | Threads adapter | ✅ Contract-tested |
| 8.11c | X adapter | ✅ Contract-tested |
| 8.11d | Bluesky and Mastodon adapters | ⬜ Phase 2 |
| 8.12 | Scheduling, publish-now, calendar, retry endpoints | ✅ Plus set status, published posts, dead letters, set-level schedule/publish-now |
| 8.13 | Contract-test cassettes | ✅ `tests/unit/test_platform_adapters.py` |

### M9 — Web UI ✅ (split into F1–F4 below)

| # | Task | Status | Notes |
| --- | --- | --- | --- |
| 9.1 | Next.js, Tailwind, shadcn/ui, dark and light | ✅ | F1 |
| 9.2 | SDK generation and CI drift check | ✅ | `@hey-api/openapi-ts` (docs named the deprecated openapi-typescript-codegen); `pnpm sdk:check` |
| 9.3 | Auth pages, session handling, workspace switcher | ✅ | BFF with httpOnly SameSite=Strict cookies, refresh-and-retry proxy, CSRF header, OAuth, verify-email, invite accept |
| 9.4 | SSE client hook | ✅ | `GET {ws}/events` with `Last-Event-ID` replay, per-event authorization; `useWorkspaceEvents` on Approvals, Calendar, Analytics |
| 9.5 – 9.17 | Screens (dashboard … sandbox inspector) | ✅ | All Phase 1 screens live; Inbox and MCP stay behind flags (Phase 2) |
| 9.18 | WCAG 2.1 AA pass | 🟡 | Components pass axe in tests; full-app pass in M11 |

### M10 — Basic analytics ✅

10.1 `metric_snapshot` (unique per subject, metric and hour; BRIN) ✅ · 10.2 metric sync,
age-tapered, per-account round robin, 429 reschedules ✅ · 10.3 normalisation per adapter
with the raw field recorded ✅ · 10.4 rollups ✅ · 10.5 endpoints (overview, timeseries,
post, post set, top posts) ✅ · 10.6 account and group comparison against each account's
own 90-day baseline ✅ · 10.7 Analytics screen (SVG charts, not Recharts: ADR-0015 §9) ✅ ·
10.8 approval turnaround ✅ · 10.9 "Not reported" (null, never zero) ✅ · 10.10 CSV export
(synchronous GET; formula-injection guarded) ✅.

### M11 — Hardening and release 🟡

| # | Task | Status |
| --- | --- | --- |
| 11.1 | E2E suite in sandbox | ✅ Backend end-to-end in integration tests; Playwright browser E2E of the approval loop (Sprint 15, `make e2e`) |
| 11.2 | Coverage gates per area in CI | ✅ `scripts/coverage_gates.py`, wired into `ci.yml` |
| 11.3 | Security scans | 🟡 `security.yml` (ruff S, pip-audit, pnpm audit, gitleaks) added; not yet run |
| 11.4 | Seed data | ✅ `python -m mera.seed` / Compose `seed`: two workspaces, five accounts in two overlapping groups, 90 days of metrics, one set waiting |
| 11.5 | OpenAPI completeness | 🟡 Every new operation has a summary and field descriptions; older ones not all reviewed |
| 11.6 | Runbooks | ✅ Telegram, connecting accounts, groups and staggering, kill switch, restore, WhatsApp (Phase 2 stub) |
| 11.7 | README clean-machine run | 🟡 README rewritten for Phase 1; clean-machine run blocked by image builds |
| 11.8 | Load sanity check | ✅ Sweep < 50 ms with 50 open requests over 200 accounts; ten accounts get ten distinct publish locks |
| 11.9 | Backup and restore drill | ✅ Drill run 2026-11-10, logged in the runbook (Sprint 15) |
| 11.10 | Production config validation | ✅ Plus: Telegram token needs a 16+ char webhook secret; production refuses polling |

---

## Frontend track

### F1 — Design system and shell ✅

| # | Task | Status | Notes |
| --- | --- | --- | --- |
| F1.1 | Next.js App Router, Tailwind v4, shadcn, pnpm workspace | ✅ | Next 16.4.5, React 19.3 |
| F1.2 | v2 tokens ported to CSS variables and Tailwind theme | ✅ | Light and dark |
| F1.3 | 4px spacing scale | ✅ | Rounding recorded in `globals.css` |
| F1.4 | IBM Plex fonts, self-hosted | ✅ | |
| F1.5 | Icon set (49 UI + 7 platform marks) | ✅ | |
| F1.6 | App shell: rail, workspace switcher, topbar, kill switch, mobile rail | ✅ | Switcher and user row on real data since Sprint 3 |
| F1.7 | Theme system/light/dark, no flash | ✅ | |
| F1.8 | Command palette | 🟡 | Navigation only; actions arrive with their features |
| F1.9 | Component gallery and accessibility checks | ✅ | `/dev/components` + axe tests instead of Storybook |

### F2 — Component library ✅

| Group | Status | Notes |
| --- | --- | --- |
| Primitives (chip, segmented control, switch, fields, notice, progress, empty state, skeleton, time) | ✅ | |
| Signature (account chip, group dot, variant row, post set card, approval proof, decision card, preview, char counter, stagger timeline, resolved setting row, capability tags) | ✅ | Platform preview is one generic frame |
| Status table for all four status enums | ✅ | `lib/status.ts`, build fails on a missing value |
| Data table, timeline, two-pane list | ✅ | TanStack Table v9 |
| Week calendar | ✅ | Calendar screen |
| Charts | ✅ | SVG bar chart with a screen-reader table |
| Per-platform preview renderers, weighted character counting (e.g. X URLs) | ✅ | Sprint 6 |

### F3 — Screens ✅ (built straight against the API rather than on mocks)

| Screen | Phase 1 | Status |
| --- | --- | --- |
| Dashboard | Build | ✅ Attention, approvals waiting, up next, published today, failures |
| Approvals | Build | ✅ |
| Composer | Build | ✅ Live: media panel, Send for approval; scheduling happens on approval and moves on the Calendar |
| Calendar | Build | ✅ |
| Sources | Build | ✅ Live (Sprint 5); non-RSS types shown "Not yet available" |
| Discovery | Build | ✅ Live (Sprint 5); "Draft for group" waits for the Composer (M5), "Best sources" for publishing data (M8) |
| Brand Voice | Build | ✅ Live (Sprint 6); "Learned from your edits" empty until M6 |
| Connected Accounts | Build | ✅ Built against the live API (Sprint 4); connect menu re-prioritised to LinkedIn, LinkedIn Page, Facebook Page, Instagram, Threads, X (Sprint 6) |
| Account Groups | Build | ✅ Built against the live API (Sprint 4) |
| Agents | Build | 🟡 Live; model editing since Sprint 8; retry and abort of a run not built |
| Automation | Build | ✅ Manual only; kill switch at three scopes |
| Approval Channels | Build | ✅ |
| Team & Roles | Build | 🟡 Members, invites, roles, scopes (read-only) live; permission matrix placeholder |
| Audit Log | Build | ✅ Filters, current actor name, text hash |
| Settings | Build | ✅ Built against the live API (Sprint 4) |
| Analytics | Build | ✅ |
| Inbox | Defer (Phase 2) | Hidden behind a flag |
| MCP | Defer (Phase 2) | Hidden behind a flag |

Screens the mockups don't cover, needing a design pass first: auth and onboarding, empty and
error states, PIN prompt, workspace switcher menu, notifications, `/sandbox` inspector,
version diff view — all ⬜.

### F4 — Wire to the real API ✅

| Backend lands | Frontend wires | Status |
| --- | --- | --- |
| M2 | Auth pages, refresh, workspace switcher, Team list, API keys | ✅ Plus invitations, role change, remove, archived banner, email verification |
| M3 | Accounts, Groups | ✅ Plus Settings defaults, dashboard health notices |
| M4 | Sources, Discovery | ✅ |
| M5 | Composer, Brand Voice, Agents viewer | ✅ Plus Discovery "Draft for group" |
| M6 | Approval Queue, Automation | ✅ Live updates over SSE |
| M7 | Approval Channels | ✅ Pairing, paired approvers, test message, sandbox phone |
| M8 | Calendar, published today, dead letters | ✅ Week view, drag or keyboard reschedule, retry/dismiss |
| M10 | Analytics | ✅ |
| M11 | Audit Log viewer, `/sandbox` inspector | ✅ |

---

## Carried forward from reviews

Known gaps from the Sprint 1 and Sprint 2 code reviews, each with a target sprint, are kept
in [docs/12 §8](docs/12-implementation-plan.md). Summary:

| Item | Target | Status |
| --- | --- | --- |
| Production must refuse to boot when `MERA_ENV` is unset | Sprint 3 | ✅ `MERA_ENV` is required |
| Worker healthcheck in Compose | Sprint 3 | ✅ Heartbeat file + healthcheck |
| Audit unknown/expired refresh, register failures, non-member probing | Sprint 3 | ✅ |
| Outbound email (Notifier): verification and invitation links are shown in-app only; outside sandbox, verification returns 503 | Before any real deployment | ✅ Sprint 15: SMTP Notifier; verification emailed (link no longer returned outside sandbox), invitations emailed with `email_sent` |
| Audit `surface` is `api` for actions taken in the web UI | Sprint 4 | ✅ Proven by a shared proxy secret (`MERA_WEB_PROXY_SECRET`), not a guessable header |
| Audit flooding: cap `permission.denied` and `security.workspace_probe` per actor | Sprint 4 | ✅ 30 a minute per actor and action |
| Envelope AAD includes the record id | Sprint 4 | ✅ |
| **Decision needed:** daily cap and quiet hours resolve most-specific (docs/03, groups mockup), so a group-scoped Editor can raise a group's cap above an Admin's workspace cap. Keep, or make caps strictest-wins / admin-only? | Before M6 | ✅ Strictest wins: lowest cap, longest quiet hours (ADR-0014) |
| No platform catalogue endpoint: the web app copies platform names, limits and hashtag conventions from `capabilities.py` (now in two places) | Sprint 7 | 🟡 `GET /platforms` exists; the media and skills panels read it; the web copy's values now match (Sprint 8) but it is still a copy. Replace it in Sprint 9 |
| M6 must re-run the deterministic guardrails at approval time (voice banned words can change after a draft), refuse approval while any duplicate check is "not checked", and check set-scoped blocks before per-variant approval | M6 | ✅ At submission and at each approval |
| `skipped` targets come back only from create; the UI keeps them in session storage | Sprint 9 | ⬜ |
| Thread semantics: when `thread_parts` exists the body is kept; confirm publish order with the adapters | M8 | ✅ Adapters publish parts in order as replies; hashtags on the last part (X) or first (Threads) |
| Erasure of PII inside immutable versions needs a defined redaction path (docs/09 §9.4) | M11 | ⬜ |
| Drafting cost cap can overshoot by in-flight calls across different sets (per-set jobs are serialised) | M11 | ⬜ |
| Composer UI tests time out occasionally when the whole web suite runs in parallel (pass alone and on rerun) | Sprint 9 | ✅ `maxWorkers: 4` in vitest config |
| Media is now critical-path for Instagram (requires media) and the Meta renditions | Sprint 7 | ✅ |
| Platform skills: the manifest's checksums sit beside the files, so they catch accidents, not a deliberate edit. Pin the manifest hash in CI and run `import_platform_skills.py --check` there (review S7-L8) | M11 | 🟡 `--check` runs in `ci.yml`; the manifest hash itself is not pinned |
| Signed media URLs: no per-route rate limit, query string (with signature) appears in access logs, whole file read per hit (review S7-M7) | Sprint 12 (Instagram publishing uses them) | ⬜ |
| Media orphan sweep: files whose DB insert failed and whose cleanup also failed are never counted against the quota (review S7-M6) | M11 | ⬜ |
| Sprint 8 review lows left open: named approvers are not pruned when a member leaves (an `all_of` request can become unfinishable; it then expires); lock order between edits and decisions (worst case a 500, never a wrong publish); the decision ledger cascades away with its workspace or variants | M11 | ⬜ |
| Bot path before M7 ships: channel binding check, `external_id` check, per-token rate limit, minting only for eligible approvers | M7 | ✅ Binding and identity checks, cards only to eligible approvers; per-token rate limit not added (tokens are single use) |
| Decide whether publishing re-runs the banned-word check (a word banned after approval does not stop the post today) | M8 | ✅ P2-M1: `assert_publishable` check 11 (voice words and banned registry tags) |
| Quiet hours now resolve to the window covering every level; two far-apart windows produce a very long quiet period. Show the effective window in Settings | Sprint 9 | ⬜ |
| A/B variants, post templates, hashtag registry, and requesting approval for only the remaining accounts of a partially approved set | Sprint 9 | 🟡 Remaining accounts ✅; the rest moved (see below) |
| Monthly cap is check-then-spend (parallel agents can overshoot by about one call each) and its month is UTC, not PT | M11 | ⬜ |
| Alt text is typed by people; the docs' alt-text generation per rendition (vision call) is not built | Sprint 8 | ✅ P2-M1.8: suggested by a vision model; generated images get alt text from the Media agent (P2-M5) |
| Instagram carousel planning skill is vendored and viewable but no agent uses it yet; carousels work as ordered multi-image posts | Sprint 12 | ✅ P2-M5: the Media agent plans a carousel as one batch, attached in order |
| Dev database must be migrated to 0010 by hand: the owner password in `.env.example` does not match the local DB (`.env` is off-limits to the agent) | Now | ✅ Migrated by hand |
| Stale copies of the platform catalogue in the web app: Accounts empty state says "Connect LinkedIn, X, Bluesky or Mastodon"; the Composer preview says Instagram takes "3–30 hashtags" (the API says 3–5). Both go away when `lib/accounts.ts`/`lib/composer.ts` read `GET /platforms` | Sprint 8 | ⬜ |
| Q4 (content pillars and brand voice) still open: no seed data; the browser runs used placeholder pillars and voices, so the Writer has no eval set yet | Before Sprint 7 | ⬜ |
| Sandbox relevance scores are low (0.05–0.2): hashed bag-of-words embeddings. Tune the dedupe threshold and "Relevance > 0.5" filter against real embeddings | When real keys are configured | ⬜ |
| Inline rescoring on pillar/link/weight edits is chunked but synchronous; move it to the `agents` queue if workspaces grow past the daily item cap | M11 | ⬜ |
| Scoped Editors' group pillars affect the workspace-wide maximum `relevance_score` (documented behaviour; revisit with the caps decision) | Before M6 | ⬜ |
| Article fetches have no per-host politeness delay; network-level egress policy for workers | M11 | ⬜ |
| Duplicates carry no relevance score, health needs a second call, dismissed items cannot be restored after reload, a pillar's group cannot be changed (UI notes from Sprint 5) | M9 polish | ⬜ |
| Validation errors from request schemas surface Pydantic's wording ("String should have at least 1 character"); map them to product copy | M9 polish | ⬜ |
| `MERA_WEB_PROXY_SECRET` could not be added to `.env.example` (a permission rule protects env files); add it by hand | Now | ✅ `.env.example` now documents every setting: proxy secret, platform apps, Telegram, LLM keys and caps, OAuth login, limits (optional secrets commented out) |
| BFF refresh dedupe is per process; several web instances need shared state | M11 | ⬜ |
| OAuth not tried against real Google/Microsoft (needs client credentials) | When credentials exist | ⬜ |
| Mid-transaction `commit()` drops the tenant setting | Sprint 4 | ✅ Settings re-applied at every transaction begin; inside `workspace_transaction` a stray commit fails loudly |
| Monthly audit partitions must get RLS and narrow grants | By M11.9 | ✅ Sprint 15: migration 0013 and a daily task create the next two months with RLS, policies and no direct grants |
| Audit `actor_label` rendered from `user` at read time | Sprint 13 | ✅ `actor_name` beside the recorded label |
| `--forwarded-allow-ips` behind the reverse proxy | M11 | ✅ Sprint 15: the BFF sends the browser's address with the proxy secret; `FORWARDED_ALLOW_IPS` documented for a proxy in front of api/bots |
| Sprint 9 additions: real adapters are contract-tested only; first live post per platform needs app credentials (ADR-0015 §10) | When credentials exist | ⬜ |
| Approval validity (default 24 h) is checked at publish time, so a post scheduled further out than the window dead-letters `approval_stale`. Either refuse such a request at submission or measure validity from scheduling | Phase 2 | ✅ P2-M1: validity is measured from the approved slot |
| Post templates and A/B variants: not built (A/B is Phase 3 per docs/11 §5; templates are in docs/04 but no Phase 1 task) | Phase 2/3 | 🟡 Templates ✅ P2-M1; A/B stays Phase 3 |
| Hashtag registry: table added (migration 0012); CRUD and the banned-tag guardrail not wired yet | Phase 2 | ✅ P2-M1: Library screen, CRUD, banned tags enforced at publish |
| `posting-slots` endpoints (docs/04 §4.10) not built; placement uses quiet hours, cap and spacing only | Phase 2 | 🟡 Posting times per group/account (P2-M1) and the Scheduler agent's scored proposals with `GET …/slot-proposals` (P2-M7); no separate recompute endpoint (scores are computed on read) |
| Analytics CSV export is a synchronous GET, not the 202-and-link job of docs/04 §4.12 | Fine at Phase 1 volumes | ⬜ |
| Facebook/Instagram connect the first granted Page; picking among several Pages means reconnecting | Phase 2 | ✅ P2-M1: Page picker |
| Playwright browser E2E and a full browser click-through of the Sprint 9–14 screens | Before release | 🟡 Playwright E2E of the approval loop passes (Sprint 15); drag-and-drop, bot Edit reply, `/kill` from the phone and phone widths still unclicked |

---

## Sprint 15 — P2-M0 go-live (docs/13)

What could be done without your credentials is done and verified. The rest needs you
(see *Your part* below).

| # | Task | Status |
| --- | --- | --- |
| 0.1–0.3 | App registrations, first live post per platform, real Telegram bot | ⬜ Needs your developer apps and bot token. `make preflight` now lists the exact redirect URIs and checks the bot's token and webhook |
| 0.4 | CI green on a hosted runner | ⬜ The repository has no commits or GitHub remote yet |
| 0.5 | Images build behind the corporate TLS proxy | ✅ Both images build with `CORP_CA_BUNDLE` (backend 162 s, web 426 s). The web image had no CA support, and `next/font/google` fetched fonts at build time from Turbopack, which ignores Node's CA settings; fonts are now self-hosted from pinned `@fontsource` packages (`next/font/local`), so builds make no font request at all. Clean-machine `make up` still to do |
| 0.6 | Backup restore drill | ✅ Dump 2 s, restore 15 s, everything matches; logged in the restore runbook |
| 0.7 | Playwright E2E of the approval loop | ✅ `make e2e`, `@playwright/test` 1.64.1 on the installed Chrome (no browser download). Brief → per-account drafts → web card → Approve all → staggered slots → a publish → Calendar, 3.3 min; cancels its leftover slots so repeat runs do not queue behind each other |
| 0.8 | Outbound email Notifier | ✅ `Notifier` port, SMTP adapter (STARTTLS/SSL, thread with timeout; links never queued or logged), in-memory capture for sandbox. Verification is emailed and no longer returned outside sandbox; a refused send is 503 `email_delivery_failed`. Invitations are emailed (`email_sent` in the response); the link is still shown once to the inviter. Tested over a real SMTP socket |
| 0.9 | Browser address and audit partitions | ✅ See below |
| 0.10 | Start WhatsApp verification | ⬜ Yours, if Q6 says WhatsApp is wanted |

**Found while doing 0.9: every user shared one address.** All browser traffic reaches the
API through the web server, so the API saw the web server's IP for everyone: the auth rate
limit (10 a minute per address) would have applied to the whole deployment at once, login
lockout per address was shared, and the audit log recorded one IP. The web server now
sends the browser's address (the right-most `X-Forwarded-For` entry) as
`X-Mera-Client-IP` on every proxied call, login, register, OAuth exchange and token
refresh, and the API believes it **only** with a matching `MERA_WEB_PROXY_SECRET`.
Production now refuses to start without that secret. A second hole was closed with it:
Compose passed an empty `WEB_PROXY_SECRET` when unset, and an empty secret matched an
empty header; blank secrets now count as unset and the proxy secret must be 16+ characters.

**Audit partitions:** migration 0013 adds `mera_ensure_audit_partitions(months)`, an
owner-owned `SECURITY DEFINER` function the app role may only execute. Each new monthly
partition gets forced RLS, the parent's two policies and no direct grants, all or nothing.
A month whose rows already sit in the default partition is skipped (moving rows would
rewrite an append-only log). A daily worker task keeps two months ready. The downgrade
folds partitions back into the default without losing a row.

**Also added:** `python -m mera.preflight` (`make preflight`), offline config checks plus
`--live` probes of the database (role, revision), Telegram (token, username, webhook URL,
last delivery error), the SMTP relay (login, optional test send) and the public URLs;
secrets never printed; exit 1 on any failure.

**Fixed on the way:** Calendar cards overflowed into the next day's column (grid items
without `min-w-0`); `approvals.py` had bare-CR line endings with doubled lines from a
Sprint 9 patch, which also put stray blank lines into six endpoint descriptions in the
OpenAPI spec (restored, SDK regenerated); a timing-sensitive audit-flood test now scales
its bound with elapsed time; a stale `noqa` and an unformatted `publisher.py`.

**Tests (2026-11-10):** backend 624 (623 passed in the full run; the one timing failure
was fixed and passes), 91.6% line coverage, every coverage gate passes; web 291 tests in
21 files; Playwright E2E 1 of 1; ruff, ruff-format, mypy, eslint, tsc, OpenAPI and SDK
drift all clean; both Docker images build.

**Your part, to finish P2-M0:**
1. Register the developer apps (GUIDE.md §5), put their credentials and a bot token in
   `.env`, set public HTTPS URLs (a tunnel is fine), then `make preflight` until clean.
2. One real post per platform (GUIDE.md §6) and one approval from a real phone.
3. Make the first commit and push to GitHub so CI runs.
4. Answer Q6, Q9 and Q10 in docs/13 §6.

---

## Phase 2 — P2-M1 to P2-M9 (docs/13)

Built in the plan's order with the defaults chosen in docs/13 §6: WhatsApp built but off
until configured; images through a pluggable provider (sandbox fake, OpenAI-compatible
API, or an approved MCP tool), video behind a flag with no provider yet; MCP servers over
HTTP and stdio, stdio only in the sandbox. Every decision is recorded in
[ADR-0016](docs/adr/0016-phase2-delivery-decisions.md) §1–43. Migrations 0014–0022.

| Milestone | What exists | Tests | Status |
| --- | --- | --- | --- |
| P2-M1 Phase 1 gaps | Validity from the approved slot, banned content rechecked at publish, posting times, Page picker, run abort/retry, alt-text suggestions, templates, hashtag registry (Library) | `test_phase2_m1*`, `phase2-m1-ui` | ✅ |
| P2-M2 Team and second factors | TOTP sign-in with recovery codes, approval PIN, action-bound single-use grants, Owner-only policy relaxations, Admins without MFA act as Editors, named escalation, out-of-office delegation, rate budgets that hold | `test_phase2_m2`, `test_totp_and_second_factor`, `mfa-bff`, `phase2-m2-ui` | ✅ |
| P2-M3 Bluesky and Mastodon | App-password and per-instance connects, facets with byte offsets, idempotent Mastodon posts | `test_phase2_m3`, `test_fediverse`, `phase2-m3-ui` | ✅ |
| P2-M4 WhatsApp | Same bot runtime with a WhatsApp adapter: buttons/lists, no edits, 24-hour window, two templates with a monthly cap, signed webhook, channel-bound pairing | `test_phase2_m4`, `test_whatsapp`, `phase2-m4-ui` | ✅ (no live number) |
| P2-M5 Media generation | Estimate before spend, monthly budget and per-request ceiling (second factor above it), jobs with timeout/retries, Media agent prompts and alt text, carousels as batches, regenerate from cards, spend in Settings | `test_phase2_m5`, `test_media_generation`, `phase2-m5-ui` | ✅ (sandbox provider; invoice match not checked) |
| P2-M6 Inbox and Engagement agent | Tapering comment poll (FB, IG, Threads, Mastodon, Bluesky), rules then agent triage, replies only as the commented account, hash-bound reply approval (web and cards), hide, delete with a second factor | `test_phase2_m6`, `test_engagement`, `phase2-m6-ui` | ✅ (X and LinkedIn comments not read: API tiers) |
| P2-M7 Scheduler agent and orchestration | Hour-of-week scores from engagement history, proposals only as input to `place`, property test over random proposals, resumable orchestration run with a run graph | `test_phase2_m7`, `test_phase2_m7_scheduling`, `voice-agents-ui` | ✅ |
| P2-M8 Mera's MCP server | Dual-era JSON-RPC over stdio and Streamable HTTP, OAuth 2.1 (CIMD, PKCE, resource-bound tokens refused by the REST API, incremental consent, rotation), tool list with no approve/resume/credential tool, URL elicitation for second factors, prompts, MCP screen and consent page | `test_phase2_m8`, `phase2-m8-ui` | ✅ (`subscriptions/listen` not offered) |
| P2-M9 MCP client registry | Review with second-factor approval, risk classes, poisoning flags, schema-change disabling, three-level default deny, approval-gated writes on cards, untrusted results, sandbox relay and egress proxy, external OAuth with `iss` checks, disable path, MCP image provider | `test_phase2_m9`, `test_mcp_sandbox`, `phase2-m9-ui` | 🟡 Relay token, one uid per server and registry-managed egress grants are tested; the container walls (gVisor, internal networks, capabilities) come from Compose and were not run here, and the sandbox subprocess test runs on Linux only |
| P2-M10 Hardening and release | Release security review (4 high, 6 medium, 7 low; one medium a false positive): all fixed with tests (ADR-0016 §38–42, migration 0022). Phase 2 Playwright journeys: an MCP client drafts and requests approval and a person approves in the web; an inbox reply approved and sent as the commented account; WhatsApp pairing from the Channels screen and approval from a WhatsApp card (fake Graph API). 50 accounts on 7 platforms with inbox polling. Two bugs found by these checks fixed (§43) | `test_phase2_load`, `phase2.spec.ts`, `approval-loop.spec.ts`, the per-finding tests | ✅ |

**Not yet verified for Phase 2:** live WhatsApp number and templates; a real image
provider and the 5% invoice check; real platform comment APIs; a real Claude client
against the HTTP endpoint; the sandbox and egress containers on a host with Docker
networking (Compose config renders; the images were not built or run here); refresh of
external MCP OAuth tokens ahead of expiry (they fail and need re-authorizing).

**Follow-ups left open by the release review (ADR-0016):** step-up through the identity
provider for accounts that sign in only with a provider (they enrol two-step sign-in
without a password); sandboxed code can reach `api:8000` on the `sandbox_rpc` network
(the same surface the API exposes publicly); the Scheduler agent moves a post with no
requested time to the account's best hour of the week, even on one or two past posts,
which can be days away (by design, docs/06 §4.8; worth a product look).

**Release checks (2026-11-13):** backend 733 tests pass (one skipped on Windows: the
sandbox subprocess relay runs on Linux), coverage gates pass at 89.8% overall. In the full
run that shared the machine with the browser tests, the per-IP login rate-limit test
crossed a one-minute window and failed; it passes on its own. Phase 2 Playwright
journeys 3 of 3 and the Phase 1 approval loop pass against the verify stack; the load check posts to 50 accounts on 7
platforms and polls them (first poll 10.3 s, second 9.6 s, listing 100 comments in 0.1 s);
web 327 tests in 30 files, lint and production build clean; coverage gates pass.

---

## Phase 3 — P3-M0 to P3-M8 (docs/14)

Started and built 2026-11-13. Decisions in [ADR-0017](docs/adr/0017-phase3-delivery-decisions.md).
Migrations 0023 to 0029.

| Task | What exists | Tests | Status |
| --- | --- | --- | --- |
| P3-M0.1–0.6 Phase 2 on live services, CI | Needs your WhatsApp number, image provider, platform apps, a Claude client, a Docker host and a remote | — | ⬜ |
| P3-M0.7 Scheduler agent minimums | A proposal needs a slot measured on 8+ posts and moves a post at most 24 h | `test_phase2_m7_scheduling` | ✅ |
| P3-M1.1 Hashtag attribution | `hashtag_stat` per account and tag, daily job, lift against the account median (capped), `GET …/hashtags/stats` with no lift below 10 posts, member scopes respected | `test_hashtag_lift`, `test_phase3_m1` | ✅ |
| P3-M1.2 Hashtag agent on evidence | The agent sees measured lift; basis, samples, lift and rank come from `hashtag_stat` (drafting and Suggest); Composer shows lift as a change against typical, nothing below 10 posts | `test_hashtag_lift`, `composer-lib`, `composer-ui` | ✅ |
| P3-M1.6 Confidence calibration | `GET …/analytics/confidence-calibration`: approved-as-written rate per confidence band, per account and workspace, `separates` once both ends have 10 decisions | `test_calibration`, `test_phase3_m1` | ✅ |
| P3-M1.3–1.5 Aggregates, insights, weights | Weekly aggregates (pillar, source, platform, account, group, turnaround), grounded Analytics agent narration with fallback, `insight` rows citing their evidence, weekly email; source and pillar weights at most 15% a run with history and exact revert, auto-tune switch (migration 0024) | `test_insights`, `test_phase3_m1` | ✅ Slot and hashtag weights not added (ADR-0017 §5) |
| P3-M1.7 Analytics screen | Insights (mark as seen), weights with history and Revert, Editor confidence bands, groups/accounts/platforms side by side | `phase3-lib`, browser check | ✅ |
| P3-M2 A/B tests | Version B as its own post set (0028), two-proportion test with 500 impressions a side, `too_early` below; Composer Experiments panel | `test_ab`, `test_phase3_m2` | ✅ |
| P3-M3 Assisted and Full Autopilot | Strictest-wins resolution, company accounts opt in, earned per account (30 approvals, under 10% edited, calibration for Assisted), Admin/Owner plus second factor, `autopilot_authorization` (0027), publish-time re-check, Stop notices; Automation screen modes | `test_phase3_m3`, `test_autopilot_resolution`, browser check | 🟡 No MCP `set_autopilot_mode`, no Undo, no daily unattended summary |
| P3-M4 Digests and webhooks | Per-member digest at local time (Telegram, WhatsApp `mera_daily_digest`, email), digest-only members get no cards; signed, SSRF-guarded webhooks with retries, auto-off after 20 failures, test and secret rotation (0026); Settings panels | `test_phase3_m4`, browser check | ✅ No "Approve all" in the digest |
| P3-M5 Cloning and evergreen | Clone copies setup only (walk of every tenant table), autopilot reset; evergreen refresh drafts after the cool-down; Composer evergreen switch | `test_phase3_m5` | ✅ |
| P3-M6 External MCP | Curated catalog plus optional MCP Registry search, install to review; per-binding call budgets; circuit breaker (0029) with degraded state; argument-description screening; `mcp` source type (MANAGE_MCP); MCP screen catalog and budgets, Sources form | `test_phase3_m6`, `test_mcp_catalog`, `test_phase2_m9` | 🟡 Tasks extension not built |
| P3-M7 Governance | Security alert scanner and email (0025), personal export and account deletion with 30-day erasure, workspace export zips, comment-author erasure, provider step-up (web hop + single-use proof), MCP OAuth refresh; Settings panels, step-up on Security | `test_phase3_m7`, `phase3-lib`, `phase3-ui` | 🟡 Step-up freshness (`prompt=login`) and platform token revoke are follow-ups; no Inbox button for author erasure |
| P3-M8 Hardening | Security review: 1 high + 5 medium fixed (autopilot release by clearing a level, per-account earning, wrapper break-out, MCP source authorization, rug-pull trust, fail-open delete); full suite and coverage gates; docs, ADR-0017 §5–12, runbooks (webhooks, data requests, WhatsApp digest template) | see Sprint log | 🟡 New E2E journeys and the load re-check not run |

## Sprint log

| Sprint | Scope | Result |
| --- | --- | --- |
| 1 | M1 Foundation, F1 design system and shell | ✅ 48 backend tests; web builds; Compose Postgres, migrations, API and worker verified by hand |
| 2 | M2 part 1 (auth, JWT, RBAC, audit), C8, F2 components | ✅ 193 backend tests, 94% coverage; 34 web tests; security review fixed (no critical findings) |
| 3 | M2 part 2 (API keys, OAuth, encryption, rate limits, idempotency, invitations, lifecycle, email verification), SDK pipeline, F4 auth wiring | ✅ 264 backend tests, 94% coverage; 112 web tests; security review: 3 high + 6 medium fixed, no critical; full browser run against the live stack |
| 4 | M3 Accounts and groups, member scopes, Accounts/Groups/Settings screens, carry-overs | ✅ 301 backend tests, 94% coverage; 151 web tests; security review: 4 medium + 6 low fixed (no critical/high), including a consent-phishing hole in account connection; full browser run against the live stack |
| 5 | M4 Ingestion (RSS, dedupe, LiteLLM, per-group scoring), source_group_link; Sources and Discovery screens | ✅ 384 backend tests, 93% coverage; 193 web tests; security review: 3 high + 9 medium + lows fixed (feed parsing on the event loop, gzip bombs, group-scope leaks among them); browser run with three real feeds |
| 6 | M5 part 1: post sets, variants, immutable versions and hashes, voices, Writer/Hashtag/Editor pipeline, guardrails; Composer, Brand Voice, Agents | ✅ 418 backend tests, 92% coverage; 262 web tests; **Checkpoint A** in the browser; security review: 5 high + 6 medium + lows fixed (approval-anchor invariants, fail-open duplicate checks, wedged generation, link and prompt injection, scope leaks, guardrail bypasses) |
| 7 | Platform skills (vendored LinkedIn/Facebook/Instagram/Threads/X writing skills, pinned and checksum-verified) in place of brand voices; account profiles; object store, media upload, per-platform renditions, Instagram media rule, article image; version diff; monthly workspace cap; Skills & Profiles screen and Composer media panel | 🟡 451 backend tests; 264 web tests; security review: 1 high + 6 medium + 5 low, high and mediums fixed (media delete race, decoder allow-list and decode bounds, pre-parse upload cap in API and BFF, atomic quota, signature hardening, hidden-text stripping in skills). Browser run against the live stack: Skills & Profiles lists six platforms with pinned skills and shows skill text; a profile with About and a story saves and reloads; sandbox Instagram + LinkedIn connected, a post drafted, Instagram blocked without an image, an upload saved with alt text as version 2 (4:5 crop, not upscaled), Instagram unblocked, preview shows the image, no console errors. Found and fixed on the way: the sandbox connector gave every platform a personal account, so Instagram (business only) could not connect; the upload size guard required a Content-Length the web proxy does not send (the byte counter alone now enforces the cap, and answers 413). A/B, templates, hashtag registry, editable agent definitions moved to Sprint 8 |
| 8 | M6 part 1: ADR-0014 (derived request state, scoped revocation, strictest caps, no Phase 1 PIN, people-only approvals); approval policy/request/per-account state/decisions/tokens/automation (0011); state machines with property tests; `ApprovalService`, `assert_publishable`, kill switch; Approval Queue, Composer "Send for approval", rail kill switch, live approvals badge; editable agent models; caps and quiet hours strictest-wins | 🟡 486 backend tests; 271 web tests; security review: 2 high + 7 medium + lows. Highs, mediums and several lows fixed (group membership could drop a kill switch or policy; settings-only approver counts had 694-day timers; quiet hours could loosen; scoped decision leak; stale "approved" set; API keys could approve; media not visible to the approver; expiry not recorded; token scope check; decision ledger trigger). Browser check pending: dev DB needs migration 0011 |
| 9 | M6 part 2: sweeper with quiet-hours shifting, edit flow (instruction / replacement, scoped revision, automatic re-ask), `edit`/`preview`/`variants`, disjoint open requests, re-request of remaining accounts | ✅ Sweeper and edit-flow integration suite; readiness bug fixed (a duplicate check that found a similar post was treated as "not checked") |
| 10 | M7: Telegram channel, card/detail/reminder/expiry/report renderers, pairing with lockout, commands, edit by reply, `/kill` scoped, webhook service, long polling, sandbox phone; Approval Channels screen | ✅ 12 bot integration tests (pairing, four-account card content, Approve all → four staggered slots, mixed outcome via detail views, edit by reply incl. injected text, expiry, scoped kill, forwarded card, scoped approver, web→bot sync, publish report, webhook secret) |
| 11–13 | M8: scheduler, publish worker, retries, reconciliation, dead letters, LinkedIn/Facebook/Instagram/Threads/X adapters and connectors, Calendar/Dashboard; M10 analytics and screen; SSE; Audit Log viewer | ✅ 11 publishing integration tests (exactly two of four publish, partial failure, lost response, 429, revoked account, group kill switch, edit after approval, calendar), 36 adapter/scheduling unit tests, 6 analytics tests |
| 14 | M11: seed, load check, every `assert_publishable` refusal, 10,000-sequence property test, coverage gates, CI and security workflows, runbooks, ADR-0015, README | 🟡 Local checks pass; CI, image builds, live platforms and a browser click-through still to do |
| P2 | P2-M1 to P2-M10 (see *Phase 2*) | ✅ 733 backend tests, coverage gates pass (89.8%); 327 web tests; release security review fixed; Phase 2 and approval-loop browser journeys and the 50-account load check pass. Live credentials still to try |
| 15 | P2-M0 go-live, the parts that need no outside credentials: email Notifier, browser address through the BFF, audit partitions (0013), preflight, Playwright E2E, restore drill, both images behind the TLS proxy, self-hosted fonts | 🟡 624 backend tests, 91.6% coverage, gates pass; 291 web tests; E2E passes. Live platforms, real bot and CI need your credentials and a remote |
