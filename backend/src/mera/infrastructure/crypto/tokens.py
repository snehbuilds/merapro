"""Access tokens (JWT) and refresh tokens (opaque) — M2.3, docs/04 §2.1.

Access: HS256, 15 minutes, carrying ``sub``, ``tv`` (the user's ``token_version``) and the
workspace memberships *as a hint for clients*. Authorization never trusts the membership
claim: the role is re-read from the database on every workspace-scoped request, so a role
change or removal takes effect immediately rather than after expiry.

Refresh: 32 random bytes, ``mera_rt_`` prefixed, stored only as SHA-256. Rotated on every
use; presenting a rotated token revokes its whole family.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

ISSUER = "mera"
AUDIENCE = "mera-api"
ALGORITHM = "HS256"
ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=30)
REFRESH_TTL_ABSOLUTE = timedelta(days=90)  # a session ends 90 days after login, however used
REFRESH_PREFIX = "mera_rt_"
# Every JWT Mera signs carries a purpose; an access-token check accepts only this one, so a
# future device, approval or MCP token signed with the same key cannot be replayed here.
ACCESS_TYP = "access"


class InvalidToken(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: uuid.UUID
    token_version: int
    jti: str
    expires_at: datetime


def issue_access_token(
    *,
    secret: str,
    user_id: uuid.UUID,
    token_version: int,
    memberships: list[dict[str, str]],
    now: datetime | None = None,
) -> tuple[str, int]:
    now = now or datetime.now(UTC)
    exp = now + ACCESS_TTL
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": secrets.token_urlsafe(12),
        "tv": token_version,
        "typ": ACCESS_TYP,
        "workspace_memberships": memberships,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM), int(ACCESS_TTL.total_seconds())


def decode_access_token(token: str, *, secret: str) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],  # pinned: never accept "none" or an asymmetric swap
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "jti", "tv", "typ"]},
            leeway=10,
        )
        if payload["typ"] != ACCESS_TYP:
            raise InvalidToken("not an access token")
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            token_version=int(payload["tv"]),
            jti=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
        raise InvalidToken(str(exc)) from exc


def new_refresh_token() -> str:
    return REFRESH_PREFIX + secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
