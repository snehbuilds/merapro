"""Keeps messages in memory instead of sending them (sandbox and tests)."""

from __future__ import annotations

from dataclasses import dataclass, field

from mera.domain.ports.notifier import EmailMessage, NotifierError


@dataclass
class CapturingNotifier:
    sent: list[EmailMessage] = field(default_factory=list)
    fail: bool = False

    async def send(self, message: EmailMessage) -> None:
        if self.fail:
            raise NotifierError("capture configured to fail")
        self.sent.append(message)
