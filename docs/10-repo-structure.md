# 10 - Repository Structure, Tooling and Testing

How the code is laid out, how it runs locally, how sandbox mode works, and what "tested"
means for a system whose central guarantee is a safety property.

---

## 1. Monorepo layout

One repository, two language ecosystems, deliberately separated at the top level.

```
mera/
├── README.md
├── docker-compose.yml
├── docker-compose.override.yml.example
├── .env.example
├── Makefile                           # the only interface anyone needs to remember
├── .github/workflows/
│   ├── ci.yml
│   ├── release.yml
│   └── security.yml
│
├── docs/                              # this documentation set
│   ├── 00-overview.md ... 11-phase1-plan.md
│   ├── adr/
│   ├── api/openapi-v1-outline.yaml
│   └── runbooks/
│       ├── telegram-bot-setup.md
│       ├── whatsapp-business-setup.md
│       ├── connecting-social-accounts.md
│       └── incident-kill-switch.md
│
├── backend/
│   ├── pyproject.toml                 # uv, single project, src layout
│   ├── uv.lock
│   ├── alembic.ini
│   ├── alembic/versions/
│   └── src/mera/
│       │
│       ├── domain/                    # no I/O, no framework imports
│       │   ├── entities/              # Workspace, AccountGroup, PostSet, PostVariant...
│       │   ├── value_objects/         # ContentHash, CompositeHash, IdempotencyKey, Platform, Role
│       │   ├── state_machines/        # approval.py, approval_variant.py, post_set.py, scheduled_post.py
│       │   ├── policies/              # rbac.py, member_scope.py, settings_resolution.py,
│       │   │                          # autopilot.py, quorum.py, stagger.py
│       │   └── ports/                 # the six Protocols from 01-architecture section 5,
│       │                              # plus Notifier
│       │
│       ├── application/               # use cases, transactions, orchestration
│       │   ├── services/
│       │   │   ├── approval_service.py
│       │   │   ├── publish_service.py      # assert_publishable lives here
│       │   │   ├── post_set_service.py     # target expansion, fan-out, composite hash
│       │   │   ├── account_service.py      # connect, health, reauthorize
│       │   │   ├── group_service.py
│       │   │   ├── media_service.py        # upload, generation jobs, renditions
│       │   │   ├── hashtag_service.py
│       │   │   ├── ingestion_service.py
│       │   │   └── ...
│       │   ├── events/                # emit and subscribe
│       │   └── dto/
│       │
│       ├── infrastructure/
│       │   ├── db/
│       │   │   ├── models/            # SQLAlchemy ORM
│       │   │   ├── repositories/      # workspace-scoped, RLS-aware
│       │   │   ├── session.py         # sets mera.workspace_id per transaction
│       │   │   └── listen_notify.py
│       │   ├── platforms/
│       │   │   ├── base.py            # PlatformAdapter protocol and shared retry logic
│       │   │   ├── bluesky/
│       │   │   ├── mastodon/
│       │   │   ├── linkedin/          # member.py and organization.py
│       │   │   ├── x/
│       │   │   ├── threads/
│       │   │   ├── meta/              # instagram.py, facebook.py
│       │   │   ├── capabilities.py    # the capability descriptors
│       │   │   └── fake.py            # sandbox adapter, records everything
│       │   ├── channels/
│       │   │   ├── base.py
│       │   │   ├── telegram/
│       │   │   ├── whatsapp/
│       │   │   └── fake.py
│       │   ├── llm/
│       │   │   ├── litellm_provider.py  # the ONLY real LLMProvider implementation
│       │   │   ├── structured.py        # schema mode, tool-call fallback, retry loop
│       │   │   ├── pricing.py           # versioned price table with effective dates
│       │   │   └── fake.py              # deterministic, fixture-backed
│       │   ├── media/
│       │   │   ├── base.py            # MediaProvider protocol
│       │   │   ├── providers/         # one module per generation backend
│       │   │   ├── mcp_provider.py    # a registered MCP server as a MediaProvider
│       │   │   ├── renditions.py      # deterministic crop, resize, re-encode
│       │   │   └── fake.py            # returns fixture images, zero cost
│       │   ├── mcp_client/
│       │   │   ├── session.py         # dual-era: modern per-request _meta, legacy initialize fallback
│       │   │   ├── discovery.py       # tools/list, schema hashing, screening, ttlMs scheduling
│       │   │   ├── oauth.py           # PRM discovery, CIMD client, PKCE, RFC 8707, iss check
│       │   │   ├── importer.py        # mcpServers JSON -> pinned draft registrations
│       │   │   ├── catalog.py         # MCP Registry client plus the curated list
│       │   │   ├── gateway.py         # effective tool list, naming, budgets, circuit breaker
│       │   │   ├── sandbox_rpc.py     # relay to mcp-sandbox for stdio servers
│       │   │   └── fake.py            # scripted servers: benign, slow, hostile, rug-pull
│       │   ├── storage/               # filesystem.py, s3.py
│       │   ├── crypto/                # envelope.py, key_provider.py, approval_token.py
│       │   └── queue/                 # procrastinate app, task registry
│       │
│       ├── agents/
│       │   ├── base.py                # Agent[TIn, TOut] with step recording built in
│       │   ├── orchestrator.py
│       │   ├── research.py
│       │   ├── writer.py
│       │   ├── editor.py
│       │   ├── media.py
│       │   ├── hashtag.py
│       │   ├── approval.py
│       │   ├── scheduler.py
│       │   ├── publisher.py           # the one with no LLM
│       │   ├── engagement.py
│       │   ├── analytics.py
│       │   ├── guardrails/
│       │   └── prompts/               # versioned .md files, not inline strings
│       │
│       ├── api/
│       │   ├── main.py
│       │   ├── deps.py                # principal resolution, workspace context
│       │   ├── middleware/            # request id, rate limit, idempotency, audit
│       │   ├── errors.py              # the single error envelope
│       │   └── v1/routers/
│       │
│       ├── workers/
│       │   ├── main.py                # queue selection from argv
│       │   └── tasks/                 # ingest, agents, media, publish, accounts,
│       │                              # metrics, approvals, notify
│       │
│       ├── bots/
│       │   ├── main.py
│       │   ├── telegram/              # webhook, render, commands
│       │   └── whatsapp/              # webhook, templates, render
│       │
│       ├── mcp/
│       │   ├── server.py              # stdio and streamable http; server/discover; dual-era
│       │   ├── elicitation.py         # input_required: workspace pick, URL-mode second factor
│       │   ├── tools/
│       │   ├── resources/
│       │   └── prompts/
│       │
│       ├── config.py                  # pydantic-settings, fails fast
│       └── seed/                      # demo workspace fixtures
│
├── apps/web/                          # Next.js
│   ├── package.json
│   ├── app/(dashboard)/               # one route group per screen in docs section 7
│   ├── components/
│   ├── lib/sdk/                       # GENERATED, do not edit
│   └── tests/
│
├── packages/sdk/                      # generated TS SDK, published to the workspace
│
├── sandbox/                           # mcp-sandbox image: supervisor, seccomp profile, package cache
│
└── tests/
    ├── unit/
    ├── integration/
    ├── e2e/
    ├── contract/                      # recorded platform API interactions
    ├── evals/                         # agent quality regression
    └── fixtures/
```

