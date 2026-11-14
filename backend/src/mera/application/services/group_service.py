"""Account groups and ordered membership (M3.4, 3.5, 3.6, 3.9; docs/03 §4, ADR-0012).

Membership is a join table with a ``position``: the publish order, which the stagger
sequence follows. Deleting a group never touches its accounts.

``stagger_required`` (docs/08 §3.3) is enforced at configuration time: a group whose
stagger mode is ``none`` cannot hold two active accounts on one platform, whichever change
would create that state — switching the mode or adding the account. The publish-time
guardrail (M8.7) checks again, because configuration is not the only path to a post.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application import autopilot
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, InvalidInput, NotFoundError
from mera.application.services.account_service import _in_scope, _levels
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.application.settings_model import LevelSettings, merged
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.policies.settings_resolution import Resolved, resolve_all
from mera.domain.principal import Principal
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.platform import AccountStatus, Platform, StaggerMode
from mera.infrastructure.db.errors import constraint_name
from mera.infrastructure.db.models import (
    AccountGroup,
    AccountGroupMember,
    SocialAccount,
    Workspace,
)
from mera.infrastructure.db.repositories.audit import record
from mera.infrastructure.platforms.capabilities import capabilities_for

STAGGER_BOUNDS = (0, 6 * 3600)


@dataclass(frozen=True, slots=True)
class MemberView:
    social_account_id: uuid.UUID
    platform: str
    nickname: str
    handle: str
    status: str
    position: int
    enabled_by_default: bool


@dataclass(frozen=True, slots=True)
class GroupView:
    id: uuid.UUID
    name: str
    description: str | None
    color: str | None
    default_brand_voice_id: uuid.UUID | None
    posting_schedule: list[dict[str, Any]] | None
    content_pillar_ids: list[uuid.UUID]
    stagger_mode: str
    stagger_min_seconds: int
    stagger_max_seconds: int
    enabled: bool
    settings: dict[str, Any]
    member_count: int
    members: list[MemberView]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GroupChanges:
    name: str | None = None
    description: str | None = None
    color: str | None = None
    posting_schedule: list[dict[str, Any]] | None = None
    clear_posting_schedule: bool = False
    stagger_mode: StaggerMode | None = None
    stagger_min_seconds: int | None = None
    stagger_max_seconds: int | None = None
    enabled: bool | None = None
    settings: LevelSettings | None = None


class GroupService(WorkspaceScopedService):
    async def list_groups(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[GroupView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            groups = list(
                (await s.execute(select(AccountGroup).order_by(AccountGroup.name))).scalars()
            )
            visible = [g for g in groups if _group_in_scope(principal, g.id)]
            return [await _group_view(s, g, principal) for g in visible]

    async def create(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        name: str,
        description: str | None,
        color: str | None,
        stagger_mode: StaggerMode,
        stagger_min_seconds: int,
        stagger_max_seconds: int,
        social_account_ids: list[uuid.UUID],
    ) -> GroupView:
        _check_bounds(stagger_min_seconds, stagger_max_seconds)
        try:
            async with self._tx(user, workspace_id, meta) as s:
                principal = await load_user_principal(s, user, workspace_id)
                # A new group has no subject yet, so a group-scoped editor is refused here
                # (fail closed: creating it would act outside their groups).
                rbac.require(principal, Permission.MANAGE_GROUPS, workspace_id)
                await self._writable(s, workspace_id)
                group = AccountGroup(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    name=name.strip(),
                    description=description,
                    color=color,
                    stagger_mode=stagger_mode.value,
                    stagger_min_seconds=stagger_min_seconds,
                    stagger_max_seconds=stagger_max_seconds,
                )
                s.add(group)
                await s.flush()
                await self._set_members(s, principal, workspace_id, group, social_account_ids)
                await _check_stagger(s, group)
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "group.created",
                        workspace_id=workspace_id,
                        subject_type="account_group",
                        subject_id=group.id,
                        account_group_id=group.id,
                        after={"name": group.name, "members": [str(a) for a in social_account_ids]},
                    ),
                )
                return await _group_view(s, group, principal)
        except IntegrityError as exc:
            if constraint_name(exc) != "uq_account_group_name":
                raise
            raise ConflictError(
                "A group with that name exists.", code="group_name_taken", field="name"
            ) from exc

    async def get(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        group_id: uuid.UUID,
        meta: RequestMeta,
    ) -> GroupView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            group = await _group(s, group_id)
            rbac.require(principal, Permission.READ, workspace_id, account_group_id=group_id)
            return await _group_view(s, group, principal)

    async def update(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        group_id: uuid.UUID,
        meta: RequestMeta,
        changes: GroupChanges,
    ) -> GroupView:
        try:
            async with self._tx(user, workspace_id, meta) as s:
                principal = await load_user_principal(s, user, workspace_id)
                group = await _group(s, group_id, lock=True)
                rbac.require(
                    principal, Permission.MANAGE_GROUPS, workspace_id, account_group_id=group_id
                )
                await self._writable(s, workspace_id)
                before = _snapshot(group)
                if changes.name is not None:
                    group.name = changes.name.strip()
                if changes.description is not None:
                    group.description = changes.description
                if changes.color is not None:
                    group.color = changes.color
                if changes.clear_posting_schedule:
                    group.posting_schedule = None
                elif changes.posting_schedule is not None:
                    group.posting_schedule = changes.posting_schedule
                if changes.stagger_mode is not None:
                    group.stagger_mode = changes.stagger_mode.value
                if changes.stagger_min_seconds is not None:
                    group.stagger_min_seconds = changes.stagger_min_seconds
                if changes.stagger_max_seconds is not None:
                    group.stagger_max_seconds = changes.stagger_max_seconds
                if changes.enabled is not None:
                    group.enabled = changes.enabled
                if changes.settings is not None:
                    new_settings = merged(group.settings, changes.settings)
                    await autopilot.guard_change(
                        s,
                        user,
                        principal,
                        workspace_id,
                        meta,
                        before=group.settings,
                        after=new_settings,
                        scope_type="account_group",
                        scope_id=group.id,
                        accounts=await autopilot.scope_accounts(s, "account_group", group.id),
                    )
                    group.settings = new_settings
                _check_bounds(group.stagger_min_seconds, group.stagger_max_seconds)
                await _check_stagger(s, group)
                after = _snapshot(group)
                if after != before:
                    await s.flush()
                    await record(
                        s,
                        self._actor_entry(
                            user,
                            meta,
                            "group.updated",
                            workspace_id=workspace_id,
                            subject_type="account_group",
                            subject_id=group_id,
                            account_group_id=group_id,
                            before=before,
                            after=after,
                        ),
                    )
                return await _group_view(s, group, principal)
        except IntegrityError as exc:
            if constraint_name(exc) != "uq_account_group_name":
                raise
            raise ConflictError(
                "A group with that name exists.", code="group_name_taken", field="name"
            ) from exc

    async def delete(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        group_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        """Only the grouping goes; every account in it stays connected (M3 acceptance)."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            group = await _group(s, group_id, lock=True)
            rbac.require(
                principal, Permission.MANAGE_GROUPS, workspace_id, account_group_id=group_id
            )
            await _require_admin_if_governed(s, principal, workspace_id, group_id)
            await self._writable(s, workspace_id)
            await s.delete(group)
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "group.deleted",
                    workspace_id=workspace_id,
                    subject_type="account_group",
                    subject_id=group_id,
                    account_group_id=group_id,
                    before={"name": group.name},
                ),
            )

    async def set_members(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        group_id: uuid.UUID,
        meta: RequestMeta,
        *,
        social_account_ids: list[uuid.UUID],
        replace: bool,
    ) -> GroupView:
        """``replace`` sets membership and order wholesale (PUT); otherwise the accounts are
        appended in the order given (POST)."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            group = await _group(s, group_id, lock=True)
            rbac.require(
                principal, Permission.MANAGE_GROUPS, workspace_id, account_group_id=group_id
            )
            await self._writable(s, workspace_id)
            before = await _member_ids(s, group_id)
            ids = (
                social_account_ids
                if replace
                else [*before, *[a for a in social_account_ids if a not in before]]
            )
            if set(before) - set(ids):
                await _require_admin_if_governed(s, principal, workspace_id, group_id)
            await self._set_members(s, principal, workspace_id, group, ids)
            await _check_stagger(s, group)
            after = await _member_ids(s, group_id)
            if after != before:
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "group.membership_changed",
                        workspace_id=workspace_id,
                        subject_type="account_group",
                        subject_id=group_id,
                        account_group_id=group_id,
                        before={"members": [str(a) for a in before]},
                        after={"members": [str(a) for a in after]},
                    ),
                )
            return await _group_view(s, group, principal)

    async def remove_member(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        group_id: uuid.UUID,
        account_id: uuid.UUID,
        meta: RequestMeta,
    ) -> GroupView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            group = await _group(s, group_id, lock=True)
            rbac.require(
                principal, Permission.MANAGE_GROUPS, workspace_id, account_group_id=group_id
            )
            await self._writable(s, workspace_id)
            ids = await _member_ids(s, group_id)
            if account_id not in ids:
                raise NotFoundError("That account is not in this group.")
            await _require_admin_if_governed(s, principal, workspace_id, group_id)
            await self._set_members(
                s, principal, workspace_id, group, [a for a in ids if a != account_id]
            )
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "group.membership_changed",
                    workspace_id=workspace_id,
                    subject_type="account_group",
                    subject_id=group_id,
                    account_group_id=group_id,
                    social_account_id=account_id,
                    after={"removed": str(account_id)},
                ),
            )
            return await _group_view(s, group, principal)

    async def update_member(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        group_id: uuid.UUID,
        account_id: uuid.UUID,
        meta: RequestMeta,
        *,
        position: int | None,
        enabled_by_default: bool | None,
    ) -> GroupView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            group = await _group(s, group_id, lock=True)
            rbac.require(
                principal, Permission.MANAGE_GROUPS, workspace_id, account_group_id=group_id
            )
            await self._writable(s, workspace_id)
            member = (
                await s.execute(
                    select(AccountGroupMember).where(
                        AccountGroupMember.account_group_id == group_id,
                        AccountGroupMember.social_account_id == account_id,
                    )
                )
            ).scalar_one_or_none()
            if member is None:
                raise NotFoundError("That account is not in this group.")
            if enabled_by_default is not None:
                member.enabled_by_default = enabled_by_default
            if position is not None:
                ids = [a for a in await _member_ids(s, group_id) if a != account_id]
                ids.insert(max(0, min(position, len(ids))), account_id)
                await _renumber(s, group_id, ids)
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "group.member_updated",
                    workspace_id=workspace_id,
                    subject_type="account_group",
                    subject_id=group_id,
                    account_group_id=group_id,
                    social_account_id=account_id,
                    after={"position": position, "enabled_by_default": enabled_by_default},
                ),
            )
            return await _group_view(s, group, principal)

    async def resolved_settings(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        group_id: uuid.UUID,
        meta: RequestMeta,
    ) -> list[Resolved]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            group = await _group(s, group_id)
            rbac.require(principal, Permission.READ, workspace_id, account_group_id=group_id)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            return resolve_all(_levels(ws, group, None))

    # ------------------------------------------------------------------ helpers

    async def _writable(self, s: AsyncSession, workspace_id: uuid.UUID) -> None:
        ws = await s.get(Workspace, workspace_id)
        assert ws is not None
        self._ensure_writable(ws)

    async def _set_members(
        self,
        s: AsyncSession,
        principal: Principal,
        workspace_id: uuid.UUID,
        group: AccountGroup,
        account_ids: list[uuid.UUID],
    ) -> None:
        if len(set(account_ids)) != len(account_ids):
            raise InvalidInput(
                "Each account can appear once.",
                code="duplicate_account",
                field="social_account_ids",
            )
        current = set(await _member_ids(s, group.id))
        found = {
            a.id: a
            for a in (
                await s.execute(select(SocialAccount).where(SocialAccount.id.in_(account_ids)))
            ).scalars()
        }
        missing = [a for a in account_ids if a not in found]
        if missing:
            raise InvalidInput(
                "Unknown account.", code="unknown_account", field="social_account_ids"
            )
        # Adding an account to a group is acting on that account: a scoped editor may only
        # add accounts already within their scope.
        for account_id in account_ids:
            if account_id not in current and not _in_scope(
                principal, account_id, await _account_groups(s, account_id)
            ):
                rbac.require(
                    principal, Permission.MANAGE_GROUPS, workspace_id, social_account_id=account_id
                )
        await s.execute(
            delete(AccountGroupMember).where(
                AccountGroupMember.account_group_id == group.id,
                AccountGroupMember.social_account_id.not_in(account_ids) if account_ids else true(),
            )
        )
        existing = {
            m.social_account_id: m
            for m in (
                await s.execute(
                    select(AccountGroupMember).where(
                        AccountGroupMember.account_group_id == group.id
                    )
                )
            ).scalars()
        }
        for account_id in account_ids:
            if account_id not in existing:
                s.add(
                    AccountGroupMember(
                        id=uuid7(),
                        workspace_id=workspace_id,
                        account_group_id=group.id,
                        social_account_id=account_id,
                        position=len(existing) + 1000,  # renumbered below
                    )
                )
        await s.flush()
        await _renumber(s, group.id, account_ids)


def _group_in_scope(principal: Principal, group_id: uuid.UUID) -> bool:
    scope = principal.member_scope
    return not scope.restricted or group_id in scope.group_ids


def _check_bounds(lo: int, hi: int) -> None:
    if not (STAGGER_BOUNDS[0] <= lo <= hi <= STAGGER_BOUNDS[1]):
        raise InvalidInput(
            "Stagger bounds must satisfy 0 ≤ minimum ≤ maximum ≤ 6 hours.",
            code="invalid_stagger_bounds",
            field="stagger_min_seconds",
        )


async def _check_stagger(s: AsyncSession, group: AccountGroup) -> None:
    if group.stagger_mode != StaggerMode.NONE.value:
        return
    rows = (
        await s.execute(
            select(SocialAccount.platform)
            .join(AccountGroupMember, AccountGroupMember.social_account_id == SocialAccount.id)
            .where(
                AccountGroupMember.account_group_id == group.id,
                SocialAccount.status != AccountStatus.DISCONNECTED.value,
            )
        )
    ).scalars()
    for platform, count in Counter(rows).items():
        caps = capabilities_for(Platform(platform))
        if count >= 2 and caps.requires_stagger_within_platform:
            raise InvalidInput(
                f"This group has {count} {caps.display_name} accounts, so posts must be "
                "staggered. Choose a stagger mode other than 'all at once'.",
                code="stagger_required",
                field="stagger_mode",
            )


async def check_stagger_for_account(s: AsyncSession, account_id: uuid.UUID) -> None:
    """Run the stagger_required check for every group the account belongs to."""
    for group_id in await _account_groups(s, account_id):
        group = await _group(s, group_id, lock=True)
        await _check_stagger(s, group)


async def _group(s: AsyncSession, group_id: uuid.UUID, *, lock: bool = False) -> AccountGroup:
    stmt = select(AccountGroup).where(AccountGroup.id == group_id)
    if lock:
        stmt = stmt.with_for_update()
    group = (await s.execute(stmt)).scalar_one_or_none()
    if group is None:
        raise NotFoundError("Group not found.")
    return group


async def _member_ids(s: AsyncSession, group_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await s.execute(
                select(AccountGroupMember.social_account_id)
                .where(AccountGroupMember.account_group_id == group_id)
                .order_by(AccountGroupMember.position)
            )
        ).scalars()
    )


async def _account_groups(s: AsyncSession, account_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await s.execute(
                select(AccountGroupMember.account_group_id).where(
                    AccountGroupMember.social_account_id == account_id
                )
            )
        ).scalars()
    )


async def _renumber(s: AsyncSession, group_id: uuid.UUID, ordered: list[uuid.UUID]) -> None:
    members = {
        m.social_account_id: m
        for m in (
            await s.execute(
                select(AccountGroupMember).where(AccountGroupMember.account_group_id == group_id)
            )
        ).scalars()
    }
    for position, account_id in enumerate(ordered):
        members[account_id].position = position
    await s.flush()


async def _group_view(s: AsyncSession, g: AccountGroup, principal: Principal) -> GroupView:
    rows = (
        await s.execute(
            select(AccountGroupMember, SocialAccount)
            .join(SocialAccount, SocialAccount.id == AccountGroupMember.social_account_id)
            .where(AccountGroupMember.account_group_id == g.id)
            .order_by(AccountGroupMember.position)
        )
    ).all()
    members = [
        MemberView(
            a.id, a.platform, a.nickname, a.handle, a.status, m.position, m.enabled_by_default
        )
        for m, a in rows
    ]
    count = (
        await s.execute(
            select(func.count())
            .select_from(AccountGroupMember)
            .where(AccountGroupMember.account_group_id == g.id)
        )
    ).scalar_one()
    return GroupView(
        id=g.id,
        name=g.name,
        description=g.description,
        color=g.color,
        default_brand_voice_id=g.default_brand_voice_id,
        posting_schedule=g.posting_schedule,
        content_pillar_ids=list(g.content_pillar_ids),
        stagger_mode=g.stagger_mode,
        stagger_min_seconds=g.stagger_min_seconds,
        stagger_max_seconds=g.stagger_max_seconds,
        enabled=g.enabled,
        settings=dict(g.settings),
        member_count=int(count),
        members=members,
        created_at=g.created_at,
    )


def _snapshot(g: AccountGroup) -> dict[str, Any]:
    return {
        "name": g.name,
        "description": g.description,
        "color": g.color,
        "posting_schedule": g.posting_schedule,
        "stagger_mode": g.stagger_mode,
        "stagger_min_seconds": g.stagger_min_seconds,
        "stagger_max_seconds": g.stagger_max_seconds,
        "enabled": g.enabled,
        "settings": g.settings,
    }


async def _require_admin_if_governed(
    s: AsyncSession, principal: Principal, workspace_id: uuid.UUID, group_id: uuid.UUID
) -> None:
    """A group carrying an approval policy or a kill switch protects its accounts. Taking
    an account out of it (or deleting it) would quietly drop that protection, so it is an
    Admin's call, like editing the policy itself (security review S8-H1)."""
    from mera.infrastructure.db.models import ApprovalPolicy, AutomationState

    governed = await s.scalar(
        select(ApprovalPolicy.id).where(
            ApprovalPolicy.scope_type == "account_group", ApprovalPolicy.scope_id == group_id
        )
    ) or await s.scalar(
        select(AutomationState.id).where(
            AutomationState.scope_type == "account_group",
            AutomationState.scope_id == group_id,
            AutomationState.kill_switch_engaged.is_(True),
        )
    )
    if governed:
        rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
