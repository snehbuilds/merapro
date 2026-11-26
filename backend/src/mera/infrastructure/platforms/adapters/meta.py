"""Facebook Page (M8.10) and Instagram (M8.11) adapters over the Graph API.

Facebook Page:
- text and link → ``POST /{page-id}/feed`` (``message``, ``link``);
- one image → ``POST /{page-id}/photos`` with the image bytes and the caption;
- several → each uploaded unpublished, then ``/feed`` with ``attached_media``.
- one MP4/MOV → the Page ``video_reels`` start/upload/finish flow.

Instagram (business accounts only; media is required):
- one image → ``POST /{ig-user-id}/media`` (``image_url``, ``caption``, ``alt_text``),
  poll the container until ``FINISHED``, then ``POST /{ig-user-id}/media_publish``;
- one video → the same container flow with ``media_type=REELS`` and ``video_url``;
- a carousel → one child container per image (``is_carousel_item``), then a parent
  ``CAROUSEL`` container, then publish.
Instagram fetches images itself, so each rendition is offered at a short-lived signed URL.

The Page/IG account access token is stored as the account's credential. Graph errors with
code 190 (and 102) are expired or revoked grants.

Verified against Meta's Graph API docs (Pages API, Instagram Content Publishing),
``GRAPH_VERSION``; re-verify when bumping it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx

from mera.domain.ports.platform_adapter import (
    MetricReading,
    PermanentError,
    PublishPayload,
    PublishResult,
    TransientError,
)
from mera.infrastructure.platforms.adapters.http import PlatformHTTP, same_text

# Keep this pinned to Meta's current Graph release.  A version is deliberately explicit:
# relying on an app's default version makes a platform upgrade an invisible production change.
GRAPH_VERSION = "v27.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
CONTAINER_POLLS = 20
CONTAINER_POLL_SECONDS = 3.0


def graph_auth_error(resp: httpx.Response) -> bool:
    if resp.status_code == 401:
        return True
    try:
        code = (resp.json().get("error") or {}).get("code")
    except (ValueError, AttributeError):
        return False
    return code in (102, 190)


def caption(body: str, hashtags: list[str], link_url: str | None, placement: str) -> str:
    parts = [body.strip()]
    if link_url and placement == "body":
        parts.append(link_url)
    if hashtags:
        parts.append(" ".join("#" + h.lstrip("#") for h in hashtags))
    return "\n\n".join(p for p in parts if p)


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


async def _insights(
    http: PlatformHTTP, object_id: str, token: str, metrics: dict[str, str]
) -> dict[str, float | None]:
    """Graph insights, one metric at a time so a deprecated metric costs only itself."""
    out: dict[str, float | None] = {}
    for platform_name, key in metrics.items():
        try:
            body = await http.json(
                "GET",
                f"{GRAPH}/{object_id}/insights",
                params={"metric": platform_name, "access_token": token},
            )
        except PermanentError:
            out[key] = None  # not reported for this object or retired by the platform
            continue
        data = body.get("data") or []
        values = (data[0].get("values") or [{}]) if data else [{}]
        raw = values[-1].get("value") if values else None
        if raw is None and data:
            raw = (data[0].get("total_value") or {}).get("value")
        out[key] = _num(raw)
    return out


class FacebookPageAdapter:
    platform = "facebook"

    def __init__(self, http: PlatformHTTP | None = None) -> None:
        self._http = http or PlatformHTTP(is_auth_error=graph_auth_error)

    async def _publish_reel(
        self, page: str, token: str, media: Any, description: str
    ) -> PublishResult:
        """Publish one Page Reel through Meta's resumable Reel upload sequence."""
        started = await self._http.json(
            "POST",
            f"{GRAPH}/{page}/video_reels",
            data={"upload_phase": "start", "access_token": token},
        )
        upload_url = str(started.get("upload_url") or "")
        video_id = str(started.get("video_id") or "")
        if not upload_url or not video_id:
            raise PermanentError("Facebook did not create a Reel upload session")
        await self._http.request(
            "POST",
            upload_url,
            headers={"Authorization": f"OAuth {token}", "Content-Type": media.mime_type},
            content=media.data,
            expect_json=False,
        )
        finished = await self._http.json(
            "POST",
            f"{GRAPH}/{page}/video_reels",
            data={
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": description,
                "access_token": token,
            },
        )
        post_id = str(finished.get("post_id") or finished.get("video_id") or video_id)
        return PublishResult(post_id, f"https://www.facebook.com/{post_id}", {"video_id": video_id})

    async def publish(self, credentials: dict[str, Any], payload: PublishPayload) -> PublishResult:
        token = str(credentials.get("access_token", ""))
        page = payload.platform_account_id
        message = caption(payload.body, payload.hashtags, payload.link_url, payload.link_placement)
        if len(payload.media) == 1:
            m = payload.media[0]
            if m.mime_type.startswith("video/"):
                return await self._publish_reel(page, token, m, message)
            body = await self._http.json(
                "POST",
                f"{GRAPH}/{page}/photos",
                data={"caption": message, "access_token": token},
                files={"source": ("image", m.data, m.mime_type)},
            )
            post_id = str(body.get("post_id") or body.get("id"))
        elif payload.media:
            if any(m.mime_type.startswith("video/") for m in payload.media):
                raise PermanentError("Facebook Reels must contain exactly one video")
            ids = []
            for m in payload.media:
                up = await self._http.json(
                    "POST",
                    f"{GRAPH}/{page}/photos",
                    data={"published": "false", "access_token": token},
                    files={"source": ("image", m.data, m.mime_type)},
                )
                ids.append(str(up["id"]))
            form: dict[str, Any] = {"message": message, "access_token": token}
            for i, fbid in enumerate(ids):
                form[f"attached_media[{i}]"] = json.dumps({"media_fbid": fbid})
            body = await self._http.json("POST", f"{GRAPH}/{page}/feed", data=form)
            post_id = str(body["id"])
        else:
            form = {"message": message, "access_token": token}
            if payload.link_url and payload.link_placement == "body":
                form["link"] = payload.link_url
            body = await self._http.json("POST", f"{GRAPH}/{page}/feed", data=form)
            post_id = str(body["id"])
        permalink = None
        try:
            info = await self._http.json(
                "GET",
                f"{GRAPH}/{post_id}",
                params={"fields": "permalink_url", "access_token": token},
            )
            permalink = info.get("permalink_url")
        except PermanentError:
            permalink = f"https://www.facebook.com/{post_id}"
        return PublishResult(post_id, permalink)

    async def find_existing(
        self, credentials: dict[str, Any], payload: PublishPayload, since: datetime
    ) -> PublishResult | None:
        token = str(credentials.get("access_token", ""))
        body = await self._http.json(
            "GET",
            f"{GRAPH}/{payload.platform_account_id}/posts",
            params={
                "fields": "id,message,permalink_url,created_time",
                "since": int(since.timestamp()),
                "limit": 10,
                "access_token": token,
            },
        )
        want = caption(payload.body, payload.hashtags, payload.link_url, payload.link_placement)
        for p in body.get("data", []):
            if same_text(p.get("message"), want):
                return PublishResult(str(p["id"]), p.get("permalink_url"))
        return None

    async def post_metrics(
        self, credentials: dict[str, Any], platform_account_id: str, platform_post_id: str
    ) -> list[MetricReading]:
        token = str(credentials.get("access_token", ""))
        counts = await self._http.json(
            "GET",
            f"{GRAPH}/{platform_post_id}",
            params={
                "fields": "shares,reactions.summary(total_count).limit(0),"
                "comments.summary(total_count).limit(0)",
                "access_token": token,
            },
        )
        ins = await _insights(
            self._http,
            platform_post_id,
            token,
            {
                "post_impressions": "impressions",
                "post_impressions_unique": "reach",
                "post_clicks": "clicks",
            },
        )
        return [
            MetricReading("impressions", ins["impressions"], "insights.post_impressions"),
            MetricReading("reach", ins["reach"], "insights.post_impressions_unique"),
            MetricReading("clicks", ins["clicks"], "insights.post_clicks"),
            MetricReading(
                "likes",
                _num(((counts.get("reactions") or {}).get("summary") or {}).get("total_count")),
                "reactions.summary.total_count",
            ),
            MetricReading(
                "comments",
                _num(((counts.get("comments") or {}).get("summary") or {}).get("total_count")),
                "comments.summary.total_count",
            ),
            MetricReading(
                "shares", _num((counts.get("shares") or {}).get("count", 0)), "shares.count"
            ),
        ]

    async def account_metrics(
        self, credentials: dict[str, Any], platform_account_id: str
    ) -> list[MetricReading]:
        token = str(credentials.get("access_token", ""))
        body = await self._http.json(
            "GET",
            f"{GRAPH}/{platform_account_id}",
            params={"fields": "followers_count,fan_count", "access_token": token},
        )
        value = body.get("followers_count", body.get("fan_count"))
        return [MetricReading("followers", _num(value), "followers_count")]


