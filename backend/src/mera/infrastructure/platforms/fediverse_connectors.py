"""Bluesky and Mastodon connectors (P2-M3.2, P2-M3.4).

**Bluesky** connects with an *app password* (Settings → Privacy and security → App
passwords on Bluesky): ``com.atproto.server.createSession`` returns a session, and only
the session is kept. The app password itself is used once and never stored, and anything
that does not look like an app password is refused, so nobody hands Mera the password to
their whole account. Sessions are refreshed with ``refreshSession``; revoking the app
password on Bluesky ends Mera's access.

**Mastodon** is OAuth on whichever instance the person names. Mera registers its own app
on that instance the first time (``POST /api/v1/apps``; the registration is kept, the
secret encrypted, by the account service) and then runs the authorization-code flow with
PKCE. The instance host is checked to resolve only to public addresses before any request.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from mera.domain.value_objects.platform import AccountType, Platform
from mera.infrastructure.http.safe_fetch import is_public, system_resolver
from mera.infrastructure.platforms.adapters.bluesky import DEFAULT_SERVICE
from mera.infrastructure.platforms.adapters.http import TIMEOUT
from mera.infrastructure.platforms.connectors import (
    ConnectedIdentity,
    ConnectorError,
    HealthReport,
    ReauthRequired,
)

APP_PASSWORD = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$")
_HOST = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(:[0-9]{2,5})?$"
)
BLUESKY_ACCESS_LIFETIME = timedelta(minutes=90)
MASTODON_SCOPES = "read write"


class InvalidInstance(ConnectorError):
    """The host is not a public HTTPS server Mera may talk to."""


async def public_host(raw: str, *, resolve: Any = system_resolver) -> str:
    """``https://Mastodon.Social/`` or ``mastodon.social`` → ``mastodon.social``, after
    checking that every address it resolves to is public (no SSRF into the deployment's
    own network)."""
    text = raw.strip().lower()
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme != "https" or parts.username or parts.password:
            raise InvalidInstance("use the server's address, like mastodon.social")
        text = parts.netloc
    text = text.rstrip("/").rstrip(".")
    if not _HOST.match(text):
        raise InvalidInstance("use the server's address, like mastodon.social")
    host, _, port = text.partition(":")
    if port and port != "443":
        raise InvalidInstance("only the standard HTTPS port is supported")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise InvalidInstance("that address is not a public server")
    try:
        addresses = await resolve(host, 443)
    except Exception as exc:
        raise InvalidInstance("that server could not be found") from exc
    if not addresses or not all(is_public(ipaddress.ip_address(a)) for a in addresses):
        raise InvalidInstance("that address is not a public server")
    return host


async def _call(
    method: str, url: str, *, transport: httpx.AsyncBaseTransport | None, **kw: Any
) -> tuple[int, dict[str, Any]]:
    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT, transport=transport, follow_redirects=False
        ) as c:
            resp = await c.request(method, url, **kw)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"could not reach the server ({type(exc).__name__})") from exc
    try:
        body = resp.json() if resp.content else {}
    except ValueError:
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {"data": body}


# --------------------------------------------------------------------------- Bluesky


def _xrpc(credentials: dict[str, Any], nsid: str) -> str:
    return f"{credentials.get('service') or DEFAULT_SERVICE}/xrpc/{nsid}"


class BlueskyConnector:
    platform = Platform.BLUESKY
    connect_method = "app_password"

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolve: Any = system_resolver,
    ) -> None:
        self._t = transport
        self._resolve = resolve

    # The OAuth redirect is not how Bluesky connects (app passwords; see the module doc).
    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        raise ConnectorError("Bluesky connects with an app password")

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ConnectedIdentity:
        raise ConnectorError("Bluesky connects with an app password")

    async def sign_in(
        self, *, identifier: str, secret: str, service: str | None = None
    ) -> ConnectedIdentity:
        if not APP_PASSWORD.match(secret.strip()):
            raise ConnectorError(
                "use an app password (xxxx-xxxx-xxxx-xxxx), not your account password"
            )
        base = DEFAULT_SERVICE
        if service and service.strip():
            base = f"https://{await public_host(service, resolve=self._resolve)}"
        status, body = await _call(
            "POST",
            f"{base}/xrpc/com.atproto.server.createSession",
            transport=self._t,
            json={"identifier": identifier.strip().lstrip("@"), "password": secret.strip()},
        )
        if status == 401:
            raise ConnectorError("Bluesky did not accept that handle and app password")
        if status != 200 or not body.get("accessJwt"):
            raise ConnectorError(f"Bluesky answered {status}")
        did, handle = str(body["did"]), str(body.get("handle") or body["did"])
        profile: dict[str, Any] = {}
        pstatus, pbody = await _call(
            "GET",
            f"{base}/xrpc/app.bsky.actor.getProfile",
            transport=self._t,
            params={"actor": did},
            headers={"Authorization": f"Bearer {body['accessJwt']}"},
        )
        if pstatus == 200:
            profile = pbody
        return ConnectedIdentity(
            platform_account_id=did,
            handle=handle,
            display_name=profile.get("displayName") or handle,
            avatar_url=profile.get("avatar"),
            account_type=AccountType.PERSONAL,
            credentials={
                "did": did,
                "handle": handle,
                "service": base,
                "access_jwt": body["accessJwt"],
                "refresh_jwt": body["refreshJwt"],
            },
            granted_scopes=["app_password"],
            expires_at=datetime.now(UTC) + BLUESKY_ACCESS_LIFETIME,
        )

    async def refresh(self, credentials: dict[str, Any]) -> tuple[dict[str, Any], datetime | None]:
        status, body = await _call(
            "POST",
            _xrpc(credentials, "com.atproto.server.refreshSession"),
            transport=self._t,
            headers={"Authorization": f"Bearer {credentials.get('refresh_jwt', '')}"},
        )
        if status in (400, 401) and body.get("error") in (
            "ExpiredToken",
            "InvalidToken",
            "AuthenticationRequired",
            None,
        ):
            raise ReauthRequired("Bluesky ended the session (the app password may be revoked)")
        if status != 200 or not body.get("accessJwt"):
            raise ConnectorError(f"Bluesky answered {status}")
        return (
            {**credentials, "access_jwt": body["accessJwt"], "refresh_jwt": body["refreshJwt"]},
            datetime.now(UTC) + BLUESKY_ACCESS_LIFETIME,
        )

    async def check_health(self, credentials: dict[str, Any]) -> HealthReport:
        status, body = await _call(
            "GET",
            _xrpc(credentials, "com.atproto.server.getSession"),
            transport=self._t,
            headers={"Authorization": f"Bearer {credentials.get('access_jwt', '')}"},
        )
        if status == 200:
            return HealthReport(token_valid=True, scopes_ok=True, rate_remaining=None)
        if status == 400 and body.get("error") == "ExpiredToken":
            # Normal between refreshes: the next refresh renews it.
            return HealthReport(
                token_valid=True, scopes_ok=True, rate_remaining=None, last_error="session renews"
            )
        if status in (400, 401):
            raise ReauthRequired("Bluesky rejected the session")
        raise ConnectorError(f"Bluesky answered {status}")

    async def revoke(self, credentials: dict[str, Any]) -> None:
        await _call(
            "POST",
            _xrpc(credentials, "com.atproto.server.deleteSession"),
            transport=self._t,
            headers={"Authorization": f"Bearer {credentials.get('refresh_jwt', '')}"},
        )


# -------------------------------------------------------------------------- Mastodon


@dataclass(frozen=True, slots=True)
class MastodonClient:
    client_id: str
    client_secret: str


class MastodonConnector:
    """Instance-independent half: app registration and the stored-token calls."""

    platform = Platform.MASTODON
    connect_method = "oauth_instance"
    per_instance = True

    def __init__(
        self,
        *,
        website: str,
        transport: httpx.AsyncBaseTransport | None = None,
        resolve: Any = system_resolver,
    ) -> None:
        self._website = website
        self._t = transport
        self._resolve = resolve

    async def instance_host(self, raw: str) -> str:
        return await public_host(raw, resolve=self._resolve)

    async def register_app(self, host: str, redirect_uri: str) -> MastodonClient:
        status, body = await _call(
            "POST",
            f"https://{host}/api/v1/apps",
            transport=self._t,
            data={
                "client_name": "Mera",
                "redirect_uris": redirect_uri,
                "scopes": MASTODON_SCOPES,
                "website": self._website,
            },
        )
        if status != 200 or not body.get("client_id") or not body.get("client_secret"):
            raise ConnectorError(f"{host} did not register the app ({status})")
        return MastodonClient(str(body["client_id"]), str(body["client_secret"]))

    def for_instance(self, host: str, client: MastodonClient) -> MastodonInstanceConnector:
        return MastodonInstanceConnector(host, client, transport=self._t)

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        raise ConnectorError("choose the Mastodon server first")

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ConnectedIdentity:
        raise ConnectorError("choose the Mastodon server first")

    async def refresh(self, credentials: dict[str, Any]) -> tuple[dict[str, Any], datetime | None]:
        return credentials, None  # Mastodon tokens do not expire; revocation shows in health

    async def check_health(self, credentials: dict[str, Any]) -> HealthReport:
        status, _body = await _call(
            "GET",
            f"https://{credentials.get('instance')}/api/v1/accounts/verify_credentials",
            transport=self._t,
            headers={"Authorization": f"Bearer {credentials.get('access_token', '')}"},
        )
        if status == 200:
            return HealthReport(token_valid=True, scopes_ok=True, rate_remaining=None)
        if status in (401, 403):
            raise ReauthRequired("the Mastodon server rejected the token")
        raise ConnectorError(f"the Mastodon server answered {status}")

    async def revoke(self, credentials: dict[str, Any]) -> None:
        await _call(
            "POST",
            f"https://{credentials.get('instance')}/oauth/revoke",
            transport=self._t,
            data={
                "client_id": credentials.get("client_id", ""),
                "client_secret": credentials.get("client_secret", ""),
                "token": credentials.get("access_token", ""),
            },
        )


class MastodonInstanceConnector:
    """The OAuth flow against one instance, with Mera's app there."""

    platform = Platform.MASTODON

    def __init__(
        self, host: str, client: MastodonClient, *, transport: httpx.AsyncBaseTransport | None
    ) -> None:
        self._host = host
        self._c = client
        self._t = transport

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return f"https://{self._host}/oauth/authorize?" + urlencode(
            {
                "response_type": "code",
                "client_id": self._c.client_id,
                "redirect_uri": redirect_uri,
                "scope": MASTODON_SCOPES,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ConnectedIdentity:
        status, tok = await _call(
            "POST",
            f"https://{self._host}/oauth/token",
            transport=self._t,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self._c.client_id,
                "client_secret": self._c.client_secret,
                "code_verifier": code_verifier,
                "scope": MASTODON_SCOPES,
            },
        )
        if status != 200 or not tok.get("access_token"):
            raise ConnectorError(f"{self._host} did not issue a token ({status})")
        token = str(tok["access_token"])
        status, me = await _call(
            "GET",
            f"https://{self._host}/api/v1/accounts/verify_credentials",
            transport=self._t,
            headers={"Authorization": f"Bearer {token}"},
        )
        if status != 200 or not me.get("id"):
            raise ConnectorError(f"{self._host} did not return the account ({status})")
        username = str(me.get("username") or me["id"])
        return ConnectedIdentity(
            platform_account_id=f"{me['id']}@{self._host}",
            handle=f"{username}@{self._host}",
            display_name=me.get("display_name") or username,
            avatar_url=me.get("avatar"),
            account_type=AccountType.PERSONAL,
            credentials={
                "instance": self._host,
                "access_token": token,
                # Kept with the account so revocation needs no other lookup.
                "client_id": self._c.client_id,
                "client_secret": self._c.client_secret,
            },
            granted_scopes=MASTODON_SCOPES.split(),
            expires_at=None,
        )

    async def refresh(self, credentials: dict[str, Any]) -> tuple[dict[str, Any], datetime | None]:
        return credentials, None

    async def check_health(self, credentials: dict[str, Any]) -> HealthReport:
        return HealthReport(token_valid=True, scopes_ok=True, rate_remaining=None)

    async def revoke(self, credentials: dict[str, Any]) -> None:
        return None
