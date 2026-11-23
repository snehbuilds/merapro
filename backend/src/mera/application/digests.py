"""Daily approval digests (P3-M4.1 and 4.2, docs/07 §3.4).

Every fifteen minutes: for each member on ``digest`` or ``both`` whose local time (the
workspace's time zone) has reached their ``digest_time`` and who has not had today's
digest, send it on every chat they paired (Telegram, WhatsApp; outside the 24-hour window
WhatsApp uses the ``mera_daily_digest`` template) and by email when a notifier is
configured. A day with nothing waiting sends nothing but still counts as done.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import UTC, datetime, time
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import InvalidInput
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.policies.scheduling import zone
from mera.domain.ports.notifier import EmailMessage, Notifier
from mera.infrastructure.db.models import User, Workspace, WorkspaceMember
from mera.infrastructure.db.session import system_scan_transaction, workspace_transaction

log = structlog.get_logger(__name__)
_TAG = re.compile(r"<[^>]+>")


def _at(value: str) -> time:
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except ValueError:
        return time(8, 30)


class Digests:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        bots: list[Any],
        notifier: Notifier | None = None,
    ) -> None:
        self._sm = sm
        self._bots = [b for b in bots if b is not None]
        self._notifier = notifier

    async def run(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        async with system_scan_transaction(self._sm) as s:
            spaces = list(
                (
                    await s.execute(
                        select(Workspace.id).where(
                            Workspace.deleted_at.is_(None), Workspace.archived_at.is_(None)
                        )
                    )
                ).scalars()
            )
        counts: Counter[str] = Counter()
        for ws in spaces:
            try:
                counts.update(await self.run_workspace(ws, now))
            except Exception as exc:
                log.exception(
                    "digests.workspace_failed",
                    message=(
                        "Digest generation failed for a workspace; continuing the scheduled run."
                    ),
                    workspace_id=str(ws),
                    error_type=type(exc).__name__,
                )
        return dict(counts)

    async def run_workspace(self, workspace_id: uuid.UUID, now: datetime) -> Counter[str]:
        counts: Counter[str] = Counter()
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            ws = await s.get(Workspace, workspace_id)
            if ws is None:
                return counts
            local = now.astimezone(zone(ws.timezone))
            due = [
                (m.id, m.user_id, email)
                for m, email in (
                    await s.execute(
                        select(WorkspaceMember, User.email)
                        .join(User, User.id == WorkspaceMember.user_id)
                        .where(WorkspaceMember.digest_mode.in_(("digest", "both")))
                    )
                ).all()
                if local.time() >= _at(m.digest_time) and m.digest_last_sent_on != local.date()
            ]
            name = ws.name
        for member_id, user_id, email in due:
            chats = 0
            text_version: str | None = None
            for bot in self._bots:
                chats += await bot.send_digest(workspace_id, user_id)
                if text_version is None:
                    built = await bot.digest(workspace_id, user_id)
                    if built is not None:
                        text_version = _TAG.sub("", built[0])
            if self._notifier is not None and text_version:
                try:
                    await self._notifier.send(
                        EmailMessage(
                            to=email,
                            subject=f"Mera: today's approvals in {name}",
                            text=text_version + "\n\nOpen Mera to review them.",
                        )
                    )
                    counts["emails"] += 1
                except Exception as exc:
                    log.warning(
                        "digests.email_failed",
                        message=(
                            "Digest email delivery failed; the next scheduled digest will retry."
                        ),
                        workspace_id=str(workspace_id),
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )
            async with workspace_transaction(self._sm, ctx) as s:
                m = await s.get(WorkspaceMember, member_id, with_for_update=True)
                if m is not None:
                    m.digest_last_sent_on = local.date()
            counts["members"] += 1
            counts["chats"] += chats
        return counts


class DigestPreferences(WorkspaceScopedService):
    """A member's own digest choice (P3-M4.1)."""

    async def set(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        mode: str,
        at: str,
    ) -> dict[str, str]:
        if mode not in ("immediate", "digest", "both"):
            raise InvalidInput(
                "Choose immediate, digest or both.", code="invalid_mode", field="mode"
            )
        if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", at):
            raise InvalidInput("Use HH:MM, 24-hour.", code="invalid_time", field="time")
        async with self._tx(user, workspace_id, meta) as s:
            await load_user_principal(s, user, workspace_id)
            m = (
                await s.execute(
                    select(WorkspaceMember)
                    .where(WorkspaceMember.user_id == user.user_id)
                    .with_for_update()
                )
            ).scalar_one()
            m.digest_mode, m.digest_time = mode, at
            return {"mode": mode, "time": at}
