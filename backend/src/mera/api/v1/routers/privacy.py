"""Data rights, step-up and security alerts (P3-M7, docs/09 §9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from mera.api.deps import CurrentUser, HumanUser, Meta, OAuth
from mera.application.services.principals import load_user_principal
from mera.application.services.privacy_service import PrivacyService
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.infrastructure.db.models import SecurityAlert

router = APIRouter(tags=["privacy"])
WS = "/workspaces/{workspace_id}"


def privacy_service(request: Request) -> PrivacyService:
    svc: PrivacyService = request.app.state.privacy_service
    return svc


Privacy = Annotated[PrivacyService, Depends(privacy_service)]


class ProofIn(BaseModel):
    proof: str = Field(
        min_length=1,
        max_length=1024,
        description=(
            "Your authenticator code if two-step sign-in is on, else your password; an "
            "account that signs in only with a provider gives a step-up proof"
        ),
    )


class StepUpIn(BaseModel):
    code: str = Field(min_length=10, max_length=256)
    code_verifier: str = Field(min_length=43, max_length=128)


class StepUpOut(BaseModel):
    proof: str = Field(description="Use as ``proof`` once, within a few minutes")
    expires_at: datetime


class ExportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    state: str
    byte_size: int | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None


@router.get("/auth/me/export")
async def export_me(user: HumanUser, svc: Privacy) -> dict[str, Any]:
    """Everything Mera holds about you, as JSON (docs/09 §9.3, access)."""
    return await svc.export_me(user)


@router.delete("/auth/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(body: ProofIn, user: HumanUser, svc: Privacy, meta: Meta) -> Response:
    """Delete your account: signed out everywhere now, personal data erased after 30 days.
    Refused while you are a workspace's only Owner."""
    await svc.delete_me(user, body.proof, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/auth/step-up/exchange", response_model=StepUpOut)
async def step_up(body: StepUpIn, user: HumanUser, oauth: OAuth) -> StepUpOut:
    """An account that signs in only with a provider confirms it is them: sign in with the
    provider again (``/auth/oauth/{provider}/authorize``) and exchange the code here for a
    proof, instead of a new session."""
    proof, expires = await oauth.step_up(
        user.user_id, code=body.code, code_verifier=body.code_verifier
    )
    return StepUpOut(proof=proof, expires_at=expires)


@router.post(f"{WS}/exports", response_model=ExportOut, status_code=status.HTTP_202_ACCEPTED)
async def request_export(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Privacy, meta: Meta
) -> ExportOut:
    """The whole workspace as JSON plus its media, in one zip (Owners and Admins)."""
    return ExportOut.model_validate(await svc.request_export(user, workspace_id, meta))


@router.get(f"{WS}/exports")
async def list_exports(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Privacy, meta: Meta
) -> dict[str, list[ExportOut]]:
    rows = await svc.list_exports(user, workspace_id, meta)
    return {"data": [ExportOut.model_validate(r) for r in rows]}


@router.get(f"{WS}/exports/{{export_id}}/link")
async def export_link(
    workspace_id: uuid.UUID, export_id: uuid.UUID, user: CurrentUser, svc: Privacy, meta: Meta
) -> dict[str, str]:
    """A download link valid for fifteen minutes."""
    return {"url": await svc.export_link(user, workspace_id, meta, export_id)}


@router.delete(
    f"{WS}/inbox/comments/{{comment_id}}/author-data", status_code=status.HTTP_204_NO_CONTENT
)
async def erase_comment_author(
    workspace_id: uuid.UUID, comment_id: uuid.UUID, user: CurrentUser, svc: Privacy, meta: Meta
) -> Response:
    """A commenter asked to be forgotten: their handle and id go, the text stays."""
    await svc.erase_comment_author(user, workspace_id, meta, comment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(f"{WS}/security-alerts")
async def security_alerts(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Privacy, meta: Meta
) -> dict[str, Any]:
    """Alerts the scanner raised for this workspace (Owners and Admins)."""
    async with svc._tx(user, workspace_id, meta) as s:
        principal = await load_user_principal(s, user, workspace_id)
        rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
        rows = (
            await s.execute(
                select(SecurityAlert).order_by(SecurityAlert.created_at.desc()).limit(100)
            )
        ).scalars()
        return {
            "data": [
                {
                    "id": str(r.id),
                    "kind": r.kind,
                    "user_id": str(r.user_id) if r.user_id else None,
                    "recipients": r.recipients,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        }
