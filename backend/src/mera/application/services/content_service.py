"""Content items, Trending, and content pillars (M4.8, 4.10; docs/04 §4.4).

Trending with a ``group_id`` ranks by that group's own pillar score, which is what the
group's queue shows; without one it ranks by the workspace-wide maximum (docs/04 §4.4). A
scope-restricted member never sees scores for groups outside their scope: their unfiltered
view ranks by the best score among *their* groups.

Pillar writes embed the pillar's text before the transaction (a provider call never holds
a pooled connection) and rescore the last 14 days inline: scoring is arithmetic over
stored vectors, so the Discovery view is right the moment the request returns.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import and_, any_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, InvalidInput, NotFoundError
from mera.application.ingest import scoring
from mera.application.ingest.pipeline import Ingestion, pillar_text
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.policies import keyword_filter, rbac
from mera.domain.policies.rbac import Permission
from mera.domain.principal import Principal
from mera.domain.value_objects.content import ItemState
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    AccountGroup,
    ContentItem,
    ContentItemScore,
    ContentPillar,
    Source,
    SourceGroupLink,
    Workspace,
)
from mera.infrastructure.db.repositories.audit import record

MAX_PILLARS: Final = 50
ABOVE_THRESHOLD: Final = 0.5


@dataclass(frozen=True, slots=True)
class DuplicateRef:
    id: uuid.UUID
    title: str
    source_name: str | None
    similarity: float | None
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class GroupScore:
    account_group_id: uuid.UUID
    group_name: str
    relevance: float
    pillar_match: float | None
    freshness: float | None
    source_weight: float | None
    best_pillar: str | None
    pillar_scores: dict[str, float]  # pillar name -> similarity


@dataclass(frozen=True, slots=True)
class ItemView:
    id: uuid.UUID
    title: str
    url: str
    source_id: uuid.UUID | None
    source_name: str | None
    author: str | None
    published_at: datetime | None
    fetched_at: datetime
    summary: str | None
    topics: list[str]
    language: str | None
    relevance: float | None
    freshness: float | None
    engagement_potential: float | None
    engagement_rationale: str | None
    risk_flags: list[str]
    best_pillar: str | None
    scored_group_id: uuid.UUID | None
    state: str
    state_reason: str | None
    og_image_url: str | None
    coverage: int
    dismissed: bool
    duplicate_of: DuplicateRef | None = None
    duplicates: list[DuplicateRef] = field(default_factory=list)
    scores: list[GroupScore] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ItemPage:
    data: list[ItemView]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class Stats:
    days: int
    ingested: int
    after_dedupe: int
    above_threshold: int
    filtered: int
    threshold: float
    drafted: int | None  # post sets arrive in M5
    published: int | None  # publishing arrives in M8


@dataclass(frozen=True, slots=True)
class PillarView:
    id: uuid.UUID
    account_group_id: uuid.UUID | None
    name: str
    description: str | None
    keywords: list[str]
    weight: float
    embedded: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PillarChanges:
    name: str | None = None
    description: str | None = None
    clear_description: bool = False
    keywords: list[str] | None = None
    weight: float | None = None


class ContentService(WorkspaceScopedService):
    def __init__(self, sm: Any, ingestion: Ingestion, *, half_life_hours: float) -> None:
        super().__init__(sm)
        self._ingestion = ingestion
        self._half_life = half_life_hours

    # ------------------------------------------------------------------ items

    async def trending(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        group_id: uuid.UUID | None,
        hours: int,
        min_relevance: float,
        limit: int,
    ) -> list[ItemView]:
        since = datetime.now(UTC) - timedelta(hours=hours)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id, account_group_id=group_id)
            base = and_(
                ContentItem.state == ItemState.READY.value,
                ContentItem.dismissed_at.is_(None),
                # The window is about the news, not when Mera found it: a first fetch backfills
                # weeks-old posts. Undated items count from discovery.
                func.coalesce(ContentItem.published_at, ContentItem.fetched_at) >= since,
            )
            groups = _score_groups(principal, group_id)
            if groups is None:
                rel = ContentItem.relevance_score
                q: Any = select(ContentItem, rel.label("rel")).where(base)
                if min_relevance > 0:
                    q = q.where(rel >= min_relevance)
                q = q.order_by(rel.desc().nulls_last(), ContentItem.fetched_at.desc())
            else:
                rel_max = func.max(ContentItemScore.relevance_score).label("rel")
                q = (
                    select(ContentItem, rel_max)
                    .join(ContentItemScore, ContentItemScore.content_item_id == ContentItem.id)
                    .where(base, ContentItemScore.account_group_id.in_(groups))
                    .group_by(ContentItem.id)
                    .order_by(rel_max.desc(), ContentItem.fetched_at.desc())
                )
                if min_relevance > 0:
                    q = q.having(rel_max >= min_relevance)
            rows = (await s.execute(q.limit(limit))).all()
            return await _views(
                s, principal, [(r[0], r[1]) for r in rows], group_id, with_duplicates=True
            )

    async def list_items(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        group_id: uuid.UUID | None,
        source_id: uuid.UUID | None,
        topic: str | None,
        states: Sequence[ItemState],
        min_relevance: float | None,
        since: datetime | None,
        include_dismissed: bool,
        limit: int,
        cursor: str | None,
    ) -> ItemPage:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id, account_group_id=group_id)
            if any(st not in (ItemState.READY, ItemState.DUPLICATE) for st in states):
                # Pending, filtered and failed items are pipeline internals (filter reasons
                # reveal keyword configuration): for people who configure sources.
                rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
            q = select(ContentItem).where(ContentItem.state.in_([st.value for st in states]))
            groups = _score_groups(principal, group_id)
            if groups is not None:
                scored = select(ContentItemScore.content_item_id).where(
                    ContentItemScore.account_group_id.in_(groups)
                )
                if min_relevance is not None:
                    scored = scored.where(ContentItemScore.relevance_score >= min_relevance)
                q = q.where(ContentItem.id.in_(scored))
            elif min_relevance is not None:
                q = q.where(ContentItem.relevance_score >= min_relevance)
            if source_id is not None:
                q = q.where(ContentItem.source_id == source_id)
            if topic:
                q = q.where(ContentItem.topics.contains([topic.lower().strip()]))
            if since is not None:
                q = q.where(ContentItem.fetched_at >= since)
            if not include_dismissed:
                q = q.where(ContentItem.dismissed_at.is_(None))
            if cursor:
                at, cid = _decode_cursor(cursor)
                q = q.where(
                    or_(
                        ContentItem.fetched_at < at,
                        and_(ContentItem.fetched_at == at, ContentItem.id < cid),
                    )
                )
            q = q.order_by(ContentItem.fetched_at.desc(), ContentItem.id.desc()).limit(limit + 1)
            items = list((await s.execute(q)).scalars())
            more = len(items) > limit
            items = items[:limit]
            views = await _views(
                s, principal, [(i, None) for i in items], group_id, with_duplicates=False
            )
            nxt = _encode_cursor(items[-1].fetched_at, items[-1].id) if more and items else None
            return ItemPage(views, nxt)

    async def get_item(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        item_id: uuid.UUID,
        meta: RequestMeta,
    ) -> ItemView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            item = await s.get(ContentItem, item_id)
            if (
                item is None
                or item.state == ItemState.FILTERED.value
                or not await _can_see(s, principal, item_id)
            ):
                raise NotFoundError("Content item not found.")
            (view,) = await _views(
                s, principal, [(item, None)], None, with_duplicates=True, with_scores=True
            )
            return view

    async def dismiss(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        item_id: uuid.UUID,
        meta: RequestMeta,
        *,
        undo: bool,
        group_id: uuid.UUID | None,
    ) -> ItemView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            # Dismissal hides an item for everyone; a group-scoped Editor may do it from
            # their group's view.
            rbac.require(principal, Permission.EDIT_POSTS, workspace_id, account_group_id=group_id)
            await self._writable(s, workspace_id)
            item = await s.get(ContentItem, item_id, with_for_update=True)
            if (
                item is None
                or item.state == ItemState.FILTERED.value
                or not await _can_see(s, principal, item_id)
            ):
                raise NotFoundError("Content item not found.")
            scope = principal.member_scope
            if scope.restricted:
                # Dismissal hides the item for every group it feeds, so a scoped member needs
                # every one of those groups in scope (review M1). Fails with a normal denial.
                item_groups = set(
                    (
                        await s.execute(
                            select(ContentItemScore.account_group_id).where(
                                ContentItemScore.content_item_id == item_id
                            )
                        )
                    ).scalars()
                )
                if not item_groups <= set(scope.group_ids):
                    rbac.require(principal, Permission.EDIT_POSTS, workspace_id)
            if group_id is not None and not await s.scalar(
                select(func.count())
                .select_from(ContentItemScore)
                .where(
                    ContentItemScore.content_item_id == item_id,
                    ContentItemScore.account_group_id == group_id,
                )
            ):
                raise NotFoundError("Content item not found.")
            changed = (item.dismissed_at is None) == (not undo)
            if undo:
                item.dismissed_at, item.dismissed_by = None, None
            elif item.dismissed_at is None:
                item.dismissed_at, item.dismissed_by = datetime.now(UTC), user.user_id
            if changed:
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "content_item.restored" if undo else "content_item.dismissed",
                        workspace_id=workspace_id,
                        subject_type="content_item",
                        subject_id=item_id,
                        account_group_id=group_id,
                    ),
                )
            await s.flush()
            (view,) = await _views(s, principal, [(item, None)], group_id, with_duplicates=False)
            return view

    async def stats(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        group_id: uuid.UUID | None,
        days: int,
    ) -> Stats:
        since = datetime.now(UTC) - timedelta(days=days)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id, account_group_id=group_id)
            q = select(ContentItem.state, func.count()).where(ContentItem.fetched_at >= since)
            groups = _score_groups(principal, group_id)
            if groups is not None:
                q = q.where(_fed_by_any(groups))
            by_state = {
                st: int(n) for st, n in (await s.execute(q.group_by(ContentItem.state))).all()
            }
            if groups is None:
                above = await s.scalar(
                    select(func.count()).where(
                        ContentItem.fetched_at >= since,
                        ContentItem.state == ItemState.READY.value,
                        ContentItem.relevance_score >= ABOVE_THRESHOLD,
                    )
                )
            else:
                above = await s.scalar(
                    select(func.count(func.distinct(ContentItemScore.content_item_id)))
                    .join(ContentItem, ContentItem.id == ContentItemScore.content_item_id)
                    .where(
                        ContentItem.fetched_at >= since,
                        ContentItem.state == ItemState.READY.value,
                        ContentItemScore.account_group_id.in_(groups),
                        ContentItemScore.relevance_score >= ABOVE_THRESHOLD,
                    )
                )
            filtered = by_state.get(ItemState.FILTERED.value, 0)
            return Stats(
                days=days,
                ingested=sum(by_state.values()) - filtered,
                after_dedupe=by_state.get(ItemState.READY.value, 0),
                above_threshold=int(above or 0),
                filtered=filtered,
                threshold=ABOVE_THRESHOLD,
                drafted=None,
                published=None,
            )

    # ---------------------------------------------------------------- pillars

    async def list_pillars(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        group_id: uuid.UUID | None,
    ) -> list[PillarView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id, account_group_id=group_id)
            q = select(ContentPillar).order_by(
                ContentPillar.account_group_id.nulls_first(), ContentPillar.name
            )
            if group_id is not None:  # the pillars that score this group's items
                q = q.where(
                    or_(
                        ContentPillar.account_group_id.is_(None),
                        ContentPillar.account_group_id == group_id,
                    )
                )
            elif principal.member_scope.restricted:
                q = q.where(
                    or_(
                        ContentPillar.account_group_id.is_(None),
                        ContentPillar.account_group_id.in_(list(principal.member_scope.group_ids)),
                    )
                )
            return [_pillar_view(p) for p in (await s.execute(q)).scalars()]

    async def create_pillar(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        name: str,
        description: str | None,
        keywords: list[str],
        weight: float,
        group_id: uuid.UUID | None,
    ) -> PillarView:
        name, kws = _pillar_fields(name, keywords, weight)
        async with self._tx(user, workspace_id, meta) as s:  # authorize before spending
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(
                principal, Permission.CONFIGURE_CONTENT, workspace_id, account_group_id=group_id
            )
            await self._writable(s, workspace_id)
        pillar_id = uuid7()
        vec = await self._ingestion.embed_text(
            workspace_id,
            pillar_text(name, description, kws),
            trigger="manual",
            subject_id=pillar_id,
        )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(
                principal, Permission.CONFIGURE_CONTENT, workspace_id, account_group_id=group_id
            )
            if group_id is not None and await s.get(AccountGroup, group_id) is None:
                raise InvalidInput("Unknown group.", code="unknown_group", field="group_id")
            if (
                await s.scalar(select(func.count()).select_from(ContentPillar)) or 0
            ) >= MAX_PILLARS:
                raise ConflictError(
                    f"A workspace can have at most {MAX_PILLARS} pillars.",
                    code="pillar_limit_reached",
                )
            p = ContentPillar(
                id=pillar_id,
                workspace_id=workspace_id,
                account_group_id=group_id,
                name=name,
                description=(description or "").strip() or None,
                keywords=kws,
                weight=weight,
                embedding=vec,
                embedding_model=self._ingestion.embedding_model if vec is not None else None,
            )
            s.add(p)
            await s.flush()
            await self._rescore(s, workspace_id)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "content_pillar.created",
                    workspace_id=workspace_id,
                    subject_type="content_pillar",
                    subject_id=p.id,
                    account_group_id=group_id,
                    after={"name": name, "keywords": kws, "weight": weight},
                ),
            )
            await s.refresh(p)
            return _pillar_view(p)

    async def update_pillar(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        pillar_id: uuid.UUID,
        meta: RequestMeta,
        changes: PillarChanges,
    ) -> PillarView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            p = await _pillar(s, pillar_id)
            rbac.require(
                principal,
                Permission.CONFIGURE_CONTENT,
                workspace_id,
                account_group_id=p.account_group_id,
            )
            await self._writable(s, workspace_id)
            name = changes.name.strip() if changes.name is not None else p.name
            description = (
                None
                if changes.clear_description
                else (
                    changes.description.strip()
                    if changes.description is not None
                    else p.description
                )
            )
            kws = (
                keyword_filter.normalize_keywords(changes.keywords)
                if changes.keywords is not None
                else list(p.keywords)
            )
            weight = changes.weight if changes.weight is not None else float(p.weight)
            _pillar_fields(name, kws, weight)
            text_changed = (name, description or None, kws) != (
                p.name,
                p.description,
                list(p.keywords),
            )
        vec = (
            await self._ingestion.embed_text(
                workspace_id,
                pillar_text(name, description, kws),
                trigger="manual",
                subject_id=pillar_id,
            )
            if text_changed
            else None
        )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            p = await _pillar(s, pillar_id, lock=True)
            rbac.require(
                principal,
                Permission.CONFIGURE_CONTENT,
                workspace_id,
                account_group_id=p.account_group_id,
            )
            before = {"name": p.name, "keywords": list(p.keywords), "weight": float(p.weight)}
            p.name, p.description, p.keywords, p.weight = name, description or None, kws, weight
            if text_changed:
                # A failed re-embed must not keep scoring with the old text's vector.
                p.embedding = vec
                p.embedding_model = self._ingestion.embedding_model if vec is not None else None
            await s.flush()
            await self._rescore(s, workspace_id)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "content_pillar.updated",
                    workspace_id=workspace_id,
                    subject_type="content_pillar",
                    subject_id=pillar_id,
                    account_group_id=p.account_group_id,
                    before=before,
                    after={"name": name, "keywords": kws, "weight": weight},
                ),
            )
            await s.refresh(p)
            return _pillar_view(p)

    async def delete_pillar(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        pillar_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            p = await _pillar(s, pillar_id, lock=True)
            rbac.require(
                principal,
                Permission.CONFIGURE_CONTENT,
                workspace_id,
                account_group_id=p.account_group_id,
            )
            await self._writable(s, workspace_id)
            await s.delete(p)
            await s.flush()
            await self._rescore(s, workspace_id)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "content_pillar.deleted",
                    workspace_id=workspace_id,
                    subject_type="content_pillar",
                    subject_id=pillar_id,
                    account_group_id=p.account_group_id,
                    before={"name": p.name},
                ),
            )

    async def _rescore(self, s: AsyncSession, workspace_id: uuid.UUID) -> None:
        await scoring.rescore_recent(
            s, workspace_id, now=datetime.now(UTC), half_life_hours=self._half_life
        )

    async def _writable(self, s: AsyncSession, workspace_id: uuid.UUID) -> None:
        ws = await s.get(Workspace, workspace_id)
        if ws is None:
            raise NotFoundError("Workspace not found.")
        self._ensure_writable(ws)


# ----------------------------------------------------------------------- helpers


def _score_groups(principal: Principal, group_id: uuid.UUID | None) -> list[uuid.UUID] | None:
    """Which groups' scores a view may use. ``None`` means the workspace-wide maximum."""
    if group_id is not None:
        return [group_id]
    scope = principal.member_scope
    return sorted(scope.group_ids) if scope.restricted else None