### Why this shape

**`domain` imports nothing.** No SQLAlchemy, no FastAPI, no httpx. It is pure Python plus
Pydantic. The consequence worth caring about: the approval state machine and the quorum
logic can be tested exhaustively with no database and no mocking, which is what makes
thorough coverage of the safety-critical path affordable.

**`application` is reused by four entry points.** The API, the workers, the bot service,
and MCP all import the same services. `assert_publishable` exists once. If it existed
twice, one copy would eventually drift, and that drift is precisely the bug that breaks
the product's central promise.

**Adapters sit under `infrastructure`, each with a `fake.py` sibling.** Sandbox mode is
not a special code path; it is dependency injection picking a different implementation of
the same Protocol.

**`llm/` holds one real implementation, not one per vendor.** LiteLLM is the provider
abstraction, so `litellm_provider.py` is the whole of it. What remains Mera's is
`structured.py`, which bridges the gap between models that support native JSON-schema
output and ones that only support tool calling, and `pricing.py`, which computes cost from
a pinned table rather than trusting the library's estimate. See
[ADR-0010](adr/0010-litellm-as-llm-gateway.md).

**`domain/policies/settings_resolution.py` is small and load-bearing.** It is the single
implementation of the post → account → group → workspace chain, including the
strictest-wins exception for autopilot mode and approval policy. The API, the agents, and
the bots all call it. A second implementation anywhere would eventually disagree with the
first about whether a company page requires approval.

