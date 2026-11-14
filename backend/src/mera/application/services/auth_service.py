"""Registration, login, refresh rotation and logout (M2.2, M2.3; docs/04 §2.1, docs/09 §5.1).

Rules that came out of the Sprint 2 security review:
- Argon2 runs in a worker thread and never while a DB transaction is open, so slow hashing
  can neither stall the event loop nor pin pooled connections.
- Refresh reuse outside a short grace window is treated as theft: the token family is
  revoked *and* the user's ``token_version`` is bumped, killing live access tokens too.
- A session ends ``REFRESH_TTL_ABSOLUTE`` after login however often it is refreshed.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.context import ApiKeyGrant, AuthenticatedUser, RequestMeta
from mera.application.emails import verification_email
from mera.application.errors import (
    AuthenticationFailed,
    ConflictError,
    InvalidInput,
    NotFoundError,
    ServiceUnavailableError,
    TooManyAttempts,
)
from mera.application.services.security_service import SecurityService
from mera.domain.context import WorkspaceContext
from mera.domain.ports.notifier import Notifier, NotifierError
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.crypto import api_keys, passwords, tokens
from mera.infrastructure.db.errors import constraint_name
from mera.infrastructure.db.models import (
    ApiKey,
    EmailVerification,
    MfaChallenge,
    RefreshToken,
    SecurityEvent,
    User,
    WorkspaceMember,
)
from mera.infrastructure.db.rate_limit import Limit, refund, take
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import (
    global_transaction,
    secret_lookup_transaction,
    user_transaction,
    workspace_transaction,
)

# Two tabs or a retried request may present the same token moments apart; that is not theft.
REFRESH_GRACE = timedelta(seconds=20)
_SESSION_EXPIRED = "Session expired. Sign in again."
# Sign-in attempts, refilling over 15 minutes; successes are refunded.
LOGIN_PAIR = Limit(capacity=5, per_seconds=900)  # per (IP, account)
LOGIN_ACCOUNT = Limit(capacity=20, per_seconds=900)  # per account, from anywhere
EMAIL_VERIFICATION_TTL = timedelta(hours=24)
MFA_CHALLENGE_TTL = timedelta(minutes=5)
MFA_MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"  # noqa: S105 (OAuth token type, not a secret)


@dataclass(frozen=True, slots=True)
class Membership:
    workspace_id: uuid.UUID
    role: str


class AuthService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        secret: str,
        breached: passwords.BreachedPasswords | None = None,
        rate_limit: bool = True,
        sandbox: bool = False,
        public_url: str = "http://localhost:3000",
        notifier: Notifier | None = None,
        security: SecurityService | None = None,
        mfa_enforced: bool = False,
    ) -> None:
        self.mcp: Any | None = None  # McpAuthService, set at startup (P2-M8)
        self._sm = sessionmaker
        self._notifier = notifier
        self._security = security
        self._mfa_enforced = mfa_enforced
        self._rate_limit = rate_limit
        self._sandbox = sandbox
        self._public_url = public_url.rstrip("/")
        self._secret = secret
        self._breached = breached

    # ---- registration ----

    async def register(
        self, *, email: str, password: str, display_name: str, meta: RequestMeta
    ) -> User:
        email = email.strip()
        try:
            passwords.validate_password(password, email=email, breached=self._breached)
        except passwords.WeakPassword as exc:
            await self._audit_global("auth.register_rejected", meta, email, {"code": exc.code})
            raise InvalidInput(exc.message, code=exc.code, field="password") from exc

        user = User(
            id=uuid7(),
            email=email,
            display_name=display_name.strip(),
            password_hash=await passwords.hash_password_async(password),
        )
        try:
            async with global_transaction(self._sm) as s:
                s.add(user)
                await s.flush()
                await record(s, self._entry("auth.registered", meta, user))
        except IntegrityError as exc:
            if constraint_name(exc) != "uq_user_email":
                raise
            await self._audit_global("auth.register_rejected", meta, email, {"code": "email_taken"})
            # Registration reveals that an email exists; that is inherent to sign-up.
            raise ConflictError(
                "An account with this email already exists.", code="email_taken", field="email"
            ) from exc
        return user

    # ---- login ----

    async def login(
        self, *, email: str, password: str, meta: RequestMeta, device_label: str | None = None
    ) -> TokenPair:
        email = email.strip()
        # Login throttle (docs/09 §5.1). A token is taken *before* the password check and
        # refunded on success, so a burst of concurrent guesses cannot all slip past a peek.
        # Two buckets: per (IP, account) is strict; per account is looser, so one attacker
        # cannot cheaply lock a victim out from everywhere.
        buckets = [
            (f"login:{meta.ip_address or 'unknown'}:{email.lower()}", LOGIN_PAIR),
            (f"login:{email.lower()}", LOGIN_ACCOUNT),
        ]
        if self._rate_limit:
            for key, limit in buckets:
                decision = await take(self._sm, key, limit)
                if not decision.allowed:
                    raise TooManyAttempts(
                        "Too many failed sign-in attempts. Try again later.",
                        retry_after=decision.retry_after,
                    )
        async with global_transaction(self._sm) as s:
            user = (
                await s.execute(select(User).where(User.email == email, User.deleted_at.is_(None)))
            ).scalar_one_or_none()
        # Verified with no transaction open (one Argon2 run either way: no timing oracle).
        ok = await passwords.verify_password_async(user.password_hash if user else None, password)
        if not ok or user is None:
            async with global_transaction(self._sm) as s:
                await record(
                    s,
                    AuditEntry(
                        action="auth.login_failed",
                        actor_type="user",
                        actor_id=user.id if user else None,
                        actor_label=email[:254],
                        surface=meta.surface,
                        ip_address=meta.ip_address,
                        request_id=meta.request_id,
                    ),
                )
            raise AuthenticationFailed("Email or password is incorrect.")
        if self._rate_limit:
            for key, limit in buckets:
                await refund(self._sm, key, limit)

        new_hash = None
        if user.password_hash and passwords.needs_rehash(user.password_hash):
            new_hash = await passwords.hash_password_async(password)
        if user.mfa_enabled_at is not None:
            if new_hash:
                async with global_transaction(self._sm) as s:
                    await s.execute(
                        update(User).where(User.id == user.id).values(password_hash=new_hash)
                    )
            raise await self._challenge(user, "password", meta, device_label)
        async with global_transaction(self._sm) as s:
            if new_hash:
                await s.execute(
                    update(User).where(User.id == user.id).values(password_hash=new_hash)
                )
            now = datetime.now(UTC)
            refresh = await self._new_refresh(
                s,
                user.id,
                family_id=uuid7(),
                family_expires_at=now + tokens.REFRESH_TTL_ABSOLUTE,
                device=device_label,
            )
            await record(s, self._entry("auth.login_succeeded", meta, user))
        return await self._pair(user, refresh)

    async def start_session(
        self,
        user_id: uuid.UUID,
        *,
        meta: RequestMeta,
        method: str,
        device_label: str | None = None,
    ) -> TokenPair:
        """Issue a token pair for a user already authenticated by another means (OAuth)."""
        async with global_transaction(self._sm) as s:
            user = (
                await s.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
            ).scalar_one_or_none()
            if user is None:
                raise AuthenticationFailed(_SESSION_EXPIRED, code="invalid_code")
        if user.mfa_enabled_at is not None and not method.startswith("mfa"):
            raise await self._challenge(user, method, meta, device_label)
        async with global_transaction(self._sm) as s:
            refresh = await self._new_refresh(
                s,
                user.id,
                family_id=uuid7(),
                family_expires_at=datetime.now(UTC) + tokens.REFRESH_TTL_ABSOLUTE,
                device=device_label,
            )
            await record(
                s,
                replace(self._entry("auth.login_succeeded", meta, user), after={"method": method}),
            )
        return await self._pair(user, refresh)

    # ---- two-step sign-in (P2-M2.3) ----

    async def _challenge(
        self, user: User, method: str, meta: RequestMeta, device_label: str | None
    ) -> MfaRequired:
        token = "mera_mfa_" + secrets.token_urlsafe(32)
        async with global_transaction(self._sm) as s:
            s.add(
                MfaChallenge(
                    id=uuid7(),
                    user_id=user.id,
                    token_hash=api_keys.hash_key(token),
                    method=method,
                    device_label=device_label,
                    expires_at=datetime.now(UTC) + MFA_CHALLENGE_TTL,
                )
            )
            await record(s, self._entry("auth.mfa_challenged", meta, user))
        return MfaRequired(token)

    async def verify_mfa(self, *, mfa_token: str, code: str, meta: RequestMeta) -> TokenPair:
        """The second step: an authenticator code or a recovery code for a challenge from
        ``login`` (or an OAuth sign-in). Five wrong codes burn the challenge."""
        invalid = AuthenticationFailed(
            "That sign-in has expired. Start again.", code="invalid_mfa_challenge"
        )
        if self._security is None:
            raise invalid
        async with global_transaction(self._sm) as s:
            ch = (
                await s.execute(
                    select(MfaChallenge)
                    .where(MfaChallenge.token_hash == api_keys.hash_key(mfa_token))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = datetime.now(UTC)
            if ch is None or ch.used_at is not None or ch.expires_at <= now:
                raise invalid
            if ch.attempts >= MFA_MAX_ATTEMPTS:
                ch.used_at = now
                raise invalid
            ch.attempts += 1
            user = await s.get(User, ch.user_id, with_for_update=True)
            if user is None or user.deleted_at is not None:
                raise invalid
            # Counted per person, not per challenge: a password alone can open challenges
            # at will, so five per challenge would be no limit at all (release review #8).
            ok = self._security.check_code(s, user, code, meta)
            if not ok:
                s.add(
                    SecurityEvent(
                        user_id=user.id,
                        kind="mfa.failed",
                        detail={"attempt": ch.attempts},
                        ip_address=meta.ip_address,
                    )
                )
                await s.flush()
                failed = True
            else:
                ch.used_at = now
                failed = False
                method, device = ch.method, ch.device_label
                user_id = user.id
        if failed:
            raise AuthenticationFailed(
                "That code is not right. Use your authenticator app or a recovery code.",
                code="invalid_mfa_code",
            )
        return await self.start_session(
            user_id, meta=meta, method=f"mfa:{method}", device_label=device
        )

    # ---- email verification (Sprint 3 review H1) ----

    async def request_email_verification(
        self, user: AuthenticatedUser, meta: RequestMeta
    ) -> str | None:
        """Email a single-use link proving control of the user's current address. In
        sandbox mode the link is also returned to the caller, since nobody reads the
        captured mail; elsewhere it travels only by email. Without a configured Notifier
        outside sandbox the request fails honestly."""
        if user.email_verified:
            raise ConflictError("Your email address is already verified.", code="already_verified")
        if self._notifier is None and not self._sandbox:
            raise ServiceUnavailableError(
                "Email delivery is not configured on this server.",
                code="email_delivery_unavailable",
            )
        token = api_keys.new_invitation_token().replace("mera_inv_", "mera_ev_", 1)
        async with global_transaction(self._sm) as s:
            s.add(
                EmailVerification(
                    id=uuid7(),
                    user_id=user.user_id,
                    email=user.email,
                    token_hash=api_keys.hash_key(token),
                    expires_at=datetime.now(UTC) + EMAIL_VERIFICATION_TTL,
                )
            )
        url = f"{self._public_url}/verify-email/{token}"
        if self._notifier is not None:
            hours = int(EMAIL_VERIFICATION_TTL.total_seconds() // 3600)
            try:
                await self._notifier.send(verification_email(user.email, url, hours=hours))
            except NotifierError as exc:
                # The unused link simply expires; asking again issues a fresh one.
                raise ServiceUnavailableError(
                    "We couldn't send the email just now. Try again in a few minutes.",
                    code="email_delivery_failed",
                ) from exc
        return url if self._sandbox else None

    async def verify_email(self, token: str, meta: RequestMeta) -> None:
        invalid = NotFoundError(
            "This verification link is invalid or has expired.", code="invalid_verification"
        )
        async with global_transaction(self._sm) as s:
            row = (
                await s.execute(
                    select(EmailVerification)
                    .where(EmailVerification.token_hash == api_keys.hash_key(token))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = datetime.now(UTC)
            if row is None or row.used_at is not None or row.expires_at <= now:
                raise invalid
            user = await s.get(User, row.user_id, with_for_update=True)
            # The link proves the address it was sent for; if the account's email has
            # changed since, it proves nothing.
            if (
                user is None
                or user.deleted_at is not None
                or user.email.lower() != row.email.lower()
            ):
                raise invalid
            row.used_at = now
            if user.email_verified_at is None:
                user.email_verified_at = now
                await record(s, self._entry("auth.email_verified", meta, user))

    # ---- refresh rotation with reuse detection ----

    async def refresh(self, *, refresh_token: str, meta: RequestMeta) -> TokenPair:
        token_hash = tokens.hash_refresh_token(refresh_token)
        outcome = "invalid"
        user: User | None = None
        new = ""
        async with global_transaction(self._sm) as s:
            row = (
                await s.execute(
                    select(RefreshToken)
                    .where(RefreshToken.token_hash == token_hash)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = datetime.now(UTC)
            if row is None:
                outcome = "invalid"
            elif row.revoked_at is not None:
                if row.revoked_reason == "rotated" and now - row.revoked_at < REFRESH_GRACE:
                    outcome = "in_progress"  # a concurrent refresh already won; not theft
                else:
                    # A rotated or revoked token came back: assume theft. Kill the family and
                    # every live access token (token_version), then audit.
                    await self._revoke_family(s, row.family_id, reason="reuse_detected", now=now)
                    await s.execute(
                        update(User)
                        .where(User.id == row.user_id)
                        .values(token_version=User.token_version + 1)
                    )
                    await record(
                        s,
                        AuditEntry(
                            action="auth.refresh_reuse_detected",
                            actor_type="user",
                            actor_id=row.user_id,
                            surface=meta.surface,
                            subject_type="refresh_token_family",
                            subject_id=row.family_id,
                            ip_address=meta.ip_address,
                            request_id=meta.request_id,
                        ),
                    )
                    # The person is told (P3-M7.1): the alert scanner reads this event.
                    s.add(
                        SecurityEvent(
                            user_id=row.user_id,
                            kind="refresh.reuse_detected",
                            detail={"family": str(row.family_id)},
                            ip_address=meta.ip_address,
                        )
                    )
                    outcome = "reuse"
            elif row.expires_at <= now or row.family_expires_at <= now:
                outcome = "expired"
            else:
                user = (
                    await s.execute(
                        select(User).where(User.id == row.user_id, User.deleted_at.is_(None))
                    )
                ).scalar_one_or_none()
                if user is not None:
                    row.revoked_at = now
                    row.revoked_reason = "rotated"
                    new = await self._new_refresh(
                        s,
                        user.id,
                        family_id=row.family_id,
                        family_expires_at=row.family_expires_at,
                        device=row.device_label,
                        rotated_from=row.id,
                    )
                    outcome = "ok"
        if outcome == "in_progress":
            raise ConflictError(
                "This session was just refreshed. Use the newest token.", code="refresh_in_progress"
            )
        if outcome in ("invalid", "expired"):
            await self._audit_global("auth.refresh_failed", meta, None, {"reason": outcome})
        if outcome != "ok" or user is None:
            raise AuthenticationFailed(_SESSION_EXPIRED, code="invalid_refresh_token")
        return await self._pair(user, new)

    async def logout(self, *, refresh_token: str, meta: RequestMeta) -> None:
        token_hash = tokens.hash_refresh_token(refresh_token)
        async with global_transaction(self._sm) as s:
            row = (
                await s.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
            ).scalar_one_or_none()
            if row is None or row.revoked_at is not None:
                return  # idempotent
            await self._revoke_family(s, row.family_id, reason="logout", now=datetime.now(UTC))
            await record(
                s,
                AuditEntry(
                    action="auth.logout",
                    actor_type="user",
                    actor_id=row.user_id,
                    surface=meta.surface,
                    ip_address=meta.ip_address,
                    request_id=meta.request_id,
                ),
            )

    # ---- access token verification ----

    async def authenticate(self, access_token: str, *, via_mcp: bool = False) -> AuthenticatedUser:
        if access_token.startswith(api_keys.KEY_PREFIX):
            return await self._authenticate_api_key(access_token)
        if access_token.startswith("mera_mcp_"):
            # An MCP token's audience is the MCP server (RFC 8707): the REST API takes it
            # only from the mcp service, never straight from a client (P2-M8.2).
            if not via_mcp or self.mcp is None:
                raise AuthenticationFailed("Not signed in.", code="invalid_token")
            via: AuthenticatedUser = await self.mcp.authenticate(access_token)
            return via
        try:
            claims = tokens.decode_access_token(access_token, secret=self._secret)
        except tokens.InvalidToken as exc:
            raise AuthenticationFailed("Not signed in.", code="invalid_token") from exc
        async with global_transaction(self._sm) as s:
            user = (
                await s.execute(
                    select(User).where(User.id == claims.user_id, User.deleted_at.is_(None))
                )
            ).scalar_one_or_none()
        if user is None or user.token_version != claims.token_version:
            raise AuthenticationFailed("Not signed in.", code="invalid_token")
        return AuthenticatedUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            email_verified=user.email_verified_at is not None,
            mfa_missing=self._mfa_enforced and user.mfa_enabled_at is None,
        )

    async def _authenticate_api_key(self, raw: str) -> AuthenticatedUser:
        key_hash = api_keys.hash_key(raw)
        async with secret_lookup_transaction(self._sm, "mera.api_key_hash", key_hash) as s:
            key = (
                await s.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
            ).scalar_one_or_none()
            creator = None
            if key is not None:
                creator = (
                    await s.execute(
                        select(User).where(User.id == key.created_by, User.deleted_at.is_(None))
                    )
                ).scalar_one_or_none()
        now = datetime.now(UTC)
        if (
            key is None
            or creator is None
            or key.revoked_at is not None
            or (key.expires_at is not None and key.expires_at <= now)
        ):
            raise AuthenticationFailed("Not signed in.", code="invalid_token")
        if key.last_used_at is None or now - key.last_used_at > api_keys.LAST_USED_RESOLUTION:
            async with workspace_transaction(self._sm, WorkspaceContext(key.workspace_id)) as s:
                await s.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
        return AuthenticatedUser(
            user_id=creator.id,
            email=creator.email,
            display_name=creator.display_name,
            email_verified=creator.email_verified_at is not None,
            mfa_missing=self._mfa_enforced and creator.mfa_enabled_at is None,
            api_key=ApiKeyGrant(
                api_key_id=key.id, workspace_id=key.workspace_id, scopes=frozenset(key.scopes)
            ),
        )

    async def memberships(self, user_id: uuid.UUID) -> list[Membership]:
        async with user_transaction(self._sm, user_id) as s:
            rows = (
                await s.execute(
                    select(WorkspaceMember.workspace_id, WorkspaceMember.role).where(
                        WorkspaceMember.user_id == user_id
                    )
                )
            ).all()
        return [Membership(workspace_id=w, role=r) for w, r in rows]

    # ---- helpers ----

    async def _pair(self, user: User, refresh: str) -> TokenPair:
        memberships = await self.memberships(user.id)
        access, expires_in = tokens.issue_access_token(
            secret=self._secret,
            user_id=user.id,
            token_version=user.token_version,
            memberships=[
                {"workspace_id": str(m.workspace_id), "role": m.role} for m in memberships
            ],
        )
        return TokenPair(access_token=access, refresh_token=refresh, expires_in=expires_in)

    @staticmethod
    async def _new_refresh(
        s: AsyncSession,
        user_id: uuid.UUID,
        *,
        family_id: uuid.UUID,
        family_expires_at: datetime,
        device: str | None,
        rotated_from: uuid.UUID | None = None,
    ) -> str:
        raw = tokens.new_refresh_token()
        s.add(
            RefreshToken(
                id=uuid7(),
                user_id=user_id,
                token_hash=tokens.hash_refresh_token(raw),
                family_id=family_id,
                rotated_from=rotated_from,
                device_label=device,
                expires_at=min(datetime.now(UTC) + tokens.REFRESH_TTL, family_expires_at),
                family_expires_at=family_expires_at,
            )
        )
        await s.flush()
        return raw

    @staticmethod
    async def _revoke_family(
        s: AsyncSession, family_id: uuid.UUID, *, reason: str, now: datetime
    ) -> None:
        await s.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
        )

    async def _audit_global(
        self, action: str, meta: RequestMeta, label: str | None, after: dict[str, str]
    ) -> None:
        """A workspace-less security row, in its own committed transaction."""
        async with global_transaction(self._sm) as s:
            await record(
                s,
                AuditEntry(
                    action=action,
                    actor_type="user",
                    actor_label=label[:254] if label else None,
                    surface=meta.surface,
                    ip_address=meta.ip_address,
                    request_id=meta.request_id,
                    after=after,
                ),
            )

    @staticmethod
    def _entry(action: str, meta: RequestMeta, user: User) -> AuditEntry:
        return AuditEntry(
            action=action,
            actor_type="user",
            actor_id=user.id,
            actor_label=user.display_name,
            surface=meta.surface,
            subject_type="user",
            subject_id=user.id,
            ip_address=meta.ip_address,
            request_id=meta.request_id,
        )


class MfaRequired(AuthenticationFailed):
    """The first factor passed; a TOTP or recovery code is owed for ``mfa_token``. The API
    answers 401 ``mfa_required`` with the token, never with session tokens."""

    code = "mfa_required"

    def __init__(self, mfa_token: str) -> None:
        super().__init__("Enter the code from your authenticator app.", code="mfa_required")
        self.mfa_token = mfa_token
