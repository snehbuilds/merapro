"""Assisted and Full Autopilot (P3-M3, docs/14 §2.3 and §2.4, ADR-0017).

**Resolution.** The mode for an account is the strictest set at the workspace, any group
the account belongs to, and the account (``manual`` < ``assisted`` < ``full``, manual being
strictest). Unset levels do not count; nothing set means manual. A company or organization
account stays manual unless its *own* setting opts it in: a group on Full never carries a
Company Page with it.

**Turning it on** (``guard_change``): Admins only, Owners for ``full`` and for a company
account, with the ``autopilot_enable`` second factor, and only where it has been earned:
``EARN_MIN_APPROVALS`` human approvals on the scope's accounts with no more than
``EARN_MAX_EDIT_RATE`` of them edited by a person first. ``assisted`` also needs the
Editor's confidence to predict approval on those accounts (P3-M1.6 calibration
``separates``). Turning it down is always allowed: stopping is safe. Every change writes
``autopilot.changed``, which alerts the workspace's Owners (P3-M7.1).

**At submission** (``authorize``): ``full`` publishes unattended when the version has no
Block-level flag; ``assisted`` also needs no Warn-level flag and a confidence at or above
the threshold (``assisted_threshold``, strictest = highest, default 0.85). A kill switch
anywhere in scope means a person. The authorization is a row bound to the variant, version
and content hash, and the approval it stands for is a ``system`` decision.

**Before publishing** (``still_permitted``, ``assert_publishable`` check 12): the
authorization must not be revoked and the account's mode now must still allow it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application import second_factor
from mera.application.approvals import core as approvals
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, Forbidden
from mera.domain.policies.calibration import calibrate
from mera.domain.principal import Principal
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.role import Role
from mera.infrastructure.db.models import (
    AccountGroup,
    AccountGroupMember,
    ApprovalDecision,
    ApprovalVariantState,
    AutopilotAuthorization,
    PostVersion,
    SecurityEvent,
    SocialAccount,
    Workspace,
)

STRICTNESS: Final = {"manual": 0, "assisted": 1, "full": 2}
EARN_MIN_APPROVALS: Final = 30  # docs/14 Q14 (proposed)
EARN_MAX_EDIT_RATE: Final = 0.10
EARN_WINDOW: Final = timedelta(days=90)
DEFAULT_THRESHOLD: Final = 0.85


def _mode(settings: dict[str, Any] | None) -> str | None:
    value = (settings or {}).get("autopilot_mode")
    return value if value in STRICTNESS else None


async def account_mode(s: AsyncSession, account: SocialAccount) -> str:
    ws = await s.get(Workspace, account.workspace_id)
    groups = list(
        (
            await s.execute(
                select(AccountGroup.settings)
                .join(AccountGroupMember, AccountGroupMember.account_group_id == AccountGroup.id)
                .where(AccountGroupMember.social_account_id == account.id)
            )
        ).scalars()
    )
    return resolve(
        _mode(ws.settings if ws else None),
        [_mode(g) for g in groups],
        _mode(account.settings),
        company=account.account_type in second_factor.COMPANY_ACCOUNT_TYPES,
    )


def resolve(
    workspace: str | None, groups: list[str | None], own: str | None, *, company: bool
) -> str:
    """The strictest mode set at any level; unset levels do not count; nothing set is
    manual; a company account is manual unless its own setting opts it in. Pure."""
    if company and own in (None, "manual"):
        return "manual"
    levels = [m for m in [workspace, *groups, own] if m]
    if not levels:
        return "manual"
    return min(levels, key=lambda m: STRICTNESS[m])


async def threshold(s: AsyncSession, account: SocialAccount) -> float:
    ws = await s.get(Workspace, account.workspace_id)
    groups = list(
        (
            await s.execute(
                select(AccountGroup.settings)
                .join(AccountGroupMember, AccountGroupMember.account_group_id == AccountGroup.id)
                .where(AccountGroupMember.social_account_id == account.id)
            )
        ).scalars()
    )
    values = [
        float(v)
        for st in [ws.settings if ws else None, *groups, account.settings]
        if (v := (st or {}).get("assisted_threshold")) is not None
    ]
    return max([DEFAULT_THRESHOLD, *values])


# --------------------------------------------------------------------- earned


async def earned(
    s: AsyncSession, account_ids: list[uuid.UUID], *, need_calibration: bool
) -> tuple[bool, str]:
    """Whether these accounts have earned autopilot, and why not if they have not."""
    if not account_ids:
        return False, "There are no accounts in this scope yet."
    since = datetime.now(UTC) - EARN_WINDOW
    rows = (
        await s.execute(
            select(ApprovalVariantState.state, PostVersion.source, PostVersion.confidence_score)
            .join(PostVersion, PostVersion.id == ApprovalVariantState.post_version_id)
            .where(
                ApprovalVariantState.social_account_id.in_(account_ids),
                ApprovalVariantState.decided_at >= since,
                ApprovalVariantState.state.in_(("approved", "rejected", "expired", "superseded")),
                ~ApprovalVariantState.id.in_(
                    select(ApprovalVariantState.id).join(
                        AutopilotAuthorization,
                        (
                            AutopilotAuthorization.approval_request_id
                            == ApprovalVariantState.approval_request_id
                        )
                        & (
                            AutopilotAuthorization.post_variant_id
                            == ApprovalVariantState.post_variant_id
                        ),
                    )
                ),
            )
        )
    ).all()
    approved = [(src, conf) for state, src, conf in rows if state == "approved"]
    if len(approved) < EARN_MIN_APPROVALS:
        return False, (
            f"Autopilot is earned after {EARN_MIN_APPROVALS} approvals by people here; "
            f"there have been {len(approved)} in the last 90 days."
        )
    edited = sum(1 for src, _ in approved if src == "human")
    if approved and edited / len(approved) > EARN_MAX_EDIT_RATE:
        return False, (
            f"{edited} of the last {len(approved)} approved posts were edited by a person "
            f"first; autopilot needs that under {int(EARN_MAX_EDIT_RATE * 100)}%."
        )
    if need_calibration:
        outcomes = [
            (float(conf), state == "approved" and src == "agent")
            for state, src, conf in rows
            if conf is not None and src == "agent"
        ]
        if calibrate(outcomes).separates is not True:
            return False, (
                "The Editor's confidence does not yet predict what gets approved here, so "
                "Assisted mode cannot threshold on it."
            )
    return True, ""


async def guard_change(
    s: AsyncSession,
    user: AuthenticatedUser,
    principal: Principal,
    workspace_id: uuid.UUID,
    meta: RequestMeta,
    *,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    scope_type: str,
    scope_id: uuid.UUID | None,
    accounts: list[SocialAccount],
) -> None:
    """Called inside the settings write's transaction, before it commits.

    Guarded on the **effective** mode of the accounts in scope, not the value at this
    level: clearing a stricter level (an explicit workspace ``manual``, say) can release
    a group's Full, and that is as much a raise as setting Full (security review #1)."""
    raw_old, raw_new = _mode(before), _mode(after)
    if raw_old == raw_new:
        return
    old = await _effective(s, accounts, scope_type, scope_id, before)
    new = await _effective(s, accounts, scope_type, scope_id, after)
    if STRICTNESS[new] > STRICTNESS[old]:
        company = any(a.account_type in second_factor.COMPANY_ACCOUNT_TYPES for a in accounts)
        needs_owner = new == "full" or (scope_type == "social_account" and company)
        rank = principal.role.rank if principal.role else -1
        if rank < (Role.OWNER if needs_owner else Role.ADMIN).rank:
            raise Forbidden(
                "Only an Owner can turn on Full Autopilot or autopilot for a company account."
                if needs_owner
                else "Only an Admin can turn on autopilot.",
                code="autopilot_forbidden",
            )
        ok, why = await earned(s, [a.id for a in accounts], need_calibration=new == "assisted")
        if not ok:
            raise ConflictError(why, code="autopilot_not_earned")
        await second_factor.require(s, user, workspace_id, "autopilot_enable", meta)
    s.add(
        SecurityEvent(
            user_id=user.user_id,
            workspace_id=workspace_id,
            kind="autopilot.changed",
            detail={
                "scope": scope_type,
                "id": str(scope_id) if scope_id else None,
                "from": raw_old or "inherit",
                "to": raw_new or "inherit",
                "effective_from": old,
                "effective_to": new,
            },
            ip_address=meta.ip_address,
        )
    )