**Prompts are versioned files, not string literals.** `agents/prompts/writer.v3.md` can be
diffed, reviewed, and evaluated. A prompt change is a reviewable change, which it is not
when prompts live inside Python string concatenation.

---

## 2. Tooling

| Concern | Choice | Note |
| --- | --- | --- |
| Python packaging | `uv` | Fast, lockfile-first, one tool for envs and dependencies |
| Lint and format | `ruff` | Replaces flake8, isort, black |
| Types | `mypy --strict` on `domain` and `application`, standard elsewhere | Strict where correctness is load-bearing |
| Tests | `pytest`, `pytest-asyncio`, `testcontainers` | Real Postgres in integration tests |
| Migrations | `alembic` | |
| JS packaging | `pnpm` | Workspace for `apps/web` and `packages/sdk` |
| Web lint | `eslint` with a custom rule banning direct API `fetch` | Forces SDK use |
| E2E | Playwright | |
| Containers | Docker Compose locally | |
| Task runner | `make` | |

### The Makefile is the interface

```make
make setup        # install deps, create .env from example, pull images
make up           # docker compose up, migrate, seed the demo workspace
make down
make test         # unit + integration
make test-e2e
make test-evals   # agent quality regression, costs real tokens
make lint
make typecheck
make migrate name="add approval tokens"
make sdk          # regenerate the TypeScript SDK from the live spec
make seed
make logs service=worker
```

Nobody should need to remember a `docker compose exec` incantation to run a migration.

---

## 3. Docker Compose

Six services. The entire data tier is one container, which is the practical payoff of
[ADR-0002](adr/0002-single-postgres-datastore.md).

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16          # pinned by digest in the real file
    environment: [POSTGRES_DB=mera, ...]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck: pg_isready
    command: >
      postgres -c max_connections=200
               -c shared_preload_libraries=pg_stat_statements

  api:
    build: { context: ./backend, target: api }
    depends_on: { postgres: { condition: service_healthy } }
    ports: ["8000:8000"]
    volumes: ["./backend:/app", "media:/var/lib/mera/media"]

  worker:
    build: { context: ./backend, target: worker }
    command: ["python", "-m", "mera.workers.main",
              "--queues", "ingest,agents,media,publish,accounts,metrics,approvals,notify,integrations"]
    depends_on: { postgres: { condition: service_healthy } }
    volumes: ["./backend:/app", "media:/var/lib/mera/media"]

  bots:
    build: { context: ./backend, target: bots }
    ports: ["8001:8001"]
    depends_on: [api]

  mcp:
    build: { context: ./backend, target: mcp }
    ports: ["8002:8002"]
    depends_on: [api]

  mcp-sandbox:
    build: { context: ./sandbox }
    runtime: runsc                     # gVisor; falls back to rootless + seccomp in the override
    networks: [sandbox]                # reaches egress only
    read_only: true
    tmpfs: ["/tmp:size=64m"]

  egress:
    image: smokescreen                 # hostname allowlist per registration, SSRF deny-list
    networks: [sandbox, default]

  web:
    build: { context: ./apps/web }
    ports: ["3000:3000"]
    depends_on: [api]

volumes: { pgdata: {}, media: {} }
networks: { default: {}, sandbox: { internal: true } }
```

`max_connections=200` rather than the default 100, per the connection budget in
[01 - Architecture](01-architecture.md) section 6.3.

An optional `tunnel` service (Cloudflare Tunnel) is available in the override file for
exposing `/webhooks` during Telegram development.

### First run

```bash
git clone <repo> && cd mera
make setup        # writes .env, prompts for MERA_MASTER_KEY generation
make up           # ~90 seconds: build, migrate, seed
open http://localhost:3000     # demo workspace, seeded, sandbox mode on
```

Sandbox mode is the default in `.env.example`, so a fresh clone cannot post to a real
account by accident. Turning it off is a deliberate edit.

---

## 4. Configuration

`backend/src/mera/config.py` uses `pydantic-settings` and **fails at startup** on anything
missing or malformed. A service that boots with a weak master key and discovers the
problem at the first encrypt call is worse than one that refuses to boot.

```bash
# ---- core ----
MERA_ENV=development                 # development | staging | production
MERA_SECRET_KEY=                     # JWT signing, 32+ bytes
MERA_MASTER_KEY=                     # envelope encryption, exactly 32 bytes base64
                                     # generate: openssl rand -base64 32
