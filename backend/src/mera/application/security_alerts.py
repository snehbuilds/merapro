"""Security alerts (P3-M7.1, docs/09 §13, docs/14 §1.11).

Every minute a worker reads the ``security_event`` rows written since its last run (the
only reader those rows have besides the person they belong to) and alerts on:

- ``mfa.locked``, ``pin.locked`` (five wrong codes or PINs): the person.
- ``refresh.reuse_detected`` (a spent session token came back): the person.
- three ``second_factor.rejected`` for one person in a workspace within ten minutes: the
  workspace's Owners and Admins.
- ``autopilot.changed`` (P3-M3): the workspace's Owners and Admins.

Each alert is recorded in ``security_alert`` whether or not email is configured, so the
workspace's alerts can be listed in the app; the same alert is not repeated for an hour.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.domain.context import WorkspaceContext
from mera.domain.ports.notifier import EmailMessage, Notifier
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    SecurityAlert,
    SecurityEvent,
    SecurityScanCursor,
    User,
    WorkspaceMember,
)
from mera.infrastructure.db.session import (
    global_transaction,
    security_scan_transaction,
    workspace_transaction,
)

log = structlog.get_logger(__name__)

PERSONAL: Final = {
    "mfa.locked": "Your authenticator codes were locked after five wrong attempts.",
    "pin.locked": "Your approval PIN was locked after five wrong attempts.",
    "refresh.reuse_detected": (
        "A signed-out session token was used again, so every session was ended."
    ),
}
WORKSPACE: Final = {
    "autopilot.changed": "Autopilot settings were changed.",
}
BURST_KIND: Final = "second_factor.rejected"
BURST: Final = 3
BURST_WINDOW: Final = timedelta(minutes=10)
QUIET: Final = timedelta(hours=1)
CURSOR: Final = "security_alerts"


class SecurityAlerts:
    def __init__(self, sm: async_sessionmaker[AsyncSession], notifier: Notifier | None) -> None:
        self._sm = sm
        self._notifier = notifier

    async def run(self, now: datetime | None = None, limit: int = 1000) -> dict[str, int]:
        now = now or datetime.now(UTC)
        counts: Counter[str] = Counter()
        async with security_scan_transaction(self._sm) as s:
            cur = await s.get(SecurityScanCursor, CURSOR, with_for_update=True)
            if cur is None:
                cur = SecurityScanCursor(name=CURSOR, last_id=0)
                s.add(cur)
                await s.flush()
            events = list(
                (
                    await s.execute(
                        select(SecurityEvent)
                        .where(SecurityEvent.id > cur.last_id)
                        .order_by(SecurityEvent.id)
                        .limit(limit)
                    )
                ).scalars()
            )
            plans: list[dict[str, Any]] = []
            for e in events:
                cur.last_id = max(cur.last_id, e.id)
                if e.kind in PERSONAL and e.user_id is not None:
                    if not await self._recent(s, e.kind, e.user_id, None, now):
                        plans.append(self._plan(s, e, e.kind, None, PERSONAL[e.kind]))
                elif e.kind in WORKSPACE and e.workspace_id is not None:
                    plans.append(self._plan(s, e, e.kind, e.workspace_id, WORKSPACE[e.kind]))
                elif e.kind == BURST_KIND and e.user_id and e.workspace_id:
                    n = await s.scalar(
                        select(func.count())
                        .select_from(SecurityEvent)
                        .where(
                            SecurityEvent.kind == BURST_KIND,
                            SecurityEvent.user_id == e.user_id,
                            SecurityEvent.workspace_id == e.workspace_id,
                            SecurityEvent.created_at > e.created_at - BURST_WINDOW,
                            SecurityEvent.created_at <= e.created_at,
                        )
                    )
                    kind = "second_factor.burst"
                    if int(n or 0) >= BURST and not await self._recent(
                        s, kind, e.user_id, e.workspace_id, now
                    ):
                        plans.append(
                            self._plan(
                                s,
                                e,
                                kind,
                                e.workspace_id,
                                f"{n} rejected second factors in ten minutes for one member.",
                            )
                        )
            counts["events"] = len(events)
        for plan in plans:
            counts["alerts"] += 1
            counts["emails"] += await self._deliver(plan)
        return dict(counts)

    @staticmethod
    async def _recent(
        s: AsyncSession,
        kind: str,
        user_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
        now: datetime,
    ) -> bool:
        q = (
            select(func.count())
            .select_from(SecurityAlert)
            .where(SecurityAlert.kind == kind, SecurityAlert.created_at > now - QUIET)
        )
        if user_id is not None:
            q = q.where(SecurityAlert.user_id == user_id)
        if workspace_id is not None:
            q = q.where(SecurityAlert.workspace_id == workspace_id)
        return int(await s.scalar(q) or 0) > 0

    @staticmethod
    def _plan(
        s: AsyncSession,
        e: SecurityEvent,
        kind: str,
        workspace_id: uuid.UUID | None,
        text: str,
    ) -> dict[str, Any]:
        alert = SecurityAlert(
            id=uuid7(),
            workspace_id=workspace_id,
            user_id=e.user_id,
            kind=kind,
            event_id=e.id,
            detail={"event": e.kind, "detail": e.detail, "ip": str(e.ip_address or "") or None},
        )
        s.add(alert)
        return {
            "id": alert.id,
            "kind": kind,
            "user_id": e.user_id,
            "workspace_id": workspace_id,
            "text": text,
            "at": e.created_at,
            "ip": str(e.ip_address) if e.ip_address else None,
        }

    async def _deliver(self, plan: dict[str, Any]) -> int:
        if self._notifier is None:
            log.warning("security_alert.no_notifier", kind=plan["kind"])
            return 0
        to: list[str] = []
        if plan["workspace_id"] is None:
            async with global_transaction(self._sm) as s:
                u = await s.get(User, plan["user_id"])
                if u is not None and u.deleted_at is None:
                    to.append(u.email)
        else:
            async with workspace_transaction(self._sm, WorkspaceContext(plan["workspace_id"])) as s:
                to = list(
                    (
                        await s.execute(
                            select(User.email)
                            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                            .where(WorkspaceMember.role.in_(("owner", "admin")))
                        )
                    ).scalars()
                )
        sent = 0
        for addr in to:
            try:
                await self._notifier.send(
                    EmailMessage(
                        to=addr,
                        subject="Mera security alert",
                        text=(
                            f"{plan['text']}\n\nWhen: {plan['at'].isoformat()}\n"
                            f"From: {plan['ip'] or 'unknown address'}\n\n"
                            "If this was not you, change your password and review your "
                            "sessions and second factors in Mera."
                        ),
                    )
                )
                sent += 1
            except Exception:  # an email failure must not stop the scan
                log.warning("security_alert.email_failed", kind=plan["kind"])
        if sent:
            async with security_scan_transaction(self._sm) as s:
                row = await s.get(SecurityAlert, plan["id"])
                if row is not None:
                    row.recipients = sent
        return sent
