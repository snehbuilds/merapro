"""The two emails Mera sends. Plain text first; the HTML part is the same words.

Copy rules (DESIGN.md voice): say what happened, what the link does, how long it lasts,
and what to do if you did not expect it. No marketing, no tracking pixels.
"""

from __future__ import annotations

from html import escape

from mera.domain.ports.notifier import EmailMessage


def _html(paragraphs: list[str], url: str, label: str) -> str:
    body = "".join(f"<p>{escape(p)}</p>" for p in paragraphs)
    link = f'<p><a href="{escape(url, quote=True)}">{escape(label)}</a></p>'
    return f'<!doctype html><html><body style="font-family:sans-serif">{body}{link}</body></html>'


def verification_email(to: str, url: str, *, hours: int) -> EmailMessage:
    lines = [
        "Confirm this is your email address for Mera.",
        f"The link works once and expires in {hours} hours.",
        "If you didn't ask for this, you can ignore this message.",
    ]
    return EmailMessage(
        to=to,
        subject="Confirm your email for Mera",
        text="\n\n".join([*lines[:1], url, *lines[1:]]) + "\n",
        html=_html(lines, url, "Confirm email address"),
    )


def invitation_email(
    to: str, url: str, *, workspace: str, inviter: str, role: str, days: int
) -> EmailMessage:
    lines = [
        f"{inviter} invited you to the {workspace} workspace on Mera as {role}.",
        f"The invitation works once and expires in {days} days. Sign in or create an "
        f"account with this email address ({to}) to accept it.",
        "If you weren't expecting this, you can ignore this message.",
    ]
    return EmailMessage(
        to=to,
        subject=f"You're invited to {workspace} on Mera",
        text="\n\n".join([*lines[:1], url, *lines[1:]]) + "\n",
        html=_html(lines, url, "Accept invitation"),
    )
