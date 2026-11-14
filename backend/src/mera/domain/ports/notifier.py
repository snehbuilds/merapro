"""Outbound email (docs/01 Notifier port).

Mera sends very little email: a link that proves control of an address, and an invitation
to join a workspace. Approval traffic goes to the bots, never to email. The port is kept
small on purpose so a provider (SMTP relay, SES, a transactional API) is one class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class NotifierError(Exception):
    """The message was not accepted for delivery. Safe to show as "try again later"."""


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str | None = None


class Notifier(Protocol):
    async def send(self, message: EmailMessage) -> None:
        """Hand the message to the provider, or raise ``NotifierError``."""
        ...
