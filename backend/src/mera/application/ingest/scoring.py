"""Writes per-group relevance (M4.8, docs/03 §5 "Sources belong to a workspace, and feed groups").

Which groups an item feeds is decided by **every source that carried it**
(``seen_source_ids``): a source linked to groups feeds those groups, a source with no links
feeds every group, and an item carried by several sources feeds the union. So the first
source to claim a URL cannot hide it from groups another source feeds, and an item whose
sources were all deleted feeds no group (it does not fall open to every group). For each
group, the pillars that count are the group's own plus the workspace-wide ones.
``content_item.relevance_score`` is the maximum across those groups and the workspace-wide
pillars alone, which is what the unfiltered Trending view sorts on.

Scoring is arithmetic over stored embeddings, so it runs inline whenever pillars, links or
source weights change. It works in chunks (bounded memory, and well under asyncpg's
parameter limit), and writes each chunk with one bulk update and one bulk insert.
Freshness is measured at the moment the item was found (publication to discovery), so the
same item rescored tomorrow gets the same number; Trending applies its own time window.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

import numpy as np
from sqlalchemy import any_, delete, literal, select, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mera.domain.policies import relevance
from mera.domain.value_objects.content import ItemState
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    AccountGroup,
    ContentItem,
    ContentItemScore,
    ContentPillar,
    Source,
    SourceGroupLink,
)

RESCORE_WINDOW: Final = timedelta(days=14)
CHUNK: Final = 500


@dataclass(frozen=True, slots=True)
class _Pillar:
    id: uuid.UUID
    group_id: uuid.UUID | None
    weight: float


async def rescore(
    s: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    item_ids: Sequence[uuid.UUID] | None = None,
    source_id: uuid.UUID | None = None,
    since: datetime | None = None,
    half_life_hours: float = relevance.DEFAULT_HALF_LIFE_HOURS,
) -> int:
    """Recompute scores for ready items in the session's workspace. Returns items scored."""
    pillar_rows = (
        await s.execute(
            select(
                ContentPillar.id,
                ContentPillar.account_group_id,
                ContentPillar.weight,
                ContentPillar.embedding,
            ).where(ContentPillar.embedding.is_not(None))
        )
    ).all()
    pillars = [_Pillar(r.id, r.account_group_id, float(r.weight)) for r in pillar_rows]
    pillar_matrix = (
        _normalise(np.array([np.asarray(r.embedding, dtype=np.float32) for r in pillar_rows]))
        if pillar_rows
        else None
    )
    group_ids = list((await s.execute(select(AccountGroup.id))).scalars())
    links: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for sid, gid in (
        await s.execute(select(SourceGroupLink.source_id, SourceGroupLink.account_group_id))
    ).all():
        links[sid].add(gid)
    weights = {
        sid: float(w) for sid, w in (await s.execute(select(Source.id, Source.weight))).all()
    }

    q = select(
        ContentItem.id,
        ContentItem.source_id,
        ContentItem.seen_source_ids,
        ContentItem.published_at,
        ContentItem.fetched_at,
    ).where(ContentItem.state == ItemState.READY.value, ContentItem.embedding.is_not(None))
    if source_id is not None:
        q = q.where(literal(source_id, PG_UUID(as_uuid=True)) == any_(ContentItem.seen_source_ids))
    if since is not None:
        q = q.where(ContentItem.fetched_at >= since)
    if item_ids is not None:
        wanted = list(dict.fromkeys(item_ids))
        items: list[Any] = []
        for i in range(0, len(wanted), CHUNK):
            items += (await s.execute(q.where(ContentItem.id.in_(wanted[i : i + CHUNK])))).all()
    else:
        items = list((await s.execute(q)).all())

    for start in range(0, len(items), CHUNK):
        chunk = items[start : start + CHUNK]
        await _score_chunk(
            s,
            workspace_id,
            chunk,
            pillars=pillars,
            pillar_matrix=pillar_matrix,
            group_ids=group_ids,
            links=links,
            weights=weights,
            half_life_hours=half_life_hours,
        )
    return len(items)


