"""Two-step sign-in, the approval PIN and second-factor grants (P2-M2.3 to 2.7).

- **TOTP** enrolment is two steps: ``setup`` returns a secret (kept encrypted as pending),
  ``confirm`` proves the authenticator works and only then turns MFA on, returning ten
  recovery codes once (stored hashed, each usable once).
- **The approval PIN** (6 to 8 digits, Argon2id) is the second factor that works from a
  phone chat, where an authenticator code would be awkward. Five wrong PINs lock it for
  15 minutes, recorded as a security event.
- **Grants**: ``grant`` checks one of those factors and returns a single-use token for one
  action in one workspace, valid for five minutes (``application.second_factor``).

Changing MFA or the PIN needs a factor the person already has (their current TOTP, or
their password when they have neither yet), so a stolen session alone cannot replace them.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import (
    AuthenticationFailed,
    ConflictError,
    Forbidden,
    InvalidInput,
    TooManyAttempts,
)
from mera.application.second_factor import ACTIONS, GRANT_TTL
from mera.application.services.principals import load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.ports.key_provider import KeyProvider
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.crypto import passwords, totp
from mera.infrastructure.crypto.api_keys import hash_key
from mera.infrastructure.db.models import (
    ReauthProof,
    RefreshToken,
    SecondFactorGrant,
    SecurityEvent,
    User,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import global_transaction, workspace_transaction

TOTP_PURPOSE: Final = "user.totp_secret"
RECOVERY_CODES: Final = 10
PIN_MAX_FAILURES: Final = 5
PIN_LOCK: Final = timedelta(minutes=15)
# Authenticator and recovery codes, wherever they are typed (migration 0022).
CODE_MAX_FAILURES: Final = 5
CODE_LOCK: Final = timedelta(minutes=15)
Method = Literal["totp", "pin", "recovery"]


def _recovery_code() -> str:
    raw = secrets.token_hex(5)
    return f"{raw[:5]}-{raw[5:]}"


def _valid_pin(pin: str) -> bool:
    return pin.isdigit() and 6 <= len(pin) <= 8 and len(set(pin)) > 1


@dataclass(frozen=True, slots=True)
class SecurityStatus:
    mfa_enabled: bool
    mfa_enabled_at: datetime | None
    mfa_pending: bool
    recovery_codes_left: int
    pin_set: bool
    pin_locked_until: datetime | None
    password_set: bool = True
    codes_locked_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class TotpSetup:
    secret: str
    otpauth_uri: str


class SecurityService:
    def __init__(self, sm: async_sessionmaker[AsyncSession], key_provider: KeyProvider) -> None:
        self._sm = sm
        self._kp = key_provider

    # ------------------------------------------------------------------ helpers

    def _seal(self, user_id: uuid.UUID, secret: str) -> str:
        return self._kp.encrypt(
            secret.encode(), workspace_id=None, purpose=TOTP_PURPOSE, record_id=user_id
        )

    def _open(self, user_id: uuid.UUID, ct: str) -> str:
        return self._kp.decrypt(
            ct, workspace_id=None, purpose=TOTP_PURPOSE, record_id=user_id
        ).decode()

    @staticmethod
    def _event(s: AsyncSession, user: User, kind: str, meta: RequestMeta, **detail: object) -> None:
        s.add(
            SecurityEvent(
                user_id=user.id, kind=kind, detail=dict(detail), ip_address=meta.ip_address
            )
        )

    @staticmethod
    def _audit(user: User, action: str, meta: RequestMeta) -> AuditEntry:
        return AuditEntry(
            action=action,
            actor_type="user",
            actor_id=user.id,
            actor_label=user.display_name,
            surface=meta.surface,
            ip_address=meta.ip_address,
            request_id=meta.request_id,
        )

    @staticmethod
    def _human(user: AuthenticatedUser) -> None:
        if user.is_api_key:
            raise Forbidden(
                "API keys cannot do this; sign in as a user.", code="user_credentials_required"
            )

    async def _locked_user(self, s: AsyncSession, user_id: uuid.UUID) -> User:
        u = await s.get(User, user_id, with_for_update=True)
        if u is None or u.deleted_at is not None:
            raise AuthenticationFailed("Not signed in.", code="invalid_token")
        return u

    def check_totp(self, u: User, code: str) -> bool:
        """True and the counter advanced when ``code`` is a fresh, valid TOTP for ``u``."""
        if u.mfa_enabled_at is None or not u.totp_secret_ct:
            return False
        step = totp.verify(
            self._open(u.id, u.totp_secret_ct), code, last_counter=u.totp_last_counter
        )
        if step is None:
            return False
        u.totp_last_counter = step
        return True

    @staticmethod
    def check_recovery(u: User, code: str) -> bool:
        h = hash_key(code.strip().lower())
        if h not in (u.recovery_code_hashes or []):
            return False
        u.recovery_code_hashes = [x for x in u.recovery_code_hashes if x != h]
        return True

    def check_code(
        self,
        s: AsyncSession,
        u: User,
        code: str,
        meta: RequestMeta,
        *,
        totp_ok: bool = True,
        recovery_ok: bool = True,
    ) -> bool:
        """An authenticator code and/or a recovery code, counted: five wrong in a row,
        wherever they are typed (sign-in, re-authentication, a second factor), and codes
        are refused for 15 minutes. The caller must let its transaction commit before it
        raises, or the count is rolled back with it."""
        now = datetime.now(UTC)
        if u.totp_locked_until is not None and u.totp_locked_until > now:
            raise TooManyAttempts(
                "Too many wrong codes. Try again in 15 minutes.",
                retry_after=int((u.totp_locked_until - now).total_seconds()) + 1,
            )
        if (totp_ok and self.check_totp(u, code)) or (recovery_ok and self.check_recovery(u, code)):
            u.totp_failed_count = 0
            return True
        u.totp_failed_count = (u.totp_failed_count or 0) + 1
        if u.totp_failed_count >= CODE_MAX_FAILURES:
            u.totp_locked_until = now + CODE_LOCK
            u.totp_failed_count = 0
            self._event(s, u, "mfa.locked", meta)
        return False

    async def check_pin(self, s: AsyncSession, u: User, pin: str, meta: RequestMeta) -> bool:
        now = datetime.now(UTC)
        if u.pin_locked_until is not None and u.pin_locked_until > now:
            raise TooManyAttempts(
                "Too many wrong PINs. Try again later.",
                retry_after=int((u.pin_locked_until - now).total_seconds()) + 1,
            )
        if not u.approval_pin_hash:
            return False
        if await passwords.verify_password_async(u.approval_pin_hash, pin.strip()):
            u.pin_failed_count = 0
            return True
        u.pin_failed_count = (u.pin_failed_count or 0) + 1
        if u.pin_failed_count >= PIN_MAX_FAILURES:
            u.pin_locked_until = now + PIN_LOCK
            u.pin_failed_count = 0
            self._event(s, u, "pin.locked", meta)
        else:
            self._event(s, u, "pin.failed", meta)
        return False

    @staticmethod
    async def _consume_reauth(s: AsyncSession, u: User, proof: str) -> bool:
        if not proof.startswith("mera_ra_"):
            return False
        now = datetime.now(UTC)
        row = (
            await s.execute(
                select(ReauthProof)
                .where(ReauthProof.token_hash == hash_key(proof), ReauthProof.user_id == u.id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.used_at is not None or row.expires_at <= now:
            return False
        row.used_at = now
        return True

    async def _reauth(
        self,
        s: AsyncSession,
        u: User,
        proof: str,
        meta: RequestMeta,
    ) -> AuthenticationFailed | None:
        """A factor the person already has: their TOTP (or a recovery code) if MFA is on,
        else their password. The failure is *returned*: the caller commits first and then
        raises it, so the event and the lockout count survive the refusal."""
        if u.mfa_enabled_at is not None:
            if not self.check_code(s, u, proof, meta):
                self._event(s, u, "reauth.failed", meta)
                return AuthenticationFailed(
                    "That code is not right. Use your authenticator app or a recovery code.",
                    code="invalid_mfa_code",
                )
            return None
        if u.password_hash is None:
            # An account that signs in only with a provider has no password: it confirms
            # by signing in with the provider again, which gives a short-lived proof
            # (P3-M7.3, ``OAuthService.step_up``).
            if await self._consume_reauth(s, u, proof):
                return None
            self._event(s, u, "reauth.failed", meta, method="provider")
            return AuthenticationFailed(
                "Confirm it's you by signing in with your provider again.",
                code="step_up_required",
            )
        if not await passwords.verify_password_async(u.password_hash, proof):
            self._event(s, u, "reauth.failed", meta)
            return AuthenticationFailed("That password is not right.", code="invalid_password")
        return None

    # ------------------------------------------------------------------ status

    async def status(self, user: AuthenticatedUser) -> SecurityStatus:
        async with global_transaction(self._sm) as s:
            u = await s.get(User, user.user_id)
            assert u is not None
            return SecurityStatus(
                mfa_enabled=u.mfa_enabled_at is not None,
                mfa_enabled_at=u.mfa_enabled_at,
                mfa_pending=u.totp_pending_ct is not None,
                recovery_codes_left=len(u.recovery_code_hashes or []),
                pin_set=u.approval_pin_hash is not None,
                pin_locked_until=u.pin_locked_until,
                password_set=u.password_hash is not None,
                codes_locked_until=u.totp_locked_until,
            )

    # ------------------------------------------------------------------ TOTP

    async def totp_setup(self, user: AuthenticatedUser, proof: str, meta: RequestMeta) -> TotpSetup:
        """Start enrolment. Needs the password: whoever enrols an authenticator controls
        the next sign-in, so a stolen session must not be enough (release review #7)."""
        self._human(user)
        secret = totp.new_secret()
        async with global_transaction(self._sm) as s:
            u = await self._locked_user(s, user.user_id)
            if u.mfa_enabled_at is not None:
                raise ConflictError(
                    "Two-step sign-in is already on. Turn it off first to change the app.",
                    code="mfa_already_enabled",
                )
            failed = await self._reauth(s, u, proof, meta)
            if failed is None:
                u.totp_pending_ct = self._seal(u.id, secret)
        if failed is not None:
            raise failed
        return TotpSetup(secret, totp.provisioning_uri(secret, account=user.email))

    async def totp_confirm(
        self, user: AuthenticatedUser, code: str, meta: RequestMeta
    ) -> list[str]:
        """Turn MFA on; returns the recovery codes, shown once."""
        self._human(user)
        async with global_transaction(self._sm) as s:
            u = await self._locked_user(s, user.user_id)
            if u.totp_pending_ct is None:
                raise ConflictError("Start the setup first.", code="mfa_not_started")
            secret = self._open(u.id, u.totp_pending_ct)
            step = totp.verify(secret, code)
            if step is None:
                self._event(s, u, "mfa.confirm_failed", meta)
                raise InvalidInput(
                    "That code is not right. Check the time on your phone and try the next one.",
                    code="invalid_mfa_code",
                    field="code",
                )
            codes = [_recovery_code() for _ in range(RECOVERY_CODES)]
            u.totp_secret_ct = self._seal(u.id, secret)
            u.totp_pending_ct = None
            u.totp_last_counter = step
            u.mfa_enabled_at = datetime.now(UTC)
            u.recovery_code_hashes = [hash_key(c) for c in codes]
            # Existing sessions were signed in without the second step: end them, access
            # tokens (token_version) and refresh tokens alike, this one included.
            u.token_version = (u.token_version or 0) + 1
            await s.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC), revoked_reason="mfa_enabled")
            )
            self._event(s, u, "mfa.enabled", meta)
            await record(s, self._audit(u, "auth.mfa_enabled", meta))
        return codes

    async def totp_disable(self, user: AuthenticatedUser, proof: str, meta: RequestMeta) -> None:
        self._human(user)
        async with global_transaction(self._sm) as s:
            u = await self._locked_user(s, user.user_id)
            if u.mfa_enabled_at is None:
                raise ConflictError("Two-step sign-in is already off.", code="mfa_not_enabled")
            failed = await self._reauth(s, u, proof, meta)
            if failed is None:
                u.totp_secret_ct = None
                u.totp_pending_ct = None
                u.mfa_enabled_at = None
                u.totp_last_counter = None
                u.recovery_code_hashes = []
                self._event(s, u, "mfa.disabled", meta)
                await record(s, self._audit(u, "auth.mfa_disabled", meta))
        if failed is not None:
            raise failed

    async def new_recovery_codes(
        self, user: AuthenticatedUser, proof: str, meta: RequestMeta
    ) -> list[str]:
        self._human(user)
        async with global_transaction(self._sm) as s:
            u = await self._locked_user(s, user.user_id)
            if u.mfa_enabled_at is None:
                raise ConflictError("Turn on two-step sign-in first.", code="mfa_not_enabled")
            failed = await self._reauth(s, u, proof, meta)
            codes = [_recovery_code() for _ in range(RECOVERY_CODES)]
            if failed is None:
                u.recovery_code_hashes = [hash_key(c) for c in codes]
                self._event(s, u, "mfa.recovery_regenerated", meta)
        if failed is not None:
            raise failed
        return codes

    # ------------------------------------------------------------------ PIN

    async def set_pin(
        self, user: AuthenticatedUser, pin: str, proof: str, meta: RequestMeta
    ) -> None:
        self._human(user)
        pin = pin.strip()
        if not _valid_pin(pin):
            raise InvalidInput(
                "Use 6 to 8 digits, not all the same.", code="invalid_pin", field="pin"
            )
        hashed = await passwords.hash_password_async(pin)
        async with global_transaction(self._sm) as s:
            u = await self._locked_user(s, user.user_id)
            failed = await self._reauth(s, u, proof, meta)
            if failed is None:
                first = u.approval_pin_hash is None
                u.approval_pin_hash = hashed
                u.pin_failed_count = 0
                u.pin_locked_until = None
                self._event(s, u, "pin.set" if first else "pin.changed", meta)
                await record(s, self._audit(u, "auth.pin_set", meta))
        if failed is not None:
            raise failed

    async def clear_pin(self, user: AuthenticatedUser, proof: str, meta: RequestMeta) -> None:
        self._human(user)
        async with global_transaction(self._sm) as s:
            u = await self._locked_user(s, user.user_id)
            failed = await self._reauth(s, u, proof, meta)
            if failed is None:
                u.approval_pin_hash = None
                self._event(s, u, "pin.cleared", meta)
        if failed is not None:
            raise failed

    # ------------------------------------------------------------------ grants

    async def grant(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        action: str,
        method: Method,
        value: str,
        meta: RequestMeta,
    ) -> tuple[str, datetime]:
        """Check a second factor and return a single-use token for ``action``."""
        self._human(user)
        if action not in ACTIONS:
            raise InvalidInput("Unknown action.", code="invalid_action", field="action")
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            # Membership only: whether the action itself is allowed is checked by the action.
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
        async with global_transaction(self._sm) as s:
            u = await self._locked_user(s, user.user_id)
            ok = False
            if method == "totp":
                ok = self.check_code(s, u, value, meta, recovery_ok=False)
            elif method == "recovery":
                ok = self.check_code(s, u, value, meta, totp_ok=False)
            elif method == "pin":
                ok = await self.check_pin(s, u, value, meta)
            if not ok:
                if method != "pin":
                    self._event(s, u, "second_factor.failed", meta, action=action, method=method)
                await s.flush()
        if not ok:
            raise AuthenticationFailed(
                "That PIN is not right." if method == "pin" else "That code is not right.",
                code="invalid_second_factor",
            )
        token = "mera_sf_" + secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + GRANT_TTL
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            s.add(
                SecondFactorGrant(
                    id=uuid7(),
                    user_id=user.user_id,
                    workspace_id=workspace_id,
                    action=action,
                    method=method,
                    token_hash=hash_key(token),
                    expires_at=expires,
                )
            )
            s.add(
                SecurityEvent(
                    user_id=user.user_id,
                    workspace_id=workspace_id,
                    kind="second_factor.granted",
                    detail={"action": action, "method": method},
                    ip_address=meta.ip_address,
                )
            )
        return token, expires

    async def pin_matches(self, user_id: uuid.UUID, pin: str, meta: RequestMeta) -> bool:
        """For the bots' ``/pin``: check (and count) a PIN without issuing a grant."""
        async with global_transaction(self._sm) as s:
            u = await self._locked_user(s, user_id)
            ok = await self.check_pin(s, u, pin, meta)
            if ok:
                self._event(s, u, "second_factor.granted", meta, method="pin", via="bot")
        return ok
