"""The approval sweeper (M6.8; docs/07 §7.2): remind, escalate, expire, on a clock.

Per open request, at most one step per sweep, in order:

1. ``expires_at`` passed → the pending accounts expire (decided ones keep their outcome);
   the cards are edited to say so and lose their buttons.
2. ``remind_at`` passed and no reminder yet → a reminder, as a reply to the card.
3. ``escalate_at`` passed and not escalated → the next level is told; the original buttons
   stay valid.

**Quiet hours shift the timers rather than consuming them.** A reminder due at 02:00 with
quiet hours until 07:00 fires at 07:00, and ``escalate_at`` and ``expires_at`` move by the
same amount, so nobody is handed a request that died overnight.

The sweep is per workspace inside its own RLS context and uses the partial indexes on open
requests, so it stays cheap with many requests in flight (M11 load check: < 50 ms).
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.approvals import core as approvals
from mera.application.events import emit
from mera.application.settings_model import level_values
from mera.domain.context import WorkspaceContext
from mera.domain.policies.scheduling import quiet_end_after, zone
from mera.domain.policies.settings_resolution import Level, resolve
from mera.domain.state_machines.approval import RequestState
from mera.infrastructure.db.models import (
    ApprovalRequest,
    ApprovalToken,
    ChannelUpdate,
    Event,
    SandboxCapture,
    Workspace,
)
from mera.infrastructure.db.session import (
    global_transaction,
    system_scan_transaction,
    workspace_transaction,
)
from mera.infrastructure.queue.deferrer import Deferrer

log = structlog.get_logger(__name__)
DISPATCH_TASK = "approvals:dispatch"
TOKEN_RETENTION = timedelta(days=30)
EVENT_RETENTION = timedelta(days=30)
UPDATE_RETENTION = timedelta(days=2)
OPEN = [RequestState.PENDING.value, RequestState.AWAITING_EDIT.value]


@dataclass(frozen=True, slots=True)
class Step:
    kind: str  # expired | reminder | escalation
    workspace_id: uuid.UUID
    request_id: uuid.UUID


def quiet_for(ws: Workspace) -> tuple[dict[str, Any] | None, str]:
    levels: dict[Level, dict[str, Any] | None] = {Level.WORKSPACE: level_values(ws.settings)}
    return resolve("quiet_hours", levels).value, ws.timezone


def shift_for_quiet(
    req: ApprovalRequest, due: datetime, quiet: dict[str, Any] | None, tz: str
) -> bool:
    """If ``due`` falls in quiet hours, move every pending timer by the same delta."""
    end = quiet_end_after(due, quiet, zone(tz))
    if end is None:
        return False
    delta = end - due
    if req.remind_at is not None and req.reminded_at is None:
        req.remind_at = req.remind_at + delta
    if req.escalate_at is not None and req.escalated_at is None:
        req.escalate_at = req.escalate_at + delta
    req.expires_at = req.expires_at + delta
    return True


async def sweep_workspace(s: AsyncSession, ws: Workspace, now: datetime) -> list[Step]:
    quiet, tz = quiet_for(ws)
    steps: list[Step] = []
    rows = (
        await s.execute(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.state.in_(OPEN),
                or_(
                    ApprovalRequest.expires_at <= now,
                    (ApprovalRequest.remind_at <= now) & ApprovalRequest.reminded_at.is_(None),
                    (ApprovalRequest.escalate_at <= now) & ApprovalRequest.escalated_at.is_(None),
                ),
            )
            .order_by(ApprovalRequest.expires_at)
            .limit(500)
            .with_for_update(skip_locked=True)
        )
    ).scalars()
    for req in rows:
        if req.expires_at <= now:
            if await approvals.expire_if_due(s, req, now):
                await emit(
                    s,
                    ws.id,
                    "approval.expired",
                    subject_type="approval_request",
                    subject_id=req.id,
                    payload={"post_set_id": str(req.post_set_id), "state": req.state},
                )
                steps.append(Step("expired", ws.id, req.id))
            continue
        if req.remind_at is not None and req.remind_at <= now and req.reminded_at is None:
            if shift_for_quiet(req, now, quiet, tz):
                continue
            req.reminded_at = now
            await emit(
                s, ws.id, "approval.reminded", subject_type="approval_request", subject_id=req.id
            )
            steps.append(Step("reminder", ws.id, req.id))
            continue
        if req.escalate_at is not None and req.escalate_at <= now and req.escalated_at is None:
            if shift_for_quiet(req, now, quiet, tz):
                continue
            req.escalated_at = now
            req.escalation_level += 1
            await emit(
                s,
                ws.id,
                "approval.escalated",
                subject_type="approval_request",
                subject_id=req.id,
                payload={"level": req.escalation_level},
            )
            steps.append(Step("escalation", ws.id, req.id))
    # Ledger retention (docs/03 §7): spent and expired tokens are kept 30 days so a late
    # replay still produces an informative audit entry.
    await s.execute(delete(ApprovalToken).where(ApprovalToken.expires_at < now - TOKEN_RETENTION))
    await s.execute(delete(Event).where(Event.created_at < now - EVENT_RETENTION))
    await s.execute(delete(SandboxCapture).where(SandboxCapture.created_at < now - EVENT_RETENTION))
    return steps


async def sweep(
    sm: async_sessionmaker[AsyncSession], deferrer: Deferrer | None, now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    async with system_scan_transaction(sm) as s:
        ids = list(
            (
                await s.execute(
                    select(Workspace.id).where(
                        Workspace.deleted_at.is_(None), Workspace.archived_at.is_(None)
                    )
                )
            ).scalars()
        )
    steps: list[Step] = []
    for ws_id in ids:
        try:
            async with workspace_transaction(sm, WorkspaceContext(ws_id)) as s:
                ws = await s.get(Workspace, ws_id)
                if ws is not None:
                    steps.extend(await sweep_workspace(s, ws, now))
        except Exception as exc:  # one workspace must not stop the sweep for the rest
            log.exception(
                "approvals.sweep_failed",
                message=(
                    "Approval maintenance failed for a workspace; continuing the scheduled sweep."
                ),
                workspace_id=str(ws_id),
                error_type=type(exc).__name__,
            )
    async with global_transaction(sm) as s:
        await s.execute(
            delete(ChannelUpdate).where(ChannelUpdate.received_at < now - UPDATE_RETENTION)
        )
    counts: Counter[str] = Counter(st.kind for st in steps)
    if deferrer is not None:
        for st in steps:
            kind = "refresh" if st.kind == "expired" else st.kind
            try:
                await deferrer.defer(
                    DISPATCH_TASK,
                    queue="approvals",
                    queueing_lock=f"dispatch:{kind}:{st.request_id}",
                    kind=kind,
                    workspace_id=str(st.workspace_id),
                    subject_id=str(st.request_id),
                )
            except Exception as exc:  # pragma: no cover
                log.warning(
                    "approvals.sweep_dispatch_failed",
                    message="Approval follow-up could not be queued; the next sweep will retry.",
                    error_type=type(exc).__name__,
                    exc_info=True,
                )
    if counts:
        log.info("approvals.sweep", **counts)
    return dict(counts)