MERA_PUBLIC_URL=http://localhost:3000
MERA_API_URL=http://localhost:8000

# ---- database ----
DATABASE_URL=postgresql+asyncpg://mera:mera@postgres:5432/mera
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=5

# ---- storage ----
OBJECT_STORE_BACKEND=filesystem      # filesystem | s3
OBJECT_STORE_PATH=/var/lib/mera/media
# S3_ENDPOINT= S3_BUCKET= S3_ACCESS_KEY_ID= S3_SECRET_ACCESS_KEY=

# ---- llm: every call routes through LiteLLM ----
# Supply a key for whichever providers you intend to use. Models are selected
# per agent as "<provider>/<model>" strings; nothing here is hardcoded in Mera.
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
# GEMINI_API_KEY=
# GROQ_API_KEY=
# MISTRAL_API_KEY=
# OPENROUTER_API_KEY=
# OLLAMA_API_BASE=http://host.docker.internal:11434

DEFAULT_MODEL=anthropic/claude-sonnet-4-5
DEFAULT_FAST_MODEL=anthropic/claude-haiku-4-5    # classification and triage
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
LITELLM_REQUEST_TIMEOUT=120
LITELLM_NUM_RETRIES=2
LITELLM_DROP_PARAMS=true             # silently drop params a given provider rejects

# ---- media generation ----
MEDIA_IMAGE_PROVIDER=                # e.g. replicate/..., openai/gpt-image-1
MEDIA_VIDEO_PROVIDER=
MEDIA_VIDEO_ENABLED=false            # opt in deliberately; clips cost dollars
MEDIA_GENERATION_DAILY_CAP_USD=5.00
MEDIA_CONFIRM_ABOVE_USD=0.50         # human checkpoint before spending more
# REPLICATE_API_TOKEN=
# FAL_KEY=

# ---- sandbox ----
SANDBOX_MODE=true                    # fake platform adapters and channels
SANDBOX_LLM=false                    # true uses fixture responses, zero cost
SANDBOX_MEDIA=true                   # fixture images instead of paid generation

# ---- approval channels ----
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=             # generated by make setup
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_BUSINESS_ACCOUNT_ID=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_WEBHOOK_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=

# ---- social platforms ----
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
X_CLIENT_ID=
X_CLIENT_SECRET=
BLUESKY_SERVICE_URL=https://bsky.social
# mastodon registers per instance at connect time

# ---- limits ----
DEFAULT_DAILY_POST_CAP=5             # per account, not per workspace
DEFAULT_AGENT_DAILY_COST_CAP_USD=5.00
WORKSPACE_MONTHLY_BUDGET_USD=50.00
MAX_ACCOUNTS_PER_WORKSPACE=25
MAX_ACCOUNTS_PER_GROUP=12            # above this the bot card degrades to one line each

# ---- multi-account safety ----
CROSS_ACCOUNT_SIMILARITY_BLOCK=0.90  # same platform, same post set
SAME_ACCOUNT_SIMILARITY_BLOCK=0.95   # against the last 90 days
DEFAULT_STAGGER_MIN_SECONDS=180
DEFAULT_STAGGER_MAX_SECONDS=900
MIN_SECONDS_BETWEEN_SAME_PLATFORM=120

