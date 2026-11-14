"""OAuth 2.1 for Mera's MCP server, and pending confirmations (P2-M8.2, 8.4; docs/05 §1.2).

- **Clients identify themselves with a Client ID Metadata Document**: the ``client_id``
  is an HTTPS URL serving JSON whose ``client_id`` is that URL and whose
  ``redirect_uris`` include the one asked for. Dynamic Client Registration is not offered
  (the spec deprecated it). Loopback redirect URIs are allowed on any port (RFC 8252).
- **PKCE S256 only.** Codes are single use and live five minutes.
- **Resource indicators (RFC 8707).** ``resource`` must be Mera's MCP endpoint, and the
  token is bound to it. The REST API accepts an MCP token only when the MCP service
  presents it, so a leaked token cannot be replayed against the REST API.
- **``iss`` is returned with the code** (RFC 9207).
- **Incremental consent**: a token carries only the scopes consented to; the MCP server
  names a missing scope in its challenge, and the client asks again for more.
- MCP tokens never carry ``admin`` or ``accounts:write``: connecting accounts and
  managing the workspace stay in the web app.

Pending actions: a high-risk tool call (deleting a comment) is parked here and completed
on a Mera web page with a second factor, so the code never passes through the model.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlencode, urlsplit

import structlog
from sqlalchemy import select

from mera.application.context import ApiKeyGrant, AuthenticatedUser, RequestMeta
from mera.application.errors import (
    AuthenticationFailed,
    ConflictError,
    Forbidden,
    InvalidInput,
    NotFoundError,
)
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.context import WorkspaceContext
from mera.domain.policies import rbac
from mera.domain.policies.rbac import KNOWN_SCOPES, Permission
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.crypto.api_keys import hash_key
from mera.infrastructure.db.models import McpGrant, McpOAuthCode, McpPendingAction, User
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import secret_lookup_transaction, workspace_transaction

log = structlog.get_logger(__name__)

TOKEN_PREFIX: Final = "mera_mcp_"  # noqa: S105 - a prefix, not a secret
REFRESH_PREFIX: Final = "mera_mcr_"
CODE_TTL: Final = timedelta(minutes=5)
TOKEN_TTL: Final = timedelta(hours=1)
REFRESH_TTL: Final = timedelta(days=30)
PENDING_TTL: Final = timedelta(minutes=15)
NEVER_FOR_MCP: Final = frozenset({"admin", "accounts:write"})
MCP_SCOPES: Final = frozenset(KNOWN_SCOPES - NEVER_FOR_MCP)
PENDING_ACTIONS: Final = frozenset({"comment_delete"})
CIMD_CACHE_MAX: Final = 512  # client metadata documents kept, ten minutes each


@dataclass(frozen=True, slots=True)
class ClientInfo:
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    logo_uri: str | None = None


@dataclass(frozen=True, slots=True)
class Introspection:
    active: bool
    kind: str = ""  # mcp_token | api_key
    workspace_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    scopes: tuple[str, ...] = ()
    client_id: str | None = None


def s256(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


def _loopback(uri: str) -> bool:
    parts = urlsplit(uri)
    if parts.scheme != "http":
        return False
    host = parts.hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*$")
_NEVER: Final = frozenset({"javascript", "data", "vbscript", "file", "blob", "about", "filesystem"})


def redirect_allowed(uri: str) -> bool:
    """Where a person may be sent back to with a code: https anywhere, http only to the
    loopback interface (RFC 8252 §7.3), or a reverse-DNS private-use scheme for native apps
    (RFC 8252 §7.1, e.g. ``com.example.app:/cb``). Never ``javascript:`` or ``data:``: the
    consent page navigates to this URI, so a script scheme would run in Mera's origin."""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if not scheme or parts.fragment or len(uri) > 2048 or any(c in uri for c in "\r\n\t \\"):
        return False
    if scheme == "https":
        return bool(parts.hostname)
    if scheme == "http":
        return _loopback(uri)
    return bool(_SCHEME.match(scheme)) and "." in scheme and scheme not in _NEVER


def _same_loopback(a: str, b: str) -> bool:
    """RFC 8252 §7.3: a loopback redirect matches whatever port the client opened."""
    pa, pb = urlsplit(a), urlsplit(b)
    return _loopback(a) and _loopback(b) and pa.hostname == pb.hostname and pa.path == pb.path


