"""Social accounts and account groups (docs/04 §4.2-4.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from fastapi import APIRouter, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator

from mera.api.deps import Accounts, CurrentUser, Groups, HumanUser, Meta
from mera.application.services.account_service import AccountView
from mera.application.services.group_service import GroupChanges, GroupView
from mera.application.settings_model import LevelSettings, PostingTime
from mera.domain.policies.settings_resolution import Resolved
from mera.domain.value_objects.platform import AccountStatus, Platform, StaggerMode
from mera.infrastructure.platforms.capabilities import PlatformCapabilities
from mera.infrastructure.platforms.oauth_connectors import ClientCredentials
from mera.infrastructure.platforms.workspace_oauth import PROVIDER_PLATFORMS, OAuthProvider

router = APIRouter(tags=["accounts"])
callbacks = APIRouter(tags=["accounts"])
T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    data: list[T]


class AccountOut(BaseModel):
    id: uuid.UUID
    platform: Platform
    platform_account_id: str
    handle: str
    display_name: str | None
    nickname: str
    avatar_url: str | None
    account_type: str
    status: AccountStatus
    status_reason: str | None
    granted_scopes: list[str]
    token_expires_at: datetime | None
    last_health_check_at: datetime | None
    health: dict[str, Any]
    default_brand_voice_id: uuid.UUID | None
    default_posting_times: list[PostingTime] | None
    settings: dict[str, Any]
    group_ids: list[uuid.UUID]
    connected_at: datetime | None


class AccountPatch(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=80)
    default_posting_times: list[PostingTime] | None = None
    settings: LevelSettings | None = None


class ConnectIn(BaseModel):
    return_path: str = Field(default="/", max_length=512, description="Web-app path to return to")
    instance: str | None = Field(
        default=None,
        max_length=255,
        description="Mastodon: the server the account is on, like mastodon.social",
    )


class SignInIn(BaseModel):
    identifier: str = Field(min_length=1, max_length=253, description="Handle or email")
    secret: str = Field(
        min_length=1, max_length=100, description="An app password; used once, never stored"
    )
    service: str | None = Field(
        default=None, max_length=255, description="Bluesky: a self-hosted PDS; default bsky.social"
    )


class SignInOut(BaseModel):
    connect_code: str = Field(description="Redeem with POST /social-accounts/connect/complete")


class ConnectOut(BaseModel):
    authorize_url: str = Field(description="Send the browser here to grant access.")


class CompleteIn(BaseModel):
    connect_code: str = Field(min_length=10, max_length=128)
    platform_account_id: str | None = Field(
        default=None,
        max_length=200,
        description="When the sign-in manages several Pages: the one to connect "
        "(see GET /social-accounts/connect/choices)",
    )


class ConnectChoiceOut(BaseModel):
    platform_account_id: str
    handle: str
    display_name: str | None
    account_type: str


class ConnectChoiceList(BaseModel):
    data: list[ConnectChoiceOut]


class CompleteOut(BaseModel):
    outcome: str = Field(description="connected or reconnected")
    account_id: uuid.UUID
    workspace_id: uuid.UUID


class ResolvedOut(BaseModel):
    key: str
    value: Any
    source_level: str
    rule: str


class GroupMemberOut(BaseModel):
    social_account_id: uuid.UUID
    platform: Platform
    nickname: str
    handle: str
    status: AccountStatus
    position: int
    enabled_by_default: bool


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    color: str | None
    default_brand_voice_id: uuid.UUID | None
    posting_schedule: list[PostingTime] | None
    content_pillar_ids: list[uuid.UUID]
    stagger_mode: StaggerMode
    stagger_min_seconds: int
    stagger_max_seconds: int
    enabled: bool
    settings: dict[str, Any]
    member_count: int
    members: list[GroupMemberOut]
    created_at: datetime


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    stagger_mode: StaggerMode = StaggerMode.RANDOM_SPREAD
    stagger_min_seconds: int = 180
    stagger_max_seconds: int = 900
    social_account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=25)


class GroupPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    posting_schedule: list[PostingTime] | None = None
    stagger_mode: StaggerMode | None = None
    stagger_min_seconds: int | None = None
    stagger_max_seconds: int | None = None
    enabled: bool | None = None
    settings: LevelSettings | None = None


class MembersIn(BaseModel):
    social_account_ids: list[uuid.UUID] = Field(max_length=25)


class MemberPatch(BaseModel):
    position: int | None = Field(default=None, ge=0)
    enabled_by_default: bool | None = None


def _account(v: AccountView) -> AccountOut:
    return AccountOut.model_validate(v, from_attributes=True)


def _group(v: GroupView) -> GroupOut:
    return GroupOut.model_validate(v, from_attributes=True)


def _resolved(rows: list[Resolved]) -> Page[ResolvedOut]:
    return Page(
        data=[
            ResolvedOut(key=r.key, value=r.value, source_level=r.source_level, rule=r.rule)
            for r in rows
        ]
    )


WS = "/workspaces/{workspace_id}"


class OAuthAppIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_id: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    client_secret: SecretStr = Field(min_length=1, max_length=4096)

    @field_validator("client_secret")
    @classmethod
    def strip_secret(cls, secret: SecretStr) -> SecretStr:
        value = secret.get_secret_value().strip()
        if not value:
            raise ValueError("Enter the app secret.")
        return SecretStr(value)


class OAuthAppOut(BaseModel):
    provider: OAuthProvider
    source: Literal["workspace", "server", "missing"]
    redirect_uris: list[str]


@router.get(f"{WS}/social-oauth-apps", response_model=Page[OAuthAppOut])
async def list_oauth_apps(
    workspace_id: uuid.UUID, user: HumanUser, svc: Accounts, meta: Meta
) -> Page[OAuthAppOut]:
    states = await svc.oauth_app_status(user, workspace_id, meta)
    return Page(data=[
            OAuthAppOut(
                provider=provider,
                source=states[provider],
                redirect_uris=[svc.callback_uri(platform) for platform in platforms],
            )
            for provider, platforms in PROVIDER_PLATFORMS.items()
    ])


@router.put(f"{WS}/social-oauth-apps/{{provider}}", status_code=204)
async def save_oauth_app(
    workspace_id: uuid.UUID, provider: OAuthProvider, body: OAuthAppIn,
    user: HumanUser, svc: Accounts, meta: Meta,
) -> None:
    await svc.save_oauth_app(
        user, workspace_id, provider, meta,
        ClientCredentials(body.client_id, body.client_secret.get_secret_value()),
    )

# ---------------------------------------------------------------- accounts


@router.get(f"{WS}/social-accounts", response_model=Page[AccountOut])
async def list_accounts(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Accounts,
    meta: Meta,
    platform: Platform | None = None,
    account_status: Annotated[AccountStatus | None, Query(alias="status")] = None,
    group_id: uuid.UUID | None = None,
) -> Page[AccountOut]:
    rows = await svc.list_accounts(
        user, workspace_id, meta, platform=platform, status=account_status, group_id=group_id
    )
    return Page(data=[_account(a) for a in rows])


@router.post(f"{WS}/social-accounts/connect/{{platform}}", response_model=ConnectOut)
async def connect_account(
    workspace_id: uuid.UUID,
    platform: Platform,
    body: ConnectIn,
    user: CurrentUser,
    svc: Accounts,
    meta: Meta,
) -> ConnectOut:
    url = await svc.start_connect(
        user, workspace_id, platform, meta, return_path=body.return_path, instance=body.instance
    )
    return ConnectOut(authorize_url=url)


@router.post(f"{WS}/social-accounts/connect/{{platform}}/sign-in", response_model=SignInOut)
async def sign_in_account(
    workspace_id: uuid.UUID,
    platform: Platform,
    body: SignInIn,
    user: CurrentUser,
    svc: Accounts,
    meta: Meta,
) -> SignInOut:
    """Connect with an app password (Bluesky, P2-M3). Anything that is not an app password
    is refused, so an account password never reaches Mera. Returns a one-time code to
    redeem as after an OAuth redirect."""
    code = await svc.sign_in_connect(
        user,
        workspace_id,
        platform,
        meta,
        identifier=body.identifier,
        secret=body.secret,
        service=body.service,
    )
    return SignInOut(connect_code=code)


@router.post(f"{WS}/social-accounts/{{account_id}}/reauthorize", response_model=ConnectOut)
async def reauthorize_account(
    workspace_id: uuid.UUID,
    account_id: uuid.UUID,
    body: ConnectIn,
    user: CurrentUser,
    svc: Accounts,
    meta: Meta,
) -> ConnectOut:
    current = await svc.get(user, workspace_id, account_id, meta)
    url = await svc.start_connect(
        user,
        workspace_id,
        Platform(current.platform),
        meta,
        return_path=body.return_path,
        reconnect_account_id=account_id,
        instance=body.instance,
    )
    return ConnectOut(authorize_url=url)


@callbacks.get("/social-accounts/oauth/{platform}/callback", status_code=status.HTTP_302_FOUND)
async def connect_callback(
    platform: Platform,
    svc: Accounts,
    meta: Meta,
    code: Annotated[str | None, Query(max_length=4096)] = None,
    state: Annotated[str | None, Query(max_length=256)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    url = await svc.complete_connect(platform, state=state, code=code, error=error, meta=meta)
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@callbacks.post("/social-accounts/connect/complete", response_model=CompleteOut)
async def complete_connect(
    body: CompleteIn, user: HumanUser, svc: Accounts, meta: Meta
) -> CompleteOut:
    """Redeem the one-time code the platform callback handed to the browser. Only the user
    who started the connection can redeem it."""
    outcome, account_id, workspace_id = await svc.finish_connect(
        user, body.connect_code, meta, choice=body.platform_account_id
    )
    return CompleteOut(outcome=outcome, account_id=account_id, workspace_id=workspace_id)


@callbacks.get("/social-accounts/connect/choices", response_model=ConnectChoiceList)
async def connect_choices(
    connect_code: Annotated[str, Query(min_length=10, max_length=128)],
    user: HumanUser,
    svc: Accounts,
    meta: Meta,
) -> ConnectChoiceList:
    """The Pages a pending connection could connect, when the sign-in granted several
    (P2-M1.5). Empty otherwise. Tokens are never returned."""
    rows = await svc.connect_choices(user, connect_code, meta)
    return ConnectChoiceList(data=[ConnectChoiceOut.model_validate(r) for r in rows])


@router.get(f"{WS}/social-accounts/{{account_id}}", response_model=AccountOut)
async def get_account(
    workspace_id: uuid.UUID, account_id: uuid.UUID, user: CurrentUser, svc: Accounts, meta: Meta
) -> AccountOut:
    return _account(await svc.get(user, workspace_id, account_id, meta))


@router.patch(f"{WS}/social-accounts/{{account_id}}", response_model=AccountOut)
async def update_account(
    workspace_id: uuid.UUID,
    account_id: uuid.UUID,
    body: AccountPatch,
    user: CurrentUser,
    svc: Accounts,
    meta: Meta,
) -> AccountOut:
    times = (
        [t.model_dump() for t in body.default_posting_times]
        if body.default_posting_times is not None
        else None
    )
    view = await svc.update(
        user,
        workspace_id,
        account_id,
        meta,
        nickname=body.nickname,
        default_posting_times=times,
        clear_posting_times="default_posting_times" in body.model_fields_set and times is None,
        settings=body.settings,
    )
    return _account(view)


@router.delete(f"{WS}/social-accounts/{{account_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_account(
    workspace_id: uuid.UUID, account_id: uuid.UUID, user: CurrentUser, svc: Accounts, meta: Meta
) -> None:
    await svc.disconnect(user, workspace_id, account_id, meta)


@router.get(
    f"{WS}/social-accounts/{{account_id}}/capabilities", response_model=PlatformCapabilities
)
async def account_capabilities(
    workspace_id: uuid.UUID, account_id: uuid.UUID, user: CurrentUser, svc: Accounts, meta: Meta
) -> PlatformCapabilities:
    return await svc.capabilities(user, workspace_id, account_id, meta)


@router.post(f"{WS}/social-accounts/{{account_id}}/health/check", response_model=AccountOut)
async def check_account_health(
    workspace_id: uuid.UUID, account_id: uuid.UUID, user: CurrentUser, svc: Accounts, meta: Meta
) -> AccountOut:
    return _account(await svc.check_health_now(user, workspace_id, account_id, meta))


@router.get(f"{WS}/social-accounts/{{account_id}}/settings", response_model=Page[ResolvedOut])
async def account_settings(
    workspace_id: uuid.UUID,
    account_id: uuid.UUID,
    user: CurrentUser,
    svc: Accounts,
    meta: Meta,
    via_group_id: uuid.UUID | None = None,
) -> Page[ResolvedOut]:
    rows = await svc.resolved_settings(
        user, workspace_id, account_id, meta, via_group_id=via_group_id
    )
    return _resolved(rows)


# ---------------------------------------------------------------- groups


@router.get(f"{WS}/account-groups", response_model=Page[GroupOut])
async def list_groups(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Groups, meta: Meta
) -> Page[GroupOut]:
    return Page(data=[_group(g) for g in await svc.list_groups(user, workspace_id, meta)])


@router.post(f"{WS}/account-groups", status_code=status.HTTP_201_CREATED, response_model=GroupOut)
async def create_group(
    workspace_id: uuid.UUID, body: GroupIn, user: CurrentUser, svc: Groups, meta: Meta
) -> GroupOut:
    view = await svc.create(
        user,
        workspace_id,
        meta,
        name=body.name,
        description=body.description,
        color=body.color,
        stagger_mode=body.stagger_mode,
        stagger_min_seconds=body.stagger_min_seconds,
        stagger_max_seconds=body.stagger_max_seconds,
        social_account_ids=body.social_account_ids,
    )
    return _group(view)


@router.get(f"{WS}/account-groups/{{group_id}}", response_model=GroupOut)
async def get_group(
    workspace_id: uuid.UUID, group_id: uuid.UUID, user: CurrentUser, svc: Groups, meta: Meta
) -> GroupOut:
    return _group(await svc.get(user, workspace_id, group_id, meta))


@router.patch(f"{WS}/account-groups/{{group_id}}", response_model=GroupOut)
async def update_group(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    body: GroupPatch,
    user: CurrentUser,
    svc: Groups,
    meta: Meta,
) -> GroupOut:
    schedule = (
        [t.model_dump() for t in body.posting_schedule]
        if body.posting_schedule is not None
        else None
    )
    changes = GroupChanges(
        name=body.name,
        description=body.description,
        color=body.color,
        posting_schedule=schedule,
        clear_posting_schedule="posting_schedule" in body.model_fields_set and schedule is None,
        stagger_mode=body.stagger_mode,
        stagger_min_seconds=body.stagger_min_seconds,
        stagger_max_seconds=body.stagger_max_seconds,
        enabled=body.enabled,
        settings=body.settings,
    )
    return _group(await svc.update(user, workspace_id, group_id, meta, changes))


@router.delete(f"{WS}/account-groups/{{group_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    workspace_id: uuid.UUID, group_id: uuid.UUID, user: CurrentUser, svc: Groups, meta: Meta
) -> None:
    await svc.delete(user, workspace_id, group_id, meta)


@router.put(f"{WS}/account-groups/{{group_id}}/members", response_model=GroupOut)
async def replace_group_members(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    body: MembersIn,
    user: CurrentUser,
    svc: Groups,
    meta: Meta,
) -> GroupOut:
    view = await svc.set_members(
        user, workspace_id, group_id, meta, social_account_ids=body.social_account_ids, replace=True
    )
    return _group(view)


@router.post(f"{WS}/account-groups/{{group_id}}/members", response_model=GroupOut)
async def add_group_members(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    body: MembersIn,
    user: CurrentUser,
    svc: Groups,
    meta: Meta,
) -> GroupOut:
    view = await svc.set_members(
        user,
        workspace_id,
        group_id,
        meta,
        social_account_ids=body.social_account_ids,
        replace=False,
    )
    return _group(view)


@router.delete(f"{WS}/account-groups/{{group_id}}/members/{{account_id}}", response_model=GroupOut)
async def remove_group_member(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    account_id: uuid.UUID,
    user: CurrentUser,
    svc: Groups,
    meta: Meta,
) -> GroupOut:
    return _group(await svc.remove_member(user, workspace_id, group_id, account_id, meta))


@router.patch(f"{WS}/account-groups/{{group_id}}/members/{{account_id}}", response_model=GroupOut)
async def update_group_member(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    account_id: uuid.UUID,
    body: MemberPatch,
    user: CurrentUser,
    svc: Groups,
    meta: Meta,
) -> GroupOut:
    view = await svc.update_member(
        user,
        workspace_id,
        group_id,
        account_id,
        meta,
        position=body.position,
        enabled_by_default=body.enabled_by_default,
    )
    return _group(view)


@router.get(f"{WS}/account-groups/{{group_id}}/settings", response_model=Page[ResolvedOut])
async def group_settings(
    workspace_id: uuid.UUID, group_id: uuid.UUID, user: CurrentUser, svc: Groups, meta: Meta
) -> Page[ResolvedOut]:
    return _resolved(await svc.resolved_settings(user, workspace_id, group_id, meta))