# ---- observability ----
LOG_LEVEL=INFO
LOG_FORMAT=json
# OTLP/HTTP base URL; every Mera service exports structured application, runtime, and
# HTTP access logs to /v1/logs, plus traces to /v1/traces. Set both variables in every
# Coolify application (or in that application's shared environment), not only worker.
OTEL_EXPORTER_OTLP_ENDPOINT=
# Optional OTLP headers, including Grafana Cloud's Authorization=Basic%20... value.
OTEL_EXPORTER_OTLP_HEADERS=
SENTRY_DSN=
```

Production validation is stricter: `SANDBOX_MODE=true` with `MERA_ENV=production` is a
startup error, as is a `MERA_MASTER_KEY` matching the example value.

---

## 5. Sandbox mode

The feature that makes this system developable and testable at all.

```python
def build_platform_registry(settings: Settings) -> PlatformRegistry:
    if settings.sandbox_mode:
        return PlatformRegistry.all_fake(recorder=InteractionRecorder())
    return PlatformRegistry.real(...)
```

| Port | Real | Sandbox |
| --- | --- | --- |
| `PlatformAdapter` | Live platform APIs | `FakePlatformAdapter`: records calls, returns realistic IDs and permalinks, simulates rate limits and **per-account failures on demand** |
| `ApprovalChannel` | Telegram, WhatsApp | `FakeChannel`: renders the real card, stores it, exposes it at `/sandbox/messages` and in the UI |
| `LLMProvider` | LiteLLM to any provider | Optional `FakeLLM`: fixture-backed deterministic responses, zero cost, **distinct text per account** so differentiation checks are exercised |
| `MediaProvider` | Image and video backends | `FakeMediaProvider`: fixture images and a stub video, simulated job latency, zero cost |
| `McpClient` | External MCP servers via the gateway and sandbox | `FakeMcpServer`: scripted modern and legacy servers, including a slow one, one that returns injection text, one whose tool description is poisoned, and one that redefines a tool between discoveries |
| `ObjectStore` | Filesystem or S3 | Temp directory |
| `Notifier` | Email, Slack | Captured in memory |

The fake LLM returning **different text per account** is a small detail that matters: a
fake returning the same string every call would make every cross-account duplicate test
pass trivially while the real system failed. It returns deterministic but distinct output
keyed by `post_variant_id`, and a dedicated fixture returns deliberately similar text so
the guardrail's blocking path is exercised too.

The fake platform adapter can be told to fail **one named account**, which is how partial
publish failure is tested without waiting for a real outage.

### Why the fakes record rather than no-op

A `FakeChannel` that returns success tells you nothing. One that stores the fully rendered
approval card lets a test assert that the card still contains the source block, the
compliance flags, and the expiry. That is the difference between testing a state machine
and testing a product.

The sandbox UI at `/sandbox` shows every message the system would have sent and every
platform call it would have made, with a button to simulate a reply or a button tap. The
full approval loop is exercisable with no Telegram account.

### Sandbox is not a toy path

The same code renders the card, mints the token, validates it, and runs
`assert_publishable`. Only the final network call is swapped. A bug in the token validation
fails in sandbox exactly as it would in production, which is the entire point.

---

## 6. Testing

Target: **80% or better on core logic**, with "core" defined precisely rather than as a
global percentage.

### 6.1 Layers

| Layer | Scope | Speed | Dependencies |
| --- | --- | --- | --- |
| Unit | `domain` and `application` in isolation | milliseconds | none |
| Integration | Services against a real Postgres | seconds | testcontainers |
| Contract | Adapters against recorded platform interactions | seconds | recorded cassettes |
| E2E | Full stack in sandbox, browser plus bot simulation | minutes | Compose |
| Evals | Agent output quality | minutes, costs tokens | live LLM |

### 6.2 Coverage expectations by area

| Area | Target | Reason |
| --- | --- | --- |
| `domain/state_machines` | **100%** including every illegal transition | The approval state machines, request and per-variant, are the product |
| `domain/policies` | **100%** | RBAC, member scopes, settings resolution, quorum, stagger |
| `infrastructure/crypto` | **100%** | Token signing, variant scoping, envelope encryption |
| `application/services/publish_service` | **100%** | `assert_publishable` |
| `application/services/post_set_service` | **100%** | Target expansion and composite hashing decide what gets approved |
| `application/services` generally | 90% | |
| `agents` orchestration and guardrails | 85% | LLM calls themselves are faked |
| `infrastructure/platforms` | 80% | Contract-tested |
| `infrastructure/media` renditions | 90% | Deterministic, and wrong crops ship silently |
| `api/v1/routers` | 80% | Thin by design |
| Web UI | 70% | Component and E2E |

`post_set_service` joins the 100% list because target expansion decides *which accounts a
post goes to* and composite hashing decides *what an "approve all" means*. A bug in either
is a bug in the approval guarantee, even though neither looks like security code.

### 6.3 The approval test matrix

Non-negotiable. These are the scenarios from
[07 - Approval Bots](07-approval-bots.md) section 11, implemented as integration tests
against fakes, plus their state-machine unit equivalents:

```
test_approval/
├── test_happy_path.py              request, approve, publish once
├── test_double_tap.py              second tap -> already_decided, one publish
├── test_edit_reapprove.py          v1 superseded, v1 tokens revoked, v2 publishes
├── test_stale_token.py             v1 token after v2 -> content_mismatch
├── test_replay.py                  captured payload replayed -> already_used
├── test_forwarded_card.py          foreign chat id -> identity_mismatch
├── test_expiry.py                  EXPIRED, nothing published, draft retained
├── test_expiry_quiet_hours.py      timers shift, usable window preserved
├── test_escalation.py              reminder, escalate, primary still valid
├── test_quorum_all_of.py           two approvers both required
├── test_quorum_veto.py             one reject terminates
├── test_kill_switch.py             queued posts held, approvals cancelled
├── test_webhook_signature.py       bad signature -> 401, no state change
├── test_webhook_duplicate.py       same update_id processed once
├── test_prompt_injection.py        hostile edit reply -> no tool call, no publish
└── multi_account/
    ├── test_approve_all.py             4 accounts, one tap, 4 staggered publishes
    ├── test_partial_approval.py        approve 2, reject 2 -> PARTIALLY_APPROVED
    ├── test_per_account_edit.py        edit 1, siblings keep their approvals
    ├── test_composite_hash_invalid.py  edit 1, "approve all" token -> content_mismatch
    ├── test_sibling_token_survives.py  edit 1, sibling's scoped token still valid
    ├── test_disable_variant.py         disabling changes composite, per-account survive
    ├── test_mixed_policy.py            company page needs 2 approvers, others need 1
    ├── test_scoped_approver.py         out-of-scope account -> approver_not_authorized
    ├── test_partial_expiry.py          2 approved publish, 2 pending expire
    ├── test_wrong_account_hash.py      version approved for A cannot publish to B
    └── test_double_tap_one_account.py  re-tap one account, siblings still actionable
