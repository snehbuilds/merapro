"""Approval mechanics shared by the service, the drafting core and publishing (M6).

Everything here runs inside a caller's workspace transaction. Under ``lock_request`` the
request row is ``FOR UPDATE``, which serialises every decision on one post set (docs/07
§7.3): two approvers tapping at once is the normal case.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mera.domain.state_machines.approval import (
    PolicyRule,
    Quorum,
    RequestState,
    VariantApproval,
    VariantTally,
    derive,
    end,
    is_open,
)
from mera.domain.value_objects.posts import PostSetState, VariantState
from mera.infrastructure.db.models import (
    AccountGroup,
    AccountGroupMember,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalToken,
    ApprovalVariantState,
    AutomationState,
    PostSet,
    PostVariant,
    SocialAccount,
    Workspace,
    WorkspaceMember,
)

# Variant states the approval machinery owns while a request is open or decided.
APPROVAL_VARIANT_STATES = frozenset(
    {VariantState.NEEDS_APPROVAL.value, VariantState.APPROVED.value, VariantState.REJECTED.value}
)
# Variant states publishing owns (M8): approval bookkeeping never moves a variant back.
PUBLISHING_VARIANT_STATES = frozenset(
    {
        VariantState.SCHEDULED.value,
        VariantState.PUBLISHING.value,
        VariantState.PUBLISHED.value,
        VariantState.FAILED.value,
    }
)


def _minutes(td: Any) -> int:
    return int(td.total_seconds() // 60)


async def account_groups(s: AsyncSession, account_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await s.execute(
                select(AccountGroupMember.account_group_id).where(
                    AccountGroupMember.social_account_id == account_id
                )
            )
        ).scalars()
    )


async def policy_rules(s: AsyncSession, variant: PostVariant) -> list[PolicyRule]:
    """Every rule that governs this variant: the workspace policy, a policy on **any** group
    the account belongs to (strictest-wins, so membership in a permissive group can never
    weaken a stricter one), the account's own policy, and the ``required_approvers`` level
    setting at each of those levels."""
    groups = await account_groups(s, variant.social_account_id)
    if variant.via_account_group_id and variant.via_account_group_id not in groups:
        groups.append(variant.via_account_group_id)
    rows = (
        await s.execute(
            select(ApprovalPolicy).where(
                or_(
                    ApprovalPolicy.scope_type == "workspace",
                    (ApprovalPolicy.scope_type == "account_group")
                    & ApprovalPolicy.scope_id.in_(groups or [uuid.UUID(int=0)]),
                    (ApprovalPolicy.scope_type == "social_account")
                    & (ApprovalPolicy.scope_id == variant.social_account_id),
                )
            )
        )
    ).scalars()
    cover = await _away_cover(s)
    rules = [
        PolicyRule(
            level=f"{p.scope_type}:{p.scope_id}" if p.scope_id else "workspace",
            required=p.required_approver_count,
            quorum=Quorum(p.quorum_mode),
            # An approver who is away is covered by their delegate (P2-M2.8).
            approvers=frozenset(cover.get(str(u), str(u)) for u in p.approver_user_ids),
            remind_minutes=_minutes(p.remind_after),
            escalate_minutes=_minutes(p.escalate_after),
            expires_minutes=_minutes(p.expires_after),
            validity_minutes=_minutes(p.approval_validity_window),
            escalate_to=frozenset(
                cover.get(str(u), str(u)) for u in (p.escalate_to_user_ids or [])
            ),
        )
        for p in rows
        if "post_set" in p.subject_types
    ]
    # The level settings' required_approvers (M3) count too, strictest-wins with the above.
    settings_levels: list[tuple[str, dict[str, Any] | None]] = []
    ws = await s.get(Workspace, variant.workspace_id)
    settings_levels.append(("settings:workspace", ws.settings if ws else None))
    for gid in groups:
        g = await s.get(AccountGroup, gid)
        settings_levels.append((f"settings:account_group:{gid}", g.settings if g else None))
    acct = await s.get(SocialAccount, variant.social_account_id)
    settings_levels.append(("settings:social_account", acct.settings if acct else None))
    for level, values in settings_levels:
        n = (values or {}).get("required_approvers")
        if isinstance(n, int) and n > 1:
            rules.append(
                PolicyRule(
                    level=level,
                    required=n,
                    quorum=Quorum.ANY_OF,
                    approvers=frozenset(),
                    remind_minutes=10**6,
                    escalate_minutes=10**6,
                    expires_minutes=10**6,
                    validity_minutes=10**6,
                )
            )
    return rules


async def kill_switch_scope(
    s: AsyncSession,
    account_ids: Iterable[uuid.UUID],
    extra_groups: Iterable[uuid.UUID] = (),
) -> list[dict[str, Any]]:
    """Every engaged kill switch that covers any of these accounts: workspace, any group
    the account is in, or the account itself (docs/07 §5.2: all three levels).
    ``extra_groups`` adds the targeting group and the groups recorded when the approval was
    requested, so leaving a group afterwards does not escape its stop (review S8-H1)."""
    ids = list(account_ids)
    groups: set[uuid.UUID] = set(extra_groups)
    for a in ids:
        groups.update(await account_groups(s, a))
    rows = (
        await s.execute(
            select(AutomationState).where(
                AutomationState.kill_switch_engaged.is_(True),
                or_(
                    AutomationState.scope_type == "workspace",
                    (AutomationState.scope_type == "account_group")
                    & AutomationState.scope_id.in_(list(groups) or [uuid.UUID(int=0)]),
                    (AutomationState.scope_type == "social_account")
                    & AutomationState.scope_id.in_(ids or [uuid.UUID(int=0)]),
                ),
            )
        )
    ).scalars()
    return [{"scope_type": r.scope_type, "scope_id": r.scope_id} for r in rows]


async def lock_request(s: AsyncSession, request_id: uuid.UUID) -> ApprovalRequest | None:
    return await s.get(ApprovalRequest, request_id, with_for_update=True)


async def variant_states(s: AsyncSession, request_id: uuid.UUID) -> list[ApprovalVariantState]:
    return list(
        (
            await s.execute(
                select(ApprovalVariantState)
                .where(ApprovalVariantState.approval_request_id == request_id)
                .order_by(ApprovalVariantState.id)
            )
        ).scalars()
    )


def tally_of(row: ApprovalVariantState) -> VariantTally:
    return VariantTally(
        VariantApproval(row.state),
        row.required_count,
        Quorum(row.quorum_mode),
        row.approve_count,
        row.reject_count,
    )


def store_tally(row: ApprovalVariantState, t: VariantTally, now: datetime) -> None:
    if row.state != t.state.value and t.state is not VariantApproval.PENDING:
        row.decided_at = now
    row.state = t.state.value
    row.approve_count = t.approvals
    row.reject_count = t.rejections


async def revoke_tokens(
    s: AsyncSession, request_id: uuid.UUID, variant_ids: Sequence[uuid.UUID] | None, now: datetime
) -> None:
    """Scoped revocation (ADR-0014 §2): only tokens whose scope intersects the resolved
    variants, plus every unscoped ("all") token, whose composite hash just went stale.
    ``variant_ids=None`` revokes everything (the request reached a terminal state)."""
    q = update(ApprovalToken).where(
        ApprovalToken.approval_request_id == request_id,
        ApprovalToken.used_at.is_(None),
        ApprovalToken.revoked_at.is_(None),
    )
    if variant_ids is not None:
        q = q.where(
            or_(
                ApprovalToken.variant_scope.is_(None),
                ApprovalToken.variant_scope.overlap(list(variant_ids)),
            )
        )
    await s.execute(q.values(revoked_at=now))


async def settle(
    s: AsyncSession, req: ApprovalRequest, rows: list[ApprovalVariantState], now: datetime
) -> RequestState:
    """Derive the request state, mirror it onto the post set and its variants, and revoke
    every remaining token once the request is terminal."""
    effective = []
    for r in rows:
        v = await s.get(PostVariant, r.post_variant_id)
        moved = v is None or v.current_version_id != r.post_version_id
        # An account edited after its approval is no longer covered by it (review S8-M5).
        effective.append(
            VariantApproval.SUPERSEDED
            if moved and r.state in (VariantApproval.APPROVED.value, VariantApproval.PENDING.value)
            else VariantApproval(r.state)
        )
    derived = derive(effective)
    if req.state == RequestState.AWAITING_EDIT.value and derived is RequestState.PENDING:
        derived = RequestState.AWAITING_EDIT
    req.state = derived.value
    if not is_open(req.state):
        req.decided_at = req.decided_at or now
        await revoke_tokens(s, req.id, None, now)
    if any(r.state == VariantApproval.APPROVED.value for r in rows) and req.approved_at is None:
        req.approved_at = now
    for r in rows:
        v = await s.get(PostVariant, r.post_variant_id)
        if v is None or v.current_version_id != r.post_version_id:
            continue  # superseded by a newer version: drafting owns it again
        if v.state in PUBLISHING_VARIANT_STATES:
            continue  # scheduled or later: publishing owns it now (M8)
        v.state = {
            VariantApproval.PENDING.value: VariantState.NEEDS_APPROVAL.value,
            VariantApproval.APPROVED.value: VariantState.APPROVED.value,
            VariantApproval.REJECTED.value: VariantState.REJECTED.value,
        }.get(r.state, VariantState.DRAFTING.value)
    ps = await s.get(PostSet, req.post_set_id)
    if ps is not None and ps.state != PostSetState.ARCHIVED.value:
        ps.state = {
            RequestState.PENDING: PostSetState.NEEDS_APPROVAL,
            RequestState.AWAITING_EDIT: PostSetState.NEEDS_APPROVAL,
            RequestState.APPROVED: PostSetState.APPROVED,
            RequestState.PARTIALLY_APPROVED: PostSetState.PARTIALLY_APPROVED,
            RequestState.REJECTED: PostSetState.REJECTED,
        }.get(derived, PostSetState.DRAFTING).value
        await s.flush()
        if not is_open(req.state) or ps.state != PostSetState.NEEDS_APPROVAL.value:
            states = set(
                (
                    await s.execute(
                        select(PostVariant.state).where(
                            PostVariant.post_set_id == ps.id, PostVariant.enabled.is_(True)
                        )
                    )
                ).scalars()
            )
            if states & PUBLISHING_VARIANT_STATES:
                from mera.application.publishing.scheduler import rollup_set

                await rollup_set(s, ps)
    await s.flush()
    return derived


def snapshot_groups(req: Any, variant_id: uuid.UUID) -> list[uuid.UUID]:
    raw = (req.policy_snapshot.get("variants", {}).get(str(variant_id)) or {}).get("groups") or []
    return [uuid.UUID(g) for g in raw]


async def expire_if_due(s: AsyncSession, req: ApprovalRequest, now: datetime) -> bool:
    """Expiry is terminal and per account (docs/03 §7): pending accounts expire, decided
    ones keep their outcome. Applied whenever an open request is touched after its time,
    until the M6.8 sweeper does it on a clock (review S8-L8)."""
    if not is_open(req.state) or req.expires_at > now:
        return False
    rows = await variant_states(s, req.id)
    for r in rows:
        store_tally(r, end(tally_of(r), VariantApproval.EXPIRED), now)
    await settle(s, req, rows, now)
    return True


async def on_new_version(s: AsyncSession, variant: PostVariant) -> None:
    """A new version of a variant supersedes its pending approval (docs/03 §7). Its
    siblings' approvals and tokens are untouched (ADR-0014 §2). An already approved or
    rejected variant keeps that outcome on record but goes back to drafting: the approval
    covered the old version, which ``assert_publishable`` will refuse to retarget."""
    rows = (
        await s.execute(
            select(ApprovalVariantState)
            .join(ApprovalRequest, ApprovalRequest.id == ApprovalVariantState.approval_request_id)
            .where(
                ApprovalVariantState.post_variant_id == variant.id,
                ApprovalRequest.state.in_(
                    [RequestState.PENDING.value, RequestState.AWAITING_EDIT.value]
                ),
            )
        )
    ).scalars()
    now = datetime.now(UTC)
    for row in rows:
        req = await lock_request(s, row.approval_request_id)
        assert req is not None
        await s.refresh(row)  # a decision may have committed before we took the lock
        store_tally(row, end(tally_of(row), VariantApproval.SUPERSEDED), now)
        await revoke_tokens(s, req.id, [variant.id], now)
        await settle(s, req, await variant_states(s, req.id), now)
    # A queued slot for the old version is cancelled: it would be refused at publish time
    # anyway (version check), and the calendar must not show a post that will not go out.
    from mera.infrastructure.db.models import ScheduledPost

    queued = (
        await s.execute(
            select(ScheduledPost)
            .where(
                ScheduledPost.post_variant_id == variant.id,
                ScheduledPost.state.in_(["queued", "failed"]),
            )
            .with_for_update()
        )
    ).scalars()
    for sp in queued:
        sp.state = "cancelled"
        sp.last_error = "The post was edited after approval; it needs approving again."
    if variant.state in APPROVAL_VARIANT_STATES or variant.state == VariantState.SCHEDULED.value:
        variant.state = VariantState.DRAFTING.value
        await s.flush()


async def _away_cover(s: AsyncSession) -> dict[str, str]:
    """Members away right now, mapped to the delegate covering for them (who must be a
    member of the same workspace). Read in the caller's workspace transaction."""
    now = datetime.now(UTC)
    rows = (
        await s.execute(
            select(WorkspaceMember.user_id, WorkspaceMember.delegate_user_id).where(
                WorkspaceMember.away_until.is_not(None),
                WorkspaceMember.away_until > now,
                WorkspaceMember.delegate_user_id.is_not(None),
            )
        )
    ).all()
    if not rows:
        return {}
    members = set(
        (
            await s.execute(
                select(WorkspaceMember.user_id).where(
                    WorkspaceMember.user_id.in_([d for _, d in rows])
                )
            )
        ).scalars()
    )
    return {str(u): str(d) for u, d in rows if d in members and d != u}
