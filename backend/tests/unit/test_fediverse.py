"""P2-M3: Bluesky and Mastodon adapters and connectors against recorded request shapes."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from mera.domain.ports.platform_adapter import AuthExpired, MediaFile, PublishPayload
from mera.infrastructure.platforms.adapters.bluesky import BlueskyAdapter, facets_for, web_url
from mera.infrastructure.platforms.adapters.http import PlatformHTTP
from mera.infrastructure.platforms.adapters.mastodon import MastodonAdapter, plain
from mera.infrastructure.platforms.connectors import ConnectorError, ReauthRequired
from mera.infrastructure.platforms.fediverse_connectors import (
    BlueskyConnector,
    InvalidInstance,
    MastodonClient,
    MastodonConnector,
    public_host,
)


def _payload(**over: Any) -> PublishPayload:
    base: dict[str, Any] = {
        "platform": "bluesky",
        "platform_account_id": "did:plc:abc",
        "account_type": "personal",
        "body": "Postgres 18 is out",
        "thread_parts": None,
        "hashtags": ["postgres"],
        "link_url": "https://example.com/pg18",
        "link_placement": "body",
        "media": [],
        "idempotency_key": "key-1",
    }
    return PublishPayload(**{**base, **over})


class Recorder:
    def __init__(self, handler: Any) -> None:
        self.calls: list[httpx.Request] = []
        self._handler = handler

    def transport(self) -> httpx.MockTransport:
        def h(req: httpx.Request) -> httpx.Response:
            self.calls.append(req)
            return self._handler(req)

        return httpx.MockTransport(h)


# ------------------------------------------------------------------------- Bluesky


def test_facets_use_utf8_byte_offsets() -> None:
    text = "Café ☕ news https://ex.com/a #pg"
    facets = facets_for(text)
    link, tag = facets
    raw = text.encode()
    assert raw[link["index"]["byteStart"] : link["index"]["byteEnd"]] == b"https://ex.com/a"
    assert raw[tag["index"]["byteStart"] : tag["index"]["byteEnd"]] == b"#pg"
    assert tag["features"][0]["tag"] == "pg"
    # A "#" inside a URL is part of the link, not a tag.
    only = facets_for("see https://ex.com/page#section now")
    assert [f["features"][0]["$type"] for f in only] == ["app.bsky.richtext.facet#link"]


async def test_bluesky_publish_with_images_and_a_thread() -> None:
    created: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("com.atproto.repo.uploadBlob"):
            assert req.headers["content-type"] == "image/jpeg"
            return httpx.Response(200, json={"blob": {"$type": "blob", "ref": {"$link": "b1"}}})
        if req.url.path.endswith("com.atproto.repo.createRecord"):
            body = json.loads(req.content)
            created.append(body)
            n = len(created)
            return httpx.Response(
                200, json={"uri": f"at://did:plc:abc/app.bsky.feed.post/r{n}", "cid": f"c{n}"}
            )
        return httpx.Response(404)

    rec = Recorder(handler)
    adapter = BlueskyAdapter(PlatformHTTP(transport=rec.transport()))
    creds = {
        "did": "did:plc:abc",
        "handle": "acme.bsky.social",
        "access_jwt": "AT",
        "service": "https://pds.example",
    }
    media = [MediaFile(b"\xff\xd8jpeg", "image/jpeg", "A chart", 1200, 675)]
    out = await adapter.publish(creds, _payload(thread_parts=["One", "Two"], media=media))
    assert out.platform_post_id == "at://did:plc:abc/app.bsky.feed.post/r1"
    assert out.permalink == "https://bsky.app/profile/acme.bsky.social/post/r1"
    assert all(c.url.host == "pds.example" for c in rec.calls)
    assert all(c.headers["authorization"] == "Bearer AT" for c in rec.calls)
    first = created[0]["record"]
    assert first["embed"]["images"][0]["alt"] == "A chart"
    assert first["embed"]["images"][0]["aspectRatio"] == {"width": 1200, "height": 675}
    assert "https://example.com/pg18" in first["text"]
    assert created[1]["record"]["reply"]["root"] == {"uri": out.platform_post_id, "cid": "c1"}
    assert out.extra_ids["part_2"].endswith("/r2")


async def test_bluesky_errors_and_metrics() -> None:
    def expired(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "ExpiredToken", "message": "Token has expired"})

    adapter = BlueskyAdapter(
        PlatformHTTP(
            transport=httpx.MockTransport(expired),
            is_auth_error=__import__(
                "mera.infrastructure.platforms.adapters.bluesky", fromlist=["_auth_error"]
            )._auth_error,
        )
    )
    with pytest.raises(AuthExpired):
        await adapter.publish({"did": "d", "access_jwt": "x"}, _payload())
    with pytest.raises(AuthExpired):
        await BlueskyAdapter().publish({"did": "d"}, _payload())

    def posts(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"posts": [{"likeCount": 5, "replyCount": 2, "repostCount": 1, "quoteCount": 1}]},
        )

    m = await BlueskyAdapter(PlatformHTTP(transport=httpx.MockTransport(posts))).post_metrics(
        {"access_jwt": "x"}, "did", "at://x/app.bsky.feed.post/1"
    )
    by = {r.metric_key: r.value for r in m}
    assert by == {"impressions": None, "likes": 5.0, "comments": 2.0, "shares": 2.0}
    assert (
        web_url("a.b", "at://did/app.bsky.feed.post/xyz") == "https://bsky.app/profile/a.b/post/xyz"
    )


async def test_bluesky_reconciliation_matches_recent_text() -> None:
    def feed(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "feed": [
                    {
                        "post": {
                            "uri": "at://d/app.bsky.feed.post/k",
                            "record": {
                                "text": "Postgres 18 is out\n\nhttps://example.com/pg18\n\n#postgres",
                                "createdAt": "2026-11-10T10:00:00Z",
                            },
                        }
                    }
                ]
            },
        )

    from datetime import UTC, datetime

    adapter = BlueskyAdapter(PlatformHTTP(transport=httpx.MockTransport(feed)))
    found = await adapter.find_existing(
        {"did": "d", "handle": "h", "access_jwt": "x"},
        _payload(),
        datetime(2026, 11, 10, 9, tzinfo=UTC),
    )
    assert found is not None
    assert found.platform_post_id == "at://d/app.bsky.feed.post/k"
    later = await adapter.find_existing(
        {"did": "d", "access_jwt": "x"}, _payload(), datetime(2026, 11, 10, 11, tzinfo=UTC)
    )
    assert later is None


async def _public(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


async def _private(host: str, port: int) -> list[str]:
    return ["10.0.0.5"]


async def test_bluesky_sign_in_accepts_only_app_passwords() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("createSession"):
            body = json.loads(req.content)
            if body["password"] != "abcd-efgh-ijkl-mnop":
                return httpx.Response(401, json={"error": "AuthenticationRequired"})
            return httpx.Response(
                200,
                json={
                    "did": "did:plc:xyz",
                    "handle": "acme.bsky.social",
                    "accessJwt": "A",
                    "refreshJwt": "R",
                },
            )
        if req.url.path.endswith("getProfile"):
            return httpx.Response(200, json={"displayName": "Acme", "avatar": "https://cdn/x.jpg"})
        return httpx.Response(404)

    c = BlueskyConnector(transport=httpx.MockTransport(handler), resolve=_public)
    with pytest.raises(ConnectorError, match="app password"):
        await c.sign_in(identifier="acme.bsky.social", secret="my real password")
    with pytest.raises(ConnectorError, match="did not accept"):
        await c.sign_in(identifier="acme.bsky.social", secret="zzzz-zzzz-zzzz-zzzz")
    ident = await c.sign_in(identifier="@acme.bsky.social", secret="abcd-efgh-ijkl-mnop")
    assert ident.platform_account_id == "did:plc:xyz"
    assert ident.display_name == "Acme"
    assert ident.credentials == {
        "did": "did:plc:xyz",
        "handle": "acme.bsky.social",
        "service": "https://bsky.social",
        "access_jwt": "A",
        "refresh_jwt": "R",
    }
    assert "abcd-efgh-ijkl-mnop" not in json.dumps(ident.credentials)
    with pytest.raises(InvalidInstance):
        await BlueskyConnector(resolve=_private).sign_in(
            identifier="x", secret="abcd-efgh-ijkl-mnop", service="pds.internal-corp.com"
        )


async def test_bluesky_refresh_and_health() -> None:
    state = {"refresh": 200, "session": 200, "error": None}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("refreshSession"):
            if state["refresh"] != 200:
                return httpx.Response(state["refresh"], json={"error": "ExpiredToken"})
            return httpx.Response(200, json={"accessJwt": "A2", "refreshJwt": "R2"})
        return httpx.Response(
            state["session"], json={"error": state["error"]} if state["error"] else {}
        )

    c = BlueskyConnector(transport=httpx.MockTransport(handler))
    creds, exp = await c.refresh({"refresh_jwt": "R"})
    assert creds["access_jwt"] == "A2"
    assert exp is not None
    assert (await c.check_health({"access_jwt": "A"})).token_valid
    state.update(session=400, error="ExpiredToken")
    assert (await c.check_health({"access_jwt": "A"})).last_error == "session renews"
    state.update(session=401, error="InvalidToken")
    with pytest.raises(ReauthRequired):
        await c.check_health({"access_jwt": "A"})
    state.update(refresh=400)
    with pytest.raises(ReauthRequired):
        await c.refresh({"refresh_jwt": "R"})


# ------------------------------------------------------------------------ Mastodon


async def test_public_host_normalizes_and_blocks_private_addresses() -> None:
    assert await public_host("https://Mastodon.Social/", resolve=_public) == "mastodon.social"
    assert await public_host("fosstodon.org", resolve=_public) == "fosstodon.org"
    for bad in (
        "http://mastodon.social",
        "mastodon.social:8443",
        "localhost",
        "a b",
        "user@host.org",
    ):
        with pytest.raises(InvalidInstance):
            await public_host(bad, resolve=_public)
    with pytest.raises(InvalidInstance, match="not a public"):
        await public_host("intranet.example.com", resolve=_private)


async def test_mastodon_publish_waits_for_media_and_uses_idempotency_keys() -> None:
    statuses: list[httpx.Request] = []
    polls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v2/media":
            assert b"An office" in req.content  # the alt text travels as description
            return httpx.Response(202, json={"id": "m1", "url": None})
        if req.url.path == "/api/v1/media/m1":
            polls["n"] += 1
            return httpx.Response(
                200, json={"id": "m1", "url": "https://x/m1" if polls["n"] > 1 else None}
            )
        if req.url.path == "/api/v1/statuses":
            statuses.append(req)
            return httpx.Response(
                200,
                json={
                    "id": f"s{len(statuses)}",
                    "url": f"https://masto.example/@a/s{len(statuses)}",
                },
            )
        return httpx.Response(404)

    adapter = MastodonAdapter(PlatformHTTP(transport=httpx.MockTransport(handler)), poll_seconds=0)
    creds = {"instance": "masto.example", "access_token": "T"}
    media = [MediaFile(b"img", "image/jpeg", "An office")]
    out = await adapter.publish(
        creds, _payload(platform="mastodon", thread_parts=["One", "Two"], media=media)
    )
    assert out.platform_post_id == "s1"
    assert out.permalink == "https://masto.example/@a/s1"
    assert polls["n"] == 2
    first = json.loads(statuses[0].content)
    assert first["media_ids"] == ["m1"]
    assert statuses[0].headers["idempotency-key"] == "key-1"
    assert statuses[1].headers["idempotency-key"] == "key-1:2"
    assert json.loads(statuses[1].content)["in_reply_to_id"] == "s1"
    with pytest.raises(AuthExpired):
        await adapter.publish({"access_token": "T"}, _payload())


def test_mastodon_html_to_text() -> None:
    assert (
        plain("<p>Hello <a href='x'>#pg</a></p><p>Second &amp; last</p>")
        == "Hello #pg\nSecond & last"
    )
    assert plain(None) == ""


async def test_mastodon_app_registration_and_oauth() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/apps":
            return httpx.Response(200, json={"client_id": "cid", "client_secret": "csec"})
        if req.url.path == "/oauth/token":
            form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
            assert form["code_verifier"] == "ver"
            assert form["client_secret"] == "csec"
            return httpx.Response(200, json={"access_token": "TOK"})
        if req.url.path == "/api/v1/accounts/verify_credentials":
            return httpx.Response(
                200, json={"id": "42", "username": "acme", "display_name": "Acme"}
            )
        return httpx.Response(404)

    base = MastodonConnector(
        website="https://mera.example", transport=httpx.MockTransport(handler), resolve=_public
    )
    client = await base.register_app("masto.example", "https://api.mera.example/cb")
    assert client == MastodonClient("cid", "csec")
    inst = base.for_instance("masto.example", client)
    url = inst.authorization_url(
        state="st", code_challenge="ch", redirect_uri="https://api.mera.example/cb"
    )
    assert url.startswith("https://masto.example/oauth/authorize?")
    assert "code_challenge_method=S256" in url
    ident = await inst.exchange(
        code="c", code_verifier="ver", redirect_uri="https://api.mera.example/cb"
    )
    assert ident.platform_account_id == "42@masto.example"
    assert ident.handle == "acme@masto.example"
    assert ident.credentials["instance"] == "masto.example"
    with pytest.raises(ConnectorError):
        base.authorization_url(state="s", code_challenge="c", redirect_uri="r")
