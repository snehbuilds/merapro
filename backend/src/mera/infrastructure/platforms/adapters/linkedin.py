"""LinkedIn adapter (M8.2 member, M8.3 organization) over the versioned REST Posts API.

- Text goes in ``commentary``, in LinkedIn's "little text format": reserved characters are
  backslash-escaped, and hashtags use the ``{hashtag|\\#|tag}`` template, or they render as
  literal text.
- Images: ``POST /rest/images?action=initializeUpload`` → ``PUT`` the bytes to the returned
  upload URL → reference the ``urn:li:image`` in ``content.media`` (one) or
  ``content.multiImage`` (several), each with its alt text.
- The source link goes in the first comment (capabilities: ``link_placement``), because
  links in the body are down-ranked.
- Reconciliation after a lost response: the author's latest posts, matched by text.

Verified against Microsoft's LinkedIn API docs (Posts API, Images API, Social Actions),
version header ``202510``; re-verify when bumping ``LINKEDIN_VERSION``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from mera.domain.ports.platform_adapter import (
    MetricReading,
    PublishPayload,
    PublishResult,
)
from mera.infrastructure.platforms.adapters.http import PlatformHTTP, bearer, same_text

API = "https://api.linkedin.com/rest"
LINKEDIN_VERSION = os.getenv("LINKEDIN_VERSION", "202610")
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")


def escape_commentary(text: str) -> str:
    return _RESERVED.sub(r"\\\1", text)


def commentary(body: str, hashtags: list[str], link_url: str | None, placement: str) -> str:
    parts = [escape_commentary(body.strip())]
    if link_url and placement == "body":
        parts.append(escape_commentary(link_url))
    if hashtags:
        parts.append(" ".join("{hashtag|\\#|" + h.lstrip("#") + "}" for h in hashtags))
    return "\n\n".join(p for p in parts if p)


def author_urn(platform: str, platform_account_id: str) -> str:
    if platform_account_id.startswith("urn:li:"):
        return platform_account_id
    kind = "organization" if platform == "linkedin_organization" else "person"
    return f"urn:li:{kind}:{platform_account_id}"


class LinkedInAdapter:
    def __init__(self, platform: str, http: PlatformHTTP | None = None) -> None:
        self.platform = platform
        self._http = http or PlatformHTTP()

    def _headers(self, credentials: dict[str, Any]) -> dict[str, str]:
        return {
            **bearer(credentials),
            "LinkedIn-Version": LINKEDIN_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        }

    async def _upload_image(
        self, credentials: dict[str, Any], owner: str, data: bytes, mime: str
    ) -> str:
        init = await self._http.json(
            "POST",
            f"{API}/images",
            params={"action": "initializeUpload"},
            headers=self._headers(credentials),
            json_body={"initializeUploadRequest": {"owner": owner}},
        )
        value = init.get("value", {})
        await self._http.request(
            "PUT",
            value["uploadUrl"],
            headers={**bearer(credentials), "Content-Type": mime},
            content=data,
        )
        return str(value["image"])

    async def publish(self, credentials: dict[str, Any], payload: PublishPayload) -> PublishResult:
        author = author_urn(self.platform, payload.platform_account_id)
        post: dict[str, Any] = {
            "author": author,
            "commentary": commentary(
                payload.body, payload.hashtags, payload.link_url, payload.link_placement
            ),
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        images = []
        for m in payload.media:
            urn = await self._upload_image(credentials, author, m.data, m.mime_type)
            images.append({"id": urn, "altText": (m.alt_text or "")[:4086]})
        if len(images) == 1:
            post["content"] = {"media": images[0]}
        elif images:
            post["content"] = {"multiImage": {"images": images}}
        resp = await self._http.request(
            "POST", f"{API}/posts", headers=self._headers(credentials), json_body=post
        )
        urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id") or ""
        if not urn:
            urn = str((resp.json() if resp.content else {}).get("id", ""))
        extra: dict[str, str] = {}
        if payload.link_url and payload.link_placement == "first_comment":
            comment = await self._http.request(
                "POST",
                f"{API}/socialActions/{quote(urn, safe='')}/comments",
                headers=self._headers(credentials),
                json_body={"actor": author, "object": urn, "message": {"text": payload.link_url}},
            )
            extra["first_comment"] = comment.headers.get("x-restli-id", "") or "created"
        return PublishResult(
            platform_post_id=urn,
            permalink=f"https://www.linkedin.com/feed/update/{urn}/",
            extra_ids=extra,
        )

    async def find_existing(
        self, credentials: dict[str, Any], payload: PublishPayload, since: datetime
    ) -> PublishResult | None:
        author = author_urn(self.platform, payload.platform_account_id)
        body = await self._http.json(
            "GET",
            f"{API}/posts",
            headers={**self._headers(credentials), "X-RestLi-Method": "FINDER"},
            params={"q": "author", "author": author, "count": 10, "sortBy": "LAST_MODIFIED"},
        )
        want = commentary(payload.body, payload.hashtags, payload.link_url, payload.link_placement)
        for el in body.get("elements", []):
            created = int(el.get("createdAt") or el.get("publishedAt") or 0) / 1000
            if created and created < since.timestamp():
                continue
            if same_text(el.get("commentary"), want):
                urn = str(el.get("id"))
                return PublishResult(urn, f"https://www.linkedin.com/feed/update/{urn}/")
        return None

    async def post_metrics(
        self, credentials: dict[str, Any], platform_account_id: str, platform_post_id: str
    ) -> list[MetricReading]:
        actions = await self._http.json(
            "GET",
            f"{API}/socialActions/{quote(platform_post_id, safe='')}",
            headers=self._headers(credentials),
        )
        likes = (actions.get("likesSummary") or {}).get("totalLikes")
        comments = (actions.get("commentsSummary") or {}).get("aggregatedTotalComments")
        out = [
            MetricReading("likes", _num(likes), "socialActions.likesSummary.totalLikes"),
            MetricReading(
                "comments", _num(comments), "socialActions.commentsSummary.aggregatedTotalComments"
            ),
        ]
        if self.platform != "linkedin_organization":
            # Member posts: LinkedIn exposes no impressions to third parties (docs/08 §4).
            return [*out, MetricReading("impressions", None, "not available for members")]
        org = author_urn(self.platform, platform_account_id)
        key = "ugcPosts" if ":ugcPost:" in platform_post_id else "shares"
        stats = await self._http.json(
            "GET",
            f"{API}/organizationalEntityShareStatistics",
            headers=self._headers(credentials),
            params={
                "q": "organizationalEntity",
                "organizationalEntity": org,
                f"{key}": f"List({quote(platform_post_id, safe='')})",
            },
        )
        totals = ((stats.get("elements") or [{}])[0]).get("totalShareStatistics") or {}
        return [
            *out,
            MetricReading("impressions", _num(totals.get("impressionCount")), "impressionCount"),
            MetricReading("clicks", _num(totals.get("clickCount")), "clickCount"),
            MetricReading("shares", _num(totals.get("shareCount")), "shareCount"),
            MetricReading("engagement_rate", _num(totals.get("engagement")), "engagement"),
        ]

    async def account_metrics(
        self, credentials: dict[str, Any], platform_account_id: str
    ) -> list[MetricReading]:
        if self.platform != "linkedin_organization":
            return [MetricReading("followers", None, "not available for members")]
        org = author_urn(self.platform, platform_account_id)
        body = await self._http.json(
            "GET",
            f"{API}/networkSizes/{quote(org, safe='')}",
            headers=self._headers(credentials),
            params={"edgeType": "COMPANY_FOLLOWED_BY_MEMBER"},
        )
        return [MetricReading("followers", _num(body.get("firstDegreeSize")), "firstDegreeSize")]


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
