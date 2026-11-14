"""Notifier unit tests: the SMTP client's protocol choices, the factory, the copy."""

from __future__ import annotations

import smtplib
from typing import Any

import pytest

from mera.application.emails import invitation_email, verification_email
from mera.config import Settings, get_settings
from mera.domain.ports.notifier import EmailMessage, NotifierError
from mera.infrastructure.notify import CapturingNotifier, SmtpNotifier, build_notifier

MSG = EmailMessage(to="a@example.com", subject="Hi", text="body", html="<p>body</p>")


class _FakeSMTP:
    calls: list[tuple[str, Any]]
    fail_on: str | None = None

    def __init__(self, host: str, port: int, timeout: float, **kw: Any) -> None:
        type(self).calls.append(("connect", (host, port, "context" in kw)))

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        type(self).calls.append(("quit", None))

    def _call(self, name: str, arg: Any = None) -> None:
        if type(self).fail_on == name:
            raise smtplib.SMTPAuthenticationError(535, b"no")
        type(self).calls.append((name, arg))

    def starttls(self, context: Any) -> None:
        self._call("starttls")

    def login(self, user: str, password: str) -> None:
        self._call("login", user)

    def send_message(self, mime: Any) -> None:
        self._call("send", mime)


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSMTP]:
    _FakeSMTP.calls = []
    _FakeSMTP.fail_on = None
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


async def test_starttls_login_and_send(fake_smtp: type[_FakeSMTP]) -> None:
    n = SmtpNotifier(
        host="relay", port=587, sender="Mera <m@example.com>", username="u", password="p"
    )
    await n.send(MSG)
    names = [c[0] for c in fake_smtp.calls]
    assert names == ["connect", "starttls", "login", "send", "quit"]
    mime = fake_smtp.calls[3][1]
    assert mime["From"] == "Mera <m@example.com>"
    assert mime["Message-ID"].endswith("@example.com>")
    assert mime.get_body(preferencelist=("html",)).get_content().strip() == "<p>body</p>"


async def test_implicit_tls_without_login(fake_smtp: type[_FakeSMTP]) -> None:
    await SmtpNotifier(host="relay", port=465, sender="m@example.com", security="ssl").send(
        EmailMessage(to="a@example.com", subject="Hi", text="body")
    )
    assert fake_smtp.calls[0] == ("connect", ("relay", 465, True))
    assert [c[0] for c in fake_smtp.calls] == ["connect", "send", "quit"]


async def test_smtp_errors_become_notifier_error(fake_smtp: type[_FakeSMTP]) -> None:
    fake_smtp.fail_on = "login"
    n = SmtpNotifier(host="relay", port=587, sender="m@example.com", username="u", password="x")
    with pytest.raises(NotifierError):
        await n.send(MSG)


async def test_capture_records_and_can_fail() -> None:
    c = CapturingNotifier()
    await c.send(MSG)
    assert c.sent == [MSG]
    c.fail = True
    with pytest.raises(NotifierError):
        await c.send(MSG)


def _settings(**over: object) -> Settings:
    return Settings(**{**get_settings().model_dump(), **over})


def test_factory_picks_smtp_capture_or_none() -> None:
    smtp = build_notifier(
        _settings(smtp_host="relay", smtp_from="m@example.com", smtp_password="pw")
    )
    assert isinstance(smtp, SmtpNotifier)
    assert isinstance(build_notifier(_settings(sandbox_mode=True)), CapturingNotifier)
    assert build_notifier(_settings(sandbox_mode=False)) is None


def test_smtp_host_needs_a_sender() -> None:
    with pytest.raises(ValueError, match="SMTP_FROM"):
        _settings(smtp_host="relay", smtp_from=None)


def test_copy_states_expiry_and_escapes_html() -> None:
    v = verification_email("a@example.com", "https://m/verify-email/t", hours=24)
    assert "https://m/verify-email/t" in v.text
    assert "24 hours" in v.text
    i = invitation_email(
        "b@example.com",
        "https://m/invite/x?a=1&b=2",
        workspace="<Acme>",
        inviter="Sam",
        role="editor",
        days=7,
    )
    assert i.subject == "You're invited to <Acme> on Mera"
    assert i.html is not None
    assert "&lt;Acme&gt;" in i.html
    assert "<Acme>" not in i.html
    assert 'href="https://m/invite/x?a=1&amp;b=2"' in i.html
    assert "7 days" in i.text
