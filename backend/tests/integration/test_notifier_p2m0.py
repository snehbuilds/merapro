"""P2-M0.8: outbound email through the Notifier, over a real SMTP socket.

Outside sandbox the verification link travels only by email; invitations are emailed and
the accept link is still returned to the inviter. A dead relay fails the verification
request honestly and leaves the invitation standing.
"""

from __future__ import annotations

import socket

import pytest

from tests.integration.conftest import make_api
from tests.integration.smtp_sink import smtp_sink
from tests.integration.test_identity_m2 import _create_ws, _h, _me, _register_login

pytestmark = pytest.mark.integration


def _smtp(port: int) -> dict[str, object]:
    return {
        "rate_limit_enabled": False,
        "sandbox_mode": False,
        "smtp_host": "127.0.0.1",
        "smtp_port": port,
        "smtp_security": "none",
        "smtp_from": "Mera <mera@example.com>",
    }


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_verification_link_is_emailed_not_returned(
    postgres_urls: tuple[str, str],
) -> None:
    async with smtp_sink() as sink, make_api(postgres_urls, **_smtp(sink.port)) as api:
        s = await _register_login(api, "mailver")
        r = await api.post("/api/v1/auth/email/verification", headers=_h(s))
        assert r.status_code == 200, r.text
        assert r.json() == {"sent": True, "verify_url": None}

        assert len(sink.messages) == 1
        msg = sink.messages[0]
        assert msg["To"] == s["email"]
        assert msg["Subject"] == "Confirm your email for Mera"
        assert msg["From"] == "Mera <mera@example.com>"
        body = sink.bodies()[0]
        link = next(w for w in body.split() if "/verify-email/" in w)
        assert "expires in 24 hours" in body
        # The HTML alternative carries the same link.
        html = msg.get_body(preferencelist=("html",))
        assert html is not None
        assert link in html.get_content()

        token = link.rsplit("/", 1)[1]
        r = await api.post("/api/v1/auth/email/verify", json={"token": token})
        assert r.status_code == 204
        assert (await _me(api, s))["email_verified"] is True


async def test_invitation_is_emailed(postgres_urls: tuple[str, str]) -> None:
    async with smtp_sink() as sink, make_api(postgres_urls, **_smtp(sink.port)) as api:
        owner = await _register_login(api, "mailinv")
        ws = await _create_ws(api, owner, "Mailed Team")
        url = f"/api/v1/workspaces/{ws}/members/invitations"
        r = await api.post(
            url, headers=_h(owner), json={"email": "new.person@example.com", "role": "approver"}
        )
        assert r.status_code == 201, r.text
        assert r.json()["email_sent"] is True
        accept_url = r.json()["accept_url"]

        msg = sink.messages[-1]
        assert msg["To"] == "new.person@example.com"
        assert msg["Subject"] == "You're invited to Mailed Team on Mera"
        body = sink.bodies()[-1]
        assert accept_url in body
        assert "as approver" in body
        assert "mailinv invited you" in body
        html = msg.get_body(preferencelist=("html",))
        assert html is not None
        assert "Accept invitation" in html.get_content()


async def test_dead_relay_fails_verification_but_not_invitation(
    postgres_urls: tuple[str, str],
) -> None:
    async with make_api(postgres_urls, **_smtp(_closed_port())) as api:
        s = await _register_login(api, "maildown")
        r = await api.post("/api/v1/auth/email/verification", headers=_h(s))
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "email_delivery_failed"

        ws = await _create_ws(api, s, "Down")
        r = await api.post(
            f"/api/v1/workspaces/{ws}/members/invitations",
            headers=_h(s),
            json={"email": "someone@example.com", "role": "viewer"},
        )
        assert r.status_code == 201
        assert r.json()["email_sent"] is False
        assert "/invite/" in r.json()["accept_url"]
