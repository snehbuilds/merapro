"""A/B tests (P3-M2, docs/04 §4.5)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from mera.api.deps import CurrentUser, Meta
from mera.api.v1.routers.posts import SETS, PostSetOut, _set
from mera.application.services.ab_service import AbService

router = APIRouter(tags=["posts"])


def _ab(request: Request) -> AbService:
    svc: AbService = request.app.state.ab_service
    return svc


Ab = Annotated[AbService, Depends(_ab)]


class AbIn(BaseModel):
    hypothesis: str = Field(min_length=3, max_length=300, description="What version B tests")
    body: str | None = Field(
        default=None,
        max_length=10000,
        description="Write B yourself; omitted, the Writer drafts it",
    )


@router.post(
    f"{SETS}/{{set_id}}/variants/{{variant_id}}/ab",
    response_model=PostSetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_ab(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    variant_id: uuid.UUID,
    body: AbIn,
    user: CurrentUser,
    svc: Ab,
    meta: Meta,
) -> PostSetOut:
    """Version B of one account's post: its own post set, approved and placed like any
    other, linked to A for comparison."""
    view = await svc.create_b(
        user, workspace_id, meta, set_id, variant_id, hypothesis=body.hypothesis, body=body.body
    )
    return _set(view)


@router.get(f"{SETS}/{{set_id}}/ab")
async def ab_results(
    workspace_id: uuid.UUID, set_id: uuid.UUID, user: CurrentUser, svc: Ab, meta: Meta
) -> dict[str, Any]:
    """Engagement rate of A and B; a winner only with enough impressions on both and a
    clear difference (``outcome``: too_early, no_difference, a, b, not_reported)."""
    return await svc.results(user, workspace_id, meta, set_id)


class CloneIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(min_length=1, max_length=48)


@router.post("/workspaces/{workspace_id}/clone", status_code=status.HTTP_201_CREATED)
async def clone_workspace(
    workspace_id: uuid.UUID, body: CloneIn, user: CurrentUser, request: Request, meta: Meta
) -> dict[str, Any]:
    """A new workspace set up like this one: settings, voices, pillars, sources, policies,
    group shapes, hashtags and templates. Never accounts, credentials, content, history or
    other members (Owners only)."""
    view = await request.app.state.clone_service.clone(
        user, workspace_id, meta, name=body.name, slug=body.slug
    )
    return {"id": str(view.id), "slug": view.slug, "name": view.name}


class EvergreenIn(BaseModel):
    evergreen: bool
    cooldown_days: int = Field(default=60, ge=7, le=365)


@router.put(f"{SETS}/{{set_id}}/evergreen")
async def set_evergreen(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    body: EvergreenIn,
    user: CurrentUser,
    request: Request,
    meta: Meta,
) -> dict[str, Any]:
    """After the cool-down, a fresh draft of this post comes back for approval (P3-M5.3)."""
    out: dict[str, Any] = await request.app.state.evergreen_service.mark(
        user, workspace_id, meta, set_id, evergreen=body.evergreen, cooldown_days=body.cooldown_days
    )
    return out
