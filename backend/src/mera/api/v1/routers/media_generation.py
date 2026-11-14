"""Media generation (P2-M5, docs/04 §4.7): estimate, request, jobs, cancel, spend, limits.

Registered before the media router so ``/media/jobs`` and ``/media/generate`` are not
taken for an asset id.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import CurrentUser, MediaGen, Meta
from mera.api.v1.routers.posts import SETS, WS

router = APIRouter(tags=["media"])

Kind = Literal["image", "video"]
Aspect = Literal["square", "landscape", "portrait"]
Quality = Literal["low", "medium", "high"]


class EstimateIn(BaseModel):
    kind: Kind = "image"
    aspect: Aspect = "square"
    quality: Quality = "medium"
    count: int = Field(default=1, ge=1, le=10)


class EstimateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    count: int
    cost_usd: Decimal
    seconds: int = Field(description="Expected time for one image")
    request_ceiling_usd: Decimal
    month_spent_usd: Decimal
    monthly_budget_usd: Decimal
    needs_second_factor: bool = Field(description="Above the ceiling, or video")
    over_budget: bool = Field(description="Would exceed this month's budget: refused")
    provider: str


class GenerateIn(EstimateIn):
    prompt: str | None = Field(
        default=None, max_length=1500, description="Omit with a variant: the Media agent plans"
    )
    post_set_id: uuid.UUID | None = None
    post_variant_id: uuid.UUID | None = None
    instruction: str | None = Field(default=None, max_length=500)
    attach: bool = Field(
        default=False, description="Replace the variant's images with the result (new version)"
    )


class RegenerateIn(BaseModel):
    instruction: str | None = Field(default=None, max_length=500)
    quality: Quality = "medium"


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    state: str = Field(description="queued, running, succeeded, failed, cancelled, timed_out")
    prompt: str
    alt_text: str | None
    provider: str
    model: str | None
    post_set_id: uuid.UUID | None
    post_variant_id: uuid.UUID | None
    batch_id: uuid.UUID | None
    slot: int
    estimated_cost_usd: Decimal
    actual_cost_usd: Decimal | None
    attempt_count: int
    error: str | None
    result_asset_id: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None


class JobList(BaseModel):
    data: list[JobOut]


class SpendOut(BaseModel):
    month_start: datetime
    month_spent_usd: Decimal
    monthly_budget_usd: Decimal
    request_ceiling_usd: Decimal
    jobs: dict[str, int]
    provider: str | None
    video_enabled: bool


class LimitsIn(BaseModel):
    request_ceiling_usd: Decimal | None = Field(default=None, ge=0, le=1000)
    monthly_budget_usd: Decimal | None = Field(default=None, ge=0, le=100000)


def _jobs(rows: list[Any]) -> JobList:
    return JobList(data=[JobOut.model_validate(r) for r in rows])


@router.post(f"{WS}/media/generate/estimate", response_model=EstimateOut)
async def estimate_generation(
    workspace_id: uuid.UUID, body: EstimateIn, user: CurrentUser, svc: MediaGen, meta: Meta
) -> EstimateOut:
    """The cost and expected time, before anything is spent, against the ceilings."""
    return EstimateOut.model_validate(
        await svc.estimate(
            user,
            workspace_id,
            meta,
            kind=body.kind,
            aspect=body.aspect,
            quality=body.quality,
            count=body.count,
        )
    )


@router.post(f"{WS}/media/generate", response_model=JobList, status_code=status.HTTP_202_ACCEPTED)
async def generate_media(
    workspace_id: uuid.UUID, body: GenerateIn, user: CurrentUser, svc: MediaGen, meta: Meta
) -> JobList:
    """Queue images. Over the monthly budget: refused. Over the per-request ceiling (or
    video): needs a second factor (``video_generate``). Refusals make no provider call."""
    return _jobs(
        await svc.generate(
            user,
            workspace_id,
            meta,
            kind=body.kind,
            prompt=body.prompt,
            aspect=body.aspect,
            quality=body.quality,
            count=body.count,
            post_set_id=body.post_set_id,
            post_variant_id=body.post_variant_id,
            instruction=body.instruction,
            attach=body.attach,
        )
    )


@router.get(f"{WS}/media/jobs", response_model=JobList)
async def list_media_jobs(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: MediaGen,
    meta: Meta,
    state: Annotated[str | None, Query(max_length=16)] = None,
    post_set_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> JobList:
    return _jobs(
        await svc.list_jobs(
            user, workspace_id, meta, state=state, post_set_id=post_set_id, limit=limit
        )
    )


@router.get(f"{WS}/media/jobs/{{job_id}}", response_model=JobOut)
async def get_media_job(
    workspace_id: uuid.UUID, job_id: uuid.UUID, user: CurrentUser, svc: MediaGen, meta: Meta
) -> JobOut:
    return JobOut.model_validate(await svc.get_job(user, workspace_id, meta, job_id))


@router.post(f"{WS}/media/jobs/{{job_id}}/cancel", response_model=JobOut)
async def cancel_media_job(
    workspace_id: uuid.UUID, job_id: uuid.UUID, user: CurrentUser, svc: MediaGen, meta: Meta
) -> JobOut:
    return JobOut.model_validate(await svc.cancel(user, workspace_id, meta, job_id))


@router.get(f"{WS}/media/spend", response_model=SpendOut)
async def media_spend(
    workspace_id: uuid.UUID, user: CurrentUser, svc: MediaGen, meta: Meta
) -> SpendOut:
    """This month's generation spend (UTC calendar month) against the budget."""
    return SpendOut(**await svc.spend(user, workspace_id, meta))


@router.put(f"{WS}/media/limits", response_model=SpendOut)
async def set_media_limits(
    workspace_id: uuid.UUID, body: LimitsIn, user: CurrentUser, svc: MediaGen, meta: Meta
) -> SpendOut:
    """The per-request ceiling and the monthly budget (Admins)."""
    return SpendOut(
        **await svc.set_limits(
            user,
            workspace_id,
            meta,
            request_ceiling_usd=body.request_ceiling_usd,
            monthly_budget_usd=body.monthly_budget_usd,
        )
    )


@router.post(
    f"{SETS}/{{set_id}}/variants/{{variant_id}}/media/regenerate",
    response_model=JobList,
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_variant_media(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    variant_id: uuid.UUID,
    body: RegenerateIn,
    user: CurrentUser,
    svc: MediaGen,
    meta: Meta,
) -> JobList:
    """New images for this account's post (as many as it has), planned by the Media agent
    and attached as a new version when done: that account's approval is asked again."""
    return _jobs(
        await svc.regenerate(
            user,
            workspace_id,
            meta,
            set_id,
            variant_id,
            instruction=body.instruction,
            quality=body.quality,
        )
    )
