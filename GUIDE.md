# Mera — Operator Guide

How to run Mera, try it in sandbox mode, connect real accounts, set up the Telegram
approval bot and make your first real post. For the design, see [README.md](README.md)
and `docs/`; for what is built and what is left, see [PROGRESS.md](PROGRESS.md).

**Status:** Phase 1 is built and tested in **sandbox mode** (fake platforms, a simulated
phone). It has **not yet posted to a real account**: the platform code follows each
platform's documented API and is contract-tested, but the first live post on each platform
is still to be done (section 6). Expect small fixes on that first run.

---

## 1. What you need

| Tool | Version | Notes |
| --- | --- | --- |
| Docker Desktop | current | Runs Postgres (and, with `make up`, every service). Integration tests need it too |
| Python | 3.11 | Through [`uv`](https://docs.astral.sh/uv/) |
| Node.js | 24 | With pnpm 9: `corepack enable pnpm` (or call it as `corepack pnpm …`) |
| make | any | Optional. On Windows use `scripts/dev.ps1 <target>` instead (same targets) |

Behind a TLS-intercepting corporate proxy (Zscaler and the like), image builds need your
organisation's root CA as a PEM file: `export CORP_CA_BUNDLE=/path/to/ca.pem` before
`make up`. Both images use it at build time only (Python packages for the backend; pnpm
for the web app) and it never ends up in an image. Fonts are bundled with the web app, so
the build makes no request to Google Fonts. On Windows, find the CA that signs your
traffic (`openssl s_client -connect pypi.org:443 -showcerts`, look at the last issuer)
and export it from the certificate store, for example:

```powershell
$c = Get-ChildItem Cert:\LocalMachine\Root | Where-Object Subject -match 'Enterprise Root CA'
"-----BEGIN CERTIFICATE-----`n$([Convert]::ToBase64String($c.RawData, 'InsertLineBreaks'))`n-----END CERTIFICATE-----" |
  Set-Content -Encoding ascii $HOME\corp-ca.pem
```

---

## 2. What the Makefile does

The Makefile is the only interface you need to remember (docs/10 §2). On Windows,
`.\scripts\dev.ps1 <target>` runs exactly the same commands.

| Command | What it runs | When to use it |
| --- | --- | --- |
| `make setup` | Copies `.env.example` to `.env` **if `.env` does not exist yet**, then `uv sync` (Python packages into `backend/.venv`) and `pnpm install` (web and SDK packages) | Once, after cloning; again after dependencies change |
| `make up` | `docker compose up -d --build --wait`: builds the images and starts **postgres → migrate → api, worker, seed → bots, web**, waiting until healthy | Run the whole stack locally |
| `make down` | `docker compose down`: stops the stack (data is kept in Docker volumes) | Stop everything |
| `make logs service=worker` | `docker compose logs -f <service>` | Follow one service's logs (`api`, `worker`, `bots`, `web`, `seed`, `migrate`, `postgres`) |
| `make test` | `cd backend && uv run pytest`: every backend test (unit + integration) | Before a change lands |
| `make test-unit` | Backend unit tests only (no Docker needed) | Fast feedback |
| `make test-integration` | Backend integration tests (start a throwaway Postgres in Docker) | Database behaviour, approvals, publishing, bot |
| `make lint` | `ruff check` + `ruff format --check` (backend) and `eslint` (web) | Style and static checks |
| `make typecheck` | `mypy` (backend) and `tsc` (web, SDK) | Type checks |
| `make migrate name="add x"` | `alembic revision --autogenerate -m "add x"`: **writes a new migration file**; it does not apply anything | When you change a database model |
| `make web-dev` | `pnpm --filter web dev`: the web app with hot reload on port 3000 | Working on the web UI |
| `make preflight` | `python -m mera.preflight --live` inside the running `api` container: checks the settings for a real deployment and probes the database, Telegram, the mail relay and the public URLs. Prints what to fix; never prints a secret; exits 1 on any failure | Before the first real post, and after every configuration change |
| `make e2e` | Playwright browser test of the approval loop against the running stack, using your installed Chrome (no browser download) | After `make up`, before a release |

What `make up` starts:

| Service | Port | Role |
| --- | --- | --- |
| `postgres` | 5432 (`POSTGRES_HOST_PORT`) | The only datastore: data, vectors, job queue, events |
| `migrate` | — | One-shot: applies database migrations, then exits |
| `api` | 8000 | REST API, `/api/v1/docs`, `/healthz`, `/readyz` |
| `worker` | — | Every background queue: ingest, drafting, publishing, approvals, metrics, accounts |
| `seed` | — | One-shot demo data (sandbox only; skips if already seeded) |
| `bots` | 8001 | Receives Telegram webhooks |
| `web` | 3000 | The web app |

Other commands you may want (run from `backend/`, loading the root `.env`):

```bash
uv run --env-file ../.env alembic upgrade head            # apply migrations
uv run --env-file ../.env python -m mera.seed             # demo data (sandbox only)
uv run --env-file ../.env python -m mera.bots.poll        # Telegram long polling (local)
uv run python scripts/export_openapi.py                   # refresh the OpenAPI spec
uv run python scripts/coverage_gates.py coverage.json     # per-area coverage floors
```

---

## 3. Quick start in sandbox mode

Sandbox mode is the default. It uses fake platform accounts, a simulated Telegram phone
and an offline writer, so nothing can reach a real account and no API keys are needed.

```bash
make setup
make up
```

Open <http://localhost:3000> and sign in as the seeded demo user:

- **Email:** `demo@example.com`
- **Password:** `Mera demo password 2026!`

The **Acme** workspace has five accounts in two overlapping groups (*Tech News* and
*Company Official*), 90 days of analytics history, and one post set waiting for approval.

### See the whole loop

1. **Approval Channels → Get a pairing code.**
2. Open **Sandbox inspector** (`/w/acme/sandbox`) and send `/pair MERA-XXXX-XXXX`. The
   waiting post set's card arrives at once.
3. Tap **✅ Approve all 4** (or a number for one account's full text and its own
   Approve / Reject / Edit buttons).
4. Open **Calendar**: one slot per account, minutes apart (the stagger).
5. Within a minute of each slot the worker publishes it. The inspector's **Platform calls**
   column shows what each account would have posted; the phone gets a per-account
   **Published** report.

To watch a failure on one account, connect a sandbox account whose handle contains `fail`,
`flaky`, `ratelimit`, `lost` or `revoked`: its siblings still publish.

### Running without Docker for the app (Postgres only)

```bash
docker compose up -d postgres
cd backend
uv run --env-file ../.env alembic upgrade head
uv run --env-file ../.env python -m mera.seed
uv run --env-file ../.env uvicorn mera.api.main:app_factory --factory --port 8000
uv run --env-file ../.env python -m mera.workers.main            # second terminal
cd ../apps/web && corepack pnpm dev                                # third terminal
```

The web app finds the API at `http://localhost:8000` unless `MERA_API_URL` says otherwise.

---

## 4. Where secrets go

| Place | What goes there |
| --- | --- |
| **`.env`** (repo root, git-ignored) | Every real value: keys, app secrets, bot token. Created from `.env.example` by `make setup` |
| **`.env.example`** (committed) | Safe sandbox defaults and documentation only. **Never put a real secret here** |
| **A secret manager** (servers) | In production, inject the same variables from Docker/Compose secrets or your cloud's secret store |
| **The database, encrypted** | Each connected account's access and refresh tokens. Mera stores them itself, encrypted with `MERA_MASTER_KEY`; you never handle them |

Optional secrets in `.env.example` are **commented out**. An empty value counts as set (an
empty `TELEGRAM_BOT_TOKEN=` makes the API refuse to start), so uncomment a line only when
you fill it in.

Generate the core keys once, for anything beyond a laptop demo:

```bash
openssl rand -base64 48    # MERA_SECRET_KEY
openssl rand -base64 32    # MERA_MASTER_KEY  (back this up, separately from the database)
openssl rand -base64 32    # MERA_WEB_PROXY_SECRET  (required in production)
```

`MERA_WEB_PROXY_SECRET` is how the API tells the web app's requests from anyone else's.
With it, the API sees each browser's own address. Without it every user shares the web
server's address, so one person's failed sign-ins (10 auth requests a minute per address)
lock everybody out and the audit log records one IP for all of them. Production refuses
to start without it.

**Email** (verification and invitation links) goes through any SMTP relay: set `SMTP_HOST`,
`SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USERNAME`, `SMTP_PASSWORD` and `SMTP_FROM` in `.env`.
Without it, email verification is refused outside sandbox and invitation links are shown
to the inviter to pass on. Test it with
`make preflight args="--live --send-test-email you@example.com"`.

**`MERA_MASTER_KEY` is the one secret you must not lose.** Without it, stored account
tokens cannot be decrypted and every account has to be reconnected. Production refuses to
start with the example keys, with sandbox on, or with Telegram long polling.

---

## 5. Connecting real accounts

### 5.1 Before you start

1. In `.env`: `SANDBOX_MODE=false`. In sandbox mode Mera uses fake connectors whatever
   credentials are set.
2. `MERA_API_URL` must be a **public HTTPS** address: OAuth redirects come back to it, and
   Instagram and Threads download your images from it. Locally, use a tunnel:
   `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`, then set
   `MERA_API_URL` (and `MERA_PUBLIC_API_URL`) to the tunnel's HTTPS URL.
3. To draft with real models, also set `SANDBOX_LLM=false` and `ANTHROPIC_API_KEY` /
   `OPENAI_API_KEY`. (You can keep the offline writer while testing publishing.)
4. Restart the API and the worker after any `.env` change.
5. Run `make preflight` (Windows: `.\scripts\dev.ps1 preflight`). It lists the exact
   redirect URIs to register for each configured platform app and flags anything that
   would stop a real post: sandbox still on, non-HTTPS URLs, a missing secret, a Telegram
   webhook pointing elsewhere.

### 5.2 Callback (redirect) URLs

Create **one developer app per platform**; every account on that platform connects through
it. Register these redirect URIs exactly (replace `https://api.example.com` with your
`MERA_API_URL`):

| Platform in Mera | Redirect URI |
| --- | --- |
| LinkedIn (profile) | `https://api.example.com/api/v1/social-accounts/oauth/linkedin_member/callback` |
| LinkedIn Page | `https://api.example.com/api/v1/social-accounts/oauth/linkedin_organization/callback` |
| Facebook Page | `https://api.example.com/api/v1/social-accounts/oauth/facebook/callback` |
| Instagram (business) | `https://api.example.com/api/v1/social-accounts/oauth/instagram/callback` |
| Threads | `https://api.example.com/api/v1/social-accounts/oauth/threads/callback` |
| X | `https://api.example.com/api/v1/social-accounts/oauth/x/callback` |

Telegram webhook (if you use webhook mode):
`https://api.example.com/api/v1/webhooks/telegram`, routed to the `bots` service.

### 5.3 Per platform

| Platform | Where to create the app | `.env` variables | Scopes Mera requests |
| --- | --- | --- | --- |
| LinkedIn profile | LinkedIn Developer portal; add *Sign In with LinkedIn using OpenID Connect* and *Share on LinkedIn* | `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | `openid profile w_member_social` |
| LinkedIn Page | Same app; needs *Community Management API* access approved | same as above | `openid profile w_organization_social r_organization_social rw_organization_admin` |
| Facebook Page | Meta for Developers, a Business app with *Facebook Login* | `META_APP_ID`, `META_APP_SECRET` | `pages_show_list pages_manage_posts pages_read_engagement read_insights` |
| Instagram | Same Meta app; the Instagram account must be a **business** account linked to a Facebook Page | same as above | `instagram_basic instagram_content_publish instagram_manage_insights pages_show_list pages_read_engagement business_management` |
| Threads | Meta for Developers, the *Threads API* use case | `THREADS_APP_ID`, `THREADS_APP_SECRET` | `threads_basic threads_content_publish threads_manage_insights threads_manage_replies` |
| X | X Developer Portal; OAuth 2.0, type *Web App*, read and write | `X_CLIENT_ID`, `X_CLIENT_SECRET` | `tweet.read tweet.write users.read media.write offline.access` |

Notes:

- **Several accounts on one platform:** connect each one through the same app, signed in as
  that account. Each keeps its own encrypted grant.
- **Facebook with several Pages:** Mera connects the first Page the login grants. To
  connect another Page, reconnect and grant only that Page in Meta's dialog.
- **X:** the posting quota is shared by every account on your app's tier.
- **Token refresh** is automatic (an accounts sweep every five minutes). A revoked grant
  puts only that account into *Needs reconnecting*; its posts are held, not failed, and
  its siblings keep publishing.

### 5.4 Connect

Web app → **Connected Accounts → Connect → (platform)** → sign in on the platform's page →
approve. You land back on Connected Accounts with the account listed. Rename it to
something you will recognise on a phone (e.g. "Work LinkedIn"), then add it to a group
under **Groups**.

---

## 6. Your first real post

Do this with **one platform and one test account** before anything else.

1. Connect the account (section 5). LinkedIn is the easiest first target.
2. **Composer → New post**: a topic, target that one account. Wait for the draft.
3. Read it, edit if needed, **Send for approval**.
4. Approve it in the **Approval Queue** (or from Telegram, section 7).
5. Watch **Calendar**: the slot publishes within a minute of its time. The Dashboard shows
   it under *Published*, with a link to the live post.
6. If it fails, the error is shown on the Calendar and the Dashboard (and in the bot's
   report). Fix the cause, then **Retry**: every check runs again.

Things to know for the first runs:

- **Approvals are valid for 24 hours.** Schedule within a day of approving, or the post is
  refused at publish time (`approval_stale`) and needs approving again.
- **Only Manual mode exists in Phase 1:** every post needs a person's approval of its
  exact text, per account. Editing an approved post cancels its slot and asks again.
- **Two accounts on one platform** always go out minutes apart (the stagger), and a set
  with near-identical text for them is refused before approval.

---

## 7. Telegram approval bot

1. Message `@BotFather` → `/newbot` → choose a name and a username ending in `bot`.
2. In `.env`:

   ```bash
   TELEGRAM_BOT_TOKEN=123456:ABC…          # from BotFather
   TELEGRAM_BOT_USERNAME=your_mera_bot      # without @
   TELEGRAM_WEBHOOK_SECRET=…                 # openssl rand -hex 24
   TELEGRAM_MODE=polling                     # local; use webhook on a server
   ```

3. **Local (no public HTTPS):** keep a worker running and start long polling:
   `cd backend && uv run --env-file ../.env python -m mera.bots.poll`.
   **Server:** route `/api/v1/webhooks/` to the `bots` service before the general `/api`
   rule, then register the webhook once:

   ```bash
   curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
     -d url="https://api.example.com/api/v1/webhooks/telegram" \
     -d secret_token="$TELEGRAM_WEBHOOK_SECRET" \
     -d 'allowed_updates=["message","callback_query"]'
   ```

4. Web app → **Approval Channels → Get a pairing code** → tap the one-tap link or send
   `/pair MERA-XXXX-XXXX` to your bot within ten minutes. One pairing covers every
   workspace you belong to. **Send test message** confirms the round trip.

Bot commands: `/queue`, `/today`, `/stats`, `/groups`, `/accounts`, `/workspaces`,
`/switch NAME`, `/pause [group|account]`, `/kill [group|account]`, `/kill all`, `/whoami`,
`/help`. Resuming after a stop is web-only and needs an Admin.

Full details: [docs/runbooks/telegram-bot-setup.md](docs/runbooks/telegram-bot-setup.md).

---

## 8. Day-to-day operations

| Need | Where |
| --- | --- |
| Stop everything now | Rail → **Stop all automation**, or `/kill` in Telegram. Posts are held, not failed |
| Stop one group or account | **Automation** → Stop on that row, or `/kill Tech News` |
| Resume | **Automation** → Resume (Admins only) |
| A post failed | **Dashboard** or **Calendar** → the error and **Retry** / **Dismiss** |
| Move a post | **Calendar** → drag to another day, or open it and pick a date and time |
| Who approved what text | **Audit Log** (Admins): actor, surface (web/telegram), account, text hash |
| Performance | **Analytics**: per account against its own baseline; "Not reported" where a platform gives no number |
| Back up | `pg_dump` plus the media volume, and keep `MERA_MASTER_KEY` safe: [restore runbook](docs/runbooks/restore-from-backup.md) |
| Check the deployment | `make preflight` |
| Audit log partitions | Created automatically: a daily worker task keeps the next two months ready, each with the same row-level security as the parent |

Runbooks: [kill switch](docs/runbooks/incident-kill-switch.md),
[groups and staggering](docs/runbooks/account-groups-and-staggering.md),
[connecting accounts](docs/runbooks/connecting-social-accounts.md),
[WhatsApp](docs/runbooks/whatsapp-business-setup.md),
[Mera from Claude](docs/runbooks/mcp-server-for-claude.md),
[registering an MCP server](docs/runbooks/registering-an-mcp-server.md),
[revoking a compromised MCP server](docs/runbooks/revoking-a-compromised-mcp-server.md),
[two-step sign-in and recovery](docs/runbooks/mfa-recovery.md).

---

## 9. Troubleshooting

| Symptom | Check |
| --- | --- |
| `make up` fails building images | Corporate proxy: set `CORP_CA_BUNDLE` to your root CA's PEM (section 1) |
| Everyone gets "Too many failed sign-in attempts" | `MERA_WEB_PROXY_SECRET` is unset or differs between `api` and `web` (section 4) |
| "We couldn't send the email just now" | The SMTP relay refused or is unreachable: `make preflight args="--live"` shows which |
| Port 5432 already in use | Set `POSTGRES_HOST_PORT=5433` in `.env` (and the host URLs to match) |
| API refuses to start | Read its log: an empty secret line, example keys outside development, or a Telegram token without `TELEGRAM_WEBHOOK_SECRET` |
| "Connect" says the platform is unavailable | `SANDBOX_MODE=false` and that platform's app variables are set; restart the API |
| Platform redirect error after sign-in | The redirect URI in the platform app must match section 5.2 exactly, using your public `MERA_API_URL` |
| Instagram or Threads post fails fetching the image | `MERA_API_URL` must be reachable from the internet |
| No Telegram cards | You are paired, can approve the account, Telegram is on for the workspace (Approval Channels), and a worker runs the `approvals` queue |
| Post held, "On hold" | A kill switch is on, or the account needs reconnecting (Connected Accounts) |
| Post failed `approval_stale` | Approved more than 24 hours before publishing: approve again |
| Seed did nothing | It runs only with `MERA_ENV=development` and `SANDBOX_MODE=true`, and only once |
| Integration tests skipped | Docker Desktop is not running |

---

## 10. Phase 2 features and their settings

Everything below is optional: with none of it set, Mera runs as in Phase 1. Put real
values in `.env` (never `.env.example`), and restart the services named.

| Feature | Settings (`.env`) | Restart | Runbook |
| --- | --- | --- | --- |
| **Second factors** | `MFA_REQUIRED_FOR_ADMINS=true` makes Admins and Owners enrol (until then they act as Editors) | api | [MFA](docs/runbooks/mfa-recovery.md) |
| **Bluesky, Mastodon** | none: Bluesky connects with an app password, Mastodon registers itself per server | — | [accounts](docs/runbooks/connecting-social-accounts.md) |
| **WhatsApp approvals** | `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` (all four), optional `WHATSAPP_DISPLAY_NUMBER`, `WHATSAPP_TEMPLATE_LANGUAGE`, `WHATSAPP_MONTHLY_TEMPLATE_CAP` | api, bots, worker | [WhatsApp](docs/runbooks/whatsapp-business-setup.md) |
| **Image generation** | `MEDIA_PROVIDER=openai`, `MEDIA_API_KEY`, optional `MEDIA_MODEL`, `MEDIA_BASE_URL`, `MEDIA_PRICE_LOW_USD` / `_MEDIUM_` / `_HIGH_`, `MEDIA_JOB_TIMEOUT_S`, `MEDIA_REQUEST_CEILING_USD`, `MEDIA_MONTHLY_BUDGET_USD`; video stays off (`MEDIA_VIDEO_ENABLED=false`) | api, worker | Settings → Media generation shows spend |
| **Inbox and replies** | none for sandbox; real accounts need the platforms' comment permissions. Optional `ENGAGEMENT_MODEL`, `ENGAGEMENT_TRIAGE_PER_POLL` | worker | Inbox screen |
| **Mera's MCP server** | `MCP_SERVICE_SECRET` (same on api and mcp), `MCP_RESOURCE_URL`, optional `MCP_ALLOWED_ORIGINS`; web: `MERA_PUBLIC_MCP_URL`. The `mcp` container gets only these (no database URL, no master key) | api, mcp, web | [Claude](docs/runbooks/mcp-server-for-claude.md) |
| **External MCP servers** | `MCP_SANDBOX_TOKEN` (api, worker and mcp-sandbox: every relay call carries it), `MCP_EGRESS_ADMIN_TOKEN` (api, worker and mcp-egress: the per-server egress grants; the sandbox never holds it), optional `MCP_SANDBOX_RUNTIME=runsc` for gVisor. Compose ships `change-me-…` defaults that work only with `MERA_ENV=development` | api, worker, mcp-sandbox, mcp-egress | [registering](docs/runbooks/registering-an-mcp-server.md) |

Generate the shared secrets the same way as the core keys:

```bash
openssl rand -hex 24    # MCP_SERVICE_SECRET, MCP_SANDBOX_TOKEN, MCP_EGRESS_ADMIN_TOKEN, WHATSAPP_VERIFY_TOKEN
```

New services in Compose: `mcp` (port 8002), `mcp-sandbox` (internal networks only; each
stdio server runs as its own user) and `mcp-egress` (the sandbox's only way out). `api` and
`worker` reach the relay over a second internal network, `sandbox_rpc`. New worker queues: `media` (generation jobs),
`integrations` (external tool calls); comment polling runs on `metrics` and reply sending
on `publish`, all already in the worker's queue list.
