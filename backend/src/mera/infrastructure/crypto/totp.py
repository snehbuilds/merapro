"""TOTP (RFC 6238) with the standard library only: HMAC-SHA1, 30-second steps, 6 digits,
the authenticator apps' defaults (P2-M2.3).

``verify`` accepts the current step and one either side (clock skew) and returns the step
it matched, so the caller can store it and refuse the same code, or an older one, again.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP = 30
DIGITS = 6
WINDOW = 1


def new_secret() -> str:
    """160 random bits, base32 without padding (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret: str, counter: int) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def counter_at(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP)


def verify(
    secret: str, code: str, *, last_counter: int | None = None, now: float | None = None
) -> int | None:
    """The matching step, or None. A step at or before ``last_counter`` never matches."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = counter_at(now)
    for counter in range(current - WINDOW, current + WINDOW + 1):
        if last_counter is not None and counter <= last_counter:
            continue
        if hmac.compare_digest(code_at(secret, counter), code):
            return counter
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str = "Mera") -> str:
    label = quote(f"{issuer}:{account}", safe=":@")
    return f"otpauth://totp/{label}?" + urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS, "period": STEP}
    )
