"""Scheduling and publishing (docs/04 §4.10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import CurrentUser, Meta, Publishing
from mera.api.v1.routers.posts import SETS, WS

router = APIRouter(tags=["publishing"])


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ScheduledPostOut(_Out):
    id: uuid.UUID
    post_set_id: uuid.UUID
    post_set_title: str
    post_variant_id: uuid.UUID
    post_version_id: uuid.UUID = Field(description="The approved version this slot publishes")
    social_account_id: uuid.UUID
    account_nickname: str
    account_handle: str
    platform: str
    account_group_id: uuid.UUID | None
    group_name: str | None
    group_color: str | None
    scheduled_for: datetime = Field(description="Includes the stagger offset; stored once")
    stagger_offset_seconds: int
    state: str = Field(
        description="queued, publishing, published, failed, dead_lettered, cancelled"
    )
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    last_error: str | None
    moved_by: list[str] = Field(
        description="Rules that moved the slot: quiet hours, spacing, daily cap"
    )
    excerpt: str
    permalink: str | None
    published_at: datetime | None


class ScheduledPostList(BaseModel):
    data: list[ScheduledPostOut]


class PublishedPostOut(_Out):
    id: uuid.UUID
    post_set_id: uuid.UUID
    post_variant_id: uuid.UUID
    social_account_id: uuid.UUID
    account_nickname: str
    platform: str
    account_group_id: uuid.UUID | None
    platform_post_id: str
    permalink: str | None
    published_at: datetime
    excerpt: str
    content_hash: str = Field(description="Exactly which bytes went out")


class PublishedPostList(BaseModel):
    data: list[PublishedPostOut]


class DeadLetterOut(_Out):
    id: uuid.UUID
    subject_id: uuid.UUID = Field(description="The scheduled post")
    social_account_id: uuid.UUID | None
    account_nickname: str | None
    post_set_id: uuid.UUID | None
    post_set_title: str | None
    error: str
    attempts: int
    failed_at: datetime
    resolved_at: datetime | None
    resolution: str | None


class DeadLetterList(BaseModel):
    data: list[DeadLetterOut]


class SetStatusOut(_Out):
    post_set_id: uuid.UUID
    state: str
    accounts: list[dict[str, Any]] = Field(description="Per-account publish state and error")


class RescheduleIn(BaseModel):
    scheduled_for: datetime


class ScheduleSetIn(BaseModel):
    scheduled_for: datetime | None = Field(
        default=None, description="Base time; stagger offsets are added"
    )


class ScheduleResult(BaseModel):
    results: list[dict[str, Any]] = Field(
        description="Per account: scheduled, moved, approval_required, …"
    )


@router.get(f"{WS}/scheduled-posts", response_model=ScheduledPostList)
async def list_scheduled_posts(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Publishing,
    meta: Meta,
    state: Annotated[str | None, Query(max_length=20, description="open, or one state")] = None,
    account_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    post_set_id: uuid.UUID | None = None,
    frm: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ScheduledPostList:
    rows = await svc.list_scheduled(
        user,
        workspace_id,
        meta,
        state=state,
        account_id=account_id,
        group_id=group_id,
        post_set_id=post_set_id,
        frm=frm,
        to=to,
        limit=limit,
    )
    return ScheduledPostList(data=[ScheduledPostOut.model_validate(r) for r in rows])


@router.patch(f"{WS}/scheduled-posts/{{sp_id}}", response_model=ScheduledPostOut)
async def reschedule(
    workspace_id: uuid.UUID,
    sp_id: uuid.UUID,
    body: RescheduleIn,
    user: CurrentUser,
    svc: Publishing,
    meta: Meta,
) -> ScheduledPostOut:
    """Reschedule only. Quiet hours, the daily cap and spacing still apply; ``moved_by`` says
    if they moved the slot."""
    return ScheduledPostOut.model_validate(
        await svc.reschedule(user, workspace_id, sp_id, meta, scheduled_for=body.scheduled_for)
    )


@router.patch(f"{WS}/calendar/{{sp_id}}/move", response_model=ScheduledPostOut)
async def calendar_move(
    workspace_id: uuid.UUID,
    sp_id: uuid.UUID,
    body: RescheduleIn,
    user: CurrentUser,
    svc: Publishing,
    meta: Meta,
) -> ScheduledPostOut:
    """The calendar's drag-and-drop (and keyboard) target: the same as a reschedule."""
    return ScheduledPostOut.model_validate(
        await svc.reschedule(user, workspace_id, sp_id, meta, scheduled_for=body.scheduled_for)
    )


@router.delete(f"{WS}/scheduled-posts/{{sp_id}}", response_model=ScheduledPostOut)
async def cancel_scheduled(
    workspace_id: uuid.UUID, sp_id: uuid.UUID, user: CurrentUser, svc: Publishing, meta: Meta
) -> ScheduledPostOut:
    return ScheduledPostOut.model_validate(await svc.cancel(user, workspace_id, sp_id, meta))