```

**A property-based test** backs the state machine: generate random sequences of events
against a random initial state and a random number of target accounts, then assert two
invariants — *no sequence without a valid approval reaches a published state*, and *no
variant publishes under an approval issued for a different variant*. Example-based tests
cover the cases we thought of; this covers the ones we did not.

### 6.4 Multi-account posting tests

The second non-negotiable suite, because it covers the mechanics the product's main new
capability rests on.

```
test_post_sets/
├── test_target_expansion.py        groups + accounts + exclusions -> deduplicated variants
├── test_overlapping_groups.py      account in two selected groups -> one variant
├── test_routing_group_recorded.py  via_account_group_id drives settings resolution
├── test_unique_per_account.py      two X accounts produce measurably different text
├── test_cross_account_block.py     similar siblings -> blocked, regenerated, passes
├── test_differentiation_limit.py   two failed attempts -> human checkpoint, no loop
├── test_stagger_fixed.py           offsets follow group position
├── test_stagger_random.py          offsets within bounds, stored, reproducible on retry
├── test_stagger_required.py        2 X accounts + mode none -> blocked with the setting named
├── test_partial_failure.py         1 of 4 fails -> 3 publish, set partially_published
├── test_retry_isolation.py         the failing account retries alone
├── test_per_account_caps.py        one account at its cap does not block the others
└── test_settings_resolution.py     post > account > group > workspace, strictest for autopilot
```

### 6.5 Tenancy and scope tests

Also non-negotiable. For every repository method, a test creates two workspaces, writes to
one, queries as the other, and asserts zero rows. A test fixture enumerates repository
methods and fails if any lacks such a test, so a new repository cannot be added without
one.

A second test connects as the application role with RLS active and attempts a raw
cross-tenant query, asserting zero rows. This verifies the second layer independently of
the first.

A third suite covers **member scopes**, which RLS cannot enforce. For every service method
that touches account-scoped data, a test acts as a principal scoped to group A against a
subject in group B and asserts a denial. A fixture enumerates those methods and fails if
one lacks the test. Isolation across workspaces and isolation across groups are verified
separately because they are enforced by different mechanisms and fail in different ways.

The cross-surface version matters most: the same scoped principal is driven through REST,
MCP, and a bot callback against the same out-of-scope account, and all three must deny.
One surface forgetting the check is the realistic failure, not the policy function being
wrong.

### 6.5.1 External MCP tests

- **Era detection:** the client against fake modern, legacy and mixed servers on both
  transports, asserting the fallback rules from [05 - MCP](05-mcp.md) section 2.1, and
  that an HTTP+SSE-only server fails the test with a clear message.
- **Default deny:** for every agent, a tool enabled but unbound, bound but disabled, and
  bound and enabled but out of group scope must all be absent from the model's tool list.
  Binding the Publisher or Approval agent must be rejected.
- **Gated writes:** a write-tool call creates an `mcp_tool_call` approval. Changing one
  byte of the arguments, or redefining the tool's schema while the request is pending,
  makes the approval unusable. Expiry never executes the call.
- **Rug-pull:** re-discovery with a changed schema disables the tool and suspends its
  bindings in the same transaction.
- **Sandbox:** a fixture stdio server that tries to read `/etc`, open a socket to
  Postgres, or reach a non-allowlisted host must fail each attempt, and each attempt
  must appear as a security event.
- **Propose-only:** `add_mcp_server` over MCP never yields an `active` registration, and
  `set_mcp_permissions` enabling a write tool returns `input_required` with a URL
  elicitation, never a success.
- **Mera's own server:** protocol tests, plus the official MCP conformance tests
  where they apply, against both the `2026-07-28` and `2025-11-25` revisions.

### 6.6 Contract tests

Platform adapters are tested against recorded interactions (VCR-style cassettes), captured
once against the real API and committed with secrets scrubbed. This catches adapter
regressions without hitting the network or needing live credentials in CI.

A separate, manually triggered **live smoke suite** runs against real test accounts to
catch platform-side changes that cassettes would hide. It runs weekly rather than per
commit.

### 6.7 Agent evaluations

Agent output quality cannot be unit tested, so it is evaluated:

```
tests/evals/
├── writer/
│   ├── cases.yaml          30 source articles with brand voices
│   └── rubric.md           grounding, voice match, platform fit, no banned phrases
├── differentiation/
│   ├── cases.yaml          one article, two accounts on the same platform
│   └── rubric.md           measured similarity, and whether the angle genuinely differs
├── editor/
│   ├── cases.yaml          drafts with planted factual errors, PII, banned words,
│   │                       and planted near-duplicate sibling pairs
│   └── expected.yaml       which issues must be caught
├── hashtag/
│   ├── cases.yaml          posts with synthetic tag-performance history
│   └── expected.yaml       must prefer measured tags, must not claim evidence below sample
└── engagement/
    ├── cases.yaml          comments with known intent labels
    └── expected.yaml
