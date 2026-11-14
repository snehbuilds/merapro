"""The unified inbox (P2-M6): comments, replies, hide and delete, engagement rules."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import CurrentUser, Inbox, Meta
from mera.api.v1.routers.posts import WS

router = APIRouter(tags=["inbox"])


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReplyOut(_Out):
    id: uuid.UUID
    comment_id: uuid.UUID
    social_account_id: uuid.UUID = Field(description="Always the commented account")
    body: str
    content_hash: str = Field(description="What an approval binds to")
    state: str = Field(
        description="draft, pending_approval, approved, sending, sent, failed, rejected"
    )
    source: str
    confidence: float | None
    error: str | None
    platform_reply_id: str | None
    created_at: datetime
    approved_at: datetime | None
    sent_at: datetime | None


class CommentOut(_Out):
    id: uuid.UUID
    published_post_id: uuid.UUID
    social_account_id: uuid.UUID
    account_nickname: str
    account_handle: str
    account_group_id: uuid.UUID | None
    platform: str
    author_handle: str | None
    body: str = Field(description="Untrusted text from the public")
    permalink: str | None
    post_permalink: str | None
    post_excerpt: str
    sentiment: str | None
    intent: str | None
    state: str
    escalation_reason: str | None
    is_own: bool
    created_at_platform: datetime | None
    created_at: datetime
    replies: list[ReplyOut]
    can_hide: bool
    can_delete: bool


class CommentList(BaseModel):
    data: list[CommentOut]


class SummaryOut(BaseModel):
    states: dict[str, int]
    replies_pending_approval: int
    platforms: list[str] = Field(description="Platforms whose comments Mera can read")


class TriageIn(BaseModel):
    comment_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    state: Literal["new", "triaged", "ignored", "escalated"]
    assigned_to_user_id: uuid.UUID | None = None


class TriageOut(BaseModel):
    changed: int


class DraftIn(BaseModel):
    body: str | None = Field(
        default=None, max_length=3000, description="Omit: the Engagement agent drafts"
    )
    instruction: str | None = Field(default=None, max_length=300)


class EditIn(BaseModel):
    body: str = Field(min_length=1, max_length=3000)


class ApproveIn(BaseModel):
    content_hash: str = Field(min_length=64, max_length=64, description="The hash you reviewed")


class HideIn(BaseModel):
    hidden: bool = True


class RuleIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    scope_type: Literal["workspace", "account_group", "social_account"] | None = None
    scope_id: uuid.UUID | None = None
    match: dict[str, list[str]] | None = Field(
        default=None, description="keywords, intent and/or sentiment lists"
    )
    action: Literal["draft_reply", "escalate", "ignore", "hide"] | None = None
    reply_template: str | None = Field(default=None, max_length=1000)
    priority: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None


class RuleOut(_Out):
    id: uuid.UUID
    name: str
    scope_type: str
    scope_id: uuid.UUID | None
    match: dict[str, Any]
    action: str
    reply_template: str | None
    priority: int
    enabled: bool
    created_at: datetime


class RuleList(BaseModel):
    data: list[RuleOut]


INBOX = f"{WS}/inbox"


@router.get(f"{INBOX}/summary", response_model=SummaryOut)
async def inbox_summary(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Inbox, meta: Meta
) -> SummaryOut:
    return SummaryOut(**await svc.summary(user, workspace_id, meta))


class PollOut(BaseModel):
    polled: int = 0
    new: int = 0
    triaged: int = 0


@router.post(f"{WS}/sandbox/poll-comments", response_model=PollOut)
async def sandbox_poll_comments(
    workspace_id: uuid.UUID, request: Request, user: CurrentUser, svc: Inbox, meta: Meta
) -> PollOut:
    """Sandbox only: fetch comments on recent posts now (instead of on the poll's schedule)."""
    out = await svc.poll_now(
        user, workspace_id, meta, sandbox=request.app.state.settings.sandbox_mode
    )
    return PollOut(**{k: int(out.get(k, 0)) for k in ("polled", "new", "triaged")})


@router.get(f"{INBOX}/comments", response_model=CommentList)
async def list_comments(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Inbox,
    meta: Meta,
    state: Annotated[str | None, Query(max_length=16)] = None,
    platform: Annotated[str | None, Query(max_length=32)] = None,
    social_account_id: uuid.UUID | None = None,
    account_group_id: uuid.UUID | None = None,
    sentiment: Annotated[str | None, Query(max_length=16)] = None,
    intent: Annotated[str | None, Query(max_length=16)] = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CommentList:
    rows = await svc.list_comments(
        user,
        workspace_id,
        meta,
        state=state,
        platform=platform,
        social_account_id=social_account_id,
        account_group_id=account_group_id,
        sentiment=sentiment,
        intent=intent,
        before=before,
        limit=limit,
    )
    return CommentList(data=[CommentOut.model_validate(r) for r in rows])


@router.get(f"{INBOX}/comments/{{comment_id}}", response_model=CommentOut)
async def get_comment(
    workspace_id: uuid.UUID, comment_id: uuid.UUID, user: CurrentUser, svc: Inbox, meta: Meta
) -> CommentOut:
    return CommentOut.model_validate(await svc.get_comment(user, workspace_id, meta, comment_id))


@router.post(f"{INBOX}/comments/triage", response_model=TriageOut)
async def triage_comments(
    workspace_id: uuid.UUID, body: TriageIn, user: CurrentUser, svc: Inbox, meta: Meta
) -> TriageOut:
    """Bulk triage: new, triaged, ignored or escalated."""
    n = await svc.set_state(
        user,
        workspace_id,
        meta,
        body.comment_ids,
        state=body.state,
        assigned_to_user_id=body.assigned_to_user_id,
    )
    return TriageOut(changed=n)


@router.post(
    f"{INBOX}/comments/{{comment_id}}/replies",
    response_model=ReplyOut,
    status_code=status.HTTP_201_CREATED,
)
async def draft_reply(
    workspace_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: DraftIn,
    user: CurrentUser,
    svc: Inbox,
    meta: Meta,
) -> ReplyOut:
    """A reply draft as the commented account: your text, or the Engagement agent's."""
    return ReplyOut.model_validate(
        await svc.draft_reply(
            user, workspace_id, meta, comment_id, body=body.body, instruction=body.instruction
        )
    )


@router.post(f"{INBOX}/comments/{{comment_id}}/hide", response_model=CommentOut)
async def hide_comment(
    workspace_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: HideIn,
    user: CurrentUser,
    svc: Inbox,
    meta: Meta,
) -> CommentOut:
    return CommentOut.model_validate(
        await svc.hide_comment(user, workspace_id, meta, comment_id, hidden=body.hidden)
    )


@router.delete(f"{INBOX}/comments/{{comment_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    workspace_id: uuid.UUID, comment_id: uuid.UUID, user: CurrentUser, svc: Inbox, meta: Meta
) -> Response:
    """Delete someone's comment on the platform. Needs a second factor (``comment_delete``)."""
    await svc.delete_comment(user, workspace_id, meta, comment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(f"{INBOX}/replies/{{reply_id}}", response_model=ReplyOut)
async def edit_reply(
    workspace_id: uuid.UUID,
    reply_id: uuid.UUID,
    body: EditIn,
    user: CurrentUser,
    svc: Inbox,
    meta: Meta,
) -> ReplyOut:
    """Edit a draft. Any approval is void: it is a different reply now."""
    return ReplyOut.model_validate(
        await svc.edit_reply(user, workspace_id, meta, reply_id, body=body.body)
    )


@router.post(f"{INBOX}/replies/{{reply_id}}/submit", response_model=ReplyOut)
async def submit_reply(
    workspace_id: uuid.UUID, reply_id: uuid.UUID, user: CurrentUser, svc: Inbox, meta: Meta
) -> ReplyOut:
    return ReplyOut.model_validate(await svc.submit_reply(user, workspace_id, meta, reply_id))


@router.post(f"{INBOX}/replies/{{reply_id}}/approve", response_model=ReplyOut)
async def approve_reply(
    workspace_id: uuid.UUID,
    reply_id: uuid.UUID,
    body: ApproveIn,
    user: CurrentUser,
    svc: Inbox,
    meta: Meta,
) -> ReplyOut:
    """Approve the text you reviewed; it is then sent as the commented account."""
    return ReplyOut.model_validate(
        await svc.approve_reply(user, workspace_id, meta, reply_id, content_hash=body.content_hash)
    )


@router.post(f"{INBOX}/replies/{{reply_id}}/reject", response_model=ReplyOut)
async def reject_reply(
    workspace_id: uuid.UUID, reply_id: uuid.UUID, user: CurrentUser, svc: Inbox, meta: Meta
) -> ReplyOut:
    return ReplyOut.model_validate(await svc.reject_reply(user, workspace_id, meta, reply_id))


@router.get(f"{WS}/engagement-rules", response_model=RuleList)
async def list_rules(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Inbox, meta: Meta
) -> RuleList:
    rows = await svc.list_rules(user, workspace_id, meta)
    return RuleList(data=[RuleOut.model_validate(r) for r in rows])


@router.post(f"{WS}/engagement-rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    workspace_id: uuid.UUID, body: RuleIn, user: CurrentUser, svc: Inbox, meta: Meta
) -> RuleOut:
    return RuleOut.model_validate(
        await svc.save_rule(user, workspace_id, meta, rule_id=None, fields=body.model_dump())
    )


@router.patch(f"{WS}/engagement-rules/{{rule_id}}", response_model=RuleOut)
async def update_rule(
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
    body: RuleIn,
    user: CurrentUser,
    svc: Inbox,
    meta: Meta,
) -> RuleOut:
    return RuleOut.model_validate(
        await svc.save_rule(
            user, workspace_id, meta, rule_id=rule_id, fields=body.model_dump(exclude_unset=True)
        )
    )


@router.delete(f"{WS}/engagement-rules/{{rule_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    workspace_id: uuid.UUID, rule_id: uuid.UUID, user: CurrentUser, svc: Inbox, meta: Meta
) -> Response:
    await svc.delete_rule(user, workspace_id, meta, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
