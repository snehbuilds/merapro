"""Threads adapter (M8.11b) over the Threads API (``graph.threads.net``).

Publishing is two steps per post: create a container (``POST /{user-id}/threads``), then
``POST /{user-id}/threads_publish``. Text-only posts use ``media_type=TEXT``; one image is
``IMAGE`` (fetched by Threads from a signed URL); several are a ``CAROUSEL`` of children.
Thread parts and the source link are replies chained with ``reply_to_id`` (the link goes in
a reply: capabilities ``link_placement=reply``).

Verified against Meta's Threads API docs (Posts, Replies, Insights); re-verify on change.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from mera.domain.ports.platform_adapter import (
    MetricReading,
    PermanentError,
    PublishPayload,
    PublishResult,
)
from mera.infrastructure.platforms.adapters.http import PlatformHTTP, same_text
from mera.infrastructure.platforms.adapters.meta import _num, graph_auth_error

API = "https://graph.threads.net/v1.0"
# Threads recommends waiting before publishing a freshly created container.
SETTLE_SECONDS = 2.0


def text_for(body: str, hashtags: list[str]) -> str:
    # Threads allows one topic tag; the guardrail already enforces max_hashtags=1.
    tag = f" #{hashtags[0].lstrip('#')}" if hashtags else ""
    return (body.strip() + tag)[:500]


class ThreadsAdapter:
    platform = "threads"

    def __init__(
        self, http: PlatformHTTP | None = None, *, settle_seconds: float = SETTLE_SECONDS
    ) -> None:
        self._http = http or PlatformHTTP(is_auth_error=graph_auth_error)
        self._settle = settle_seconds

    async def _post(self, user: str, token: str, fields: dict[str, Any]) -> str:
        created = await self._http.json(
            "POST", f"{API}/{user}/threads", data={**fields, "access_token": token}
        )
        if self._settle:
            await asyncio.sleep(self._settle)
        published = await self._http.json(
            "POST",
            f"{API}/{user}/threads_publish",
            data={"creation_id": created["id"], "access_token": token},
        )
        return str(published["id"])

    async def publish(self, credentials: dict[str, Any], payload: PublishPayload) -> PublishResult:
        token = str(credentials.get("access_token", ""))
        user = payload.platform_account_id
        parts = payload.thread_parts or [payload.body]
        first_text = text_for(parts[0], payload.hashtags)
        if payload.media and any(not m.public_url for m in payload.media):
            raise PermanentError("Threads needs a public URL for each image")
        if len(payload.media) == 1:
            m = payload.media[0]
            fields: dict[str, Any] = {
                "media_type": "IMAGE",
                "image_url": m.public_url,
                "text": first_text,
            }
            if m.alt_text:
                fields["alt_text"] = m.alt_text[:1000]
            root = await self._post(user, token, fields)
        elif payload.media:
            children = []
            for m in payload.media[:20]:
                child = await self._http.json(
                    "POST",
                    f"{API}/{user}/threads",
                    data={
                        "media_type": "IMAGE",
                        "image_url": m.public_url,
                        "is_carousel_item": "true",
                        **({"alt_text": m.alt_text[:1000]} if m.alt_text else {}),
                        "access_token": token,
                    },
                )
                children.append(str(child["id"]))
            root = await self._post(
                user,
                token,
                {"media_type": "CAROUSEL", "children": ",".join(children), "text": first_text},
            )
        else:
            root = await self._post(user, token, {"media_type": "TEXT", "text": first_text})
        extra: dict[str, str] = {}
        parent = root
        for i, part in enumerate(parts[1:], start=2):
            parent = await self._post(
                user, token, {"media_type": "TEXT", "text": part[:500], "reply_to_id": parent}
            )
            extra[f"part_{i}"] = parent
        if payload.link_url and payload.link_placement == "reply":
            extra["reply"] = await self._post(
                user, token, {"media_type": "TEXT", "text": payload.link_url, "reply_to_id": root}
            )
        permalink = None
        try:
            info = await self._http.json(
                "GET", f"{API}/{root}", params={"fields": "permalink", "access_token": token}
            )
            permalink = info.get("permalink")
        except PermanentError:
            permalink = None
        return PublishResult(root, permalink, extra)

    async def find_existing(
        self, credentials: dict[str, Any], payload: PublishPayload, since: datetime
    ) -> PublishResult | None:
        token = str(credentials.get("access_token", ""))
        body = await self._http.json(
            "GET",
            f"{API}/{payload.platform_account_id}/threads",
            params={
                "fields": "id,text,permalink,timestamp",
                "since": int(since.timestamp()),
                "limit": 10,
                "access_token": token,
            },
        )
        want = text_for((payload.thread_parts or [payload.body])[0], payload.hashtags)
        for p in body.get("data", []):
            if same_text(p.get("text"), want):
                return PublishResult(str(p["id"]), p.get("permalink"))
        return None

    async def post_metrics(
        self, credentials: dict[str, Any], platform_account_id: str, platform_post_id: str
    ) -> list[MetricReading]:
        token = str(credentials.get("access_token", ""))
        body = await self._http.json(
            "GET",
            f"{API}/{platform_post_id}/insights",
            params={"metric": "views,likes,replies,reposts,quotes,shares", "access_token": token},
        )
        raw: dict[str, float | None] = {}
        for item in body.get("data", []):
            values = item.get("values") or []
            value = (
                values[-1].get("value") if values else (item.get("total_value") or {}).get("value")
            )
            raw[str(item.get("name"))] = _num(value)
        shares = None
        if any(raw.get(k) is not None for k in ("reposts", "quotes", "shares")):
            shares = sum(raw.get(k) or 0 for k in ("reposts", "quotes", "shares"))
        return [
            MetricReading("impressions", raw.get("views"), "insights.views"),
            MetricReading("likes", raw.get("likes"), "insights.likes"),
            MetricReading("comments", raw.get("replies"), "insights.replies"),
            MetricReading("shares", shares, "insights.reposts+quotes+shares"),
        ]

    async def account_metrics(
        self, credentials: dict[str, Any], platform_account_id: str
    ) -> list[MetricReading]:
        token = str(credentials.get("access_token", ""))
        body = await self._http.json(
            "GET",
            f"{API}/{platform_account_id}/threads_insights",
            params={"metric": "followers_count", "access_token": token},
        )
        data = body.get("data") or []
        value = (data[0].get("total_value") or {}).get("value") if data else None
        return [MetricReading("followers", _num(value), "threads_insights.followers_count")]
