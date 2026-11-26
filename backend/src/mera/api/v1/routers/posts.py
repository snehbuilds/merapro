"""Post sets, variants, versions (docs/04 §4.6), brand voices (§4.5) and agent runs (§4.13)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from mera.api.deps import AgentRuns, CurrentUser, Meta, Orchestration, Posts, Templates, Voices
from mera.application.drafting.core import ContentInput
from mera.application.errors import InvalidInput
from mera.application.services.post_service import TargetSelection
from mera.application.services.voice_service import VoiceFields
from mera.domain.value_objects.platform import Platform
from mera.domain.value_objects.posts import EmojiPolicy, VoiceScope

router = APIRouter(tags=["posts"])
agents_router = APIRouter(tags=["agents"])
WS = "/workspaces/{workspace_id}"
SETS = f"{WS}/post-sets"


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- post sets


class GuardrailOut(_Out):
    guardrail_key: str
    outcome: str = Field(description="pass, warn or block")
    score: float | None
    details: dict[str, Any]


class VersionOut(_Out):
    id: uuid.UUID
    version_no: int
    body: str
    thread_parts: list[str] | None
    hashtags: list[str]
    link_url: str | None
    first_comment: str | None
    media: list[dict[str, Any]]
    content_hash: str = Field(description="sha256 hex; what an approval binds to")
    confidence_score: float | None
    compliance_flags: list[dict[str, Any]]
    validation: list[dict[str, Any]]
    agent_notes: dict[str, Any]
    hashtag_suggestions: list[dict[str, Any]]
    source: str = Field(description="agent or human")
    revision_instruction: str | None
    char_count: int = Field(description="As the platform counts it, hashtags included")
    char_limit: int
    created_at: datetime


class VariantOut(_Out):
    id: uuid.UUID
    social_account_id: uuid.UUID
    nickname: str
    handle: str
    avatar_url: str | None
    account_type: str
    platform: Platform
    via_account_group_id: uuid.UUID | None
    group_name: str | None
    enabled: bool
    state: str
    brand_voice_id: uuid.UUID | None
    voice_name: str
    position: int
    stagger_offset_seconds: int
    version_count: int
    current_version: VersionOut | None
    guardrails: list[GuardrailOut]


class ContentRefOut(_Out):
    id: uuid.UUID
    title: str
    url: str
    source_name: str | None


class SkippedOut(_Out):
    social_account_id: uuid.UUID
    reason: str = Field(description="excluded, inactive or unknown")


class PostSetOut(_Out):
    id: uuid.UUID
    title: str
    topic_brief: str | None
    origin: str
    state: str
    content: ContentRefOut | None
    target_account_group_ids: list[uuid.UUID]
    target_social_account_ids: list[uuid.UUID]
    composite_hash: str | None
    generation_status: str = Field(description="idle, queued, running or failed")
    generation_error: str | None
    last_run_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    variants: list[VariantOut]
    set_guardrails: list[GuardrailOut]
    skipped: list[SkippedOut] = Field(default_factory=list)
    is_evergreen: bool = False
    recycle_after: datetime | None = None
    ab_parent_id: uuid.UUID | None = Field(default=None, description="Set on version B")
    ab_label: str | None = None
    ab_hypothesis: str | None = None
    publish_mode: str = "draft"
    requested_scheduled_at: datetime | None = None
    requested_timezone: str | None = None


class PostSetList(BaseModel):
    data: list[PostSetOut]


class TargetsIn(BaseModel):
    account_group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=25)
    social_account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=25)
    exclude_social_account_ids: list[uuid.UUID] = Field(default_factory=list, max_length=25)


class PostSetIn(BaseModel):
    content_item_id: uuid.UUID | None = None
    topic: str | None = Field(
        default=None, max_length=2000, description="Brief when there is no item"
    )
    title: str | None = Field(default=None, max_length=200)
    targets: TargetsIn = Field(default_factory=TargetsIn)
    template_id: uuid.UUID | None = Field(
        default=None,
        description="Start from a template: its brief, outline and hashtags fill in what is "
        "not given here, and its groups are the targets when none are",
    )
    generate: bool = Field(default=True, description="Queue drafting immediately")
    publish_mode: Literal["draft", "immediate", "scheduled"] = "draft"
    scheduled_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)


class PostSetPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    topic: str | None = Field(default=None, max_length=2000)


class RewriteIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=500)
    variant_ids: list[uuid.UUID] | None = None


class GenerateIn(BaseModel):
    variant_ids: list[uuid.UUID] | None = Field(
        default=None, description="Default: every enabled account"
    )


class VariantIn(BaseModel):
    social_account_id: uuid.UUID
    generate: bool = True


class VariantPatch(BaseModel):
    enabled: bool | None = None
    brand_voice_id: uuid.UUID | None = Field(default=None, description="null clears the override")


class VersionIn(BaseModel):
    body: str = Field(max_length=6000)
    thread_parts: list[Annotated[str, Field(max_length=2000)]] | None = Field(
        default=None, max_length=12
    )
    hashtags: list[Annotated[str, Field(max_length=80)]] = Field(
        default_factory=list, max_length=30
    )
    link_url: str | None = Field(default=None, max_length=2048)
    first_comment: str | None = Field(
        default=None,
        max_length=1250,
        description="Optional comment published only after the main post succeeds",
    )
    based_on_version_id: uuid.UUID | None = Field(
        default=None, description="The version you edited; a newer one makes this a 409"
    )


class VersionList(BaseModel):
    data: list[VersionOut]


class TagOut(_Out):
    tag: str
    rank: int
    basis: str = Field(description="history, platform_convention or content_only")
    sample_size: int
    lift_vs_account_median: float | None
    rationale: str


class TagList(BaseModel):
    data: list[TagOut]


class ValidationOut(_Out):
    variant_id: uuid.UUID
    issues: list[dict[str, Any]]


class ValidationList(BaseModel):
    data: list[ValidationOut]


def _set(view: Any) -> PostSetOut:
    return PostSetOut.model_validate(view)


@router.get(SETS, response_model=PostSetList)
async def list_post_sets(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
    state: str | None = None,
    account_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> PostSetList:
    rows = await svc.list_sets(
        user,
        workspace_id,
        meta,
        state=state,
        account_id=account_id,
        group_id=group_id,
        limit=limit,
        include_archived=include_archived,
    )
    return PostSetList(data=[_set(r) for r in rows])


@router.post(SETS, response_model=PostSetOut, status_code=status.HTTP_201_CREATED)
async def create_post_set(
    workspace_id: uuid.UUID,
    body: PostSetIn,
    user: CurrentUser,
    svc: Posts,
    templates: Templates,
    meta: Meta,
) -> PostSetOut:
    """Expands groups and accounts into one variant per account (deduplicated) and, by
    default, queues drafting. ``skipped`` names accounts left out and why."""
    topic, title, targets = body.topic, body.title, body.targets.model_dump()
    if body.template_id is not None:
        applied = await templates.apply(user, workspace_id, body.template_id, meta)
        topic = applied.brief(topic)
        title = title or applied.title
        if not targets["account_group_ids"] and not targets["social_account_ids"]:
            targets["account_group_ids"] = applied.account_group_ids
    scheduled_at = body.scheduled_at
    if body.publish_mode == "scheduled":
        if scheduled_at is None:
            raise InvalidInput(
                "scheduled_at is required for scheduled publishing.",
                code="scheduled_at_required",
                field="scheduled_at",
            )
        try:
            tz = ZoneInfo(body.timezone or "UTC")
        except ZoneInfoNotFoundError as exc:
            raise InvalidInput(
                "Use a valid IANA time zone.", code="invalid_timezone", field="timezone"
            ) from exc
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=tz)
        scheduled_at = scheduled_at.astimezone(UTC)
        if scheduled_at <= datetime.now(UTC) + timedelta(minutes=1):
            raise InvalidInput(
                "Pick a time at least one minute in the future.",
                code="invalid_scheduled_at",
                field="scheduled_at",
            )
    elif scheduled_at is not None:
        raise InvalidInput(
            "scheduled_at is only valid in scheduled mode.",
            code="unexpected_scheduled_at",
            field="scheduled_at",
        )
    view = await svc.create(
        user,
        workspace_id,
        meta,
        content_item_id=body.content_item_id,
        topic_brief=topic,
        title=title,
        selection=TargetSelection(**targets),
        generate=body.generate,
        publish_mode=body.publish_mode,
        scheduled_at=scheduled_at,
        timezone=body.timezone,
    )
    return _set(view)


@router.get(f"{SETS}/{{set_id}}", response_model=PostSetOut)
async def get_post_set(
    workspace_id: uuid.UUID, set_id: uuid.UUID, user: CurrentUser, svc: Posts, meta: Meta
) -> PostSetOut:
    return _set(await svc.get(user, workspace_id, set_id, meta))


@router.patch(f"{SETS}/{{set_id}}", response_model=PostSetOut)
async def update_post_set(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    body: PostSetPatch,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
) -> PostSetOut:
    """Metadata only. Content changes only through new versions."""
    return _set(
        await svc.update_set(
            user, workspace_id, set_id, meta, title=body.title, topic_brief=body.topic
        )
    )


@router.delete(f"{SETS}/{{set_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_post_set(
    workspace_id: uuid.UUID, set_id: uuid.UUID, user: CurrentUser, svc: Posts, meta: Meta
) -> Response:
    """Archives the set; versions are immutable and kept."""
    await svc.archive(user, workspace_id, set_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    f"{SETS}/{{set_id}}/generate", response_model=PostSetOut, status_code=status.HTTP_202_ACCEPTED
)
async def generate_post_set(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
    body: GenerateIn | None = None,
) -> PostSetOut:
    ids = body.variant_ids if body else None
    return _set(await svc.generate(user, workspace_id, set_id, meta, variant_ids=ids))


@router.post(
    f"{SETS}/{{set_id}}/rewrite", response_model=PostSetOut, status_code=status.HTTP_202_ACCEPTED
)
async def rewrite_post_set(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    body: RewriteIn,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
) -> PostSetOut:
    """Fans the instruction out to each chosen (default: every enabled) account."""
    view = await svc.generate(
        user,
        workspace_id,
        set_id,
        meta,
        variant_ids=body.variant_ids,
        instruction=body.instruction,
        mode="revise",
    )
    return _set(view)


@router.post(f"{SETS}/{{set_id}}/validate", response_model=ValidationList)
async def validate_post_set(
    workspace_id: uuid.UUID, set_id: uuid.UUID, user: CurrentUser, svc: Posts, meta: Meta
) -> ValidationList:
    rows = await svc.validate(user, workspace_id, set_id, meta)
    return ValidationList(data=[ValidationOut.model_validate(r) for r in rows])


@router.post(
    f"{SETS}/{{set_id}}/variants", response_model=PostSetOut, status_code=status.HTTP_201_CREATED
)
async def add_variant(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    body: VariantIn,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
) -> PostSetOut:
    view = await svc.add_variant(
        user, workspace_id, set_id, meta, account_id=body.social_account_id, generate=body.generate
    )
    return _set(view)


@router.patch(f"{SETS}/{{set_id}}/variants/{{variant_id}}", response_model=PostSetOut)
async def update_variant(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    variant_id: uuid.UUID,
    body: VariantPatch,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
) -> PostSetOut:
    """Enable, disable, or switch voice. Never content."""
    view = await svc.update_variant(
        user,
        workspace_id,
        set_id,
        variant_id,
        meta,
        enabled=body.enabled,
        brand_voice_id=body.brand_voice_id,
        clear_brand_voice="brand_voice_id" in body.model_fields_set and body.brand_voice_id is None,
    )
    return _set(view)


@router.get(f"{SETS}/{{set_id}}/variants/{{variant_id}}/versions", response_model=VersionList)
async def list_versions(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    variant_id: uuid.UUID,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
) -> VersionList:
    rows = await svc.list_versions(user, workspace_id, set_id, variant_id, meta)
    return VersionList(data=[VersionOut.model_validate(r) for r in rows])


class DiffOp(BaseModel):
    op: str = Field(description="equal, delete or insert")
    text: str


class VersionDiffOut(BaseModel):
    from_version_no: int
    to_version_no: int
    same_content: bool = Field(description="Equal content hashes: an approval would still hold")
    body: list[DiffOp]
    body_changed: bool
    thread_parts: list[list[DiffOp]]
    hashtags_added: list[str]
    hashtags_removed: list[str]
    link_before: str | None
    link_after: str | None
    media_added: list[str] = Field(description="Checksums")
    media_removed: list[str]
    media_reordered: bool
    alt_text_changed: list[str] = Field(description="Checksums whose alt text changed")


@router.get(
    f"{SETS}/{{set_id}}/variants/{{variant_id}}/versions/{{a}}/diff/{{b}}",
    response_model=VersionDiffOut,
)
async def diff_versions(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    variant_id: uuid.UUID,
    a: uuid.UUID,
    b: uuid.UUID,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
) -> VersionDiffOut:
    """Word-level text changes and hashtag, link and media changes from ``a`` to ``b``."""
    out = await svc.diff_versions(user, workspace_id, set_id, variant_id, a, b, meta)
    return VersionDiffOut.model_validate(out)


@router.post(
    f"{SETS}/{{set_id}}/variants/{{variant_id}}/versions",
    response_model=PostSetOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    variant_id: uuid.UUID,
    body: VersionIn,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
) -> PostSetOut:
    """A new immutable version from supplied content, checked by the same guardrails as
    agent output. Hashtags are set here too (docs/04 §4.8 PUT .../hashtags)."""
    view = await svc.create_version(
        user,
        workspace_id,
        set_id,
        variant_id,
        meta,
        content=ContentInput(
            body.body,
            body.thread_parts,
            body.hashtags,
            body.link_url,
            [],
            body.first_comment,
        ),
        based_on_version_id=body.based_on_version_id,
    )
    return _set(view)


@router.post(
    f"{SETS}/{{set_id}}/variants/{{variant_id}}/rewrite",
    response_model=PostSetOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rewrite_variant(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    variant_id: uuid.UUID,
    body: RewriteIn,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
) -> PostSetOut:
    view = await svc.generate(
        user,
        workspace_id,
        set_id,
        meta,
        variant_ids=[variant_id],
        instruction=body.instruction,
        mode="revise",
    )
    return _set(view)


@router.post(
    f"{SETS}/{{set_id}}/variants/{{variant_id}}/regenerate",
    response_model=PostSetOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_variant(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    variant_id: uuid.UUID,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
) -> PostSetOut:
    """Draft this account again from scratch (the Composer's Regenerate)."""
    return _set(await svc.generate(user, workspace_id, set_id, meta, variant_ids=[variant_id]))


@router.post(f"{SETS}/{{set_id}}/variants/{{variant_id}}/hashtags/suggest", response_model=TagList)
async def suggest_hashtags(
    workspace_id: uuid.UUID,
    set_id: uuid.UUID,
    variant_id: uuid.UUID,
    user: CurrentUser,
    svc: Posts,
    meta: Meta,
) -> TagList:
    """Runs the Hashtag agent now and returns suggestions; nothing is saved until a new
    version is created with the chosen tags."""
    tags = await svc.suggest_hashtags(user, workspace_id, set_id, variant_id, meta)
    return TagList(data=[TagOut.model_validate(t) for t in tags])


# ------------------------------------------------------------------ brand voices


class VoiceOut(_Out):
    id: uuid.UUID
    scope_type: VoiceScope
    scope_id: uuid.UUID | None
    scope_name: str
    name: str
    tone: str | None
    target_audience: str | None
    cta_style: str | None
    emoji_policy: EmojiPolicy
    banned_words: list[str]
    example_posts: list[str]
    learned_preferences: list[dict[str, Any]] = Field(description="Filled by approver edits (M6)")
    is_default: bool
    about: str | None = Field(description="Who is posting (the skills' voice profile)")
    story_bank: list[str] = Field(description="True stories and facts the Writer may use")
    created_at: datetime
    updated_at: datetime


class VoiceList(BaseModel):
    data: list[VoiceOut]


class VoiceIn(BaseModel):
    scope_type: VoiceScope
    scope_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    tone: str | None = Field(default=None, max_length=1000)
    target_audience: str | None = Field(default=None, max_length=500)
    cta_style: str | None = Field(default=None, max_length=300)
    emoji_policy: EmojiPolicy = EmojiPolicy.SPARING
    banned_words: list[str] = Field(default_factory=list, max_length=100)
    example_posts: list[str] = Field(default_factory=list, max_length=10)
    is_default: bool = False
    about: str | None = Field(default=None, max_length=1000)
    story_bank: list[Annotated[str, Field(max_length=600)]] = Field(
        default_factory=list, max_length=12
    )


class VoicePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    tone: str | None = Field(default=None, max_length=1000)
    target_audience: str | None = Field(default=None, max_length=500)
    cta_style: str | None = Field(default=None, max_length=300)
    emoji_policy: EmojiPolicy | None = None
    banned_words: list[str] | None = Field(default=None, max_length=100)
    example_posts: list[str] | None = Field(default=None, max_length=10)
    is_default: bool | None = None
    about: str | None = Field(default=None, max_length=1000)
    story_bank: list[Annotated[str, Field(max_length=600)]] | None = Field(
        default=None, max_length=12
    )


class VoiceSampleIn(BaseModel):
    topic: str = Field(min_length=3, max_length=500)
    platform: Platform = Platform.LINKEDIN_MEMBER


class VoiceSampleOut(_Out):
    body: str
    angle: str
    self_critique: str
    voice_name: str


@router.get(f"{WS}/brand-voices", response_model=VoiceList)
async def list_voices(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Voices,
    meta: Meta,
    scope_type: VoiceScope | None = None,
    scope_id: uuid.UUID | None = None,
) -> VoiceList:
    rows = await svc.list_voices(user, workspace_id, meta, scope_type=scope_type, scope_id=scope_id)
    return VoiceList(data=[VoiceOut.model_validate(r) for r in rows])


@router.post(f"{WS}/brand-voices", response_model=VoiceOut, status_code=status.HTTP_201_CREATED)
async def create_voice(
    workspace_id: uuid.UUID, body: VoiceIn, user: CurrentUser, svc: Voices, meta: Meta
) -> VoiceOut:
    data = body.model_dump(exclude={"scope_type", "scope_id"})
    view = await svc.create(
        user,
        workspace_id,
        meta,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        fields=VoiceFields(**data),
    )
    return VoiceOut.model_validate(view)


@router.patch(f"{WS}/brand-voices/{{voice_id}}", response_model=VoiceOut)
async def update_voice(
    workspace_id: uuid.UUID,
    voice_id: uuid.UUID,
    body: VoicePatch,
    user: CurrentUser,
    svc: Voices,
    meta: Meta,
) -> VoiceOut:
    fields = VoiceFields(**body.model_dump(exclude_unset=True))
    return VoiceOut.model_validate(await svc.update(user, workspace_id, voice_id, meta, fields))


@router.delete(f"{WS}/brand-voices/{{voice_id}}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice(
    workspace_id: uuid.UUID, voice_id: uuid.UUID, user: CurrentUser, svc: Voices, meta: Meta
) -> Response:
    await svc.delete(user, workspace_id, voice_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(f"{WS}/brand-voices/{{voice_id}}/sample", response_model=VoiceSampleOut)
async def sample_voice(
    workspace_id: uuid.UUID,
    voice_id: uuid.UUID,
    body: VoiceSampleIn,
    user: CurrentUser,
    svc: Voices,
    meta: Meta,
) -> VoiceSampleOut:
    """ "Test this voice": one Writer call on a topic. Nothing is saved."""
    view = await svc.sample(
        user, workspace_id, voice_id, meta, topic=body.topic, platform=body.platform
    )
    return VoiceSampleOut.model_validate(view)


# -------------------------------------------------------------------- agent runs


class StepOut(_Out):
    id: uuid.UUID
    seq: int
    agent_key: str
    step_type: str
    state: str
    model: str | None
    fallback_from: str | None
    price_source: str | None
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    latency_ms: int
    post_variant_id: uuid.UUID | None
    input_redacted: dict[str, Any]
    output: dict[str, Any]
    error: str | None
    started_at: datetime


class RunOut(_Out):
    id: uuid.UUID
    workflow_key: str
    trigger: str
    subject_type: str | None
    subject_id: uuid.UUID | None
    state: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    step_count: int
    steps: list[StepOut]
    parent_run_id: uuid.UUID | None = None
    children: list[RunOut] = Field(
        default_factory=list, description="Child runs of an orchestration, with their steps"
    )


class RunList(BaseModel):
    data: list[RunOut]


class CostRowOut(_Out):
    key: str
    label: str
    cost_usd: Decimal
    tokens_in: int
    tokens_out: int
    calls: int


class CostReportOut(_Out):
    since: datetime
    until: datetime
    total_usd: Decimal
    by_agent: list[CostRowOut]
    by_model: list[CostRowOut]
    by_workflow: list[CostRowOut]
    by_account: list[CostRowOut]


class AgentOut(_Out):
    agent_key: str
    name: str
    model: str
    fallback_models: list[str]
    temperature: float
    cost_30d_usd: Decimal
    editable: bool
    default_model: str | None = Field(description="The deployment's model for this agent")
    choices: list[str] = Field(description="Models that can be chosen (each has a pinned price)")


class AgentList(BaseModel):
    data: list[AgentOut]


class AgentModelIn(BaseModel):
    model: str | None = Field(description="null returns to the default", max_length=100)
    fallbacks: list[Annotated[str, Field(max_length=100)]] = Field(
        default_factory=list, max_length=3
    )


@agents_router.patch(f"{WS}/agents/{{agent_key}}", response_model=AgentOut)
async def set_agent_model(
    workspace_id: uuid.UUID,
    agent_key: str,
    body: AgentModelIn,
    user: CurrentUser,
    svc: AgentRuns,
    meta: Meta,
) -> AgentOut:
    """Choose this workspace's model for one agent (Admins). Takes effect on the next call."""
    view = await svc.set_model(
        user,
        workspace_id,
        meta,
        agent_key=agent_key,
        model=body.model,
        fallbacks=body.fallbacks if body.model else None,
    )
    return AgentOut.model_validate(view)


@agents_router.get(f"{WS}/agents", response_model=AgentList)
async def list_agents(
    workspace_id: uuid.UUID, user: CurrentUser, svc: AgentRuns, meta: Meta
) -> AgentList:
    rows = await svc.definitions(user, workspace_id, meta)
    return AgentList(data=[AgentOut.model_validate(r) for r in rows])


@agents_router.get(f"{WS}/agent-runs", response_model=RunList)
async def list_agent_runs(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: AgentRuns,
    meta: Meta,
    workflow: str | None = None,
    state: str | None = None,
    subject_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> RunList:
    rows = await svc.list_runs(
        user,
        workspace_id,
        meta,
        workflow_key=workflow,
        state=state,
        subject_id=subject_id,
        limit=limit,
    )
    return RunList(data=[RunOut.model_validate(r) for r in rows])


@agents_router.get(f"{WS}/agent-runs/costs", response_model=CostReportOut)
async def agent_costs(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: AgentRuns,
    meta: Meta,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
    account_id: uuid.UUID | None = None,
) -> CostReportOut:
    """Spend by agent, model, workflow and target account (default: last 30 days)."""
    report = await svc.costs(
        user, workspace_id, meta, since=since, until=until, account_id=account_id
    )
    return CostReportOut.model_validate(report)


@agents_router.post(f"{WS}/agent-runs/{{run_id}}/abort", response_model=RunOut)
async def abort_agent_run(
    workspace_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, svc: AgentRuns, meta: Meta
) -> RunOut:
    """Stop a queued or running run. The account being drafted is finished first."""
    return RunOut.model_validate(await svc.abort(user, workspace_id, run_id, meta))


@agents_router.post(f"{WS}/agent-runs/{{run_id}}/retry", response_model=PostSetOut, status_code=202)
async def retry_agent_run(
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    user: CurrentUser,
    svc: AgentRuns,
    posts: Posts,
    meta: Meta,
) -> PostSetOut:
    """Queue drafting again for the post set a failed or stopped drafting run was for."""
    set_id = await svc.retry_target(user, workspace_id, run_id, meta)
    return _set(await posts.generate(user, workspace_id, set_id, meta, variant_ids=None))


class RunStarted(BaseModel):
    run_id: uuid.UUID


@agents_router.post(f"{SETS}/{{set_id}}/orchestrate", response_model=RunStarted, status_code=202)
async def orchestrate_post_set(
    workspace_id: uuid.UUID, set_id: uuid.UUID, user: CurrentUser, orch: Orchestration, meta: Meta
) -> RunStarted:
    """Draft, illustrate and propose times for every account, as one resumable run
    (P2-M7.3). Approval and scheduling stay as they are."""
    return RunStarted(run_id=await orch.start(user, workspace_id, meta, set_id))


@agents_router.post(
    f"{WS}/agent-runs/{{run_id}}/resume", response_model=RunStarted, status_code=202
)
async def resume_orchestration(
    workspace_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, orch: Orchestration, meta: Meta
) -> RunStarted:
    """Continue a failed or stopped orchestration from its first unfinished stage."""
    return RunStarted(run_id=await orch.resume(user, workspace_id, meta, run_id))


class SlotProposalOut(BaseModel):
    at: datetime
    score: float
    samples: int
    reason: str


class SlotScoreOut(BaseModel):
    weekday: int = Field(description="0 = Monday, in the account's time zone")
    hour: int
    score: float = Field(description="Relative to the account's typical engagement (1.0)")
    samples: int


class SlotProposalsOut(BaseModel):
    proposals: list[SlotProposalOut]
    scores: list[SlotScoreOut]
    time_zone: str


@agents_router.get(
    f"{WS}/social-accounts/{{account_id}}/slot-proposals", response_model=SlotProposalsOut
)
async def slot_proposals(
    workspace_id: uuid.UUID,
    account_id: uuid.UUID,
    user: CurrentUser,
    orch: Orchestration,
    meta: Meta,
) -> SlotProposalsOut:
    """The Scheduler agent's view of one account: hour-of-week scores from its own history
    and the next best times. Inputs only: placement still applies every rule."""
    return SlotProposalsOut(**await orch.slot_proposals(user, workspace_id, meta, account_id))


@agents_router.get(f"{WS}/agent-runs/{{run_id}}", response_model=RunOut)
async def get_agent_run(
    workspace_id: uuid.UUID, run_id: uuid.UUID, user: CurrentUser, svc: AgentRuns, meta: Meta
) -> RunOut:
    """The full step timeline. Prompts are not stored yet (object store: Sprint 7)."""
    return RunOut.model_validate(await svc.get_run(user, workspace_id, run_id, meta))