def _visibility(principal: Principal) -> list[Any]:
    """Extra WHERE terms limiting items to what a principal may see: everything for an
    unrestricted member; for a scoped one, items scored for one of their groups (review H3).
    Duplicates carry no scores: one is visible when a source that carried *it* feeds one of
    their groups, so another group's feed never leaks through a duplicate reference."""
    scope = principal.member_scope
    if not scope.restricted:
        return []
    in_scope = select(ContentItemScore.content_item_id).where(
        ContentItemScore.account_group_id.in_(list(scope.group_ids))
    )
    groups = list(scope.group_ids)
    return [
        or_(
            ContentItem.id.in_(in_scope),
            and_(ContentItem.duplicate_of.is_not(None), _fed_by_any(groups)),
        )
    ]


async def _can_see(s: AsyncSession, principal: Principal, item_id: uuid.UUID) -> bool:
    terms = _visibility(principal)
    if not terms:
        return True
    return bool(await s.scalar(select(ContentItem.id).where(ContentItem.id == item_id, *terms)))


def _fed_by_any(groups: list[uuid.UUID]) -> Any:
    """Items fed to any of ``groups``: carried by a source linked to one of them, or by a
    live source with no links (the same rule as scoring.fed_groups)."""
    carried = any_(ContentItem.seen_source_ids)
    linked_here = (
        select(SourceGroupLink.id)
        .where(SourceGroupLink.account_group_id.in_(groups), SourceGroupLink.source_id == carried)
        .exists()
    )
    unlinked = (
        select(Source.id)
        .where(
            Source.id == carried,
            ~select(SourceGroupLink.id).where(SourceGroupLink.source_id == Source.id).exists(),
        )
        .exists()
    )
    return or_(linked_here, unlinked)


