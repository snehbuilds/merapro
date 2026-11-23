"""``/api/v1/workspaces`` (docs/04 §4.1): the Sprint 2 subset.

Tenant-scoped resources are always nested under ``/workspaces/{workspace_id}``; there is no
implicit current workspace (docs/04 §1).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, EmailStr, Field

from mera.api.deps import Access, CurrentUser, Meta, Security, Workspaces
from mera.application.services.principals import load_user_principal
from mera.application.services.workspace_service import MemberView, WorkspaceView
from mera.application.settings_model import LevelSettings
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.value_objects.role import Role
from mera.domain.value_objects.workspace import WorkspaceType
from mera.infrastructure.db.models import Workspace

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=48)
    workspace_type: WorkspaceType = WorkspaceType.TEAM
    timezone: str = Field(default="UTC", max_length=64)


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    workspace_type: str
    timezone: str
    role: str
    archived: bool
    settings: dict[str, Any] = Field(default_factory=dict, description="Workspace-level defaults")


class WorkspacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)
    workspace_type: WorkspaceType | None = None
    settings: LevelSettings | None = None


class ScopesIn(BaseModel):
    account_group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    social_account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class ScopesOut(BaseModel):
    restricted: bool
    account_group_ids: list[uuid.UUID]
    social_account_ids: list[uuid.UUID]


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    severity: str
    title: str
    body: str | None
    subject_type: str | None
    subject_id: uuid.UUID | None
    social_account_id: uuid.UUID | None
    created_at: datetime
    read_at: datetime | None


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=32)
    expires_in_days: int | None = None


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    scopes: list[str]
    created_by: uuid.UUID
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreatedOut(BaseModel):
    key: ApiKeyOut
    secret: str = Field(description="Shown exactly once. Store it now.")


class InvitationIn(BaseModel):
    email: EmailStr
    role: Role


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    invited_by: uuid.UUID | None
    created_at: datetime
    expires_at: datetime


class InvitationCreatedOut(BaseModel):
    invitation: InvitationOut
    token: str = Field(description="Shown exactly once.")
    accept_url: str
    email_sent: bool = Field(
        default=False,
        description="True when the invitation was emailed; otherwise share accept_url yourself.",
    )


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    scope_restricted: bool
    joined_at: datetime | None
    away_until: datetime | None = None
    delegate_user_id: uuid.UUID | None = None


class AwayIn(BaseModel):
    away_until: datetime | None = Field(description="null: back now")
    delegate_user_id: uuid.UUID | None = Field(
        default=None, description="Who decides in their place meanwhile"
    )


class RoleIn(BaseModel):
    role: Role


class AuditEntryOut(BaseModel):
    id: int
    created_at: datetime
    actor_type: str
    actor_id: uuid.UUID | None
    actor_label: str | None
    actor_name: str | None = None  # the actor's name now, rendered at read time
    subject_content_hash: str | None = None  # which exact text an approval named
    surface: str
    action: str
    subject_type: str | None
    subject_id: uuid.UUID | None
    social_account_id: uuid.UUID | None
    account_group_id: uuid.UUID | None
    before: dict[str, object] | None
    after: dict[str, object] | None
    request_id: str | None


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    data: list[T]


def _ws(v: WorkspaceView) -> WorkspaceOut:
    fields = {k: getattr(v, k) for k in WorkspaceOut.model_fields}
    fields["settings"] = v.settings or {}
    return WorkspaceOut(**fields)


def _member(v: MemberView) -> MemberOut:
    return MemberOut(**{k: getattr(v, k) for k in MemberOut.model_fields})


@router.get("", response_model=Page[WorkspaceOut])
async def list_workspaces(user: CurrentUser, svc: Workspaces) -> Page[WorkspaceOut]:
    return Page(data=[_ws(w) for w in await svc.list_for_user(user)])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=WorkspaceOut)
async def create_workspace(
    body: WorkspaceIn, user: CurrentUser, svc: Workspaces, meta: Meta
) -> WorkspaceOut:
    view = await svc.create(
        user,
        name=body.name,
        slug=body.slug,
        workspace_type=body.workspace_type,
        timezone=body.timezone,
        meta=meta,
    )
    return _ws(view)


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Workspaces, meta: Meta
) -> WorkspaceOut:
    return _ws(await svc.get(user, workspace_id, meta))


@router.get("/{workspace_id}/members", response_model=Page[MemberOut])
async def list_members(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Workspaces, meta: Meta
) -> Page[MemberOut]:
    return Page(data=[_member(m) for m in await svc.list_members(user, workspace_id, meta)])


@router.patch("/{workspace_id}/members/{user_id}", response_model=MemberOut)
async def change_member_role(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    body: RoleIn,
    user: CurrentUser,
    svc: Workspaces,
    meta: Meta,
) -> MemberOut:
    return _member(await svc.change_role(user, workspace_id, user_id, body.role, meta))


@router.put("/{workspace_id}/members/{user_id}/away", response_model=MemberOut)
async def set_member_away(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    body: AwayIn,
    user: CurrentUser,
    svc: Workspaces,
    meta: Meta,
) -> MemberOut:
    """Out of office: no new approval cards, and a delegate decides where a policy names
    this person. Your own, or anyone's as an Admin."""
    view = await svc.set_away(
        user,
        workspace_id,
        user_id,
        meta,
        away_until=body.away_until,
        delegate_user_id=body.delegate_user_id,
    )
    return _member(view)


