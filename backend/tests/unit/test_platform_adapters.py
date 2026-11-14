"""Contract tests per adapter (M8.13): each one speaks its platform's documented request
shapes and classifies its errors, against an ``httpx.MockTransport`` cassette."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from mera.domain.ports.platform_adapter import (
    AuthExpired,
    MediaFile,
    PermanentError,
    PublishPayload,
    RateLimited,
    TransientError,
)
from mera.infrastructure.platforms.adapters.fake import FakePlatformAdapter
from mera.infrastructure.platforms.adapters.http import PlatformHTTP
from mera.infrastructure.platforms.adapters.linkedin import (
    LinkedInAdapter,
    commentary,
    escape_commentary,
)
from mera.infrastructure.platforms.adapters.meta import (
    FacebookPageAdapter,
    InstagramAdapter,
    graph_auth_error,
)
from mera.infrastructure.platforms.adapters.threads import ThreadsAdapter
from mera.infrastructure.platforms.adapters.x import XAdapter

CREDS = {"access_token": "tok-123"}
IMG = MediaFile(
    b"\xff\xd8jpegbytes",
    "image/jpeg",
    "A chart of read latency",
    1080,
    1350,
    "https://m.example/i.jpg?sig=1",
)


def _payload(**kw: Any) -> PublishPayload:
    base: dict[str, Any] = {
        "platform": "x",
        "platform_account_id": "acct-1",
        "account_type": "personal",
        "body": "Postgres 18 ships async I/O (and it matters).",
        "thread_parts": None,
        "hashtags": ["postgres"],
        "link_url": "https://www.postgresql.org/about/news/",
        "link_placement": "reply",
        "media": [],
        "idempotency_key": "k" * 64,
    }
    base.update(kw)
    return PublishPayload(**base)


class Cassette:
    """Records requests and answers them from (method, url-prefix) rules, in order."""

    def __init__(
        self, rules: list[tuple[str, str, Callable[[httpx.Request], httpx.Response]]]
    ) -> None:
        self.rules = rules
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for i, (method, prefix, fn) in enumerate(self.rules):
            if request.method == method and str(request.url).startswith(prefix):
                self.rules.pop(i)
                return fn(request)
        raise AssertionError(f"unexpected {request.method} {request.url}")

    def http(self, **kw: Any) -> PlatformHTTP:
        return PlatformHTTP(transport=httpx.MockTransport(self), **kw)


def _json(
    body: Any, status: int = 200, headers: dict[str, str] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _r: httpx.Response(status, json=body, headers=headers)


def _form(r: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(r.content.decode())


# --------------------------------------------------------------------------- LinkedIn


def test_linkedin_commentary_escapes_and_templates_hashtags() -> None:
    assert escape_commentary("a (b) #c @d") == r"a \(b\) \#c \@d"
    text = commentary("Hello (world)", ["postgres"], "https://x.example", "first_comment")
    assert text == "Hello \\(world\\)\n\n{hashtag|\\#|postgres}"
    assert "https://x.example" in commentary("Hi", [], "https://x.example", "body")


async def test_linkedin_member_post_with_image_and_first_comment() -> None:
    cas = Cassette(
        [
            (
                "POST",
                "https://api.linkedin.com/rest/images",
                _json({"value": {"uploadUrl": "https://up.example/1", "image": "urn:li:image:9"}}),
            ),
            ("PUT", "https://up.example/1", lambda r: httpx.Response(201)),
            (
                "POST",
                "https://api.linkedin.com/rest/posts",
                lambda r: httpx.Response(201, headers={"x-restli-id": "urn:li:share:77"}),
            ),
            (
                "POST",
                "https://api.linkedin.com/rest/socialActions/",
                lambda r: httpx.Response(201, headers={"x-restli-id": "c1"}),
            ),
        ]
    )
    a = LinkedInAdapter("linkedin_member", cas.http())
    res = await a.publish(
        CREDS,
        _payload(
            platform="linkedin_member",
            platform_account_id="abc",
            link_placement="first_comment",
            media=[IMG],
        ),
    )
    assert res.platform_post_id == "urn:li:share:77"
    assert res.permalink == "https://www.linkedin.com/feed/update/urn:li:share:77/"
    assert res.extra_ids == {"first_comment": "c1"}
    post = json.loads(cas.requests[2].content)
    assert post["author"] == "urn:li:person:abc"
    assert post["content"] == {
        "media": {"id": "urn:li:image:9", "altText": "A chart of read latency"}
    }
    assert "postgresql.org" not in post["commentary"]  # the link goes in the first comment
    assert cas.requests[2].headers["LinkedIn-Version"]
    assert cas.requests[1].content == IMG.data
    comment = json.loads(cas.requests[3].content)
    assert comment["message"]["text"] == "https://www.postgresql.org/about/news/"


async def test_linkedin_org_author_and_multi_image() -> None:
    cas = Cassette(
        [
            (
                "POST",
                "https://api.linkedin.com/rest/images",
                _json({"value": {"uploadUrl": "https://up/1", "image": "urn:li:image:1"}}),
            ),
            ("PUT", "https://up/1", lambda r: httpx.Response(201)),
            (
                "POST",
                "https://api.linkedin.com/rest/images",
                _json({"value": {"uploadUrl": "https://up/2", "image": "urn:li:image:2"}}),
            ),
            ("PUT", "https://up/2", lambda r: httpx.Response(201)),
            (
                "POST",
                "https://api.linkedin.com/rest/posts",
                lambda r: httpx.Response(201, headers={"x-restli-id": "urn:li:share:5"}),
            ),
        ]
    )
    a = LinkedInAdapter("linkedin_organization", cas.http())
    await a.publish(
        CREDS,
        _payload(
            platform="linkedin_organization",
            platform_account_id="42",
            link_url=None,
            media=[IMG, IMG],
        ),
    )
    post = json.loads(cas.requests[-1].content)
    assert post["author"] == "urn:li:organization:42"
    assert [i["id"] for i in post["content"]["multiImage"]["images"]] == [
        "urn:li:image:1",
        "urn:li:image:2",
    ]


async def test_linkedin_member_metrics_report_no_impressions() -> None:
    cas = Cassette(
        [
            (
                "GET",
                "https://api.linkedin.com/rest/socialActions/",
                _json(
                    {
                        "likesSummary": {"totalLikes": 12},
                        "commentsSummary": {"aggregatedTotalComments": 3},
                    }
                ),
            )
        ]
    )
    rows = await LinkedInAdapter("linkedin_member", cas.http()).post_metrics(
        CREDS, "abc", "urn:li:share:1"
    )
    by = {r.metric_key: r.value for r in rows}
    assert by == {"likes": 12.0, "comments": 3.0, "impressions": None}


async def test_linkedin_reconciles_by_text() -> None:
    p = _payload(
        platform="linkedin_member", platform_account_id="abc", link_placement="first_comment"
    )
    want = commentary(p.body, p.hashtags, p.link_url, p.link_placement)
    cas = Cassette(
        [
            (
                "GET",
                "https://api.linkedin.com/rest/posts",
                _json(
                    {
                        "elements": [
                            {"id": "urn:li:share:3", "commentary": want, "createdAt": 4102444800000}
                        ]
                    }
                ),
            )
        ]
    )
    found = await LinkedInAdapter("linkedin_member", cas.http()).find_existing(
        CREDS, p, datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert found is not None
    assert found.platform_post_id == "urn:li:share:3"


# ---------------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    ("status", "headers", "body", "kind"),
    [
        (429, {"retry-after": "42"}, {"message": "slow down"}, RateLimited),
        (401, {}, {"message": "expired"}, AuthExpired),
        (503, {}, {"message": "unavailable"}, TransientError),
        (422, {}, {"message": "Content is a duplicate"}, PermanentError),
    ],
)
async def test_error_classification(
    status: int, headers: dict[str, str], body: Any, kind: type[Exception]
) -> None:
    cas = Cassette([("POST", "https://api.x.com/2/tweets", _json(body, status, headers))])
    with pytest.raises(kind) as exc:
        await XAdapter(cas.http()).publish(CREDS, _payload(link_url=None))
    if kind is RateLimited:
        assert exc.value.retry_after == 42  # type: ignore[attr-defined]
    assert "tok-123" not in str(exc.value)


async def test_timeout_is_transient() -> None:
    def boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    cas = Cassette([("POST", "https://api.x.com/2/tweets", boom)])
    with pytest.raises(TransientError):
        await XAdapter(cas.http()).publish(CREDS, _payload(link_url=None))


def test_graph_code_190_is_auth() -> None:
    resp = httpx.Response(400, json={"error": {"code": 190, "message": "Session expired"}})
    assert graph_auth_error(resp)
    assert not graph_auth_error(httpx.Response(400, json={"error": {"code": 100}}))


# ------------------------------------------------------------------------------- X


async def test_x_thread_with_media_and_link_reply() -> None:
    ids = iter(["t1", "t2", "t3"])
    cas = Cassette(
        [
            ("POST", "https://api.x.com/2/media/upload", _json({"data": {"id": "m1"}})),
            ("POST", "https://api.x.com/2/media/metadata", _json({})),
            (
                "POST",
                "https://api.x.com/2/tweets",
                lambda r: httpx.Response(201, json={"data": {"id": next(ids)}}),
            ),
            (
                "POST",
                "https://api.x.com/2/tweets",
                lambda r: httpx.Response(201, json={"data": {"id": next(ids)}}),
            ),
            (
                "POST",
                "https://api.x.com/2/tweets",
                lambda r: httpx.Response(201, json={"data": {"id": next(ids)}}),
            ),
        ]
    )
    res = await XAdapter(cas.http()).publish(
        CREDS, _payload(thread_parts=["1/ first", "2/ second"], media=[IMG])
    )
    assert res.platform_post_id == "t1"
    assert res.extra_ids == {"part_2": "t2", "reply": "t3"}
    first = json.loads(cas.requests[2].content)
    assert first == {"text": "1/ first", "media": {"media_ids": ["m1"]}}
    second = json.loads(cas.requests[3].content)
    assert second["reply"] == {"in_reply_to_tweet_id": "t1"}
    assert "#postgres" in second["text"]
    link = json.loads(cas.requests[4].content)
    assert link == {
        "text": "https://www.postgresql.org/about/news/",
        "reply": {"in_reply_to_tweet_id": "t1"},
    }
    assert cas.requests[2].headers["Authorization"] == "Bearer tok-123"


async def test_x_missing_token_is_auth() -> None:
    with pytest.raises(AuthExpired):
        await XAdapter(Cassette([]).http()).publish({}, _payload())


async def test_x_metrics_map_public_metrics() -> None:
    cas = Cassette(
        [
            (
                "GET",
                "https://api.x.com/2/tweets/9",
                _json(
                    {
                        "data": {
                            "public_metrics": {
                                "impression_count": 500,
                                "like_count": 9,
                                "reply_count": 2,
                                "retweet_count": 3,
                                "quote_count": 1,
                            }
                        }
                    }
                ),
            )
        ]
    )
    by = {r.metric_key: r.value for r in await XAdapter(cas.http()).post_metrics(CREDS, "a", "9")}
    assert by["impressions"] == 500
    assert by["shares"] == 4
    assert by["saves"] is None


# -------------------------------------------------------------------------- Facebook


async def test_facebook_text_and_link_post() -> None:
    cas = Cassette(
        [
            ("POST", "https://graph.facebook.com/v23.0/page1/feed", _json({"id": "page1_9"})),
            (
                "GET",
                "https://graph.facebook.com/v23.0/page1_9",
                _json({"permalink_url": "https://facebook.com/p/9"}),
            ),
        ]
    )
    res = await FacebookPageAdapter(cas.http(is_auth_error=graph_auth_error)).publish(
        CREDS, _payload(platform="facebook", platform_account_id="page1", link_placement="body")
    )
    assert res.platform_post_id == "page1_9"
    assert res.permalink == "https://facebook.com/p/9"
    form = _form(cas.requests[0])
    assert form["link"] == ["https://www.postgresql.org/about/news/"]
    assert "#postgres" in form["message"][0]
    assert form["access_token"] == ["tok-123"]


async def test_facebook_multi_photo_uses_attached_media() -> None:
    cas = Cassette(
        [
            ("POST", "https://graph.facebook.com/v23.0/p/photos", _json({"id": "ph1"})),
            ("POST", "https://graph.facebook.com/v23.0/p/photos", _json({"id": "ph2"})),
            ("POST", "https://graph.facebook.com/v23.0/p/feed", _json({"id": "p_1"})),
            ("GET", "https://graph.facebook.com/v23.0/p_1", _json({"permalink_url": "u"})),
        ]
    )
    await FacebookPageAdapter(cas.http()).publish(
        CREDS, _payload(platform="facebook", platform_account_id="p", media=[IMG, IMG])
    )
    feed = _form(cas.requests[2])
    assert json.loads(feed["attached_media[1]"][0]) == {"media_fbid": "ph2"}


async def test_facebook_auth_error_code_190() -> None:
    cas = Cassette(
        [
            (
                "POST",
                "https://graph.facebook.com/v23.0/p/feed",
                _json({"error": {"code": 190, "message": "Session expired"}}, 400),
            )
        ]
    )
    with pytest.raises(AuthExpired):
        await FacebookPageAdapter(cas.http(is_auth_error=graph_auth_error)).publish(
            CREDS, _payload(platform="facebook", platform_account_id="p")
        )


# ------------------------------------------------------------------------- Instagram


async def test_instagram_requires_media() -> None:
    with pytest.raises(PermanentError, match="image"):
        await InstagramAdapter(Cassette([]).http()).publish(CREDS, _payload(platform="instagram"))


async def test_instagram_single_image_container_flow() -> None:
    cas = Cassette(
        [
            ("POST", "https://graph.facebook.com/v23.0/ig1/media", _json({"id": "c1"})),
            ("GET", "https://graph.facebook.com/v23.0/c1", _json({"status_code": "IN_PROGRESS"})),
            ("GET", "https://graph.facebook.com/v23.0/c1", _json({"status_code": "FINISHED"})),
            ("POST", "https://graph.facebook.com/v23.0/ig1/media_publish", _json({"id": "m9"})),
            (
                "GET",
                "https://graph.facebook.com/v23.0/m9",
                _json({"permalink": "https://instagram.com/p/x"}),
            ),
        ]
    )
    res = await InstagramAdapter(cas.http(), poll_seconds=0).publish(
        CREDS,
        _payload(
            platform="instagram", platform_account_id="ig1", media=[IMG], link_placement="none"
        ),
    )
    assert res.platform_post_id == "m9"
    created = _form(cas.requests[0])
    assert created["image_url"] == [IMG.public_url]
    assert created["alt_text"] == ["A chart of read latency"]
    assert "postgresql.org" not in created["caption"][0]  # Instagram: no link in the caption


async def test_instagram_carousel() -> None:
    cas = Cassette(
        [
            ("POST", "https://graph.facebook.com/v23.0/ig/media", _json({"id": "k1"})),
            ("POST", "https://graph.facebook.com/v23.0/ig/media", _json({"id": "k2"})),
            ("GET", "https://graph.facebook.com/v23.0/k1", _json({"status_code": "FINISHED"})),
            ("GET", "https://graph.facebook.com/v23.0/k2", _json({"status_code": "FINISHED"})),
            ("POST", "https://graph.facebook.com/v23.0/ig/media", _json({"id": "parent"})),
            ("GET", "https://graph.facebook.com/v23.0/parent", _json({"status_code": "FINISHED"})),
            ("POST", "https://graph.facebook.com/v23.0/ig/media_publish", _json({"id": "m1"})),
            ("GET", "https://graph.facebook.com/v23.0/m1", _json({"permalink": "p"})),
        ]
    )
    await InstagramAdapter(cas.http(), poll_seconds=0).publish(
        CREDS, _payload(platform="instagram", platform_account_id="ig", media=[IMG, IMG])
    )
    parent = _form(cas.requests[4])
    assert parent["media_type"] == ["CAROUSEL"]
    assert parent["children"] == ["k1,k2"]
    assert _form(cas.requests[0])["is_carousel_item"] == ["true"]


async def test_instagram_container_error_is_permanent() -> None:
    cas = Cassette(
        [
            ("POST", "https://graph.facebook.com/v23.0/ig/media", _json({"id": "c"})),
            ("GET", "https://graph.facebook.com/v23.0/c", _json({"status_code": "ERROR"})),
        ]
    )
    with pytest.raises(PermanentError):
        await InstagramAdapter(cas.http(), poll_seconds=0).publish(
            CREDS, _payload(platform="instagram", platform_account_id="ig", media=[IMG])
        )


# --------------------------------------------------------------------------- Threads


async def test_threads_text_post_with_link_reply() -> None:
    cas = Cassette(
        [
            ("POST", "https://graph.threads.net/v1.0/u1/threads", _json({"id": "c1"})),
            ("POST", "https://graph.threads.net/v1.0/u1/threads_publish", _json({"id": "p1"})),
            ("POST", "https://graph.threads.net/v1.0/u1/threads", _json({"id": "c2"})),
            ("POST", "https://graph.threads.net/v1.0/u1/threads_publish", _json({"id": "p2"})),
            (
                "GET",
                "https://graph.threads.net/v1.0/p1",
                _json({"permalink": "https://threads.net/p1"}),
            ),
        ]
    )
    res = await ThreadsAdapter(cas.http(), settle_seconds=0).publish(
        CREDS, _payload(platform="threads", platform_account_id="u1")
    )
    assert res.platform_post_id == "p1"
    assert res.extra_ids == {"reply": "p2"}
    first = _form(cas.requests[0])
    assert first["media_type"] == ["TEXT"]
    assert first["text"][0].endswith("#postgres")
    reply = _form(cas.requests[2])
    assert reply["reply_to_id"] == ["p1"]
    assert reply["text"] == ["https://www.postgresql.org/about/news/"]


# ------------------------------------------------------------------------------ fake


async def test_fake_adapter_failure_injection_and_reconciliation() -> None:
    fake = FakePlatformAdapter("x")
    with pytest.raises(PermanentError):
        await fake.publish({"handle": "x_fail"}, _payload())
    flaky = _payload(idempotency_key="f" * 64)
    with pytest.raises(TransientError):
        await fake.publish({"handle": "x_flaky"}, flaky)
    assert (await fake.publish({"handle": "x_flaky"}, flaky)).platform_post_id
    lost = _payload(idempotency_key="l" * 64)
    with pytest.raises(TransientError):
        await fake.publish({"handle": "x_lost"}, lost)
    found = await fake.find_existing({"handle": "x_lost"}, lost, datetime.now(UTC))
    assert found is not None
    with pytest.raises(RateLimited):
        await fake.publish({"handle": "x_ratelimit"}, _payload(idempotency_key="r" * 64))
    with pytest.raises(AuthExpired):
        await fake.publish({"handle": "x_revoked"}, _payload())
    metrics = {m.metric_key: m.value for m in await fake.post_metrics({}, "a", "x-1")}
    assert metrics["saves"] is None
    assert metrics["impressions"] is not None
