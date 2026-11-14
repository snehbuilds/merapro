"""Social accounts (M3.1-3.3, 3.6-3.8; docs/03 §4, docs/04 §4.2).

Connecting is a redirect round trip that lands on the API with no Mera session (the browser
was at the platform), so the single-use ``account_connect_flow`` carries the workspace and
the initiating user, and authority is re-checked when the callback completes.

Credentials are envelope-encrypted, bound to the workspace, the purpose *and the account
row*, and are decrypted only at the moment of use. They are never returned by the API.

Platform calls (exchange, refresh, health, revoke) never run inside a database
transaction: a slow platform must not pin pooled connections.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application import autopilot, second_factor
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import (
    ConflictError,
    InvalidInput,
    NotFoundError,
)
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.application.settings_model import LevelSettings, level_values, merged
from mera.domain.context import WorkspaceContext
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.policies.settings_resolution import Level, Resolved, resolve_all
from mera.domain.ports.key_provider import KeyProvider
from mera.domain.principal import Principal
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.platform import AccountStatus, Platform
from mera.infrastructure.crypto.api_keys import hash_key
from mera.infrastructure.db.models import (
    AccountConnectFlow,
    AccountGroup,
    AccountGroupMember,
    MastodonApp,
    Notification,
    SocialAccount,
    Workspace,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import (
    global_transaction,
    secret_lookup_transaction,
    workspace_transaction,
)
from mera.infrastructure.platforms.capabilities import PlatformCapabilities, capabilities_for
from mera.infrastructure.platforms.connectors import (
    ConnectorError,
    PlatformConnector,
    ReauthRequired,
    s256,
)

log = structlog.get_logger(__name__)

CREDENTIALS_PURPOSE = "social_account.credentials"
PENDING_PURPOSE = "account_connect_flow.identity"
MASTODON_PURPOSE = "mastodon_app.client_secret"
CONNECT_CODE_TTL = timedelta(minutes=5)
CONNECT_FLOW_TTL = timedelta(minutes=10)
REFRESH_AT_FRACTION = 0.8  # refresh at 80% of the token's lifetime (docs/09 §4.4)


@dataclass(frozen=True, slots=True)
class AccountView:
    id: uuid.UUID
    platform: str
    platform_account_id: str
    handle: str
    display_name: str | None
    nickname: str
    avatar_url: str | None
    account_type: str
    status: str
    status_reason: str | None
    granted_scopes: list[str]
    token_expires_at: datetime | None
    last_health_check_at: datetime | None
    health: dict[str, Any]
    default_brand_voice_id: uuid.UUID | None
    default_posting_times: list[dict[str, Any]] | None
    settings: dict[str, Any]
    group_ids: list[uuid.UUID]
    connected_at: datetime | None


class AccountService(WorkspaceScopedService):
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        connectors: dict[Platform, PlatformConnector],
        key_provider: KeyProvider,
        api_url: str,
        public_url: str,
        max_accounts: int = 25,
    ) -> None:
        super().__init__(sessionmaker)
        self._connectors = connectors
        self._kp = key_provider
        self._api_url = api_url.rstrip("/")
        self._public_url = public_url.rstrip("/")
        self._max_accounts = max_accounts

    def callback_uri(self, platform: Platform) -> str:
        # One fixed URI per platform: platforms only accept pre-registered redirect URIs,
        # so it cannot carry the workspace (deliberate deviation from docs/04 §4.2).
        return f"{self._api_url}/api/v1/social-accounts/oauth/{platform.value}/callback"

    def connector(self, platform: Platform) -> PlatformConnector:
        c = self._connectors.get(platform)
        if c is None:
            raise NotFoundError(
                f"{capabilities_for(platform).display_name} cannot be connected "
                "on this server yet.",
                code="platform_unavailable",
            )
        return c

    # ------------------------------------------------------------------ connect

    async def start_connect(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        platform: Platform,
        meta: RequestMeta,
        *,
        return_path: str,
        reconnect_account_id: uuid.UUID | None = None,
        instance: str | None = None,
    ) -> str:
        """Returns the platform consent URL to send the browser to. Mastodon needs the
        ``instance`` (the person's server); Bluesky connects with ``sign_in_connect``."""
        self._require_human(user)
        if not return_path.startswith("/") or return_path.startswith("//") or "\\" in return_path:
            raise InvalidInput(
                "return_path must be a path on the Mera web app.",
                code="invalid_return_path",
                field="return_path",
            )
        connector = self.connector(platform)
        if getattr(connector, "connect_method", "oauth") == "app_password":
            raise InvalidInput(
                f"{capabilities_for(platform).display_name} connects with an app password.",
                code="use_app_password",
            )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONNECT_ACCOUNTS, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            self._ensure_writable(ws)
            if reconnect_account_id is not None:
                existing = await s.get(SocialAccount, reconnect_account_id)
                if existing is None or existing.platform != platform.value:
                    raise NotFoundError("Account not found.")
                if existing.status == AccountStatus.DISCONNECTED.value:
                    await self._ensure_capacity(s, workspace_id)
            else:
                await self._ensure_capacity(s, workspace_id)
        # The server is resolved, and Mera's app registered there, only after the person is
        # known to be allowed to connect: no outbound call for anyone else.
        context: dict[str, Any] | None = None
        if getattr(connector, "per_instance", False):
            if not instance:
                raise InvalidInput(
                    "Which server is the account on?", code="instance_required", field="instance"
                )
            host = await self._instance_host(connector, instance)
            client = await self._instance_client(connector, host)
            connector = connector.for_instance(host, client)  # type: ignore[attr-defined]
            context = {"instance": host}
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            s.add(
                AccountConnectFlow(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    platform=platform.value,
                    state_hash=hash_key(state),
                    code_verifier=verifier,
                    initiated_by=user.user_id,
                    reconnect_account_id=reconnect_account_id,
                    return_path=return_path,
                    expires_at=datetime.now(UTC) + CONNECT_FLOW_TTL,
                    context=context,
                )
            )
        return connector.authorization_url(
            state=state, code_challenge=s256(verifier), redirect_uri=self.callback_uri(platform)
        )

    async def complete_connect(
        self,
        platform: Platform,
        *,
        state: str | None,
        code: str | None,
        error: str | None,
        meta: RequestMeta,
    ) -> str:
        """The platform's redirect lands here, in whatever browser granted consent. The
        exchanged identity is held (encrypted) under a one-time ``connect_code`` that is sent
        back to that browser; nothing is attached yet. Attaching happens in
        ``finish_connect``, which only the user who started the flow can do (Sprint 4 review
        M2: a consent link forwarded to someone else must not connect *their* account to
        the sender's workspace)."""
        connector = self.connector(platform)
        if not state:
            raise InvalidInput("Missing state.", code="invalid_state")
        state_hash = hash_key(state)
        async with secret_lookup_transaction(self._sm, "mera.connect_state_hash", state_hash) as s:
            found = (
                await s.execute(
                    select(AccountConnectFlow.id, AccountConnectFlow.workspace_id).where(
                        AccountConnectFlow.state_hash == state_hash,
                        AccountConnectFlow.platform == platform.value,
                    )
                )
            ).first()
        expired = InvalidInput(
            "This connection link has expired. Start again.", code="invalid_state"
        )
        if found is None:
            raise expired
        flow_id, flow_ws = found
        async with workspace_transaction(self._sm, WorkspaceContext(flow_ws)) as s:
            flow = (
                await s.execute(
                    select(AccountConnectFlow)
                    .where(AccountConnectFlow.id == flow_id)
                    .with_for_update()
                )
            ).scalar_one()
            if flow.used_at is not None or flow.expires_at <= datetime.now(UTC):
                raise expired
            flow.used_at = datetime.now(UTC)
            verifier, return_path = flow.code_verifier, flow.return_path
            context = dict(flow.context or {})
        back = f"{self._public_url}{return_path}"
        if error or not code:
            return _with(back, error="platform_denied")
        if getattr(connector, "per_instance", False):
            host = str(context.get("instance") or "")
            try:
                client = await self._instance_client(connector, host)
            except InvalidInput:
                return _with(back, error="platform_error")
            connector = connector.for_instance(host, client)  # type: ignore[attr-defined]
        try:
            identity = await connector.exchange(
                code=code, code_verifier=verifier, redirect_uri=self.callback_uri(platform)
            )
        except ConnectorError as exc:
            log.warning("account.connect_exchange_failed", platform=platform.value, error=str(exc))
            return _with(back, error="platform_error")
        connect_code = secrets.token_urlsafe(32)
        async with workspace_transaction(self._sm, WorkspaceContext(flow_ws)) as s:
            held = await s.get(AccountConnectFlow, flow_id, with_for_update=True)
            assert held is not None
            held.connect_code_hash = hash_key(connect_code)
            held.pending_identity_ct = self._kp.encrypt(
                json.dumps(_identity_json(identity)).encode(),
                workspace_id=flow_ws,
                purpose=PENDING_PURPOSE,
                record_id=flow_id,
            )
        return _with(back, connect_code=connect_code)

    # --------------------------------------------------- app passwords and instances (P2-M3)

    async def sign_in_connect(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        platform: Platform,
        meta: RequestMeta,
        *,
        identifier: str,
        secret: str,
        service: str | None = None,
        reconnect_account_id: uuid.UUID | None = None,
    ) -> str:
        """Connect with a handle and an app password (Bluesky). Returns a one-time
        ``connect_code`` redeemed exactly as after an OAuth redirect, so binding, duplicate
        and limit checks are the same. The secret is used once and never stored."""
        self._require_human(user)
        connector = self.connector(platform)
        sign_in = getattr(connector, "sign_in", None)
        if getattr(connector, "connect_method", "oauth") != "app_password" or sign_in is None:
            raise InvalidInput(
                f"{capabilities_for(platform).display_name} connects through its own sign-in page.",
                code="use_oauth",
            )
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONNECT_ACCOUNTS, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            self._ensure_writable(ws)
            if reconnect_account_id is not None:
                existing = await s.get(SocialAccount, reconnect_account_id)
                if existing is None or existing.platform != platform.value:
                    raise NotFoundError("Account not found.")
            else:
                await self._ensure_capacity(s, workspace_id)
        try:
            identity = await sign_in(identifier=identifier, secret=secret, service=service)
        except ConnectorError as exc:
            raise InvalidInput(
                str(exc).capitalize() + ".", code="sign_in_failed", field="secret"
            ) from exc
        connect_code = secrets.token_urlsafe(32)
        flow_id = uuid7()
        now = datetime.now(UTC)
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            s.add(
                AccountConnectFlow(
                    id=flow_id,
                    workspace_id=workspace_id,
                    platform=platform.value,
                    state_hash=hash_key(secrets.token_urlsafe(32)),
                    code_verifier=secrets.token_urlsafe(48),
                    initiated_by=user.user_id,
                    reconnect_account_id=reconnect_account_id,
                    return_path="/",
                    expires_at=now + CONNECT_FLOW_TTL,
                    used_at=now,
                    connect_code_hash=hash_key(connect_code),
                )
            )
            await s.flush()
            held = await s.get(AccountConnectFlow, flow_id)
            assert held is not None
            held.pending_identity_ct = self._kp.encrypt(
                json.dumps(_identity_json(identity)).encode(),
                workspace_id=workspace_id,
                purpose=PENDING_PURPOSE,
                record_id=flow_id,
            )
        return connect_code

    @staticmethod
    async def _instance_host(connector: Any, raw: str) -> str:
        try:
            return str(await connector.instance_host(raw))
        except ConnectorError as exc:
            raise InvalidInput(
                str(exc).capitalize() + ".", code="invalid_instance", field="instance"
            ) from exc

    async def _instance_client(self, connector: Any, host: str) -> Any:
        """Mera's app on this instance, registered on first use (Mastodon). The secret is
        envelope-encrypted; the row is instance-wide, not tenant data."""
        from mera.infrastructure.platforms.fediverse_connectors import MastodonClient

        nil = uuid.UUID(int=0)
        async with global_transaction(self._sm) as s:
            row = (
                await s.execute(select(MastodonApp).where(MastodonApp.instance == host))
            ).scalar_one_or_none()
            if row is not None:
                secret = self._kp.decrypt(
                    row.client_secret_ct,
                    workspace_id=nil,
                    purpose=MASTODON_PURPOSE,
                    record_id=row.id,
                ).decode()
                return MastodonClient(row.client_id, secret)
        redirect = self.callback_uri(Platform.MASTODON)
        try:
            client = await connector.register_app(host, redirect)
        except ConnectorError as exc:
            raise InvalidInput(
                f"{host} did not let Mera register. Check the address, or try again later.",
                code="instance_unavailable",
                field="instance",
            ) from exc
        row_id = uuid7()
        try:
            async with global_transaction(self._sm) as s:
                s.add(
                    MastodonApp(
                        id=row_id,
                        instance=host,
                        client_id=client.client_id,
                        client_secret_ct=self._kp.encrypt(
                            client.client_secret.encode(),
                            workspace_id=nil,
                            purpose=MASTODON_PURPOSE,
                            record_id=row_id,
                        ),
                        redirect_uri=redirect,
                        scopes="read write",
                    )
                )
        except IntegrityError:
            # Another request registered first; use theirs.
            return await self._instance_client(connector, host)
        return client

    async def connect_choices(
        self, user: AuthenticatedUser, connect_code: str, meta: RequestMeta
    ) -> list[dict[str, str | None]]:
        """The Pages a pending connection could become, without their tokens. Empty when the
        login granted one. Only whoever started the flow may look, as for finishing it."""
        self._require_human(user)
        code_hash = hash_key(connect_code)
        async with secret_lookup_transaction(self._sm, "mera.connect_code_hash", code_hash) as s:
            found = (
                await s.execute(
                    select(AccountConnectFlow.id, AccountConnectFlow.workspace_id).where(
                        AccountConnectFlow.connect_code_hash == code_hash
                    )
                )
            ).first()
        if found is None:
            raise _invalid_connect_code()
        flow_id, ws_id = found
        async with self._tx(user, ws_id, meta) as s:
            flow = await s.get(AccountConnectFlow, flow_id)
            if (
                flow is None
                or flow.completed_at is not None
                or flow.pending_identity_ct is None
                or flow.used_at is None
                or flow.used_at + CONNECT_CODE_TTL <= datetime.now(UTC)
                or flow.initiated_by != user.user_id
            ):
                raise _invalid_connect_code()
            identity = json.loads(
                self._kp.decrypt(
                    flow.pending_identity_ct,
                    workspace_id=ws_id,
                    purpose=PENDING_PURPOSE,
                    record_id=flow_id,
                )
            )
        return [
            {
                "platform_account_id": c["platform_account_id"],
                "handle": c["handle"],
                "display_name": c["display_name"],
                "account_type": c["account_type"],
            }
            for c in identity.get("choices") or []
        ]

    async def finish_connect(
        self,
        user: AuthenticatedUser,
        connect_code: str,
        meta: RequestMeta,
        *,
        choice: str | None = None,
    ) -> tuple[str, uuid.UUID, uuid.UUID]:
        """Redeem a ``connect_code``. Returns (outcome, account_id, workspace_id). When the
        login granted several Pages, ``choice`` (a ``platform_account_id``) says which;
        without it the redemption is refused (409 ``choice_required``) and stays open."""
        self._require_human(user)
        invalid = NotFoundError(
            "This connection link has expired or belongs to someone else. Start again.",
            code="invalid_connect_code",
        )
        code_hash = hash_key(connect_code)
        async with secret_lookup_transaction(self._sm, "mera.connect_code_hash", code_hash) as s:
            found = (
                await s.execute(
                    select(AccountConnectFlow.id, AccountConnectFlow.workspace_id).where(
                        AccountConnectFlow.connect_code_hash == code_hash
                    )
                )
            ).first()
        if found is None:
            raise invalid
        flow_id, ws_id = found
        async with self._tx(user, ws_id, meta) as s:
            flow = (
                await s.execute(
                    select(AccountConnectFlow)
                    .where(AccountConnectFlow.id == flow_id)
                    .with_for_update()
                )
            ).scalar_one()
            if (
                flow.completed_at is not None
                or flow.pending_identity_ct is None
                or flow.used_at is None
                or flow.used_at + CONNECT_CODE_TTL <= datetime.now(UTC)
                # The binding: only whoever started the flow can finish it.
                or flow.initiated_by != user.user_id
            ):
                raise invalid
            flow.completed_at = datetime.now(UTC)
            platform = Platform(flow.platform)
            reconnect_id = flow.reconnect_account_id
            identity = json.loads(
                self._kp.decrypt(
                    flow.pending_identity_ct,
                    workspace_id=ws_id,
                    purpose=PENDING_PURPOSE,
                    record_id=flow_id,
                )
            )
            flow.pending_identity_ct = None  # the tokens now live only on the account
            choices = identity.pop("choices", None) or []
            if choices:
                if choice is None:
                    # Raised inside the transaction: the flow stays redeemable.
                    raise ConflictError(
                        "This sign-in manages several Pages. Choose the one to connect.",
                        code="choice_required",
                    )
                picked = next((c for c in choices if c["platform_account_id"] == choice), None)
                if picked is None:
                    raise InvalidInput(
                        "Choose one of the Pages this sign-in offered.",
                        code="invalid_choice",
                        field="platform_account_id",
                    )
                identity = picked
            # Authority is re-checked now: the initiator may have been demoted meanwhile.
            principal = await load_user_principal(s, user, ws_id)
            rbac.require(principal, Permission.CONNECT_ACCOUNTS, ws_id)
            ws = await s.get(Workspace, ws_id, with_for_update=True)
            assert ws is not None
            self._ensure_writable(ws)
            existing = (
                await s.execute(
                    select(SocialAccount).where(
                        SocialAccount.workspace_id == ws_id,
                        SocialAccount.platform == platform.value,
                        SocialAccount.platform_account_id == identity["platform_account_id"],
                    )
                )
            ).scalar_one_or_none()
            if reconnect_id is not None and (existing is None or existing.id != reconnect_id):
                raise ConflictError(
                    "You signed in to a different account than the one being reconnected.",
                    code="account_mismatch",
                )
            if existing is not None and existing.status == AccountStatus.SUSPENDED.value:
                raise ConflictError(
                    "This account is suspended; reconnecting does not lift that.",
                    code="account_suspended",
                )
            if (
                existing is not None
                and reconnect_id is None
                and existing.status == AccountStatus.ACTIVE.value
            ):
                raise ConflictError(
                    f"{existing.nickname} is already connected to this workspace.",
                    code="account_already_connected",
                )
            # Reactivating a disconnected account counts against the limit too (review M1).
            if (existing is None or existing.status == AccountStatus.DISCONNECTED.value) and not (
                await self._has_capacity(s, ws_id)
            ):
                raise ConflictError(
                    f"This workspace already has {self._max_accounts} connected accounts.",
                    code="account_limit_reached",
                )
            now = datetime.now(UTC)
            caps = capabilities_for(platform)
            if existing is None:
                account = SocialAccount(
                    id=uuid7(),
                    workspace_id=ws_id,
                    platform=platform.value,
                    platform_account_id=identity["platform_account_id"],
                    nickname=f"{caps.display_name} {identity['handle']}"[:80],
                )
                s.add(account)
                outcome, action = "connected", "account.connected"
            else:
                account = existing
                outcome, action = "reconnected", "account.reconnected"
            account.handle = str(identity["handle"])[:200]
            account.display_name = (identity["display_name"] or None) and str(
                identity["display_name"]
            )[:200]
            account.avatar_url = _safe_url(identity["avatar_url"])
            account.account_type = identity["account_type"]
            account.credentials_ct = self._seal(ws_id, account.id, identity["credentials"])
            account.granted_scopes = list(identity["granted_scopes"])
            account.token_issued_at = now
            account.token_expires_at = (
                datetime.fromisoformat(identity["expires_at"]) if identity["expires_at"] else None
            )
            account.status = AccountStatus.ACTIVE.value
            account.status_reason = None
            account.capabilities_snapshot = caps.model_dump(mode="json")
            account.connected_by = user.user_id
            account.connected_at = now
            account.disconnected_at = None
            account.health = {"token_valid": True, "scopes_ok": True, "last_error": None}
            account.last_health_check_at = now
            await s.flush()
            # A reactivated account rejoins its groups: it must not create two same-platform
            # accounts in a group set to "all at once" (review M3).
            if existing is not None:
                from mera.application.services.group_service import check_stagger_for_account

                await check_stagger_for_account(s, account.id)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    action,
                    workspace_id=ws_id,
                    subject_type="social_account",
                    subject_id=account.id,
                    social_account_id=account.id,
                    after={"platform": platform.value, "handle": account.handle},
                ),
            )
            return outcome, account.id, ws_id

    async def disconnect(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        """Revokes at the platform first (docs/04 §4.2), then forgets the credentials. If the
        platform cannot be reached, Mera still forgets them and says so: holding a grant it
        was told to drop would be worse than an untidy grant left at the platform."""
        self._require_human(user)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONNECT_ACCOUNTS, workspace_id)
            account = await self._account(s, account_id)
            await second_factor.require(s, user, workspace_id, "account_disconnect", meta)
            creds = self._open(account) if account.credentials_ct else None
            platform = Platform(account.platform)
        revoke_error: str | None = None
        if creds is not None:
            try:
                await self.connector(platform).revoke(creds)
            except (ConnectorError, NotFoundError) as exc:
                revoke_error = type(exc).__name__
        async with self._tx(user, workspace_id, meta) as s:
            account = await self._account(s, account_id, lock=True)
            account.credentials_ct = None
            account.status = AccountStatus.DISCONNECTED.value
            account.status_reason = "disconnected by a member"
            account.disconnected_at = datetime.now(UTC)
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "account.disconnected",
                    workspace_id=workspace_id,
                    subject_type="social_account",
                    subject_id=account_id,
                    social_account_id=account_id,
                    after={"platform_revoked": revoke_error is None, "revoke_error": revoke_error},
                ),
            )
            if revoke_error is not None:
                s.add(
                    _notification(
                        workspace_id,
                        account,
                        kind="account.revoke_failed",
                        severity="warning",
                        title=f"{account.nickname}: Mera forgot its access, but could not "
                        "tell the platform",
                        body="Remove Mera's access in the platform's own settings to finish.",
                    )
                )

    # ------------------------------------------------------------------ reads & edits

    async def list_accounts(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        platform: Platform | None = None,
        status: AccountStatus | None = None,
        group_id: uuid.UUID | None = None,
    ) -> list[AccountView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            stmt = select(SocialAccount).order_by(SocialAccount.platform, SocialAccount.nickname)
            if platform is not None:
                stmt = stmt.where(SocialAccount.platform == platform.value)
            if status is not None:
                stmt = stmt.where(SocialAccount.status == status.value)
            if group_id is not None:
                stmt = stmt.where(
                    SocialAccount.id.in_(
                        select(AccountGroupMember.social_account_id).where(
                            AccountGroupMember.account_group_id == group_id
                        )
                    )
                )
            accounts = list((await s.execute(stmt)).scalars())
            groups = await _groups_by_account(s, [a.id for a in accounts])
            return [
                _view(a, _visible_groups(principal, groups.get(a.id, [])))
                for a in accounts
                if _in_scope(principal, a.id, groups.get(a.id, []))
            ]

    async def get(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
        meta: RequestMeta,
    ) -> AccountView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            account = await self._account(s, account_id)
            group_ids = (await _groups_by_account(s, [account_id])).get(account_id, [])
            self._require_for_account(
                principal, Permission.READ, workspace_id, account_id, group_ids
            )
            return _view(account, _visible_groups(principal, group_ids))

    async def update(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
        meta: RequestMeta,
        *,
        nickname: str | None,
        default_posting_times: list[dict[str, Any]] | None,
        clear_posting_times: bool,
        settings: LevelSettings | None,
    ) -> AccountView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONNECT_ACCOUNTS, workspace_id)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            self._ensure_writable(ws)
            account = await self._account(s, account_id, lock=True)
            before = {
                "nickname": account.nickname,
                "default_posting_times": account.default_posting_times,
                "settings": account.settings,
            }
            if nickname is not None:
                account.nickname = nickname.strip()
            if clear_posting_times:
                account.default_posting_times = None
            elif default_posting_times is not None:
                account.default_posting_times = default_posting_times
            if settings is not None:
                new_settings = merged(account.settings, settings)
                await autopilot.guard_change(
                    s,
                    user,
                    principal,
                    workspace_id,
                    meta,
                    before=account.settings,
                    after=new_settings,
                    scope_type="social_account",
                    scope_id=account.id,
                    accounts=[account],
                )
                account.settings = new_settings
            after = {
                "nickname": account.nickname,
                "default_posting_times": account.default_posting_times,
                "settings": account.settings,
            }
            if after != before:
                await s.flush()
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "account.updated",
                        workspace_id=workspace_id,
                        subject_type="social_account",
                        subject_id=account_id,
                        social_account_id=account_id,
                        before=before,
                        after=after,
                    ),
                )
            group_ids = (await _groups_by_account(s, [account_id])).get(account_id, [])
            return _view(account, group_ids)

    async def capabilities(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
        meta: RequestMeta,
    ) -> PlatformCapabilities:
        view = await self.get(user, workspace_id, account_id, meta)
        return capabilities_for(Platform(view.platform))

    async def resolved_settings(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
        meta: RequestMeta,
        *,
        via_group_id: uuid.UUID | None,
    ) -> list[Resolved]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            account = await self._account(s, account_id)
            group_ids = (await _groups_by_account(s, [account_id])).get(account_id, [])
            self._require_for_account(
                principal, Permission.READ, workspace_id, account_id, group_ids
            )
            group = None
            if via_group_id is not None:
                if via_group_id not in group_ids:
                    raise InvalidInput(
                        "That account is not in that group.",
                        code="not_a_member",
                        field="via_group_id",
                    )
                rbac.require(
                    principal, Permission.READ, workspace_id, account_group_id=via_group_id
                )
                group = await s.get(AccountGroup, via_group_id)
            ws = await s.get(Workspace, workspace_id)
            assert ws is not None
            return resolve_all(_levels(ws, group, account))

    async def check_health_now(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
        meta: RequestMeta,
    ) -> AccountView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.CONNECT_ACCOUNTS, workspace_id)
        await AccountMaintenance(self._sm, self._connectors, self._kp).check_health(
            workspace_id, account_id
        )
        return await self.get(user, workspace_id, account_id, meta)

    # ------------------------------------------------------------------ helpers

    async def _account(
        self, s: AsyncSession, account_id: uuid.UUID, *, lock: bool = False
    ) -> SocialAccount:
        stmt = select(SocialAccount).where(SocialAccount.id == account_id)
        if lock:
            stmt = stmt.with_for_update()
        account = (await s.execute(stmt)).scalar_one_or_none()
        if account is None:
            raise NotFoundError("Account not found.")
        return account

    async def _has_capacity(self, s: AsyncSession, workspace_id: uuid.UUID) -> bool:
        count = (
            await s.execute(
                select(func.count())
                .select_from(SocialAccount)
                .where(
                    SocialAccount.workspace_id == workspace_id,
                    SocialAccount.status != AccountStatus.DISCONNECTED.value,
                )
            )
        ).scalar_one()
        return int(count) < self._max_accounts

    async def _ensure_capacity(self, s: AsyncSession, workspace_id: uuid.UUID) -> None:
        if not await self._has_capacity(s, workspace_id):
            raise ConflictError(
                f"This workspace already has {self._max_accounts} connected accounts.",
                code="account_limit_reached",
            )

    def _seal(self, workspace_id: uuid.UUID, account_id: uuid.UUID, creds: dict[str, Any]) -> str:
        return self._kp.encrypt(
            json.dumps(creds).encode(),
            workspace_id=workspace_id,
            purpose=CREDENTIALS_PURPOSE,
            record_id=account_id,
        )

    def _open(self, account: SocialAccount) -> dict[str, Any]:
        return open_credentials(self._kp, account)


