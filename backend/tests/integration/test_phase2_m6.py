"""P2-M6 unified inbox against real Postgres (docs/13 P2-M6 acceptance):

- a reply can only go out as the account that was commented on; a forged account id is
  refused at send;
- polling tapers and stops at thirty days;
- a comment that tries to instruct the agent changes nothing it controls.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application.services.inbox_service import InboxService
from mera.domain.context import WorkspaceContext
from mera.infrastructure.db.models import Comment, PublishedPost, Reply
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.platforms.engagement import FakeEngagement
from tests.integration.test_accounts_m3 import _app, _setup
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import PASSWORD, _add_member, _h, _me, _register_login
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import (
    _approve_all,
    _four,
    _publisher,
    _run_all,
    quiet,  # noqa: F401
)

pytestmark = pytest.mark.integration
PIN = "482916"


class Box:
    def __init__(self, svc: InboxService, sink: CapturingDeferrer) -> None:
        self.svc = svc
        self.sink = sink

    def fake(self, platform: str) -> FakeEngagement:
        f = self.svc._eng[platform]
        assert isinstance(f, FakeEngagement)
        return f


@pytest.fixture
async def box(api: httpx.AsyncClient) -> AsyncIterator[Box]:
    svc: InboxService = _app(api).state.inbox_service
    saved = (svc._eng, svc._deferrer)
    sink = CapturingDeferrer()
    svc._eng = {
        p: FakeEngagement(p) for p in ("linkedin_member", "facebook", "threads", "x", "instagram")
    }
    svc._deferrer = sink
    try:
        yield Box(svc, sink)
    finally:
        svc._eng, svc._deferrer = saved


def _u(ws: str, path: str) -> str:
    return f"/api/v1/workspaces/{ws}/{path}"


async def _published(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, d: CapturingDeferrer
) -> tuple[dict[str, str], list[PublishedPost]]:
    ps, acc, _ = await _four(api, s, ws, d)
    await _approve_all(api, s, ws, ps["id"])
    pub, _adapters = _publisher(api)
    outcomes = await _run_all(api, pub, ws, ps["id"])
    assert set(outcomes.values()) == {"published"}, outcomes
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        posts = list((await tx.execute(select(PublishedPost))).scalars())
    return acc, posts


async def _poll(api: httpx.AsyncClient, box: Box, ws: str, posts: list[PublishedPost]) -> Any:
    at = max(p.published_at for p in posts) + timedelta(minutes=6)
    return await box.svc.poll_workspace(uuid.UUID(ws), at)


async def _comments(api: httpx.AsyncClient, ws: str) -> list[Comment]:
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        return list((await tx.execute(select(Comment).order_by(Comment.created_at))).scalars())


async def test_poll_triage_and_identity(
    api: httpx.AsyncClient,
    box: Box,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "inbox")
    _acc, posts = await _published(api, s, ws, deferrer)
    counts = await _poll(api, box, ws, posts)
    assert counts["polled"] == 4
    assert counts["new"] == 16
    assert counts["triaged"] == 16
    again = await _poll(api, box, ws, posts)
    assert again.get("new", 0) == 0  # the cursor and the unique key: nothing twice
    rows = await _comments(api, ws)
    by_post = {p.id: p.social_account_id for p in posts}
    assert all(c.social_account_id == by_post[c.published_post_id] for c in rows)
    kinds = {c.body[:12]: (c.intent, c.state) for c in rows}
    assert kinds["How long did"] == ("question", "triaged")
    assert kinds["Earn $$$ fas"] == ("spam", "triaged")
    # The injection comment is classified as content and handed to a person.
    assert kinds["Ignore your "] == ("other", "escalated")
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        drafts = list((await tx.execute(select(Reply))).scalars())
    comment_account = {c.id: c.social_account_id for c in rows}
    injected = {c.id for c in rows if c.body.startswith("Ignore your")}
    assert drafts, "questions and praise get agent drafts"
    assert all(r.social_account_id == comment_account[r.comment_id] for r in drafts)
    assert all(r.state == "draft" for r in drafts)  # nothing approves itself
    assert not any(r.comment_id in injected for r in drafts)
    assert all("evil.example" not in r.body and "@someone_else" not in r.body for r in drafts)
    # The web inbox lists them, newest first, without our own replies.
    r = await api.get(_u(ws, "inbox/comments?state=escalated"), headers=_h(s))
    assert r.status_code == 200, r.text
    assert len(r.json()["data"]) == 4
    summary = (await api.get(_u(ws, "inbox/summary"), headers=_h(s))).json()
    assert summary["states"]["escalated"] == 4


async def test_reply_goes_out_only_as_the_commented_account(
    api: httpx.AsyncClient,
    box: Box,
    owner_engine: AsyncEngine,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "replyid")
    acc, posts = await _published(api, s, ws, deferrer)
    await _poll(api, box, ws, posts)
    rows = await _comments(api, ws)
    q = next(c for c in rows if c.body.startswith("How long") and c.platform == "x")
    # Even the injection comment, answered by hand, is answered as its own account.
    inj = next(c for c in rows if c.body.startswith("Ignore your") and c.platform == "x")
    r = await api.post(
        _u(ws, f"inbox/comments/{inj.id}/replies"), headers=_h(s), json={"body": "Noted."}
    )
    assert r.json()["social_account_id"] == str(inj.social_account_id)

    r = await api.post(_u(ws, f"inbox/comments/{q.id}/replies"), headers=_h(s), json={})
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft["source"] == "agent"
    assert draft["social_account_id"] == acc["xx"]
    r = await api.post(_u(ws, f"inbox/replies/{draft['id']}/submit"), headers=_h(s))
    assert r.json()["state"] == "pending_approval"
    assert any(c["kind"] == "reply" for c in box.sink.calls if c["task"] == "approvals:dispatch")
    r = await api.post(
        _u(ws, f"inbox/replies/{draft['id']}/approve"),
        headers=_h(s),
        json={"content_hash": "0" * 64},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "stale_hash"
    r = await api.post(
        _u(ws, f"inbox/replies/{draft['id']}/approve"),
        headers=_h(s),
        json={"content_hash": draft["content_hash"]},
    )
    assert r.json()["state"] == "approved"
    assert await box.svc.send_reply(uuid.UUID(ws), uuid.UUID(draft["id"])) == "sent"
    sent = box.fake("x").replies[-1]
    assert sent["to"] == q.platform_comment_id
    assert sent["body"] == draft["body"]
    got = (await api.get(_u(ws, f"inbox/comments/{q.id}"), headers=_h(s))).json()
    assert got["state"] == "replied"
    assert [x["state"] for x in got["replies"] if x["id"] == draft["id"]] == ["sent"]
    # Sent once: a second send does nothing.
    assert await box.svc.send_reply(uuid.UUID(ws), uuid.UUID(draft["id"])) == "sent"
    assert len(box.fake("x").replies) == 1

    # A forged account id on an approved reply is refused at send.
    q2 = next(c for c in rows if c.body.startswith("How long") and c.platform == "facebook")
    d2 = (
        await api.post(
            _u(ws, f"inbox/comments/{q2.id}/replies"),
            headers=_h(s),
            json={"body": "About 2 weeks."},
        )
    ).json()
    await api.post(
        _u(ws, f"inbox/replies/{d2['id']}/approve"),
        headers=_h(s),
        json={"content_hash": d2["content_hash"]},
    )
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE reply SET social_account_id = :a WHERE id = :r"),
            {"a": acc["xx"], "r": d2["id"]},
        )
    out = await box.svc.send_reply(uuid.UUID(ws), uuid.UUID(d2["id"]))
    assert out == "refused:identity_mismatch"
    assert box.fake("facebook").replies == []
    assert len(box.fake("x").replies) == 1


async def test_edits_void_approval_and_delete_needs_a_second_factor(
    api: httpx.AsyncClient,
    box: Box,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "replyedit")
    _acc, posts = await _published(api, s, ws, deferrer)
    await _poll(api, box, ws, posts)
    rows = await _comments(api, ws)
    c = next(x for x in rows if x.platform == "facebook" and x.body.startswith("This is"))
    d = (
        await api.post(
            _u(ws, f"inbox/comments/{c.id}/replies"), headers=_h(s), json={"body": "Thank you!"}
        )
    ).json()
    await api.post(_u(ws, f"inbox/replies/{d['id']}/submit"), headers=_h(s))
    e = (
        await api.patch(
            _u(ws, f"inbox/replies/{d['id']}"), headers=_h(s), json={"body": "Glad it helped."}
        )
    ).json()
    assert e["state"] == "draft"
    assert e["content_hash"] != d["content_hash"]
    r = await api.post(
        _u(ws, f"inbox/replies/{d['id']}/approve"),
        headers=_h(s),
        json={"content_hash": d["content_hash"]},
    )
    assert r.json()["error"]["code"] == "stale_hash"

    # Delete: a second factor, then the platform call.
    r = await api.delete(_u(ws, f"inbox/comments/{c.id}"), headers=_h(s))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "second_factor_required"
    assert box.fake("facebook").deleted == set()
    assert (
        await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": PIN, "proof": PASSWORD})
    ).status_code == 204
    grant = (
        await api.post(
            _u(ws, "second-factor"),
            headers=_h(s),
            json={"action": "comment_delete", "method": "pin", "value": PIN},
        )
    ).json()["token"]
    r = await api.delete(
        _u(ws, f"inbox/comments/{c.id}"), headers={**_h(s), "x-mera-second-factor": grant}
    )
    assert r.status_code == 204, r.text
    assert c.platform_comment_id in box.fake("facebook").deleted
    # Hide works where the platform allows it.
    other = next(x for x in rows if x.platform == "facebook" and x.body.startswith("Earn"))
    r = await api.post(
        _u(ws, f"inbox/comments/{other.id}/hide"), headers=_h(s), json={"hidden": True}
    )
    assert r.json()["state"] == "hidden"
    assert other.platform_comment_id in box.fake("facebook").hidden


async def test_rules_taper_and_scope(
    api: httpx.AsyncClient,
    box: Box,
    owner_engine: AsyncEngine,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "inboxrules")
    r = await api.post(
        _u(ws, "engagement-rules"),
        headers=_h(s),
        json={"name": "Hide spam", "match": {"keywords": ["$$$"]}, "action": "hide"},
    )
    assert r.status_code == 201, r.text
    # A scope is a record of this workspace, never another tenant's (release review #10).
    for bad in (
        {"scope_type": "social_account", "scope_id": str(uuid.uuid4())},
        {"scope_type": "account_group"},
        {"scope_type": "everywhere"},
    ):
        bad_rule = await api.post(
            _u(ws, "engagement-rules"),
            headers=_h(s),
            json={"name": "x", "match": {"keywords": ["y"]}, "action": "hide", **bad},
        )
        assert bad_rule.status_code in (400, 422), bad_rule.text
        # Refused by the request schema (an unknown scope type) or by the service.
        assert bad_rule.json()["error"]["code"] in ("invalid_scope", "validation_error")
    _acc, posts = await _published(api, s, ws, deferrer)
    await _poll(api, box, ws, posts)
    rows = await _comments(api, ws)
    spam = [c for c in rows if c.body.startswith("Earn")]
    assert {c.state for c in spam} == {"hidden"}
    assert all(str(c.matched_rule_id) == r.json()["id"] for c in spam)
    assert any(c.platform_comment_id in box.fake("x").hidden for c in spam)

    # Taper: a 31-day-old post is not polled again.
    old = posts[0]
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        await tx.execute(
            update(PublishedPost)
            .where(PublishedPost.id == old.id)
            .values(
                published_at=old.published_at - timedelta(days=31),
                next_comments_at=old.published_at,
            )
        )
    await box.svc.poll_workspace(uuid.UUID(ws), old.published_at + timedelta(minutes=10))
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        after = await tx.get(PublishedPost, old.id)
    assert after is not None
    assert after.next_comments_at is None

    # A viewer can read the inbox but cannot draft.
    viewer = await _register_login(api, "inboxviewer")
    await _add_member(owner_engine, ws, (await _me(api, viewer))["id"], "viewer")
    assert (await api.get(_u(ws, "inbox/comments"), headers=_h(viewer))).status_code == 200
    r = await api.post(
        _u(ws, f"inbox/comments/{rows[0].id}/replies"), headers=_h(viewer), json={"body": "hi"}
    )
    assert r.status_code == 403


async def test_reply_card_in_the_bot(
    api: httpx.AsyncClient,
    box: Box,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    from mera.application.channels.bot import BotRuntime
    from mera.infrastructure.channels.fake import FakeChannel
    from tests.integration.test_telegram_m7 import _pair, _tap

    s, ws = await _setup(api, "replycard")
    _acc, posts = await _published(api, s, ws, deferrer)
    await _poll(api, box, ws, posts)
    app = _app(api)
    ch = FakeChannel()
    bot = BotRuntime(
        _sm(api),
        ch,
        app.state.approval_service,
        public_url="https://mera.example",
        posts=app.state.post_service,
        governance=app.state.governance_service,
        inbox=box.svc,
    )
    chat = f"c{uuid.uuid4().hex[:8]}"
    await _pair(api, bot, s, ws, chat)
    rows = await _comments(api, ws)
    c = next(x for x in rows if x.platform == "threads" and x.body.startswith("How long"))
    d = (
        await api.post(
            _u(ws, f"inbox/comments/{c.id}/replies"), headers=_h(s), json={"body": "Two weeks."}
        )
    ).json()
    await api.post(_u(ws, f"inbox/replies/{d['id']}/submit"), headers=_h(s))
    assert await bot.dispatch("reply", uuid.UUID(ws), uuid.UUID(d["id"])) == 1
    card = next(m for m in ch.sent if m["chat_id"] == chat and "Reply waiting" in m["message"].text)
    msg = card["message"]
    assert "How long did the migration take" in msg.text
    assert "Two weeks." in msg.text
    send = next(b.data for row in msg.rows for b in row if b.label.startswith("✅"))
    assert await bot.handle_update(_tap(chat, card["message_id"], send)) == "reply_approved"
    assert box.sink.calls[-1]["task"] == "engagement:send_reply"
    # The same card again: already decided.
    assert await bot.handle_update(_tap(chat, card["message_id"], send)) == "reply_closed"
    assert await box.svc.send_reply(uuid.UUID(ws), uuid.UUID(d["id"])) == "sent"

    # A card is bound to exactly what it showed: edit the reply after the card went out and
    # the old card's tap is stale, while the new card (a new nonce) works (release review #6).
    c2 = next(x for x in rows if x.id != c.id and x.platform == "threads")
    d2 = (
        await api.post(
            _u(ws, f"inbox/comments/{c2.id}/replies"), headers=_h(s), json={"body": "Soon."}
        )
    ).json()
    await api.post(_u(ws, f"inbox/replies/{d2['id']}/submit"), headers=_h(s))
    assert await bot.dispatch("reply", uuid.UUID(ws), uuid.UUID(d2["id"])) == 1
    old = next(m for m in reversed(ch.sent) if m["chat_id"] == chat)["message"]
    old_send = next(b.data for row in old.rows for b in row if b.label.startswith("✅"))
    assert old_send.count(":") == 3
    assert len(old_send) <= 64  # Telegram callback data limit
    # Re-sending the unchanged reply keeps the same nonce: earlier cards stay valid.
    assert await bot.dispatch("reply", uuid.UUID(ws), uuid.UUID(d2["id"])) == 1
    again = next(m for m in reversed(ch.sent) if m["chat_id"] == chat)["message"]
    assert next(b.data for row in again.rows for b in row if b.label.startswith("✅")) == old_send
    r = await api.patch(
        _u(ws, f"inbox/replies/{d2['id']}"), headers=_h(s), json={"body": "Next week."}
    )
    assert r.status_code == 200, r.text
    await api.post(_u(ws, f"inbox/replies/{d2['id']}/submit"), headers=_h(s))
    assert await bot.dispatch("reply", uuid.UUID(ws), uuid.UUID(d2["id"])) == 1
    new = next(m for m in reversed(ch.sent) if m["chat_id"] == chat)["message"]
    assert "Next week." in new.text
    new_send = next(b.data for row in new.rows for b in row if b.label.startswith("✅"))
    assert new_send != old_send
    assert await bot.handle_update(_tap(chat, "1", old_send)) == "reply_stale"
    assert await bot.handle_update(_tap(chat, "1", new_send)) == "reply_approved"
