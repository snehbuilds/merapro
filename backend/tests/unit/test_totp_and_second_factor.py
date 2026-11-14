"""P2-M2: TOTP against RFC 6238 vectors, replay and skew; the second-factor policy."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

import pytest

from mera.application import second_factor
from mera.infrastructure.crypto import totp

RFC = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize(
    ("t", "code"),
    # RFC 6238 Appendix B (SHA-1), last six digits of the eight-digit values.
    [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")],
)
def test_rfc6238_vectors(t: int, code: str) -> None:
    assert totp.code_at(RFC, t // 30) == code
    assert totp.verify(RFC, code, now=t) == t // 30


def test_skew_window_and_replay() -> None:
    now = 1_700_000_000
    step = now // 30
    secret = totp.new_secret()
    assert totp.verify(secret, totp.code_at(secret, step - 1), now=now) == step - 1
    assert totp.verify(secret, totp.code_at(secret, step + 1), now=now) == step + 1
    assert totp.verify(secret, totp.code_at(secret, step + 2), now=now) is None
    # A code at or before the last one used never works again.
    assert totp.verify(secret, totp.code_at(secret, step), last_counter=step, now=now) is None
    assert totp.verify(secret, totp.code_at(secret, step - 1), last_counter=step, now=now) is None
    for junk in ("", "12345", "1234567", "abcdef"):
        assert totp.verify(secret, junk, now=now) is None
    assert (
        totp.verify(
            secret,
            " " + totp.code_at(secret, step)[:3] + " " + totp.code_at(secret, step)[3:],
            now=now,
        )
        == step
    )


def test_provisioning_uri() -> None:
    uri = totp.provisioning_uri("ABC", account="a@b.co")
    assert uri.startswith("otpauth://totp/Mera:a@b.co?")
    assert "secret=ABC" in uri
    assert "issuer=Mera" in uri


def _ws(settings: dict[str, Any] | None) -> Any:
    return SimpleNamespace(settings=settings)


def test_policy_defaults_on_and_can_be_switched_off() -> None:
    assert all(second_factor.required(_ws(None), a) for a in second_factor.ACTIONS)
    off = _ws({"second_factor": {"account_disconnect": False}})
    assert second_factor.required(off, "account_disconnect") is False
    assert second_factor.required(off, "kill_switch_resume") is True
    with pytest.raises(ValueError, match="unknown"):
        second_factor.required(off, "kill_switch_engage")
    # Engaging a kill switch is never on the list: stopping is always safe.
    assert "kill_switch_engage" not in second_factor.ACTIONS
