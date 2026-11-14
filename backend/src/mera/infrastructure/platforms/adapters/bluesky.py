"""Bluesky adapter (P2-M3.1) over the AT Protocol XRPC API.

A post is an ``app.bsky.feed.post`` record written with ``com.atproto.repo.createRecord``
to the account's own repository (its DID) on its PDS. Rich text is plain text plus
*facets* (links, hashtags), whose ranges are **UTF-8 byte offsets**, not characters: a
wrong offset silently breaks the link, so it is computed here once and tested with
multi-byte text. Images (up to four, about 1 MB each) are uploaded as blobs first and
embedded with their alt text. Thread parts are replies chained with ``root``/``parent``
strong references (``uri`` + ``cid``).

Credentials are the session from ``com.atproto.server.createSession`` (app password,
never stored) and are refreshed with ``refreshSession``. Verified against the AT Protocol
and Bluesky lexicons (app.bsky.feed.post, app.bsky.richtext.facet, app.bsky.embed.images);
re-verify on change.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from mera.domain.ports.platform_adapter import (
    AuthExpired,
    MetricReading,
    PermanentError,
    PublishPayload,
    PublishResult,
)
from mera.infrastructure.platforms.adapters.http import PlatformHTTP, same_text

DEFAULT_SERVICE = "https://bsky.social"
MAX_IMAGES = 4
MAX_GRAPHEMES = 300

_URL = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)\]]")
_TAG = re.compile(r"(?<![\w&])#([^\s#.,;:!?()\[\]]{1,64})")


def _auth_error(resp: Any) -> bool:
    if resp.status_code == 401:
        return True
    if resp.status_code == 400:
        try:
            err = resp.json().get("error")
        except ValueError:
            return False
        return err in ("ExpiredToken", "InvalidToken", "AuthenticationRequired")
    return False


def text_for(body: str, hashtags: list[str], link_url: str | None, placement: str) -> str:
    parts = [body.strip()]
    if link_url and placement == "body" and link_url not in body:
        parts.append(link_url)
    if hashtags:
        parts.append(" ".join("#" + h.lstrip("#") for h in hashtags))
    return "\n\n".join(p for p in parts if p)


def facets_for(text: str) -> list[dict[str, Any]]:
    """Link and hashtag facets with UTF-8 byte ranges."""

    def byte_range(start: int, end: int) -> dict[str, int]:
        return {
            "byteStart": len(text[:start].encode("utf-8")),
            "byteEnd": len(text[:end].encode("utf-8")),
        }

    out: list[dict[str, Any]] = []
    for m in _URL.finditer(text):
        out.append(
            {
                "index": byte_range(m.start(), m.end()),
                "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group(0)}],
            }
        )
    for m in _TAG.finditer(text):
        if any(
            f["index"]["byteStart"] <= len(text[: m.start()].encode()) < f["index"]["byteEnd"]
            for f in out
        ):
            continue  # a "#" inside a link is part of the link
        out.append(
            {
                "index": byte_range(m.start(), m.end()),
                "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": m.group(1)}],
            }
        )
    return sorted(out, key=lambda f: f["index"]["byteStart"])


def _service(credentials: dict[str, Any]) -> str:
    return str(credentials.get("service") or DEFAULT_SERVICE).rstrip("/")


def _token(credentials: dict[str, Any]) -> str:
    token = credentials.get("access_jwt") or credentials.get("access_token")
    if not token:
        raise AuthExpired("no Bluesky session stored for this account")
    return str(token)


def web_url(handle: str, uri: str) -> str:
    """``at://did/app.bsky.feed.post/rkey`` → the bsky.app link people can open."""
    rkey = uri.rsplit("/", 1)[-1]
    return f"https://bsky.app/profile/{handle}/post/{rkey}"


