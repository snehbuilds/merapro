"""Hashtag registry helpers shared by drafting, approvals and publishing (P2-M1.3)."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.agents.reviewers import SuggestedTag, TagEvidence
from mera.application.errors import InvalidInput
from mera.domain.policies.hashtag_lift import MIN_HASHTAG_SAMPLES
from mera.domain.policies.text_checks import normalize_hashtag
from mera.infrastructure.db.models import Hashtag, HashtagStat

_TAG = re.compile(r"^[^\s#,]{1,100}$")


def normalize_tag(raw: str) -> str:
    """``#MachineLearning`` → ``machinelearning``. Raises ``InvalidInput`` on junk."""
    tag = raw.strip().lstrip("#").strip().lower()
    if not _TAG.match(tag):
        raise InvalidInput(
            "A hashtag is one word of up to 100 characters, with or without #.",
            code="invalid_hashtag",
            field="tag",
        )
    return tag


async def registry_tags(s: AsyncSession) -> tuple[set[str], set[str]]:
    """(banned, preferred) for the transaction's workspace, aliases included."""
    banned: set[str] = set()
    preferred: set[str] = set()
    rows = (
        await s.execute(
            select(Hashtag.tag, Hashtag.status, Hashtag.aliases).where(
                Hashtag.status.in_(["banned", "preferred"])
            )
        )
    ).all()
    for tag, status, aliases in rows:
        target = banned if status == "banned" else preferred
        target.add(tag)
        target.update(a for a in aliases or [])
    return banned, preferred


_INLINE = re.compile(r"(?<![\w&])#(\w{1,100})")


def banned_tags_in(text: str, hashtags: list[str], banned: set[str]) -> list[str]:
    """Banned tags used either as hashtags or inline in the text (``#tag``)."""
    if not banned:
        return []
    used = {h.strip().lstrip("#").lower() for h in hashtags}
    used.update(m.lower() for m in _INLINE.findall(text))
    return sorted(used & banned)


async def account_evidence(
    s: AsyncSession, account_id: uuid.UUID, *, limit: int = 25
) -> tuple[TagEvidence, ...]:
    """The account's measured tags (P3-M1.1 rows), measured ones first by lift, then the
    most used of the rest; what the Hashtag agent is shown and its output is checked
    against."""
    rows = (
        await s.execute(
            select(HashtagStat.tag, HashtagStat.post_count, HashtagStat.lift_vs_account_median)
            .where(HashtagStat.social_account_id == account_id)
            .order_by(HashtagStat.post_count.desc())
            .limit(200)
        )
    ).all()
    evidence = [
        TagEvidence(tag, n, float(lift) if n >= MIN_HASHTAG_SAMPLES else None)
        for tag, n, lift in rows
    ]
    evidence.sort(key=lambda e: (e.lift is None, -(e.lift or 0.0), -e.post_count, e.tag))
    return tuple(evidence[:limit])


def apply_evidence(
    tags: list[SuggestedTag], evidence: tuple[TagEvidence, ...]
) -> list[SuggestedTag]:
    """The model picks the tags; the numbers and the order are Mera's (P3-M1.2).

    Each suggestion's basis, sample size and lift are replaced by what ``hashtag_stat``
    says (a model claiming ``history`` for an unmeasured tag is corrected), then ranked:
    measured at or above typical by lift, then unmeasured in the model's order, then
    measured below typical last. Ranks are renumbered from 1."""
    known = {normalize_hashtag(e.tag): e for e in evidence}
    fixed: list[SuggestedTag] = []
    for t in sorted(tags, key=lambda x: x.rank):
        e = known.get(t.tag)
        if e is not None and e.lift is not None:
            fixed.append(
                t.model_copy(
                    update={
                        "basis": "history",
                        "sample_size": e.post_count,
                        "lift_vs_account_median": round(e.lift, 4),
                    }
                )
            )
        else:
            basis = "content_only" if t.basis == "history" else t.basis
            fixed.append(
                t.model_copy(
                    update={
                        "basis": basis,
                        "sample_size": e.post_count if e is not None else 0,
                        "lift_vs_account_median": None,
                    }
                )
            )

    def band(t: SuggestedTag) -> tuple[int, float]:
        lift = t.lift_vs_account_median
        if lift is None:
            return (1, 0.0)
        return (0, -lift) if lift >= 1.0 else (2, -lift)

    ordered = sorted(enumerate(fixed), key=lambda it: (*band(it[1]), it[0]))
    return [t.model_copy(update={"rank": i}) for i, (_n, t) in enumerate(ordered, start=1)]
