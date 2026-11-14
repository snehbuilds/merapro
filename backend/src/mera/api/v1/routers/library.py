"""The content library (P2-M1): the hashtag registry (docs/06 §4.4) and post templates
(docs/04 §4.5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import CurrentUser, Hashtags, Meta, Templates
from mera.application.services.template_service import TemplateFields

router = APIRouter(tags=["library"])
WS = "/workspaces/{workspace_id}"
Status = Literal["allowed", "preferred", "banned"]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HashtagOut(_Out):
    id: uuid.UUID
    tag: str = Field(description="Lower case, without #")
    status: str = Field(description="allowed, preferred or banned")
    aliases: list[str]
    first_used_at: datetime | None
    updated_at: datetime


class HashtagList(BaseModel):
    data: list[HashtagOut]


class HashtagIn(BaseModel):
    tag: str = Field(min_length=1, max_length=101, description="With or without #")
    status: Status
    aliases: list[str] | None = Field(default=None, max_length=10)


@router.get(f"{WS}/hashtags", response_model=HashtagList)
async def list_hashtags(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Hashtags,
    meta: Meta,
    status_filter: Annotated[Status | None, Query(alias="status")] = None,
) -> HashtagList:
    rows = await svc.list_tags(user, workspace_id, meta, status=status_filter)
    return HashtagList(data=[HashtagOut.model_validate(r) for r in rows])


class HashtagStatOut(_Out):
    social_account_id: uuid.UUID
    tag: str
    platform: str
    post_count: int
    avg_engagement: float
    lift: float | None = Field(
        description="Average engagement relative to the account's median post (1.0 = typical); "
        "null below the sample minimum"
    )
    evidence: Literal["measured", "not_enough_data"]
    computed_at: datetime


class HashtagStatList(BaseModel):
    data: list[HashtagStatOut]
    min_samples: int = Field(description="Posts a tag needs on an account before it has a lift")


@router.get(f"{WS}/hashtags/stats", response_model=HashtagStatList)
async def hashtag_stats(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Hashtags,
    meta: Meta,
    social_account_id: uuid.UUID | None = None,
    tag: str | None = Query(default=None, max_length=101),
    limit: int = Query(default=200, ge=1, le=1000),
) -> HashtagStatList:
    """How each tag did on each account over the last ninety days, recomputed daily
    (P3-M1.1). Only accounts you may read are included."""
    from mera.domain.policies.hashtag_lift import MIN_HASHTAG_SAMPLES

    rows = await svc.stats(
        user, workspace_id, meta, social_account_id=social_account_id, tag=tag, limit=limit
    )
    return HashtagStatList(
        data=[HashtagStatOut.model_validate(r) for r in rows], min_samples=MIN_HASHTAG_SAMPLES
    )


@router.put(f"{WS}/hashtags", response_model=HashtagOut)
async def put_hashtag(
    workspace_id: uuid.UUID, body: HashtagIn, user: CurrentUser, svc: Hashtags, meta: Meta
) -> HashtagOut:
    """Add a tag or change its status. A banned tag blocks drafts that use it, blocks a set
    from going for approval, and stops a queued post that uses it at publish time."""
    view = await svc.upsert(
        user, workspace_id, meta, tag=body.tag, status=body.status, aliases=body.aliases
    )
    return HashtagOut.model_validate(view)


@router.delete(f"{WS}/hashtags/{{hashtag_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hashtag(
    workspace_id: uuid.UUID, hashtag_id: uuid.UUID, user: CurrentUser, svc: Hashtags, meta: Meta
) -> Response:
    await svc.delete(user, workspace_id, hashtag_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- templates


class TemplateOut(_Out):
    id: uuid.UUID
    name: str
    description: str | None
    topic: str = Field(description="The brief a new post set starts from")
    outline: str | None = Field(description="Structure the Writer follows")
    hashtags: list[str]
    target_group_ids: list[uuid.UUID]
    use_count: int
    updated_at: datetime


class TemplateList(BaseModel):
    data: list[TemplateOut]


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    topic: str = Field(min_length=1, max_length=2000)
    outline: str | None = Field(default=None, max_length=4000)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    target_group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=25)


class TemplatePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    topic: str | None = Field(default=None, min_length=1, max_length=2000)
    outline: str | None = Field(default=None, max_length=4000)
    hashtags: list[str] | None = Field(default=None, max_length=30)
    target_group_ids: list[uuid.UUID] | None = Field(default=None, max_length=25)


@router.get(f"{WS}/post-templates", response_model=TemplateList)
async def list_templates(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Templates, meta: Meta
) -> TemplateList:
    rows = await svc.list_templates(user, workspace_id, meta)
    return TemplateList(data=[TemplateOut.model_validate(r) for r in rows])


@router.post(f"{WS}/post-templates", response_model=TemplateOut, status_code=201)
async def create_template(
    workspace_id: uuid.UUID, body: TemplateIn, user: CurrentUser, svc: Templates, meta: Meta
) -> TemplateOut:
    view = await svc.create(user, workspace_id, meta, TemplateFields(**body.model_dump()))
    return TemplateOut.model_validate(view)


@router.patch(f"{WS}/post-templates/{{template_id}}", response_model=TemplateOut)
async def update_template(
    workspace_id: uuid.UUID,
    template_id: uuid.UUID,
    body: TemplatePatch,
    user: CurrentUser,
    svc: Templates,
    meta: Meta,
) -> TemplateOut:
    fields = TemplateFields(**body.model_dump(exclude_unset=True))
    view = await svc.update(user, workspace_id, template_id, meta, fields)
    return TemplateOut.model_validate(view)


@router.delete(f"{WS}/post-templates/{{template_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    workspace_id: uuid.UUID, template_id: uuid.UUID, user: CurrentUser, svc: Templates, meta: Meta
) -> Response:
    await svc.delete(user, workspace_id, template_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
