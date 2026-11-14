"""Demo seed data (M11.4; docs/10 §6.8): ``python -m mera.seed``.

Sandbox only. Creates, through the real API and services (so every invariant holds):

- a demo user (``demo@mera.local``) owning two workspaces, **Acme** and **Northwind**;
- in Acme, five accounts (a LinkedIn profile, a LinkedIn Page, a Facebook Page and two X
  accounts) in two **overlapping** groups: *Tech News* and *Company Official* share the
  LinkedIn Page;
- a history of approved, published post sets over the last 90 days, each account with its
  own metric curve, so Analytics, account comparison and approval turnaround have data;
- one set waiting for approval (for the queue and the bot), one scheduled for tomorrow.

Idempotent: if the demo workspace exists it does nothing. The history is moved into the
past with the migration (owner) role, which is why this refuses to run outside sandbox.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mera.config import Env, Settings, get_settings

log = structlog.get_logger(__name__)
EMAIL = "demo@example.com"
PASSWORD = "Mera demo password 2026!"  # noqa: S105 - sandbox demo credential, printed on purpose
# Company Page approvals need a second factor (P2-M2): the demo approves them with this PIN.
PIN = "246810"
TOPICS = [
    "Postgres 18 ships async I/O for read-heavy workloads",
    "What io_uring changes for database read replicas",
    "Kafka 4.0 drops ZooKeeper for good",
    "Why we moved nightly batch jobs to Postgres",
    "Skip-scan indexes: when they help and when they do not",
    "OAuth support lands in Postgres 18",
    "Rust in the Linux kernel: a year of drivers",
    "Choosing between logical and physical replication",
    "How we cut our cloud bill with connection pooling",
    "The case for boring technology in 2026",
    "What changed in our on-call after adding SLOs",
    "A practical guide to zero-downtime migrations",
    "Vector search in Postgres with pgvector HNSW",
    "Lessons from a year of feature flags",
    "Observability for background job queues",
    "When to shard, and when not to",
    "Hiring: platform engineers in Bengaluru",
    "Our approach to accessible product design",
]


SHIFT_DECISIONS = (
    "UPDATE approval_decision SET decided_at = decided_at - CAST(:d AS interval) "
    "+ (random() * interval '3 hours') WHERE approval_request_id IN "
    "(SELECT id FROM approval_request WHERE post_set_id = :s)"
)
SELECT_PUBLISHED = (
    "SELECT id, workspace_id, social_account_id, post_set_id, account_group_id, platform, "
    "published_at FROM published_post WHERE post_set_id = :s"
)
INSERT_POST_METRIC = (
    "INSERT INTO metric_snapshot (workspace_id, subject_type, published_post_id, "
    "social_account_id, post_set_id, account_group_id, metric_key, value, platform_field, "
    "captured_at) VALUES (:w, 'post', :p, :a, :ps, :g, :k, :v, :f, :t) ON CONFLICT DO NOTHING"
)
INSERT_FOLLOWERS = (
    "INSERT INTO metric_snapshot (workspace_id, subject_type, social_account_id, metric_key, "
    "value, platform_field, captured_at) VALUES (:w, 'account', :a, 'followers', :v, "
    "'seed.followers', :t) ON CONFLICT DO NOTHING"
)


class SeedError(RuntimeError):
    pass


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _ok(r: httpx.Response, *codes: int) -> dict[str, Any]:
    if r.status_code not in (codes or (200, 201)):
        raise SeedError(
            f"{r.request.method} {r.request.url.path} → {r.status_code}: {r.text[:300]}"
        )
    return r.json() if r.content else {}


async def _decide(
    api: httpx.AsyncClient, tok: str, ws: str, request: dict[str, Any]
) -> dict[str, Any]:
    """Approve every variant of a request; when the server asks for a second factor
    (a company Page), confirm with the demo PIN the way the web app does."""
    url = f"/api/v1/workspaces/{ws}/approval-requests/{request['id']}/decisions"
    body = {
        "action": "approve",
        "variant_scope": None,
        "content_hashes": {v["post_variant_id"]: v["content_hash"] for v in request["variants"]},
    }
    r = await api.post(url, headers=_h(tok), json=body)
    err = (r.json().get("error") or {}) if r.status_code == 403 else {}
    if err.get("code") == "second_factor_required":
        action = (err.get("field_errors") or [{}])[0].get("field") or "company_page_approve"
        grant = await _ok(
            await api.post(
                f"/api/v1/workspaces/{ws}/second-factor",
                headers=_h(tok),
                json={"action": action, "method": "pin", "value": PIN},
            )
        )
        r = await api.post(
            url, headers={**_h(tok), "X-Mera-Second-Factor": grant["token"]}, json=body
        )
    return await _ok(r)


async def _connect(
    api: httpx.AsyncClient, tok: str, ws: str, platform: str, handle: str, kind: str, nick: str
) -> str:
    r = await _ok(
        await api.post(
            f"/api/v1/workspaces/{ws}/social-accounts/connect/{platform}",
            headers=_h(tok),
            json={"return_path": "/accounts"},
        )
    )
    state = parse_qs(urlsplit(r["authorize_url"]).query)["state"][0]
    code = f"fake:{handle}-{uuid.uuid4().hex[:6]}:{handle}:{kind}"
    cb = await api.get(
        f"/api/v1/social-accounts/oauth/{platform}/callback", params={"code": code, "state": state}
    )
    params = {k: v[0] for k, v in parse_qs(urlsplit(cb.headers["location"]).query).items()}
    done = await _ok(
        await api.post(
            "/api/v1/social-accounts/connect/complete",
            headers=_h(tok),
            json={"connect_code": params["connect_code"]},
        )
    )
    account_id = str(done["account_id"])
    await _ok(
        await api.patch(
            f"/api/v1/workspaces/{ws}/social-accounts/{account_id}",
            headers=_h(tok),
            json={"nickname": nick},
        )
    )
    return account_id


async def _set(
    api: httpx.AsyncClient, app: Any, tok: str, ws: str, topic: str, targets: dict[str, list[str]]
) -> dict[str, Any]:
    ps = await _ok(
        await api.post(
            f"/api/v1/workspaces/{ws}/post-sets",
            headers=_h(tok),
            json={"topic": topic, "targets": targets},
        )
    )
    # Play the agents queue inline: the seed must not depend on a running worker.
    await app.state.drafting.draft(uuid.UUID(ws), uuid.UUID(ps["id"]), mode="create")
    return await _ok(
        await api.get(f"/api/v1/workspaces/{ws}/post-sets/{ps['id']}", headers=_h(tok))
    )


def _curve(seed: str, day: int, platform: str) -> dict[str, float | None]:
    h = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)
    base = 300 + h % 2400
    grow = min(1.0, 0.35 + 0.65 * (day / 7))
    reported = {
        "linkedin_member": ("likes", "comments", "shares"),
        "linkedin_organization": ("impressions", "likes", "comments", "shares", "clicks"),
        "facebook": ("impressions", "reach", "likes", "comments", "shares", "clicks"),
        "x": ("impressions", "likes", "comments", "shares"),
        "threads": ("impressions", "likes", "comments", "shares"),
    }.get(platform, ("likes",))
    f = {
        "impressions": 1.0,
        "reach": 0.7,
        "likes": 0.05,
        "comments": 0.007,
        "shares": 0.005,
        "clicks": 0.02,
    }
    return {
        k: (round(base * v * grow * (1 + (h % 9) / 10)) if k in reported else None)
        for k, v in f.items()
    }


async def seed(settings: Settings) -> str:
    if settings.mera_env is not Env.DEVELOPMENT or not settings.sandbox_mode:
        raise SeedError("the demo seed runs only in development with SANDBOX_MODE=true")
    from mera.api.main import create_app
    from mera.application.publishing.publisher import Publisher
    from mera.infrastructure.platforms.adapters.registry import build_adapters

    app = create_app(settings.model_copy(update={"rate_limit_enabled": False}))
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://seed") as api,
    ):
        # Approvals and publishing are played inline below; nothing goes to the queue.
        app.state.approval_service._deferrer = None
        app.state.publishing_service._deferrer = None
        app.state.post_service._deferrer = _NoDefer()
        r = await api.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        if r.status_code == 200:
            tok = r.json()["access_token"]
            spaces = (await api.get("/api/v1/workspaces", headers=_h(tok))).json()["data"]
            if any(w["slug"] == "acme" for w in spaces):
                return "already seeded"
        else:
            await _ok(
                await api.post(
                    "/api/v1/auth/register",
                    json={"email": EMAIL, "password": PASSWORD, "display_name": "Alex Demo"},
                ),
                201,
            )
            tok = (
                await _ok(
                    await api.post(
                        "/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
                    )
                )
            )["access_token"]
        acme = (
            await _ok(
                await api.post(
                    "/api/v1/workspaces",
                    headers=_h(tok),
                    json={"name": "Acme", "slug": "acme", "timezone": "Asia/Kolkata"},
                ),
                201,
            )
        )["id"]
        north = (
            await _ok(
                await api.post(
                    "/api/v1/workspaces",
                    headers=_h(tok),
                    json={
                        "name": "Northwind",
                        "slug": "northwind",
                        "timezone": "America/Los_Angeles",
                    },
                ),
                201,
            )
        )["id"]
        acc = {
            "li": await _connect(
                api, tok, acme, "linkedin_member", "work_linkedin", "personal", "Work LinkedIn"
            ),
            "page": await _connect(
                api, tok, acme, "linkedin_organization", "acme_page", "organization", "Acme Page"
            ),
            "fb": await _connect(
                api, tok, acme, "facebook", "acme_facebook", "page", "Acme Facebook"
            ),
            "xm": await _connect(api, tok, acme, "x", "x_main", "personal", "X @main"),
            "xt": await _connect(api, tok, acme, "x", "x_tech", "personal", "X @tech"),
        }
        tech = (
            await _ok(
                await api.post(
                    f"/api/v1/workspaces/{acme}/account-groups",
                    headers=_h(tok),
                    json={
                        "name": "Tech News",
                        "social_account_ids": [acc["li"], acc["page"], acc["xm"], acc["xt"]],
                    },
                ),
                201,
            )
        )["id"]
        corp = (
            await _ok(
                await api.post(
                    f"/api/v1/workspaces/{acme}/account-groups",
                    headers=_h(tok),
                    json={
                        "name": "Company Official",
                        "social_account_ids": [acc["page"], acc["fb"]],
                    },
                ),
                201,
            )
        )["id"]
        n_threads = await _connect(
            api, tok, north, "threads", "northwind_threads", "personal", "Northwind Threads"
        )
        n_li = await _connect(
            api, tok, north, "linkedin_member", "northwind_li", "personal", "Northwind LinkedIn"
        )
        await _ok(
            await api.post(
                f"/api/v1/workspaces/{north}/account-groups",
                headers=_h(tok),
                json={"name": "Launch", "social_account_ids": [n_threads, n_li]},
            ),
            201,
        )
        # The approval PIN that confirms Company Page approvals (shown with the sign-in).
        await _ok(
            await api.put(
                "/api/v1/auth/pin", headers=_h(tok), json={"pin": PIN, "proof": PASSWORD}
            ),
            204,
        )

        publisher = Publisher(
            app.state.sessionmaker,
            build_adapters(settings),
            app.state.key_provider,
            app.state.object_store,
            sandbox=True,
        )
        history: list[tuple[str, int]] = []
        for i, topic in enumerate(TOPICS):
            group = corp if i % 3 == 2 else tech
            ps = await _set(api, app, tok, acme, topic, {"account_group_ids": [group]})
            if ps["state"] == "blocked" or any(v["state"] != "drafting" for v in ps["variants"]):
                continue
            req = await api.post(
                f"/api/v1/workspaces/{acme}/post-sets/{ps['id']}/request-approval", headers=_h(tok)
            )
            if req.status_code != 201:
                continue
            await _decide(api, tok, acme, req.json())
            slots = await _ok(
                await api.get(
                    f"/api/v1/workspaces/{acme}/scheduled-posts",
                    headers=_h(tok),
                    params={"post_set_id": ps["id"]},
                )
            )
            for sp in slots["data"]:
                await publisher.publish(
                    uuid.UUID(acme),
                    uuid.UUID(sp["id"]),
                    now=datetime.fromisoformat(sp["scheduled_for"]) + timedelta(seconds=1),
                )
            history.append((ps["id"], 88 - i * 5))
        # Waiting for approval: the queue and the bot have something to show.
        live = await _set(
            api,
            app,
            tok,
            acme,
            "Postgres 18 is out: async I/O, OAuth and skip-scan",
            {"account_group_ids": [tech]},
        )
        await api.post(
            f"/api/v1/workspaces/{acme}/post-sets/{live['id']}/request-approval", headers=_h(tok)
        )
        # History: move each set into the past and give every post a 90-day metric curve.
        engine = create_async_engine(settings.migration_url)
        try:
            async with engine.begin() as c:
                # Demo history only: the decision ledger is append-only, so the trigger is
                # lifted inside this one transaction to backdate it, and restored before
                # commit. Nothing outside the seed ever does this.
                await c.execute(text("ALTER TABLE approval_decision DISABLE TRIGGER USER"))
                for set_id, days_ago in history:
                    shift = timedelta(days=max(1, days_ago))
                    await c.execute(
                        text(
                            "UPDATE published_post SET "
                            "published_at = published_at - CAST(:d AS interval), "
                            "next_metrics_at = NULL, metrics_synced_at = now() "
                            "WHERE post_set_id = :s"
                        ),
                        {"d": shift, "s": set_id},
                    )
                    for tbl, col in (
                        ("scheduled_post", "scheduled_for"),
                        ("approval_request", "created_at"),
                    ):
                        await c.execute(
                            text(
                                f"UPDATE {tbl} SET {col} = {col} - CAST(:d AS interval) "  # noqa: S608
                                "WHERE post_set_id = :s"
                            ),
                            {"d": shift, "s": set_id},
                        )
                    await c.execute(
                        text(SHIFT_DECISIONS),
                        {"d": shift, "s": set_id},
                    )
                    rows = (
                        await c.execute(
                            text(SELECT_PUBLISHED),
                            {"s": set_id},
                        )
                    ).all()
                    for pp in rows:
                        for day in range(0, min(10, days_ago) + 1, 2):
                            at = (pp.published_at + timedelta(days=day)).replace(
                                minute=0, second=0, microsecond=0
                            )
                            for key, value in _curve(str(pp.id), day, pp.platform).items():
                                await c.execute(
                                    text(INSERT_POST_METRIC),
                                    {
                                        "w": pp.workspace_id,
                                        "p": pp.id,
                                        "a": pp.social_account_id,
                                        "ps": pp.post_set_id,
                                        "g": pp.account_group_id,
                                        "k": key,
                                        "v": None if value is None else Decimal(value),
                                        "f": f"seed.{key}",
                                        "t": at,
                                    },
                                )
                await c.execute(text("ALTER TABLE approval_decision ENABLE TRIGGER USER"))
                for name, aid in acc.items():
                    for day in range(0, 91, 7):
                        await c.execute(
                            text(INSERT_FOLLOWERS),
                            {
                                "w": acme,
                                "a": aid,
                                "v": Decimal(800 + 37 * (90 - day) % 400 + len(name) * 50),
                                "t": (datetime.now(UTC) - timedelta(days=day)).replace(
                                    minute=0, second=0, microsecond=0
                                ),
                            },
                        )
        finally:
            await engine.dispose()
    return f"seeded: sign in as {EMAIL} / {PASSWORD} (approval PIN {PIN})"


class _NoDefer:
    async def defer(self, task_name: str, **kwargs: Any) -> bool:
        return True


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        print(asyncio.run(seed(get_settings())))
    except SeedError as exc:
        print(f"seed skipped: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