@router.get("/{workspace_id}/audit-log", response_model=Page[AuditEntryOut])
async def audit_log(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Workspaces,
    meta: Meta,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: int | None = None,
    action: Annotated[
        str | None, Query(max_length=60, description="Action prefix, e.g. approval.")
    ] = None,
    actor_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
    surface: Annotated[str | None, Query(max_length=20)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Page[AuditEntryOut]:
    """Admins: who did what, when, through which surface, on which account, against which
    exact text. Filter by action prefix, actor, account, surface and time."""
    rows = await svc.audit_log(
        user,
        workspace_id,
        meta,
        limit=limit,
        before_id=before_id,
        action=action,
        actor_id=actor_id,
        account_id=account_id,
        surface=surface,
        since=since,
        until=until,
    )
    return Page(data=[AuditEntryOut.model_validate(r) for r in rows])


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: uuid.UUID, body: WorkspacePatch, user: CurrentUser, svc: Workspaces, meta: Meta
) -> WorkspaceOut:
    view = await svc.update(
        user,
        workspace_id,
        meta,
        name=body.name,
        timezone=body.timezone,
        workspace_type=body.workspace_type,
        settings=body.settings,
    )
    return _ws(view)


@router.post("/{workspace_id}/archive", response_model=WorkspaceOut)
async def archive_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Workspaces, meta: Meta
) -> WorkspaceOut:
    return _ws(await svc.set_archived(user, workspace_id, meta, archived=True))


@router.post("/{workspace_id}/unarchive", response_model=WorkspaceOut)
async def unarchive_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Workspaces, meta: Meta
) -> WorkspaceOut:
    return _ws(await svc.set_archived(user, workspace_id, meta, archived=False))


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Workspaces, meta: Meta
) -> None:
    await svc.delete(user, workspace_id, meta)


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    workspace_id: uuid.UUID, user_id: uuid.UUID, user: CurrentUser, svc: Workspaces, meta: Meta
) -> None:
    await svc.remove_member(user, workspace_id, user_id, meta)


# ---- API keys ----


@router.get("/{workspace_id}/api-keys", response_model=Page[ApiKeyOut])
async def list_api_keys(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Access, meta: Meta
) -> Page[ApiKeyOut]:
    keys = await svc.list_api_keys(user, workspace_id, meta)
    return Page(data=[ApiKeyOut.model_validate(k, from_attributes=True) for k in keys])


