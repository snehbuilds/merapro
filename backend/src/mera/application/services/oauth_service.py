"""OAuth login (M2.7, docs/04 §2.2): Authorization Code + PKCE with Google and Microsoft.

Two PKCE legs. Mera ↔ provider uses a server-held verifier. Client ↔ Mera uses the
client's own challenge: the callback never hands the browser a token, only a one-time code
that is worthless without the client's verifier (``POST /auth/oauth/exchange``). So a
leaked callback URL, browser history entry or referrer cannot be replayed into a session.

Account resolution, in order:
1. A known ``(provider, subject)`` signs in that user.
2. An existing account with the same email is linked **only** if the provider is trusted to
   assert email ownership (Google) and asserts ``email_verified``. Otherwise the callback
   fails with ``account_exists``: an attacker who controls a Microsoft tenant could
   otherwise claim any address.
3. Otherwise a new password-less user is created.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.context import RequestMeta
from mera.application.errors import AuthenticationFailed, InvalidInput, NotFoundError
from mera.application.services.auth_service import AuthService, TokenPair
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.crypto.api_keys import hash_key
from mera.infrastructure.db.models import OAuthFlow, OAuthIdentity, ReauthProof, RefreshToken, User
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import global_transaction
from mera.infrastructure.oauth.base import OidcError, OidcIdentity, OidcProvider

FLOW_TTL = timedelta(minutes=10)
LOGIN_CODE_TTL = timedelta(minutes=2)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def s256(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


REAUTH_TTL = timedelta(minutes=5)  # a step-up proof (P3-M7.3)


class OAuthService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        auth: AuthService,
        providers: dict[str, OidcProvider],
        *,
        api_url: str,
        public_url: str,
    ) -> None:
        self._sm = sessionmaker
        self._auth = auth
        self._providers = providers
        self._api_url = api_url.rstrip("/")
        public = urlsplit(public_url)
        self._client_origin = (public.scheme, public.netloc)

    def provider(self, name: str) -> OidcProvider:
        p = self._providers.get(name)
        if p is None:
            raise NotFoundError("That sign-in provider is not configured.", code="unknown_provider")
        return p

    def callback_uri(self, provider: str) -> str:
        return f"{self._api_url}/api/v1/auth/oauth/{provider}/callback"

    async def authorize(
        self, provider: str, *, redirect_uri: str, code_challenge: str, method: str
    ) -> str:
        p = self.provider(provider)
        parts = urlsplit(redirect_uri)
        if (parts.scheme, parts.netloc) != self._client_origin or not parts.path.startswith("/"):
            # Only Mera's own web origin may receive login codes (no open redirect).
            raise InvalidInput(
                "redirect_uri must be on the Mera web origin.",
                code="invalid_redirect_uri",
                field="redirect_uri",
            )
        if method != "S256" or not 43 <= len(code_challenge) <= 128:
            raise InvalidInput(
                "PKCE S256 challenge required.",
                code="invalid_code_challenge",
                field="code_challenge",
            )
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        async with global_transaction(self._sm) as s:
            s.add(
                OAuthFlow(
                    id=uuid7(),
                    provider=p.name,
                    state_hash=_sha(state),
                    provider_code_verifier=verifier,
                    nonce=nonce,
                    client_code_challenge=code_challenge,
                    client_redirect_uri=redirect_uri,
                    expires_at=datetime.now(UTC) + FLOW_TTL,
                )
            )
        return p.authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=s256(verifier),
            redirect_uri=self.callback_uri(p.name),
        )

    async def callback(
        self,
        provider: str,
        *,
        code: str | None,
        state: str | None,
        error: str | None,
        meta: RequestMeta,
    ) -> str:
        """Returns where to send the browser: the client redirect with ``code`` or ``error``."""
        p = self.provider(provider)
        if not state:
            raise InvalidInput("Missing state.", code="invalid_state")
        async with global_transaction(self._sm) as s:
            flow = (
                await s.execute(
                    select(OAuthFlow)
                    .where(OAuthFlow.state_hash == _sha(state), OAuthFlow.provider == p.name)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if flow is None or flow.callback_at is not None or flow.expires_at <= datetime.now(UTC):
                # Unknown, replayed or stale state: no safe place to redirect to.
                raise InvalidInput(
                    "This sign-in link has expired. Start again.", code="invalid_state"
                )
            flow.callback_at = datetime.now(UTC)
            target = flow.client_redirect_uri
            verifier, nonce, flow_id = flow.provider_code_verifier, flow.nonce, flow.id

        if error or not code:
            return _redirect(target, error="provider_denied")
        try:
            identity = await p.exchange(
                code=code,
                code_verifier=verifier,
                redirect_uri=self.callback_uri(p.name),
                nonce=nonce,
            )
        except OidcError:
            return _redirect(target, error="provider_error")

        login_code = secrets.token_urlsafe(32)
        async with global_transaction(self._sm) as s:
            user_id = await self._resolve_user(s, p, identity, meta)
            if user_id is None:
                return _redirect(target, error="account_exists")
            flow_row = await s.get(OAuthFlow, flow_id, with_for_update=True)
            assert flow_row is not None
            flow_row.user_id = user_id
            flow_row.login_code_hash = _sha(login_code)
        return _redirect(target, code=login_code)

    async def exchange(self, *, code: str, code_verifier: str, meta: RequestMeta) -> TokenPair:
        invalid = AuthenticationFailed(
            "This sign-in has expired. Start again.", code="invalid_code"
        )
        async with global_transaction(self._sm) as s:
            flow = (
                await s.execute(
                    select(OAuthFlow)
                    .where(OAuthFlow.login_code_hash == _sha(code))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = datetime.now(UTC)
            if (
                flow is None
                or flow.user_id is None
                or flow.exchanged_at is not None
                or flow.callback_at is None
                or flow.callback_at + LOGIN_CODE_TTL <= now
                or not secrets.compare_digest(s256(code_verifier), flow.client_code_challenge)
            ):
                raise invalid
            flow.exchanged_at = now
            user_id, provider = flow.user_id, flow.provider
        return await self._auth.start_session(user_id, meta=meta, method=f"oauth:{provider}")

    async def step_up(
        self, user_id: uuid.UUID, *, code: str, code_verifier: str
    ) -> tuple[str, datetime]:
        """P3-M7.3: a person who signs in only with a provider just did so again, as
        themselves. Instead of a session, a single-use proof valid for a few minutes, which
        stands in for the password they do not have (``SecurityService._reauth``)."""
        invalid = AuthenticationFailed(
            "This confirmation has expired, or was for someone else. Start again.",
            code="invalid_code",
        )
        token = "mera_ra_" + secrets.token_urlsafe(32)
        async with global_transaction(self._sm) as s:
            flow = (
                await s.execute(
                    select(OAuthFlow)
                    .where(OAuthFlow.login_code_hash == _sha(code))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = datetime.now(UTC)
            if (
                flow is None
                or flow.user_id != user_id
                or flow.exchanged_at is not None
                or flow.callback_at is None
                or flow.callback_at + LOGIN_CODE_TTL <= now
                or not secrets.compare_digest(s256(code_verifier), flow.client_code_challenge)
            ):
                raise invalid
            flow.exchanged_at = now
            expires = now + REAUTH_TTL
            s.add(
                ReauthProof(
                    id=uuid7(), user_id=user_id, token_hash=hash_key(token), expires_at=expires
                )
            )
        return token, expires

    async def _resolve_user(
        self, s: AsyncSession, p: OidcProvider, identity: OidcIdentity, meta: RequestMeta
    ) -> uuid.UUID | None:
        known = (
            await s.execute(
                select(OAuthIdentity.user_id).where(
                    OAuthIdentity.provider == p.name,
                    OAuthIdentity.provider_subject == identity.subject,
                )
            )
        ).scalar_one_or_none()
        if known is not None:
            return known
        vouched = p.trusts_email and identity.email_verified is True
        existing = None
        if identity.email:
            existing = (
                await s.execute(select(User).where(User.email == identity.email).with_for_update())
            ).scalar_one_or_none()
        now = datetime.now(UTC)
        if existing is not None:
            if not vouched or existing.deleted_at is not None:
                return None
            user_id = existing.id
            action = "auth.oauth_linked"
            if existing.email_verified_at is None:
                # Nobody had proved this address; the provider now has. Whoever registered
                # it without proof is evicted: password removed, sessions and other sign-in
                # methods revoked (review H1, account pre-hijacking).
                existing.email_verified_at = now
                existing.password_hash = None
                existing.token_version += 1
                await s.execute(
                    update(RefreshToken)
                    .where(RefreshToken.user_id == existing.id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=now, revoked_reason="account_reclaimed")
                )
                await s.execute(delete(OAuthIdentity).where(OAuthIdentity.user_id == existing.id))
                action = "auth.unverified_account_reclaimed"
        else:
            if not identity.email:
                return None
            user = User(
                id=uuid7(),
                email=identity.email,
                display_name=(identity.name or identity.email.split("@")[0])[:120],
                password_hash=None,
                email_verified_at=now if vouched else None,
            )
            s.add(user)
            await s.flush()
            user_id = user.id
            action = "auth.registered"
        s.add(
            OAuthIdentity(
                id=uuid7(), user_id=user_id, provider=p.name, provider_subject=identity.subject
            )
        )
        await s.flush()
        await record(
            s,
            AuditEntry(
                action=action,
                actor_type="user",
                actor_id=user_id,
                surface=meta.surface,
                subject_type="user",
                subject_id=user_id,
                ip_address=meta.ip_address,
                request_id=meta.request_id,
                after={"provider": p.name},
            ),
        )
        return user_id


def _redirect(target: str, **params: str) -> str:
    sep = "&" if urlsplit(target).query else "?"
    return f"{target}{sep}{urlencode(params)}"
