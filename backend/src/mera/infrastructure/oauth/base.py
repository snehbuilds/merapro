"""OIDC login providers (M2.7, docs/04 §2.2). Google and Microsoft, Authorization Code + PKCE."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class OidcError(Exception):
    """The provider refused, or its response failed verification."""


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


class OidcProvider(Protocol):
    name: str
    # Whether the provider's email claim can be trusted to link to an existing account.
    trusts_email: bool

    def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str
    ) -> str: ...

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str
    ) -> OidcIdentity: ...
