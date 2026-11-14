"""Approval requests: submit, decide, cancel, read (M6.5, M6.11; docs/07, ADR-0014).

One path for every surface. The web (and API keys) decide with the content hashes the
approver was shown; a bot decides with a single-use token whose row names its scope and
hash. Both arrive at ``_apply``, under a ``FOR UPDATE`` lock on the request, so two
approvers acting at once serialise across the whole set.

Rules that carry the weight:

- **Per account.** Every decision names post variant ids (or none, meaning every pending
  one). A scope naming an account the approver may not approve fails whole (docs/04 §4.9).
- **Bound to content.** A decision must carry the hash of each account's post as the
  approver saw it. If the post changed, the decision is refused (422), never applied to
  newer text.
- **Never undone.** A terminal outcome for an account stays. Editing makes a new version,
  which supersedes a pending approval (``approvals.core.on_new_version``).
- **Fail closed at the gate.** Submission re-runs the deterministic guardrails and refuses
  while any duplicate check is "not checked" or the set is blocked (Sprint 6 carry-over).
  Approving re-runs the guardrails too, because a banned word can be added after drafting.
"""

from __future__ import annotations

import secrets
import string
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application import autopilot, second_factor
from mera.application.approvals import core as approvals
from mera.application.approvals.card import card_extras
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.drafting import core as drafting
from mera.application.errors import (
    ConflictError,
    Forbidden,
    Gone,
    InvalidInput,
    NotFoundError,
)
from mera.application.events import emit
from mera.application.hashtags import banned_tags_in, registry_tags
from mera.application.publishing.scheduler import schedule_approved
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import acting_user, load_user_principal
from mera.domain.errors import PermissionDenied
from mera.domain.policies import rbac, text_checks
from mera.domain.policies.content_hash import VariantHash, compute_composite_hash
from mera.domain.policies.rbac import Permission
from mera.domain.principal import Principal
from mera.domain.state_machines.approval import (
    NoCommonApprover,
    Quorum,
    RequestState,
    VariantApproval,
    is_open,
    strictest,
    vote,
)
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.platform import Platform
from mera.domain.value_objects.posts import (
    GenerationStatus,
    GuardrailOutcome,
    PostSetState,
)
from mera.infrastructure.db.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalToken,
    ApprovalVariantState,
    GuardrailResult,
    PostSet,
    PostVariant,
    PostVersion,
    SocialAccount,
    User,
    Workspace,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.platforms.capabilities import capabilities_for
from mera.infrastructure.queue.deferrer import Deferrer

MAX_LIST: Final = 100
DISPATCH_TASK: Final = "approvals:dispatch"
REVISE_TASK: Final = "drafting:draft"
log = structlog.get_logger(__name__)
TOKEN_TTL: Final = timedelta(hours=24)
_B62: Final = string.ascii_letters + string.digits


# ------------------------------------------------------------------------------ views


@dataclass(frozen=True, slots=True)
class ApprovalVariantView:
    post_variant_id: uuid.UUID
    social_account_id: uuid.UUID
    platform: str
    nickname: str
    handle: str
    state: str
    required: int
    quorum: str
    approve_count: int
    reject_count: int
    decided_at: datetime | None
    post_version_id: uuid.UUID
    version_no: int
    content_hash: str
    is_current: bool
    body: str
    thread_parts: list[str] | None
    hashtags: list[str]
    link_url: str | None
    media: list[dict[str, Any]]
    confidence_score: float | None
    compliance_flags: list[dict[str, Any]]
    my_vote: str | None
    can_decide: bool


@dataclass(frozen=True, slots=True)
class DecisionView:
    approver_label: str | None
    channel_type: str
    action: str
    variant_scope: list[uuid.UUID]
    reason: str | None
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalRequestView:
    id: uuid.UUID
    post_set_id: uuid.UUID
    title: str | None
    state: str
    content_hash: str
    created_at: datetime
    expires_at: datetime
    remind_at: datetime | None
    approved_at: datetime | None
    decided_at: datetime | None
    requested_by: str | None
    variants: list[ApprovalVariantView] = field(default_factory=list)
    decisions: list[DecisionView] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Problem:
    post_variant_id: uuid.UUID | None
    code: str
    message: str


class NotReady(ConflictError):
    """Submission refused; ``problems`` says which account and why."""

    def __init__(self, problems: list[Problem]) -> None:
        text = " ".join(p.message for p in problems[:5])
        if len(problems) > 5:
            text += f" (and {len(problems) - 5} more)"
        super().__init__(text, code="not_ready_for_approval")
        self.problems = problems


def _jti() -> str:
    return "".join(secrets.choice(_B62) for _ in range(22))  # ~131 bits


