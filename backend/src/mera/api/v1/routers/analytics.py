"""Analytics (docs/04 §4.12) and the workspace event stream (§5)."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from mera.api.deps import Analytics, CurrentUser, Meta
from mera.api.v1.routers.posts import SETS, WS
from mera.application.approvals import core as approvals
from mera.application.services.principals import load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.errors import PermissionDenied
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.infrastructure.db.models import Event
from mera.infrastructure.db.session import workspace_transaction

router = APIRouter(tags=["analytics"])
events_router = APIRouter(tags=["events"])

From = Annotated[datetime | None, Query(alias="from")]


@router.get(f"{WS}/analytics/insights")
async def list_insights(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Analytics,
    meta: Meta,
    kind: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """The weekly findings (P3-M1.4), newest first. Each cites the aggregate it came from
    in ``evidence``; the ``summary`` row is the Analytics agent's narrative, citing ids."""
    return {"data": await svc.insights(user, workspace_id, meta, limit=limit, kind=kind)}


@router.post(f"{WS}/analytics/insights/{{insight_id}}/acknowledge")
async def acknowledge_insight(
    workspace_id: uuid.UUID, insight_id: uuid.UUID, user: CurrentUser, svc: Analytics, meta: Meta
) -> dict[str, Any]:
    return await svc.acknowledge_insight(user, workspace_id, meta, insight_id)


@router.get(f"{WS}/analytics/weights")
async def weights(
    workspace_id: uuid.UUID, user: CurrentUser, svc: Analytics, meta: Meta
) -> dict[str, Any]:
    """Source and pillar weights now, and every move the feedback loop made (P3-M1.5)."""
    return await svc.weights(user, workspace_id, meta)


@router.post(f"{WS}/analytics/weight-changes/{{change_id}}/revert")
async def revert_weight(
    workspace_id: uuid.UUID, change_id: uuid.UUID, user: CurrentUser, svc: Analytics, meta: Meta
) -> dict[str, Any]:
    """Put the weight back exactly as it was before this change."""
    return await svc.revert_weight(user, workspace_id, meta, change_id)


class AutoTuneIn(BaseModel):
    auto_tune: bool


@router.put(f"{WS}/analytics/settings")
async def analytics_settings(
    workspace_id: uuid.UUID, body: AutoTuneIn, user: CurrentUser, svc: Analytics, meta: Meta
) -> dict[str, Any]:
    """Turn the feedback loop's weight moves on or off (on by default)."""
    return await svc.set_auto_tune(user, workspace_id, meta, body.auto_tune)


@router.get(f"{WS}/analytics/comparison")
async def comparison(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Analytics,
    meta: Meta,
    days: Annotated[int, Query(ge=1, le=365)] = 28,
) -> dict[str, Any]:
    """Group against group, account against account and platform against platform, each
    post measured against its own account's typical post (P3-M1.3)."""
    return await svc.comparison(user, workspace_id, meta, days=days)


@router.get(f"{WS}/analytics/confidence-calibration")
async def confidence_calibration(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Analytics,
    meta: Meta,
    account_id: uuid.UUID | None = None,
    days: Annotated[int, Query(ge=1, le=730)] = 180,
) -> dict[str, Any]:
    """Does the Editor's confidence predict approval? Agent-written versions a person
    decided on, approved as written or not, by confidence band (P3-M1.6). A band under
    ``min_per_band`` decisions has no rate; ``separates`` is null until both ends have one."""
    return await svc.confidence_calibration(
        user, workspace_id, meta, account_id=account_id, days=days
    )


