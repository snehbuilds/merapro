"""The Scheduler agent (P2-M7.1, 7.2): proposes times; the policy still decides.

It reads one account's last ninety days of published posts and their latest engagement
(likes + comments + shares), scores each local hour of the week relative to that
account's median, and proposes the best upcoming slots with a reason. ``schedule_approved``
uses a proposal only when nobody asked for a time and the account has no posting times of
its own; the proposal then goes through ``place``, which applies quiet hours, the daily
cap and spacing exactly as for any other time. The proposal and its reason are kept on
the scheduled post's ``placement``, so the calendar can say why a post went where it did.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.domain.policies.slot_scores import Proposal, SlotScore, propose, score_slots
from mera.infrastructure.db.models import MetricSnapshot, PublishedPost

HISTORY: Final = timedelta(days=90)
ENGAGEMENT: Final = ("likes", "comments", "shares")
# P3-M0.7 (docs/14 §2.2): a post nobody asked a time for waits at most a day for a
# better slot, and only for a slot measured on enough posts to be evidence rather than
# luck. Before that the post goes out from its approval, as in Phase 1.
MAX_SHIFT: Final = timedelta(hours=24)
MIN_SLOT_SAMPLES: Final = 8


async def engagement_history(
    s: AsyncSession, account_id: uuid.UUID, *, now: datetime | None = None
) -> list[tuple[datetime, float]]:
    return [(at, e) for at, e in (await post_engagement(s, account_id, now=now)).values()]


async def post_engagement(
    s: AsyncSession, account_id: uuid.UUID, *, now: datetime | None = None
) -> dict[uuid.UUID, tuple[datetime, float]]:
    """Per published post of the account in the last ninety days: (published_at, likes +
    comments + shares at their latest snapshot). Shared with hashtag stats (P3-M1.1)."""
    now = now or datetime.now(UTC)
    latest = (
        select(
            MetricSnapshot.published_post_id,
            MetricSnapshot.metric_key,
            func.max(MetricSnapshot.captured_at).label("at"),
        )
        .where(
            MetricSnapshot.social_account_id == account_id,
            MetricSnapshot.subject_type == "post",
            MetricSnapshot.metric_key.in_(ENGAGEMENT),
        )
        .group_by(MetricSnapshot.published_post_id, MetricSnapshot.metric_key)
        .subquery()
    )
    rows = (
        await s.execute(
            select(PublishedPost.id, PublishedPost.published_at, MetricSnapshot.value)
            .join(latest, latest.c.published_post_id == PublishedPost.id)
            .join(
                MetricSnapshot,
                (MetricSnapshot.published_post_id == latest.c.published_post_id)
                & (MetricSnapshot.metric_key == latest.c.metric_key)
                & (MetricSnapshot.captured_at == latest.c.at),
            )
            .where(
                PublishedPost.social_account_id == account_id,
                PublishedPost.deleted_at.is_(None),
                PublishedPost.published_at >= now - HISTORY,
            )
        )
    ).all()
    per: dict[uuid.UUID, tuple[datetime, float]] = {}
    for pid, at, value in rows:
        t, total = per.get(pid, (at, 0.0))
        per[pid] = (t, total + float(value or Decimal(0)))
    return per


async def account_scores(
    s: AsyncSession, account_id: uuid.UUID, tz_name: str | None
) -> list[SlotScore]:
    return score_slots(await engagement_history(s, account_id), tz_name)


async def proposal_for(
    s: AsyncSession, account_id: uuid.UUID, *, after: datetime, tz_name: str | None
) -> Proposal | None:
    """The best slot after ``after`` within ``MAX_SHIFT`` measured on at least
    ``MIN_SLOT_SAMPLES`` posts, or None (too little history, or nothing better than
    typical soon enough)."""
    scores = await account_scores(s, account_id, tz_name)
    best = propose(
        scores,
        after=after,
        tz_name=tz_name,
        horizon=MAX_SHIFT,
        count=3,
        min_samples=MIN_SLOT_SAMPLES,
    )
    if not best:
        return None
    return max(best, key=lambda p: (p.score, -p.at.timestamp()))


def as_placement(p: Proposal) -> dict[str, Any]:
    return {
        "at": p.at.isoformat(),
        "score": p.score,
        "samples": p.samples,
        "reason": p.reason,
        "by": "scheduler_agent",
    }