class ApprovalService(WorkspaceScopedService):
    def __init__(
        self, sm: Any, deferrer: Deferrer | None = None, *, mfa_enforced: bool = False
    ) -> None:
        super().__init__(sm)
        self._deferrer = deferrer
        self._mfa_enforced = mfa_enforced

    async def _dispatch(self, kind: str, workspace_id: uuid.UUID, subject_id: uuid.UUID) -> None:
        """Tell the approval channels (M7) after the transaction committed: send the cards
        for a new request, or edit them in place after a decision from any surface (web
        and bot stay in sync, docs/11 DoD 10). Best effort: a queue outage never undoes a
        decision, and the sweeper re-syncs open cards."""
        if self._deferrer is None:
            return
        try:
            await self._deferrer.defer(
                DISPATCH_TASK,
                queue="approvals",
                queueing_lock=f"dispatch:{kind}:{subject_id}",
                kind=kind,
                workspace_id=str(workspace_id),
                subject_id=str(subject_id),
            )
        except Exception as exc:  # pragma: no cover - logged, never raised to the approver
            log.warning("approvals.dispatch_failed", kind=kind, error_type=type(exc).__name__)

    # ------------------------------------------------------------------- submit

    async def submit(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        meta: RequestMeta,
        *,
        scheduled_for: datetime | None = None,
        variant_ids: list[uuid.UUID] | None = None,
    ) -> ApprovalRequestView:
        """Send accounts for approval: by default every enabled account still in drafting
        (so after a partial outcome or an edit, only the remaining accounts are asked),
        or exactly ``variant_ids``. Two open requests never share an account."""
        now = datetime.now(UTC)
        if scheduled_for is not None and (
            scheduled_for.tzinfo is None or scheduled_for <= now + timedelta(minutes=1)
        ):
            raise InvalidInput(
                "Pick a publish time in the future (with a time zone).",
                code="invalid_scheduled_for",
                field="scheduled_for",
            )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            await self._writable(s, workspace_id)
            ps = await s.get(PostSet, set_id, with_for_update=True)
            if ps is None:
                raise NotFoundError("Post set not found.")
            variants = await _enabled_variants(s, set_id)
            if variant_ids is not None:
                wanted = set(variant_ids)
                variants = [v for v in variants if v.id in wanted]
                if len(variants) != len(wanted):
                    raise InvalidInput(
                        "Unknown or disabled account.", code="unknown_variant", field="variant_ids"
                    )
            else:
                variants = [v for v in variants if v.state in drafting.DRAFT_STATES]
            for v in variants:
                await self._require(s, principal, Permission.EDIT_POSTS, workspace_id, v)
            if ps.state == PostSetState.ARCHIVED.value:
                raise ConflictError("This post set is archived.", code="post_set_archived")
            if ps.generation_status in (
                GenerationStatus.QUEUED.value,
                GenerationStatus.RUNNING.value,
            ):
                raise ConflictError(
                    "Drafting is still running; send it once the drafts are in.",
                    code="generation_in_progress",
                )
            open_requests = list(
                (
                    await s.execute(
                        select(ApprovalRequest)
                        .where(
                            ApprovalRequest.post_set_id == set_id,
                            ApprovalRequest.state.in_(
                                [RequestState.PENDING.value, RequestState.AWAITING_EDIT.value]
                            ),
                        )
                        .with_for_update()
                    )
                ).scalars()
            )
            still_open = []
            for existing in open_requests:
                if not await approvals.expire_if_due(s, existing, now):
                    still_open.append(existing)
            if len(still_open) != len(open_requests):
                # States moved with the expiry: pick the accounts again.
                ids = {v.id for v in variants}
                variants = [
                    v
                    for v in await _enabled_variants(s, set_id)
                    if (
                        (v.id in ids)
                        if variant_ids is not None
                        else v.state in drafting.DRAFT_STATES
                    )
                ]
            pending_elsewhere: set[uuid.UUID] = set()
            for existing in still_open:
                pending_elsewhere |= {
                    r.post_variant_id
                    for r in await approvals.variant_states(s, existing.id)
                    if r.state == VariantApproval.PENDING.value
                }
            if (still_open and not variants) or any(v.id in pending_elsewhere for v in variants):
                raise ConflictError(
                    "This post is already waiting for approval.", code="approval_pending"
                )
            problems = await _readiness(s, ps, variants)
            if problems:
                raise NotReady(problems)

            per_variant: dict[str, dict[str, Any]] = {}
            effective = {}
            for v in variants:
                try:
                    eff = strictest(await approvals.policy_rules(s, v))
                except NoCommonApprover as exc:
                    raise NotReady(
                        [Problem(v.id, "policy_unsatisfiable", f"{await _name(s, v)}: {exc}.")]
                    ) from exc
                effective[v.id] = eff
                per_variant[str(v.id)] = {
                    "required": eff.required,
                    "quorum": eff.quorum.value,
                    "approvers": sorted(eff.approvers) if eff.approvers is not None else None,
                    "sources": list(eff.sources),
                    # Groups covering the account now: their kill switches keep applying
                    # even if the account later leaves them (review S8-H1).
                    "groups": sorted(
                        {
                            str(g)
                            for g in [
                                *(await approvals.account_groups(s, v.social_account_id)),
                                *([v.via_account_group_id] if v.via_account_group_id else []),
                            ]
                        }
                    ),
                }
            # One clock per request: the strictest timers among its accounts.
            remind = min(e.remind_minutes for e in effective.values())
            expires = min(e.expires_minutes for e in effective.values())
            validity = min(e.validity_minutes for e in effective.values())
            versions = {v.id: await s.get(PostVersion, v.current_version_id) for v in variants}
            composite = compute_composite_hash(
                VariantHash(v.social_account_id, True, versions[v.id].content_hash)  # type: ignore[union-attr]
                for v in variants
            )
            req = ApprovalRequest(
                id=uuid7(),
                workspace_id=workspace_id,
                post_set_id=set_id,
                account_group_id=next(
                    (v.via_account_group_id for v in variants if v.via_account_group_id), None
                ),
                content_hash=composite,
                state=RequestState.PENDING.value,
                policy_snapshot={
                    "variants": per_variant,
                    # P2-M2.8: who hears about an escalation, named by any level.
                    "escalate_to": sorted(
                        set().union(*(e.escalate_to for e in effective.values()))
                    ),
                    "remind_minutes": remind,
                    "expires_minutes": expires,
                    "validity_minutes": validity,
                },
                remind_at=now + timedelta(minutes=remind),
                escalate_at=now
                + timedelta(minutes=min(e.escalate_minutes for e in effective.values())),
                expires_at=now + timedelta(minutes=expires),
                scheduled_for=scheduled_for,
                requested_by_user_id=user.user_id,
            )
            s.add(req)
            await s.flush()
            for v in variants:
                ver = versions[v.id]
                assert ver is not None
                eff = effective[v.id]
                s.add(
                    ApprovalVariantState(
                        id=uuid7(),
                        workspace_id=workspace_id,
                        approval_request_id=req.id,
                        post_variant_id=v.id,
                        post_version_id=ver.id,
                        social_account_id=v.social_account_id,
                        content_hash=ver.content_hash,
                        state=VariantApproval.PENDING.value,
                        required_count=eff.required,
                        quorum_mode=eff.quorum.value,
                    )
                )
            await s.flush()
            # Autopilot (P3-M3): accounts whose resolved mode lets this version go out
            # without a person get an authorization bound to its hash, and the approval it
            # stands for is a system decision. Everything else waits for a person.
            auto_rows: list[ApprovalVariantState] = []
            for row in await approvals.variant_states(s, req.id):
                v = next(x for x in variants if x.id == row.post_variant_id)
                ver = versions[v.id]
                assert ver is not None
                if await autopilot.authorize(s, req, row, v, ver, now) is not None:
                    approvals.store_tally(
                        row,
                        approvals.tally_of(row).__class__(
                            VariantApproval.APPROVED,
                            row.required_count,
                            Quorum(row.quorum_mode),
                            row.required_count,
                            0,
                        ),
                        now,
                    )
                    auto_rows.append(row)
            if auto_rows:
                s.add(
                    ApprovalDecision(
                        id=uuid7(),
                        workspace_id=workspace_id,
                        approval_request_id=req.id,
                        approver_user_id=None,
                        channel_type="system",
                        external_id="autopilot",
                        action="approve",
                        reason="Autopilot",
                        variant_scope=[r.post_variant_id for r in auto_rows],
                    )
                )
                await s.flush()
            await approvals.settle(s, req, await approvals.variant_states(s, req.id), now)
            if auto_rows:
                await schedule_approved(s, req, auto_rows, now=now, user_id=None)
                for r in auto_rows:
                    await emit(
                        s,
                        workspace_id,
                        "approval.granted",
                        subject_type="approval_request",
                        subject_id=req.id,
                        social_account_id=r.social_account_id,
                        payload={"post_variant_id": str(r.post_variant_id), "surface": "autopilot"},
                    )
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "approval_request.created",
                    workspace_id=workspace_id,
                    subject_type="approval_request",
                    subject_id=req.id,
                    subject_content_hash=composite,
                    after={"post_set_id": str(set_id), "variants": len(variants)},
                ),
            )
            await emit(
                s,
                workspace_id,
                "approval.requested",
                subject_type="approval_request",
                subject_id=req.id,
                account_group_id=req.account_group_id,
                payload={"post_set_id": str(set_id), "accounts": len(variants)},
            )
            view = await self._view(s, principal, req)
            any_pending = is_open(req.state)
        if any_pending:
            await self._dispatch("card", workspace_id, view.id)
        if auto_rows:
            await self._dispatch("autopilot", workspace_id, view.id)
        return view

    # ------------------------------------------------------------------- decide

    async def decide(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        request_id: uuid.UUID,
        meta: RequestMeta,
        *,
        action: str,
        variant_scope: list[uuid.UUID] | None,
        content_hashes: dict[str, str],
        reason: str | None,
    ) -> ApprovalRequestView:
        """A decision from the web or the API: bound to the hashes the approver was shown."""
        if action not in ("approve", "reject"):
            raise InvalidInput("Approve or reject.", code="invalid_action", field="action")
        if user.api_key is not None:
            # The product's guarantee is a person's approval of each account's text. A key
            # (or an agent holding one) can read the queue, never supply that approval
            # (review S8-M6; recorded in ADR-0014).
            raise Forbidden(
                "Approvals are given by a person, not an API key.", code="human_approval_required"
            )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            req = await approvals.lock_request(s, request_id)
            if req is None:
                raise NotFoundError("Approval request not found.")
            expired = await approvals.expire_if_due(s, req, datetime.now(UTC))
        if expired:
            raise Gone("This approval request has expired.", code="approval_expired")
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            req = await approvals.lock_request(s, request_id)
            if req is None:
                raise NotFoundError("Approval request not found.")
            rows = await self._apply(
                s,
                principal,
                req,
                approver_id=user.user_id,
                action=action,
                scope=variant_scope,
                shown=content_hashes,
                reason=reason,
                channel="web",
                jti=None,
                ip=meta.ip_address,
            )
            if action == "approve" and await _company_rows(s, rows):
                # Publishing as a company or organization (docs/09 §5.3); a refusal rolls
                # the whole decision back.
                await second_factor.require(s, user, workspace_id, "company_page_approve", meta)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    f"approval.{action}d" if action == "approve" else "approval.rejected",
                    workspace_id=workspace_id,
                    subject_type="approval_request",
                    subject_id=req.id,
                    social_account_id=rows[0].social_account_id if len(rows) == 1 else None,
                    subject_content_hash=rows[0].content_hash
                    if len(rows) == 1
                    else req.content_hash,
                    after={"variants": [str(r.post_variant_id) for r in rows], "state": req.state},
                ),
            )
            view = await self._view(s, principal, req)
        await self._dispatch("refresh", workspace_id, request_id)
        return view

    async def _apply(
        self,
        s: AsyncSession,
        principal: Principal,
        req: ApprovalRequest,
        *,
        approver_id: uuid.UUID,
        action: str,
        scope: list[uuid.UUID] | None,
        shown: dict[str, str] | None,
        reason: str | None,
        channel: str,
        jti: str | None,
        ip: str | None,
        external_id: str | None = None,
    ) -> list[ApprovalVariantState]:
        now = datetime.now(UTC)
        await self._writable(s, req.workspace_id)
        if not is_open(req.state):
            raise ConflictError(
                f"This post was already {req.state.replace('_', ' ')}.",
                code="approval_already_decided",
            )
        if req.state == RequestState.AWAITING_EDIT.value:
            raise ConflictError(
                "An edit was requested; wait for the new text.", code="awaiting_edit"
            )
        all_rows = await approvals.variant_states(s, req.id)
        by_variant = {r.post_variant_id: r for r in all_rows}
        if scope is None:
            chosen = [r for r in all_rows if r.state == VariantApproval.PENDING.value]
        else:
            if len(set(scope)) != len(scope) or any(v not in by_variant for v in scope):
                raise InvalidInput(
                    "The scope names an account that is not in this request.",
                    code="unknown_variant",
                    field="variant_scope",
                )
            chosen = [by_variant[v] for v in scope]
        if not chosen:
            raise ConflictError(
                "Every account here is already decided.", code="approval_already_decided"
            )
        # Authorised for every named account, or nothing happens (docs/04 §4.9).
        snapshot = req.policy_snapshot.get("variants", {})
        for r in chosen:
            v = await s.get(PostVariant, r.post_variant_id)
            assert v is not None
            try:
                await self._require(s, principal, Permission.APPROVE, req.workspace_id, v)
            except PermissionDenied as exc:
                raise Forbidden(
                    f"You can't approve {await _name(s, v)}.", code="approver_not_authorized"
                ) from exc
            named = (snapshot.get(str(r.post_variant_id)) or {}).get("approvers")
            if named is not None and str(approver_id) not in named:
                raise Forbidden(
                    f"You are not an approver for {await _name(s, v)}.",
                    code="approver_not_authorized",
                )
        if req.expires_at <= now:
            raise Gone("This approval request has expired.", code="approval_expired")
        for r in chosen:
            if r.state != VariantApproval.PENDING.value:
                raise ConflictError(
                    "One of these accounts was already decided.", code="approval_already_decided"
                )
            v = await s.get(PostVariant, r.post_variant_id)
            assert v is not None
            if v.current_version_id != r.post_version_id:
                raise InvalidInput(
                    f"{await _name(s, v)}'s post changed; review the new text.",
                    code="approval_content_mismatch",
                )
            if shown is not None and shown.get(str(r.post_variant_id)) != r.content_hash.hex():
                raise InvalidInput(
                    f"{await _name(s, v)}'s post is not the one you were shown; "
                    "reload and review it.",
                    code="approval_content_mismatch",
                )
        if action == "approve":
            accounts = [r.social_account_id for r in chosen]
            extra = [g for r in chosen for g in approvals.snapshot_groups(req, r.post_variant_id)]
            if await approvals.kill_switch_scope(s, accounts, extra):
                raise ConflictError(
                    "Automation is stopped for one of these accounts.", code="kill_switch_engaged"
                )
            # The deterministic guardrails again: a banned word may have been added since.
            for r in chosen:
                v = await s.get(PostVariant, r.post_variant_id)
                assert v is not None
                blocked = await _guardrail_block(s, v)
                if blocked:
                    raise ConflictError(f"{await _name(s, v)}: {blocked}", code="guardrail_blocked")
            voted = await _approved_by(s, req.id, approver_id)
            for r in chosen:
                if r.post_variant_id in voted:
                    raise ConflictError(
                        "You already approved this account; another approver is needed.",
                        code="already_voted",
                    )
        for r in chosen:
            approvals.store_tally(r, vote(approvals.tally_of(r), action), now)
        resolved = [r.post_variant_id for r in chosen if r.state != VariantApproval.PENDING.value]
        if resolved:
            await approvals.revoke_tokens(s, req.id, resolved, now)
        s.add(
            ApprovalDecision(
                id=uuid7(),
                workspace_id=req.workspace_id,
                approval_request_id=req.id,
                approver_user_id=approver_id,
                channel_type=channel,
                action=action,
                reason=(reason or "").strip()[:1000] or None,
                variant_scope=[r.post_variant_id for r in chosen],
                token_jti=jti,
                external_id=(external_id or None) and external_id[:100],
                ip_address=ip,
            )
        )
        await s.flush()
        await approvals.settle(s, req, all_rows, now)
        if action == "approve":
            # ADR-0014 §2: every account approved now is placed on the calendar now, in the
            # decision's transaction, so an approval and its slot can never disagree.
            await schedule_approved(s, req, chosen, now=now, user_id=approver_id)
        for r in chosen:
            await emit(
                s,
                req.workspace_id,
                "approval.granted"
                if r.state == VariantApproval.APPROVED.value
                else (
                    "approval.rejected"
                    if r.state == VariantApproval.REJECTED.value
                    else "approval.vote"
                ),
                subject_type="approval_request",
                subject_id=req.id,
                social_account_id=r.social_account_id,
                payload={"post_variant_id": str(r.post_variant_id), "surface": channel},
            )
        if not is_open(req.state):
            await emit(
                s,
                req.workspace_id,
                f"approval.{req.state}",
                subject_type="approval_request",
                subject_id=req.id,
                payload={"post_set_id": str(req.post_set_id)},
            )
        return chosen

    # ------------------------------------------------------------------- tokens

    async def mint_token(
        self,
        s: AsyncSession,
        req: ApprovalRequest,
        *,
        approver_id: uuid.UUID,
        action: str,
        scope: list[uuid.UUID] | None,
        channel: str,
        ttl: timedelta = TOKEN_TTL,
    ) -> str:
        """One button for one approver (docs/07 §5). A scoped token carries that variant's
        hash; an unscoped one the composite hash, so it dies when any account moves."""
        rows = {r.post_variant_id: r for r in await approvals.variant_states(s, req.id)}
        if scope is not None:
            if len(scope) != 1 or scope[0] not in rows:
                raise InvalidInput("A scoped token names exactly one account of this request.")
            content_hash = rows[scope[0]].content_hash
        else:
            content_hash = req.content_hash
        jti = _jti()
        now = datetime.now(UTC)
        s.add(
            ApprovalToken(
                jti=jti,
                workspace_id=req.workspace_id,
                approval_request_id=req.id,
                approver_user_id=approver_id,
                action=action,
                variant_scope=scope,
                content_hash=content_hash,
                channel=channel,
                issued_at=now,
                expires_at=min(now + ttl, req.expires_at),
            )
        )
        await s.flush()
        return jti

    async def decide_with_token(
        self,
        workspace_id: uuid.UUID,
        jti: str,
        *,
        channel: str,
        external_id: str | None,
        binding_user_id: uuid.UUID | None = None,
        pin_verified: bool = False,
    ) -> RequestState:
        """A bot button (docs/07 §5.2). The channel binding check (``identity_mismatch``)
        joins with the Telegram bot in M7; everything else is enforced here. Approving a
        company account needs the approval PIN (P2-M2.6): without ``pin_verified`` the
        decision is refused with ``second_factor_required`` and rolled back, token unused."""
        from mera.domain.context import WorkspaceContext
        from mera.infrastructure.db.session import workspace_transaction

        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            tok = await s.get(ApprovalToken, jti, with_for_update=True)
            now = datetime.now(UTC)
            if tok is None or tok.channel != channel:
                raise Gone("This button is not valid.", code="approval_token_used")
            if tok.revoked_at is not None or tok.used_at is not None:
                raise Gone("This button was already used or replaced.", code="approval_token_used")
            if tok.expires_at <= now:
                raise Gone("This button has expired.", code="approval_expired")
            # docs/07 §5.2: the tap must come from the chat bound to the token's approver; a
            # forwarded card carries working buttons, but not the approver's identity.
            if binding_user_id is not None and binding_user_id != tok.approver_user_id:
                raise Forbidden("This button belongs to someone else.", code="identity_mismatch")
            req = await approvals.lock_request(s, tok.approval_request_id)
            assert req is not None
            if await approvals.expire_if_due(s, req, now):
                return RequestState(req.state)  # committed; the bot renders "expired"
            principal = await _principal_for(s, tok.approver_user_id, workspace_id)
            # The token's hash binds it to what its button showed.
            if tok.variant_scope:
                current = {r.post_variant_id: r for r in await approvals.variant_states(s, req.id)}[
                    tok.variant_scope[0]
                ].content_hash
            else:
                current = req.content_hash
            if current != tok.content_hash:
                raise InvalidInput(
                    "The post changed after this button was sent.",
                    code="approval_content_mismatch",
                )
            tok.used_at = now  # burned in the same transaction as the decision
            rows = await self._apply(
                s,
                principal,
                req,
                approver_id=tok.approver_user_id,
                action=tok.action,
                scope=list(tok.variant_scope) if tok.variant_scope else None,
                shown=None,
                reason=None,
                channel=channel,
                jti=jti,
                ip=None,
                external_id=external_id,
            )
            if tok.action == "approve" and not pin_verified and await _company_rows(s, rows):
                ws_row = await s.get(Workspace, workspace_id)
                if second_factor.required(ws_row, "company_page_approve"):
                    raise second_factor.SecondFactorRequired("company_page_approve")
            # docs/07 §9: who approved *which text*, from which chat.
            approver = await s.get(User, tok.approver_user_id)
            await record(
                s,
                AuditEntry(
                    action="approval.approved" if tok.action == "approve" else "approval.rejected",
                    actor_type="user",
                    actor_id=tok.approver_user_id,
                    actor_label=approver.display_name if approver else None,
                    surface=channel if channel in ("telegram", "whatsapp") else "api",
                    workspace_id=workspace_id,
                    subject_type="approval_request",
                    subject_id=req.id,
                    social_account_id=rows[0].social_account_id if len(rows) == 1 else None,
                    subject_content_hash=tok.content_hash,
                    after={
                        "variants": [str(r.post_variant_id) for r in rows],
                        "state": req.state,
                        "external_id": (external_id or "")[:100] or None,
                        "token": jti[:6],
                    },
                ),
            )
            return RequestState(req.state)

    # --------------------------------------------------------------------- edit

    async def request_edit(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        request_id: uuid.UUID,
        meta: RequestMeta,
        *,
        instruction: str,
        variant_ids: list[uuid.UUID] | None,
        channel: str = "web",
        external_id: str | None = None,
    ) -> ApprovalRequestView:
        """Edit with an instruction (M6.10, docs/07 §3.3): the Writer revises **only the named
        accounts**, each in its own voice. When the new version lands it supersedes that
        account's pending approval (and only that one), and the account is asked again
        automatically. Siblings keep their decisions and stay decidable meanwhile.

        Requesting a revision is part of approving, so an Approver may do it; replacing the
        text outright is a person's edit and goes through the post endpoints instead."""
        text = (instruction or "").strip()
        if not text or len(text) > 1000:
            raise InvalidInput(
                "Say what to change (up to 1,000 characters).", code="invalid_instruction"
            )
        if user.api_key is not None:
            raise Forbidden(
                "Approvals are given by a person, not an API key.", code="human_approval_required"
            )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            req = await approvals.lock_request(s, request_id)
            if req is None:
                raise NotFoundError("Approval request not found.")
            await self._writable(s, workspace_id)
            if not is_open(req.state):
                raise ConflictError(
                    f"This post was already {req.state.replace('_', ' ')}.",
                    code="approval_already_decided",
                )
            rows = await approvals.variant_states(s, req.id)
            by_variant = {r.post_variant_id: r for r in rows}
            if variant_ids is None:
                chosen = [r for r in rows if r.state == VariantApproval.PENDING.value]
            else:
                if any(v not in by_variant for v in variant_ids):
                    raise InvalidInput(
                        "Unknown account.", code="unknown_variant", field="variant_ids"
                    )
                chosen = [by_variant[v] for v in variant_ids]
            if not chosen or any(r.state != VariantApproval.PENDING.value for r in chosen):
                raise ConflictError(
                    "Only accounts still waiting can be revised.", code="approval_already_decided"
                )
            for r in chosen:
                v = await s.get(PostVariant, r.post_variant_id)
                assert v is not None
                try:
                    await self._require(s, principal, Permission.APPROVE, workspace_id, v)
                except PermissionDenied as exc:
                    raise Forbidden(
                        f"You can't revise {await _name(s, v)}.", code="approver_not_authorized"
                    ) from exc
            ps = await s.get(PostSet, req.post_set_id, with_for_update=True)
            assert ps is not None
            if ps.generation_status in (
                GenerationStatus.QUEUED.value,
                GenerationStatus.RUNNING.value,
            ):
                raise ConflictError(
                    "A revision is already running for this post.", code="generation_in_progress"
                )
            ps.generation_status = GenerationStatus.QUEUED.value
            ps.generation_requested_at = datetime.now(UTC)
            ps.generation_error = None
            s.add(
                ApprovalDecision(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    approval_request_id=req.id,
                    approver_user_id=user.user_id,
                    channel_type=channel,
                    action="edit_requested",
                    instruction_text=text,
                    variant_scope=[r.post_variant_id for r in chosen],
                    external_id=(external_id or None) and external_id[:100],
                    ip_address=meta.ip_address,
                )
            )
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "approval.edit_requested",
                    workspace_id=workspace_id,
                    subject_type="approval_request",
                    subject_id=req.id,
                    after={
                        "variants": [str(r.post_variant_id) for r in chosen],
                        "instruction": text,
                    },
                ),
            )
            await emit(
                s,
                workspace_id,
                "approval.edit_requested",
                subject_type="approval_request",
                subject_id=req.id,
                payload={"variants": [str(r.post_variant_id) for r in chosen]},
            )
            view = await self._view(s, principal, req)
            set_id = req.post_set_id
        if self._deferrer is not None:
            await self._deferrer.defer(
                REVISE_TASK,
                queue="agents",
                lock=f"draft:{set_id}",
                queueing_lock=f"draft:{set_id}",
                workspace_id=str(workspace_id),
                post_set_id=str(set_id),
                mode="revise",
                variant_ids=[str(r.post_variant_id) for r in chosen],
                instruction=text,
                user_id=str(user.user_id),
                trigger="approval_edit",
            )
        await self._dispatch("refresh", workspace_id, request_id)
        return view

    async def resubmit_after_edit(
        self,
        workspace_id: uuid.UUID,
        set_id: uuid.UUID,
        variant_ids: list[uuid.UUID],
        user_id: uuid.UUID,
    ) -> ApprovalRequestView | None:
        """After an approval-triggered revision lands (worker), ask again for exactly the
        revised accounts. Refused quietly (logged) if they are not ready, e.g. a guardrail
        now blocks one: the Composer shows why."""
        from mera.domain.context import WorkspaceContext
        from mera.infrastructure.db.session import workspace_transaction

        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            u = await s.get(User, user_id)
            if u is None:
                return None
            wanted = set(variant_ids)
            ready = [
                v.id
                for v in await _enabled_variants(s, set_id)
                if v.id in wanted and v.state in drafting.DRAFT_STATES
            ]
        if not ready:
            return None
        who = acting_user(u, mfa_enforced=self._mfa_enforced)
        try:
            return await self.submit(
                who, workspace_id, set_id, RequestMeta(surface="worker"), variant_ids=ready
            )
        except (ConflictError, InvalidInput, Forbidden, PermissionDenied) as exc:
            log.info("approvals.resubmit_skipped", reason=getattr(exc, "code", type(exc).__name__))
            return None

    async def preview(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        request_id: uuid.UUID,
        meta: RequestMeta,
    ) -> dict[str, Any]:
        """Everything a card shows (docs/07 §3.2, §8.1): the request view plus the source
        block once, the set-level distinctness signal and the stagger span."""
        view = await self.get(user, workspace_id, request_id, meta)
        async with self._tx(user, workspace_id, meta) as s:
            return await card_extras(s, view)

    async def reschedule(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        request_id: uuid.UUID,
        meta: RequestMeta,
        *,
        scheduled_for: datetime,
        channel: str = "web",
        external_id: str | None = None,
    ) -> ApprovalRequestView:
        """Move the set's requested time (the card's Reschedule, docs/07 §3.1). Accounts not
        yet decided will be placed from the new time; accounts already approved and queued
        are moved now, each keeping its stagger offset."""
        now = datetime.now(UTC)
        if scheduled_for.tzinfo is None or scheduled_for <= now + timedelta(minutes=1):
            raise InvalidInput("Pick a future time.", code="invalid_scheduled_for")
        from mera.application.publishing.scheduler import place_variant
        from mera.infrastructure.db.models import ScheduledPost

        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            req = await approvals.lock_request(s, request_id)
            if req is None:
                raise NotFoundError("Approval request not found.")
            await self._writable(s, workspace_id)
            rows = await approvals.variant_states(s, req.id)
            for r in rows:
                v = await s.get(PostVariant, r.post_variant_id)
                assert v is not None
                try:
                    await self._require(s, principal, Permission.APPROVE, workspace_id, v)
                except PermissionDenied as exc:
                    raise Forbidden(
                        "You can't reschedule every account here.", code="approver_not_authorized"
                    ) from exc
            req.scheduled_for = scheduled_for
            moved = 0
            for sp in (
                await s.execute(
                    select(ScheduledPost)
                    .where(
                        ScheduledPost.approval_request_id == req.id,
                        ScheduledPost.state.in_(["queued", "failed"]),
                    )
                    .with_for_update()
                )
            ).scalars():
                v = await s.get(PostVariant, sp.post_variant_id)
                assert v is not None
                at, _ = await place_variant(s, v, base=scheduled_for, now=now, exclude=sp.id)
                sp.scheduled_for = sp.next_attempt_at = at
                v.scheduled_for = at
                moved += 1
            s.add(
                ApprovalDecision(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    approval_request_id=req.id,
                    approver_user_id=user.user_id,
                    channel_type=channel,
                    action="reschedule",
                    reason=f"to {scheduled_for.isoformat()}",
                    variant_scope=[r.post_variant_id for r in rows],
                    external_id=(external_id or None) and external_id[:100],
                    ip_address=meta.ip_address,
                )
            )
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "approval.rescheduled",
                    workspace_id=workspace_id,
                    subject_type="approval_request",
                    subject_id=req.id,
                    after={"scheduled_for": scheduled_for.isoformat(), "moved": moved},
                ),
            )
            view = await self._view(s, principal, req)
        await self._dispatch("refresh", workspace_id, request_id)
        return view

    async def cancel_for_accounts(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        account_ids: set[uuid.UUID],
        meta: RequestMeta,
    ) -> int:
        """A kill switch stops pending approvals for the accounts it covers (docs/07 §3.5):
        those accounts are cancelled; other accounts on the same requests stay pending."""
        from mera.domain.state_machines.approval import end

        now = datetime.now(UTC)
        cancelled = 0
        touched: list[uuid.UUID] = []
        async with self._tx(user, workspace_id, meta) as s:
            reqs = list(
                (
                    await s.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.state.in_(
                                [RequestState.PENDING.value, RequestState.AWAITING_EDIT.value]
                            )
                        )
                    )
                ).scalars()
            )
            for r0 in reqs:
                req = await approvals.lock_request(s, r0.id)
                assert req is not None
                rows = await approvals.variant_states(s, req.id)
                hit = [
                    r
                    for r in rows
                    if r.social_account_id in account_ids
                    and r.state == VariantApproval.PENDING.value
                ]
                if not hit:
                    continue
                for r in hit:
                    approvals.store_tally(
                        r, end(approvals.tally_of(r), VariantApproval.CANCELLED), now
                    )
                await approvals.revoke_tokens(s, req.id, [r.post_variant_id for r in hit], now)
                s.add(
                    ApprovalDecision(
                        id=uuid7(),
                        workspace_id=workspace_id,
                        approval_request_id=req.id,
                        approver_user_id=user.user_id,
                        channel_type=meta.surface
                        if meta.surface in ("telegram", "web")
                        else "system",
                        action="cancel",
                        reason="kill switch",
                        variant_scope=[r.post_variant_id for r in hit],
                    )
                )
                await s.flush()
                await approvals.settle(s, req, rows, now)
                cancelled += 1
                touched.append(req.id)
        for rid in touched:
            await self._dispatch("refresh", workspace_id, rid)
        return cancelled

    # ------------------------------------------------------------- cancel, read

    async def cancel(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        request_id: uuid.UUID,
        meta: RequestMeta,
        *,
        reason: str | None,
    ) -> ApprovalRequestView:
        """Withdraw: every still-pending account is cancelled; decided ones keep their outcome."""
        now = datetime.now(UTC)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            req = await approvals.lock_request(s, request_id)
            if req is None:
                raise NotFoundError("Approval request not found.")
            rows = await approvals.variant_states(s, req.id)
            for r in rows:
                v = await s.get(PostVariant, r.post_variant_id)
                assert v is not None
                await self._require(s, principal, Permission.EDIT_POSTS, workspace_id, v)
            await self._writable(s, workspace_id)
            if not is_open(req.state):
                raise ConflictError(
                    "This request is already decided.", code="approval_already_decided"
                )
            from mera.domain.state_machines.approval import end

            for r in rows:
                approvals.store_tally(r, end(approvals.tally_of(r), VariantApproval.CANCELLED), now)
            s.add(
                ApprovalDecision(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    approval_request_id=req.id,
                    approver_user_id=user.user_id,
                    channel_type="api" if user.api_key else "web",
                    action="cancel",
                    reason=(reason or "").strip()[:1000] or None,
                    variant_scope=[r.post_variant_id for r in rows],
                    ip_address=meta.ip_address,
                )
            )
            await s.flush()
            await approvals.settle(s, req, rows, now)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "approval_request.cancelled",
                    workspace_id=workspace_id,
                    subject_type="approval_request",
                    subject_id=req.id,
                    after={"state": req.state},
                ),
            )
            await emit(
                s,
                workspace_id,
                "approval.cancelled",
                subject_type="approval_request",
                subject_id=req.id,
            )
            view = await self._view(s, principal, req)
        await self._dispatch("refresh", workspace_id, request_id)
        return view

    async def list_requests(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        state: str | None,
        limit: int,
    ) -> list[ApprovalRequestView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())
            if state == "open":
                q = q.where(
                    ApprovalRequest.state.in_(
                        [RequestState.PENDING.value, RequestState.AWAITING_EDIT.value]
                    )
                )
            elif state:
                q = q.where(ApprovalRequest.state == state)
            out = []
            now = datetime.now(UTC)
            for req in (await s.execute(q.limit(min(limit, MAX_LIST)))).scalars():
                await approvals.expire_if_due(s, req, now)
                if state == "open" and not is_open(req.state):
                    continue
                view = await self._view(s, principal, req)
                if view.variants:  # nothing in this member's scope: not theirs to see
                    out.append(view)
            return out

    async def get(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        request_id: uuid.UUID,
        meta: RequestMeta,
    ) -> ApprovalRequestView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            req = await s.get(ApprovalRequest, request_id)
            if req is None:
                raise NotFoundError("Approval request not found.")
            await approvals.expire_if_due(s, req, datetime.now(UTC))
            view = await self._view(s, principal, req)
            if not view.variants:
                raise NotFoundError("Approval request not found.")
            return view

    # ------------------------------------------------------------------ helpers

    async def _require(
        self,
        s: AsyncSession,
        principal: Principal,
        permission: Permission,
        workspace_id: uuid.UUID,
        v: PostVariant,
    ) -> None:
        groups = await approvals.account_groups(s, v.social_account_id)
        self._require_for_account(principal, permission, workspace_id, v.social_account_id, groups)

    async def _writable(self, s: AsyncSession, workspace_id: uuid.UUID) -> None:
        ws = await s.get(Workspace, workspace_id)
        if ws is None:
            raise NotFoundError("Workspace not found.")
        self._ensure_writable(ws)

    async def _view(
        self, s: AsyncSession, principal: Principal, req: ApprovalRequest
    ) -> ApprovalRequestView:
        ps = await s.get(PostSet, req.post_set_id)
        rows = await approvals.variant_states(s, req.id)
        voted = await _votes_by(s, req.id, principal.user_id) if principal.user_id else {}
        snapshot = req.policy_snapshot.get("variants", {})
        variants = []
        for r in rows:
            v = await s.get(PostVariant, r.post_variant_id)
            acct = await s.get(SocialAccount, r.social_account_id)
            ver = await s.get(PostVersion, r.post_version_id)
            if v is None or acct is None or ver is None:
                continue
            try:
                await self._require(s, principal, Permission.READ, req.workspace_id, v)
            except PermissionDenied:
                continue  # scoped member: other groups' accounts are not shown
            can = False
            if r.state == VariantApproval.PENDING.value and is_open(req.state):
                try:
                    await self._require(s, principal, Permission.APPROVE, req.workspace_id, v)
                    named = (snapshot.get(str(v.id)) or {}).get("approvers")
                    can = named is None or str(principal.user_id) in named
                except PermissionDenied:
                    can = False
            variants.append(
                ApprovalVariantView(
                    post_variant_id=v.id,
                    social_account_id=acct.id,
                    platform=acct.platform,
                    nickname=acct.nickname,
                    handle=acct.handle,
                    state=r.state,
                    required=r.required_count,
                    quorum=r.quorum_mode,
                    approve_count=r.approve_count,
                    reject_count=r.reject_count,
                    decided_at=r.decided_at,
                    post_version_id=ver.id,
                    version_no=ver.version_no,
                    content_hash=r.content_hash.hex(),
                    is_current=v.current_version_id == ver.id,
                    body=ver.body,
                    thread_parts=list(ver.thread_parts) if ver.thread_parts else None,
                    hashtags=list(ver.hashtags),
                    link_url=ver.link_url,
                    media=list(ver.media),
                    confidence_score=ver.confidence_score,
                    compliance_flags=list(ver.compliance_flags),
                    my_vote=voted.get(v.id),
                    can_decide=can and voted.get(v.id) != "approve",
                )
            )
        visible = {x.post_variant_id for x in variants}
        decisions = []
        for d in (
            await s.execute(
                select(ApprovalDecision, User.display_name)
                .outerjoin(User, User.id == ApprovalDecision.approver_user_id)
                .where(ApprovalDecision.approval_request_id == req.id)
                .order_by(ApprovalDecision.decided_at)
            )
        ).all():
            dec, name = d
            # A scoped member sees decisions about their own accounts only (review S8-M4).
            mine = [vid for vid in dec.variant_scope if vid in visible]
            if not mine:
                continue
            decisions.append(
                DecisionView(
                    name,
                    dec.channel_type,
                    dec.action,
                    mine,
                    dec.reason if len(mine) == len(dec.variant_scope) else None,
                    dec.decided_at,
                )
            )
        requester = (
            await s.get(User, req.requested_by_user_id) if req.requested_by_user_id else None
        )
        return ApprovalRequestView(
            id=req.id,
            post_set_id=req.post_set_id,
            title=ps.title if ps else None,
            state=req.state,
            # The composite hash names every account; a scoped member sees only theirs.
            content_hash=req.content_hash.hex() if len(variants) == len(rows) else "",
            created_at=req.created_at,
            expires_at=req.expires_at,
            remind_at=req.remind_at,
            approved_at=req.approved_at,
            decided_at=req.decided_at,
            requested_by=requester.display_name if requester else None,
            variants=variants,
            decisions=decisions,
        )


