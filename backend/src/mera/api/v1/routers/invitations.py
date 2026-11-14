"""POST /api/v1/invitations/accept: outside {ws} because the invitee is not yet a member
and the workspace is discovered from the token."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from mera.api.deps import Access, HumanUser, Meta

router = APIRouter(prefix="/invitations", tags=["members"])


class AcceptIn(BaseModel):
    token: str = Field(min_length=10, max_length=128)


class AcceptOut(BaseModel):
    workspace_id: uuid.UUID


@router.post("/accept", response_model=AcceptOut)
async def accept_invitation(body: AcceptIn, user: HumanUser, svc: Access, meta: Meta) -> AcceptOut:
    return AcceptOut(workspace_id=await svc.accept_invitation(user, body.token, meta))