class AccountMaintenance:
    """The ``accounts`` queue's work (M3.7, 3.8): token refresh at 80% of lifetime and
    health checks. Used by the periodic tasks and by "check now"."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        connectors: dict[Platform, PlatformConnector],
        key_provider: KeyProvider,
    ) -> None:
        self._sm = sessionmaker
        self._connectors = connectors
        self._kp = key_provider

    async def refresh_if_due(self, workspace_id: uuid.UUID, account_id: uuid.UUID) -> str:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            account = await s.get(SocialAccount, account_id)
            if account is None or account.status != AccountStatus.ACTIVE.value:
                return "skipped"
            if not _refresh_due(account):
                return "not_due"
            creds = open_credentials(self._kp, account)
            platform = Platform(account.platform)
            issued_before = account.token_issued_at
        connector = self._connectors.get(platform)
        if connector is None:
            return "skipped"
        try:
            new_creds, expires_at = await connector.refresh(creds)
        except ReauthRequired as exc:
            await self._needs_reauth(workspace_id, account_id, _safe_reason(exc))
            return "needs_reauth"
        except ConnectorError:
            return "failed"  # transient: the next sweep tries again
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            account = await s.get(SocialAccount, account_id, with_for_update=True)
            if account is None or account.status != AccountStatus.ACTIVE.value:
                return "skipped"
            if account.token_issued_at != issued_before:
                return "superseded"  # reconnected while we refreshed: keep the newer grant
            account.credentials_ct = self._kp.encrypt(
                json.dumps(new_creds).encode(),
                workspace_id=workspace_id,
                purpose=CREDENTIALS_PURPOSE,
                record_id=account_id,
            )
            account.token_issued_at = datetime.now(UTC)
            account.token_expires_at = expires_at
        return "refreshed"

    async def check_health(self, workspace_id: uuid.UUID, account_id: uuid.UUID) -> str:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            account = await s.get(SocialAccount, account_id)
            if account is None or account.status != AccountStatus.ACTIVE.value:
                return "skipped"
            creds = open_credentials(self._kp, account)
            platform = Platform(account.platform)
        connector = self._connectors.get(platform)
        if connector is None:
            return "skipped"
        try:
            report = await connector.check_health(creds)
        except ReauthRequired as exc:
            await self._needs_reauth(workspace_id, account_id, _safe_reason(exc))
            return "needs_reauth"
        except ConnectorError as exc:
            report_dict: dict[str, Any] = {"token_valid": None, "last_error": _safe_reason(exc)}
        else:
            report_dict = {
                "token_valid": report.token_valid,
                "scopes_ok": report.scopes_ok,
                "rate_remaining": report.rate_remaining,
                "last_error": report.last_error,
            }
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            account = await s.get(SocialAccount, account_id, with_for_update=True)
            if account is None:
                return "skipped"
            account.health = report_dict
            account.last_health_check_at = datetime.now(UTC)
            account.capabilities_snapshot = capabilities_for(platform).model_dump(mode="json")
        return "checked"

    async def _needs_reauth(
        self, workspace_id: uuid.UUID, account_id: uuid.UUID, reason: str
    ) -> None:
        """Only this account stops; its siblings carry on (M3 acceptance). Publishing will
        refuse it via ``assert_publishable`` (account must be active) from M6."""
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            account = await s.get(SocialAccount, account_id, with_for_update=True)
            if account is None or account.status != AccountStatus.ACTIVE.value:
                return
            account.status = AccountStatus.NEEDS_REAUTH.value
            account.status_reason = reason
            account.health = {**account.health, "token_valid": False, "last_error": reason}
            account.last_health_check_at = datetime.now(UTC)
            await s.flush()
            s.add(
                _notification(
                    workspace_id,
                    account,
                    kind="account.needs_reauth",
                    severity="critical",
                    title=f"{account.nickname} needs reconnecting",
                    body=f"{reason}. Posts to this account are on hold; other accounts are "
                    "unaffected.",
                )
            )
            await record(
                s,
                AuditEntry(
                    action="account.needs_reauth",
                    actor_type="system",
                    surface="worker",
                    workspace_id=workspace_id,
                    subject_type="social_account",
                    subject_id=account_id,
                    social_account_id=account_id,
                    after={"reason": reason},
                ),
            )


def open_credentials(kp: KeyProvider, account: SocialAccount) -> dict[str, Any]:
    assert account.credentials_ct is not None
    raw = kp.decrypt(
        account.credentials_ct,
        workspace_id=account.workspace_id,
        purpose=CREDENTIALS_PURPOSE,
        record_id=account.id,
    )
    creds: dict[str, Any] = json.loads(raw)
    return creds


def _refresh_due(account: SocialAccount, now: datetime | None = None) -> bool:
    if account.token_expires_at is None or account.token_issued_at is None:
        return False
    now = now or datetime.now(UTC)
    lifetime = account.token_expires_at - account.token_issued_at
    return now >= account.token_issued_at + lifetime * REFRESH_AT_FRACTION


def _levels(
    ws: Workspace, group: AccountGroup | None, account: SocialAccount | None
) -> dict[Level, dict[str, Any] | None]:
    return {
        Level.WORKSPACE: level_values(ws.settings),
        Level.GROUP: level_values(
            group.settings,
            brand_voice_id=group.default_brand_voice_id,
            posting_times=group.posting_schedule,
            content_pillar_ids=group.content_pillar_ids,
        )
        if group is not None
        else None,
        Level.ACCOUNT: level_values(
            account.settings,
            brand_voice_id=account.default_brand_voice_id,
            posting_times=account.default_posting_times,
        )
        if account is not None
        else None,
    }


async def _groups_by_account(
    s: AsyncSession, account_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[uuid.UUID]]:
    if not account_ids:
        return {}
    rows = (
        await s.execute(
            select(AccountGroupMember.social_account_id, AccountGroupMember.account_group_id).where(
                AccountGroupMember.social_account_id.in_(account_ids)
            )
        )
    ).all()
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for account_id, group_id in rows:
        out.setdefault(account_id, []).append(group_id)
    return out


def _in_scope(principal: Principal, account_id: uuid.UUID, group_ids: list[uuid.UUID]) -> bool:
    scope = principal.member_scope
    if not scope.restricted:
        return True
    return account_id in scope.account_ids or any(g in scope.group_ids for g in group_ids)


def _notification(
    workspace_id: uuid.UUID,
    account: SocialAccount,
    *,
    kind: str,
    severity: str,
    title: str,
    body: str | None,
) -> Notification:
    return Notification(
        id=uuid7(),
        workspace_id=workspace_id,
        kind=kind,
        severity=severity,
        title=title,
        body=body,
        subject_type="social_account",
        subject_id=account.id,
        social_account_id=account.id,
    )


def _view(a: SocialAccount, group_ids: list[uuid.UUID]) -> AccountView:
    return AccountView(
        id=a.id,
        platform=a.platform,
        platform_account_id=a.platform_account_id,
        handle=a.handle,
        display_name=a.display_name,
        nickname=a.nickname,
        avatar_url=a.avatar_url,
        account_type=a.account_type,
        status=a.status,
        status_reason=a.status_reason,
        granted_scopes=list(a.granted_scopes),
        token_expires_at=a.token_expires_at,
        last_health_check_at=a.last_health_check_at,
        health=dict(a.health),
        default_brand_voice_id=a.default_brand_voice_id,
        default_posting_times=a.default_posting_times,
        settings=dict(a.settings),
        group_ids=group_ids,
        connected_at=a.connected_at,
    )


def _visible_groups(principal: Principal, group_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    scope = principal.member_scope
    return group_ids if not scope.restricted else [g for g in group_ids if g in scope.group_ids]


def _identity_json(i: Any) -> dict[str, Any]:
    return {
        "platform_account_id": i.platform_account_id,
        "handle": i.handle,
        "display_name": i.display_name,
        "avatar_url": i.avatar_url,
        "account_type": i.account_type.value,
        "credentials": i.credentials,
        "granted_scopes": i.granted_scopes,
        "expires_at": i.expires_at.isoformat() if i.expires_at else None,
        # Every Page the login granted, when there are several (P2-M1.5).
        "choices": [_identity_json(c) for c in getattr(i, "choices", ())],
    }


def _safe_url(url: Any) -> str | None:
    return url if isinstance(url, str) and url.startswith("https://") and len(url) < 2048 else None


def _safe_reason(exc: Exception) -> str:
    """Platform error text can carry request details; keep a short, single-line summary of
    the exception's own message only (review L10). Real connectors must still never put
    tokens in exception messages (tested per adapter in M8)."""
    text = " ".join(str(exc).split())
    return text[:200] or type(exc).__name__


def _with(url: str, **params: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"


def _invalid_connect_code() -> NotFoundError:
    return NotFoundError(
        "This connection link has expired or belongs to someone else. Start again.",
        code="invalid_connect_code",
    )
