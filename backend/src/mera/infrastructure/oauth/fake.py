"""Sandbox OIDC provider (docs/10 §5): the full Mera-side flow — state, nonce, both PKCE legs,
one-time code — runs for real; only the provider's network calls are replaced.

``authorization_url`` points straight back at Mera's callback with a code that encodes the
identity to sign in as. Tests (or a developer) choose the identity by passing their own
code to the callback: ``fake:<subject>:<email>:<verified 0|1>``. The fake still checks that
the PKCE verifier matches the challenge Mera sent, so the server leg is exercised.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import urlencode

from mera.infrastructure.oauth.base import OidcError, OidcIdentity


class FakeOidcProvider:
    def __init__(self, name: str, *, trusts_email: bool) -> None:
        self.name = name
        self.trusts_email = trusts_email
        self._challenges: dict[str, str] = {}

    def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str
    ) -> str:
        self._challenges[state] = code_challenge
        code = f"fake:sandbox-user:sandbox@{self.name}.example:1"
        return f"{redirect_uri}?{urlencode({'code': code, 'state': state})}"

    def challenge_for(self, state: str) -> str | None:
        return self._challenges.get(state)

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str
    ) -> OidcIdentity:
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = next((st for st, ch in self._challenges.items() if ch == challenge), None)
        if state is None:
            raise OidcError("PKCE verifier does not match")
        del self._challenges[state]  # single use
        try:
            prefix, subject, email, verified = code.split(":", 3)
        except ValueError as exc:
            raise OidcError("malformed fake code") from exc
        if prefix != "fake":
            raise OidcError("malformed fake code")
        return OidcIdentity(
            subject=subject, email=email or None, email_verified=verified == "1", name=subject
        )