# ------------------------------------------------------------------------- helpers


async def _enabled_variants(s: AsyncSession, set_id: uuid.UUID) -> list[PostVariant]:
    return list(
        (
            await s.execute(
                select(PostVariant)
                .where(PostVariant.post_set_id == set_id, PostVariant.enabled.is_(True))
                .order_by(PostVariant.position)
            )
        ).scalars()
    )


async def _guardrail_block(s: AsyncSession, v: PostVariant) -> str | None:
    """Re-run the deterministic checks on the current version with today's voice."""
    ver = await s.get(PostVersion, v.current_version_id) if v.current_version_id else None
    if ver is None:
        return "not drafted yet"
    voice = await drafting.voice_for_variant(s, v)
    text = drafting.published_text(ver)
    if text_checks.find_banned(text, voice.banned_words):
        return "contains a banned word or phrase"
    banned_registry, _preferred = await registry_tags(s)
    if found := banned_tags_in(text, list(ver.hashtags), banned_registry):
        return "uses a banned hashtag (" + ", ".join(f"#{t}" for t in found) + ")"
    if text_checks.find_pii(text):
        return "contains personal data (an email address, phone number or similar)"
    caps = capabilities_for(Platform(v.platform))
    issues = text_checks.validate(
        platform=v.platform,
        body=ver.body,
        thread_parts=ver.thread_parts,
        hashtags=ver.hashtags,
        max_chars=caps.max_chars,
        counts_graphemes=caps.counts_graphemes,
        supports_threads=caps.supports_threads,
        conventional_hashtags=caps.conventional_hashtags,
        max_hashtags=caps.max_hashtags,
        link_placement=caps.link_placement,
        media_count=len(ver.media),
        requires_media=caps.requires_media,
        max_media=caps.max_media,
    )
    blocks = [i for i in issues if i.severity == "block"]
    return blocks[0].message if blocks else None


