"""Brand voices at workspace, group and account scope (M5.2; docs/03 §6, docs/04 §4.5).

A voice belongs to one scope; at most one per scope is the default, and drafting resolves
the most specific default (account, then routing group, then workspace). Configuring a
voice is ``CONFIGURE_CONTENT`` on its subject: a group-scoped Editor can shape their own
group's and accounts' voices but not the workspace's. "Learned from your edits" is filled
by M6.10 (approver edit classification); it is read-only here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.agents.base import AgentContext, CostCapReached
from mera.application.agents.contracts import AccountSummary, DraftRequest, SourceMaterial
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.drafting import core
from mera.application.drafting.pipeline import Drafting
from mera.application.errors import (
    InvalidInput,
    NotFoundError,
    ServiceUnavailableError,
    TooManyAttempts,
)
from mera.application.ingest import agent_runs
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.policies import keyword_filter, rbac
from mera.domain.policies.rbac import Permission
from mera.domain.ports.llm import LLMError
from mera.domain.principal import Principal
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.platform import Platform
from mera.domain.value_objects.posts import EmojiPolicy, VoiceScope
from mera.infrastructure.db.models import (
    AccountGroup,
    AccountGroupMember,
    BrandVoice,
    SocialAccount,
    Workspace,
)
from mera.infrastructure.db.rate_limit import Limit, take
from mera.infrastructure.db.repositories.audit import record
from mera.skills import loader as skills

MAX_EXAMPLES: Final = 10
MAX_VOICES: Final = 100
SAMPLE_LIMIT: Final = Limit(capacity=30, per_seconds=60)


@dataclass(frozen=True, slots=True)
class VoiceView:
    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID | None
    scope_name: str
    name: str
    tone: str | None
    target_audience: str | None
    cta_style: str | None
    emoji_policy: str
    banned_words: list[str]
    example_posts: list[str]
    learned_preferences: list[dict[str, Any]]
    is_default: bool
    about: str | None
    story_bank: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class VoiceFields:
    name: str | None = None
    tone: str | None = None
    target_audience: str | None = None
    cta_style: str | None = None
    emoji_policy: EmojiPolicy | None = None
    banned_words: list[str] | None = None
    example_posts: list[str] | None = None
    is_default: bool | None = None
    about: str | None = None
    story_bank: list[str] | None = None


@dataclass(frozen=True, slots=True)
class VoiceSample:
    body: str
    angle: str
    self_critique: str
    voice_name: str


class VoiceService(WorkspaceScopedService):
    def __init__(self, sm: Any, drafting: Drafting) -> None:
        super().__init__(sm)
        self._drafting = drafting

    async def list_voices(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        scope_type: VoiceScope | None,
        scope_id: uuid.UUID | None,
    ) -> list[VoiceView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = select(BrandVoice).order_by(BrandVoice.scope_type.desc(), BrandVoice.name)
            if scope_type:
                q = q.where(BrandVoice.scope_type == scope_type.value)
            if scope_id:
                q = q.where(BrandVoice.scope_id == scope_id)
            voices = list((await s.execute(q)).scalars())
            if principal.member_scope.restricted:
                # Workspace voices plus their own groups' and accounts' (review M1).
                voices = [x for x in voices if await _voice_in_scope(s, principal, x)]
            names = await _scope_names(s)
            return [_view(v, names) for v in voices]

    async def create(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        scope_type: VoiceScope,
        scope_id: uuid.UUID | None,
        fields: VoiceFields,
    ) -> VoiceView:
        if (scope_type is VoiceScope.WORKSPACE) != (scope_id is None):
            raise InvalidInput(
                "A workspace voice has no scope id; a group or account voice needs one.",
                code="invalid_scope",
                field="scope_id",
            )
        if not (fields.name or "").strip():
            raise InvalidInput("Name the voice.", code="invalid_name", field="name")
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            await _require_scope(s, principal, workspace_id, scope_type, scope_id)
            await _writable(s, workspace_id, self)
            if len(list((await s.execute(select(BrandVoice.id))).scalars())) >= MAX_VOICES:
                raise InvalidInput(f"At most {MAX_VOICES} voices.", code="voice_limit_reached")
            v = BrandVoice(
                id=uuid7(),
                workspace_id=workspace_id,
                scope_type=scope_type.value,
                scope_id=scope_id,
                name="",
                created_by=user.user_id,
            )
            _apply(v, fields)
            if fields.is_default:
                await _clear_default(s, scope_type, scope_id)
                v.is_default = True
            s.add(v)
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "brand_voice.created",
                    workspace_id=workspace_id,
                    subject_type="brand_voice",
                    subject_id=v.id,
                    after={"name": v.name, "scope_type": v.scope_type, "default": v.is_default},
                ),
            )
            await s.refresh(v)
            return _view(v, await _scope_names(s))

    async def update(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        voice_id: uuid.UUID,
        meta: RequestMeta,
        fields: VoiceFields,
    ) -> VoiceView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            v = await _get(s, voice_id, lock=True)
            await _require_scope(s, principal, workspace_id, VoiceScope(v.scope_type), v.scope_id)
            await _writable(s, workspace_id, self)
            if fields.name is not None and not fields.name.strip():
                raise InvalidInput("Name the voice.", code="invalid_name", field="name")
            _apply(v, fields)
            if fields.is_default is True and not v.is_default:
                await _clear_default(s, VoiceScope(v.scope_type), v.scope_id)
                await s.flush()
                v.is_default = True
            elif fields.is_default is False:
                v.is_default = False
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "brand_voice.updated",
                    workspace_id=workspace_id,
                    subject_type="brand_voice",
                    subject_id=v.id,
                    after={"name": v.name, "default": v.is_default},
                ),
            )
            await s.refresh(v)
            return _view(v, await _scope_names(s))

    async def delete(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        voice_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            v = await _get(s, voice_id, lock=True)
            await _require_scope(s, principal, workspace_id, VoiceScope(v.scope_type), v.scope_id)
            await _writable(s, workspace_id, self)
            # Variants keep their versions; their voice pointer becomes NULL (ON DELETE SET
            # NULL) and the next draft resolves a voice again.
            await s.delete(v)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "brand_voice.deleted",
                    workspace_id=workspace_id,
                    subject_type="brand_voice",
                    subject_id=voice_id,
                    before={"name": v.name},
                ),
            )

    async def sample(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        voice_id: uuid.UUID,
        meta: RequestMeta,
        *,
        topic: str,
        platform: Platform,
    ) -> VoiceSample:
        """ "Test this voice": one Writer call on a topic, nothing saved."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            v = await _get(s, voice_id)
            await _require_scope(s, principal, workspace_id, VoiceScope(v.scope_type), v.scope_id)
            await _writable(s, workspace_id, self)
            voice = core.voice_summary(v)
        actor = user.api_key.api_key_id if user.api_key else user.user_id
        decision = await take(self._sm, f"llm_request:{actor}", SAMPLE_LIMIT)
        if not decision.allowed:
            raise TooManyAttempts(
                "Too many samples; try again shortly.", retry_after=decision.retry_after
            )
        req = DraftRequest(
            account=AccountSummary(
                "Sample account",
                "@sample",
                "personal",
                platform.value,
                core.platform_name(platform.value),
            ),
            group_name=None,
            voice=voice,
            platform=core.platform_spec(platform.value),
            source=SourceMaterial(
                title=topic[:200], url=None, publication=None, summary=topic[:1000], excerpt=None
            ),
            topic_brief=topic[:1000],
            mode="create",
            playbook=skills.playbook(platform.value, "writer"),
            playbook_name=f"{platform.value}:writer",
        )
        run = await agent_runs.start_run(
            self._sm,
            workspace_id,
            workflow_key="voice_sample",
            trigger="manual",
            subject_type="brand_voice",
            subject_id=voice_id,
        )
        ctx = AgentContext(
            self._sm,
            run,
            self._drafting.config.daily_cost_cap_usd,
            monthly_cap_usd=self._drafting.config.monthly_cost_cap_usd,
        )
        try:
            out = await self._drafting.writer.run(ctx, req)
        except CostCapReached as exc:
            await agent_runs.finish_run(self._sm, run, state="failed", error=str(exc))
            raise ServiceUnavailableError(
                "Today's drafting budget is spent; try again tomorrow."
                if exc.period == "daily"
                else "This month's AI budget for the workspace is spent."
            ) from exc
        except LLMError as exc:
            await agent_runs.finish_run(self._sm, run, state="failed", error=exc.kind)
            raise ServiceUnavailableError("The Writer is unavailable right now.") from exc
        await agent_runs.finish_run(self._sm, run, state="succeeded")
        return VoiceSample(out.post.body, out.post.angle, out.post.self_critique, voice.name)


