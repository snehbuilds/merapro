"""A stable key for "who is calling", for rate limiting and idempotency.

Cheap and stateless: a JWT is verified (signature, expiry) but not looked up, an API key is
only hashed. The full authentication still happens in the route; this key only decides
whose bucket and whose cached responses a request uses. It is stable across access-token
refreshes, so a retried request after a refresh still finds its idempotent response.
"""

from __future__ import annotations

import hashlib
import ipaddress

from starlette.types import Scope

from mera.infrastructure.crypto import api_keys, tokens


def client_ip(scope: Scope) -> str:
    client = scope.get("client")
    host = client[0] if client else None
    try:
        return str(ipaddress.ip_address(host)) if host else "unknown"
    except ValueError:
        return "unknown"


def bearer(scope: Scope) -> str | None:
    raw = dict(scope.get("headers") or []).get(b"authorization")
    if not raw:
        return None
    value = raw.decode("latin-1")
    scheme, _, credential = value.partition(" ")
    return credential.strip() if scheme.lower() == "bearer" and credential.strip() else None


def principal_key(scope: Scope, *, secret: str) -> str:
    credential = bearer(scope)
    if credential:
        if credential.startswith(api_keys.KEY_PREFIX):
            return "key:" + hashlib.sha256(credential.encode()).hexdigest()[:32]
        try:
            return f"user:{tokens.decode_access_token(credential, secret=secret).user_id}"
        except tokens.InvalidToken:
            pass
    return f"ip:{client_ip(scope)}"
