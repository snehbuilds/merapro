"""Sources, content items, Trending and content pillars (docs/04 §4.4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import Content, CurrentUser, Meta, Sources
from mera.application.services.content_service import PillarChanges
from mera.application.services.source_service import SourceChanges, SourceInput
from mera.domain.value_objects.content import ItemState, SourceStatus, SourceType

router = APIRouter(tags=["content"])
WS = "/workspaces/{workspace_id}"


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- sources


class SourceOut(_Out):
    id: uuid.UUID
    source_type: SourceType
    name: str
    url: str | None
    include_keywords: list[str]
    exclude_keywords: list[str]
    language: str | None
    fetch_interval_seconds: int
    weight: float
    status: SourceStatus
    group_ids: list[uuid.UUID] = Field(description="Groups this source feeds; empty = every group")
    last_fetched_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    next_fetch_at: datetime | None
    items_7d: int
    created_at: datetime


class SourceList(BaseModel):
    data: list[SourceOut]


class SourceIn(BaseModel):
    source_type: SourceType = SourceType.RSS
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(
        default="", max_length=2048, description="Feed URL (RSS or Atom); unused for mcp"
    )
    include_keywords: list[str] = Field(default_factory=list, max_length=50)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=50)
    language: str | None = Field(default=None, max_length=3, description="Keep only this language")
    fetch_interval_seconds: int = Field(default=3600, ge=900, le=86_400)
    weight: float = Field(default=1.0, ge=0, le=4)
    group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    mcp_server_id: uuid.UUID | None = Field(default=None, description="For source_type mcp")
    mcp_tool: str | None = Field(default=None, max_length=128)
    mcp_arguments: dict[str, Any] | None = None


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=8, max_length=2048)
    include_keywords: list[str] | None = Field(default=None, max_length=50)
    exclude_keywords: list[str] | None = Field(default=None, max_length=50)
    language: str | None = Field(default=None, max_length=3, description="Empty string clears it")
    fetch_interval_seconds: int | None = Field(default=None, ge=900, le=86_400)
    weight: float | None = Field(default=None, ge=0, le=4)
    status: SourceStatus | None = Field(default=None, description="active (resume) or paused")


class SourceGroupsIn(BaseModel):
    group_ids: list[uuid.UUID] = Field(max_length=50)


class FetchQueuedOut(BaseModel):
    queued_at: datetime
    detail: str = "The fetch runs within a minute."


class SourceHealthOut(_Out):
    source_id: uuid.UUID
    status: SourceStatus
    last_fetched_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    last_http_status: int | None
    last_yield: int | None
    consecutive_failures: int
    failing_since: datetime | None
    next_fetch_at: datetime | None
    retrying: bool = Field(description="False once retries stop (48 hours of failures) or paused")
    items_7d: int
    filtered_7d: int


class FeedTestIn(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class FeedTestItem(_Out):
    title: str
    url: str
    published_at: datetime | None


class FeedTestOut(_Out):
    ok: bool
    title: str | None
    item_count: int
    items: list[FeedTestItem]
    error_code: str | None
    error: str | None


@router.get(f"{WS}/sources", response_model=SourceList)
async def list_sources(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Sources,
    meta: Meta,
    group_id: uuid.UUID | None = None,
    source_status: Annotated[SourceStatus | None, Query(alias="status")] = None,
    source_type: Annotated[SourceType | None, Query(alias="type")] = None,
) -> SourceList:
    rows = await svc.list_sources(
        user, workspace_id, meta, group_id=group_id, status=source_status, source_type=source_type
    )
    return SourceList(data=[SourceOut.model_validate(r) for r in rows])


@router.post(f"{WS}/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(
    workspace_id: uuid.UUID, body: SourceIn, user: CurrentUser, svc: Sources, meta: Meta
) -> SourceOut:
    view = await svc.create(user, workspace_id, meta, SourceInput(**body.model_dump()))
    return SourceOut.model_validate(view)


@router.post(f"{WS}/sources/test", response_model=FeedTestOut)
async def test_feed(
    workspace_id: uuid.UUID, body: FeedTestIn, user: CurrentUser, svc: Sources, meta: Meta
) -> FeedTestOut:
    """Fetch and parse a feed without saving anything (the "Test feed" button)."""
    return FeedTestOut.model_validate(await svc.test_feed(user, workspace_id, meta, body.url))


@router.get(f"{WS}/sources/{{source_id}}", response_model=SourceOut)
async def get_source(
    workspace_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, svc: Sources, meta: Meta
) -> SourceOut:
    return SourceOut.model_validate(await svc.get(user, workspace_id, source_id, meta))


@router.patch(f"{WS}/sources/{{source_id}}", response_model=SourceOut)
async def update_source(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    body: SourcePatch,
    user: CurrentUser,
    svc: Sources,
    meta: Meta,
) -> SourceOut:
    data = body.model_dump(exclude_unset=True)
    lang = data.pop("language", None)
    changes = SourceChanges(
        **data,
        language=lang or None,
        clear_language="language" in body.model_fields_set and not lang,
    )
    return SourceOut.model_validate(await svc.update(user, workspace_id, source_id, meta, changes))


@router.delete(f"{WS}/sources/{{source_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    workspace_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, svc: Sources, meta: Meta
) -> Response:
    await svc.delete(user, workspace_id, source_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(f"{WS}/sources/{{source_id}}/groups", response_model=SourceOut)
async def set_source_groups(
    workspace_id: uuid.UUID,
    source_id: uuid.UUID,
    body: SourceGroupsIn,
    user: CurrentUser,
    svc: Sources,
    meta: Meta,
) -> SourceOut:
    """Which groups this source feeds. An empty list feeds every group."""
    view = await svc.set_groups(user, workspace_id, source_id, meta, body.group_ids)
    return SourceOut.model_validate(view)


@router.post(
    f"{WS}/sources/{{source_id}}/fetch",
    response_model=FetchQueuedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def fetch_source(
    workspace_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, svc: Sources, meta: Meta
) -> FetchQueuedOut:
    return FetchQueuedOut(queued_at=await svc.fetch_now(user, workspace_id, source_id, meta))


@router.get(f"{WS}/sources/{{source_id}}/health", response_model=SourceHealthOut)
async def source_health(
    workspace_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, svc: Sources, meta: Meta
) -> SourceHealthOut:
    return SourceHealthOut.model_validate(await svc.health(user, workspace_id, source_id, meta))


# ------------------------------------------------------------- content items


class DuplicateRefOut(_Out):
    id: uuid.UUID
    title: str
    source_name: str | None
    similarity: float | None
    fetched_at: datetime


class GroupScoreOut(_Out):
    account_group_id: uuid.UUID
    group_name: str
    relevance: float
    pillar_match: float | None
    freshness: float | None
    source_weight: float | None
    best_pillar: str | None
    pillar_scores: dict[str, float]


class ContentItemOut(_Out):
    id: uuid.UUID
    title: str
    url: str
    source_id: uuid.UUID | None
    source_name: str | None
    author: str | None
    published_at: datetime | None
    fetched_at: datetime
    summary: str | None
    topics: list[str]
    language: str | None
    relevance: float | None = Field(description="For the requested group, else the maximum")
    freshness: float | None
    engagement_potential: float | None
    engagement_rationale: str | None
    risk_flags: list[str]
    best_pillar: str | None
    scored_group_id: uuid.UUID | None
    state: ItemState
    state_reason: str | None
    og_image_url: str | None
    coverage: int = Field(description="Distinct sources that carried this story")
    dismissed: bool
    duplicate_of: DuplicateRefOut | None = None
    duplicates: list[DuplicateRefOut] = Field(default_factory=list)
    scores: list[GroupScoreOut] = Field(default_factory=list)


class ContentItemList(BaseModel):
    data: list[ContentItemOut]


class ContentItemPage(BaseModel):
    data: list[ContentItemOut]
    next_cursor: str | None


class ContentStatsOut(_Out):
    days: int
    ingested: int
    after_dedupe: int
    above_threshold: int
    filtered: int
    threshold: float
    drafted: int | None = Field(description="Null until post sets exist (M5)")
    published: int | None = Field(description="Null until publishing exists (M8)")


class DismissIn(BaseModel):
    undo: bool = False
    group_id: uuid.UUID | None = Field(default=None, description="The group view acted from")


@router.get(f"{WS}/content-items/trending", response_model=ContentItemList)
async def trending(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Content,
    meta: Meta,
    group_id: uuid.UUID | None = None,
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    min_relevance: Annotated[float, Query(ge=0, le=1)] = 0.0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ContentItemList:
    """Ranked for that group's pillars, or by the workspace-wide maximum without one."""
    rows = await svc.trending(
        user,
        workspace_id,
        meta,
        group_id=group_id,
        hours=hours,
        min_relevance=min_relevance,
        limit=limit,
    )
    return ContentItemList(data=[ContentItemOut.model_validate(r) for r in rows])


