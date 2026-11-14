"""API key and invitation secrets (M2.6, M2.8; docs/04 §2.4).

``mera_sk_live_<random>`` in normal operation, ``mera_sk_test_`` in sandbox mode, so a key
is greppable by secret scanners and a sandbox key is recognisable at a glance. Only the
SHA-256 is stored: 256-bit random secrets need no slow hash.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

KEY_PREFIX = "mera_sk_"
INVITE_PREFIX = "mera_inv_"
DISPLAY_PREFIX_LEN = 16
LAST_USED_RESOLUTION = timedelta(minutes=1)


def new_key(*, sandbox: bool) -> str:
    return f"{KEY_PREFIX}{'test' if sandbox else 'live'}_{secrets.token_urlsafe(32)}"


def new_invitation_token() -> str:
    return INVITE_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def display_prefix(raw: str) -> str:
    return raw[:DISPLAY_PREFIX_LEN]
