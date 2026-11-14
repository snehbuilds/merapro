"""Notifier adapters: SMTP for real delivery, an in-memory capture for sandbox and tests."""

from __future__ import annotations

from mera.config import Settings
from mera.domain.ports.notifier import Notifier
from mera.infrastructure.notify.capture import CapturingNotifier
from mera.infrastructure.notify.smtp import SmtpNotifier

__all__ = ["CapturingNotifier", "SmtpNotifier", "build_notifier"]


def build_notifier(settings: Settings) -> Notifier | None:
    """SMTP when configured; the capture in sandbox; otherwise none, and the features that
    need email say so honestly (503 ``email_delivery_unavailable``)."""
    if settings.smtp_host:
        return SmtpNotifier(
            host=settings.smtp_host,
            port=settings.smtp_port,
            sender=settings.smtp_from or "",
            username=settings.smtp_username,
            password=(
                settings.smtp_password.get_secret_value() if settings.smtp_password else None
            ),
            security=settings.smtp_security,
        )
    if settings.sandbox_mode:
        return CapturingNotifier()
    return None