@router.post(
    "/{workspace_id}/api-keys", status_code=status.HTTP_201_CREATED, response_model=ApiKeyCreatedOut
)
async def create_api_key(
    workspace_id: uuid.UUID, body: ApiKeyIn, user: CurrentUser, svc: Access, meta: Meta
) -> ApiKeyCreatedOut:
    created = await svc.create_api_key(
        user,
        workspace_id,
        meta,
        name=body.name,
        scopes=body.scopes,
        expires_in_days=body.expires_in_days,
    )
    return ApiKeyCreatedOut(
        key=ApiKeyOut.model_validate(created.key, from_attributes=True), secret=created.secret
    )


@router.delete("/{workspace_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    workspace_id: uuid.UUID, key_id: uuid.UUID, user: CurrentUser, svc: Access, meta: Meta
) -> None:
    await svc.revoke_api_key(user, workspace_id, key_id, meta)


# ---- invitations ----


@router.get("/{workspace_id}/members/invitations", response_model=Page[InvitationOut])
async def list_invitations(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Access, meta: Meta
) -> Page[InvitationOut]:
    rows = await svc.list_invitations(user, workspace_id, meta)
    return Page(data=[InvitationOut.model_validate(i, from_attributes=True) for i in rows])


@router.post(
    "/{workspace_id}/members/invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=InvitationCreatedOut,
)
async def create_invitation(
    workspace_id: uuid.UUID, body: InvitationIn, user: CurrentUser, svc: Access, meta: Meta
) -> InvitationCreatedOut:
    created = await svc.invite(user, workspace_id, meta, email=str(body.email), role=body.role)
    return InvitationCreatedOut(
        invitation=InvitationOut.model_validate(created.invitation, from_attributes=True),
        token=created.token,
        accept_url=created.accept_url,
        email_sent=created.email_sent,
    )


@router.delete(
    "/{workspace_id}/members/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_invitation(
    workspace_id: uuid.UUID, invitation_id: uuid.UUID, user: CurrentUser, svc: Access, meta: Meta
) -> None:
    await svc.revoke_invitation(user, workspace_id, invitation_id, meta)


# ---- member scopes ----


@router.get("/{workspace_id}/members/{user_id}/scopes", response_model=ScopesOut)
async def get_member_scopes(
    workspace_id: uuid.UUID, user_id: uuid.UUID, user: CurrentUser, svc: Workspaces, meta: Meta
) -> ScopesOut:
    return ScopesOut.model_validate(
        await svc.get_scopes(user, workspace_id, user_id, meta), from_attributes=True
    )


@router.put("/{workspace_id}/members/{user_id}/scopes", response_model=ScopesOut)
async def set_member_scopes(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    body: ScopesIn,
    user: CurrentUser,
    svc: Workspaces,
    meta: Meta,
) -> ScopesOut:
    scopes = await svc.set_scopes(
        user,
        workspace_id,
        user_id,
        meta,
        account_group_ids=body.account_group_ids,
        social_account_ids=body.social_account_ids,
    )
    return ScopesOut.model_validate(scopes, from_attributes=True)


# ---- notifications ----


@router.get("/{workspace_id}/notifications", response_model=Page[NotificationOut])
async def list_notifications(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Workspaces,
    meta: Meta,
    unread_only: bool = False,
) -> Page[NotificationOut]:
    rows = await svc.notifications(user, workspace_id, meta, unread_only=unread_only)
    return Page(data=[NotificationOut.model_validate(n, from_attributes=True) for n in rows])


@router.post(
    "/{workspace_id}/notifications/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT
)
async def read_notification(
    workspace_id: uuid.UUID,
    notification_id: uuid.UUID,
    user: CurrentUser,
    svc: Workspaces,
    meta: Meta,
) -> None:
    await svc.mark_notification_read(user, workspace_id, notification_id, meta)


# ---- second factors (P2-M2.5) ----


