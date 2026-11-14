"""A/B tests (P3-M2): version B of one account's post, and an honest result.

``create_b`` makes B a post set of its own for the same account, linked to A's, drafted by
the Writer from the same subject with the hypothesis and A's text as guidance (or written by
a person). Both go through approval like any post; the per-account spacing keeps them apart
on the calendar. ``results`` compares their engagement rates (``mera.domain.policies.ab``).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.drafting import core
from mera.application.errors import ConflictError, InvalidInput, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.post_service import PostService, PostSetView, TargetSelection
from mera.application.services.principals import load_user_principal
from mera.domain.policies.ab import MIN_IMPRESSIONS, Verdict, verdict
from mera.domain.policies.rbac import Permission
from mera.infrastructure.db.models import (
    AccountGroupMember,
    MetricSnapshot,
    PostSet,
    PostVariant,
    PostVersion,
    PublishedPost,
)

ENGAGEMENT = ("likes", "comments", "shares")


class AbService(WorkspaceScopedService):
    def __init__(self, sm: Any, posts: PostService) -> None:
        super().__init__(sm)
        self._posts = posts

    async def _check(
        self,
        s: AsyncSession,
        user: AuthenticatedUser,
        ws: uuid.UUID,
        v: PostVariant,
        perm: Permission,
    ) -> None:
        principal = await load_user_principal(s, user, ws)
        groups = list(
            (
                await s.execute(
                    select(AccountGroupMember.account_group_id).where(
                        AccountGroupMember.social_account_id == v.social_account_id
                    )
                )
            ).scalars()
        )
        self._require_for_account(principal, perm, ws, v.social_account_id, groups)

    async def create_b(
        self,
        user: AuthenticatedUser,
        ws: uuid.UUID,
        meta: RequestMeta,
        set_id: uuid.UUID,
        variant_id: uuid.UUID,
        *,
        hypothesis: str,
        body: str | None = None,
    ) -> PostSetView:
        hypothesis = " ".join(hypothesis.split())[:300]
        if not hypothesis:
            raise InvalidInput("Say what B is testing.", code="no_hypothesis", field="hypothesis")
        async with self._tx(user, ws, meta) as s:
            ps = await s.get(PostSet, set_id)
            v = await s.get(PostVariant, variant_id)
            if ps is None or v is None or v.post_set_id != set_id:
                raise NotFoundError("Post not found.")
            await self._check(s, user, ws, v, Permission.EDIT_POSTS)
            if ps.ab_label == "B":
                raise ConflictError("This is already a B version.", code="already_b")
            existing = await s.scalar(
                select(func.count())
                .select_from(PostSet)
                .join(PostVariant, PostVariant.post_set_id == PostSet.id)
                .where(
                    PostSet.ab_parent_id == set_id,
                    PostVariant.social_account_id == v.social_account_id,
                )
            )
            if existing:
                raise ConflictError("This account already has a B version.", code="ab_exists")
            version = (
                await s.get(PostVersion, v.current_version_id) if v.current_version_id else None
            )
            if version is None:
                raise ConflictError("Draft version A first.", code="nothing_to_test")
            brief = (
                f"{ps.topic_brief or ps.title}\n\n"
                f"This is version B of an A/B test. It tests: {hypothesis}. Keep the subject "
                "and facts; change the hook and angle so the two can be compared. Version A "
                f"was:\n{version.body}"
            )
            item, title, account = ps.content_item_id, ps.title, v.social_account_id
        view = await self._posts.create(
            user,
            ws,
            meta,
            content_item_id=item,
            topic_brief=brief[:4000],
            title=f"{title} (B)"[:200],
            selection=TargetSelection(social_account_ids=[account]),
            generate=body is None,
        )
        async with self._tx(user, ws, meta) as s:
            child = await s.get(PostSet, view.id, with_for_update=True)
            parent = await s.get(PostSet, set_id, with_for_update=True)
            assert child is not None
            assert parent is not None
            child.ab_parent_id, child.ab_label, child.ab_hypothesis = set_id, "B", hypothesis
            parent.ab_label = parent.ab_label or "A"
            for pv in (
                await s.execute(
                    select(PostVariant).where(PostVariant.post_set_id.in_([set_id, view.id]))
                )
            ).scalars():
                if pv.social_account_id == account:
                    pv.variant_label = "B" if pv.post_set_id == view.id else "A"
            b_variant = next(
                (
                    pv.id
                    for pv in (
                        await s.execute(
                            select(PostVariant).where(PostVariant.post_set_id == view.id)
                        )
                    ).scalars()
                ),
                None,
            )
        if body is not None and b_variant is not None:
            return await self._posts.create_version(
                user,
                ws,
                view.id,
                b_variant,
                meta,
                content=core.ContentInput(body, None, [], None, []),
                based_on_version_id=None,
            )
        return await self._posts.get(user, ws, view.id, meta)

    async def results(
        self, user: AuthenticatedUser, ws: uuid.UUID, meta: RequestMeta, set_id: uuid.UUID
    ) -> dict[str, Any]:
        async with self._tx(user, ws, meta) as s:
            ps = await s.get(PostSet, set_id)
            if ps is None:
                raise NotFoundError("Post not found.")
            parent_id = ps.ab_parent_id or ps.id
            parent = await s.get(PostSet, parent_id)
            children = list(
                (
                    await s.execute(select(PostSet).where(PostSet.ab_parent_id == parent_id))
                ).scalars()
            )
            if parent is None or not children:
                return {"min_impressions": MIN_IMPRESSIONS, "tests": []}  # none yet
            tests = []
            for child in children:
                b_var = (
                    (
                        await s.execute(
                            select(PostVariant).where(PostVariant.post_set_id == child.id)
                        )
                    )
                    .scalars()
                    .first()
                )
                if b_var is None:
                    continue
                a_var = (
                    await s.execute(
                        select(PostVariant).where(
                            PostVariant.post_set_id == parent_id,
                            PostVariant.social_account_id == b_var.social_account_id,
                        )
                    )
                ).scalar_one_or_none()
                if a_var is None:
                    continue
                await self._check(s, user, ws, a_var, Permission.READ)
                a = await self._numbers(s, a_var.id)
                b = await self._numbers(s, b_var.id)
                v = verdict(a["engagement"], a["impressions"], b["engagement"], b["impressions"])
                if not (a["published"] and b["published"]):
                    v = Verdict("too_early", None, None, None)
                tests.append(
                    {
                        "social_account_id": str(a_var.social_account_id),
                        "hypothesis": child.ab_hypothesis,
                        "a": {"post_set_id": str(parent_id), **a, "rate": v.a_rate},
                        "b": {"post_set_id": str(child.id), **b, "rate": v.b_rate},
                        "outcome": v.outcome,
                        "p_value": v.p_value,
                    }
                )
            return {"min_impressions": MIN_IMPRESSIONS, "tests": tests}

    @staticmethod
    async def _numbers(s: AsyncSession, variant_id: uuid.UUID) -> dict[str, Any]:
        post = (
            (
                await s.execute(
                    select(PublishedPost).where(
                        PublishedPost.post_variant_id == variant_id,
                        PublishedPost.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if post is None:
            return {"published": False, "engagement": 0.0, "impressions": None}
        latest: dict[str, float | None] = {}
        for key, value in (
            await s.execute(
                select(MetricSnapshot.metric_key, MetricSnapshot.value)
                .where(MetricSnapshot.published_post_id == post.id)
                .order_by(MetricSnapshot.captured_at)
            )
        ).all():
            latest[key] = float(value) if value is not None else None
        eng = sum(latest.get(k) or 0.0 for k in ENGAGEMENT)
        imp = latest.get("impressions")
        return {
            "published": True,
            "engagement": eng,
            "impressions": float(imp) if imp is not None else None,
        }
