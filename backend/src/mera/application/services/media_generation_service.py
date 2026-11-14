"""Media generation (P2-M5, docs/04 §4.7, docs/06 §4.5): estimate, request, run, attach.

The order of checks is the point of this module:

1. **Plan.** Without a prompt, the Media agent writes one per image from the post and the
   account (a carousel is planned together), with alt text; a deterministic screen and
   the agent's verdict refuse unsafe imagery.
2. **Cost, before anything is spent.** The estimate (pure, no network) is checked against
   the workspace's monthly budget (a hard stop) and its per-request ceiling (above it, a
   second factor is required, docs/09 §5.3). A refused request makes no provider call.
3. **A tracked job.** Each image is a ``media_job`` on the ``media`` queue, with a timeout
   and bounded retries. Its cost is recorded whether or not the result was usable.
4. **A new version.** A result requested for a variant is attached through
   ``MediaService.set_variant_media``, which makes a new version and so invalidates that
   account's approval and only that one. If the post changed since the request, the image
   stays in the library and nothing is overwritten.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application import second_factor
from mera.application.agents.base import AgentContext, CostCapReached
from mera.application.agents.media import MAX_IMAGES, MediaBrief, MediaPlan, screen
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.drafting import core
from mera.application.drafting.pipeline import Drafting
from mera.application.errors import (
    ConflictError,
    InvalidInput,
    NotFoundError,
    ServiceUnavailableError,
)
from mera.application.ingest import agent_runs
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.media_service import MediaChoice, MediaService
from mera.application.services.principals import acting_user, load_user_principal
from mera.config import Settings
from mera.domain.context import WorkspaceContext
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.ports.llm import LLMError
from mera.domain.ports.media_generator import GenerationError, GenerationRequest, MediaGenerator
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    MediaJob,
    PostSet,
    PostVariant,
    PostVersion,
    SocialAccount,
    User,
    Workspace,
)
from mera.infrastructure.db.repositories.audit import record
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.media import images
from mera.infrastructure.queue.deferrer import Deferrer

log = structlog.get_logger(__name__)

TASK: Final = "media:generate"
MAX_ATTEMPTS: Final = 3
LIVE: Final = ("queued", "running")
ASPECTS: Final[dict[str, tuple[int, int]]] = {
    "square": (1024, 1024),
    "landscape": (1536, 1024),
    "portrait": (1024, 1536),
}
QUALITIES: Final = ("low", "medium", "high")
CENT: Final = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class Limits:
    request_ceiling_usd: Decimal
    monthly_budget_usd: Decimal


@dataclass(frozen=True, slots=True)
class EstimateView:
    kind: str
    count: int
    cost_usd: Decimal
    seconds: int
    request_ceiling_usd: Decimal
    month_spent_usd: Decimal
    monthly_budget_usd: Decimal
    needs_second_factor: bool
    over_budget: bool
    provider: str


@dataclass(frozen=True, slots=True)
class JobView:
    id: uuid.UUID
    kind: str
    state: str
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


def _job_view(j: MediaJob) -> JobView:
    return JobView(
        j.id,
        j.kind,
        j.state,
        j.prompt,
        j.alt_text,
        j.provider,
        j.model,
        j.post_set_id,
        j.post_variant_id,
        j.batch_id,
        j.slot,
        j.estimated_cost_usd,
        j.actual_cost_usd,
        j.attempt_count,
        j.error,
        j.result_asset_id,
        j.created_at,
        j.finished_at,
    )


def aspect_for(platform: str) -> str:
    """The generation aspect closest to what the platform shows best."""
    if platform == "instagram":
        return "portrait"
    if platform in ("x", "linkedin_member", "linkedin_organization", "facebook"):
        return "landscape"
    return "square"


def _month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def month_spent(s: AsyncSession, workspace_id: uuid.UUID) -> Decimal:
    """What the month has cost so far: actual costs, plus the estimates of jobs that are
    still queued or running (they are committed spend until they finish)."""
    total = await s.scalar(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(
                        MediaJob.actual_cost_usd,
                        case((MediaJob.state.in_(LIVE), MediaJob.estimated_cost_usd), else_=0),
                    )
                ),
                0,
            )
        ).where(MediaJob.workspace_id == workspace_id, MediaJob.created_at >= _month_start())
    )
    return Decimal(str(total or 0)).quantize(CENT)


class MediaGenerationService(WorkspaceScopedService):
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        settings: Settings,
        generator: MediaGenerator | None,
        media: MediaService,
        deferrer: Deferrer,
        drafting: Drafting | None = None,
        *,
        approvals: Any | None = None,
        registry: Any | None = None,
    ) -> None:
        super().__init__(sm)
        self._settings = settings
        self._gen = generator
        self._media = media
        self._deferrer = deferrer
        self._drafting = drafting
        self._approvals = approvals  # asks again after a regenerate from a card
        self._registry = registry  # McpRegistryService, for MCP image tools (P2-M9.9)

    def price(
        self, count: int = 1, aspect: str = "square", quality: str = "medium"
    ) -> Decimal | None:
        """The estimate shown on a button (no checks, no network); None when unavailable."""
        if self._gen is None or "image" not in self._gen.kinds:
            return None
        one = self._gen.estimate(self._request("image", "estimate", aspect, quality, None))
        return (one.cost_usd * max(1, count)).quantize(Decimal("0.01"))

    # ------------------------------------------------------------------ limits

    def limits_for(self, ws: Workspace | None) -> Limits:
        conf = ((ws.settings if ws else None) or {}).get("media") or {}
        return Limits(
            Decimal(str(conf.get("request_ceiling_usd", self._settings.media_request_ceiling_usd))),
            Decimal(str(conf.get("monthly_budget_usd", self._settings.media_monthly_budget_usd))),
        )

    async def set_limits(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        request_ceiling_usd: Decimal | None,
        monthly_budget_usd: Decimal | None,
    ) -> dict[str, Any]:
        self._require_human(user)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.MANAGE_WORKSPACE, workspace_id)
            ws = await s.get(Workspace, workspace_id, with_for_update=True)
            assert ws is not None
            self._ensure_writable(ws)
            before = self.limits_for(ws)
            settings = dict(ws.settings or {})
            conf = dict(settings.get("media") or {})
            if request_ceiling_usd is not None:
                conf["request_ceiling_usd"] = str(request_ceiling_usd.quantize(CENT))
            if monthly_budget_usd is not None:
                conf["monthly_budget_usd"] = str(monthly_budget_usd.quantize(CENT))
            settings["media"] = conf
            ws.settings = settings
            after = self.limits_for(ws)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "workspace.media_limits",
                    workspace_id=workspace_id,
                    subject_type="workspace",
                    subject_id=workspace_id,
                    before={k: str(v) for k, v in vars_of(before).items()},
                    after={k: str(v) for k, v in vars_of(after).items()},
                ),
            )
        return await self.spend(user, workspace_id, meta)

    async def spend(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> dict[str, Any]:
        """The month so far (UTC calendar month), for Settings."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            limits = self.limits_for(ws)
            spent = await month_spent(s, workspace_id)
            rows = (
                await s.execute(
                    select(MediaJob.state, func.count())
                    .where(
                        MediaJob.workspace_id == workspace_id,
                        MediaJob.created_at >= _month_start(),
                    )
                    .group_by(MediaJob.state)
                )
            ).all()
        return {
            "month_start": _month_start().isoformat(),
            "month_spent_usd": spent,
            "monthly_budget_usd": limits.monthly_budget_usd,
            "request_ceiling_usd": limits.request_ceiling_usd,
            "jobs": {state: int(n) for state, n in rows},
            "provider": self._gen.name if self._gen else None,
            "video_enabled": self._settings.media_video_enabled,
        }

    # ---------------------------------------------------------------- estimate

    def _check_kind(self, kind: str) -> MediaGenerator:
        if kind not in ("image", "video"):
            raise InvalidInput("Choose image or video.", code="invalid_kind", field="kind")
        if kind == "video" and not self._settings.media_video_enabled:
            raise ConflictError(
                "Video generation is switched off on this deployment.", code="video_disabled"
            )
        if self._gen is None or kind not in self._gen.kinds:
            raise ServiceUnavailableError(f"{kind.capitalize()} generation is not configured here.")
        return self._gen

    @staticmethod
    def _request(
        kind: str, prompt: str, aspect: str, quality: str, seed: int | None
    ) -> GenerationRequest:
        if aspect not in ASPECTS:
            raise InvalidInput("Unknown aspect.", code="invalid_aspect", field="aspect")
        if quality not in QUALITIES:
            raise InvalidInput("Unknown quality.", code="invalid_quality", field="quality")
        w, h = ASPECTS[aspect]
        return GenerationRequest(kind, prompt, w, h, quality, seed)

    async def _estimate(
        self,
        s: AsyncSession,
        workspace_id: uuid.UUID,
        kind: str,
        aspect: str,
        quality: str,
        count: int,
    ) -> EstimateView:
        gen = self._check_kind(kind)
        if not 1 <= count <= MAX_IMAGES:
            raise InvalidInput(
                f"Between 1 and {MAX_IMAGES} images.", code="invalid_count", field="count"
            )
        one = gen.estimate(self._request(kind, "estimate", aspect, quality, None))
        cost = (one.cost_usd * count).quantize(CENT)
        ws = await s.get(Workspace, workspace_id)
        limits = self.limits_for(ws)
        spent = await month_spent(s, workspace_id)
        return EstimateView(
            kind=kind,
            count=count,
            cost_usd=cost,
            seconds=one.seconds,
            request_ceiling_usd=limits.request_ceiling_usd,
            month_spent_usd=spent,
            monthly_budget_usd=limits.monthly_budget_usd,
            needs_second_factor=kind == "video" or cost > limits.request_ceiling_usd,
            over_budget=spent + cost > limits.monthly_budget_usd,
            provider=gen.name,
        )

    async def estimate(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        kind: str = "image",
        aspect: str = "square",
        quality: str = "medium",
        count: int = 1,
    ) -> EstimateView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.UPLOAD_MEDIA, workspace_id)
            return await self._estimate(s, workspace_id, kind, aspect, quality, count)

    # ----------------------------------------------------------------- request

    async def generate(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        kind: str = "image",
        prompt: str | None = None,
        aspect: str = "square",
        quality: str = "medium",
        count: int = 1,
        post_set_id: uuid.UUID | None = None,
        post_variant_id: uuid.UUID | None = None,
        instruction: str | None = None,
        attach: bool = False,
    ) -> list[JobView]:
        """Queue ``count`` images. With a variant and no prompt, the Media agent plans them;
        with ``attach``, the results replace the variant's media as a new version."""
        self._check_kind(kind)
        target: dict[str, Any] | None = None
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.UPLOAD_MEDIA, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            self._ensure_writable(ws)
            if post_variant_id is not None:
                target = await self._target(s, post_set_id, post_variant_id)
                post_set_id = target["post_set_id"]
                if attach:
                    # The same checks the attach will make, before anything is spent:
                    # edit rights on that account, a writable set, the current version.
                    await self._media._editable_variant(
                        s,
                        user,
                        workspace_id,
                        target["post_set_id"],
                        post_variant_id,
                        uuid.UUID(target["version_id"]),
                    )
            elif post_set_id is not None and await s.get(PostSet, post_set_id) is None:
                raise NotFoundError("Post set not found.")
        if attach and target is None:
            raise InvalidInput(
                "Say which account's post the image is for.",
                code="variant_required",
                field="post_variant_id",
            )

        # 1. Plan (and screen) the prompts.
        alts: list[str | None]
        if prompt:
            prompts = [prompt.strip()[:1500]] * count
            alts = [None] * count
        elif target is not None:
            plan = await self._plan(workspace_id, target, count, instruction, aspect, user)
            prompts, alts = plan.prompts[:count], list(plan.alt_texts[:count])
            count = len(prompts)
        else:
            raise InvalidInput("Describe the image.", code="prompt_required", field="prompt")
        for p in prompts:
            reason = screen(p)
            if reason:
                raise InvalidInput(reason, code="unsafe_prompt", field="prompt")

        # 2. Cost, before any provider call; 3. the jobs, in the same transaction.
        jobs: list[MediaJob] = []
        async with self._tx(user, workspace_id, meta) as s:
            await s.execute(
                select(Workspace.id).where(Workspace.id == workspace_id).with_for_update()
            )  # one budget check at a time per workspace
            est = await self._estimate(s, workspace_id, kind, aspect, quality, count)
            if est.over_budget:
                raise ConflictError(
                    f"This month's media budget (${est.monthly_budget_usd}) would be exceeded: "
                    f"${est.month_spent_usd} spent, this needs ${est.cost_usd}.",
                    code="media_budget_spent",
                )
            if est.needs_second_factor:
                # Above the ceiling, or video: a second factor, or nothing is queued.
                await second_factor.require(s, user, workspace_id, "video_generate", meta)
            batch = uuid7() if count > 1 else None
            per = (est.cost_usd / count).quantize(CENT)
            for i, p in enumerate(prompts):
                job = MediaJob(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    post_set_id=post_set_id,
                    post_variant_id=post_variant_id if attach else None,
                    batch_id=batch,
                    slot=i,
                    kind=kind,
                    provider=est.provider,
                    prompt=p,
                    alt_text=alts[i],
                    parameters={
                        "aspect": aspect,
                        "quality": quality,
                        "seed": int(uuid.uuid4().int % 2**31),
                        "count": count,
                        "based_on_version_id": target["version_id"] if target else None,
                        "for_variant_id": str(post_variant_id) if post_variant_id else None,
                    },
                    estimated_cost_usd=per,
                    requested_by_user_id=user.user_id,
                    surface=meta.surface,
                )
                s.add(job)
                jobs.append(job)
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "media_job.requested",
                    workspace_id=workspace_id,
                    subject_type="media_job",
                    subject_id=jobs[0].id,
                    after={
                        "kind": kind,
                        "count": count,
                        "estimated_cost_usd": str(est.cost_usd),
                        "attach_to_variant": str(post_variant_id) if attach else None,
                        "second_factor": est.needs_second_factor,
                    },
                ),
            )
            for j in jobs:
                await s.refresh(j)
            views = [_job_view(j) for j in jobs]
        for j in jobs:
            await self._deferrer.defer(
                TASK,
                queue="media",
                queueing_lock=f"media:{j.id}",
                workspace_id=str(workspace_id),
                job_id=str(j.id),
            )
        return views

    async def regenerate(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        set_id: uuid.UUID,
        variant_id: uuid.UUID,
        *,
        instruction: str | None = None,
        quality: str = "medium",
    ) -> list[JobView]:
        """New images for one account's post, as many as it has now (a carousel stays a
        carousel), planned by the Media agent and attached as a new version."""
        async with self._tx(user, workspace_id, meta) as s:
            await load_user_principal(s, user, workspace_id)
            target = await self._target(s, set_id, variant_id)
        count = max(1, min(MAX_IMAGES, target["media_count"]))
        return await self.generate(
            user,
            workspace_id,
            meta,
            quality=quality,
            count=count,
            aspect=target["aspect"],
            post_set_id=set_id,
            post_variant_id=variant_id,
            instruction=instruction,
            attach=True,
        )

    async def _target(
        self, s: AsyncSession, set_id: uuid.UUID | None, variant_id: uuid.UUID
    ) -> dict[str, Any]:
        v = await s.get(PostVariant, variant_id)
        if v is None or (set_id is not None and v.post_set_id != set_id):
            raise NotFoundError("Variant not found.")
        if v.current_version_id is None:
            raise ConflictError(
                "Draft this account's text before adding images.", code="not_drafted"
            )
        version = await s.get(PostVersion, v.current_version_id)
        account = await s.get(SocialAccount, v.social_account_id)
        assert version is not None
        assert account is not None
        aspect = aspect_for(v.platform)
        return {
            "post_set_id": v.post_set_id,
            "variant_id": v.id,
            "version_id": str(version.id),
            "platform": v.platform,
            "account": account.nickname,
            "body": version.body,
            "media_count": len(version.media or []),
            "aspect": aspect,
        }

    async def _plan(
        self,
        workspace_id: uuid.UUID,
        target: dict[str, Any],
        count: int,
        instruction: str | None,
        aspect: str,
        user: AuthenticatedUser,
    ) -> MediaPlan:
        if self._drafting is None:
            raise ServiceUnavailableError("The Media agent is not configured here.")
        run = await agent_runs.start_run(
            self._sm,
            workspace_id,
            workflow_key="media",
            trigger="manual",
            subject_type="post_variant",
            subject_id=target["variant_id"],
        )
        ctx = AgentContext(
            self._sm,
            run,
            self._drafting.config.daily_cost_cap_usd,
            monthly_cap_usd=self._drafting.config.monthly_cost_cap_usd,
        )
        try:
            plan = await self._drafting.media.run(
                ctx,
                MediaBrief(
                    post_text=target["body"],
                    platform_name=core.platform_name(target["platform"]),
                    account_name=target["account"],
                    count=count,
                    instruction=instruction,
                    aspect=aspect,
                ),
            )
        except CostCapReached as exc:
            await agent_runs.finish_run(self._sm, run, state="failed", error=str(exc))
            raise ServiceUnavailableError("The AI budget for today is spent.") from exc
        except LLMError as exc:
            await agent_runs.finish_run(self._sm, run, state="failed", error=exc.kind)
            raise ServiceUnavailableError("The Media agent is unavailable right now.") from exc
        await agent_runs.finish_run(self._sm, run, state="succeeded")
        if not plan.safe:
            raise InvalidInput(
                plan.reason or "This post cannot be illustrated safely; describe an image instead.",
                code="unsafe_prompt",
                field="prompt",
            )
        return plan

    # ------------------------------------------------------------------- jobs

    async def list_jobs(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        state: str | None = None,
        post_set_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[JobView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = select(MediaJob).order_by(MediaJob.created_at.desc()).limit(min(limit, 200))
            if state:
                q = q.where(MediaJob.state == state)
            if post_set_id:
                q = q.where(MediaJob.post_set_id == post_set_id)
            return [_job_view(j) for j in (await s.execute(q)).scalars()]

    async def get_job(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, job_id: uuid.UUID
    ) -> JobView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            job = await s.get(MediaJob, job_id)
            if job is None:
                raise NotFoundError("Job not found.")
            return _job_view(job)

    async def cancel(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, job_id: uuid.UUID
    ) -> JobView:
        """A queued job is never sent. A running one finishes at the provider (and costs
        what it costs), but its result is not attached."""
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.UPLOAD_MEDIA, workspace_id)
            job = await s.get(MediaJob, job_id, with_for_update=True)
            if job is None:
                raise NotFoundError("Job not found.")
            if job.state in LIVE:
                job.state = "cancelled"
                job.finished_at = datetime.now(UTC)
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "media_job.cancelled",
                        workspace_id=workspace_id,
                        subject_type="media_job",
                        subject_id=job.id,
                    ),
                )
            await s.flush()
            await s.refresh(job)
            return _job_view(job)

    # ------------------------------------------------------------------ worker

    async def run_job(self, workspace_id: uuid.UUID, job_id: uuid.UUID) -> str:
        """One attempt, from the ``media`` queue. Returns the job's state afterwards."""
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            job = await s.get(MediaJob, job_id, with_for_update=True)
            if job is None or job.state != "queued":
                return job.state if job else "missing"
            if self._gen is None:
                job.state, job.error = "failed", "no media provider is configured"
                job.finished_at = datetime.now(UTC)
                return job.state
            job.state = "running"
            job.attempt_count += 1
            job.started_at = datetime.now(UTC)
            p = dict(job.parameters or {})
            req = self._request(
                job.kind,
                job.prompt,
                str(p.get("aspect", "square")),
                str(p.get("quality", "medium")),
                p.get("seed"),
            )
            attempt = job.attempt_count
            estimated = job.estimated_cost_usd
        outcome: dict[str, Any]
        gen = await self._generator_for(workspace_id)
        try:
            result = await asyncio.wait_for(
                gen.generate(req), timeout=self._settings.media_job_timeout_s
            )
            info = images.probe(result.data)
            outcome = {"ok": result, "info": info}
        except TimeoutError:
            # The provider may have charged for a result that never arrived: count it.
            outcome = {
                "state": "timed_out",
                "error": "the provider took too long",
                "cost": estimated,
            }
        except GenerationError as exc:
            retry = exc.retryable and attempt < MAX_ATTEMPTS
            outcome = {
                "state": "queued" if retry else "failed",
                "error": str(exc)[:500],
                "cost": exc.cost_usd,
            }
        except images.ImageRejected as exc:
            outcome = {
                "state": "failed",
                "error": f"unusable image: {exc.message}",
                "cost": estimated,
            }

        if "ok" not in outcome:
            async with workspace_transaction(self._sm, ctx) as s:
                job = await s.get(MediaJob, job_id, with_for_update=True)
                assert job is not None
                prior = job.actual_cost_usd or Decimal(0)
                job.actual_cost_usd = (prior + Decimal(outcome["cost"])).quantize(CENT)
                job.error = outcome["error"]
                if job.state == "cancelled":
                    return job.state
                job.state = outcome["state"]
                if job.state != "queued":
                    job.finished_at = datetime.now(UTC)
            if outcome["state"] == "queued":
                await self._deferrer.defer(
                    TASK,
                    queue="media",
                    queueing_lock=f"media:{job_id}:{attempt}",
                    workspace_id=str(workspace_id),
                    job_id=str(job_id),
                )
            log.info("media_job.attempt_failed", job=str(job_id), state=outcome["state"])
            return str(outcome["state"])

        result = outcome["ok"]
        async with workspace_transaction(self._sm, ctx) as s:
            job = await s.get(MediaJob, job_id, with_for_update=True)
            assert job is not None
            job.actual_cost_usd = ((job.actual_cost_usd or Decimal(0)) + result.cost_usd).quantize(
                CENT
            )
            job.model = result.model
            if job.state == "cancelled":
                return job.state  # paid for, but not kept
            requester = (
                await s.get(User, job.requested_by_user_id) if job.requested_by_user_id else None
            )
        if requester is None:
            await self._finish(ctx, job_id, "failed", error="the person who asked has left")
            return "failed"
        who = acting_user(requester, mfa_enforced=self._settings.mfa_enforced)
        meta = RequestMeta(surface="worker")
        asset = await self._media.ingest_generated(
            who, workspace_id, meta, result.data, outcome["info"], alt=job.alt_text
        )
        await self._finish(ctx, job_id, "succeeded", asset_id=asset.id)
        await self._maybe_attach(who, workspace_id, meta, job_id)
        return "succeeded"

    async def _generator_for(self, workspace_id: uuid.UUID) -> MediaGenerator:
        """The workspace's MCP image tool when it chose one (Settings), else the deployment's."""
        assert self._gen is not None
        if self._registry is None:
            return self._gen
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            ws = await s.get(Workspace, workspace_id)
        conf = ((ws.settings if ws else None) or {}).get("media") or {}
        tool = conf.get("mcp_tool")
        if conf.get("provider") != "mcp" or not tool:
            return self._gen
        from mera.infrastructure.media.generators import McpMediaGenerator

        return McpMediaGenerator(
            self._registry, workspace_id, str(tool), Decimal(str(conf.get("mcp_cost_usd", "0.04")))
        )

    async def _finish(
        self,
        ctx: WorkspaceContext,
        job_id: uuid.UUID,
        state: str,
        *,
        asset_id: uuid.UUID | None = None,
        error: str | None = None,
    ) -> None:
        async with workspace_transaction(self._sm, ctx) as s:
            await s.execute(
                update(MediaJob)
                .where(MediaJob.id == job_id)
                .values(
                    state=state,
                    result_asset_id=asset_id,
                    error=error,
                    finished_at=datetime.now(UTC),
                )
            )

    async def _maybe_attach(
        self, who: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, job_id: uuid.UUID
    ) -> None:
        """Attach once every image of the request has succeeded, in slot order."""
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            job = await s.get(MediaJob, job_id)
            assert job is not None
            if job.post_variant_id is None or job.post_set_id is None:
                return
            batch = [job]
            if job.batch_id is not None:
                batch = list(
                    (
                        await s.execute(
                            select(MediaJob)
                            .where(MediaJob.batch_id == job.batch_id)
                            .order_by(MediaJob.slot)
                            .with_for_update()
                        )
                    ).scalars()
                )
            if any(j.state != "succeeded" for j in batch):
                return  # the last one to finish attaches the set
            if any((j.parameters or {}).get("attached") for j in batch):
                return
            for j in batch:
                j.parameters = {**(j.parameters or {}), "attached": True}
            set_id, variant_id = job.post_set_id, job.post_variant_id
            from_card = job.surface in ("telegram", "whatsapp")
            based_on = (job.parameters or {}).get("based_on_version_id")
            choices = [
                MediaChoice(j.result_asset_id, alt_text=j.alt_text)
                for j in batch
                if j.result_asset_id is not None
            ]
            batch_ids = [j.id for j in batch]
        try:
            await self._media.set_variant_media(
                who,
                workspace_id,
                meta,
                set_id,
                variant_id,
                choices=choices,
                based_on_version_id=uuid.UUID(based_on) if based_on else None,
            )
        except (ConflictError, InvalidInput, NotFoundError) as exc:
            # The post changed since the request (or can no longer be edited): never
            # overwrite someone's newer work. The images stay in the library.
            note = f"kept in the library, not attached: {exc}"[:500]
            async with workspace_transaction(self._sm, ctx) as s:
                await s.execute(
                    update(MediaJob).where(MediaJob.id.in_(batch_ids)).values(error=note)
                )
            log.info("media_job.not_attached", job=str(job_id), reason=str(exc))
            return
        if from_card and self._approvals is not None:
            # Regenerated from an approval card: ask again for exactly that account.
            await self._approvals.resubmit_after_edit(
                workspace_id, set_id, [variant_id], who.user_id
            )


def vars_of(limits: Limits) -> dict[str, Decimal]:
    return {
        "request_ceiling_usd": limits.request_ceiling_usd,
        "monthly_budget_usd": limits.monthly_budget_usd,
    }