@router.get(f"{WS}/content-items/stats", response_model=ContentStatsOut)
async def content_stats(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Content,
    meta: Meta,
    group_id: uuid.UUID | None = None,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> ContentStatsOut:
    view = await svc.stats(user, workspace_id, meta, group_id=group_id, days=days)
    return ContentStatsOut.model_validate(view)


@router.get(f"{WS}/content-items", response_model=ContentItemPage)
async def list_content_items(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Content,
    meta: Meta,
    group_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    topic: Annotated[str | None, Query(max_length=40)] = None,
    state: Annotated[list[ItemState] | None, Query()] = None,
    min_relevance: Annotated[float | None, Query(ge=0, le=1)] = None,
    since: datetime | None = None,
    include_dismissed: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> ContentItemPage:
    page = await svc.list_items(
        user,
        workspace_id,
        meta,
        group_id=group_id,
        source_id=source_id,
        topic=topic,
        states=state or [ItemState.READY, ItemState.DUPLICATE],
        min_relevance=min_relevance,
        since=since,
        include_dismissed=include_dismissed,
        limit=limit,
        cursor=cursor,
    )
    return ContentItemPage(
        data=[ContentItemOut.model_validate(r) for r in page.data], next_cursor=page.next_cursor
    )


@router.get(f"{WS}/content-items/{{item_id}}", response_model=ContentItemOut)
async def get_content_item(
    workspace_id: uuid.UUID, item_id: uuid.UUID, user: CurrentUser, svc: Content, meta: Meta
) -> ContentItemOut:
    """Includes each visible group's score with its parts, and the item's duplicates."""
    return ContentItemOut.model_validate(await svc.get_item(user, workspace_id, item_id, meta))


@router.post(f"{WS}/content-items/{{item_id}}/dismiss", response_model=ContentItemOut)
async def dismiss_content_item(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    user: CurrentUser,
    svc: Content,
    meta: Meta,
    body: DismissIn | None = None,
) -> ContentItemOut:
    body = body or DismissIn()
    view = await svc.dismiss(
        user, workspace_id, item_id, meta, undo=body.undo, group_id=body.group_id
    )
    return ContentItemOut.model_validate(view)


# ------------------------------------------------------------------- pillars


class PillarOut(_Out):
    id: uuid.UUID
    account_group_id: uuid.UUID | None = Field(description="Null = workspace-wide")
    name: str
    description: str | None
    keywords: list[str]
    weight: float
    embedded: bool = Field(description="False until its embedding exists; it does not score yet")
    created_at: datetime
    updated_at: datetime


class PillarList(BaseModel):
    data: list[PillarOut]


class PillarIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    weight: float = Field(default=1.0, ge=0, le=4)
    group_id: uuid.UUID | None = Field(default=None, description="Null = workspace-wide")


class PillarPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500, description="Empty clears it")
    keywords: list[str] | None = Field(default=None, max_length=20)
    weight: float | None = Field(default=None, ge=0, le=4)


@router.get(f"{WS}/content-pillars", response_model=PillarList)
async def list_pillars(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Content,
    meta: Meta,
    group_id: uuid.UUID | None = None,
) -> PillarList:
    """With ``group_id``: the pillars that score that group (its own and workspace-wide)."""
    rows = await svc.list_pillars(user, workspace_id, meta, group_id=group_id)
    return PillarList(data=[PillarOut.model_validate(r) for r in rows])


@router.post(f"{WS}/content-pillars", response_model=PillarOut, status_code=status.HTTP_201_CREATED)
async def create_pillar(
    workspace_id: uuid.UUID, body: PillarIn, user: CurrentUser, svc: Content, meta: Meta
) -> PillarOut:
    view = await svc.create_pillar(
        user,
        workspace_id,
        meta,
        name=body.name,
        description=body.description,
        keywords=body.keywords,
        weight=body.weight,
        group_id=body.group_id,
    )
    return PillarOut.model_validate(view)


@router.patch(f"{WS}/content-pillars/{{pillar_id}}", response_model=PillarOut)
async def update_pillar(
    workspace_id: uuid.UUID,
    pillar_id: uuid.UUID,
    body: PillarPatch,
    user: CurrentUser,
    svc: Content,
    meta: Meta,
) -> PillarOut:
    data: dict[str, Any] = body.model_dump(exclude_unset=True)
    desc = data.pop("description", None)
    changes = PillarChanges(
        **data,
        description=desc or None,
        clear_description="description" in body.model_fields_set and not desc,
    )
    return PillarOut.model_validate(
        await svc.update_pillar(user, workspace_id, pillar_id, meta, changes)
    )


@router.delete(f"{WS}/content-pillars/{{pillar_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pillar(
    workspace_id: uuid.UUID, pillar_id: uuid.UUID, user: CurrentUser, svc: Content, meta: Meta
) -> Response:
    await svc.delete_pillar(user, workspace_id, pillar_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