async def _voice_in_scope(s: AsyncSession, principal: Principal, v: BrandVoice) -> bool:
    scope = principal.member_scope
    if v.scope_type == VoiceScope.WORKSPACE.value:
        return True
    if v.scope_type == VoiceScope.ACCOUNT_GROUP.value:
        return v.scope_id in scope.group_ids
    if v.scope_id in scope.account_ids:
        return True
    groups = (
        await s.execute(
            select(AccountGroupMember.account_group_id).where(
                AccountGroupMember.social_account_id == v.scope_id
            )
        )
    ).scalars()
    return any(g in scope.group_ids for g in groups)


async def _writable(s: AsyncSession, workspace_id: uuid.UUID, svc: WorkspaceScopedService) -> None:
    ws = await s.get(Workspace, workspace_id)
    if ws is None:
        raise NotFoundError("Workspace not found.")
    svc._ensure_writable(ws)


async def _require_scope(
    s: AsyncSession,
    principal: Principal,
    workspace_id: uuid.UUID,
    scope: VoiceScope,
    scope_id: uuid.UUID | None,
) -> None:
    if scope is VoiceScope.WORKSPACE:
        rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
    elif scope is VoiceScope.ACCOUNT_GROUP:
        if scope_id is None or await s.get(AccountGroup, scope_id) is None:
            raise InvalidInput("Unknown group.", code="unknown_group", field="scope_id")
        rbac.require(
            principal, Permission.CONFIGURE_CONTENT, workspace_id, account_group_id=scope_id
        )
    else:
        if scope_id is None or await s.get(SocialAccount, scope_id) is None:
            raise InvalidInput("Unknown account.", code="unknown_account", field="scope_id")
        groups = list(
            (
                await s.execute(
                    select(AccountGroupMember.account_group_id).where(
                        AccountGroupMember.social_account_id == scope_id
                    )
                )
            ).scalars()
        )
        WorkspaceScopedService._require_for_account(
            principal, Permission.CONFIGURE_CONTENT, workspace_id, scope_id, groups
        )


