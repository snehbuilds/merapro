"""The Telegram Bot API client against a cassette, and the card renderer's layouts
(docs/07 §3: the card is asserted on its content, not just its state)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from mera.application.channels import render
from mera.domain.policies.settings_resolution import quiet_hull
from mera.domain.ports.approval_channel import Button, ChannelError, OutboundMessage
from mera.infrastructure.channels.fake import FakeChannel
from mera.infrastructure.channels.telegram import TelegramChannel, keyboard

NOW = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)


class Bot:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.calls.append((method, body))
        out = self.answers.get(method, {"ok": True, "result": True})
        if isinstance(out, Exception):
            raise out
        status = out.pop("_status", 200) if isinstance(out, dict) else 200
        return httpx.Response(status, json=out)

    def channel(self) -> TelegramChannel:
        return TelegramChannel("123:secret-token", transport=httpx.MockTransport(self))


async def test_send_edit_answer_and_webhook() -> None:
    bot = Bot({"sendMessage": {"ok": True, "result": {"message_id": 42}}})
    ch = bot.channel()
    msg = OutboundMessage(
        text="<b>Card</b>",
        rows=[[Button("✅ Approve", "a:ws:jti")], [Button("Web", url="https://mera.example")]],
        reply_to_message_id="7",
        silent=True,
    )
    assert await ch.send("100", msg) == "42"
    method, body = bot.calls[0]
    assert method == "sendMessage"
    assert body["parse_mode"] == "HTML"
    assert body["disable_notification"] is True
    assert body["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "✅ Approve",
        "callback_data": "a:ws:jti",
    }
    assert body["reply_markup"]["inline_keyboard"][1][0] == {
        "text": "Web",
        "url": "https://mera.example",
    }
    assert body["reply_parameters"]["message_id"] == 7
    await ch.edit("100", "42", OutboundMessage(text="done"))
    assert bot.calls[-1][1]["reply_markup"] == {"inline_keyboard": []}  # buttons removed
    await ch.answer("cb1", "ok")
    await ch.set_webhook("https://mera.example/api/v1/webhooks/telegram", "s" * 24)
    assert bot.calls[-1][1]["secret_token"] == "s" * 24
    await ch.delete_webhook()


async def test_force_reply_and_errors() -> None:
    assert keyboard(
        OutboundMessage(text="x", force_reply=True, placeholder="What should change?")
    ) == {
        "force_reply": True,
        "selective": True,
        "input_field_placeholder": "What should change?",
    }
    assert keyboard(OutboundMessage(text="x")) is None
    bot = Bot(
        {
            "sendMessage": {
                "ok": False,
                "description": "Forbidden: bot was blocked by the user",
                "_status": 403,
            },
            "editMessageText": {
                "ok": False,
                "description": "Bad Request: message is not modified",
                "_status": 400,
            },
            "answerCallbackQuery": {"ok": False, "description": "query is too old", "_status": 400},
        }
    )
    ch = bot.channel()
    with pytest.raises(ChannelError, match="blocked"):
        await ch.send("1", OutboundMessage(text="x"))
    await ch.edit("1", "2", OutboundMessage(text="x"))  # "not modified" is not an error
    await ch.answer("old")  # a late answer is harmless
    down = Bot({"getMe": httpx.ConnectError("down")}).channel()
    with pytest.raises(ChannelError, match="could not reach") as exc:
        await down.me()
    assert "secret-token" not in str(exc.value)


async def test_polling() -> None:
    bot = Bot(
        {
            "getUpdates": {"ok": True, "result": [{"update_id": 5}]},
            "getMe": {"ok": True, "result": {"username": "mera_bot"}},
        }
    )
    ch = bot.channel()
    assert await ch.get_updates(4) == [{"update_id": 5}]
    assert bot.calls[0][1]["offset"] == 4
    assert (await ch.me())["username"] == "mera_bot"


async def test_fake_channel_records() -> None:
    fake = FakeChannel()
    mid = await fake.send("c", OutboundMessage(text="hi", rows=[[Button("A", "a")]]))
    assert fake.buttons("c", mid) == [("A", "a")]
    await fake.edit("c", mid, OutboundMessage(text="edited"))
    assert fake.last("c").text == "edited"
    with pytest.raises(KeyError):
        fake.last("nobody")


# ------------------------------------------------------------------------- render


def _variant(i: int, **kw: Any) -> dict[str, Any]:
    v = {
        "post_variant_id": f"v{i}",
        "nickname": f"Account {i}",
        "handle": f"acct{i}",
        "platform": "x" if i % 2 else "linkedin_member",
        "state": "pending",
        "version_no": 1,
        "body": f"Post {i}: Postgres 18 ships async I/O, and read-heavy workloads benefit. " * 3,
        "thread_parts": None,
        "hashtags": ["postgres"],
        "link_url": "https://www.postgresql.org/about/news/",
        "media": [],
        "confidence_score": 0.8,
        "compliance_flags": [],
        "can_decide": True,
    }
    v.update(kw)
    return v


def _payload(n: int, **kw: Any) -> dict[str, Any]:
    p = {
        "request": {
            "id": "r1",
            "title": "Postgres 18",
            "state": "pending",
            "expires_at": (NOW + timedelta(hours=11, minutes=42)).isoformat(),
            "variants": [_variant(i) for i in range(n)],
        },
        "workspace": {"name": "Acme", "timezone": "Asia/Kolkata"},
        "group": {"name": "Tech News"},
        "source": {
            "title": "PostgreSQL 18 Released",
            "source_name": "PostgreSQL News",
            "summary": "Async I/O.",
        },
        "distinct": {"max_similarity": 0.41, "ok": True},
        "stagger_span_seconds": 780,
        "scheduled_for": {},
        "facts_verified_all": True,
    }
    p.update(kw)
    return p


def test_summary_card_layout() -> None:
    p = _payload(4)
    p["request"]["variants"][0]["media"] = [{"alt_text": "chart"}]
    p["request"]["variants"][1]["compliance_flags"] = [
        {"severity": "warning", "explanation": "Similar to your post from 14 Oct"}
    ]
    p["request"]["variants"][2]["thread_parts"] = ["1/ a", "2/ b"]
    msg = render.summary_card(
        p,
        now=NOW,
        approve_all="a:x",
        per_account=["v:1", "v:2", "v:3", "v:4"],
        message_ref="w:c",
        web_url="https://mera.example/w/acme/approvals",
    )
    assert "expires in 11h 42m" in msg.text
    assert "Acme · Tech News · 4 accounts" in msg.text
    assert msg.text.count("📰") == 1
    assert "📎 1 image · alt text set" in msg.text
    assert "⚠️ Similar to your post from 14 Oct" in msg.text
    assert "thread, 2" in msg.text
    assert "max 0.41" in msg.text
    assert "Staggered over 13 min" in msg.text
    assert [b.label for b in msg.rows[0]] == ["✅ Approve all 4"]
    assert msg.rows[-1][-1].url == "https://mera.example/w/acme/approvals"


def test_more_than_eight_accounts_are_one_line_each_and_fit_truncates() -> None:
    p = _payload(9)
    msg = render.summary_card(
        p,
        now=NOW,
        approve_all=None,
        per_account=[f"v:{i}" for i in range(9)],
        message_ref="w:c",
        web_url=None,
    )
    assert "one line each" in msg.text
    assert "Post 0:" not in msg.text  # no previews above eight accounts
    assert [len(r) for r in msg.rows[0:2]] == [5, 4]  # numbered buttons in rows of five
    long = render.fit(
        [
            ["head"],
            ["source", "summary"],
            ["line", "   preview " * 500, "   ⚠️ kept"],
            ["✅ pass", "🕒 span"],
        ],
        300,
    )
    assert "head" in long
    assert "✅ pass" not in long
    assert "⚠️ kept" in long
    assert len(long) <= 300


def test_final_states_detail_and_messages() -> None:
    p = _payload(2)
    p["request"]["state"] = "partially_approved"
    p["request"]["variants"][0]["state"] = "approved"
    p["request"]["variants"][1]["state"] = "expired"
    final = render.summary_card(
        p, now=NOW, approve_all=None, per_account=["v:0", "v:1"], message_ref="w:c", web_url=None
    )
    assert "Partly approved" in final.text
    assert final.rows == []
    for state in ("approved", "rejected", "expired", "cancelled", "superseded", "odd"):
        assert render._final_head(state)
    d = render.detail_view(
        _payload(2, scheduled_for={"v1": "2026-11-10T03:30:00+00:00"}),
        1,
        approve="a:1",
        reject="a:2",
        message_ref="w:c",
    )
    assert "Account 1" in d.text
    assert "09:00" in d.text
    assert "#postgres" in d.text
    assert [b.label for b in d.rows[0]] == ["✅ Approve this", "❌ Reject this"]
    threaded = render.detail_view(
        _payload(
            1,
            request={
                **_payload(1)["request"],
                "variants": [
                    _variant(0, thread_parts=["1/ a", "2/ b"], media=[{"alt_text": None}])
                ],
            },
        ),
        0,
        approve=None,
        reject=None,
        message_ref="w:c",
    )
    assert "1/ 1/ a" in threaded.text
    assert "alt text on 0" in threaded.text
    rem = render.reminder(_payload(3), now=NOW, approve_all="a:x", web_url="https://mera.example")
    assert "Still waiting" in rem.text
    assert rem.rows[0][0].label == "✅ Approve 3"
    assert "Escalated (level 2)" in render.escalation(_payload(1), level=2, now=NOW).text
    rows = [
        {
            "nickname": "Work",
            "state": "published",
            "published_at": "2026-11-10T03:30:00+00:00",
            "permalink": "https://li/1",
        },
        {
            "nickname": "Page",
            "state": "failed",
            "error": "Meta API returned 500",
            "attempts": 2,
            "max_attempts": 5,
        },
        {"nickname": "X", "state": "dead_lettered", "error": "duplicate content"},
        {"nickname": "T", "state": "queued"},
    ]
    rep = render.publish_report(
        title="Postgres 18", group="Tech News", rows=rows, tz="Asia/Kolkata", web_url="https://m"
    )
    assert "1 of 4" in rep.text
    assert "attempt 2 of 5" in rep.text
    assert "will not retry" in rep.text
    alert = render.health_alert(nickname="Work", reason=None, held=1, web_url="https://m")
    assert "1 scheduled post for this account is on hold" in alert.text
    assert "unaffected" in alert.text
    assert render.when(None, "UTC") == "on approval"
    assert render.remaining((NOW - timedelta(minutes=1)).isoformat(), NOW) == "expired"
    assert render.remaining((NOW + timedelta(minutes=5)).isoformat(), NOW) == "5m"
    assert render.platform_name("nope") == "nope"
    assert render.num(12) == "13."


def test_quiet_hull_of_empty_windows_is_the_window() -> None:
    assert quiet_hull([{"start": "09:00", "end": "09:00"}]) == {"start": "09:00", "end": "09:00"}
