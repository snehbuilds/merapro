"""Connecting social accounts (M3.2): the ``PlatformConnector`` port and its sandbox fake.

A connector owns everything about *authority over an account*: the consent redirect, code
exchange, token refresh, health and revocation. Publishing is the separate
``PlatformAdapter`` (M8.1). Real connectors ship with their adapters in M8 (Bluesky and
Mastodon first, docs/11 §2.1); until then only the sandbox fake exists, and only in
development (the lesson of the Sprint 3 review M6: a fake accepts whatever it is told).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

from mera.config import Env, Settings
from mera.domain.value_objects.platform import AccountType, Platform
from mera.infrastructure.platforms.capabilities import CAPABILITIES


class ConnectorError(Exception):
    """The platform refused or failed; the account's state is unchanged."""


class ReauthRequired(ConnectorError):
    """The grant is gone (revoked, expired beyond refresh, scopes withdrawn): only the
    account owner can fix this, by reconnecting."""


@dataclass(frozen=True, slots=True)
class ConnectedIdentity:
    platform_account_id: str
    handle: str
    display_name: str | None
    avatar_url: str | None
    account_type: AccountType
    credentials: dict[str, Any]
    granted_scopes: list[str]
    expires_at: datetime | None
    # Several Pages under one login (P2-M1.5): every one, this identity among them. The
    # person picks one when redeeming, and only the chosen credentials are kept.
    choices: tuple[ConnectedIdentity, ...] = ()


@dataclass(frozen=True, slots=True)
class HealthReport:
    token_valid: bool
    scopes_ok: bool
    rate_remaining: int | None
    last_error: str | None = None


class PlatformConnector(Protocol):
    platform: Platform

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str: ...

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ConnectedIdentity: ...

    async def refresh(
        self, credentials: dict[str, Any]
    ) -> tuple[dict[str, Any], datetime | None]: ...

    async def check_health(self, credentials: dict[str, Any]) -> HealthReport: ...

    async def revoke(self, credentials: dict[str, Any]) -> None: ...


