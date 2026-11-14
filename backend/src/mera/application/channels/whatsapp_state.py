"""The 24-hour window and the template budget for WhatsApp (P2-M4, docs/07 §4.2).

The window opens whenever the approver messages Mera (any text or tap), recorded on their
binding. The budget is deployment-wide: business-initiated templates are the billed
messages, so a monthly ceiling stops a runaway loop from becoming an invoice; once it is
spent, templates are refused and the web queue still works.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.infrastructure.db.models import ApproverBinding, ChannelSendLog
from mera.infrastructure.db.session import channel_transaction, global_transaction

CHANNEL = "whatsapp"
WINDOW = timedelta(hours=24)


class WhatsAppState:
    def __init__(self, sm: async_sessionmaker[AsyncSession], *, monthly_template_cap: int) -> None:
        self._sm = sm
        self._cap = monthly_template_cap
        # The chat's last inbound in this process: the first message (a /pair) arrives
        # before any binding exists to record it on, and its reply must not be a template.
        self._recent: dict[str, datetime] = {}

    async def window_open(self, chat_id: str) -> bool:
        seen = self._recent.get(chat_id)
        if seen is not None and seen > datetime.now(UTC) - WINDOW:
            return True
        async with channel_transaction(self._sm, CHANNEL, chat_id) as s:
            last = await s.scalar(
                select(func.max(ApproverBinding.last_inbound_at)).where(
                    ApproverBinding.channel_type == CHANNEL,
                    ApproverBinding.external_id == chat_id,
                )
            )
        return last is not None and last > datetime.now(UTC) - WINDOW

    async def note_inbound(self, chat_id: str) -> None:
        self._recent[chat_id] = datetime.now(UTC)
        if len(self._recent) > 10_000:
            self._recent.clear()
        async with channel_transaction(self._sm, CHANNEL, chat_id) as s:
            await s.execute(
                update(ApproverBinding)
                .where(
                    ApproverBinding.channel_type == CHANNEL,
                    ApproverBinding.external_id == chat_id,
                )
                .values(last_inbound_at=datetime.now(UTC))
            )

    async def record_send(self, chat_id: str, kind: str, template: str | None) -> None:
        if kind == "template":
            return  # recorded when it was reserved (reserve_template)
        async with global_transaction(self._sm) as s:
            s.add(
                ChannelSendLog(channel_type=CHANNEL, chat_id=chat_id, kind=kind, template=template)
            )

    @staticmethod
    async def _templates_this_month(s: AsyncSession) -> int:
        start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = await s.scalar(
            select(func.count())
            .select_from(ChannelSendLog)
            .where(
                ChannelSendLog.channel_type == CHANNEL,
                ChannelSendLog.kind == "template",
                ChannelSendLog.created_at >= start,
            )
        )
        return int(used or 0)

    async def can_send_template(self) -> bool:
        """Whether any budget is left (a reading; sends reserve with reserve_template)."""
        async with global_transaction(self._sm) as s:
            return await self._templates_this_month(s) < self._cap

    async def reserve_template(self, chat_id: str, template: str) -> bool:
        """Count one template against the month's budget *before* it is sent. A lock
        serialises reservations, so concurrent sends can never overshoot the ceiling
        (release review #14); a send that then fails still counts, so the budget fails
        closed rather than open."""
        async with global_transaction(self._sm) as s:
            await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('mera.wa_templates'))"))
            if await self._templates_this_month(s) >= self._cap:
                return False
            s.add(
                ChannelSendLog(
                    channel_type=CHANNEL, chat_id=chat_id, kind="template", template=template
                )
            )
        return True

    async def month_usage(self) -> dict[str, int]:
        start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        async with global_transaction(self._sm) as s:
            rows = (
                await s.execute(
                    select(ChannelSendLog.kind, func.count())
                    .where(
                        ChannelSendLog.channel_type == CHANNEL, ChannelSendLog.created_at >= start
                    )
                    .group_by(ChannelSendLog.kind)
                )
            ).all()
        out = {"template": 0, "session": 0, "cap": self._cap}
        out.update({k: int(n) for k, n in rows})
        return out