```

Three evaluations carry real weight.

**Editor recall on planted errors** is the headline number: planted mistakes with known
locations give a measurable figure, and a drop between model versions is a concrete
regression rather than a vibe.

**Differentiation** is the new one, and it is harder than it looks. The metric is not
"are the two posts different strings" but "do they take different angles", measured as
embedding similarity plus a rubric judgement on whether the second post would be worth
reading by someone who read the first. A model that passes the similarity threshold by
shuffling sentences fails the rubric.

**Hashtag honesty** checks a specific failure: recommending a tag as evidence-backed when
its sample size is below the threshold. The eval plants thin history and asserts the agent
falls back to `platform_convention` rather than quoting a lift from four posts.

Evals run on prompt changes, model changes, and nightly, not on every commit, because they
cost real tokens. Since the model is now a configuration string, "model changes" happens
more easily than it used to, which makes this gate more load-bearing than it was.

### 6.8 Seed data

`make seed` creates a demo workspace that is actually usable for development, matching the
brief's example shape:

- **Two workspaces**: "Personal Brand" and "Acme (client)", so workspace switching and
  isolation are exercisable from the first run
- Three users: an owner, an approver, and an **editor scoped to one group**, so member
  scoping is visible rather than theoretical
- **Five social accounts** in the Acme workspace: 2 LinkedIn (one personal, one company
  page), 1 Facebook Page, 2 X (`@main` and `@tech`) — all sandbox-backed
- **Two account groups**: "Tech News" (both LinkedIn, both X, the Page) and "Company
  Official" (the company page and the Page only), with the company page in both, which is
  what exercises overlapping membership
- Different stagger settings per group, and an account-level autopilot override on the
  company page pinned to Manual
- Three content pillars with embeddings, one of them group-scoped
- Six RSS sources, two linked to a specific group
- Three brand voices: workspace, group, and account level, so resolution is demonstrable
- **Post sets in every state**, including one `partially_approved` and one
  `partially_published` with a failed account in the dead-letter queue
- Two approval requests mid-flight, one near expiry, one escalated, one with mixed
  per-account decisions
- Uploaded and generated media, with per-platform renditions, and one failed `media_job`
- Hashtag history with varied sample sizes, including tags below the evidence threshold
- Ninety days of synthetic metric snapshots per account with **deliberately different
  performance between the two X accounts**, so account comparison and group rollups have
  something true-shaped to work on

The ninety days of metrics matter. Building analytics against an empty table produces
charts that look fine and break on first contact with real variance. The *differing*
performance between accounts matters for the same reason: a seed where every account
performs identically makes the comparison views look correct while hiding whether they
actually group and aggregate properly.

---

## 7. CI

```mermaid
flowchart LR
  PR[Pull request] --> L[lint + typecheck]
  L --> U[unit tests]
  U --> I[integration, testcontainers]
  I --> C[contract tests]
  C --> S[SDK drift check]
  S --> SEC[pip-audit, npm audit, secret scan]
  SEC --> E[E2E in sandbox]
  E --> Cov[coverage gates by area]
  Cov --> Build[build and push images]