class SecondFactorIn(BaseModel):
    action: str = Field(
        max_length=40,
        description="kill_switch_resume, account_disconnect, company_page_approve, owner_remove, "
        "credentials_rotate, video_generate or comment_delete",
    )
    method: Literal["totp", "pin", "recovery"]
    value: str = Field(min_length=1, max_length=20)


class SecondFactorOut(BaseModel):
    token: str = Field(description="Send as X-Mera-Second-Factor with the action, once")
    expires_at: datetime


@router.post("/{workspace_id}/second-factor", response_model=SecondFactorOut)
async def second_factor(
    workspace_id: uuid.UUID, body: SecondFactorIn, user: CurrentUser, security: Security, meta: Meta
) -> SecondFactorOut:
    """Confirm a high-risk action with an authenticator code, the approval PIN or a recovery
    code (docs/09 §5.3). The token is single use, for this action in this workspace, and
    expires in five minutes."""
    token, expires = await security.grant(
        user, workspace_id, body.action, body.method, body.value, meta
    )
    return SecondFactorOut(token=token, expires_at=expires)


class SecondFactorPolicy(BaseModel):
    data: dict[str, bool] = Field(description="Action → whether it asks for a second factor")


class SecondFactorPolicyIn(BaseModel):
    changes: dict[str, bool] = Field(max_length=20)


@router.get("/{workspace_id}/second-factor-policy", response_model=SecondFactorPolicy)
async def get_second_factor_policy(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Workspaces, meta: Meta
) -> SecondFactorPolicy:
    return SecondFactorPolicy(data=await svc.second_factor_policy(user, workspace_id, meta))


@router.put("/{workspace_id}/second-factor-policy", response_model=SecondFactorPolicy)
async def put_second_factor_policy(
    workspace_id: uuid.UUID,
    body: SecondFactorPolicyIn,
    user: CurrentUser,
    svc: Workspaces,
    meta: Meta,
) -> SecondFactorPolicy:
    """Admins switch protections on; switching one off takes an Owner."""
    data = await svc.set_second_factor_policy(user, workspace_id, meta, body.changes)
    return SecondFactorPolicy(data=data)


def _mask_key(key: str | None) -> str | None:
    if not key:
        return None
    k = key.strip()
    if len(k) <= 8:
        return "••••••••"
    return f"{k[:7]}••••{k[-4:]}"


class LlmSettingsOut(BaseModel):
    sandbox_llm: bool
    has_openai_key: bool
    openai_key_preview: str | None
    has_anthropic_key: bool
    anthropic_key_preview: str | None
    has_gemini_key: bool
    gemini_key_preview: str | None
    has_openrouter_key: bool
    openrouter_key_preview: str | None
    has_groq_key: bool
    groq_key_preview: str | None
    custom_api_base: str | None
    has_custom_key: bool
    custom_key_preview: str | None


class LlmSettingsPatch(BaseModel):
    sandbox_llm: bool | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    groq_api_key: str | None = None
    custom_api_base: str | None = None
    custom_api_key: str | None = None


async def _llm_config(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Workspaces,
    meta: Meta,
    *,
    permission: Permission,
) -> dict[str, Any]:
    """Read provider configuration without ever exposing it through WorkspaceOut.

    Provider keys are workspace-scoped.  UI visibility is allowed to members, but modifying
    them is reserved for workspace managers; hiding the control in the browser is not an
    authorization boundary.
    """
    async with svc._tx(user, workspace_id, meta) as s:
        principal = await load_user_principal(s, user, workspace_id)
        rbac.require(principal, permission, workspace_id)
        ws = await s.get(Workspace, workspace_id)
        return dict((ws.settings or {}).get("llm_config") or {}) if ws else {}


