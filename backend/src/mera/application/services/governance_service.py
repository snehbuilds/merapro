"""Approval policies and automation state (M6.9, M6.12; docs/07 §7.1, docs/03 §10).

Policies resolve strictest-wins (ADR-0014), so writing one can only tighten what applies,
but deleting one loosens: every policy write is an Admin's (MANAGE_WORKSPACE).

The kill switch stops everything at its scope: decisions are refused and nothing
publishes (``assert_publishable`` check 9). Engaging is always safe, so any Approver may do
it for what they can reach; releasing is an Admin's (CHANGE_AUTOPILOT).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application import second_factor
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, InvalidInput, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    AccountGroup,
    ApprovalPolicy,
    AutomationState,
    SocialAccount,
    Workspace,
    WorkspaceMember,
)
from mera.infrastructure.db.repositories.audit import record

SCOPES = ("workspace", "account_group", "social_account")


@dataclass(frozen=True, slots=True)
class PolicyFields:
    required_approver_count: int | None = None
    quorum_mode: str | None = None
    approver_user_ids: list[uuid.UUID] | None = None
    escalate_to_user_ids: list[uuid.UUID] | None = None
    remind_after_minutes: int | None = None
    escalate_after_minutes: int | None = None
    expires_after_minutes: int | None = None
    validity_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class PolicyView:
    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID | None
    required_approver_count: int
    quorum_mode: str
    approver_user_ids: list[uuid.UUID]
    remind_after_minutes: int
    escalate_after_minutes: int
    expires_after_minutes: int
    validity_minutes: int
    updated_at: datetime
    escalate_to_user_ids: list[uuid.UUID]


@dataclass(frozen=True, slots=True)
class AutomationView:
    scope_type: str
    scope_id: uuid.UUID | None
    autopilot_mode: str
    kill_switch_engaged: bool
    kill_switch_at: datetime | None
    kill_switch_by_user_id: uuid.UUID | None
    kill_switch_reason: str | None


def _m(td: timedelta) -> int:
    return int(td.total_seconds() // 60)


def _policy_view(p: ApprovalPolicy) -> PolicyView:
    return PolicyView(
        id=p.id,
        scope_type=p.scope_type,
        scope_id=p.scope_id,
        required_approver_count=p.required_approver_count,
        quorum_mode=p.quorum_mode,
        approver_user_ids=list(p.approver_user_ids),
        escalate_to_user_ids=list(p.escalate_to_user_ids or []),
        remind_after_minutes=_m(p.remind_after),
        escalate_after_minutes=_m(p.escalate_after),
        expires_after_minutes=_m(p.expires_after),
        validity_minutes=_m(p.approval_validity_window),
        updated_at=p.updated_at,
    )


def _auto_view(a: AutomationState) -> AutomationView:
    return AutomationView(
        a.scope_type,
        a.scope_id,
        a.autopilot_mode,
        a.kill_switch_engaged,
        a.kill_switch_at,
        a.kill_switch_by_user_id,
        a.kill_switch_reason,
    )


async def _check_scope(s: AsyncSession, scope_type: str, scope_id: uuid.UUID | None) -> None:
    if scope_type not in SCOPES:
        raise InvalidInput("Unknown scope.", code="invalid_scope", field="scope_type")
    if (scope_type == "workspace") != (scope_id is None):
        raise InvalidInput(
            "A workspace scope has no id; a group or account scope needs one.",
            code="invalid_scope",
            field="scope_id",
        )
    if scope_type == "account_group" and await s.get(AccountGroup, scope_id) is None:
        raise NotFoundError("Group not found.")
    if scope_type == "social_account" and await s.get(SocialAccount, scope_id) is None:
        raise NotFoundError("Account not found.")


class GovernanceService(WorkspaceScopedService):
    async def _writable(self, s: AsyncSession, workspace_id: uuid.UUID) -> None:
        ws = await s.get(Workspace, workspace_id)
        if ws is None:
            raise NotFoundError("Workspace not found.")
        self._ensure_writable(ws)

    # ----------------------------------------------------------------- policies

    async def list_policies(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[PolicyView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            rows = (
                await s.execute(
                    select(ApprovalPolicy).order_by(
                        ApprovalPolicy.scope_type.desc(), ApprovalPolicy.created_at
                    )
                )
            ).scalars()
            return [_policy_view(p) for p in rows]

    async def put_policy(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        scope_type: str,
        scope_id: uuid.UUID | None,
        fields: PolicyFields,
    ) -> PolicyView:
        """Create or update the policy at one scope (one per scope)."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
            await self._writable(s, workspace_id)
            await _check_scope(s, scope_type, scope_id)
            q = select(ApprovalPolicy).where(ApprovalPolicy.scope_type == scope_type)
            q = (
                q.where(ApprovalPolicy.scope_id.is_(None))
                if scope_id is None
                else q.where(ApprovalPolicy.scope_id == scope_id)
            )
            p = (await s.execute(q.with_for_update())).scalar_one_or_none()
            before = asdict(_policy_view(p)) if p else None
            if p is None:
                p = ApprovalPolicy(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    remind_after=timedelta(hours=2),
                    escalate_after=timedelta(hours=4),
                    expires_after=timedelta(hours=12),
                    approval_validity_window=timedelta(hours=24),
                )
                s.add(p)
            if fields.required_approver_count is not None:
                p.required_approver_count = fields.required_approver_count
            if fields.quorum_mode is not None:
                p.quorum_mode = fields.quorum_mode
            if fields.approver_user_ids is not None:
                ids = list(dict.fromkeys(fields.approver_user_ids))
                members = set(
                    (
                        await s.execute(
                            select(WorkspaceMember.user_id).where(
                                WorkspaceMember.user_id.in_(ids or [uuid.UUID(int=0)]),
                                WorkspaceMember.role.in_(["owner", "admin", "editor", "approver"]),
                            )
                        )
                    ).scalars()
                )
                if set(ids) - members:
                    raise InvalidInput(
                        "Every named approver must be a member who can approve.",
                        code="invalid_approvers",
                        field="approver_user_ids",
                    )
                p.approver_user_ids = ids
            if fields.escalate_to_user_ids is not None:
                esc = list(dict.fromkeys(fields.escalate_to_user_ids))
                members = set(
                    (
                        await s.execute(
                            select(WorkspaceMember.user_id).where(
                                WorkspaceMember.user_id.in_(esc or [uuid.UUID(int=0)])
                            )
                        )
                    ).scalars()
                )
                if set(esc) - members:
                    raise InvalidInput(
                        "Escalate only to members of this workspace.",
                        code="invalid_escalation",
                        field="escalate_to_user_ids",
                    )
                p.escalate_to_user_ids = esc
            for name, attr in (
                ("remind_after_minutes", "remind_after"),
                ("escalate_after_minutes", "escalate_after"),
                ("expires_after_minutes", "expires_after"),
                ("validity_minutes", "approval_validity_window"),
            ):
                v = getattr(fields, name)
                if v is not None:
                    setattr(p, attr, timedelta(minutes=v))
            if p.approver_user_ids and p.required_approver_count > len(p.approver_user_ids):
                raise InvalidInput(
                    "More approvals are required than there are named approvers.",
                    code="unsatisfiable_policy",
                    field="required_approver_count",
                )
            p.updated_by_user_id = user.user_id
            p.updated_at = datetime.now(UTC)
            try:
                await s.flush()
            except IntegrityError as exc:
                raise InvalidInput(
                    "Timers must run remind ≤ escalate ≤ expire, within 7 days.",
                    code="invalid_timers",
                ) from exc
            await s.refresh(p)
            view = _policy_view(p)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "approval_policy.updated" if before else "approval_policy.created",
                    workspace_id=workspace_id,
                    subject_type="approval_policy",
                    subject_id=p.id,
                    before=_jsonable(before),
                    after=_jsonable(asdict(view)),
                ),
            )
            return view

    async def delete_policy(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        policy_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
            await self._writable(s, workspace_id)
            p = await s.get(ApprovalPolicy, policy_id)
            if p is None:
                raise NotFoundError("Policy not found.")
            # Requests in flight keep their snapshot; only new ones see the change.
            await s.delete(p)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "approval_policy.deleted",
                    workspace_id=workspace_id,
                    subject_type="approval_policy",
                    subject_id=policy_id,
                ),
            )

    # --------------------------------------------------------------- automation

    async def list_automation(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[AutomationView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            rows = (await s.execute(select(AutomationState))).scalars()
            return [_auto_view(a) for a in rows]

    async def set_kill_switch(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        scope_type: str,
        scope_id: uuid.UUID | None,
        engaged: bool,
        reason: str | None,
    ) -> AutomationView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            await _check_scope(s, scope_type, scope_id)
            permission = Permission.ENGAGE_KILL_SWITCH if engaged else Permission.CHANGE_AUTOPILOT
            rbac.require(
                principal,
                permission,
                workspace_id,
                social_account_id=scope_id if scope_type == "social_account" else None,
                account_group_id=scope_id if scope_type == "account_group" else None,
            )
            if not engaged:
                # Stopping is always safe; resuming is a decision (docs/09 §5.3).
                await second_factor.require(s, user, workspace_id, "kill_switch_resume", meta)
            q = select(AutomationState).where(AutomationState.scope_type == scope_type)
            q = (
                q.where(AutomationState.scope_id.is_(None))
                if scope_id is None
                else q.where(AutomationState.scope_id == scope_id)
            )
            a = (await s.execute(q.with_for_update())).scalar_one_or_none()
            if a is None:
                a = AutomationState(
                    id=uuid7(), workspace_id=workspace_id, scope_type=scope_type, scope_id=scope_id
                )
                s.add(a)
            if engaged and a.kill_switch_engaged:
                raise ConflictError("Automation is already stopped here.", code="already_stopped")
            if not engaged and not a.kill_switch_engaged:
                raise ConflictError("Automation is not stopped here.", code="not_stopped")
            now = datetime.now(UTC)
            a.kill_switch_engaged = engaged
            a.kill_switch_by_user_id = user.user_id
            a.kill_switch_surface = (
                meta.surface
                if meta.surface in ("telegram", "whatsapp", "mcp")
                else ("api" if user.api_key else "web")
            )
            a.kill_switch_at = now
            a.kill_switch_reason = (reason or "").strip()[:500] or None
            a.updated_at = now
            await s.flush()
            await s.refresh(a)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "kill_switch.engaged" if engaged else "kill_switch.released",
                    workspace_id=workspace_id,
                    subject_type=scope_type,
                    subject_id=scope_id,
                    after={"reason": a.kill_switch_reason},
                ),
            )
            return _auto_view(a)


def _jsonable(d: dict[str, Any] | None) -> dict[str, Any] | None:
    if d is None:
        return None
    return {
        k: (
            str(v)
            if isinstance(v, (uuid.UUID, datetime))
            else [str(x) for x in v]
            if isinstance(v, list)
            else v
        )
        for k, v in d.items()
    }
