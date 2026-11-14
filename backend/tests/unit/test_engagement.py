"""Inbox pure logic and engagement adapters (P2-M6)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx

from mera.application.agents.engagement import EngagementBrief, EngagementTriage, clean_reply
from mera.application.engagement.sendable import reply_hash
from mera.application.services.inbox_service import next_poll, rule_matches
from mera.domain.ports.engagement import CommentRef, PostRef
from mera.infrastructure.platforms.adapters.http import PlatformHTTP
from mera.infrastructure.platforms.engagement import (
    BlueskyEngagement,
    FacebookEngagement,
    MastodonEngagement,
    build_engagement,
)

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)


def test_poll_tapers_then_stops() -> None:
    def gap(age: timedelta) -> timedelta | None:
        at = next_poll(NOW - age, NOW)
        return None if at is None else at - NOW

    assert gap(timedelta(minutes=10)) == timedelta(minutes=5)
    assert gap(timedelta(hours=12)) == timedelta(minutes=30)
    assert gap(timedelta(days=3)) == timedelta(hours=3)
    assert gap(timedelta(days=20)) == timedelta(days=1)
    assert gap(timedelta(days=30)) is None


def _rule(**match: list[str]) -> SimpleNamespace:
    return SimpleNamespace(match=match)


def _triage(intent: str, sentiment: str = "neutral") -> EngagementTriage:
    return EngagementTriage(
        sentiment=sentiment,
        intent=intent,
        toxicity_score=0,
        spam_score=0,  # type: ignore[arg-type]
    )


def test_rules_match_all_given_conditions() -> None:
    spam = _rule(keywords=["$$$"])
    assert rule_matches(spam, "Earn $$$ now", None)  # type: ignore[arg-type]
    assert not rule_matches(spam, "hello", None)  # type: ignore[arg-type]
    questions = _rule(intent=["question"])
    assert not rule_matches(questions, "why?", None)  # type: ignore[arg-type]
    assert rule_matches(questions, "why?", _triage("question"))  # type: ignore[arg-type]
    both = _rule(keywords=["refund"], sentiment=["negative"])
    assert not rule_matches(both, "refund please", _triage("complaint"))  # type: ignore[arg-type]
    assert rule_matches(both, "refund please", _triage("complaint", "negative"))  # type: ignore[arg-type]
    assert not rule_matches(_rule(), "anything", None)  # type: ignore[arg-type]


def test_replies_are_cleaned_of_links_hashtags_and_other_handles() -> None:
    brief = EngagementBrief(
        comment="?",
        commenter="ana_dev",
        post_text="",
        platform_name="X",
        account_name="Tech",
        allowed_links=("https://ours.example/post",),
    )
    out = clean_reply(
        "Thanks @ana_dev and @mallory! See https://evil.example and "
        "https://ours.example/post #growth",
        brief,
    )
    assert out == "Thanks @ana_dev and ! See and https://ours.example/post"
    assert clean_reply("   ", brief) is None


def test_reply_hash_binds_account_comment_and_text() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    h = reply_hash(a, c, "hi")
    assert h != reply_hash(b, c, "hi")
    assert h != reply_hash(a, b, "hi")
    assert h != reply_hash(a, c, "hi!")
    assert len(h) == 32


async def test_facebook_fetch_marks_own_and_filters_by_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/123_456/comments")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "c2",
                        "message": "new",
                        "created_time": "2026-11-10T11:00:00+0000",
                        "from": {"id": "9", "name": "Ana"},
                    },
                    {
                        "id": "c1",
                        "message": "old",
                        "created_time": "2026-11-09T11:00:00+0000",
                        "from": {"id": "page1"},
                    },
                ]
            },
        )

    eng = FacebookEngagement(PlatformHTTP(transport=httpx.MockTransport(handler)))
    post = PostRef("123_456", "page1")
    every = await eng.fetch_comments({"access_token": "t"}, post, None)
    assert [c.platform_comment_id for c in every] == ["c1", "c2"]
    assert every[0].is_own is True
    newer = await eng.fetch_comments(
        {"access_token": "t"}, post, datetime(2026, 11, 10, tzinfo=UTC)
    )
    assert [c.platform_comment_id for c in newer] == ["c2"]


async def test_bluesky_reply_carries_root_and_parent() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"uri": "at://did:me/app.bsky.feed.post/r1", "cid": "x"})

    eng = BlueskyEngagement(PlatformHTTP(transport=httpx.MockTransport(handler)))
    post = PostRef("at://did:me/p/1", "did:me", {"uri": "at://did:me/p/1", "cid": "rootcid"})
    out = await eng.reply(
        {"access_jwt": "j", "did": "did:me"},
        post,
        CommentRef("at://did:them/p/9", {"cid": "parentcid"}),
        "Thanks",
        "k",
    )
    assert out.endswith("/r1")
    reply = seen[0]["record"]["reply"]  # type: ignore[index]
    assert reply == {
        "root": {"uri": "at://did:me/p/1", "cid": "rootcid"},
        "parent": {"uri": "at://did:them/p/9", "cid": "parentcid"},
    }


async def test_mastodon_reply_is_idempotent_per_key() -> None:
    keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        keys.append(request.headers["idempotency-key"])
        return httpx.Response(200, json={"id": "777"})

    eng = MastodonEngagement(PlatformHTTP(transport=httpx.MockTransport(handler)))
    creds = {"instance": "mastodon.example", "access_token": "t"}
    rid = await eng.reply(
        creds, PostRef("1", "5@mastodon.example"), CommentRef("2"), "hi", "mera-reply-1"
    )
    assert rid == "777"
    assert keys == ["mera-reply-1"]


def test_build_engagement_names_what_is_supported() -> None:
    real = build_engagement(SimpleNamespace(sandbox_mode=False))
    assert set(real) == {"facebook", "instagram", "threads", "mastodon", "bluesky"}
    assert "x" in build_engagement(SimpleNamespace(sandbox_mode=True))