async def _effective(
    s: AsyncSession,
    accounts: list[SocialAccount],
    scope_type: str,
    scope_id: uuid.UUID | None,
    settings: dict[str, Any] | None,
) -> str:
    """The most permissive effective mode among ``accounts`` if this scope's settings
    were ``settings``."""
    if not accounts:
        return "manual"
    ws = await s.get(Workspace, accounts[0].workspace_id)
    ws_mode = _mode(settings) if scope_type == "workspace" else _mode(ws.settings if ws else None)
    best = "manual"
    for acc in accounts:
        rows = (
            await s.execute(
                select(AccountGroup.id, AccountGroup.settings)
                .join(AccountGroupMember, AccountGroupMember.account_group_id == AccountGroup.id)
                .where(AccountGroupMember.social_account_id == acc.id)
            )
        ).all()
        groups = [
            _mode(settings) if scope_type == "account_group" and gid == scope_id else _mode(g)
            for gid, g in rows
        ]
        own = (
            _mode(settings)
            if scope_type == "social_account" and acc.id == scope_id
            else _mode(acc.settings)
        )
        m = resolve(
            ws_mode,
            groups,
            own,
            company=acc.account_type in second_factor.COMPANY_ACCOUNT_TYPES,
        )
        if STRICTNESS[m] > STRICTNESS[best]:
            best = m
    return best


