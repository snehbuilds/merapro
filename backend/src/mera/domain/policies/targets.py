"""Target expansion and stagger offsets (M5.7; docs/04 §4.6, docs/03 §4.2).

Groups and accounts are expanded into one target per **account**: an account reachable
through two selected groups still gets one variant, routed through the first selected
group that contains it (that group's settings apply). Direct picks are routed through no
group. Excluded and inactive accounts are dropped with a reason, never silently.

Stagger offsets are fixed at expansion and stored, so the calendar, a retry and what the
approver saw agree. The order is the routing groups' member order, then direct picks;
the random spread is seeded by the set id, so recomputing gives the same answer.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AccountInfo:
    id: uuid.UUID
    platform: str
    active: bool


@dataclass(frozen=True, slots=True)
class GroupInfo:
    id: uuid.UUID
    member_ids: tuple[uuid.UUID, ...]  # in publish order (position)
    stagger_mode: str  # none, fixed, random_spread
    stagger_min_seconds: int
    stagger_max_seconds: int


@dataclass(frozen=True, slots=True)
class Target:
    social_account_id: uuid.UUID
    platform: str
    via_account_group_id: uuid.UUID | None
    stagger_offset_seconds: int


@dataclass(frozen=True, slots=True)
class Skipped:
    social_account_id: uuid.UUID
    reason: str  # excluded, inactive, unknown


@dataclass(frozen=True, slots=True)
class Expansion:
    targets: list[Target]
    skipped: list[Skipped]


DEFAULT_STAGGER = ("random_spread", 180, 900)


def expand(
    *,
    group_ids: Sequence[uuid.UUID],
    account_ids: Sequence[uuid.UUID],
    exclude_ids: Sequence[uuid.UUID],
    groups: Mapping[uuid.UUID, GroupInfo],
    accounts: Mapping[uuid.UUID, AccountInfo],
    seed: str,
) -> Expansion:
    excluded = set(exclude_ids)
    ordered: list[tuple[uuid.UUID, uuid.UUID | None]] = []
    seen: set[uuid.UUID] = set()
    skipped: dict[uuid.UUID, Skipped] = {}

    def consider(aid: uuid.UUID, via: uuid.UUID | None) -> None:
        if aid in seen or aid in skipped:
            return
        info = accounts.get(aid)
        if aid in excluded:
            skipped[aid] = Skipped(aid, "excluded")
        elif info is None:
            skipped[aid] = Skipped(aid, "unknown")
        elif not info.active:
            skipped[aid] = Skipped(aid, "inactive")
        else:
            seen.add(aid)
            ordered.append((aid, via))

    for gid in dict.fromkeys(group_ids):
        group = groups.get(gid)
        for aid in group.member_ids if group else ():
            consider(aid, gid)
    for aid in dict.fromkeys(account_ids):
        consider(aid, None)

    first_group = next((groups[g] for g in dict.fromkeys(group_ids) if g in groups), None)
    mode, lo, hi = (
        (first_group.stagger_mode, first_group.stagger_min_seconds, first_group.stagger_max_seconds)
        if first_group
        else DEFAULT_STAGGER
    )
    offsets = stagger_offsets(len(ordered), mode=mode, lo=lo, hi=hi, seed=seed)
    targets = [
        Target(aid, accounts[aid].platform, via, off)
        for (aid, via), off in zip(ordered, offsets, strict=True)
    ]
    return Expansion(targets, list(skipped.values()))


def stagger_offsets(n: int, *, mode: str, lo: int, hi: int, seed: str) -> list[int]:
    """Cumulative offsets from the first post (which is at 0)."""
    if n <= 0:
        return []
    if mode == "none":
        return [0] * n
    rng = random.Random(seed)  # noqa: S311 - spacing posts, not security
    out = [0]
    for _ in range(n - 1):
        gap = lo if mode == "fixed" else rng.randint(min(lo, hi), max(lo, hi))
        out.append(out[-1] + gap)
    return out
