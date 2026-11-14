"""The real OIDC client's verification logic, against a local RSA key and a mocked token
endpoint (no network). This is the security-critical half of OAuth: what makes a returned
ID token trustworthy."""

from __future__ import annotations

import base64
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from mera.config import Settings
from mera.infrastructure.oauth import oidc
from mera.infrastructure.oauth.base import OidcError
from mera.infrastructure.oauth.fake import FakeOidcProvider
from mera.infrastructure.oauth.registry import build_providers

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CLIENT_ID = "client-123"
GOOGLE_ISS = "https://accounts.google.com"
MS_ISS = "https://login.microsoftonline.com/0f0f0f0f-1111-2222-3333-444444444444/v2.0"


def _id_token(key: Any = KEY, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": GOOGLE_ISS,
        "sub": "user-1",
        "aud": CLIENT_ID,
        "exp": now + 300,
        "iat": now,
        "nonce": "n-1",
        "email": "a@example.com",
        "email_verified": True,
        "name": "A",
        **overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


def _client(build: Any, response: httpx.Response) -> oidc.OidcClient:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["form"] = parse_qs(request.content.decode())
        return response

    c: oidc.OidcClient = build(CLIENT_ID, "secret")
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c._jwks = SimpleNamespace(  # type: ignore[assignment]
        get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=KEY.public_key())
    )
    c._seen = seen  # type: ignore[attr-defined]
    return c


async def _exchange(c: oidc.OidcClient) -> Any:
    return await c.exchange(
        code="c", code_verifier="v" * 43, redirect_uri="https://x/cb", nonce="n-1"
    )


async def test_valid_google_token() -> None:
    c = _client(oidc.google, httpx.Response(200, json={"id_token": _id_token()}))
    ident = await _exchange(c)
    assert (ident.subject, ident.email, ident.email_verified) == ("user-1", "a@example.com", True)
    form = c._seen["form"]  # type: ignore[attr-defined]
    assert form["code_verifier"] == ["v" * 43]
    assert form["grant_type"] == ["authorization_code"]


async def test_valid_microsoft_token_with_tenant_issuer() -> None:
    token = _id_token(iss=MS_ISS, email_verified=None)
    c = _client(oidc.microsoft, httpx.Response(200, json={"id_token": token}))
    ident = await _exchange(c)
    assert ident.email_verified is False
    assert c.trusts_email is False


@pytest.mark.parametrize(
    ("build", "token", "why"),
    [
        (oidc.google, _id_token(nonce="other"), "nonce"),
        (oidc.google, _id_token(iss="https://evil.example"), "issuer"),
        (oidc.google, _id_token(aud="someone-else"), "audience"),
        (oidc.google, _id_token(exp=int(time.time()) - 3600), "expired"),
        (oidc.google, _id_token(nonce=None), "missing nonce"),
        (oidc.google, _id_token(key=OTHER_KEY), "signature"),
        (
            oidc.microsoft,
            _id_token(iss="https://login.microsoftonline.com/not-a-tenant/v2.0"),
            "ms issuer",
        ),
    ],
)
async def test_untrustworthy_tokens_are_rejected(build: Any, token: str, why: str) -> None:
    c = _client(build, httpx.Response(200, json={"id_token": token}))
    with pytest.raises(OidcError):
        await _exchange(c)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"error": "invalid_grant"}),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"access_token": "x"}),
    ],
)
async def test_token_endpoint_failures(response: httpx.Response) -> None:
    with pytest.raises(OidcError):
        await _exchange(_client(oidc.google, response))


async def test_unreachable_token_endpoint() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    c: oidc.OidcClient = oidc.google(CLIENT_ID, "secret")
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    with pytest.raises(OidcError, match="unreachable"):
        await _exchange(c)


def test_authorization_url_carries_pkce_state_and_nonce() -> None:
    url = oidc.google(CLIENT_ID, "secret").authorization_url(
        state="st", nonce="nn", code_challenge="ch", redirect_uri="https://api/cb"
    )
    q = parse_qs(urlsplit(url).query)
    assert q["code_challenge_method"] == ["S256"]
    assert (q["state"], q["nonce"], q["code_challenge"]) == (["st"], ["nn"], ["ch"])
    assert q["scope"] == ["openid email profile"]


async def test_fake_provider_rejects_bad_codes_and_verifiers() -> None:
    f = FakeOidcProvider("google", trusts_email=True)
    url = f.authorization_url(
        state="s1", nonce="n", code_challenge="nomatch", redirect_uri="https://api/cb"
    )
    assert f.challenge_for("s1") == "nomatch"
    assert "state=s1" in url
    with pytest.raises(OidcError, match="PKCE"):
        await f.exchange(code="fake:a:b:1", code_verifier="v" * 43, redirect_uri="x", nonce="n")
    import base64
    import hashlib

    verifier = "w" * 43
    good = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    for i, code in enumerate(("nope", "other:a:b:1")):
        f.authorization_url(state=f"s{i + 3}", nonce="n", code_challenge=good, redirect_uri="x")
        with pytest.raises(OidcError, match="malformed"):
            await f.exchange(code=code, code_verifier=verifier, redirect_uri="x", nonce="n")
    # Each challenge is single use.
    with pytest.raises(OidcError, match="PKCE"):
        await f.exchange(code="fake:a:b:1", code_verifier=verifier, redirect_uri="x", nonce="n")


def _settings(**kw: Any) -> Settings:
    base = {
        "mera_env": "development",
        "mera_secret_key": "s" * 40,
        "mera_master_key": base64.b64encode(bytes(range(32))).decode(),
        "database_url": "postgresql+asyncpg://u:p@h/db",
    }
    return Settings(_env_file=None, **{**base, **kw})  # type: ignore[arg-type]


def test_registry_real_fake_and_none() -> None:
    real = build_providers(
        _settings(
            google_client_id="g",
            google_client_secret="gs",
            microsoft_client_id="m",
            microsoft_client_secret="ms",
        )
    )
    assert isinstance(real["google"], oidc.OidcClient)
    assert isinstance(real["microsoft"], oidc.OidcClient)
    fake = build_providers(_settings(sandbox_mode=True))
    assert isinstance(fake["google"], FakeOidcProvider)
    assert build_providers(_settings(sandbox_mode=False)) == {}
    # Fakes accept any identity: never outside development, even with sandbox on.
    assert build_providers(_settings(mera_env="staging", sandbox_mode=False)) == {}


async def test_string_email_verified_is_not_trusted() -> None:
    token = _id_token(email_verified="true")
    ident = await _exchange(_client(oidc.google, httpx.Response(200, json={"id_token": token})))
    assert ident.email_verified is False