@router.get("/{workspace_id}/llm-settings", response_model=LlmSettingsOut)
async def get_llm_settings(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Workspaces, meta: Meta
) -> LlmSettingsOut:
    ws_llm = await _llm_config(workspace_id, user, svc, meta, permission=Permission.READ)

    openai = ws_llm.get("openai_api_key")
    anthropic = ws_llm.get("anthropic_api_key")
    gemini = ws_llm.get("gemini_api_key")
    openrouter = ws_llm.get("openrouter_api_key")
    groq = ws_llm.get("groq_api_key")
    custom_base = ws_llm.get("custom_api_base")
    custom_key = ws_llm.get("custom_api_key")

    sandbox_val = ws_llm.get("sandbox_llm", False)

    return LlmSettingsOut(
        sandbox_llm=bool(sandbox_val),
        has_openai_key=bool(openai),
        openai_key_preview=_mask_key(openai),
        has_anthropic_key=bool(anthropic),
        anthropic_key_preview=_mask_key(anthropic),
        has_gemini_key=bool(gemini),
        gemini_key_preview=_mask_key(gemini),
        has_openrouter_key=bool(openrouter),
        openrouter_key_preview=_mask_key(openrouter),
        has_groq_key=bool(groq),
        groq_key_preview=_mask_key(groq),
        custom_api_base=custom_base,
        has_custom_key=bool(custom_key),
        custom_key_preview=_mask_key(custom_key),
    )


@router.patch("/{workspace_id}/llm-settings", response_model=LlmSettingsOut)
async def patch_llm_settings(
    workspace_id: uuid.UUID, body: LlmSettingsPatch, user: CurrentUser, svc: Workspaces, meta: Meta
) -> LlmSettingsOut:
    # Verify server-side permission before changing credentials.  This also prevents a
    # viewer from bypassing the disabled form with a direct HTTP request.
    llm_cfg = await _llm_config(
        workspace_id, user, svc, meta, permission=Permission.MANAGE_WORKSPACE
    )

    if body.sandbox_llm is not None:
        llm_cfg["sandbox_llm"] = body.sandbox_llm

    if body.openai_api_key is not None:
        if body.openai_api_key.strip():
            llm_cfg["openai_api_key"] = body.openai_api_key.strip()
        else:
            llm_cfg.pop("openai_api_key", None)

    if body.anthropic_api_key is not None:
        if body.anthropic_api_key.strip():
            llm_cfg["anthropic_api_key"] = body.anthropic_api_key.strip()
        else:
            llm_cfg.pop("anthropic_api_key", None)

    if body.gemini_api_key is not None:
        if body.gemini_api_key.strip():
            llm_cfg["gemini_api_key"] = body.gemini_api_key.strip()
        else:
            llm_cfg.pop("gemini_api_key", None)

    if body.openrouter_api_key is not None:
        if body.openrouter_api_key.strip():
            llm_cfg["openrouter_api_key"] = body.openrouter_api_key.strip()
        else:
            llm_cfg.pop("openrouter_api_key", None)

    if body.groq_api_key is not None:
        if body.groq_api_key.strip():
            llm_cfg["groq_api_key"] = body.groq_api_key.strip()
        else:
            llm_cfg.pop("groq_api_key", None)

    if body.custom_api_base is not None:
        base_val = body.custom_api_base.strip()
        if base_val:
            llm_cfg["custom_api_base"] = base_val
        else:
            llm_cfg.pop("custom_api_base", None)

    if body.custom_api_key is not None:
        key_val = body.custom_api_key.strip()
        if key_val:
            llm_cfg["custom_api_key"] = key_val
        else:
            llm_cfg.pop("custom_api_key", None)

    # Save only in the workspace.  Do not mutate process-wide environment variables or
    # rewrite a deployment's .env: either would leak one workspace's credentials to another.
    from sqlalchemy.orm.attributes import flag_modified
    async with svc._tx(user, workspace_id, meta) as s:
        principal = await load_user_principal(s, user, workspace_id)
        rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
        ws = await s.get(Workspace, workspace_id, with_for_update=True)
        if ws:
            settings = dict(ws.settings or {})
            settings["llm_config"] = llm_cfg
            ws.settings = dict(settings)
            flag_modified(ws, "settings")
            await s.flush()

    return await get_llm_settings(workspace_id, user, svc, meta)