async def _views(
    s: AsyncSession,
    principal: Principal,
    rows: list[tuple[ContentItem, float | None]],
    group_id: uuid.UUID | None,
    *,
    with_duplicates: bool,
    with_scores: bool = False,
) -> list[ItemView]:
    if not rows:
        return []
    ids = [i.id for i, _ in rows]
    source_names: dict[uuid.UUID, str] = dict(
        (await s.execute(select(Source.id, Source.name))).all()
    )
    pillar_names: dict[str, str] = {
        str(pid): n
        for pid, n in (await s.execute(select(ContentPillar.id, ContentPillar.name))).all()
    }
    group_names: dict[uuid.UUID, str] = dict(
        (await s.execute(select(AccountGroup.id, AccountGroup.name))).all()
    )
    visible = _score_groups(principal, group_id)
    score_q = select(ContentItemScore).where(ContentItemScore.content_item_id.in_(ids))
    if visible is not None:
        score_q = score_q.where(ContentItemScore.account_group_id.in_(visible))
    scores: defaultdict[uuid.UUID, list[ContentItemScore]] = defaultdict(list)
    for sc in (await s.execute(score_q)).scalars():
        scores[sc.content_item_id].append(sc)

    dups: defaultdict[uuid.UUID, list[ContentItem]] = defaultdict(list)
    dup_q = (
        select(ContentItem)
        .where(ContentItem.duplicate_of.in_(ids), *_visibility(principal))
        .order_by(ContentItem.fetched_at)
    )
    for d in (await s.execute(dup_q)).scalars():
        dups[d.duplicate_of].append(d)  # type: ignore[index]
    originals: dict[uuid.UUID, ContentItem] = {}
    orig_ids = [i.duplicate_of for i, _ in rows if i.duplicate_of]
    if orig_ids:
        originals = {
            o.id: o
            for o in (
                await s.execute(
                    select(ContentItem).where(ContentItem.id.in_(orig_ids), *_visibility(principal))
                )
            ).scalars()
        }

    def ref(d: ContentItem) -> DuplicateRef:
        return DuplicateRef(
            id=d.id,
            title=d.title,
            source_name=source_names.get(d.source_id) if d.source_id else None,
            similarity=d.duplicate_similarity,
            fetched_at=d.fetched_at,
        )

    out: list[ItemView] = []
    for item, rel in rows:
        item_scores = sorted(scores.get(item.id, []), key=lambda x: x.relevance_score, reverse=True)
        top = item_scores[0] if item_scores else None
        if rel is None:
            # A scoped view never falls back to the workspace-wide maximum.
            scoped_rel = top.relevance_score if top else None
            rel = item.relevance_score if visible is None else scoped_rel
        comps = dict(top.components) if top else {}
        best = (
            pillar_names.get(str(comps.get("best_pillar_id")))
            if comps.get("best_pillar_id")
            else None
        )
        sources = set(item.seen_source_ids or [])
        for d in dups.get(item.id, []):
            sources.update(d.seen_source_ids or [])
        out.append(
            ItemView(
                id=item.id,
                title=item.title,
                url=item.url,
                source_id=item.source_id,
                source_name=source_names.get(item.source_id) if item.source_id else None,
                author=item.author,
                published_at=item.published_at,
                fetched_at=item.fetched_at,
                summary=item.summary,
                topics=list(item.topics or []),
                language=item.language,
                relevance=rel,
                freshness=item.freshness_score,
                engagement_potential=item.engagement_potential,
                engagement_rationale=item.engagement_rationale,
                risk_flags=list(item.risk_flags or []),
                best_pillar=best,
                scored_group_id=top.account_group_id if top else None,
                state=item.state,
                state_reason=item.state_reason,
                og_image_url=item.og_image_url,
                # Distinct sources that carried the story: exact repeats (seen_source_ids)
                # plus the sources of its semantic duplicates.
                coverage=max(len(sources), 1),
                dismissed=item.dismissed_at is not None,
                duplicate_of=ref(originals[item.duplicate_of])
                if item.duplicate_of in originals
                else None,
                duplicates=[ref(d) for d in dups.get(item.id, [])] if with_duplicates else [],
                scores=[
                    GroupScore(
                        account_group_id=sc.account_group_id,
                        group_name=group_names.get(sc.account_group_id, "Unknown group"),
                        relevance=sc.relevance_score,
                        pillar_match=sc.components.get("pillar_match"),
                        freshness=sc.components.get("freshness"),
                        source_weight=sc.components.get("source_weight"),
                        best_pillar=pillar_names.get(str(sc.components.get("best_pillar_id"))),
                        pillar_scores={
                            pillar_names.get(k, k): v for k, v in sc.pillar_scores.items()
                        },
                    )
                    for sc in item_scores
                ]
                if with_scores
                else [],
            )
        )
    return out