async def scope_accounts(
    s: AsyncSession, scope_type: str, scope_id: uuid.UUID | None
) -> list[SocialAccount]:
    if scope_type == "social_account" and scope_id:
        acc = await s.get(SocialAccount, scope_id)
        return [acc] if acc else []
    q = select(SocialAccount)
    if scope_type == "account_group" and scope_id:
        q = q.join(
            AccountGroupMember, AccountGroupMember.social_account_id == SocialAccount.id
        ).where(AccountGroupMember.account_group_id == scope_id)
    return list((await s.execute(q)).scalars())


# ----------------------------------------------------------------- submission


def _flags(version: PostVersion, severity: str) -> list[dict[str, Any]]:
    return [
        f
        for f in [*(version.compliance_flags or []), *(version.validation or [])]
        if isinstance(f, dict) and f.get("severity") == severity
    ]


async def authorize(
    s: AsyncSession,
    req: Any,
    row: ApprovalVariantState,
    variant: Any,
    version: PostVersion,
    now: datetime,
) -> AutopilotAuthorization | None:
    """The authorization for one account of a new request, or None: a person decides."""
    account = await s.get(SocialAccount, row.social_account_id)
    if account is None:
        return None
    mode = await account_mode(s, account)
    if mode == "manual":
        return None
    # Earned per account, at every submission: an account connected or added to a group
    # after the mode was turned on has to earn it too (security review #2).
    ok, _why = await earned(s, [account.id], need_calibration=mode == "assisted")
    if not ok:
        return None
    extra = [g for g in [variant.via_account_group_id] if g] + approvals.snapshot_groups(
        req, variant.id
    )
    if await approvals.kill_switch_scope(s, [account.id], extra):
        return None
    if _flags(version, "block"):
        return None
    confidence = version.confidence_score
    reasons: dict[str, Any] = {"mode": mode}
    if mode == "assisted":
        limit = await threshold(s, account)
        reasons["threshold"] = limit
        if _flags(version, "warning") or _flags(version, "warn"):
            return None
        if confidence is None or confidence < limit:
            return None
    auth = AutopilotAuthorization(
        id=uuid7(),
        workspace_id=row.workspace_id,
        approval_request_id=row.approval_request_id,
        post_variant_id=row.post_variant_id,
        post_version_id=row.post_version_id,
        social_account_id=row.social_account_id,
        content_hash=row.content_hash,
        mode=mode,
        confidence=confidence,
        reasons=reasons,
    )
    s.add(auth)
    return auth


async def live_authorization(
    s: AsyncSession, state: ApprovalVariantState
) -> AutopilotAuthorization | None:
    return (
        await s.execute(
            select(AutopilotAuthorization).where(
                AutopilotAuthorization.approval_request_id == state.approval_request_id,
                AutopilotAuthorization.post_variant_id == state.post_variant_id,
            )
        )
    ).scalar_one_or_none()


async def still_permitted(s: AsyncSession, auth: AutopilotAuthorization) -> str | None:
    """None if the unattended publish may go ahead, else why not."""
    if auth.revoked_at is not None:
        return "Autopilot was withdrawn for this post."
    account = await s.get(SocialAccount, auth.social_account_id)
    if account is None:
        return "The account is gone."
    now_mode = await account_mode(s, account)
    if STRICTNESS[now_mode] < STRICTNESS[auth.mode]:
        return f"Autopilot for this account is now {now_mode}."
    return None


async def human_decisions(s: AsyncSession, request_id: uuid.UUID) -> int:
    return int(
        await s.scalar(
            select(func.count())
            .select_from(ApprovalDecision)
            .where(
                ApprovalDecision.approval_request_id == request_id,
                ApprovalDecision.channel_type != "system",
            )
        )
        or 0
    )