class InstagramAdapter:
    platform = "instagram"

    def __init__(
        self, http: PlatformHTTP | None = None, *, poll_seconds: float = CONTAINER_POLL_SECONDS
    ) -> None:
        self._http = http or PlatformHTTP(is_auth_error=graph_auth_error)
        self._poll = poll_seconds

    async def _container(self, ig: str, token: str, fields: dict[str, Any]) -> str:
        body = await self._http.json(
            "POST", f"{GRAPH}/{ig}/media", data={**fields, "access_token": token}
        )
        return str(body["id"])

    async def _wait(self, container: str, token: str) -> None:
        for _ in range(CONTAINER_POLLS):
            body = await self._http.json(
                "GET",
                f"{GRAPH}/{container}",
                params={"fields": "status_code", "access_token": token},
            )
            status = body.get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise PermanentError(f"Instagram could not process the media ({status})")
            await asyncio.sleep(self._poll)
        raise TransientError("Instagram is still processing the media")

    async def publish(self, credentials: dict[str, Any], payload: PublishPayload) -> PublishResult:
        if not payload.media:
            raise PermanentError("Instagram posts need at least one image or video")
        if any(not m.public_url for m in payload.media):
            raise PermanentError("Instagram needs a public URL for each media item")
        token = str(credentials.get("access_token", ""))
        ig = payload.platform_account_id
        text = caption(payload.body, payload.hashtags, payload.link_url, payload.link_placement)
        if len(payload.media) == 1:
            m = payload.media[0]
            if m.mime_type.startswith("video/"):
                fields = {"media_type": "REELS", "video_url": m.public_url, "caption": text}
            else:
                fields = {"image_url": m.public_url, "caption": text}
            if m.alt_text and not m.mime_type.startswith("video/"):
                fields["alt_text"] = m.alt_text[:1000]
            creation = await self._container(ig, token, fields)
        else:
            if any(m.mime_type.startswith("video/") for m in payload.media):
                raise PermanentError("Instagram Reels must contain exactly one video")
            children = []
            for m in payload.media[:10]:
                child: dict[str, Any] = {"image_url": m.public_url, "is_carousel_item": "true"}
                if m.alt_text:
                    child["alt_text"] = m.alt_text[:1000]
                children.append(await self._container(ig, token, child))
            for c in children:
                await self._wait(c, token)
            creation = await self._container(
                ig,
                token,
                {"media_type": "CAROUSEL", "children": ",".join(children), "caption": text},
            )
        await self._wait(creation, token)
        body = await self._http.json(
            "POST",
            f"{GRAPH}/{ig}/media_publish",
            data={"creation_id": creation, "access_token": token},
        )
        media_id = str(body["id"])
        permalink = None
        try:
            info = await self._http.json(
                "GET", f"{GRAPH}/{media_id}", params={"fields": "permalink", "access_token": token}
            )
            permalink = info.get("permalink")
        except PermanentError:
            permalink = None
        return PublishResult(media_id, permalink, {"container": creation})

    async def find_existing(
        self, credentials: dict[str, Any], payload: PublishPayload, since: datetime
    ) -> PublishResult | None:
        token = str(credentials.get("access_token", ""))
        body = await self._http.json(
            "GET",
            f"{GRAPH}/{payload.platform_account_id}/media",
            params={"fields": "id,caption,permalink,timestamp", "limit": 10, "access_token": token},
        )
        want = caption(payload.body, payload.hashtags, payload.link_url, payload.link_placement)
        for p in body.get("data", []):
            if same_text(p.get("caption"), want):
                return PublishResult(str(p["id"]), p.get("permalink"))
        return None

    async def post_metrics(
        self, credentials: dict[str, Any], platform_account_id: str, platform_post_id: str
    ) -> list[MetricReading]:
        token = str(credentials.get("access_token", ""))
        ins = await _insights(
            self._http,
            platform_post_id,
            token,
            {
                "views": "impressions",
                "reach": "reach",
                "likes": "likes",
                "comments": "comments",
                "shares": "shares",
                "saved": "saves",
            },
        )
        return [MetricReading(k, v, f"insights.{k}") for k, v in ins.items()]

    async def account_metrics(
        self, credentials: dict[str, Any], platform_account_id: str
    ) -> list[MetricReading]:
        token = str(credentials.get("access_token", ""))
        body = await self._http.json(
            "GET",
            f"{GRAPH}/{platform_account_id}",
            params={"fields": "followers_count", "access_token": token},
        )
        return [MetricReading("followers", _num(body.get("followers_count")), "followers_count")]
