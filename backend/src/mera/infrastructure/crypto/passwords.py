"""Password hashing and policy (M2.2, docs/09 §5.1).

Argon2id with argon2-cffi's defaults, which track the RFC 9106 / OWASP recommendation.
Minimum length 12, no composition rules, checked against a breached-password list.

The bundled list is a small local fallback so registration works offline and in sandbox
mode. A Have I Been Pwned k-anonymity checker can implement the same ``BreachedPasswords``
protocol later; it is an outbound call, so it belongs behind configuration.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from importlib import resources
from typing import Protocol

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 1024  # bound hashing cost on hostile input

_hasher = PasswordHasher()
# Verified against when the user does not exist, so login timing does not reveal it.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


class WeakPassword(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BreachedPasswords(Protocol):
    def is_breached(self, password: str) -> bool: ...


class LocalBreachedList:
    def __init__(self, words: frozenset[str]) -> None:
        self._words = words

    def is_breached(self, password: str) -> bool:
        return password.lower() in self._words


@lru_cache(maxsize=1)
def bundled_breached_list() -> LocalBreachedList:
    text = (resources.files("mera.infrastructure.crypto") / "breached_passwords.txt").read_text(
        encoding="utf-8"
    )
    words = frozenset(
        line.strip().lower()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    )
    return LocalBreachedList(words)


def validate_password(
    password: str, *, email: str | None = None, breached: BreachedPasswords | None = None
) -> None:
    if len(password) < MIN_LENGTH:
        raise WeakPassword("password_too_short", f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise WeakPassword("password_too_long", f"Use at most {MAX_LENGTH} characters.")
    if email and password.lower() in {email.lower(), email.split("@", 1)[0].lower()}:
        raise WeakPassword("password_matches_email", "Don't use your email address as a password.")
    if (breached or bundled_breached_list()).is_breached(password):
        raise WeakPassword(
            "password_breached", "This password appears in known breaches. Choose another."
        )


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-ish time: always runs one Argon2 verification."""
    if len(password) > MAX_LENGTH:
        return False
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# ---- async wrappers: Argon2 is deliberately slow, so never run it on the event loop ----


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password_hash: str | None, password: str) -> bool:
    return await asyncio.to_thread(verify_password, password_hash, password)
