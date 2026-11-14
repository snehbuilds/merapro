"""Evergreen recycling (P3-M5.3).

A published post set marked evergreen comes back after its cool-down as a *new* draft:
same subject and accounts, written fresh by the Writer from the previous text, origin
``recycled``. It is never re-posted as it was, and like every draft it publishes only
after approval. The original's ``recycle_after`` moves forward by the cool-down each time,
so one evergreen set makes at most one refresh per period.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import select

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, InvalidInput, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.post_service import PostService, TargetSelection
from mera.application.services.principals import acting_user, load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.infrastructure.db.models import PostSet, PostVariant, PostVersion, User, Workspace
from mera.infrastructure.db.session import system_scan_transaction, workspace_transaction

log = structlog.get_logger(__name__)
DEFAULT_COOLDOWN_DAYS: Final = 60


class EvergreenService(WorkspaceScopedService):
    def __init__(self, sm: Any, posts: PostService, *, mfa_enforced: bool = False) -> None:
        super().__init__(sm)
        self._posts = posts
        self._mfa = mfa_enforced

    async def mark(
        self,
        user: AuthenticatedUser,
        ws: uuid.UUID,
        meta: RequestMeta,
        set_id: uuid.UUID,
        *,
        evergreen: bool,
        cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
    ) -> dict[str, Any]:
        if not 7 <= cooldown_days <= 365:
            raise InvalidInput(
                "Between 7 and 365 days.", code="invalid_cooldown", field="cooldown_days"
            )
        async with self._tx(user, ws, meta) as s:
            principal = await load_user_principal(s, user, ws)
            rbac.require(principal, Permission.EDIT_POSTS, ws)
            ps = await s.get(PostSet, set_id, with_for_update=True)
            if ps is None:
                raise NotFoundError("Post not found.")
            if evergreen and ps.state not in ("published", "partially_published"):
                raise ConflictError("Only a published post can be evergreen.", code="not_published")
            ps.is_evergreen = evergreen
            ps.recycle_after = (
                datetime.now(UTC) + timedelta(days=cooldown_days) if evergreen else None
            )
            return {
                "is_evergreen": ps.is_evergreen,
                "recycle_after": ps.recycle_after.isoformat() if ps.recycle_after else None,
            }

    async def run(
        self, now: datetime | None = None, cooldown_days: int = DEFAULT_COOLDOWN_DAYS
    ) -> int:
        now = now or datetime.now(UTC)
        async with system_scan_transaction(self._sm) as s:
            spaces = list(
                (
                    await s.execute(
                        select(Workspace.id).where(
                            Workspace.deleted_at.is_(None), Workspace.archived_at.is_(None)
                        )
                    )
                ).scalars()
            )
        made = 0
        for ws in spaces:
            async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
                due = list(
                    (
                        await s.execute(
                            select(PostSet).where(
                                PostSet.is_evergreen.is_(True),
                                PostSet.recycle_after.is_not(None),
                                PostSet.recycle_after <= now,
                            )
                        )
                    ).scalars()
                )
                plans = []
                for ps in due:
                    v = (
                        (
                            await s.execute(
                                select(PostVariant).where(
                                    PostVariant.post_set_id == ps.id,
                                    PostVariant.current_version_id.is_not(None),
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    ver = (
                        await s.get(PostVersion, v.current_version_id)
                        if v and v.current_version_id
                        else None
                    )
                    owner = (
                        await s.get(User, ps.created_by_user_id) if ps.created_by_user_id else None
                    )
                    ps.recycle_after = now + timedelta(days=cooldown_days)
                    if ver is None or owner is None or owner.deleted_at is not None:
                        continue
                    plans.append(
                        (
                            ps.id,
                            ps.title,
                            ps.content_item_id,
                            ps.topic_brief,
                            ver.body,
                            list(ps.target_account_group_ids),
                            list(ps.target_social_account_ids),
                            owner,
                        )
                    )
            for set_id, title, item, brief, body, groups, accounts, owner in plans:
                who = acting_user(owner, mfa_enforced=self._mfa)
                meta = RequestMeta(surface="worker")
                try:
                    view = await self._posts.create(
                        who,
                        ws,
                        meta,
                        content_item_id=item,
                        topic_brief=(
                            f"{brief or title}\n\nRefresh of an evergreen post. Keep the message "
                            "and the facts; write it fresh for today. The previous version "
                            f"was:\n{body}"
                        )[:4000],
                        title=f"{title} (refresh)"[:200],
                        selection=TargetSelection(
                            account_group_ids=groups, social_account_ids=accounts
                        ),
                        generate=True,
                    )
                except Exception as exc:  # one set must not stop the others
                    log.warning(
                        "evergreen.failed", post_set_id=str(set_id), error=type(exc).__name__
                    )
                    continue
                async with workspace_transaction(self._sm, WorkspaceContext(ws)) as s:
                    new = await s.get(PostSet, view.id, with_for_update=True)
                    if new is not None:
                        new.origin = "recycled"
                made += 1
        return made
