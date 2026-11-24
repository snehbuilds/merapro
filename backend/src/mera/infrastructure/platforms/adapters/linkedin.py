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
    PermanentError,
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

    async def _upload_document(
        self, credentials: dict[str, Any], owner: str, data: bytes, mime: str
    ) -> str:
        init = await self._http.json(
            "POST",
            f"{API}/documents",
            params={"action": "initializeUpload"},
            headers=self._headers(credentials),
            json_body={"initializeUploadRequest": {"owner": owner}},
        )
        value = init.get("value", {})
        await self._http.request(
            "PUT", value["uploadUrl"], headers={"Content-Type": mime}, content=data
        )
        return str(value["document"])

    async def _upload_video(
        self, credentials: dict[str, Any], owner: str, data: bytes, mime: str
    ) -> str:
        init = await self._http.json(
            "POST",
            f"{API}/videos",
            params={"action": "initializeUpload"},
            headers=self._headers(credentials),
            json_body={
                "initializeUploadRequest": {
                    "owner": owner,
                    "fileSizeBytes": len(data),
                    "uploadCaptions": False,
                    "uploadThumbnail": False,
                }
            },
        )
        value = init.get("value", {})
        etags = []
        for instruction in value.get("uploadInstructions") or []:
            first = int(instruction.get("firstByte", 0))
            last = int(instruction.get("lastByte", len(data) - 1))
            response = await self._http.request(
                "PUT",
                instruction["uploadUrl"],
                headers={"Content-Type": mime},
                content=data[first : last + 1],
                expect_json=False,
            )
            etags.append(response.headers.get("etag", ""))
        await self._http.json(
            "POST",
            f"{API}/videos",
            params={"action": "finalizeUpload"},
            headers=self._headers(credentials),
            json_body={
                "finalizeUploadRequest": {
                    "video": value["video"],
                    "uploadToken": value.get("uploadToken", ""),
                    "uploadedPartIds": etags,
                }
            },
        )
        return str(value["video"])

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
        single_media: dict[str, str] | None = None
        for m in payload.media:
            if m.mime_type.startswith("image/"):
                urn = await self._upload_image(credentials, author, m.data, m.mime_type)
                images.append({"id": urn, "altText": (m.alt_text or "")[:4086]})
            elif m.mime_type == "application/pdf":
                if len(payload.media) != 1:
                    raise PermanentError("LinkedIn documents must be attached alone")
                urn = await self._upload_document(credentials, author, m.data, m.mime_type)
                single_media = {"id": urn, "title": "Document"}
            elif m.mime_type.startswith("video/"):
                if len(payload.media) != 1:
                    raise PermanentError("LinkedIn videos must be attached alone")
                urn = await self._upload_video(credentials, author, m.data, m.mime_type)
                single_media = {"id": urn, "title": "Video"}
            else:
                raise PermanentError(f"Unsupported LinkedIn media type: {m.mime_type}")
        if len(images) == 1:
            post["content"] = {"media": images[0]}
        elif images:
            post["content"] = {"multiImage": {"images": images}}
        elif single_media:
            post["content"] = {"media": single_media}
        resp = await self._http.request(
            "POST", f"{API}/posts", headers=self._headers(credentials), json_body=post
        )
        urn = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id") or ""
        if not urn:
            urn = str((resp.json() if resp.content else {}).get("id", ""))
        return PublishResult(
            platform_post_id=urn,
            permalink=f"https://www.linkedin.com/feed/update/{urn}/",
        )

    async def publish_comment(
        self,
        credentials: dict[str, Any],
        *,
        platform_account_id: str,
        platform_post_id: str,
        body: str,
        idempotency_key: str,
    ) -> str:
        if len(body) > 1250:
            raise ValueError("LinkedIn comments can be at most 1,250 characters")
        author = author_urn(self.platform, platform_account_id)
        comment = await self._http.request(
            "POST",
            f"{API}/socialActions/{quote(platform_post_id, safe='')}/comments",
            headers={**self._headers(credentials), "X-RestLi-Idempotency-Key": idempotency_key},
            json_body={"actor": author, "object": platform_post_id, "message": {"text": body}},
        )
        return comment.headers.get("x-restli-id", "") or str(
            (comment.json() if comment.content else {}).get("id", "created")
        )

    async def find_comment(
        self,
        credentials: dict[str, Any],
        *,
        platform_account_id: str,
        platform_post_id: str,
        body: str,
    ) -> str | None:
        author = author_urn(self.platform, platform_account_id)
        found = await self._http.json(
            "GET",
            f"{API}/socialActions/{quote(platform_post_id, safe='')}/comments",
            headers=self._headers(credentials),
            params={"count": 100},
        )
        for item in found.get("elements") or []:
            text = ((item.get("message") or {}).get("text"))
            if str(item.get("actor")) == author and text == body:
                return str(item.get("id"))
        return None

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