```

| Stage | Blocking | Typical duration |
| --- | --- | --- |
| Lint and format | yes | 30s |
| Typecheck, strict on domain | yes | 1m |
| Unit | yes | 1m |
| Integration | yes | 3m |
| Contract | yes | 1m |
| **SDK drift** | yes | 30s |
| Security scan | yes on high severity | 2m |
| E2E sandbox | yes | 5m |
| Coverage gates | yes, per-area thresholds | - |
| Evals | no, nightly and on prompt changes | 10m |
| Live platform smoke | no, weekly | 5m |

**SDK drift** regenerates the TypeScript SDK from the live OpenAPI spec and fails if the
committed output differs. This is what keeps the API contract and its clients from
separating quietly.

**Coverage gates are per area**, not global. A global 80% can be satisfied while the
approval state machine sits at 40%, which would be the worst possible way to hit the
number.

---

## 8. Runbooks

`docs/runbooks/` carries the procedures that are needed rarely and urgently:

| Runbook | Covers |
| --- | --- |
| `telegram-bot-setup.md` | BotFather, token, webhook registration, tunnelling locally, pairing, troubleshooting |
| `whatsapp-business-setup.md` | Business verification, number, the nine templates with submission text, webhook config, pricing alerts |
| `connecting-social-accounts.md` | Per platform: developer app creation, scopes, review submission, **connecting several accounts from one app**, shared-quota implications, common rejection reasons |
| `account-groups-and-staggering.md` | Designing groups, the settings-resolution order, why two accounts on one platform force staggering, and what the duplicate guardrail blocks |
| `adding-an-mcp-server.md` | The four ways to add a server (form, JSON import, catalog, over MCP), OAuth consent, testing the connection and era, reviewing risk classes and approval policies, group scoping, binding to agents, pinning versions, egress allowlists, reading the call history |
| `media-generation-setup.md` | Choosing providers, cost ceilings, enabling video, and the per-platform rendition pipeline |
| `incident-kill-switch.md` | Engaging from each surface, what it stops, how to verify, how to resume |
| `key-rotation.md` | Rotating the master key, re-encrypting by key version, rotating downstream credentials |
| `restore-from-backup.md` | `pg_dump` restore, media volume restore, what to re-verify afterwards |

The backup runbook is short, which is the one genuine operational benefit of the
single-datastore decision: a `pg_dump` plus the media directory is the entire backup.