def _pillar_fields(name: str, keywords: list[str], weight: float) -> tuple[str, list[str]]:
    name = name.strip()
    if not 1 <= len(name) <= 80:
        raise InvalidInput(
            "Give the pillar a name of up to 80 characters.", code="invalid_name", field="name"
        )
    kws = keyword_filter.normalize_keywords(keywords)
    if len(kws) > 20 or any(len(k) > 60 for k in kws):
        raise InvalidInput(
            "At most 20 keywords of up to 60 characters.", code="invalid_keywords", field="keywords"
        )
    if not 0 <= weight <= 4:
        raise InvalidInput("Weight must be between 0 and 4.", code="invalid_weight", field="weight")
    return name, kws


async def _pillar(s: AsyncSession, pillar_id: uuid.UUID, *, lock: bool = False) -> ContentPillar:
    p = await s.get(ContentPillar, pillar_id, with_for_update=lock)
    if p is None:
        raise NotFoundError("Pillar not found.")
    return p


def _pillar_view(p: ContentPillar) -> PillarView:
    return PillarView(
        id=p.id,
        account_group_id=p.account_group_id,
        name=p.name,
        description=p.description,
        keywords=list(p.keywords),
        weight=float(p.weight),
        embedded=p.embedding is not None,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _encode_cursor(at: datetime, item_id: uuid.UUID) -> str:
    raw = json.dumps({"t": at.isoformat(), "id": str(item_id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
        return datetime.fromisoformat(data["t"]), uuid.UUID(data["id"])
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise InvalidInput("Invalid cursor.", code="invalid_cursor", field="cursor") from exc
