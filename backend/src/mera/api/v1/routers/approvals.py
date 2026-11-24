"""Approvals (docs/04 §4.9), approval policies and the kill switch (§4.12)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import Approvals, CurrentUser, Governance, Meta
from mera.api.v1.routers.posts import SETS, WS
from mera.application.services.governance_service import PolicyFields

router = APIRouter(tags=["approvals"])
automation_router = APIRouter(tags=["automation"])

Scope = Literal["workspace", "account_group", "social_account"]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ApprovalVariantOut(_Out):
    post_variant_id: uuid.UUID
    social_account_id: uuid.UUID
    platform: str
    nickname: str
    handle: str
    state: str = Field(description="pending, approved, rejected, superseded, expired, cancelled")
    required: int = Field(description="Approvals this account needs (strictest policy)")
    quorum: str
    approve_count: int
    reject_count: int
    decided_at: datetime | None
    post_version_id: uuid.UUID = Field(description="The exact version this approval covers")
    version_no: int
    content_hash: str = Field(description="Send this back as the hash you were shown")
    is_current: bool = Field(description="False once the post was edited after submission")
    body: str
    thread_parts: list[str] | None
    hashtags: list[str]
    link_url: str | None
    first_comment: str | None
    media: list[dict[str, Any]]
    confidence_score: float | None
    compliance_flags: list[dict[str, Any]]
    my_vote: str | None
    can_decide: bool


class DecisionOut(_Out):
    approver_label: str | None
    channel_type: str
    action: str
    variant_scope: list[uuid.UUID]
    reason: str | None
    decided_at: datetime


class ApprovalRequestOut(_Out):
    id: uuid.UUID
    post_set_id: uuid.UUID
    title: str | None
    state: str = Field(
        description="pending, awaiting_edit, approved, partially_approved, rejected, expired, "
        "superseded, cancelled (derived from the accounts, ADR-0014)"
    )
    content_hash: str
    created_at: datetime
    expires_at: datetime
    remind_at: datetime | None
    approved_at: datetime | None
    decided_at: datetime | None
    requested_by: str | None
    variants: list[ApprovalVariantOut]
    decisions: list[DecisionOut]


class ApprovalRequestList(BaseModel):
    data: list[ApprovalRequestOut]


class SubmitIn(BaseModel):
    scheduled_for: datetime | None = Field(default=None, description="Requested publish time")
    variant_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=25,
        description="Accounts to send; default every enabled account still in drafting",
    )


class EditIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=1000, description="What to change")
    variant_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=25, description="Accounts to revise; null = every pending one"
    )


class VariantList(BaseModel):
    data: list[ApprovalVariantOut]


class DecisionIn(BaseModel):
    action: Literal["approve", "reject"]
    variant_scope: list[uuid.UUID] | None = Field(
        default=None, max_length=25, description="Post variant ids; null = every pending account"
    )
    content_hashes: dict[str, Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]] = Field(
        description="post_variant_id → the content_hash you were shown, for every account in "
        "scope; a changed post is refused with 422 approval_content_mismatch",
        max_length=25,
    )
    reason: str | None = Field(default=None, max_length=1000)


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


@router.post(
    f"{SETS}/{{set_id}}/request-approval",
    response_model=ApprovalRequestOut,
    status_code=status.HTTP_201_CREATED,
)
async def request_approval(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    user: CurrentUser,
    svc: Approvals,
    meta: Meta,
    body: SubmitIn | None = None,
) -> ApprovalRequestOut:
    """Send every enabled account's post for approval. Refused (409 not_ready_for_approval)
    while a guardrail blocks, a duplicate check could not run, or drafting is running."""
    view = await svc.submit(
        user,
        workspace_id,
        set_id,
        meta,
        scheduled_for=body.scheduled_for if body else None,
        variant_ids=body.variant_ids if body else None,
    )
    return ApprovalRequestOut.model_validate(view)


@router.get(f"{WS}/approval-requests", response_model=ApprovalRequestList)
async def list_approval_requests(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Approvals,
    meta: Meta,
    state: Annotated[str | None, Query(max_length=30, description="open, or one state")] = "open",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ApprovalRequestList:
    rows = await svc.list_requests(user, workspace_id, meta, state=state, limit=limit)
    return ApprovalRequestList(data=[ApprovalRequestOut.model_validate(r) for r in rows])


@router.get(f"{WS}/approval-requests/{{request_id}}", response_model=ApprovalRequestOut)
async def get_approval_request(
    workspace_id: uuid.UUID, request_id: uuid.UUID, user: CurrentUser, svc: Approvals, meta: Meta
) -> ApprovalRequestOut:
    return ApprovalRequestOut.model_validate(await svc.get(user, workspace_id, request_id, meta))


@router.post(f"{WS}/approval-requests/{{request_id}}/decisions", response_model=ApprovalRequestOut)
async def decide(
    workspace_id: uuid.UUID,
    request_id: uuid.UUID,
    body: DecisionIn,
    user: CurrentUser,
    svc: Approvals,
    meta: Meta,
) -> ApprovalRequestOut:
    """Approve or reject some or all accounts. 403 approver_not_authorized, 409
    approval_already_decided / kill_switch_engaged / already_voted, 410 approval_expired,
    422 approval_content_mismatch (ADR-0014 §3)."""
    view = await svc.decide(
        user,
        workspace_id,
        request_id,
        meta,
        action=body.action,
        variant_scope=body.variant_scope,
        content_hashes=body.content_hashes,
        reason=body.reason,
    )
    return ApprovalRequestOut.model_validate(view)


@router.get(f"{WS}/approval-requests/{{request_id}}/variants", response_model=VariantList)
async def approval_variants(
    workspace_id: uuid.UUID, request_id: uuid.UUID, user: CurrentUser, svc: Approvals, meta: Meta
) -> VariantList:
    """Per-account decision state."""
    view = await svc.get(user, workspace_id, request_id, meta)
    return VariantList(data=[ApprovalVariantOut.model_validate(v) for v in view.variants])


@router.get(f"{WS}/approval-requests/{{request_id}}/preview")
async def approval_preview(
    workspace_id: uuid.UUID, request_id: uuid.UUID, user: CurrentUser, svc: Approvals, meta: Meta
) -> dict[str, Any]:
    """The rendered-card payload every surface shows: per-account posts, the source block
    once, the distinctness check and the stagger span (docs/07 §3.2)."""
    return await svc.preview(user, workspace_id, request_id, meta)


@router.post(f"{WS}/approval-requests/{{request_id}}/edit", response_model=ApprovalRequestOut)
async def request_edit(
    workspace_id: uuid.UUID,
    request_id: uuid.UUID,
    body: EditIn,
    user: CurrentUser,
    svc: Approvals,
    meta: Meta,
) -> ApprovalRequestOut:
    """Ask the Writer to revise some accounts with an instruction. Only those accounts'
    approvals are superseded; they are asked again when the new text lands. To replace the
    text yourself, create a version on the variant instead."""
    view = await svc.request_edit(
        user,
        workspace_id,
        request_id,
        meta,
        instruction=body.instruction,
        variant_ids=body.variant_ids,
    )
    return ApprovalRequestOut.model_validate(view)


@router.post(f"{WS}/approval-requests/{{request_id}}/cancel", response_model=ApprovalRequestOut)
async def cancel_approval_request(
    workspace_id: uuid.UUID,
    request_id: uuid.UUID,
    user: CurrentUser,
    svc: Approvals,
    meta: Meta,
    body: CancelIn | None = None,
) -> ApprovalRequestOut:
    """Withdraw the request; accounts already decided keep their outcome."""
    view = await svc.cancel(
        user, workspace_id, request_id, meta, reason=body.reason if body else None
    )
    return ApprovalRequestOut.model_validate(view)


# ---------------------------------------------------------------- policies


class PolicyOut(_Out):
    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID | None
    required_approver_count: int
    quorum_mode: str
    approver_user_ids: list[uuid.UUID]
    escalate_to_user_ids: list[uuid.UUID] = Field(
        default_factory=list, description="Named people told on escalation; empty = Admins"
    )
    remind_after_minutes: int
    escalate_after_minutes: int
    expires_after_minutes: int
    validity_minutes: int
    updated_at: datetime


class PolicyList(BaseModel):
    data: list[PolicyOut]


class PolicyIn(BaseModel):
    scope_type: Scope
    scope_id: uuid.UUID | None = None
    required_approver_count: int | None = Field(default=None, ge=1, le=5)
    quorum_mode: Literal["any_of", "all_of"] | None = None
    approver_user_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=20, description="Empty: anyone who can approve the account"
    )
    escalate_to_user_ids: list[uuid.UUID] | None = Field(
        default=None, max_length=20, description="Who hears about an escalation; empty: Admins"
    )
    remind_after_minutes: int | None = Field(default=None, ge=5, le=10080)
    escalate_after_minutes: int | None = Field(default=None, ge=5, le=10080)
    expires_after_minutes: int | None = Field(default=None, ge=5, le=10080)
    validity_minutes: int | None = Field(default=None, ge=5, le=10080)


@router.get(f"{WS}/approval-policies", response_model=PolicyList)
async def list_policies(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Governance, meta: Meta
) -> PolicyList:
    rows = await svc.list_policies(user, workspace_id, meta)
    return PolicyList(data=[PolicyOut.model_validate(r) for r in rows])


@router.put(f"{WS}/approval-policies", response_model=PolicyOut)
async def put_policy(
    workspace_id: uuid.UUID, body: PolicyIn, user: CurrentUser, svc: Governance, meta: Meta
) -> PolicyOut:
    """Create or update the policy at one scope. Policies resolve strictest-wins across the
    workspace, every group an account is in, and the account (ADR-0014)."""
    fields = PolicyFields(
        required_approver_count=body.required_approver_count,
        quorum_mode=body.quorum_mode,
        approver_user_ids=body.approver_user_ids,
        escalate_to_user_ids=body.escalate_to_user_ids,
        remind_after_minutes=body.remind_after_minutes,
        escalate_after_minutes=body.escalate_after_minutes,
        expires_after_minutes=body.expires_after_minutes,
        validity_minutes=body.validity_minutes,
    )
    view = await svc.put_policy(
        user, workspace_id, meta, scope_type=body.scope_type, scope_id=body.scope_id, fields=fields
    )
    return PolicyOut.model_validate(view)


@router.delete(f"{WS}/approval-policies/{{policy_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    workspace_id: uuid.UUID, policy_id: uuid.UUID, user: CurrentUser, svc: Governance, meta: Meta
) -> Response:
    await svc.delete_policy(user, workspace_id, policy_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------- automation


class AutomationOut(_Out):
    scope_type: str
    scope_id: uuid.UUID | None
    autopilot_mode: str
    kill_switch_engaged: bool
    kill_switch_at: datetime | None
    kill_switch_by_user_id: uuid.UUID | None
    kill_switch_reason: str | None


class AutomationList(BaseModel):
    data: list[AutomationOut]


class KillSwitchIn(BaseModel):
    scope_type: Scope
    scope_id: uuid.UUID | None = None
    engaged: bool = Field(description="true stops; false resumes (Admins only)")
    reason: str | None = Field(default=None, max_length=500)


@automation_router.get(f"{WS}/automation", response_model=AutomationList)
async def list_automation(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Governance, meta: Meta
) -> AutomationList:
    rows = await svc.list_automation(user, workspace_id, meta)
    return AutomationList(data=[AutomationOut.model_validate(r) for r in rows])


@automation_router.post(f"{WS}/automation/kill-switch", response_model=AutomationOut)
async def kill_switch(
    workspace_id: uuid.UUID, body: KillSwitchIn, user: CurrentUser, svc: Governance, meta: Meta
) -> AutomationOut:
    """Stop (any Approver, for what they can reach) or resume (Admins) at one scope. While
    stopped, approvals are refused and nothing publishes for the accounts it covers."""
    view = await svc.set_kill_switch(
        user,
        workspace_id,
        meta,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        engaged=body.engaged,
        reason=body.reason,
    )
    return AutomationOut.model_validate(view)
