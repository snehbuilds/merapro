"""The hashtag registry (P2-M1.3, docs/06 §4.4): workspace-wide tags marked allowed,
preferred or banned.

Banned tags are enforced three times, like banned words: by the drafting guardrail, by
the readiness check before a set goes for approval, and by ``assert_publishable`` just
before the platform call, so banning a tag after approval still stops a queued post.
Preferred tags are offered to the Hashtag agent first. Tags are stored lower case and
without the ``#``; aliases (``ml`` for ``machinelearning``) count as the same tag.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import InvalidInput, NotFoundError
from mera.application.hashtags import normalize_tag
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.errors import PermissionDenied
from mera.domain.policies import rbac
from mera.domain.policies.hashtag_lift import MIN_HASHTAG_SAMPLES
from mera.domain.policies.rbac import Permission
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import AccountGroupMember, Hashtag, HashtagStat, Workspace
from mera.infrastructure.db.repositories.audit import record

Status = Literal["allowed", "preferred", "banned"]
MAX_TAGS = 2000
MAX_ALIASES = 10


@dataclass(frozen=True, slots=True)
class HashtagView:
    id: uuid.UUID
    tag: str
    status: str
    aliases: list[str]
    first_used_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class HashtagStatView:
    social_account_id: uuid.UUID
    tag: str
    platform: str
    post_count: int
    avg_engagement: float
    lift: float | None  # None below the sample minimum: "not enough data"
    evidence: Literal["measured", "not_enough_data"]
    computed_at: datetime


def _view(h: Hashtag) -> HashtagView:
    return HashtagView(h.id, h.tag, h.status, list(h.aliases), h.first_used_at, h.updated_at)


class HashtagService(WorkspaceScopedService):
    async def list_tags(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        status: Status | None = None,
    ) -> list[HashtagView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = select(Hashtag).order_by(Hashtag.status, Hashtag.tag)
            if status:
                q = q.where(Hashtag.status == status)
            return [_view(h) for h in (await s.execute(q)).scalars()]

    async def stats(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        social_account_id: uuid.UUID | None = None,
        tag: str | None = None,
        limit: int = 200,
    ) -> list[HashtagStatView]:
        """Measured hashtag performance (P3-M1.1), only for accounts the caller may read.
        A row under the sample minimum carries no lift, so nothing downstream can show or
        use a number that is luck rather than evidence."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = (
                select(HashtagStat)
                .order_by(HashtagStat.lift_vs_account_median.desc(), HashtagStat.post_count.desc())
                .limit(min(max(limit, 1), 1000))
            )
            if social_account_id:
                q = q.where(HashtagStat.social_account_id == social_account_id)
            if tag:
                q = q.where(HashtagStat.tag == normalize_tag(tag))
            rows = list((await s.execute(q)).scalars())
            accounts = {r.social_account_id for r in rows}
            groups: dict[uuid.UUID, list[uuid.UUID]] = {a: [] for a in accounts}
            if accounts:
                for acc, grp in (
                    await s.execute(
                        select(
                            AccountGroupMember.social_account_id,
                            AccountGroupMember.account_group_id,
                        ).where(AccountGroupMember.social_account_id.in_(accounts))
                    )
                ).all():
                    groups[acc].append(grp)
            readable = set()
            for acc in accounts:
                try:
                    self._require_for_account(
                        principal, Permission.READ, workspace_id, acc, groups[acc]
                    )
                except PermissionDenied:
                    continue
                readable.add(acc)
            return [
                HashtagStatView(
                    social_account_id=r.social_account_id,
                    tag=r.tag,
                    platform=r.platform,
                    post_count=r.post_count,
                    avg_engagement=float(r.avg_engagement),
                    lift=(
                        float(r.lift_vs_account_median)
                        if r.post_count >= MIN_HASHTAG_SAMPLES
                        else None
                    ),
                    evidence=(
                        "measured" if r.post_count >= MIN_HASHTAG_SAMPLES else "not_enough_data"
                    ),
                    computed_at=r.computed_at,
                )
                for r in rows
                if r.social_account_id in readable
            ]

    async def upsert(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        tag: str,
        status: Status,
        aliases: list[str] | None = None,
    ) -> HashtagView:
        """Create the tag, or change its status and aliases if it exists."""
        norm = normalize_tag(tag)
        alias_set = sorted({normalize_tag(a) for a in (aliases or [])} - {norm})
        if len(alias_set) > MAX_ALIASES:
            raise InvalidInput(
                f"At most {MAX_ALIASES} aliases.", code="too_many_aliases", field="aliases"
            )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            self._ensure_writable(ws)
            existing = (
                await s.execute(select(Hashtag).where(Hashtag.tag == norm).with_for_update())
            ).scalar_one_or_none()
            before = (
                {"status": existing.status, "aliases": list(existing.aliases)} if existing else None
            )
            if existing is None:
                count = len(list((await s.execute(select(Hashtag.id))).scalars()))
                if count >= MAX_TAGS:
                    raise InvalidInput(f"At most {MAX_TAGS} tags.", code="hashtag_limit_reached")
                existing = Hashtag(id=uuid7(), workspace_id=workspace_id, tag=norm)
                s.add(existing)
            existing.status = status
            if aliases is not None:
                existing.aliases = alias_set
            existing.updated_at = datetime.now(UTC)
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "hashtag.updated" if before else "hashtag.created",
                    workspace_id=workspace_id,
                    subject_type="hashtag",
                    subject_id=existing.id,
                    before=before,
                    after={"tag": norm, "status": status, "aliases": list(existing.aliases)},
                ),
            )
            return _view(existing)

    async def delete(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        hashtag_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
            h = await s.get(Hashtag, hashtag_id, with_for_update=True)
            if h is None:
                raise NotFoundError("No such hashtag.", code="hashtag_not_found")
            await s.delete(h)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "hashtag.deleted",
                    workspace_id=workspace_id,
                    subject_type="hashtag",
                    subject_id=hashtag_id,
                    before={"tag": h.tag, "status": h.status},
                ),
            )