class BlueskyAdapter:
    platform = "bluesky"

    def __init__(self, http: PlatformHTTP | None = None, *, transport: Any | None = None) -> None:
        self._http = http or PlatformHTTP(transport=transport, is_auth_error=_auth_error)

    async def _xrpc(
        self, credentials: dict[str, Any], method: str, nsid: str, **kw: Any
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {_token(credentials)}", **kw.pop("headers", {})}
        return await self._http.json(
            method, f"{_service(credentials)}/xrpc/{nsid}", headers=headers, **kw
        )

    async def _upload(self, credentials: dict[str, Any], data: bytes, mime: str) -> dict[str, Any]:
        out = await self._xrpc(
            credentials,
            "POST",
            "com.atproto.repo.uploadBlob",
            content=data,
            headers={"Content-Type": mime},
        )
        blob = out.get("blob")
        if not isinstance(blob, dict):
            raise PermanentError("Bluesky did not accept the image")
        return blob

    async def _create(
        self, credentials: dict[str, Any], repo: str, record: dict[str, Any]
    ) -> dict[str, str]:
        out = await self._xrpc(
            credentials,
            "POST",
            "com.atproto.repo.createRecord",
            json_body={"repo": repo, "collection": "app.bsky.feed.post", "record": record},
        )
        return {"uri": str(out["uri"]), "cid": str(out["cid"])}

    def _record(self, text: str, now: datetime) -> dict[str, Any]:
        record: dict[str, Any] = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": now.isoformat().replace("+00:00", "Z"),
        }
        facets = facets_for(text)
        if facets:
            record["facets"] = facets
        return record

    async def publish(self, credentials: dict[str, Any], payload: PublishPayload) -> PublishResult:
        repo = str(credentials.get("did") or payload.platform_account_id)
        handle = str(credentials.get("handle") or repo)
        now = datetime.now(UTC)
        parts = payload.thread_parts or [payload.body]
        first = self._record(
            text_for(parts[0], payload.hashtags, payload.link_url, payload.link_placement), now
        )
        if payload.media:
            images = []
            for m in payload.media[:MAX_IMAGES]:
                blob = await self._upload(credentials, m.data, m.mime_type)
                image: dict[str, Any] = {"image": blob, "alt": (m.alt_text or "")[:2000]}
                if m.width and m.height:
                    image["aspectRatio"] = {"width": m.width, "height": m.height}
                images.append(image)
            first["embed"] = {"$type": "app.bsky.embed.images", "images": images}
        root = await self._create(credentials, repo, first)
        parent = root
        extra: dict[str, str] = {"uri": root["uri"], "cid": root["cid"]}
        for i, part in enumerate(parts[1:], start=2):
            rec = self._record(part.strip(), now)
            rec["reply"] = {"root": root, "parent": parent}
            parent = await self._create(credentials, repo, rec)
            extra[f"part_{i}"] = parent["uri"]
        if payload.link_url and payload.link_placement == "reply":
            rec = self._record(payload.link_url, now)
            rec["reply"] = {"root": root, "parent": root}
            extra["reply"] = (await self._create(credentials, repo, rec))["uri"]
        return PublishResult(root["uri"], web_url(handle, root["uri"]), extra)

    async def find_existing(
        self, credentials: dict[str, Any], payload: PublishPayload, since: datetime
    ) -> PublishResult | None:
        actor = str(credentials.get("did") or payload.platform_account_id)
        handle = str(credentials.get("handle") or actor)
        feed = await self._xrpc(
            credentials,
            "GET",
            "app.bsky.feed.getAuthorFeed",
            params={"actor": actor, "limit": 10, "filter": "posts_no_replies"},
        )
        want = text_for(
            (payload.thread_parts or [payload.body])[0],
            payload.hashtags,
            payload.link_url,
            payload.link_placement,
        )
        for item in feed.get("feed", []):
            post = item.get("post") or {}
            record = post.get("record") or {}
            created = str(record.get("createdAt") or "")
            try:
                if datetime.fromisoformat(created.replace("Z", "+00:00")) < since:
                    continue
            except ValueError:
                continue
            if same_text(record.get("text"), want):
                uri = str(post["uri"])
                return PublishResult(uri, web_url(handle, uri))
        return None

    async def post_metrics(
        self, credentials: dict[str, Any], platform_account_id: str, platform_post_id: str
    ) -> list[MetricReading]:
        out = await self._xrpc(
            credentials, "GET", "app.bsky.feed.getPosts", params={"uris": platform_post_id}
        )
        posts = out.get("posts") or []
        p = posts[0] if posts else {}

        def num(key: str) -> float | None:
            v = p.get(key)
            return float(v) if isinstance(v, int | float) else None

        shares = None
        if num("repostCount") is not None or num("quoteCount") is not None:
            shares = (num("repostCount") or 0) + (num("quoteCount") or 0)
        return [
            # Bluesky does not report views or impressions.
            MetricReading("impressions", None, "not reported by Bluesky"),
            MetricReading("likes", num("likeCount"), "getPosts.likeCount"),
            MetricReading("comments", num("replyCount"), "getPosts.replyCount"),
            MetricReading("shares", shares, "getPosts.repostCount+quoteCount"),
        ]

    async def account_metrics(
        self, credentials: dict[str, Any], platform_account_id: str
    ) -> list[MetricReading]:
        actor = str(credentials.get("did") or platform_account_id)
        out = await self._xrpc(
            credentials, "GET", "app.bsky.actor.getProfile", params={"actor": actor}
        )
        v = out.get("followersCount")
        return [
            MetricReading(
                "followers", float(v) if isinstance(v, int) else None, "getProfile.followersCount"
            )
        ]
