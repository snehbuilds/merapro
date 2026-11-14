"""infrastructure/crypto is a 100%-coverage area (docs/10 §6.2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from mera.infrastructure.crypto import passwords, tokens

SECRET = "s" * 40


@pytest.mark.parametrize(
    ("pw", "email", "code"),
    [
        ("short", None, "password_too_short"),
        ("x" * (passwords.MAX_LENGTH + 1), None, "password_too_long"),
        ("alex.mercer@acme.example", "alex.mercer@acme.example", "password_matches_email"),
        ("ALEX.MERCER.LONG", "alex.mercer.long@acme.example", "password_matches_email"),
        ("Password1234", None, "password_breached"),
    ],
)
def test_weak_passwords(pw: str, email: str | None, code: str) -> None:
    with pytest.raises(passwords.WeakPassword) as exc:
        passwords.validate_password(pw, email=email)
    assert exc.value.code == code


def test_good_password_passes_and_custom_breach_list_is_used() -> None:
    passwords.validate_password("a perfectly fine passphrase", email="a@b.example")
    custom = passwords.LocalBreachedList(frozenset({"a perfectly fine passphrase"}))
    with pytest.raises(passwords.WeakPassword):
        passwords.validate_password("a perfectly fine passphrase", breached=custom)


def test_hash_and_verify() -> None:
    h = passwords.hash_password("a perfectly fine passphrase")
    assert h.startswith("$argon2id$")
    assert passwords.verify_password(h, "a perfectly fine passphrase")
    assert not passwords.verify_password(h, "wrong passphrase here")
    assert not passwords.needs_rehash(h)


def test_verify_without_hash_or_with_garbage_is_false() -> None:
    assert not passwords.verify_password(None, "timing-equaliser-not-a-real-password")
    assert not passwords.verify_password("not-a-hash", "anything at all")
    assert not passwords.verify_password(
        passwords.hash_password("x" * 12), "x" * (passwords.MAX_LENGTH + 1)
    )


def test_access_token_roundtrip() -> None:
    uid = uuid.uuid4()
    tok, ttl = tokens.issue_access_token(
        secret=SECRET, user_id=uid, token_version=3, memberships=[]
    )
    assert ttl == 900
    claims = tokens.decode_access_token(tok, secret=SECRET)
    assert claims.user_id == uid
    assert claims.token_version == 3


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: {**p, "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp())},
        lambda p: {**p, "aud": "someone-else"},
        lambda p: {**p, "iss": "someone-else"},
        lambda p: {k: v for k, v in p.items() if k != "tv"},
        lambda p: {**p, "sub": "not-a-uuid"},
        lambda p: {**p, "typ": "device"},  # another purpose signed with the same key
        lambda p: {k: v for k, v in p.items() if k != "typ"},
    ],
)
def test_tampered_access_tokens_are_rejected(mutate) -> None:  # type: ignore[no-untyped-def]
    tok, _ = tokens.issue_access_token(
        secret=SECRET, user_id=uuid.uuid4(), token_version=0, memberships=[]
    )
    payload = jwt.decode(tok, SECRET, algorithms=["HS256"], audience=tokens.AUDIENCE)
    forged = jwt.encode(mutate(payload), SECRET, algorithm="HS256")
    with pytest.raises(tokens.InvalidToken):
        tokens.decode_access_token(forged, secret=SECRET)


def test_wrong_secret_and_alg_none_are_rejected() -> None:
    tok, _ = tokens.issue_access_token(
        secret=SECRET, user_id=uuid.uuid4(), token_version=0, memberships=[]
    )
    with pytest.raises(tokens.InvalidToken):
        tokens.decode_access_token(tok, secret="t" * 40)
    payload = jwt.decode(tok, SECRET, algorithms=["HS256"], audience=tokens.AUDIENCE)
    unsigned = jwt.encode(payload, None, algorithm="none")
    with pytest.raises(tokens.InvalidToken):
        tokens.decode_access_token(unsigned, secret=SECRET)


def test_refresh_tokens_are_prefixed_unique_and_hashed() -> None:
    a, b = tokens.new_refresh_token(), tokens.new_refresh_token()
    assert a.startswith("mera_rt_")
    assert a != b
    assert tokens.hash_refresh_token(a) == tokens.hash_refresh_token(a)
    assert a not in tokens.hash_refresh_token(a)
