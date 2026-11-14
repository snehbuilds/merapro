"""P3-M5 workspace cloning and evergreen recycling against real Postgres."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application.services.evergreen_service import EvergreenService
from mera.application.services.privacy_service import tenant_tables
from mera.domain.context import WorkspaceContext
from mera.infrastructure.db.models import PostSet
from mera.infrastructure.db.session import workspace_transaction
from tests.integration.test_accounts_m3 import _app, _setup
from tests.integration.test_analytics_m10 import _published_set
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import quiet  # noqa: F401

pytestmark = pytest.mark.integration


async def test_a_clone_copies_the_setup_and_nothing_it_holds(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    s, ws = await _setup(api, "clonesrc")
    await _published_set(api, s, ws, deferrer)  # accounts, a group, content, history
    await api.put(
        f"/api/v1/workspaces/{ws}/hashtags",
        headers=_h(s),
        json={"tag": "postgres", "status": "preferred"},
    )
    r = await api.post(
        f"/api/v1/workspaces/{ws}/clone",
        headers=_h(s),
        json={"name": "Clone", "slug": f"clone-{uuid.uuid4().hex[:6]}"},
    )
    assert r.status_code == 201, r.text
    new = uuid.UUID(r.json()["id"])
    counts: dict[str, int] = {}
    async with owner_engine.connect() as c:
        for t in tenant_tables():
            n = (
                await c.execute(select(func.count()).select_from(t).where(t.c.workspace_id == new))
            ).scalar_one()
            counts[t.name] = int(n)
        cloned_from = (
            await c.execute(
                text("SELECT cloned_from_workspace_id FROM workspace WHERE id = :w"), {"w": new}
            )
        ).scalar_one()
    assert str(cloned_from) == ws
    for never in (
        "social_account",
        "post_set",
        "post_variant",
        "post_version",
        "published_post",
        "metric_snapshot",
        "approval_request",
        "account_group_member",
        "content_item",
    ):
        assert counts.get(never, 0) == 0, never
    assert counts["workspace_member"] == 1  # only the person who cloned it
    assert counts["account_group"] >= 1
    assert counts["hashtag"] == 1
    me = (await api.get(f"/api/v1/workspaces/{new}", headers=_h(s))).json()
    assert me["settings"].get("autopilot_mode", "manual") == "manual"


async def test_evergreen_posts_come_back_as_fresh_drafts(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    s, ws = await _setup(api, "evergreen")
    ps, _acc = await _published_set(api, s, ws, deferrer)
    base = f"/api/v1/workspaces/{ws}/post-sets/{ps['id']}/evergreen"
    r = await api.put(base, headers=_h(s), json={"evergreen": True, "cooldown_days": 30})
    assert r.status_code == 200, r.text
    assert r.json()["is_evergreen"] is True
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE post_set SET recycle_after = now() - interval '1 day' WHERE id = :p"),
            {"p": ps["id"]},
        )
    posts: Any = _app(api).state.post_service
    saved = posts._deferrer
    posts._deferrer = deferrer
    try:
        assert await EvergreenService(_sm(api), posts).run() == 1
        assert await EvergreenService(_sm(api), posts).run() == 0  # once per cool-down
    finally:
        posts._deferrer = saved
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        refresh = (
            await tx.execute(select(PostSet).where(PostSet.origin == "recycled"))
        ).scalar_one()
        assert refresh.title.endswith("(refresh)")
        assert refresh.state == "drafting"  # a draft: it still needs approval
        original = await tx.get(PostSet, uuid.UUID(ps["id"]))
        assert original is not None
        assert original.recycle_after is not None
