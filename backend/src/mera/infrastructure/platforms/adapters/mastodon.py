"""Mastodon adapter (P2-M3.3) over the Mastodon REST API, on whichever instance the
account lives.

``POST /api/v1/statuses`` with an ``Idempotency-Key`` header: Mastodon itself refuses a
second status with the same key for an hour, so a retry after a lost response cannot post
twice even before reconciliation runs. Images go up first through ``POST /api/v2/media``
(with their alt text as ``description``); large files are processed asynchronously
(202), so the adapter waits until the attachment has a URL. Thread parts are replies
chained with ``in_reply_to_id``. Status text comes back as HTML; reconciliation compares
it as plain text.

Credentials: ``{"instance": host, "access_token": ...}``; Mastodon tokens do not expire
unless revoked. Verified against the Mastodon API docs (statuses, media, accounts);
re-verify on change.
"""

from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime
from typing import Any

from mera.domain.ports.platform_adapter import (
    AuthExpired,
    MetricReading,
    PermanentError,
    PublishPayload,
    PublishResult,
    TransientError,
)
from mera.infrastructure.platforms.adapters.http import PlatformHTTP, same_text

MAX_MEDIA = 4
MEDIA_POLL_SECONDS = 1.0
MEDIA_POLL_TRIES = 30

_TAGS = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<br\s*/?>|</p>\s*<p>", re.IGNORECASE)


def plain(content: str | None) -> str:
    """Mastodon's status HTML as the text the person wrote."""
    if not content:
        return ""
    return html.unescape(_TAGS.sub("", _BREAK.sub("\n", content))).strip()


def text_for(body: str, hashtags: list[str], link_url: str | None, placement: str) -> str:
    parts = [body.strip()]
    if link_url and placement == "body" and link_url not in body:
        parts.append(link_url)
    if hashtags:
        parts.append(" ".join("#" + h.lstrip("#") for h in hashtags))
    return "\n\n".join(p for p in parts if p)


def _base(credentials: dict[str, Any]) -> str:
    host = str(credentials.get("instance") or "")
    if not host:
        raise AuthExpired("no Mastodon instance stored for this account")
    return f"https://{host}"


def _headers(credentials: dict[str, Any]) -> dict[str, str]:
    token = credentials.get("access_token")
    if not token:
        raise AuthExpired("no access token stored for this account")
    return {"Authorization": f"Bearer {token}"}


def _account_id(platform_account_id: str) -> str:
    """``1234@mastodon.social`` → ``1234`` (Mera keeps the host so ids never collide)."""
    return platform_account_id.split("@", 1)[0]


class MastodonAdapter:
    platform = "mastodon"

    def __init__(
        self,
        http: PlatformHTTP | None = None,
        *,
        poll_seconds: float = MEDIA_POLL_SECONDS,
        transport: Any | None = None,
    ) -> None:
        self._http = http or PlatformHTTP(transport=transport)
        self._poll = poll_seconds

    async def _media(
        self, credentials: dict[str, Any], data: bytes, mime: str, alt: str | None
    ) -> str:
        base, headers = _base(credentials), _headers(credentials)
        fields = {"description": alt[:1500]} if alt else {}
        resp = await self._http.request(
            "POST",
            f"{base}/api/v2/media",
            headers=headers,
            data=fields,
            files={"file": ("image", data, mime)},
        )
        body = resp.json()
        media_id = str(body["id"])
        if resp.status_code == 202 or not body.get("url"):
            for _ in range(MEDIA_POLL_TRIES):
                await asyncio.sleep(self._poll)
                check = await self._http.request(
                    "GET", f"{base}/api/v1/media/{media_id}", headers=headers
                )
                if check.status_code == 200 and check.json().get("url"):
                    break
            else:
                raise TransientError("Mastodon is still processing the image")
        return media_id

    async def _status(
        self, credentials: dict[str, Any], fields: dict[str, Any], key: str
    ) -> dict[str, Any]:
        return await self._http.json(
            "POST",
            f"{_base(credentials)}/api/v1/statuses",
            headers={**_headers(credentials), "Idempotency-Key": key},
            json_body=fields,
        )

    async def publish(self, credentials: dict[str, Any], payload: PublishPayload) -> PublishResult:
        parts = payload.thread_parts or [payload.body]
        media_ids = [
            await self._media(credentials, m.data, m.mime_type, m.alt_text)
            for m in payload.media[:MAX_MEDIA]
        ]
        visibility = str(credentials.get("visibility") or "public")
        first_fields: dict[str, Any] = {
            "status": text_for(
                parts[0], payload.hashtags, payload.link_url, payload.link_placement
            ),
            "visibility": visibility,
        }
        if media_ids:
            first_fields["media_ids"] = media_ids
        root = await self._status(credentials, first_fields, payload.idempotency_key)
        if not root.get("id"):
            raise PermanentError("Mastodon did not return the new status")
        extra: dict[str, str] = {}
        parent = str(root["id"])
        for i, part in enumerate(parts[1:], start=2):
            reply = await self._status(
                credentials,
                {"status": part.strip(), "in_reply_to_id": parent, "visibility": visibility},
                f"{payload.idempotency_key}:{i}",
            )
            parent = str(reply["id"])
            extra[f"part_{i}"] = parent
        if payload.link_url and payload.link_placement == "reply":
            reply = await self._status(
                credentials,
                {
                    "status": payload.link_url,
                    "in_reply_to_id": str(root["id"]),
                    "visibility": visibility,
                },
                f"{payload.idempotency_key}:link",
            )
            extra["reply"] = str(reply["id"])
        return PublishResult(str(root["id"]), root.get("url"), extra)

    async def find_existing(
        self, credentials: dict[str, Any], payload: PublishPayload, since: datetime
    ) -> PublishResult | None:
        statuses = await self._http.json(
            "GET",
            f"{_base(credentials)}/api/v1/accounts/{_account_id(payload.platform_account_id)}/statuses",
            headers=_headers(credentials),
            params={"limit": 10, "exclude_replies": "true"},
        )
        want = text_for(
            (payload.thread_parts or [payload.body])[0],
            payload.hashtags,
            payload.link_url,
            payload.link_placement,
        )
        for st in statuses.get("data", []):
            try:
                created = datetime.fromisoformat(str(st.get("created_at")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if created >= since and same_text(plain(st.get("content")), want):
                return PublishResult(str(st["id"]), st.get("url"))
        return None

    async def post_metrics(
        self, credentials: dict[str, Any], platform_account_id: str, platform_post_id: str
    ) -> list[MetricReading]:
        st = await self._http.json(
            "GET",
            f"{_base(credentials)}/api/v1/statuses/{platform_post_id}",
            headers=_headers(credentials),
        )

        def num(key: str) -> float | None:
            v = st.get(key)
            return float(v) if isinstance(v, int | float) else None

        return [
            MetricReading("impressions", None, "not reported by Mastodon"),
            MetricReading("likes", num("favourites_count"), "status.favourites_count"),
            MetricReading("comments", num("replies_count"), "status.replies_count"),
            MetricReading("shares", num("reblogs_count"), "status.reblogs_count"),
        ]

    async def account_metrics(
        self, credentials: dict[str, Any], platform_account_id: str
    ) -> list[MetricReading]:
        acct = await self._http.json(
            "GET",
            f"{_base(credentials)}/api/v1/accounts/{_account_id(platform_account_id)}",
            headers=_headers(credentials),
        )
        v = acct.get("followers_count")
        return [
            MetricReading(
                "followers", float(v) if isinstance(v, int) else None, "account.followers_count"
            )
        ]