class McpAuthService(WorkspaceScopedService):
    def __init__(
        self,
        sm: Any,
        *,
        issuer: str,
        resource: str,
        fetcher: Any | None = None,
        web_url: str,
    ) -> None:
        super().__init__(sm)
        self._issuer = issuer.rstrip("/")
        self._resource = resource.rstrip("/")
        self._fetcher = fetcher
        self._web = web_url.rstrip("/")
        self._cimd: dict[str, tuple[float, ClientInfo]] = {}
        # Tests and sandbox demos register metadata documents here instead of hosting them.
        self.local_clients: dict[str, dict[str, Any]] = {}

    @property
    def resource(self) -> str:
        return self._resource

    def metadata(self) -> dict[str, Any]:
        """RFC 8414 authorization server metadata."""
        return {
            "issuer": self._issuer,
            "authorization_endpoint": f"{self._web}/oauth/authorize",
            "token_endpoint": f"{self._issuer}/api/v1/oauth/token",
            "revocation_endpoint": f"{self._issuer}/api/v1/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": sorted(MCP_SCOPES),
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
        }

    # ------------------------------------------------------------- clients

    async def client_info(self, client_id: str) -> ClientInfo:
        cached = self._cimd.get(client_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        parts = urlsplit(client_id)
        if parts.scheme != "https" or not parts.hostname or parts.fragment:
            raise InvalidInput(
                "client_id must be an https URL to a Client ID Metadata Document.",
                code="invalid_client",
                field="client_id",
            )
        doc = self.local_clients.get(client_id)
        if doc is None:
            if self._fetcher is None:
                raise InvalidInput("Unknown client.", code="invalid_client", field="client_id")
            try:
                fetched = await self._fetcher.get(
                    client_id,
                    headers={"Accept": "application/json"},
                    max_bytes=64 * 1024,
                    accept_statuses=frozenset({200}),
                )
                import json

                doc = json.loads(fetched.body)
            except Exception as exc:
                raise InvalidInput(
                    "The client's metadata document could not be read.",
                    code="invalid_client",
                    field="client_id",
                ) from exc
        if not isinstance(doc, dict) or doc.get("client_id") != client_id:
            raise InvalidInput(
                "The client's metadata document does not name itself.",
                code="invalid_client",
                field="client_id",
            )
        uris = tuple(
            str(u)
            for u in doc.get("redirect_uris") or []
            if isinstance(u, str) and redirect_allowed(str(u))
        )
        if not uris:
            raise InvalidInput("The client lists no redirect URIs.", code="invalid_client")
        info = ClientInfo(
            client_id=client_id,
            client_name=str(doc.get("client_name") or parts.hostname)[:120],
            redirect_uris=uris,
            logo_uri=doc.get("logo_uri") if isinstance(doc.get("logo_uri"), str) else None,
        )
        now = time.monotonic()
        if len(self._cimd) >= CIMD_CACHE_MAX:
            # Bounded: anyone can name a new client_id, so the cache must not grow with them
            # (release review #16). Expired entries go first, then the oldest.
            for key in [k for k, (exp, _i) in self._cimd.items() if exp <= now]:
                del self._cimd[key]
            while len(self._cimd) >= CIMD_CACHE_MAX:
                del self._cimd[next(iter(self._cimd))]
        self._cimd[client_id] = (now + 600, info)
        return info

    @staticmethod
    def _redirect_ok(info: ClientInfo, redirect_uri: str) -> bool:
        return any(redirect_uri == u or _same_loopback(redirect_uri, u) for u in info.redirect_uris)

    def _scopes(self, raw: str | list[str]) -> list[str]:
        asked = raw.split() if isinstance(raw, str) else list(raw)
        bad = [s for s in asked if s not in MCP_SCOPES]
        if bad or not asked:
            raise InvalidInput(
                f"Scopes not available to MCP clients: {', '.join(bad) or 'none asked'}.",
                code="invalid_scope",
                field="scope",
            )
        return sorted(set(asked))

    def _check_resource(self, resource: str | None) -> str:
        if not resource or resource.rstrip("/") != self._resource:
            raise InvalidInput(
                "resource must be Mera's MCP endpoint.", code="invalid_target", field="resource"
            )
        return self._resource

    # ----------------------------------------------------------- authorize

    async def authorize(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str,
        resource: str,
        state: str | None,
    ) -> str:
        """The consent page's approval: a code, as a redirect back to the client."""
        self._require_human(user)
        if code_challenge_method != "S256" or not 43 <= len(code_challenge) <= 128:
            raise InvalidInput("PKCE with S256 is required.", code="invalid_request")
        info = await self.client_info(client_id)
        if not redirect_allowed(redirect_uri) or not self._redirect_ok(info, redirect_uri):
            raise InvalidInput(
                "That redirect URI is not one the client lists.", code="invalid_redirect_uri"
            )
        scopes = self._scopes(scope)
        target = self._check_resource(resource)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
        code = secrets.token_urlsafe(32)
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            s.add(
                McpOAuthCode(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    user_id=user.user_id,
                    client_id=client_id,
                    client_name=info.client_name,
                    redirect_uri=redirect_uri,
                    code_hash=hash_key(code),
                    code_challenge=code_challenge,
                    scopes=scopes,
                    resource=target,
                    expires_at=datetime.now(UTC) + CODE_TTL,
                )
            )
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "mcp.authorized",
                    workspace_id=workspace_id,
                    after={"client_id": client_id, "scopes": scopes},
                ),
            )
        query = {"code": code, "iss": self._issuer}
        if state:
            query["state"] = state
        sep = "&" if "?" in redirect_uri else "?"
        return f"{redirect_uri}{sep}{urlencode(query)}"

    # --------------------------------------------------------------- token

    async def token(self, form: dict[str, str]) -> dict[str, Any]:
        grant = form.get("grant_type")
        if grant == "authorization_code":
            return await self._exchange(form)
        if grant == "refresh_token":
            return await self._refresh(form)
        raise InvalidInput("Unsupported grant type.", code="unsupported_grant_type")

    async def _issue(
        self,
        s: Any,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        client_id: str,
        client_name: str | None,
        scopes: list[str],
        resource: str,
    ) -> dict[str, Any]:
        access = TOKEN_PREFIX + secrets.token_urlsafe(32)
        refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        s.add(
            McpGrant(
                id=uuid7(),
                workspace_id=workspace_id,
                user_id=user_id,
                client_id=client_id,
                client_name=client_name,
                scopes=scopes,
                resource=resource,
                token_hash=hash_key(access),
                refresh_hash=hash_key(refresh),
                expires_at=now + TOKEN_TTL,
                refresh_expires_at=now + REFRESH_TTL,
            )
        )
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": int(TOKEN_TTL.total_seconds()),
            "refresh_token": refresh,
            "scope": " ".join(scopes),
        }

    async def _exchange(self, form: dict[str, str]) -> dict[str, Any]:
        raw = form.get("code") or ""
        h = hash_key(raw)
        async with secret_lookup_transaction(self._sm, "mera.mcp_code_hash", h) as s:
            code = (
                await s.execute(select(McpOAuthCode).where(McpOAuthCode.code_hash == h))
            ).scalar_one_or_none()
        if code is None:
            raise InvalidInput("Invalid code.", code="invalid_grant")
        async with workspace_transaction(self._sm, WorkspaceContext(code.workspace_id)) as s:
            row = await s.get(McpOAuthCode, code.id, with_for_update=True)
            assert row is not None
            now = datetime.now(UTC)
            ok = (
                row.used_at is None
                and row.expires_at > now
                and row.client_id == form.get("client_id")
                and row.redirect_uri == form.get("redirect_uri")
                and secrets.compare_digest(
                    row.code_challenge, s256(form.get("code_verifier") or "")
                )
                and (form.get("resource") or row.resource).rstrip("/") == row.resource
            )
            issued: dict[str, Any] | None = None
            if row.used_at is not None:
                # A replayed code: revoke what it issued (OAuth 2.1 §4.1.3). The refusal is
                # raised after this transaction commits, so the revocation stays.
                await self._revoke_chain(s, row.user_id, row.client_id, row.created_at, now)
            elif ok:
                row.used_at = now
                issued = await self._issue(
                    s,
                    workspace_id=row.workspace_id,
                    user_id=row.user_id,
                    client_id=row.client_id,
                    client_name=row.client_name,
                    scopes=list(row.scopes),
                    resource=row.resource,
                )
        if issued is None:
            raise InvalidInput("Invalid code.", code="invalid_grant")
        return issued

    @staticmethod
    async def _revoke_chain(
        s: Any, user_id: uuid.UUID, client_id: str, since: datetime, now: datetime
    ) -> None:
        """Every live grant this client holds for this person from ``since`` on: what a
        leaked code or refresh token could have produced."""
        for g in (
            await s.execute(
                select(McpGrant).where(
                    McpGrant.user_id == user_id,
                    McpGrant.client_id == client_id,
                    McpGrant.created_at >= since,
                    McpGrant.revoked_at.is_(None),
                )
            )
        ).scalars():
            g.revoked_at = now

    async def _refresh(self, form: dict[str, str]) -> dict[str, Any]:
        raw = form.get("refresh_token") or ""
        h = hash_key(raw)
        async with secret_lookup_transaction(self._sm, "mera.mcp_refresh_hash", h) as s:
            g = (
                await s.execute(select(McpGrant).where(McpGrant.refresh_hash == h))
            ).scalar_one_or_none()
        if g is None:
            raise InvalidInput("Invalid refresh token.", code="invalid_grant")
        async with workspace_transaction(self._sm, WorkspaceContext(g.workspace_id)) as s:
            row = await s.get(McpGrant, g.id, with_for_update=True)
            assert row is not None
            now = datetime.now(UTC)
            issued: dict[str, Any] | None = None
            if row.revoked_at is not None:
                # A spent refresh token came back (OAuth 2.1 §4.3.1): assume it leaked and
                # end everything issued from it. Kept: the refusal is raised after commit.
                await self._revoke_chain(s, row.user_id, row.client_id, row.created_at, now)
            elif (
                row.client_id == form.get("client_id")
                and row.refresh_expires_at is not None
                and row.refresh_expires_at > now
            ):
                scopes = list(row.scopes)
                if form.get("scope"):
                    narrower = self._scopes(form["scope"])
                    if not set(narrower) <= set(scopes):
                        raise InvalidInput("A refresh cannot widen scopes.", code="invalid_scope")
                    scopes = narrower
                # Rotation: the old pair is spent. Its refresh hash stays on the row, so a
                # second use is recognised as reuse rather than as an unknown token.
                row.revoked_at = now
                issued = await self._issue(
                    s,
                    workspace_id=row.workspace_id,
                    user_id=row.user_id,
                    client_id=row.client_id,
                    client_name=row.client_name,
                    scopes=scopes,
                    resource=row.resource,
                )
        if issued is None:
            raise InvalidInput("Invalid refresh token.", code="invalid_grant")
        return issued

    async def revoke_token(self, raw: str) -> None:
        h = hash_key(raw)
        setting = (
            "mera.mcp_refresh_hash" if raw.startswith(REFRESH_PREFIX) else "mera.mcp_token_hash"
        )
        async with secret_lookup_transaction(self._sm, setting, h) as s:
            g = (
                await s.execute(
                    select(McpGrant).where(
                        (McpGrant.token_hash == h) | (McpGrant.refresh_hash == h)
                    )
                )
            ).scalar_one_or_none()
        if g is None:
            return  # RFC 7009: unknown tokens are not an error
        async with workspace_transaction(self._sm, WorkspaceContext(g.workspace_id)) as s:
            row = await s.get(McpGrant, g.id, with_for_update=True)
            if row is not None and row.revoked_at is None:
                row.revoked_at = datetime.now(UTC)

    # ------------------------------------------------------ authentication

    async def authenticate(self, raw: str) -> AuthenticatedUser:
        h = hash_key(raw)
        async with secret_lookup_transaction(self._sm, "mera.mcp_token_hash", h) as s:
            g = (
                await s.execute(select(McpGrant).where(McpGrant.token_hash == h))
            ).scalar_one_or_none()
            user = (
                (
                    await s.execute(
                        select(User).where(User.id == g.user_id, User.deleted_at.is_(None))
                    )
                ).scalar_one_or_none()
                if g
                else None
            )
        now = datetime.now(UTC)
        if g is None or user is None or g.revoked_at is not None or g.expires_at <= now:
            raise AuthenticationFailed("Not signed in.", code="invalid_token")
        if g.last_used_at is None or now - g.last_used_at > timedelta(minutes=1):
            async with workspace_transaction(self._sm, WorkspaceContext(g.workspace_id)) as s:
                row = await s.get(McpGrant, g.id)
                if row is not None:
                    row.last_used_at = now
        return AuthenticatedUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            email_verified=user.email_verified_at is not None,
            api_key=ApiKeyGrant(
                api_key_id=g.id, workspace_id=g.workspace_id, scopes=frozenset(g.scopes)
            ),
        )

    # ----------------------------------------------------- connected clients

    async def list_grants(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[McpGrant]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = (
                select(McpGrant)
                .where(McpGrant.revoked_at.is_(None))
                .order_by(McpGrant.created_at.desc())
            )
            if not rbac.allowed(principal, Permission.MANAGE_MCP, workspace_id):
                q = q.where(McpGrant.user_id == user.user_id)  # your own clients only
            return list((await s.execute(q.limit(200))).scalars())

    async def revoke_grant(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        grant_id: uuid.UUID,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            g = await s.get(McpGrant, grant_id, with_for_update=True)
            if g is None:
                raise NotFoundError("Client not found.")
            if g.user_id != user.user_id:
                rbac.require(principal, Permission.MANAGE_MCP, workspace_id)
            if g.revoked_at is None:
                g.revoked_at = datetime.now(UTC)
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "mcp.client_revoked",
                        workspace_id=workspace_id,
                        after={"client_id": g.client_id},
                    ),
                )

    # ------------------------------------------------------ pending actions

    async def create_pending(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        action: str,
        tool: str,
        arguments: dict[str, Any],
        summary: str,
    ) -> tuple[McpPendingAction, str]:
        """``summary`` is the server's own description of the action (built from the
        records it names, never from text a client sent): it is what the person reads on
        the confirmation page before giving a second factor (release review #12)."""
        if action not in PENDING_ACTIONS:
            raise InvalidInput("That action cannot be confirmed this way.", code="invalid_action")
        async with self._tx(user, workspace_id, meta) as s:
            await load_user_principal(s, user, workspace_id)
            row = McpPendingAction(
                id=uuid7(),
                workspace_id=workspace_id,
                user_id=user.user_id,
                action=action,
                tool=tool[:64],
                arguments=arguments,
                summary=summary[:300],
                expires_at=datetime.now(UTC) + PENDING_TTL,
            )
            s.add(row)
            await s.flush()
            from mera.infrastructure.db.models import Workspace

            ws = await s.get(Workspace, workspace_id)
            slug = ws.slug if ws else str(workspace_id)
        return row, f"{self._web}/w/{slug}/confirm/{row.id}"

    async def get_pending(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        pending_id: uuid.UUID,
    ) -> McpPendingAction:
        async with self._tx(user, workspace_id, meta) as s:
            await load_user_principal(s, user, workspace_id)
            row = await s.get(McpPendingAction, pending_id)
            if row is None or row.user_id != user.user_id:
                raise NotFoundError("Confirmation not found.")
            if row.state == "pending" and row.expires_at <= datetime.now(UTC):
                row.state = "expired"
            return row

    async def finish_pending(
        self,
        workspace_id: uuid.UUID,
        pending_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        state: str,
        result: dict[str, Any],
    ) -> None:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            row = await s.get(McpPendingAction, pending_id, with_for_update=True)
            if row is None or row.user_id != user_id:
                raise NotFoundError("Confirmation not found.")
            if row.state != "pending":
                raise ConflictError("This confirmation was already used.", code="confirmation_used")
            if row.expires_at <= datetime.now(UTC):
                row.state = "expired"
                raise ConflictError("This confirmation expired.", code="confirmation_expired")
            row.state, row.result, row.completed_at = state, result, datetime.now(UTC)
            await record(
                s,
                AuditEntry(
                    action=f"mcp.pending_{state}",
                    actor_type="user",
                    actor_id=user_id,
                    surface="web",
                    workspace_id=workspace_id,
                    subject_type="mcp_pending_action",
                    subject_id=pending_id,
                ),
            )

    # --------------------------------------------------------- introspection

    async def introspect(self, raw: str, auth: Any) -> Introspection:
        """For the MCP service only (it presents its secret): who a bearer is."""
        try:
            if raw.startswith(TOKEN_PREFIX):
                who = await self.authenticate(raw)
                kind = "mcp_token"
            else:
                who = await auth.authenticate(raw)
                kind = "api_key" if who.is_api_key else "session"
        except AuthenticationFailed:
            return Introspection(active=False)
        if kind == "session":
            # A browser session token is not an MCP credential.
            return Introspection(active=False)
        grant = who.api_key
        assert grant is not None
        client = None
        if kind == "mcp_token":
            async with workspace_transaction(self._sm, WorkspaceContext(grant.workspace_id)) as s:
                g = await s.get(McpGrant, grant.api_key_id)
                client = g.client_id if g else None
        return Introspection(
            active=True,
            kind=kind,
            workspace_id=grant.workspace_id,
            user_id=who.user_id,
            scopes=tuple(sorted(grant.scopes)),
            client_id=client,
        )


def require_human_for_pending(user: AuthenticatedUser) -> None:
    if user.is_api_key:
        raise Forbidden("Confirm in the Mera web app.", code="user_credentials_required")