@router.get(f"{WS}/analytics/overview")
async def overview(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Analytics,
    meta: Meta,
    frm: From = None,
    to: datetime | None = None,
    group_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Totals, by platform and by account. A metric no post reported is ``null``: show
    "Not reported", never zero."""
    return await svc.overview(
        user, workspace_id, meta, frm=frm, to=to, group_id=group_id, account_id=account_id
    )


@router.get(f"{WS}/analytics/timeseries")
async def timeseries(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Analytics,
    meta: Meta,
    metric: Annotated[str, Query(max_length=30)] = "engagement",
    frm: From = None,
    to: datetime | None = None,
    group_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return await svc.timeseries(
        user,
        workspace_id,
        meta,
        metric=metric,
        frm=frm,
        to=to,
        group_id=group_id,
        account_id=account_id,
    )


@router.get(f"{WS}/analytics/posts/{{post_id}}")
async def post_analytics(
    workspace_id: uuid.UUID, post_id: uuid.UUID, user: CurrentUser, svc: Analytics, meta: Meta
) -> dict[str, Any]:
    return await svc.post(user, workspace_id, post_id, meta)


@router.get(f"{WS}/analytics/post-sets/{{set_id}}")
async def post_set_analytics(
    workspace_id: uuid.UUID, set_id: uuid.UUID, user: CurrentUser, svc: Analytics, meta: Meta
) -> dict[str, Any]:
    """Combined across the set's accounts, with the per-account split beside it."""
    return await svc.post_set(user, workspace_id, set_id, meta)


@router.get(f"{SETS}/{{set_id}}/results")
async def post_set_results(
    workspace_id: uuid.UUID, set_id: uuid.UUID, user: CurrentUser, svc: Analytics, meta: Meta
) -> dict[str, Any]:
    return await svc.post_set(user, workspace_id, set_id, meta)


@router.get(f"{WS}/analytics/account-comparison")
async def account_comparison(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Analytics,
    meta: Meta,
    frm: From = None,
    to: datetime | None = None,
    group_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Each account against its own 90-day baseline, not raw totals."""
    return {
        "data": await svc.account_comparison(
            user, workspace_id, meta, frm=frm, to=to, group_id=group_id
        )
    }


@router.get(f"{WS}/analytics/group-comparison")
async def group_comparison(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Analytics,
    meta: Meta,
    frm: From = None,
    to: datetime | None = None,
) -> dict[str, Any]:
    return {"data": await svc.group_comparison(user, workspace_id, meta, frm=frm, to=to)}


@router.get(f"{WS}/analytics/top-posts")
async def top_posts(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Analytics,
    meta: Meta,
    frm: From = None,
    to: datetime | None = None,
    group_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    return {
        "data": await svc.top_posts(
            user,
            workspace_id,
            meta,
            frm=frm,
            to=to,
            group_id=group_id,
            account_id=account_id,
            limit=limit,
        )
    }


@router.get(f"{WS}/analytics/approval-turnaround")
async def approval_turnaround(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Analytics,
    meta: Meta,
    frm: From = None,
    to: datetime | None = None,
    group_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Minutes from request to first decision: Mera's own data, every platform."""
    return await svc.approval_turnaround(
        user, workspace_id, meta, frm=frm, to=to, group_id=group_id
    )


@router.get(f"{WS}/analytics/export.csv", response_class=PlainTextResponse)
async def export_csv(
    workspace_id: uuid.UUID,
    user: CurrentUser,
    svc: Analytics,
    meta: Meta,
    frm: From = None,
    to: datetime | None = None,
    group_id: uuid.UUID | None = None,
    account_id: uuid.UUID | None = None,
) -> PlainTextResponse:
    body = await svc.export_csv(
        user, workspace_id, meta, frm=frm, to=to, group_id=group_id, account_id=account_id
    )
    return PlainTextResponse(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mera-analytics.csv"'},
    )


# --------------------------------------------------------------------- events

HEARTBEAT_SECONDS = 20
POLL_SECONDS = 2.0
MAX_STREAM_SECONDS = 300  # the browser's EventSource reconnects with Last-Event-ID


@events_router.get(f"{WS}/events")
async def events(
    workspace_id: uuid.UUID,
    request: Request,
    user: CurrentUser,
    meta: Meta,
    types: Annotated[
        str | None,
        Query(max_length=300, description="Comma-separated globs, e.g. approval.*,post.*"),
    ] = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    once: bool = False,
) -> StreamingResponse:
    """Server-Sent Events with replay from ``Last-Event-ID``. Authorization is evaluated
    per event, so a member whose scope or role changes mid-stream stops receiving what they
    should no longer see. A heartbeat comment every 20 s keeps proxies from timing out."""
    sm = request.app.state.sessionmaker
    globs = [g.strip() for g in (types or "*").split(",") if g.strip()][:20]
    try:
        cursor = int(last_event_id) if last_event_id else None
    except ValueError:
        cursor = None
    async with workspace_transaction(sm, WorkspaceContext(workspace_id)) as s:
        principal = await load_user_principal(s, user, workspace_id)
        rbac.require(principal, Permission.READ, workspace_id)
        if cursor is None:
            cursor = int(await s.scalar(select(Event.id).order_by(Event.id.desc()).limit(1)) or 0)

    async def stream() -> AsyncIterator[bytes]:
        nonlocal cursor
        loop = asyncio.get_running_loop()
        started = last_beat = loop.time()
        yield b"retry: 3000\n\n"
        while True:
            async with workspace_transaction(sm, WorkspaceContext(workspace_id)) as s:
                try:
                    p = await load_user_principal(s, user, workspace_id)
                    rbac.require(p, Permission.READ, workspace_id)
                except Exception:
                    return  # membership gone: the stream ends
                rows = list(
                    (
                        await s.execute(
                            select(Event).where(Event.id > cursor).order_by(Event.id).limit(200)
                        )
                    ).scalars()
                )
                out = []
                for ev in rows:
                    cursor = ev.id
                    if not any(fnmatch.fnmatchcase(ev.event_type, g) for g in globs):
                        continue
                    if ev.social_account_id is not None:
                        groups = await approvals.account_groups(s, ev.social_account_id)
                        allowed = False
                        for gid in [None, *groups]:
                            try:
                                rbac.require(
                                    p,
                                    Permission.READ,
                                    workspace_id,
                                    social_account_id=ev.social_account_id,
                                    account_group_id=gid,
                                )
                                allowed = True
                                break
                            except PermissionDenied:
                                continue
                        if not allowed:
                            continue
                    payload = {
                        "type": ev.event_type,
                        "subject_type": ev.subject_type,
                        "subject_id": str(ev.subject_id) if ev.subject_id else None,
                        "created_at": ev.created_at.isoformat(),
                        **ev.payload,
                    }
                    out.append(
                        (
                            f"id: {ev.id}\nevent: {ev.event_type}\ndata: {json.dumps(payload)}\n\n"
                        ).encode()
                    )
            for chunk in out:
                yield chunk
            now = loop.time()
            if once or now - started > MAX_STREAM_SECONDS or await request.is_disconnected():
                return
            if now - last_beat > HEARTBEAT_SECONDS:
                last_beat = now
                yield b": heartbeat\n\n"
            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