def s256(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


@dataclass
class FakeConnector:
    """Sandbox connector (docs/10 §5). The full Mera-side flow runs for real — signed
    state, PKCE, encryption, dedupe, health — only the platform is simulated.

    The authorization code chooses the identity: ``fake:<platform_account_id>:<handle>:<type>``.
    A handle containing ``revoked`` simulates a platform that later withdraws the grant,
    which is how NEEDS_REAUTH is exercised without a real outage.
    """

    platform: Platform
    token_lifetime: timedelta = timedelta(hours=1)
    revoked: list[str] = field(default_factory=list)  # platform_account_ids revoked here
    _challenges: dict[str, str] = field(default_factory=dict)

    @property
    def connect_method(self) -> str:
        return CAPABILITIES[self.platform].connect_method

    @property
    def per_instance(self) -> bool:
        return self.connect_method == "oauth_instance"

    async def instance_host(self, raw: str) -> str:
        host = raw.strip().lower().removeprefix("https://").rstrip("/")
        if not host or "/" in host:
            raise ConnectorError("use the server's address, like mastodon.social")
        return host

    async def register_app(self, host: str, redirect_uri: str) -> Any:
        from mera.infrastructure.platforms.fediverse_connectors import MastodonClient

        return MastodonClient(f"sandbox-{host}", "sandbox-secret")

    def for_instance(self, host: str, client: Any) -> FakeConnector:
        return self

    async def sign_in(
        self, *, identifier: str, secret: str, service: str | None = None
    ) -> ConnectedIdentity:
        """Sandbox app-password sign-in: any app-password-shaped secret works; a handle
        containing ``revoked`` behaves as in the OAuth fake."""
        from mera.infrastructure.platforms.fediverse_connectors import APP_PASSWORD

        if not APP_PASSWORD.match(secret.strip()):
            raise ConnectorError(
                "use an app password (xxxx-xxxx-xxxx-xxxx), not your account password"
            )
        handle = identifier.strip().lstrip("@")
        return ConnectedIdentity(
            platform_account_id=f"did:plc:{handle.replace('.', '-')}",
            handle=handle,
            display_name=handle.split(".")[0].replace("_", " ").title(),
            avatar_url=None,
            account_type=AccountType.PERSONAL,
            credentials=self._issue(handle),
            granted_scopes=["app_password"],
            expires_at=datetime.now(UTC) + self.token_lifetime,
        )

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        self._challenges[state] = code_challenge
        # An account type the platform accepts (Instagram has business accounts only).
        kind = CAPABILITIES[self.platform].account_types[0].value
        code = f"fake:sandbox-{self.platform.value}:{self.platform.value}_sandbox:{kind}"
        return f"{redirect_uri}?{urlencode({'code': code, 'state': state})}"

    async def exchange(
        self, *, code: str, code_verifier: str, redirect_uri: str
    ) -> ConnectedIdentity:
        challenge = s256(code_verifier)
        state = next((s for s, c in self._challenges.items() if c == challenge), None)
        if state is None:
            raise ConnectorError("PKCE verifier does not match")
        del self._challenges[state]
        try:
            prefix, account_ids, handles, kind = code.split(":", 3)
            account_type = AccountType(kind)
        except ValueError as exc:
            raise ConnectorError("malformed fake code") from exc
        if prefix != "fake" or account_type not in CAPABILITIES[self.platform].account_types:
            raise ConnectorError("malformed fake code")
        # "id1,id2" with "h1,h2" simulates a login that manages several Pages.
        pairs = list(zip(account_ids.split(","), handles.split(","), strict=False))
        identities = tuple(
            ConnectedIdentity(
                platform_account_id=account_id,
                handle=handle,
                display_name=handle.replace("_", " ").title(),
                avatar_url=None,
                account_type=account_type,
                credentials=self._issue(handle),
                granted_scopes=["read", "write"],
                expires_at=datetime.now(UTC) + self.token_lifetime,
            )
            for account_id, handle in pairs
        )
        if len(identities) == 1:
            return identities[0]
        return replace(identities[0], choices=identities)

    async def refresh(self, credentials: dict[str, Any]) -> tuple[dict[str, Any], datetime | None]:
        if "revoked" in credentials.get("handle", ""):
            raise ReauthRequired("the platform rejected the refresh token")
        return self._issue(credentials["handle"]), datetime.now(UTC) + self.token_lifetime

    async def check_health(self, credentials: dict[str, Any]) -> HealthReport:
        if "revoked" in credentials.get("handle", ""):
            raise ReauthRequired("the platform reports the grant was revoked")
        return HealthReport(token_valid=True, scopes_ok=True, rate_remaining=100)

    async def revoke(self, credentials: dict[str, Any]) -> None:
        self.revoked.append(credentials["handle"])

    @staticmethod
    def _issue(handle: str) -> dict[str, Any]:
        return {
            "handle": handle,
            "access_token": "fake_at_" + secrets.token_urlsafe(16),
            "refresh_token": "fake_rt_" + secrets.token_urlsafe(16),
        }


def build_connectors(settings: Settings) -> dict[Platform, PlatformConnector]:
    """Sandbox (development only) connects fake accounts on every platform. Otherwise each
    Phase 1 platform is connectable once its app credentials are configured (M8)."""
    if settings.sandbox_mode and settings.mera_env is Env.DEVELOPMENT:
        return {p: FakeConnector(p) for p in Platform}
    # Bluesky (app passwords) and Mastodon (an app registered per instance on first use)
    # need no app credentials of their own, so they are always connectable (P2-M3).
    # Their hosts are chosen by the person connecting: every request goes through the
    # guarded transport (public addresses only, no DNS rebinding).
    from mera.infrastructure.http.safe_fetch import guarded_transport
    from mera.infrastructure.platforms.fediverse_connectors import (
        BlueskyConnector,
        MastodonConnector,
    )
    from mera.infrastructure.platforms.oauth_connectors import (
        ClientCredentials,
        LinkedInConnector,
        MetaConnector,
        ThreadsConnector,
        XConnector,
    )

    guard = guarded_transport(system_trust_store=settings.http_system_trust_store)
    out: dict[Platform, PlatformConnector] = {
        Platform.BLUESKY: BlueskyConnector(transport=guard),
        Platform.MASTODON: MastodonConnector(website=settings.mera_public_url, transport=guard),
    }
    if settings.linkedin_client_id and settings.linkedin_client_secret:
        li = ClientCredentials(
            settings.linkedin_client_id, settings.linkedin_client_secret.get_secret_value()
        )
        out[Platform.LINKEDIN_MEMBER] = LinkedInConnector(Platform.LINKEDIN_MEMBER, li)
        out[Platform.LINKEDIN_ORGANIZATION] = LinkedInConnector(Platform.LINKEDIN_ORGANIZATION, li)
    if settings.meta_app_id and settings.meta_app_secret:
        meta = ClientCredentials(settings.meta_app_id, settings.meta_app_secret.get_secret_value())
        out[Platform.FACEBOOK] = MetaConnector(Platform.FACEBOOK, meta)
        out[Platform.INSTAGRAM] = MetaConnector(Platform.INSTAGRAM, meta)
    if settings.threads_app_id and settings.threads_app_secret:
        out[Platform.THREADS] = ThreadsConnector(
            ClientCredentials(
                settings.threads_app_id, settings.threads_app_secret.get_secret_value()
            )
        )
    if settings.x_client_id and settings.x_client_secret:
        out[Platform.X] = XConnector(
            ClientCredentials(settings.x_client_id, settings.x_client_secret.get_secret_value())
        )
    return out
