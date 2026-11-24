"""Real ``PlatformConnector`` implementations (M8.2, 8.3, 8.10, 8.11): LinkedIn (member and
organization), Facebook Pages and Instagram (Facebook Login), Threads, and X.

Each one is the platform's documented OAuth 2.0 authorization-code flow:

- LinkedIn: ``/oauth/v2/authorization`` + ``/oauth/v2/accessToken``; the member id from the
  OpenID ``userinfo``; for a Page, the first organization the member administers
  (``organizationAcls``). PKCE is not supported by LinkedIn's confidential-client flow, so
  the verifier is unused there; the signed ``state`` still binds the callback.
- Meta (Facebook Page, Instagram): Facebook Login dialog, code → user token → long-lived
  user token → ``/me/accounts`` (Page tokens do not expire when derived from a long-lived
  user token). Instagram resolves the Page's ``instagram_business_account``.
- Threads: ``threads.net/oauth/authorize`` → short-lived → long-lived token (60 days,
  refreshable with ``th_refresh_token``).
- X: OAuth 2.0 with PKCE (S256), ``offline.access`` for refresh tokens.

A workspace with several Pages connects the first one the grant covers; connecting another
Page means reconnecting with only that Page selected in Meta's dialog (documented in the
runbook). Credentials never leave the encrypted ``credentials_ct`` column.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from opentelemetry.instrumentation.utils import suppress_instrumentation

from mera.domain.value_objects.platform import AccountType, Platform
from mera.infrastructure.platforms.adapters.http import TIMEOUT
from mera.infrastructure.platforms.adapters.linkedin import LINKEDIN_VERSION
from mera.infrastructure.platforms.adapters.meta import GRAPH, GRAPH_VERSION
from mera.infrastructure.platforms.connectors import (
    ConnectedIdentity,
    ConnectorError,
    HealthReport,
    ReauthRequired,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClientCredentials:
    client_id: str
    client_secret: str


async def _call(
    method: str,
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None,
    **kw: Any,
) -> dict[str, Any]:
    try:
        # Some provider OAuth endpoints require secrets or tokens in the query string.
        with suppress_instrumentation():
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as c:
                resp = await c.request(method, url, **kw)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"could not reach the platform ({type(exc).__name__})") from exc
    if resp.status_code in (400, 401) and _looks_revoked(resp):
        raise ReauthRequired("the platform rejected the grant")
    if not resp.is_success:
        raise ConnectorError(f"the platform answered {resp.status_code}")
    try:
        body = resp.json() if resp.content else {}
    except ValueError as exc:
        raise ConnectorError("the platform answered with something that is not JSON") from exc
    return body if isinstance(body, dict) else {"data": body}


def _looks_revoked(resp: httpx.Response) -> bool:
    try:
        body = resp.json()
    except ValueError:
        return resp.status_code == 401
    if not isinstance(body, dict):
        return resp.status_code == 401
    err = body.get("error")
    if isinstance(err, dict):
        return err.get("code") in (102, 190) or resp.status_code == 401
    return (
        err in ("invalid_grant", "invalid_token", "unauthorized_client") or resp.status_code == 401
    )


def _expiry(seconds: Any) -> datetime | None:
    try:
        return datetime.now(UTC) + timedelta(seconds=int(seconds))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------ LinkedIn


class LinkedInConnector:
    AUTH = "https://www.linkedin.com/oauth/v2/authorization"
    TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"  # noqa: S105 - an endpoint
    MEMBER_SCOPES = ("openid", "profile", "w_member_social")
    ORG_SCOPES = (
        "openid",
        "profile",
        "w_organization_social",
        "r_organization_social",
        "rw_organization_admin",
    )

    def __init__(
        self,
        platform: Platform,
        client: ClientCredentials,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.platform = platform
        self._c = client
        self._t = transport

    @property
    def _scopes(self) -> tuple[str, ...]:
        return (
            self.ORG_SCOPES
            if self.platform is Platform.LINKEDIN_ORGANIZATION
            else self.MEMBER_SCOPES
        )

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return f"{self.AUTH}?" + urlencode(
            {
                "response_type": "code",
                "client_id": self._c.client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": " ".join(self._scopes),
            }
        )

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ConnectedIdentity:
        tok = await _call(
            "POST",
            self.TOKEN,
            transport=self._t,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self._c.client_id,
                "client_secret": self._c.client_secret,
            },
        )
        headers = {"Authorization": f"Bearer {tok['access_token']}"}
        me = await _call(
            "GET", "https://api.linkedin.com/v2/userinfo", transport=self._t, headers=headers
        )
        scopes = str(tok.get("scope", "")).replace(",", " ").split()
        creds = {
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "member_id": me.get("sub"),
            "granted_scopes": scopes,
        }
        if self.platform is Platform.LINKEDIN_ORGANIZATION:
            acls = await _call(
                "GET",
                "https://api.linkedin.com/rest/organizationAcls",
                transport=self._t,
                headers={
                    **headers,
                    "LinkedIn-Version": LINKEDIN_VERSION,
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                params={"q": "roleAssignee", "role": "ADMINISTRATOR", "state": "APPROVED"},
            )
            orgs = [
                e.get("organization") for e in acls.get("elements", []) if e.get("organization")
            ]
            if not orgs:
                raise ConnectorError("this LinkedIn member administers no Page")
            org = str(orgs[0])
            info = await _call(
                "GET",
                f"https://api.linkedin.com/rest/organizations/{org.rsplit(':', 1)[-1]}",
                transport=self._t,
                headers={**headers, "LinkedIn-Version": LINKEDIN_VERSION},
            )
            name = info.get("localizedName") or org
            return ConnectedIdentity(
                platform_account_id=org,
                handle=str(info.get("vanityName") or name),
                display_name=str(name),
                avatar_url=None,
                account_type=AccountType.ORGANIZATION,
                credentials=creds,
                granted_scopes=scopes,
                expires_at=_expiry(tok.get("expires_in")),
            )
        return ConnectedIdentity(
            platform_account_id=f"urn:li:person:{me.get('sub')}",
            handle=str(me.get("name") or me.get("sub")),
            display_name=me.get("name"),
            avatar_url=me.get("picture"),
            account_type=AccountType.PERSONAL,
            credentials=creds,
            granted_scopes=scopes,
            expires_at=_expiry(tok.get("expires_in")),
        )

    async def refresh(self, credentials: dict[str, Any]) -> tuple[dict[str, Any], datetime | None]:
        if not credentials.get("refresh_token"):
            # Members without programmatic refresh reconnect before the 60-day expiry.
            raise ReauthRequired("LinkedIn issued no refresh token; reconnect the account")
        tok = await _call(
            "POST",
            self.TOKEN,
            transport=self._t,
            data={
                "grant_type": "refresh_token",
                "refresh_token": credentials["refresh_token"],
                "client_id": self._c.client_id,
                "client_secret": self._c.client_secret,
            },
        )
        return (
            {
                **credentials,
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token", credentials["refresh_token"]),
                "granted_scopes": str(tok.get("scope", " ")).replace(",", " ").split()
                or credentials.get("granted_scopes", []),
            },
            _expiry(tok.get("expires_in")),
        )

    async def check_health(self, credentials: dict[str, Any]) -> HealthReport:
        await _call(
            "GET",
            "https://api.linkedin.com/v2/userinfo",
            transport=self._t,
            headers={"Authorization": f"Bearer {credentials.get('access_token')}"},
        )
        info = await _call(
            "POST",
            "https://www.linkedin.com/oauth/v2/introspectToken",
            transport=self._t,
            data={
                "token": credentials.get("access_token", ""),
                "client_id": self._c.client_id,
                "client_secret": self._c.client_secret,
            },
        )
        granted = set(str(info.get("scope", "")).replace(",", " ").split())
        required = set(self._scopes)
        return HealthReport(
            token_valid=bool(info.get("active", True)),
            scopes_ok=required <= granted,
            rate_remaining=None,
            last_error=None if required <= granted else "LinkedIn publishing permissions changed",
        )

    async def revoke(self, credentials: dict[str, Any]) -> None:
        await _call(
            "POST",
            "https://www.linkedin.com/oauth/v2/revoke",
            transport=self._t,
            data={
                "token": credentials.get("access_token", ""),
                "client_id": self._c.client_id,
                "client_secret": self._c.client_secret,
            },
        )


# ---------------------------------------------------------------------------- Meta


class MetaConnector:
    """Facebook Pages and Instagram business accounts, both through Facebook Login."""

    DIALOG = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
    PAGE_SCOPES = (
        "pages_show_list",
        "pages_manage_posts",
        "pages_read_engagement",
        "read_insights",
    )
    IG_SCOPES = (
        "pages_show_list",
        "pages_read_engagement",
        "instagram_basic",
        "instagram_content_publish",
        "instagram_manage_insights",
        "business_management",
    )

    def __init__(
        self,
        platform: Platform,
        client: ClientCredentials,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.platform = platform
        self._c = client
        self._t = transport

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        scopes = self.IG_SCOPES if self.platform is Platform.INSTAGRAM else self.PAGE_SCOPES
        return f"{self.DIALOG}?" + urlencode(
            {
                "client_id": self._c.client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "response_type": "code",
                "scope": ",".join(scopes),
            }
        )

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ConnectedIdentity:
        short = await _call(
            "GET",
            f"{GRAPH}/oauth/access_token",
            transport=self._t,
            params={
                "client_id": self._c.client_id,
                "client_secret": self._c.client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        long = await _call(
            "GET",
            f"{GRAPH}/oauth/access_token",
            transport=self._t,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self._c.client_id,
                "client_secret": self._c.client_secret,
                "fb_exchange_token": short["access_token"],
            },
        )
        user_token = long["access_token"]
        pages = await _call(
            "GET",
            f"{GRAPH}/me/accounts",
            transport=self._t,
            params={
                "fields": (
                    "id,name,access_token,username,picture.type(large){url},instagram_business_account{id,username,name,profile_picture_url}"
                ),
                "access_token": user_token,
            },
        )
        data = pages.get("data") or []
        if not data:
            # With Meta Granular Scopes, inspect debug_token for selected target IDs.
            try:
                debug_info = await _call(
                    "GET",
                    f"{GRAPH}/debug_token",
                    transport=self._t,
                    params={
                        "input_token": user_token,
                        "access_token": f"{self._c.client_id}|{self._c.client_secret}",
                    },
                )
                debug_data = debug_info.get("data", {})
                target_ids: set[str] = set()
                for gs in debug_data.get("granular_scopes", []):
                    for tid in gs.get("target_ids", []):
                        target_ids.add(str(tid))

                for tid in target_ids:
                    try:
                        p = await _call(
                            "GET",
                            f"{GRAPH}/{tid}",
                            transport=self._t,
                            params={
                                "fields": (
                                    "id,name,access_token,username,picture.type(large){url},instagram_business_account{id,username,name,profile_picture_url}"
                                ),
                                "access_token": user_token,
                            },
                        )
                        if p.get("id"):
                            data.append(p)
                    except Exception as p_err:
                        log.warning(
                            "meta.target_page_fetch_failed",
                            message="Could not fetch a Meta target page during account discovery.",
                            page_id=tid,
                            error_type=type(p_err).__name__,
                            exc_info=True,
                        )
            except Exception as d_err:
                log.warning(
                    "meta.debug_token_inspection_failed",
                    message="Could not inspect the Meta token during account discovery.",
                    error_type=type(d_err).__name__,
                    exc_info=True,
                )

        if not data:
            raise ConnectorError("this Facebook login manages no Page")
        if self.platform is Platform.INSTAGRAM:
            linked = [p for p in data if p.get("instagram_business_account")]
            if not linked:
                raise ConnectorError("no Page here has an Instagram business account linked")
            identities = tuple(self._instagram(p, user_token) for p in linked)
        else:
            identities = tuple(self._page(p, user_token) for p in data)
        # Several Pages under one login: the person picks one when redeeming (P2-M1.5).
        if len(identities) == 1:
            return identities[0]
        return replace(identities[0], choices=identities)

    def _instagram(self, page: dict[str, Any], user_token: str) -> ConnectedIdentity:
        ig = page["instagram_business_account"]
        return ConnectedIdentity(
            platform_account_id=str(ig["id"]),
            handle=str(ig.get("username") or ig["id"]),
            display_name=ig.get("name") or ig.get("username"),
            avatar_url=ig.get("profile_picture_url"),
            account_type=AccountType.BUSINESS,
            credentials={
                "access_token": page["access_token"],
                "page_id": page["id"],
                "user_token": user_token,
            },
            granted_scopes=list(self.IG_SCOPES),
            expires_at=None,
        )

    def _page(self, page: dict[str, Any], user_token: str) -> ConnectedIdentity:
        pic = (
            page.get("picture", {}).get("data", {}).get("url")
            if isinstance(page.get("picture"), dict)
            else None
        )
        return ConnectedIdentity(
            platform_account_id=str(page["id"]),
            handle=str(page.get("username") or page["name"]),
            display_name=page.get("name"),
            avatar_url=pic,
            account_type=AccountType.PAGE,
            credentials={"access_token": page["access_token"], "user_token": user_token},
            granted_scopes=list(self.PAGE_SCOPES),
            expires_at=None,  # Page tokens from a long-lived user token do not expire
        )

    async def refresh(self, credentials: dict[str, Any]) -> tuple[dict[str, Any], datetime | None]:
        return credentials, None  # non-expiring Page token; revocation shows up in health

    async def check_health(self, credentials: dict[str, Any]) -> HealthReport:
        body = await _call(
            "GET",
            f"{GRAPH}/debug_token",
            transport=self._t,
            params={
                "input_token": credentials.get("access_token", ""),
                "access_token": f"{self._c.client_id}|{self._c.client_secret}",
            },
        )
        data = body.get("data") or {}
        if not data.get("is_valid", False):
            raise ReauthRequired("Meta reports the token is no longer valid")
        return HealthReport(token_valid=True, scopes_ok=True, rate_remaining=None)

    async def revoke(self, credentials: dict[str, Any]) -> None:
        token = credentials.get("user_token") or credentials.get("access_token", "")
        await _call(
            "DELETE", f"{GRAPH}/me/permissions", transport=self._t, params={"access_token": token}
        )


# ------------------------------------------------------------------------- Threads


class ThreadsConnector:
    platform = Platform.THREADS
    AUTH = "https://threads.net/oauth/authorize"
    SCOPES = (
        "threads_basic",
        "threads_content_publish",
        "threads_manage_insights",
        "threads_manage_replies",
    )

    def __init__(
        self, client: ClientCredentials, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._c = client
        self._t = transport

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return f"{self.AUTH}?" + urlencode(
            {
                "client_id": self._c.client_id,
                "redirect_uri": redirect_uri,
                "scope": ",".join(self.SCOPES),
                "response_type": "code",
                "state": state,
            }
        )

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ConnectedIdentity:
        short = await _call(
            "POST",
            "https://graph.threads.net/oauth/access_token",
            transport=self._t,
            data={
                "client_id": self._c.client_id,
                "client_secret": self._c.client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        long = await _call(
            "GET",
            "https://graph.threads.net/access_token",
            transport=self._t,
            params={
                "grant_type": "th_exchange_token",
                "client_secret": self._c.client_secret,
                "access_token": short["access_token"],
            },
        )
        me = await _call(
            "GET",
            "https://graph.threads.net/v1.0/me",
            transport=self._t,
            params={"fields": "id,username,name", "access_token": long["access_token"]},
        )
        return ConnectedIdentity(
            platform_account_id=str(me["id"]),
            handle=str(me.get("username") or me["id"]),
            display_name=me.get("name"),
            avatar_url=None,
            account_type=AccountType.PERSONAL,
            credentials={"access_token": long["access_token"]},
            granted_scopes=list(self.SCOPES),
            expires_at=_expiry(long.get("expires_in")),
        )

    async def refresh(self, credentials: dict[str, Any]) -> tuple[dict[str, Any], datetime | None]:
        tok = await _call(
            "GET",
            "https://graph.threads.net/refresh_access_token",
            transport=self._t,
            params={
                "grant_type": "th_refresh_token",
                "access_token": credentials.get("access_token", ""),
            },
        )
        return {**credentials, "access_token": tok["access_token"]}, _expiry(tok.get("expires_in"))

    async def check_health(self, credentials: dict[str, Any]) -> HealthReport:
        await _call(
            "GET",
            "https://graph.threads.net/v1.0/me",
            transport=self._t,
            params={"fields": "id", "access_token": credentials.get("access_token", "")},
        )
        return HealthReport(token_valid=True, scopes_ok=True, rate_remaining=None)

    async def revoke(self, credentials: dict[str, Any]) -> None:
        return None  # Threads has no token revocation endpoint; the token is destroyed here


# ------------------------------------------------------------------------------- X


class XConnector:
    platform = Platform.X
    AUTH = "https://x.com/i/oauth2/authorize"
    TOKEN = "https://api.x.com/2/oauth2/token"  # noqa: S105 - an endpoint
    SCOPES = ("tweet.read", "tweet.write", "users.read", "media.write", "offline.access")

    def __init__(
        self, client: ClientCredentials, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._c = client
        self._t = transport

    def _basic(self) -> dict[str, str]:
        raw = f"{self._c.client_id}:{self._c.client_secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        return f"{self.AUTH}?" + urlencode(
            {
                "response_type": "code",
                "client_id": self._c.client_id,
                "redirect_uri": redirect_uri,
                "scope": " ".join(self.SCOPES),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ConnectedIdentity:
        tok = await _call(
            "POST",
            self.TOKEN,
            transport=self._t,
            headers=self._basic(),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "client_id": self._c.client_id,
            },
        )
        me = await _call(
            "GET",
            "https://api.x.com/2/users/me",
            transport=self._t,
            headers={"Authorization": f"Bearer {tok['access_token']}"},
            params={"user.fields": "profile_image_url"},
        )
        user = me.get("data") or {}
        return ConnectedIdentity(
            platform_account_id=str(user["id"]),
            handle=str(user.get("username") or user["id"]),
            display_name=user.get("name"),
            avatar_url=user.get("profile_image_url"),
            account_type=AccountType.PERSONAL,
            credentials={
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token"),
            },
            granted_scopes=str(tok.get("scope", "")).split(),
            expires_at=_expiry(tok.get("expires_in")),
        )

    async def refresh(self, credentials: dict[str, Any]) -> tuple[dict[str, Any], datetime | None]:
        if not credentials.get("refresh_token"):
            raise ReauthRequired("X issued no refresh token; reconnect the account")
        tok = await _call(
            "POST",
            self.TOKEN,
            transport=self._t,
            headers=self._basic(),
            data={
                "grant_type": "refresh_token",
                "refresh_token": credentials["refresh_token"],
                "client_id": self._c.client_id,
            },
        )
        return (
            {
                **credentials,
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token", credentials["refresh_token"]),
            },
            _expiry(tok.get("expires_in")),
        )

    async def check_health(self, credentials: dict[str, Any]) -> HealthReport:
        await _call(
            "GET",
            "https://api.x.com/2/users/me",
            transport=self._t,
            headers={"Authorization": f"Bearer {credentials.get('access_token')}"},
        )
        return HealthReport(token_valid=True, scopes_ok=True, rate_remaining=None)

    async def revoke(self, credentials: dict[str, Any]) -> None:
        await _call(
            "POST",
            "https://api.x.com/2/oauth2/revoke",
            transport=self._t,
            headers=self._basic(),
            data={"token": credentials.get("access_token", ""), "token_type_hint": "access_token"},
        )
