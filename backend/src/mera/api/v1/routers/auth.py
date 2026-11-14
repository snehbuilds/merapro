"""``/api/v1/auth`` (docs/04 §2.1). OAuth, device flow and API keys arrive in Sprint 3."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from mera.api.deps import Auth, HumanUser, Meta, OAuth, Security
from mera.application.services.auth_service import TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)
    device_label: str | None = Field(default=None, max_length=120)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=256)


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"  # noqa: S105 (OAuth token type, not a secret)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str


class MembershipOut(BaseModel):
    workspace_id: uuid.UUID
    role: str


class MeOut(UserOut):
    email_verified: bool
    workspace_memberships: list[MembershipOut]


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def register(body: RegisterIn, auth: Auth, meta: Meta) -> UserOut:
    user = await auth.register(
        email=str(body.email), password=body.password, display_name=body.display_name, meta=meta
    )
    return UserOut(id=user.id, email=user.email, display_name=user.display_name)


@router.post("/login", response_model=TokenPairOut)
async def login(body: LoginIn, auth: Auth, meta: Meta) -> TokenPairOut:
    pair = await auth.login(
        email=str(body.email), password=body.password, meta=meta, device_label=body.device_label
    )
    return _out(pair)


@router.post("/refresh", response_model=TokenPairOut)
async def refresh(body: RefreshIn, auth: Auth, meta: Meta) -> TokenPairOut:
    return _out(await auth.refresh(refresh_token=body.refresh_token, meta=meta))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, auth: Auth, meta: Meta) -> None:
    await auth.logout(refresh_token=body.refresh_token, meta=meta)


@router.get("/me", response_model=MeOut)
async def me(user: HumanUser, auth: Auth) -> MeOut:
    memberships = await auth.memberships(user.user_id)
    return MeOut(
        id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        email_verified=user.email_verified,
        workspace_memberships=[
            MembershipOut(workspace_id=m.workspace_id, role=m.role) for m in memberships
        ],
    )


def _out(pair: TokenPair) -> TokenPairOut:
    return TokenPairOut(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        token_type=pair.token_type,
    )


# ---- email verification ----


class VerificationRequestOut(BaseModel):
    sent: bool
    verify_url: str | None = Field(
        default=None, description="Sandbox only: the link that was emailed, for local development."
    )


class VerifyEmailIn(BaseModel):
    token: str = Field(min_length=10, max_length=128)


@router.post("/email/verification", response_model=VerificationRequestOut)
async def request_email_verification(
    user: HumanUser, auth: Auth, meta: Meta
) -> VerificationRequestOut:
    url = await auth.request_email_verification(user, meta)
    return VerificationRequestOut(sent=True, verify_url=url)


@router.post("/email/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(body: VerifyEmailIn, auth: Auth, meta: Meta) -> None:
    await auth.verify_email(body.token, meta)


# ---- OAuth (docs/04 §2.2) ----

Provider = Literal["google", "microsoft"]


class OAuthExchangeIn(BaseModel):
    code: str = Field(min_length=10, max_length=256)
    code_verifier: str = Field(min_length=43, max_length=128)


@router.get("/oauth/{provider}/authorize", status_code=status.HTTP_302_FOUND)
async def oauth_authorize(
    provider: Provider,
    oauth: OAuth,
    redirect_uri: Annotated[str, Query(max_length=2048)],
    code_challenge: Annotated[str, Query(min_length=43, max_length=128)],
    code_challenge_method: Annotated[str, Query()] = "S256",
) -> RedirectResponse:
    url = await oauth.authorize(
        provider,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        method=code_challenge_method,
    )
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/oauth/{provider}/callback", status_code=status.HTTP_302_FOUND)
async def oauth_callback(
    provider: Provider,
    oauth: OAuth,
    meta: Meta,
    code: Annotated[str | None, Query(max_length=4096)] = None,
    state: Annotated[str | None, Query(max_length=256)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
) -> RedirectResponse:
    url = await oauth.callback(provider, code=code, state=state, error=error, meta=meta)
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.post("/oauth/exchange", response_model=TokenPairOut)
async def oauth_exchange(body: OAuthExchangeIn, oauth: OAuth, meta: Meta) -> TokenPairOut:
    return _out(await oauth.exchange(code=body.code, code_verifier=body.code_verifier, meta=meta))


# ---- two-step sign-in, the approval PIN (P2-M2) ----


class MfaVerifyIn(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=128, description="From the mfa_required error")
    code: str = Field(min_length=6, max_length=20, description="Authenticator or recovery code")


class SecurityOut(BaseModel):
    mfa_enabled: bool
    mfa_enabled_at: datetime | None
    mfa_pending: bool
    recovery_codes_left: int
    pin_set: bool
    pin_locked_until: datetime | None
    password_set: bool = Field(
        default=True, description="False for an account that signs in only with a provider"
    )
    codes_locked_until: datetime | None = Field(
        default=None, description="Authenticator codes are refused until then (5 wrong codes)"
    )
    mfa_required: bool = Field(description="This deployment requires MFA for Admins and Owners")


class TotpSetupOut(BaseModel):
    secret: str = Field(description="Shown once; add it to an authenticator app")
    otpauth_uri: str


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class ProofIn(BaseModel):
    proof: str = Field(
        min_length=1,
        max_length=1024,
        description="Your current authenticator or recovery code; your password if MFA is off",
    )


class MfaSetupIn(BaseModel):
    proof: str = Field(
        default="",
        max_length=1024,
        description=(
            "Your password. An account that signs in only with a provider has none: leave it empty"
        ),
    )


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str] = Field(description="Shown once; each works once")


class PinIn(ProofIn):
    pin: str = Field(min_length=6, max_length=8, description="6 to 8 digits")


@router.post("/mfa/verify", response_model=TokenPairOut)
async def verify_mfa(body: MfaVerifyIn, auth: Auth, meta: Meta) -> TokenPairOut:
    """The second step of signing in: a login (or OAuth sign-in) answered 401
    ``mfa_required`` with the challenge in ``error.detail``. Five wrong codes end it."""
    return _out(await auth.verify_mfa(mfa_token=body.mfa_token, code=body.code, meta=meta))


@router.get("/security", response_model=SecurityOut)
async def security_status(user: HumanUser, security: Security, request: Request) -> SecurityOut:
    st = await security.status(user)
    return SecurityOut(
        **{f: getattr(st, f) for f in SecurityOut.model_fields if hasattr(st, f)},
        mfa_required=request.app.state.settings.mfa_enforced,
    )


@router.post("/mfa/setup", response_model=TotpSetupOut)
async def mfa_setup(
    body: MfaSetupIn, user: HumanUser, security: Security, meta: Meta
) -> TotpSetupOut:
    """Start two-step sign-in: add the secret to an authenticator, then confirm a code.
    Needs the password, so a stolen session cannot enrol its own authenticator. Turning
    it on ends every session, this one included: sign in again with the new code."""
    out = await security.totp_setup(user, body.proof, meta)
    return TotpSetupOut(secret=out.secret, otpauth_uri=out.otpauth_uri)


@router.post("/mfa/confirm", response_model=RecoveryCodesOut)
async def mfa_confirm(
    body: CodeIn, user: HumanUser, security: Security, meta: Meta
) -> RecoveryCodesOut:
    """Turn two-step sign-in on. Other sessions are signed out; recovery codes are returned
    once."""
    return RecoveryCodesOut(recovery_codes=await security.totp_confirm(user, body.code, meta))


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def mfa_disable(body: ProofIn, user: HumanUser, security: Security, meta: Meta) -> None:
    await security.totp_disable(user, body.proof, meta)


@router.post("/mfa/recovery-codes", response_model=RecoveryCodesOut)
async def mfa_recovery_codes(
    body: ProofIn, user: HumanUser, security: Security, meta: Meta
) -> RecoveryCodesOut:
    """Replace the recovery codes; the old ones stop working."""
    return RecoveryCodesOut(
        recovery_codes=await security.new_recovery_codes(user, body.proof, meta)
    )


@router.put("/pin", status_code=status.HTTP_204_NO_CONTENT)
async def set_pin(body: PinIn, user: HumanUser, security: Security, meta: Meta) -> None:
    """Set or change the approval PIN, the second factor that works from a phone chat."""
    await security.set_pin(user, body.pin, body.proof, meta)


@router.post("/pin/clear", status_code=status.HTTP_204_NO_CONTENT)
async def clear_pin(body: ProofIn, user: HumanUser, security: Security, meta: Meta) -> None:
    await security.clear_pin(user, body.proof, meta)