async def _readiness(s: AsyncSession, ps: PostSet, variants: list[PostVariant]) -> list[Problem]:
    problems: list[Problem] = []
    if not variants:
        return [Problem(None, "no_accounts", "Turn on at least one account first.")]
    if ps.state == PostSetState.BLOCKED.value:
        problems.append(
            Problem(None, "set_blocked", "Two accounts read too much alike; rewrite one first.")
        )
    for v in variants:
        if v.current_version_id is None:
            problems.append(Problem(v.id, "not_drafted", f"{await _name(s, v)} has no draft yet."))
            continue
        if v.state not in drafting.DRAFT_STATES or v.state == "blocked":
            if v.state == "blocked":
                problems.append(
                    Problem(
                        v.id, "variant_blocked", f"{await _name(s, v)} is blocked by a guardrail."
                    )
                )
            else:
                problems.append(
                    Problem(
                        v.id,
                        "variant_locked",
                        f"{await _name(s, v)} is {v.state.replace('_', ' ')}.",
                    )
                )
            continue
        blocked = await _guardrail_block(s, v)
        if blocked:
            problems.append(Problem(v.id, "guardrail_blocked", f"{await _name(s, v)}: {blocked}."))
        checked = await s.scalar(
            select(GuardrailResult.id).where(
                GuardrailResult.post_version_id == v.current_version_id,
                GuardrailResult.guardrail_key == "duplicate_post",
                # A check that ran and found a similar post records a score, not a
                # "checked" flag: only an explicit "false" means it did not run.
                func.coalesce(GuardrailResult.details["checked"].astext, "true") != "false",
            )
        )
        if not checked:  # no row at all fails closed too (review S8-L13)
            problems.append(
                Problem(
                    v.id,
                    "duplicate_unchecked",
                    (
                        f"{await _name(s, v)}: the duplicate check could not run; "
                        "save the post again to retry."
                    ),
                )
            )
    # M8.7 stagger_required: two accounts on one platform must not post together (docs/08
    # §3.3). Offsets were fixed at expansion from the group's stagger mode.
    from mera.domain.policies.scheduling import StaggerItem, stagger_violations

    base = datetime(2000, 1, 1, tzinfo=UTC)
    items = [
        StaggerItem(
            v.social_account_id,
            v.platform,
            base + timedelta(seconds=v.stagger_offset_seconds),
            capabilities_for(Platform(v.platform)).requires_stagger_within_platform,
        )
        for v in variants
    ]
    for a, b in stagger_violations(items):
        va = next(v for v in variants if v.social_account_id == a)
        vb = next(v for v in variants if v.social_account_id == b)
        problems.append(
            Problem(
                vb.id,
                "stagger_required",
                f"{await _name(s, va)} and {await _name(s, vb)} are on the same platform and "
                "would post at the same moment: the group's stagger is set to 'none' (All at "
                "once). Change the group's stagger mode so they go out minutes apart.",
            )
        )
    set_unchecked = await s.scalar(
        select(GuardrailResult.id).where(
            GuardrailResult.post_set_id == ps.id,
            GuardrailResult.subject_type == "post_set",
            GuardrailResult.superseded_at.is_(None),
            GuardrailResult.outcome.in_(
                [GuardrailOutcome.BLOCK.value, GuardrailOutcome.WARN.value]
            ),
            GuardrailResult.details.has_key("unchecked_variant_ids"),
        )
    )
    if set_unchecked:
        problems.append(
            Problem(
                None,
                "cross_account_unchecked",
                "The check that accounts read differently could not run; save a post to retry.",
            )
        )
    return problems