@router.post(f"{WS}/scheduled-posts/{{sp_id}}/publish-now", response_model=ScheduledPostOut)
async def publish_now(
    workspace_id: uuid.UUID, sp_id: uuid.UUID, user: CurrentUser, svc: Publishing, meta: Meta
) -> ScheduledPostOut:
    return ScheduledPostOut.model_validate(await svc.publish_now(user, workspace_id, sp_id, meta))


@router.post(f"{WS}/scheduled-posts/{{sp_id}}/retry", response_model=ScheduledPostOut)
async def retry(
    workspace_id: uuid.UUID, sp_id: uuid.UUID, user: CurrentUser, svc: Publishing, meta: Meta
) -> ScheduledPostOut:
    """Retry a failed or dead-lettered post. Every publish check runs again."""
    return ScheduledPostOut.model_validate(await svc.retry(user, workspace_id, sp_id, meta))


@router.post(f"{SETS}/{{set_id}}/schedule", response_model=ScheduleResult)
async def schedule_set(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    user: CurrentUser,
    svc: Publishing,
    meta: Meta,
    body: ScheduleSetIn | None = None,
) -> ScheduleResult:
    """Place every approved account without a live slot, staggered. Not a transaction across
    accounts: the per-account result says what landed (an unapproved account is
    ``approval_required``)."""
    results = await svc.schedule_set(
        user, workspace_id, set_id, meta, scheduled_for=body.scheduled_for if body else None
    )
    return ScheduleResult(results=results)


@router.post(f"{SETS}/{{set_id}}/publish-now", response_model=ScheduleResult)
async def publish_set_now(
    workspace_id: uuid.UUID, set_id: uuid.UUID, user: CurrentUser, svc: Publishing, meta: Meta
) -> ScheduleResult:
    """Publish every approved account now, keeping the stagger between them."""
    results = await svc.schedule_set(
        user, workspace_id, set_id, meta, scheduled_for=None, now_mode=True
    )
    return ScheduleResult(results=results)


@router.get(f"{SETS}/{{set_id}}/status", response_model=SetStatusOut)
async def set_status(
    workspace_id: uuid.UUID, set_id: uuid.UUID, user: CurrentUser, svc: Publishing, meta: Meta
) -> SetStatusOut:
    return SetStatusOut.model_validate(await svc.set_status(user, workspace_id, set_id, meta))


@router.get(f"{WS}/published-posts", response_model=PublishedPostList)
async def list_published_posts(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Publishing,
    meta: Meta,
    account_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    post_set_id: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> PublishedPostList:
    rows = await svc.list_published(
        user,
        workspace_id,
        meta,
        account_id=account_id,
        group_id=group_id,
        post_set_id=post_set_id,
        since=since,
        limit=limit,
    )
    return PublishedPostList(data=[PublishedPostOut.model_validate(r) for r in rows])


@router.get(f"{WS}/calendar", response_model=ScheduledPostList)
async def calendar(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Publishing,
    meta: Meta,
    frm: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    group_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
) -> ScheduledPostList:
    """Scheduled and published posts in a window (default: this week and next), with the
    group each went out through for colour coding."""
    start = frm or (datetime.now(UTC) - timedelta(days=7))
    end = to or (start + timedelta(days=21))
    rows = await svc.calendar(
        user, workspace_id, meta, frm=start, to=end, group_id=group_id, account_id=account_id
    )
    return ScheduledPostList(data=[ScheduledPostOut.model_validate(r) for r in rows])


class PostingSlotOut(_Out):
    social_account_id: uuid.UUID
    account_nickname: str
    platform: str
    account_group_id: uuid.UUID | None
    at: datetime
    taken: bool = Field(description="A queued or published post is within the spacing")
    source: str = Field(description="account, group or workspace: whose times these are")


class PostingSlotList(BaseModel):
    data: list[PostingSlotOut]


@router.get(f"{WS}/posting-slots", response_model=PostingSlotList)
async def posting_slots(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Publishing,
    meta: Meta,
    account_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    days: Annotated[int, Query(ge=1, le=14)] = 7,
) -> PostingSlotList:
    """Upcoming posting times per account (docs/04 §4.10). Approving without a requested
    time places each account at its next free posting time, plus its stagger offset."""
    rows = await svc.posting_slots(
        user, workspace_id, meta, account_id=account_id, group_id=group_id, days=days
    )
    return PostingSlotList(data=[PostingSlotOut.model_validate(r) for r in rows])


@router.get(f"{WS}/dead-letters", response_model=DeadLetterList)
async def list_dead_letters(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Publishing,
    meta: Meta,
    account_id: uuid.UUID | None = None,
    include_resolved: bool = False,
) -> DeadLetterList:
    rows = await svc.list_dead_letters(
        user, workspace_id, meta, account_id=account_id, include_resolved=include_resolved
    )
    return DeadLetterList(data=[DeadLetterOut.model_validate(r) for r in rows])


@router.post(f"{WS}/dead-letters/{{dl_id}}/resolve", response_model=DeadLetterOut)
async def resolve_dead_letter(
    workspace_id: uuid.UUID, dl_id: uuid.UUID, user: CurrentUser, svc: Publishing, meta: Meta
) -> DeadLetterOut:
    return DeadLetterOut.model_validate(
        await svc.resolve_dead_letter(user, workspace_id, dl_id, meta)
    )
