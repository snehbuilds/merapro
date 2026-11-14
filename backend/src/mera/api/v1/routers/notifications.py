"""Digest preferences and outbound webhooks (P3-M4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import CurrentUser, Meta
from mera.application.digests import DigestPreferences
from mera.application.services.webhook_service import EVENT_TYPES, WebhookService

router = APIRouter(tags=["notifications"])
WS = "/workspaces/{workspace_id}"


def _webhooks(request: Request) -> WebhookService:
    svc: WebhookService = request.app.state.webhook_service
    return svc


def _prefs(request: Request) -> DigestPreferences:
    svc: DigestPreferences = request.app.state.digest_preferences
    return svc


Webhooks = Annotated[WebhookService, Depends(_webhooks)]
Prefs = Annotated[DigestPreferences, Depends(_prefs)]


class DigestIn(BaseModel):
    mode: str = Field(description="immediate, digest or both")
    time: str = Field(default="08:30", description="Local HH:MM (the workspace's time zone)")


@router.put(f"{WS}/members/me/digest")
async def set_digest(
    workspace_id: uuid.UUID, body: DigestIn, user: CurrentUser, prefs: Prefs, meta: Meta
) -> dict[str, str]:
    """How you receive approval requests: each card at once, one daily digest, or both."""
    return await prefs.set(user, workspace_id, meta, mode=body.mode, at=body.time)


class WebhookIn(BaseModel):
    url: str = Field(max_length=2048)
    events: list[str] = Field(min_length=1, max_length=30)
    description: str | None = Field(default=None, max_length=200)


class WebhookPatch(BaseModel):
    url: str | None = Field(default=None, max_length=2048)
    events: list[str] | None = Field(default=None, max_length=30)
    active: bool | None = None
    rotate_secret: bool = False


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    description: str | None
    events: list[str]
    active: bool
    consecutive_failures: int
    disabled_reason: str | None
    created_at: datetime
    secret: str | None = Field(default=None, description="Shown once: on creation and rotation")


class DeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    state: str
    attempts: int
    last_status: int | None
    last_error: str | None
    next_attempt_at: datetime
    delivered_at: datetime | None
    created_at: datetime


@router.get(f"{WS}/webhooks")
async def list_webhooks(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Webhooks, meta: Meta
) -> dict[str, Any]:
    rows = await svc.list_endpoints(user, workspace_id, meta)
    return {"data": [WebhookOut.model_validate(r) for r in rows], "event_types": list(EVENT_TYPES)}


@router.post(f"{WS}/webhooks", response_model=WebhookOut, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    workspace_id: uuid.UUID, body: WebhookIn, user: CurrentUser, svc: Webhooks, meta: Meta
) -> WebhookOut:
    """Signed deliveries of the chosen events to a public https URL (Admins)."""
    ep, secret = await svc.create(
        user, workspace_id, meta, url=body.url, events=body.events, description=body.description
    )
    out = WebhookOut.model_validate(ep)
    out.secret = secret
    return out


@router.patch(f"{WS}/webhooks/{{webhook_id}}", response_model=WebhookOut)
async def update_webhook(
    workspace_id: uuid.UUID,
    webhook_id: uuid.UUID,
    body: WebhookPatch,
    user: CurrentUser,
    svc: Webhooks,
    meta: Meta,
) -> WebhookOut:
    ep, secret = await svc.update(
        user,
        workspace_id,
        meta,
        webhook_id,
        url=body.url,
        events=body.events,
        active=body.active,
        rotate_secret=body.rotate_secret,
    )
    out = WebhookOut.model_validate(ep)
    out.secret = secret
    return out


@router.delete(f"{WS}/webhooks/{{webhook_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    workspace_id: uuid.UUID, webhook_id: uuid.UUID, user: CurrentUser, svc: Webhooks, meta: Meta
) -> Response:
    await svc.delete(user, workspace_id, meta, webhook_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(f"{WS}/webhooks/{{webhook_id}}/deliveries")
async def list_deliveries(
    workspace_id: uuid.UUID, webhook_id: uuid.UUID, user: CurrentUser, svc: Webhooks, meta: Meta
) -> dict[str, list[DeliveryOut]]:
    rows = await svc.deliveries(user, workspace_id, meta, webhook_id)
    return {"data": [DeliveryOut.model_validate(r) for r in rows]}


@router.post(f"{WS}/webhooks/{{webhook_id}}/test", response_model=DeliveryOut)
async def test_webhook(
    workspace_id: uuid.UUID, webhook_id: uuid.UUID, user: CurrentUser, svc: Webhooks, meta: Meta
) -> DeliveryOut:
    """Send a signed ``ping`` now and show what came back."""
    return DeliveryOut.model_validate(await svc.test(user, workspace_id, meta, webhook_id))