async def _votes_by(
    s: AsyncSession, request_id: uuid.UUID, user_id: uuid.UUID
) -> dict[uuid.UUID, str]:
    out: dict[uuid.UUID, str] = {}
    rows = await s.execute(
        select(ApprovalDecision.action, ApprovalDecision.variant_scope).where(
            ApprovalDecision.approval_request_id == request_id,
            ApprovalDecision.approver_user_id == user_id,
            ApprovalDecision.action.in_(["approve", "reject"]),
        )
    )
    for action, scope in rows.all():
        for vid in scope:
            out[vid] = action
    return out


async def _approved_by(
    s: AsyncSession, request_id: uuid.UUID, user_id: uuid.UUID
) -> set[uuid.UUID]:
    return {v for v, a in (await _votes_by(s, request_id, user_id)).items() if a == "approve"}


async def _principal_for(s: AsyncSession, user_id: uuid.UUID, workspace_id: uuid.UUID) -> Principal:
    from mera.application.context import AuthenticatedUser as AU

    user = await s.get(User, user_id)
    if user is None:
        raise Gone("This button's approver no longer exists.", code="approval_token_used")
    return await load_user_principal(
        s, AU(user_id=user.id, email=user.email, display_name=user.display_name), workspace_id
    )


async def _name(s: AsyncSession, v: PostVariant) -> str:
    acct = await s.get(SocialAccount, v.social_account_id)
    return acct.nickname if acct else "This account"


async def _company_rows(s: AsyncSession, rows: list[ApprovalVariantState]) -> bool:
    """Whether any approved account speaks for a company or organization."""
    for r in rows:
        acct = await s.get(SocialAccount, r.social_account_id)
        if acct is not None and acct.account_type in second_factor.COMPANY_ACCOUNT_TYPES:
            return True
    return False
