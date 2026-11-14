"""X adapter (M8.11c) over the X API v2.

- Images: ``POST /2/media/upload`` (multipart, ``media_category=tweet_image``), alt text
  with ``POST /2/media/metadata``; at most four per post.
- Posts: ``POST /2/tweets``. Threads chain ``reply.in_reply_to_tweet_id``; the source link
  goes in a reply (capabilities ``link_placement=reply``: links in the first post are
  down-ranked).
- The posting quota is shared by every account on the app's tier (docs/08), so 429s are
  common under load: they back off on ``x-rate-limit-reset``.

Verified against the X API v2 docs (Manage Posts, Media Upload v2); re-verify on change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mera.domain.ports.platform_adapter import (
    MetricReading,
    PublishPayload,
    PublishResult,
)
from mera.infrastructure.platforms.adapters.http import PlatformHTTP, bearer, same_text
from mera.infrastructure.platforms.adapters.meta import _num

API = "https://api.x.com/2"


def text_for(body: str, hashtags: list[str], link_url: str | None, placement: str) -> str:
    parts = [body.strip()]
    if link_url and placement == "body":
        parts.append(link_url)
    if hashtags:
        parts.append(" ".join("#" + h.lstrip("#") for h in hashtags))
    return "\n\n".join(p for p in parts if p)


class XAdapter:
    platform = "x"

    def __init__(self, http: PlatformHTTP | None = None) -> None:
        self._http = http or PlatformHTTP()

    async def _media(
        self, credentials: dict[str, Any], data: bytes, mime: str, alt: str | None
    ) -> str:
        body = await self._http.json(
            "POST",
            f"{API}/media/upload",
            headers=bearer(credentials),
            data={"media_category": "tweet_image", "media_type": mime},
            files={"media": ("image", data, mime)},
        )
        media_id = str(
            (body.get("data") or body).get("id")
            or (body.get("data") or body).get("media_id_string")
        )
        if alt:
            await self._http.request(
                "POST",
                f"{API}/media/metadata",
                headers=bearer(credentials),
                json_body={"id": media_id, "metadata": {"alt_text": {"text": alt[:1000]}}},
            )
        return media_id

    async def _tweet(self, credentials: dict[str, Any], fields: dict[str, Any]) -> str:
        body = await self._http.json(
            "POST", f"{API}/tweets", headers=bearer(credentials), json_body=fields
        )
        return str(body["data"]["id"])

    async def publish(self, credentials: dict[str, Any], payload: PublishPayload) -> PublishResult:
        parts = payload.thread_parts or [payload.body]
        first: dict[str, Any] = {
            "text": text_for(parts[0], payload.hashtags, payload.link_url, payload.link_placement)
            if len(parts) == 1
            else parts[0]
        }
        if payload.media:
            ids = [
                await self._media(credentials, m.data, m.mime_type, m.alt_text)
                for m in payload.media[:4]
            ]
            first["media"] = {"media_ids": ids}
        root = await self._tweet(credentials, first)
        extra: dict[str, str] = {}
        parent = root
        for i, part in enumerate(parts[1:], start=2):
            text = part
            if i == len(parts) and payload.hashtags:
                text = text_for(part, payload.hashtags, None, "none")
            parent = await self._tweet(
                credentials, {"text": text, "reply": {"in_reply_to_tweet_id": parent}}
            )
            extra[f"part_{i}"] = parent
        if payload.link_url and payload.link_placement == "reply":
            extra["reply"] = await self._tweet(
                credentials, {"text": payload.link_url, "reply": {"in_reply_to_tweet_id": root}}
            )
        handle = payload.platform_account_id
        return PublishResult(root, f"https://x.com/i/status/{root}", extra, {"author": handle})

    async def find_existing(
        self, credentials: dict[str, Any], payload: PublishPayload, since: datetime
    ) -> PublishResult | None:
        body = await self._http.json(
            "GET",
            f"{API}/users/{payload.platform_account_id}/tweets",
            headers=bearer(credentials),
            params={
                "max_results": 10,
                "start_time": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tweet.fields": "created_at",
            },
        )
        parts = payload.thread_parts or [payload.body]
        want = (
            text_for(parts[0], payload.hashtags, payload.link_url, payload.link_placement)
            if len(parts) == 1
            else parts[0]
        )
        for t in body.get("data", []) or []:
            if same_text(t.get("text"), want):
                return PublishResult(str(t["id"]), f"https://x.com/i/status/{t['id']}")
        return None

    async def post_metrics(
        self, credentials: dict[str, Any], platform_account_id: str, platform_post_id: str
    ) -> list[MetricReading]:
        body = await self._http.json(
            "GET",
            f"{API}/tweets/{platform_post_id}",
            headers=bearer(credentials),
            params={"tweet.fields": "public_metrics"},
        )
        pm = (body.get("data") or {}).get("public_metrics") or {}
        shares = None
        if "retweet_count" in pm or "quote_count" in pm:
            shares = float((pm.get("retweet_count") or 0) + (pm.get("quote_count") or 0))
        return [
            MetricReading(
                "impressions", _num(pm.get("impression_count")), "public_metrics.impression_count"
            ),
            MetricReading("likes", _num(pm.get("like_count")), "public_metrics.like_count"),
            MetricReading("comments", _num(pm.get("reply_count")), "public_metrics.reply_count"),
            MetricReading("shares", shares, "public_metrics.retweet_count+quote_count"),
            MetricReading("saves", _num(pm.get("bookmark_count")), "public_metrics.bookmark_count"),
        ]

    async def account_metrics(
        self, credentials: dict[str, Any], platform_account_id: str
    ) -> list[MetricReading]:
        body = await self._http.json(
            "GET",
            f"{API}/users/{platform_account_id}",
            headers=bearer(credentials),
            params={"user.fields": "public_metrics"},
        )
        pm = (body.get("data") or {}).get("public_metrics") or {}
        return [
            MetricReading(
                "followers", _num(pm.get("followers_count")), "public_metrics.followers_count"
            )
        ]
