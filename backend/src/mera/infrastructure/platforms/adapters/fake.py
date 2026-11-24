"""``FakePlatformAdapter`` (M8.1): the sandbox platform, with per-account failure injection.

The account's handle chooses its behaviour, so a demo or a test can make one account fail
while its siblings publish (docs/11 M8 acceptance, partial failure):

- ``…fail…``      every publish is refused (permanent): dead-lettered at once;
- ``…flaky…``     the first attempt for each post fails (5xx), the retry succeeds;
- ``…ratelimit…`` the first attempt gets a 429 with ``Retry-After: 30``;
- ``…lost…``      the post is created but the response is "lost" (timeout); the retry
                  must reconcile through ``find_existing`` instead of posting twice;
- ``…revoked…``   the grant is gone: the account moves to NEEDS_REAUTH.

State is per process (the worker), which is exactly the scope a retry runs in.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from mera.domain.ports.platform_adapter import (
    AuthExpired,
    MetricReading,
    PermanentError,
    PublishPayload,
    PublishResult,
    RateLimited,
    TransientError,
)

_BASES = {
    "linkedin_member": "https://www.linkedin.com/feed/update/",
    "linkedin_organization": "https://www.linkedin.com/feed/update/",
    "facebook": "https://www.facebook.com/",
    "instagram": "https://www.instagram.com/p/",
    "threads": "https://www.threads.net/post/",
    "x": "https://x.com/i/status/",
    "bluesky": "https://bsky.app/profile/sandbox/post/",
    "mastodon": "https://mastodon.example/@sandbox/",
}


@dataclass
class FakePlatformAdapter:
    platform: str
    posts: dict[str, PublishResult] = field(default_factory=dict)  # idempotency_key → post
    attempts: dict[str, int] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    comments: dict[str, str] = field(default_factory=dict)
    comment_payloads: dict[str, tuple[str, str]] = field(default_factory=dict)

    async def publish(self, credentials: dict[str, Any], payload: PublishPayload) -> PublishResult:
        handle = str(credentials.get("handle", ""))
        n = self.attempts.get(payload.idempotency_key, 0) + 1
        self.attempts[payload.idempotency_key] = n
        self.calls.append(
            {
                "op": "publish",
                "platform": self.platform,
                "handle": handle,
                "attempt": n,
                "chars": len(payload.body),
                "media": len(payload.media),
            }
        )
        if "revoked" in handle:
            raise AuthExpired("the platform reports the grant was revoked", status=401)
        if "fail" in handle:
            raise PermanentError("Sandbox platform refused this post (simulated)", status=400)
        if "flaky" in handle and n == 1:
            raise TransientError("Sandbox platform returned 500 (simulated)", status=500)
        if "ratelimit" in handle and n == 1:
            raise RateLimited("Sandbox platform rate limit (simulated)", retry_after=30)
        if payload.idempotency_key in self.posts:
            return self.posts[payload.idempotency_key]  # the platform's own dedupe
        post_id = f"{self.platform}-{secrets.token_hex(6)}"
        extra: dict[str, str] = {}
        for i, _ in enumerate(payload.thread_parts or [], start=1):
            if i > 1:
                extra[f"part_{i}"] = f"{post_id}-{i}"
        if payload.link_url and payload.link_placement == "reply":
            extra["reply"] = f"{post_id}-reply"
        result = PublishResult(
            platform_post_id=post_id,
            permalink=_BASES.get(self.platform, "https://sandbox.example/") + post_id,
            extra_ids=extra,
        )
        self.posts[payload.idempotency_key] = result
        if "lost" in handle and n == 1:
            raise TransientError("Sandbox platform timed out after accepting (simulated)")
        return result

    async def publish_comment(
        self,
        credentials: dict[str, Any],
        *,
        platform_account_id: str,
        platform_post_id: str,
        body: str,
        idempotency_key: str,
    ) -> str:
        if idempotency_key in self.comments:
            return self.comments[idempotency_key]
        handle = str(credentials.get("handle", ""))
        self.calls.append({"op": "comment", "post_id": platform_post_id, "body": body})
        if "commentfail" in handle:
            raise TransientError("Sandbox comment failed (simulated)", status=500)
        comment_id = f"{platform_post_id}-comment-{secrets.token_hex(3)}"
        self.comments[idempotency_key] = comment_id
        self.comment_payloads[idempotency_key] = (platform_post_id, body)
        return comment_id

    async def find_comment(
        self,
        credentials: dict[str, Any],
        *,
        platform_account_id: str,
        platform_post_id: str,
        body: str,
    ) -> str | None:
        return next(
            (
                self.comments[key]
                for key, payload in self.comment_payloads.items()
                if payload == (platform_post_id, body)
            ),
            None,
        )

    async def find_existing(
        self, credentials: dict[str, Any], payload: PublishPayload, since: datetime
    ) -> PublishResult | None:
        return self.posts.get(payload.idempotency_key)

    async def post_metrics(
        self, credentials: dict[str, Any], platform_account_id: str, platform_post_id: str
    ) -> list[MetricReading]:
        return sandbox_post_metrics(self.platform, platform_post_id, datetime.now(UTC))

    async def account_metrics(
        self, credentials: dict[str, Any], platform_account_id: str
    ) -> list[MetricReading]:
        seed = _seed(platform_account_id)
        return [MetricReading("followers", float(800 + seed % 5000), "sandbox.followers")]


def _seed(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")


# What each platform reports (docs/08 §4): a metric absent here is "not reported".
REPORTED: dict[str, tuple[str, ...]] = {
    "linkedin_member": ("likes", "comments", "shares"),
    "linkedin_organization": ("impressions", "likes", "comments", "shares", "clicks"),
    "facebook": ("impressions", "reach", "likes", "comments", "shares", "clicks"),
    "instagram": ("impressions", "reach", "likes", "comments", "shares", "saves"),
    "threads": ("impressions", "likes", "comments", "shares"),
    "x": ("impressions", "likes", "comments", "shares"),
}


def sandbox_post_metrics(platform: str, post_id: str, now: datetime) -> list[MetricReading]:
    """Deterministic for a post, growing with its age's hour bucket, so a seeded history
    looks like real decay curves and a re-run in the same hour is identical."""
    seed = _seed(post_id)
    base = 200 + seed % 1800
    reported = REPORTED.get(platform, ("likes", "comments"))
    out = []
    for key in ("impressions", "reach", "likes", "comments", "shares", "saves", "clicks"):
        if key not in reported:
            out.append(MetricReading(key, None, f"sandbox.{key}"))
            continue
        factor = {
            "impressions": 1.0,
            "reach": 0.7,
            "likes": 0.045,
            "comments": 0.006,
            "shares": 0.004,
            "saves": 0.008,
            "clicks": 0.02,
        }[key]
        out.append(
            MetricReading(
                key, float(round(base * factor * (1 + (seed % 7) / 10))), f"sandbox.{key}"
            )
        )
    return out
