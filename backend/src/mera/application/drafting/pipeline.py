"""The ``draft_from_content`` and ``revise_post`` workflows (M5.13; docs/06 §3, §4.1).

The plan is a static template, not an LLM call (docs/06 §4.1: most runs spend zero
orchestrator tokens). Per target account, in publish order:

    Writer (shown this set's drafts for other accounts on the same platform)
      -> embed -> cross-account similarity; too close => Writer in differentiate mode
         (at most DIFFERENTIATE_ATTEMPTS), then the set-scoped guardrail decides
      -> Hashtag -> Editor -> new immutable version -> variant guardrails

A failure for one account leaves the others alone (docs/06 §3: "that variant blocked,
siblings continue"). The daily drafting cost cap stops the run before the next call.
Provider calls happen outside database transactions; each version is written in its own
short transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.agents.alt_text import AltTextAgent
from mera.application.agents.analytics import AnalyticsAgent
from mera.application.agents.base import AgentContext, CostCapReached
from mera.application.agents.contracts import (
    AccountSummary,
    DraftRequest,
    Sibling,
    SourceMaterial,
    VoiceSummary,
)
from mera.application.agents.engagement import EngagementAgent
from mera.application.agents.media import MediaAgent
from mera.application.agents.reviewers import (
    Editor,
    EditorOutput,
    EditRequest,
    HashtagAgent,
    HashtagRequest,
    TagEvidence,
)
from mera.application.agents.writer import Writer, WriterOutput
from mera.application.drafting import core
from mera.application.hashtags import account_evidence, apply_evidence, registry_tags
from mera.application.ingest import agent_runs
from mera.domain.context import WorkspaceContext
from mera.domain.ports.llm import LLMError, LLMProvider, ModelConfig
from mera.domain.value_objects.posts import (
    CROSS_ACCOUNT_BLOCK,
    DIFFERENTIATE_ATTEMPTS,
    GenerationStatus,
    PostSetState,
)
from mera.infrastructure.db.models import (
    AccountGroup,
    BrandVoice,
    ContentItem,
    PostSet,
    PostVariant,
    PostVersion,
    SocialAccount,
    Source,
    Workspace,
)
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.http.safe_fetch import validate_public_url
from mera.skills import loader as skills

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DraftingConfig:
    writer: ModelConfig
    editor: ModelConfig
    hashtag: ModelConfig
    embedding_model: str
    daily_cost_cap_usd: Decimal
    monthly_cost_cap_usd: Decimal | None = None
    # Vision model for alt text suggestions (P2-M1.8); defaults to the hashtag model.
    alt_text: ModelConfig | None = None
    # The Media agent's prompt planner (P2-M5.4); defaults to the hashtag model.
    media: ModelConfig | None = None
    # The Engagement agent (P2-M6.3); defaults to the hashtag model.
    engagement: ModelConfig | None = None
    # The Analytics agent (P3-M1.4): narrates the weekly insights.
    analytics: ModelConfig | None = None


@dataclass(slots=True)
class _Target:
    variant_id: uuid.UUID
    platform: str
    account: AccountSummary
    group_name: str | None
    voice: VoiceSummary
    voice_source: str
    prior_body: str | None
    prior_content: core.ContentInput | None
    recent_openings: tuple[str, ...]
    loaded_version_id: uuid.UUID | None
    # The workspace hashtag registry (P2-M1.3).
    banned_tags: tuple[str, ...] = ()
    preferred_tags: tuple[str, ...] = ()
    # The account's measured tag performance (P3-M1.2).
    evidence: tuple[TagEvidence, ...] = ()


@dataclass(slots=True)
class _Written:
    variant_id: uuid.UUID
    nickname: str
    body: str
    embedding: list[float] | None


@dataclass(slots=True)
class DraftOutcome:
    drafted: int = 0
    failed: list[str] = field(default_factory=list)
    capped: bool = False
    cap_period: str = "daily"
    differentiated: int = 0
    superseded: list[str] = field(default_factory=list)


class Drafting:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        *,
        llm: LLMProvider,
        config: DraftingConfig,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sm = sm
        self._llm = llm
        self._cfg = config
        self._clock = clock
        self.writer = Writer(llm, config.writer)
        self.editor = Editor(llm, config.editor)
        self.hashtag = HashtagAgent(llm, config.hashtag)
        self.alt_text = AltTextAgent(
            llm, config.alt_text or ModelConfig(config.hashtag.model, config.hashtag.fallbacks)
        )
        self.media = MediaAgent(
            llm, config.media or ModelConfig(config.hashtag.model, config.hashtag.fallbacks)
        )
        self.engagement = EngagementAgent(
            llm, config.engagement or ModelConfig(config.hashtag.model, config.hashtag.fallbacks)
        )
        self.analytics = AnalyticsAgent(
            llm,
            config.analytics
            or config.engagement
            or ModelConfig(config.hashtag.model, config.hashtag.fallbacks),
        )

    @property
    def config(self) -> DraftingConfig:
        return self._cfg

    def _tx(self, workspace_id: uuid.UUID) -> Any:
        return workspace_transaction(self._sm, WorkspaceContext(workspace_id))

    async def draft(
        self,
        workspace_id: uuid.UUID,
        post_set_id: uuid.UUID,
        *,
        variant_ids: Sequence[uuid.UUID] | None = None,
        mode: str = "create",
        instruction: str | None = None,
        trigger: str = "manual",
        user_id: uuid.UUID | None = None,
    ) -> DraftOutcome:
        out = DraftOutcome()
        try:
            loaded = await self._load(workspace_id, post_set_id, variant_ids, mode)
        except Exception:
            await self._settle(workspace_id, post_set_id, "Drafting could not start; try again.")
            raise
        if loaded is None:
            # Nothing to draft (archived, or every account disabled meanwhile): never leave
            # the set looking queued.
            await self._settle(workspace_id, post_set_id, None)
            return out
        source, topic_brief, targets, siblings = loaded
        try:
            return await self._run(
                workspace_id,
                post_set_id,
                out,
                source,
                topic_brief,
                targets,
                siblings,
                mode=mode,
                instruction=instruction,
                trigger=trigger,
                user_id=user_id,
            )
        except Exception:
            log.exception("drafting.crashed", post_set_id=str(post_set_id))
            await self._settle(
                workspace_id, post_set_id, "Drafting stopped unexpectedly; try again."
            )
            raise

    async def _settle(
        self, workspace_id: uuid.UUID, post_set_id: uuid.UUID, error: str | None
    ) -> None:
        async with self._tx(workspace_id) as s:
            ps = await s.get(PostSet, post_set_id, with_for_update=True)
            if ps is None:
                return
            await core.refresh_set(s, ps)
            ps.generation_status = (
                GenerationStatus.FAILED if error else GenerationStatus.IDLE
            ).value
            ps.generation_error = error

    async def _run(
        self,
        workspace_id: uuid.UUID,
        post_set_id: uuid.UUID,
        out: DraftOutcome,
        source: SourceMaterial | None,
        topic_brief: str | None,
        targets: list[_Target],
        siblings: dict[str, list[_Written]],
        *,
        mode: str,
        instruction: str | None,
        trigger: str,
        user_id: uuid.UUID | None,
    ) -> DraftOutcome:
        run = await agent_runs.start_run(
            self._sm,
            workspace_id,
            workflow_key="revise_post" if mode == "revise" else "draft_from_content",
            trigger=trigger,
            subject_type="post_set",
            subject_id=post_set_id,
        )
        async with self._tx(workspace_id) as s:
            ps = await s.get(PostSet, post_set_id)
            assert ps is not None
            ps.generation_status = GenerationStatus.RUNNING.value
            ps.generation_error = None
            ps.last_run_id = run.run_id
        ctx = AgentContext(
            self._sm,
            run,
            self._cfg.daily_cost_cap_usd,
            clock=self._clock,
            monthly_cap_usd=self._cfg.monthly_cost_cap_usd,
        )

        aborted = False
        for t in targets:
            # A person can stop a run (P2-M1.7); the account in progress is finished first,
            # so no half-written version is ever stored.
            if await agent_runs.is_aborted(self._sm, run):
                aborted = True
                break
            platform_siblings = siblings.setdefault(t.platform, [])
            others = [w for w in platform_siblings if w.variant_id != t.variant_id]
            try:
                written = await self._draft_one(
                    ctx,
                    workspace_id,
                    t,
                    others,
                    source,
                    topic_brief,
                    mode,
                    instruction,
                    user_id,
                    out,
                    post_set_id,
                )
            except CostCapReached as exc:
                out.capped = True
                out.cap_period = exc.period
                break
            except LLMError as exc:
                log.warning("drafting.variant_failed", variant_id=str(t.variant_id), kind=exc.kind)
                out.failed.append(t.account.nickname)
                continue
            if written is not None:
                platform_siblings[:] = [
                    w for w in platform_siblings if w.variant_id != t.variant_id
                ]
                platform_siblings.append(written)

        errors = []
        if aborted:
            errors.append("Stopped; the remaining accounts were not drafted.")
        if out.capped:
            errors.append(
                f"{'Daily drafting' if out.cap_period == 'daily' else 'Monthly workspace'} "
                "cost cap reached; the remaining accounts were not drafted."
            )
        if out.failed:
            errors.append(f"Drafting failed for {', '.join(out.failed)}; try again.")
        if out.superseded:
            errors.append(
                f"{', '.join(out.superseded)} changed while drafting; their edits were kept."
            )
        async with self._tx(workspace_id) as s:
            ps = await s.get(PostSet, post_set_id, with_for_update=True)
            assert ps is not None
            await core.refresh_set(s, ps, run_id=run.run_id)
            ps.generation_status = (
                GenerationStatus.FAILED if errors and not out.drafted else GenerationStatus.IDLE
            ).value
            ps.generation_error = " ".join(errors) or None
        await agent_runs.finish_run(
            self._sm,
            run,
            state="aborted" if aborted else "failed" if errors and not out.drafted else "succeeded",
            error=" ".join(errors) or None,
        )
        return out

    # ------------------------------------------------------------------ one account

    async def _draft_one(
        self,
        ctx: AgentContext,
        workspace_id: uuid.UUID,
        t: _Target,
        others: list[_Written],
        source: SourceMaterial | None,
        topic_brief: str | None,
        mode: str,
        instruction: str | None,
        user_id: uuid.UUID | None,
        out: DraftOutcome,
        post_set_id: uuid.UUID,
    ) -> _Written | None:
        spec = core.platform_spec(t.platform)
        revising = mode == "revise" and t.prior_body is not None
        req = DraftRequest(
            account=t.account,
            group_name=t.group_name,
            voice=t.voice,
            platform=spec,
            source=source,
            topic_brief=topic_brief,
            mode="revise" if revising else "create",
            prior_body=t.prior_body if revising else None,
            instruction=instruction if revising else None,
            siblings=tuple(Sibling(w.nickname, w.body) for w in others),
            recent_openings=t.recent_openings,
            # The platform's writing skill: repurposing an article, or writing from a brief.
            playbook=skills.playbook(t.platform, "repurposer" if source else "writer"),
            playbook_name=f"{t.platform}:{'repurposer' if source else 'writer'}",
        )
        written: WriterOutput = await self.writer.run(ctx, req, post_variant_id=t.variant_id)
        embedding = await self._embed(
            ctx, core.embedding_text(written.post.body, written.post.thread_parts), t.variant_id
        )
        attempts = 0
        similarity = _closest(embedding, others)
        while (
            similarity
            and similarity[0] >= CROSS_ACCOUNT_BLOCK
            and attempts < DIFFERENTIATE_ATTEMPTS
        ):
            attempts += 1
            out.differentiated += 1
            sim, sibling = similarity
            retry = DraftRequest(
                **{
                    **{f: getattr(req, f) for f in req.__dataclass_fields__},
                    "mode": "differentiate",
                    "prior_body": written.post.body,
                    "too_similar_to": Sibling(sibling.nickname, sibling.body),
                    "measured_similarity": sim,
                    "attempt": attempts,
                }
            )
            written = await self.writer.run(ctx, retry, post_variant_id=t.variant_id)
            embedding = await self._embed(
                ctx, core.embedding_text(written.post.body, written.post.thread_parts), t.variant_id
            )
            similarity = _closest(embedding, others)

        body = written.post.body
        # The link is not the model's to choose (review H5): a set from an article links
        # that article; otherwise only a public http(s) link survives.
        link = source.url if source and source.url else _safe_link(written.post.link_url)
        if revising and t.prior_content is not None:
            tags = list(t.prior_content.hashtags)
            suggestions: list[dict[str, Any]] = []
        else:
            try:
                tag_out = await self.hashtag.run(
                    ctx,
                    HashtagRequest(
                        account=t.account,
                        platform=spec,
                        body=body,
                        topics=source.topics if source else (),
                        banned_tags=(*t.voice.banned_words, *t.banned_tags),
                        preferred_tags=t.preferred_tags,
                        playbook=skills.playbook(t.platform, "hashtags"),
                        evidence=t.evidence,
                    ),
                    post_variant_id=t.variant_id,
                )
                ranked = apply_evidence(tag_out.tags, t.evidence)
                suggestions = [x.model_dump() for x in ranked]
                ceiling = min(spec.conventional_hashtags[1], spec.max_hashtags or 99)
                never = {b.lower() for b in t.banned_tags}
                tags = [x.tag for x in ranked if x.tag.lstrip("#").lower() not in never][:ceiling]
            except Exception as exc:
                log.warning("drafting.hashtag_failed", error=str(exc))
                tags = []
                suggestions = []
        review: EditorOutput = await self.editor.run(
            ctx,
            EditRequest(
                account=t.account,
                voice=t.voice,
                platform=spec,
                body=body,
                hashtags=tuple(tags),
                link_url=link,
                source=source,
                topic_brief=topic_brief,
                siblings=tuple(Sibling(w.nickname, w.body) for w in others),
                thread_parts=tuple(written.post.thread_parts)
                if written.post.thread_parts
                else None,
                playbook=skills.playbook(t.platform, "humanizer"),
            ),
            post_variant_id=t.variant_id,
        )
        issues, confidence = _reconcile(review)
        notes = {
            "angle": written.post.angle,
            "hook_type": written.post.hook_type,
            "self_critique": written.post.self_critique,
            "source_attribution": written.source_attribution,
            "voice_name": t.voice.name,
            "voice_source": t.voice_source,
            "voice_match": review.voice_match,
            "confidence_rationale": review.confidence_rationale,
            "factual_claims": [c.model_dump() for c in review.factual_claims],
            "differentiate_attempts": attempts,
            "closest_sibling_similarity": round(similarity[0], 4) if similarity else None,
        }
        async with self._tx(workspace_id) as s:
            ps = await s.get(PostSet, post_set_id, with_for_update=True)
            variant = await s.get(PostVariant, t.variant_id, with_for_update=True)
            if ps is None or variant is None or not variant.enabled:
                return None
            if variant.current_version_id != t.loaded_version_id:
                # Someone saved this account's text while the model was writing: their
                # edit wins, and the run says so (review M4).
                out.superseded.append(t.account.nickname)
                return None
            await core.create_version(
                s,
                variant,
                core.ContentInput(
                    body=body,
                    thread_parts=written.post.thread_parts,
                    hashtags=tags,
                    link_url=link,
                    media=list(t.prior_content.media) if t.prior_content else [],
                ),
                source="agent",
                embedding=embedding,
                voice=t.voice,
                run_id=ctx.run.run_id,
                user_id=user_id,
                revision_instruction=instruction if revising else None,
                confidence=confidence,
                compliance_flags=issues,
                agent_notes=notes,
                hashtag_suggestions=suggestions,
            )
            # The stored composite and states are current after every version (review H4).
            await core.refresh_set(s, ps, run_id=ctx.run.run_id)
        out.drafted += 1
        return _Written(t.variant_id, t.account.nickname, body, embedding)

    async def _embed(
        self, ctx: AgentContext, text: str, variant_id: uuid.UUID
    ) -> list[float] | None:
        try:
            emb = await self._llm.embed(self._cfg.embedding_model, [text or "(empty)"])
        except LLMError as exc:
            async with self._tx(ctx.workspace_id) as s:
                await agent_runs.record_step(
                    s,
                    ctx.run,
                    agent_key="writer",
                    step_type="embedding",
                    usage=exc.usage,
                    state="failed",
                    error=f"{exc.kind}",
                    post_variant_id=variant_id,
                )
            return None
        async with self._tx(ctx.workspace_id) as s:
            await agent_runs.record_step(
                s,
                ctx.run,
                agent_key="writer",
                step_type="embedding",
                usage=emb.usage,
                state="succeeded",
                input_redacted={"chars": len(text)},
                post_variant_id=variant_id,
            )
        return emb.vectors[0]

    async def embed_for_version(
        self, ctx: AgentContext, text: str, variant_id: uuid.UUID
    ) -> list[float] | None:
        return await self._embed(ctx, text, variant_id)

    # ---------------------------------------------------------------------- loading

    async def _load(
        self,
        workspace_id: uuid.UUID,
        post_set_id: uuid.UUID,
        variant_ids: Sequence[uuid.UUID] | None,
        mode: str,
    ) -> tuple[SourceMaterial | None, str | None, list[_Target], dict[str, list[_Written]]] | None:
        async with self._tx(workspace_id) as s:
            ps = await s.get(PostSet, post_set_id)
            ws = await s.get(Workspace, workspace_id)
            if (
                ps is None
                or ps.state == PostSetState.ARCHIVED.value
                or ws is None
                or ws.archived_at is not None
                or ws.deleted_at is not None
            ):
                return None
            source = await _source_material(s, ps.content_item_id)
            variants = list(
                (
                    await s.execute(
                        select(PostVariant)
                        .where(
                            PostVariant.post_set_id == post_set_id, PostVariant.enabled.is_(True)
                        )
                        .order_by(PostVariant.position)
                    )
                ).scalars()
            )
            wanted = set(variant_ids) if variant_ids else None
            current = await _current_versions(
                s, [v.current_version_id for v in variants if v.current_version_id]
            )
            siblings: dict[str, list[_Written]] = {}
            targets: list[_Target] = []
            registry_banned, registry_preferred = await registry_tags(s)
            for v in variants:
                account = await s.get(SocialAccount, v.social_account_id)
                assert account is not None
                summary = AccountSummary(
                    nickname=account.nickname,
                    handle=account.handle,
                    account_type=account.account_type,
                    platform=v.platform,
                    platform_name=core.platform_name(v.platform),
                )
                ver = current.get(v.current_version_id) if v.current_version_id else None
                if wanted is not None and v.id not in wanted:
                    if ver is not None:  # an untouched sibling the Writer must differ from
                        siblings.setdefault(v.platform, []).append(
                            _Written(
                                v.id,
                                summary.nickname,
                                ver.body,
                                list(ver.embedding) if ver.embedding is not None else None,
                            )
                        )
                    continue
                if mode == "revise" and ver is None:
                    continue
                if v.state not in core.EDITABLE_STATES:
                    continue  # approved or later: not the drafting job's to change
                voice_row = await s.get(BrandVoice, v.brand_voice_id) if v.brand_voice_id else None
                voice_source = "variant"
                if voice_row is None:
                    voice_row, voice_source = await core.resolve_voice(
                        s, account, v.via_account_group_id
                    )
                group = (
                    await s.get(AccountGroup, v.via_account_group_id)
                    if v.via_account_group_id
                    else None
                )
                targets.append(
                    _Target(
                        variant_id=v.id,
                        platform=v.platform,
                        account=summary,
                        group_name=group.name if group else None,
                        voice=core.voice_summary(voice_row),
                        voice_source=voice_source,
                        prior_body=ver.body if ver else None,
                        prior_content=core.ContentInput(
                            ver.body,
                            ver.thread_parts,
                            list(ver.hashtags),
                            ver.link_url,
                            list(ver.media),
                        )
                        if ver
                        else None,
                        recent_openings=await _recent_openings(s, account.id, post_set_id),
                        loaded_version_id=v.current_version_id,
                        banned_tags=tuple(sorted(registry_banned)),
                        preferred_tags=tuple(sorted(registry_preferred)),
                        evidence=await account_evidence(s, account.id),
                    )
                )
            if not targets:
                return None
            ps.generation_status = GenerationStatus.RUNNING.value
        return source, ps.topic_brief, targets, siblings


def _safe_link(url: str | None) -> str | None:
    if not url or len(url) > 2048 or validate_public_url(url) is not None:
        return None
    return url


def _reconcile(review: EditorOutput) -> tuple[list[dict[str, Any]], float]:
    """The Editor's verdict, checked in code (review M3): an unsupported factual claim is a
    block even if the model did not file one, and confidence cannot exceed what its own
    issues allow."""
    issues = [i.model_dump() for i in review.issues]
    for claim in review.factual_claims:
        if not claim.supported and not any(i["category"] == "factual" for i in issues):
            issues.append(
                {
                    "category": "factual",
                    "severity": "block",
                    "excerpt": claim.claim[:500],
                    "explanation": "The Editor found no support for this claim in the source.",
                    "suggested_fix": "Remove the claim or ground it in the source.",
                }
            )
    severities = {i["severity"] for i in issues}
    confidence = review.confidence_score
    if "block" in severities:
        confidence = min(confidence, 0.3)
    elif "warning" in severities:
        confidence = min(confidence, 0.6)
    return issues, confidence


def _closest(
    embedding: list[float] | None, others: list[_Written]
) -> tuple[float, _Written] | None:
    if embedding is None:
        return None
    scored = [(core.cosine(embedding, w.embedding), w) for w in others if w.embedding is not None]
    return max(scored, key=lambda x: x[0], default=None)


async def _current_versions(s: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, PostVersion]:
    if not ids:
        return {}
    return {
        v.id: v
        for v in (await s.execute(select(PostVersion).where(PostVersion.id.in_(ids)))).scalars()
    }


async def _source_material(
    s: AsyncSession, content_item_id: uuid.UUID | None
) -> SourceMaterial | None:
    if content_item_id is None:
        return None
    item = await s.get(ContentItem, content_item_id)
    if item is None:
        return None
    pub = None
    if item.source_id:
        src = await s.get(Source, item.source_id)
        pub = src.name if src else None
    return SourceMaterial(
        title=item.title,
        url=item.url,
        publication=pub,
        summary=item.summary,
        excerpt=(item.extracted_text or "")[:4000] or None,
        topics=tuple(item.topics or ()),
    )


async def _recent_openings(
    s: AsyncSession, account_id: uuid.UUID, exclude_set: uuid.UUID
) -> tuple[str, ...]:
    rows = (
        await s.execute(
            select(PostVersion.body)
            .join(PostVariant, PostVariant.current_version_id == PostVersion.id)
            .where(
                PostVariant.social_account_id == account_id, PostVariant.post_set_id != exclude_set
            )
            .order_by(PostVersion.created_at.desc())
            .limit(5)
        )
    ).scalars()
    return tuple(b.split("\n", 1)[0][:90] for b in rows)
