"""Real OIDC providers: token exchange over HTTPS, ID token verified against the provider's
JWKS (signature, issuer, audience, expiry, nonce)."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlencode

import httpx
import jwt

from mera.infrastructure.oauth.base import OidcError, OidcIdentity

_MS_ISSUER = re.compile(r"^https://login\.microsoftonline\.com/[0-9a-f-]{36}/v2\.0$")


class OidcClient:
    def __init__(
        self,
        *,
        name: str,
        client_id: str,
        client_secret: str,
        authorize_endpoint: str,
        token_endpoint: str,
        jwks_uri: str,
        issuer: str | None,
        trusts_email: bool,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.trusts_email = trusts_email
        self._client_id = client_id
        self._client_secret = client_secret
        self._authorize = authorize_endpoint
        self._token = token_endpoint
        self._issuer = issuer
        self._jwks = jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)
        self._http = http or httpx.AsyncClient(timeout=10)

    def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str
    ) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return f"{self._authorize}?{query}"

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str
    ) -> OidcIdentity:
        try:
            r = await self._http.post(
                self._token,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OidcError("token endpoint unreachable") from exc
        try:
            payload = r.json()
        except ValueError as exc:
            raise OidcError("token endpoint returned non-JSON") from exc
        if r.status_code != 200 or not isinstance(payload, dict) or "id_token" not in payload:
            raise OidcError(f"token endpoint returned {r.status_code}")
        id_token = payload["id_token"]
        try:
            # PyJWKClient fetches over the network synchronously; keep it off the loop.
            key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, id_token)
            claims = jwt.decode(
                id_token,
                key.key,
                algorithms=["RS256"],
                audience=self._client_id,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise OidcError("ID token failed verification") from exc
        issuer = str(claims["iss"])
        if self._issuer is not None and issuer != self._issuer:
            raise OidcError("unexpected issuer")
        if self._issuer is None and not _MS_ISSUER.match(issuer):
            raise OidcError("unexpected issuer")
        if claims["nonce"] != nonce:
            raise OidcError("nonce mismatch")
        return OidcIdentity(
            subject=str(claims["sub"]),
            email=claims.get("email"),
            email_verified=claims.get("email_verified") is True,  # never a truthy string
            name=claims.get("name"),
        )


def google(client_id: str, client_secret: str) -> OidcClient:
    return OidcClient(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        authorize_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",  # noqa: S106 (a URL, not a secret)
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        issuer="https://accounts.google.com",
        trusts_email=True,  # Google asserts email_verified
    )


def microsoft(client_id: str, client_secret: str) -> OidcClient:
    # Multi-tenant endpoint: the issuer carries the tenant id, checked by pattern. Entra ID
    # does not assert email ownership, so its email is never used to link accounts.
    return OidcClient(
        name="microsoft",
        client_id=client_id,
        client_secret=client_secret,
        authorize_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",  # noqa: S106 (a URL, not a secret)
        jwks_uri="https://login.microsoftonline.com/common/discovery/v2.0/keys",
        issuer=None,
        trusts_email=False,
    )