async def _clear_default(s: AsyncSession, scope: VoiceScope, scope_id: uuid.UUID | None) -> None:
    q = update(BrandVoice).where(
        BrandVoice.scope_type == scope.value, BrandVoice.is_default.is_(True)
    )
    q = (
        q.where(BrandVoice.scope_id.is_(None))
        if scope_id is None
        else q.where(BrandVoice.scope_id == scope_id)
    )
    await s.execute(q.values(is_default=False))


def _apply(v: BrandVoice, f: VoiceFields) -> None:
    def text(value: str | None, limit: int) -> str | None:
        return (value or "").strip()[:limit] or None

    if f.name is not None:
        v.name = f.name.strip()[:80]
    if f.tone is not None:
        v.tone = text(f.tone, 1000)
    if f.target_audience is not None:
        v.target_audience = text(f.target_audience, 500)
    if f.cta_style is not None:
        v.cta_style = text(f.cta_style, 300)
    if f.emoji_policy is not None:
        v.emoji_policy = f.emoji_policy.value
    if f.banned_words is not None:
        words = keyword_filter.normalize_keywords(f.banned_words)
        if len(words) > 100 or any(len(w) > 60 for w in words):
            raise InvalidInput(
                "At most 100 banned words of up to 60 characters.",
                code="invalid_banned_words",
                field="banned_words",
            )
        v.banned_words = words
    if f.about is not None:
        v.about = text(f.about, 1000)
    if f.story_bank is not None:
        stories = [x.strip() for x in f.story_bank if x.strip()]
        if len(stories) > 12 or any(len(x) > 600 for x in stories):
            raise InvalidInput(
                "At most 12 story-bank entries of up to 600 characters.",
                code="invalid_story_bank",
                field="story_bank",
            )
        v.story_bank = stories
    if f.example_posts is not None:
        posts = [p.strip() for p in f.example_posts if p.strip()]
        if len(posts) > MAX_EXAMPLES or any(len(p) > 3000 for p in posts):
            raise InvalidInput(
                f"At most {MAX_EXAMPLES} example posts of up to 3000 characters.",
                code="invalid_examples",
                field="example_posts",
            )
        v.example_posts = posts


async def _get(s: AsyncSession, voice_id: uuid.UUID, *, lock: bool = False) -> BrandVoice:
    v = await s.get(BrandVoice, voice_id, with_for_update=lock)
    if v is None:
        raise NotFoundError("Brand voice not found.")
    return v


async def _scope_names(s: AsyncSession) -> dict[uuid.UUID, str]:
    groups = dict((await s.execute(select(AccountGroup.id, AccountGroup.name))).all())
    accounts = dict((await s.execute(select(SocialAccount.id, SocialAccount.nickname))).all())
    return {**groups, **accounts}


def _view(v: BrandVoice, names: dict[uuid.UUID, str]) -> VoiceView:
    return VoiceView(
        id=v.id,
        scope_type=v.scope_type,
        scope_id=v.scope_id,
        scope_name=names.get(v.scope_id, "Unknown") if v.scope_id else "Workspace",
        name=v.name,
        tone=v.tone,
        target_audience=v.target_audience,
        cta_style=v.cta_style,
        emoji_policy=v.emoji_policy,
        banned_words=list(v.banned_words),
        example_posts=[str(p) for p in v.example_posts],
        learned_preferences=list(v.learned_preferences),
        is_default=v.is_default,
        about=v.about,
        story_bank=[str(x) for x in v.story_bank],
        created_at=v.created_at,
        updated_at=v.updated_at,
    )