def fed_groups(
    seen: Sequence[uuid.UUID],
    *,
    links: dict[uuid.UUID, set[uuid.UUID]],
    live_sources: set[uuid.UUID],
    group_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for sid in seen:
        if sid not in live_sources:
            continue  # a deleted source feeds nobody
        linked = links.get(sid)
        if not linked:
            return group_ids  # a workspace-wide source feeds every group
        out |= linked
    return sorted(out)


async def _score_chunk(
    s: AsyncSession,
    workspace_id: uuid.UUID,
    chunk: list[Any],
    *,
    pillars: list[_Pillar],
    pillar_matrix: np.ndarray | None,
    group_ids: list[uuid.UUID],
    links: dict[uuid.UUID, set[uuid.UUID]],
    weights: dict[uuid.UUID, float],
    half_life_hours: float,
) -> None:
    ids = [i.id for i in chunk]
    embeddings = dict(
        (
            await s.execute(
                select(ContentItem.id, ContentItem.embedding).where(ContentItem.id.in_(ids))
            )
        ).all()
    )
    sims = None
    if pillar_matrix is not None:
        item_matrix = _normalise(
            np.array([np.asarray(embeddings[i], dtype=np.float32) for i in ids])
        )
        sims = item_matrix @ pillar_matrix.T
    live = set(weights)
    rows: list[dict[str, Any]] = []
    item_updates: list[dict[str, Any]] = []
    for idx, item in enumerate(chunk):
        seen = list(item.seen_source_ids or ([item.source_id] if item.source_id else []))
        groups = fed_groups(seen, links=links, live_sources=live, group_ids=group_ids)
        fr = relevance.freshness(
            item.published_at, item.fetched_at, half_life_hours=half_life_hours
        )
        live_seen = [sid for sid in seen if sid in live]
        sw = max((weights[sid] for sid in live_seen), default=1.0)
        sim_row = (
            {str(p.id): float(sims[idx, j]) for j, p in enumerate(pillars)}
            if sims is not None
            else {}
        )
        best: float | None = None
        ws_score = _combine([p for p in pillars if p.group_id is None], sim_row, sw, fr)
        if ws_score is not None:
            best = ws_score.relevance
        for gid in groups:
            sc = _combine([p for p in pillars if p.group_id in (None, gid)], sim_row, sw, fr)
            if sc is None:
                continue
            best = sc.relevance if best is None else max(best, sc.relevance)
            rows.append(
                {
                    "id": uuid7(),
                    "workspace_id": workspace_id,
                    "content_item_id": item.id,
                    "account_group_id": gid,
                    "relevance_score": sc.relevance,
                    "pillar_scores": sc.pillar_scores,
                    "components": {
                        "pillar_match": sc.pillar_match,
                        "freshness": sc.freshness,
                        "source_weight": sc.source_weight,
                        "best_pillar_id": sc.best_pillar_id,
                    },
                }
            )
        item_updates.append(
            {"id": item.id, "relevance_score": best, "freshness_score": round(fr, 4)}
        )

    await s.execute(delete(ContentItemScore).where(ContentItemScore.content_item_id.in_(ids)))
    if rows:
        await s.execute(insert(ContentItemScore), rows)
    await s.execute(update(ContentItem), item_updates)


def _combine(
    selected: list[_Pillar], sim_row: dict[str, float], sw: float, fr: float
) -> relevance.Score | None:
    if not selected:
        return None
    return relevance.combine(
        {str(p.id): sim_row[str(p.id)] for p in selected},
        {str(p.id): p.weight for p in selected},
        source_weight=sw,
        freshness_value=fr,
    )


def _normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    out: np.ndarray = m / norms
    return out


async def rescore_recent(
    s: AsyncSession, workspace_id: uuid.UUID, *, now: datetime, half_life_hours: float
) -> int:
    return await rescore(
        s, workspace_id, since=now - RESCORE_WINDOW, half_life_hours=half_life_hours
    )
