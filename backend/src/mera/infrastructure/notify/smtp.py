"""SMTP delivery through any relay (corporate relay, SES SMTP, a transactional provider).

The standard library client runs in a worker thread with a hard timeout, so a slow relay
costs one request a few seconds, never the event loop. Sending is synchronous with the
request on purpose: the messages carry single-use links, and queueing them would store
those links in the job table.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage as MimeMessage
from email.utils import make_msgid
from typing import Literal

import structlog

from mera.domain.ports.notifier import EmailMessage, NotifierError

log = structlog.get_logger(__name__)

Security = Literal["starttls", "ssl", "none"]


class SmtpNotifier:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str | None = None,
        password: str | None = None,
        security: Security = "starttls",
        timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._security = security
        self._timeout = timeout

    def _mime(self, message: EmailMessage) -> MimeMessage:
        mime = MimeMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Message-ID"] = make_msgid(domain=self._sender.rpartition("@")[2] or None)
        mime.set_content(message.text)
        if message.html is not None:
            mime.add_alternative(message.html, subtype="html")
        return mime

    def _deliver(self, mime: MimeMessage) -> None:
        context = ssl.create_default_context()
        client: smtplib.SMTP
        if self._security == "ssl":
            client = smtplib.SMTP_SSL(
                self._host, self._port, timeout=self._timeout, context=context
            )
        else:
            client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        with client:
            if self._security == "starttls":
                client.starttls(context=context)
            if self._username:
                client.login(self._username, self._password or "")
            client.send_message(mime)

    async def send(self, message: EmailMessage) -> None:
        mime = self._mime(message)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._deliver, mime), timeout=self._timeout + 5
            )
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            # Never log the body: it carries a single-use link.
            log.warning(
                "email_delivery_failed",
                message="Email delivery failed; message content was not logged.",
                error_type=type(exc).__name__,
                exc_info=True,
            )
            raise NotifierError("email delivery failed") from exc
