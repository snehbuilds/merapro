"""More adapter contract tests (M8.13, M10.3): reconciliation after a lost response,
metric normalisation (a missing metric is None, never 0) and the HTTP error helpers."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from mera.config import get_settings
from mera.domain.ports.platform_adapter import PermanentError, PublishError
from mera.infrastructure.platforms.adapters import http as h
from mera.infrastructure.platforms.adapters.fake import FakePlatformAdapter
from mera.infrastructure.platforms.adapters.linkedin import LinkedInAdapter
from mera.infrastructure.platforms.adapters.meta import (
    FacebookPageAdapter,
    InstagramAdapter,
    caption,
)
from mera.infrastructure.platforms.adapters.registry import build_adapters
from mera.infrastructure.platforms.adapters.threads import ThreadsAdapter, text_for
from mera.infrastructure.platforms.adapters.x import XAdapter
from tests.unit.test_platform_adapters import CREDS, IMG, Cassette, _form, _json, _payload

SINCE = datetime(2026, 1, 1, tzinfo=UTC)


def _by(rows: list[Any]) -> dict[str, float | None]:
    return {r.metric_key: r.value for r in rows}


# --------------------------------------------------------------------------- Threads


async def test_threads_image_and_carousel_and_thread_parts() -> None:
    ids = iter(["c1", "p1", "c2", "p2"])
    cas = Cassette(
        [
            (
                "POST",
                "https://graph.threads.net/v1.0/u/threads",
                lambda r: httpx.Response(200, json={"id": next(ids)}),
            ),
            (
                "POST",
                "https://graph.threads.net/v1.0/u/threads_publish",
                lambda r: httpx.Response(200, json={"id": next(ids)}),
            ),
            (
                "POST",
                "https://graph.threads.net/v1.0/u/threads",
                lambda r: httpx.Response(200, json={"id": next(ids)}),
            ),
            (
                "POST",
                "https://graph.threads.net/v1.0/u/threads_publish",
                lambda r: httpx.Response(200, json={"id": next(ids)}),
            ),
            ("GET", "https://graph.threads.net/v1.0/p1", _json({}, 400)),
        ]
    )
    res = await ThreadsAdapter(cas.http(), settle_seconds=0).publish(
        CREDS,
        _payload(
            platform="threads",
            platform_account_id="u",
            media=[IMG],
            thread_parts=["one", "two"],
            link_url=None,
        ),
    )
    assert res.platform_post_id == "p1"
    assert res.extra_ids == {"part_2": "p2"}
    assert res.permalink is None
    first = _form(cas.requests[0])
    assert first["media_type"] == ["IMAGE"]
    assert first["image_url"] == [IMG.public_url]
    assert _form(cas.requests[2])["reply_to_id"] == ["p1"]
    car = Cassette(
        [
            ("POST", "https://graph.threads.net/v1.0/u/threads", _json({"id": "k1"})),
            ("POST", "https://graph.threads.net/v1.0/u/threads", _json({"id": "k2"})),
            ("POST", "https://graph.threads.net/v1.0/u/threads", _json({"id": "parent"})),
            ("POST", "https://graph.threads.net/v1.0/u/threads_publish", _json({"id": "pp"})),
            (
                "GET",
                "https://graph.threads.net/v1.0/pp",
                _json({"permalink": "https://threads.net/pp"}),
            ),
        ]
    )
    out = await ThreadsAdapter(car.http(), settle_seconds=0).publish(
        CREDS,
        _payload(platform="threads", platform_account_id="u", media=[IMG, IMG], link_url=None),
    )
    assert out.permalink == "https://threads.net/pp"
    assert _form(car.requests[2])["children"] == ["k1,k2"]
    with pytest.raises(PermanentError):
        await ThreadsAdapter(Cassette([]).http()).publish(
            CREDS, _payload(platform="threads", media=[type(IMG)(b"x", "image/png", None)])
        )
    assert text_for("hi", ["one", "two"]) == "hi #one"


async def test_threads_reconcile_and_metrics() -> None:
    p = _payload(platform="threads", platform_account_id="u")
    want = text_for(p.body, p.hashtags)
    cas = Cassette(
        [
            (
                "GET",
                "https://graph.threads.net/v1.0/u/threads",
                _json(
                    {
                        "data": [
                            {"id": "other", "text": "x"},
                            {"id": "t9", "text": want, "permalink": "l"},
                        ]
                    }
                ),
            ),
            (
                "GET",
                "https://graph.threads.net/v1.0/t9/insights",
                _json(
                    {
                        "data": [
                            {"name": "views", "values": [{"value": 120}]},
                            {"name": "likes", "total_value": {"value": 7}},
                            {"name": "reposts", "values": [{"value": 1}]},
                        ]
                    }
                ),
            ),
            (
                "GET",
                "https://graph.threads.net/v1.0/u/threads_insights",
                _json({"data": [{"total_value": {"value": 300}}]}),
            ),
        ]
    )
    a = ThreadsAdapter(cas.http(), settle_seconds=0)
    assert (await a.find_existing(CREDS, p, SINCE)).platform_post_id == "t9"  # type: ignore[union-attr]
    m = _by(await a.post_metrics(CREDS, "u", "t9"))
    assert m == {"impressions": 120.0, "likes": 7.0, "comments": None, "shares": 1.0}
    assert _by(await a.account_metrics(CREDS, "u")) == {"followers": 300.0}


# ---------------------------------------------------------------------------- Meta


async def test_facebook_single_photo_reconcile_and_metrics() -> None:
    cas = Cassette(
        [
            (
                "POST",
                "https://graph.facebook.com/v23.0/p/photos",
                _json({"id": "ph", "post_id": "p_7"}),
            ),
            (
                "GET",
                "https://graph.facebook.com/v23.0/p_7",
                _json({"error": {"code": 100, "message": "no"}}, 400),
            ),
        ]
    )
    res = await FacebookPageAdapter(cas.http()).publish(
        CREDS, _payload(platform="facebook", platform_account_id="p", media=[IMG])
    )
    assert res.platform_post_id == "p_7"
    assert res.permalink == "https://www.facebook.com/p_7"
    p = _payload(platform="facebook", platform_account_id="p", link_placement="body")
    want = caption(p.body, p.hashtags, p.link_url, p.link_placement)
    cas2 = Cassette(
        [
            (
                "GET",
                "https://graph.facebook.com/v23.0/p/posts",
                _json({"data": [{"id": "p_1", "message": want, "permalink_url": "u"}]}),
            ),
            (
                "GET",
                "https://graph.facebook.com/v23.0/p_1",
                _json(
                    {
                        "shares": {"count": 3},
                        "reactions": {"summary": {"total_count": 20}},
                        "comments": {"summary": {"total_count": 4}},
                    }
                ),
            ),
            (
                "GET",
                "https://graph.facebook.com/v23.0/p_1/insights",
                _json({"data": [{"values": [{"value": 900}]}]}),
            ),
            (
                "GET",
                "https://graph.facebook.com/v23.0/p_1/insights",
                _json({"error": {"code": 100, "message": "metric retired"}}, 400),
            ),
            (
                "GET",
                "https://graph.facebook.com/v23.0/p_1/insights",
                _json({"data": [{"total_value": {"value": 12}}]}),
            ),
            ("GET", "https://graph.facebook.com/v23.0/p", _json({"fan_count": 1500})),
        ]
    )
    fb = FacebookPageAdapter(cas2.http())
    assert (await fb.find_existing(CREDS, p, SINCE)).platform_post_id == "p_1"  # type: ignore[union-attr]
    m = _by(await fb.post_metrics(CREDS, "p", "p_1"))
    assert m["impressions"] == 900
    assert m["reach"] is None
    assert m["clicks"] == 12
    assert m["likes"] == 20
    assert m["comments"] == 4
    assert m["shares"] == 3
    assert _by(await fb.account_metrics(CREDS, "p")) == {"followers": 1500.0}


async def test_instagram_reconcile_metrics_and_timeout() -> None:
    p = _payload(platform="instagram", platform_account_id="ig", link_placement="none")
    want = caption(p.body, p.hashtags, p.link_url, p.link_placement)
    insight = _json({"data": [{"values": [{"value": 5}]}]})
    cas = Cassette(
        [
            (
                "GET",
                "https://graph.facebook.com/v23.0/ig/media",
                _json({"data": [{"id": "m1", "caption": want, "permalink": "pl"}]}),
            )
        ]
        + [("GET", "https://graph.facebook.com/v23.0/m1/insights", insight) for _ in range(6)]
        + [("GET", "https://graph.facebook.com/v23.0/ig", _json({"followers_count": 42}))]
    )
    ig = InstagramAdapter(cas.http(), poll_seconds=0)
    assert (await ig.find_existing(CREDS, p, SINCE)).permalink == "pl"  # type: ignore[union-attr]
    assert set(_by(await ig.post_metrics(CREDS, "ig", "m1")).values()) == {5.0}
    assert _by(await ig.account_metrics(CREDS, "ig")) == {"followers": 42.0}
    slow = Cassette(
        [("POST", "https://graph.facebook.com/v23.0/ig/media", _json({"id": "c"}))]
        + [
            ("GET", "https://graph.facebook.com/v23.0/c", _json({"status_code": "IN_PROGRESS"}))
            for _ in range(20)
        ]
    )
    with pytest.raises(PublishError, match="still processing"):
        await InstagramAdapter(slow.http(), poll_seconds=0).publish(
            CREDS, _payload(platform="instagram", platform_account_id="ig", media=[IMG])
        )


# ------------------------------------------------------------------ LinkedIn and X


async def test_linkedin_org_metrics_and_followers() -> None:
    cas = Cassette(
        [
            (
                "GET",
                "https://api.linkedin.com/rest/socialActions/",
                _json({"likesSummary": {"totalLikes": 3}, "commentsSummary": {}}),
            ),
            (
                "GET",
                "https://api.linkedin.com/rest/organizationalEntityShareStatistics",
                _json(
                    {
                        "elements": [
                            {
                                "totalShareStatistics": {
                                    "impressionCount": 1000,
                                    "clickCount": 30,
                                    "shareCount": 2,
                                    "engagement": 0.05,
                                }
                            }
                        ]
                    }
                ),
            ),
            (
                "GET",
                "https://api.linkedin.com/rest/networkSizes/",
                _json({"firstDegreeSize": 5000}),
            ),
        ]
    )
    org = LinkedInAdapter("linkedin_organization", cas.http())
    m = _by(await org.post_metrics(CREDS, "42", "urn:li:share:1"))
    assert m["impressions"] == 1000
    assert m["comments"] is None
    assert m["engagement_rate"] == 0.05
    assert _by(await org.account_metrics(CREDS, "42")) == {"followers": 5000.0}
    assert _by(
        await LinkedInAdapter("linkedin_member", Cassette([]).http()).account_metrics(CREDS, "a")
    ) == {"followers": None}
    none = Cassette(
        [
            (
                "GET",
                "https://api.linkedin.com/rest/posts",
                _json({"elements": [{"id": "old", "commentary": "x", "createdAt": 1}]}),
            )
        ]
    )
    assert (
        await LinkedInAdapter("linkedin_member", none.http()).find_existing(
            CREDS, _payload(platform="linkedin_member", platform_account_id="a"), SINCE
        )
        is None
    )


async def test_x_reconcile_and_followers() -> None:
    p = _payload(link_url=None)
    from mera.infrastructure.platforms.adapters.x import text_for as x_text

    cas = Cassette(
        [
            (
                "GET",
                "https://api.x.com/2/users/acct-1/tweets",
                _json({"data": [{"id": "9", "text": x_text(p.body, p.hashtags, None, "reply")}]}),
            ),
            (
                "GET",
                "https://api.x.com/2/users/acct-1",
                _json({"data": {"public_metrics": {"followers_count": 77}}}),
            ),
        ]
    )
    x = XAdapter(cas.http())
    assert (await x.find_existing(CREDS, p, SINCE)).platform_post_id == "9"  # type: ignore[union-attr]
    assert _by(await x.account_metrics(CREDS, "acct-1")) == {"followers": 77.0}


# --------------------------------------------------------------- shared plumbing


async def test_http_helpers() -> None:
    assert h._retry_after(httpx.Response(429, headers={"retry-after": "12"})) == 12
    assert (
        0
        <= h._retry_after(
            httpx.Response(429, headers={"x-rate-limit-reset": str(int(time.time()) + 30)})
        )
        <= 30
    )  # type: ignore[operator]
    assert h._retry_after(httpx.Response(429, headers={"retry-after": "soon"})) is None
    assert h.platform_message(httpx.Response(400, text="plain")) == "plain"
    assert h.platform_message(httpx.Response(400, json={"detail": "nope"})) == "nope"
    assert h.platform_message(httpx.Response(400, json={"errors": [{"message": "dup"}]})) == "dup"
    assert h.platform_message(httpx.Response(400, json=[1, 2])) == "[1, 2]"
    assert h.same_text("a  b", "a b")
    assert not h.same_text(None, "a")
    cas = Cassette([("GET", "https://x.example/", lambda r: httpx.Response(200, text="not json"))])
    with pytest.raises(PublishError):
        await cas.http().json("GET", "https://x.example/")
    empty = Cassette([("GET", "https://x.example/", lambda r: httpx.Response(204))])
    assert await empty.http().json("GET", "https://x.example/") == {}


async def test_fake_and_registry() -> None:
    fake = FakePlatformAdapter("x")
    assert _by(await fake.account_metrics({}, "a"))["followers"] is not None
    sandbox = get_settings().model_copy(update={"sandbox_mode": True})
    assert all(isinstance(a, FakePlatformAdapter) for a in build_adapters(sandbox).values())
    live = get_settings().model_copy(update={"sandbox_mode": False})
    real = build_adapters(live)
    assert set(real) == {
        "linkedin_member",
        "linkedin_organization",
        "facebook",
        "instagram",
        "threads",
        "x",
        "bluesky",
        "mastodon",
    }
    assert not any(isinstance(a, FakePlatformAdapter) for a in real.values())
