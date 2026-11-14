"""Per-call metadata every service records into the audit log."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestMeta:
    surface: str = "api"  # web, api, mcp, telegram, whatsapp, worker
    ip_address: str | None = None
    request_id: str | None = None
    user_agent: str | None = None
    # ``X-Mera-Second-Factor``: a grant for one high-risk action (P2-M2.5).
    second_factor: str | None = None


@dataclass(frozen=True, slots=True)
class ApiKeyGrant:
    """What an API key adds to its creator's identity: one workspace and a scope list."""

    api_key_id: uuid.UUID
    workspace_id: uuid.UUID
    scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """A verified caller. For a JWT, the user; for an API key, the key's creator plus the
    key's grant. Not yet bound to a workspace: that happens per call, inside the call's
    transaction, so the role is always read at the moment of the action."""

    user_id: uuid.UUID
    email: str
    display_name: str
    api_key: ApiKeyGrant | None = None
    email_verified: bool = False
    # P2-M2.3: an Admin or Owner without MFA while it is enforced acts as an Editor until
    # they enrol (set by AuthService.authenticate).
    mfa_missing: bool = False

    @property
    def is_api_key(self) -> bool:
        return self.api_key is not None
