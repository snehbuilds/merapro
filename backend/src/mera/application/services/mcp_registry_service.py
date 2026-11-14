"""The MCP client registry (P2-M9, docs/05 §2): which external servers agents may use, how.

The guarantees, in the order a server earns them:

1. **Registered is not usable.** Every entry point (form, ``mcpServers`` JSON, an agent's
   proposal over MCP) lands in ``pending_review`` with every tool disabled. Only an Admin
   with a second factor (``mcp_server_approve``) makes it ``active``.
2. **Discovery is not trust.** Annotations are hints: an uncurated server's tools are
   ``write`` (``destructive`` when it says so) whatever they claim, so they default to
   ``always`` ask. Descriptions are screened for tool poisoning; a flagged tool cannot be
   enabled until an Admin acknowledges the warning. A changed schema disables the tool.
3. **Three levels, default deny.** An agent sees a tool only if it is enabled on an active
   registration, the registration's scope covers the run's group, and a binding names that
   agent. The Publisher and the Approval agent can never be bound.
4. **Writes wait for a person.** A call under ``always`` becomes an ``mcp_tool_call`` card
   showing the exact arguments, bound by a hash over registration, tool, schema hash and
   canonical arguments. Expiry or rejection never executes; the agent gets a tool error.
   ``auto`` on a write tool takes a second factor (``mcp_tool_auto``).
5. **Results are untrusted.** They are returned to agents inside an
   ``<untrusted_tool_result>`` block, size-capped.
6. **Disable is instant**: no second factor, pending calls cancelled, the sandbox stopped.

stdio servers run only in ``mcp-sandbox``, from a pinned package, with egress through the
proxy and the registration's allowlist; shell wrappers are refused at import.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlencode, urlsplit

import httpx
import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application import second_factor
from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, InvalidInput, NotFoundError
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import acting_user, load_user_principal
from mera.config import Settings
from mera.domain.context import WorkspaceContext
from mera.domain.errors import PermissionDenied
from mera.domain.policies import rbac
from mera.domain.policies.rbac import Permission
from mera.domain.ports.key_provider import KeyProvider
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.crypto.api_keys import hash_key
from mera.infrastructure.db.models import (
    McpAgentBinding,
    McpOAuthFlow,
    McpServer,
    McpTool,
    McpToolCall,
    User,
)
from mera.infrastructure.db.repositories.audit import AuditEntry, record
from mera.infrastructure.db.session import secret_lookup_transaction, workspace_transaction
from mera.infrastructure.http.safe_fetch import FetchError, guarded_transport, validate_public_url
from mera.infrastructure.mcp_client import (
    HttpTransport,
    McpClient,
    McpClientError,
    SandboxTransport,
    ToolInfo,
)
from mera.infrastructure.queue.deferrer import Deferrer

log = structlog.get_logger(__name__)

CREDS_PURPOSE: Final = "mcp_server.credentials"
RISK_ORDER: Final = {"read": 0, "write": 1, "destructive": 2}
MAX_SOURCE_ARGS: Final = 4096  # bytes of JSON arguments for an mcp source
BREAKER_FAILURES: Final = 5  # P3-M6.3
BREAKER_OPEN: Final = timedelta(seconds=60)
DEFAULT_CALLS_PER_RUN: Final = 20  # P3-M6.2
DEFAULT_CALLS_PER_DAY: Final = 200
FLOW_PURPOSE: Final = "mcp_server.oauth_flow"
CALL_TTL: Final = timedelta(minutes=30)
FLOW_TTL: Final = timedelta(minutes=10)
RESULT_CAP: Final = 20_000
NEVER_BIND: Final = frozenset({"publisher", "approval"})
AGENTS: Final = frozenset(
    {"research", "writer", "editor", "hashtag", "media", "engagement", "scheduler", "alt_text"}
)
EXEC_TASK: Final = "mcp:execute_call"
DISPATCH_TASK: Final = "approvals:dispatch"

PACKAGE: Final = re.compile(
    r"^(npm:(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*@\d+\.\d+\.\d+([-+][\w.-]+)?"
    r"|pypi:[A-Za-z0-9][\w.-]*==\d+(\.\d+)*([a-z0-9.+-]*)?"
    r"|oci:[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64})$"
)
SHELL: Final = re.compile(r"^(bash|sh|zsh|dash|cmd|powershell|pwsh)(\.exe)?$|[|;&`$<>]|^-c$", re.I)
POISON: Final = re.compile(
    r"ignore (all |any |the )?(previous|prior|above)|you must always|system prompt"
    r"|do not (tell|mention|reveal)|<important>|before using (this|any) tool"
    r"|(call|use|invoke) (the )?other tools?"
    r"|(send|include|return|read|reveal|forward)(s|ing)?\s+(your |the |any )?"
    r"(api[_ ]?keys?|passwords?|secrets?|credentials?|access tokens?)"
    r"|exfiltrat|send .{0,40} to https?://",
    re.I,
)
SLUG: Final = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


def schema_hash(t: ToolInfo) -> bytes:
    canon = json.dumps(
        {"n": t.name, "d": t.description, "i": t.input_schema, "o": t.output_schema},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canon.encode()).digest()


def classify(t: ToolInfo, curated: bool) -> tuple[str, str]:
    """(risk_class, approval_policy). Annotations count only for curated servers."""
    ann = t.annotations or {}
    if ann.get("destructiveHint"):
        risk = "destructive"
    elif curated and ann.get("readOnlyHint"):
        risk = "read"
    else:
        risk = "write"
    return risk, "auto" if risk == "read" else "always"


def screen(info: ToolInfo) -> str | None:
    """The tool's description and every argument's description (P3-M6.4)."""
    found = poisoned(info.description)
    if found:
        return found
    props = (info.input_schema or {}).get("properties") or {}
    for name, spec in props.items() if isinstance(props, dict) else []:
        desc = spec.get("description") if isinstance(spec, dict) else None
        if isinstance(desc, str) and (hit := poisoned(desc)):
            return f"argument {name}: {hit}"
    return None


def poisoned(description: str) -> str | None:
    m = POISON.search(description or "")
    return (
        f"The description contains “{m.group(0)}”, which looks like an instruction to the model."
        if m
        else None
    )


def call_hash(server_id: uuid.UUID, tool: str, schema: bytes, arguments: dict[str, Any]) -> bytes:
    canon = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"mcpcall|{server_id}|{tool}|{schema.hex()}|{canon}".encode()).digest()


def exposed_name(slug: str, tool: str) -> str:
    """``{slug}__{tool}``, provider-safe, at most 64 characters (docs/05 §2.8)."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", f"{slug}__{tool}")
    if len(name) <= 64:
        return name
    return name[:55] + "_" + hashlib.sha256(name.encode()).hexdigest()[:8]


def untrusted(result: dict[str, Any]) -> str:
    parts = []
    for c in result.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(str(c.get("text") or ""))
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], default=str))
    text = neutralise("\n".join(parts)[:RESULT_CAP])
    return f"<untrusted_tool_result>\n{text}\n</untrusted_tool_result>"


_WRAPPER = re.compile(r"<\s*/?\s*untrusted_tool_result[^>]*>", re.IGNORECASE)


def neutralise(text: str) -> str:
    """The server cannot close the wrapper and write outside it (security review #3)."""
    return _WRAPPER.sub("[tag removed]", text)


@dataclass(frozen=True, slots=True)
class CallOutcome:
    state: str  # succeeded | pending_approval | failed | refused
    call_id: uuid.UUID | None
    text: str  # for the agent: an untrusted result, or why not


class McpRegistryService(WorkspaceScopedService):
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        settings: Settings,
        key_provider: KeyProvider,
        deferrer: Deferrer | None,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
        sandbox_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(sm)
        self._settings = settings
        self._kp = key_provider
        self._deferrer = deferrer
        self._http_t = http_transport
        self._sandbox_t = sandbox_transport

    # ------------------------------------------------------------- helpers

    def _creds(self, server: McpServer) -> dict[str, Any]:
        if not server.credentials_ct:
            return {}
        raw = self._kp.decrypt(
            server.credentials_ct,
            workspace_id=server.workspace_id,
            purpose=CREDS_PURPOSE,
            record_id=server.id,
        )
        out: dict[str, Any] = json.loads(raw)
        return out

    def _seal(self, server: McpServer, creds: dict[str, Any]) -> str | None:
        if not creds:
            return None
        return self._kp.encrypt(
            json.dumps(creds).encode(),
            workspace_id=server.workspace_id,
            purpose=CREDS_PURPOSE,
            record_id=server.id,
        )

    # ------------------------------------------------- the sandbox and its egress

    def _relay_token(self) -> str | None:
        tok = self._settings.mcp_sandbox_token
        return tok.get_secret_value() if tok is not None else None

    def _internal(self, timeout: float) -> httpx.AsyncClient:
        """For mcp-sandbox and mcp-egress: internal services, never a proxy from the env."""
        return httpx.AsyncClient(timeout=timeout, transport=self._sandbox_t, trust_env=False)

    def egress_token(self, server_id: uuid.UUID) -> str | None:
        """The proxy credential for one server: derived, so the API and every worker agree
        on it without storing it, and useless for any other server's allowlist."""
        admin = self._settings.mcp_egress_admin_token
        if admin is None:
            return None
        mac = hmac.new(
            admin.get_secret_value().encode(), f"mcp-egress:{server_id}".encode(), hashlib.sha256
        )
        return mac.hexdigest()[:48]

    async def _grant_egress(self, server: McpServer) -> str | None:
        """Registers the server's own allowlist with mcp-egress and returns the proxy URL
        it is started with. None: no egress at all (the sandbox network has no other way
        out), because the allowlist is empty or egress is not configured."""
        hosts = sorted(server.egress_allowlist)
        token = self.egress_token(server.id)
        admin = self._settings.mcp_egress_admin_token
        if not hosts or token is None or admin is None:
            return None
        url = f"{self._settings.mcp_egress_admin_url.rstrip('/')}/grants/{server.id}"
        try:
            async with self._internal(10) as c:
                r = await c.put(
                    url,
                    json={"token": token, "hosts": hosts},
                    headers={"X-Egress-Admin": admin.get_secret_value()},
                )
        except httpx.HTTPError as exc:
            raise McpClientError(f"the egress proxy is unavailable ({type(exc).__name__})") from exc
        if r.status_code >= 400:
            raise McpClientError(f"the egress proxy refused the grant ({r.status_code})")
        return f"http://{server.id}:{token}@{self._settings.mcp_egress_proxy}"

    async def _revoke_egress(self, server_id: uuid.UUID) -> None:
        admin = self._settings.mcp_egress_admin_token
        if admin is None:
            return
        try:
            async with self._internal(5) as c:
                await c.delete(
                    f"{self._settings.mcp_egress_admin_url.rstrip('/')}/grants/{server_id}",
                    headers={"X-Egress-Admin": admin.get_secret_value()},
                )
        except httpx.HTTPError:
            log.warning("mcp.egress_revoke_failed", server=str(server_id))

    async def ensure_started(self, server: McpServer) -> None:
        """A stdio server is started in ``mcp-sandbox`` (idempotent), with its decrypted
        environment and a proxy URL that works for its own allowlist only; credentials
        never touch disk. Re-registering the grant each time heals an egress restart."""
        if server.transport != "stdio":
            return
        token = self._relay_token()
        if token is None:
            raise McpClientError("the sandbox is not configured (MCP_SANDBOX_TOKEN)")
        body = {
            "package": server.package,
            "args": list(server.args),
            "env": dict(self._creds(server).get("env") or {}),
            "egress_proxy": await self._grant_egress(server),
        }
        try:
            async with self._internal(120) as c:
                r = await c.post(
                    f"{self._settings.mcp_sandbox_url.rstrip('/')}/servers/{server.id}/start",
                    json=body,
                    headers={"X-Sandbox-Token": token},
                )
        except httpx.HTTPError as exc:
            raise McpClientError(f"the sandbox is unavailable ({type(exc).__name__})") from exc
        if r.status_code >= 400:
            raise McpClientError(f"the sandbox refused to start the server ({r.status_code})")

    async def stop_sandboxed(self, server_id: uuid.UUID) -> None:
        """The incident path: the process stopped and its egress grant revoked."""
        token = self._relay_token()
        if token is not None:
            try:
                async with self._internal(5) as c:
                    await c.post(
                        f"{self._settings.mcp_sandbox_url.rstrip('/')}/servers/{server_id}/stop",
                        headers={"X-Sandbox-Token": token},
                    )
            except httpx.HTTPError:
                log.warning("mcp.sandbox_stop_failed", server=str(server_id))
        await self._revoke_egress(server_id)

    def client_for(self, server: McpServer) -> McpClient:
        if server.transport == "stdio":
            return McpClient(
                SandboxTransport(
                    self._settings.mcp_sandbox_url,
                    str(server.id),
                    token=self._relay_token(),
                    transport=self._sandbox_t,
                ),
                era=server.protocol_era,
            )
        creds = self._creds(server)
        headers: dict[str, str] = {}
        if server.auth_method == "bearer" and creds.get("token"):
            headers["Authorization"] = f"Bearer {creds['token']}"
        elif server.auth_method == "headers":
            headers.update({str(k): str(v) for k, v in (creds.get("headers") or {}).items()})
        elif server.auth_method == "oauth" and server.oauth_issuer:
            # Keyed by issuer: a token is only ever sent to the server it was issued for.
            tok = ((creds.get("oauth") or {}).get(server.oauth_issuer) or {}).get("access_token")
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
        assert server.url is not None
        return McpClient(
            HttpTransport(
                server.url,
                headers,
                transport=self._http_t,
                allow_private=self._settings.sandbox_mode,
            ),
            era=server.protocol_era,
        )

    async def _admin(
        self, s: AsyncSession, user: AuthenticatedUser, workspace_id: uuid.UUID
    ) -> None:
        principal = await load_user_principal(s, user, workspace_id)
        rbac.require(principal, Permission.MANAGE_MCP, workspace_id)

    async def _server(
        self, s: AsyncSession, server_id: uuid.UUID, *, lock: bool = False
    ) -> McpServer:
        srv = await s.get(McpServer, server_id, with_for_update=lock)
        if srv is None:
            raise NotFoundError("Server not found.")
        return srv

    # ---------------------------------------------------------- registering

    def _validate(
        self, *, slug: str, transport: str, url: str | None, package: str | None, args: list[str]
    ) -> None:
        if not SLUG.match(slug):
            raise InvalidInput(
                "A slug is 2-31 lowercase letters, digits or underscores.",
                code="invalid_slug",
                field="slug",
            )
        if transport == "http":
            if not url:
                raise InvalidInput("Give the server's URL.", code="url_required", field="url")
            parts = urlsplit(url)
            if parts.scheme != "https" and not (
                self._settings.sandbox_mode and parts.scheme == "http"
            ):
                raise InvalidInput(
                    "Remote servers must use https.", code="invalid_url", field="url"
                )
            if not self._settings.sandbox_mode and (problem := validate_public_url(url)):
                raise InvalidInput(
                    f"That URL is not allowed: {problem}.", code="invalid_url", field="url"
                )
        elif transport == "stdio":
            if not package or not PACKAGE.match(package):
                raise InvalidInput(
                    "A stdio server is a pinned package: npm:name@1.2.3, pypi:name==1.2.3 or "
                    "oci:image@sha256:… Unpinned versions and scripts are refused.",
                    code="unpinned_package",
                    field="package",
                )
        else:
            raise InvalidInput("Transport is http or stdio.", code="invalid_transport")
        if any(SHELL.search(a) for a in args):
            raise InvalidInput(
                "Shell wrappers and pipes are refused.", code="shell_refused", field="args"
            )

    async def register(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        slug: str,
        name: str,
        transport: str,
        url: str | None = None,
        package: str | None = None,
        args: list[str] | None = None,
        auth_method: str = "none",
        credentials: dict[str, Any] | None = None,
        egress_allowlist: list[str] | None = None,
        scope_group_ids: list[uuid.UUID] | None = None,
        added_via: str = "form",
        need_admin: bool = True,
    ) -> McpServer:
        args = [str(a)[:200] for a in (args or [])][:30]
        self._validate(slug=slug, transport=transport, url=url, package=package, args=args)
        hosts = sorted({h.strip().lower() for h in egress_allowlist or [] if h.strip()})
        if any(not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", h) for h in hosts):
            raise InvalidInput(
                "The egress allowlist takes hostnames only.", code="invalid_allowlist"
            )
        async with self._tx(user, workspace_id, meta) as s:
            if need_admin:
                await self._admin(s, user, workspace_id)
            else:
                principal = await load_user_principal(s, user, workspace_id)
                rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
            if await s.scalar(select(McpServer.id).where(McpServer.slug == slug)):
                raise ConflictError("That slug is taken in this workspace.", code="slug_taken")
            srv = McpServer(
                id=uuid7(),
                workspace_id=workspace_id,
                slug=slug,
                name=name.strip()[:120] or slug,
                transport=transport,
                url=url if transport == "http" else None,
                package=package if transport == "stdio" else None,
                args=args,
                auth_method=auth_method,
                egress_allowlist=hosts,
                scope_group_ids=list(scope_group_ids or []),
                added_via=added_via,
                state="pending_review",
                created_by_user_id=user.user_id,
            )
            srv.credentials_ct = self._seal(srv, credentials or {})
            s.add(srv)
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "mcp_server.registered",
                    workspace_id=workspace_id,
                    subject_type="mcp_server",
                    subject_id=srv.id,
                    after={"slug": slug, "transport": transport, "via": added_via},
                ),
            )
            await s.refresh(srv)
            return srv

    async def import_json(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        config: dict[str, Any],
    ) -> list[McpServer]:
        """The ``mcpServers`` object Claude Desktop uses. ``env`` and ``headers`` values move
        to encrypted credentials; a command must be a pinned package runner, not a shell."""
        servers = config.get("mcpServers") if isinstance(config, dict) else None
        if not isinstance(servers, dict) or not servers:
            raise InvalidInput("Paste an object with mcpServers.", code="invalid_config")
        out = []
        for raw_name, entry in list(servers.items())[:20]:
            if not isinstance(entry, dict):
                raise InvalidInput(f"{raw_name}: not an object.", code="invalid_config")
            slug = re.sub(r"[^a-z0-9_]", "_", str(raw_name).lower()).strip("_")[:31] or "server"
            if not slug[0].isalpha():
                slug = "s_" + slug[:29]
            if entry.get("url"):
                headers = {str(k): str(v) for k, v in (entry.get("headers") or {}).items()}
                out.append(
                    await self.register(
                        user,
                        workspace_id,
                        meta,
                        slug=slug,
                        name=str(raw_name),
                        transport="http",
                        url=str(entry["url"]),
                        auth_method="headers" if headers else "none",
                        credentials={"headers": headers} if headers else None,
                        added_via="json",
                    )
                )
                continue
            command = str(entry.get("command") or "")
            args = [str(a) for a in entry.get("args") or []]
            if SHELL.search(command) or any(SHELL.search(a) for a in args):
                raise InvalidInput(f"{raw_name}: shell wrappers are refused.", code="shell_refused")
            package = self._package_from(command, args)
            env = {str(k): str(v) for k, v in (entry.get("env") or {}).items()}
            out.append(
                await self.register(
                    user,
                    workspace_id,
                    meta,
                    slug=slug,
                    name=str(raw_name),
                    transport="stdio",
                    package=package,
                    args=[
                        a
                        for a in args
                        if not a.startswith("-y") and package.split(":", 1)[1] not in a
                    ],
                    auth_method="env" if env else "none",
                    credentials={"env": env} if env else None,
                    added_via="json",
                )
            )
        return out

    @staticmethod
    def _package_from(command: str, args: list[str]) -> str:
        base = command.rsplit("/", 1)[-1].lower()
        spec = next((a for a in args if not a.startswith("-")), "")
        if base in ("npx", "npx.cmd"):
            pkg = f"npm:{spec}"
        elif base in ("uvx", "pipx"):
            pkg = f"pypi:{spec}"
        elif base == "docker":
            pkg = f"oci:{args[-1] if args else ''}"
        else:
            raise InvalidInput(
                f"{command}: only npx, uvx and docker packages can be imported.",
                code="unpinned_package",
            )
        if not PACKAGE.match(pkg):
            raise InvalidInput(
                f"{spec or command}: pin an exact version "
                "(name@1.2.3, name==1.2.3, image@sha256:…).",
                code="unpinned_package",
            )
        return pkg

    # ------------------------------------------------------------ discovery

    async def discover(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        server_id: uuid.UUID,
    ) -> McpServer:
        async with self._tx(user, workspace_id, meta) as s:
            await self._admin(s, user, workspace_id)
            srv = await self._server(s, server_id)
            if srv.state in ("disabled", "rejected"):
                raise ConflictError("This server is disabled.", code="server_disabled")
        return await self._discover(workspace_id, server_id)

    async def _discover(self, workspace_id: uuid.UUID, server_id: uuid.UUID) -> McpServer:
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            srv = await self._server(s, server_id)
            client = self.client_for(srv)
        try:
            await self.ensure_started(srv)
            probe = await client.probe()
        except McpClientError as exc:
            async with workspace_transaction(self._sm, ctx) as s:
                srv = await self._server(s, server_id, lock=True)
                srv.last_error = str(exc)[:500]
                if srv.state == "active":
                    srv.state = "degraded"
                await s.flush()
                await s.refresh(srv)
                return srv
        async with workspace_transaction(self._sm, ctx) as s:
            srv = await self._server(s, server_id, lock=True)
            srv.protocol_era, srv.protocol_version = probe.era, probe.version
            srv.last_discovered_at, srv.last_error = datetime.now(UTC), None
            if srv.state == "degraded":
                srv.state = "active"
            existing = {
                t.name: t
                for t in (
                    await s.execute(select(McpTool).where(McpTool.server_id == server_id))
                ).scalars()
            }
            seen = set()
            for info in probe.tools:
                seen.add(info.name)
                h = schema_hash(info)
                risk, policy = classify(info, srv.curated)
                row = existing.get(info.name)
                if row is None:
                    s.add(
                        McpTool(
                            id=uuid7(),
                            workspace_id=workspace_id,
                            server_id=server_id,
                            name=info.name,
                            title=info.title,
                            description=info.description,
                            input_schema=info.input_schema,
                            output_schema=info.output_schema,
                            annotations=info.annotations,
                            schema_hash=h,
                            risk_class=risk,
                            approval_policy=policy,
                            enabled=False,
                            flag=screen(info),
                        )
                    )
                elif row.schema_hash != h:
                    # A redefined tool (the rug pull): disabled until reviewed again.
                    row.description, row.input_schema = info.description, info.input_schema
                    row.output_schema, row.annotations, row.schema_hash = (
                        info.output_schema,
                        info.annotations,
                        h,
                    )
                    row.enabled, row.presence = False, "changed"
                    row.flag, row.flag_acknowledged = screen(info), False
                    # Trust is not inherited by a redefinition (security review #5).
                    row.risk_class, row.approval_policy = risk, policy
                    row.updated_at = datetime.now(UTC)
                elif row.presence != "present":
                    row.presence = "present"
            for name, row in existing.items():
                if name not in seen:
                    row.presence, row.enabled = "gone", False
            await s.flush()
            await s.refresh(srv)
            return srv

    # -------------------------------------------------------------- review

    async def approve(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        server_id: uuid.UUID,
    ) -> McpServer:
        """An Admin, with a second factor, makes the registration usable (tools still each
        need enabling and binding)."""
        self._require_human(user)
        async with self._tx(user, workspace_id, meta) as s:
            await self._admin(s, user, workspace_id)
            srv = await self._server(s, server_id, lock=True)
            if srv.state not in ("pending_review", "rejected"):
                raise ConflictError(
                    "Only a server waiting for review can be approved.", code="not_pending"
                )
            if srv.last_discovered_at is None:
                raise ConflictError(
                    "Test the connection first, so its tools can be reviewed.",
                    code="not_discovered",
                )
            await second_factor.require(s, user, workspace_id, "mcp_server_approve", meta)
            srv.state, srv.approved_by_user_id, srv.approved_at = (
                "active",
                user.user_id,
                datetime.now(UTC),
            )
            srv.updated_at = datetime.now(UTC)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "mcp_server.approved",
                    workspace_id=workspace_id,
                    subject_type="mcp_server",
                    subject_id=srv.id,
                ),
            )
            await s.flush()
            await s.refresh(srv)
            return srv

    async def reject(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        server_id: uuid.UUID,
    ) -> McpServer:
        async with self._tx(user, workspace_id, meta) as s:
            await self._admin(s, user, workspace_id)
            srv = await self._server(s, server_id, lock=True)
            srv.state, srv.updated_at = "rejected", datetime.now(UTC)
            await s.flush()
            await s.refresh(srv)
            return srv

    async def disable(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        server_id: uuid.UUID,
    ) -> McpServer:
        """The per-server kill switch (docs/09 incident table): instant, no second factor,
        pending calls cancelled, the sandbox stopped."""
        async with self._tx(user, workspace_id, meta) as s:
            await self._admin(s, user, workspace_id)
            srv = await self._server(s, server_id, lock=True)
            now = datetime.now(UTC)
            srv.state, srv.disabled_at, srv.updated_at = "disabled", now, now
            cancelled = await s.execute(
                update(McpToolCall)
                .where(
                    McpToolCall.server_id == server_id,
                    McpToolCall.state.in_(("pending_approval", "approved")),
                )
                .values(state="cancelled", finished_at=now, error="the server was disabled")
            )
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "mcp_server.disabled",
                    workspace_id=workspace_id,
                    subject_type="mcp_server",
                    subject_id=srv.id,
                    after={"cancelled_calls": getattr(cancelled, "rowcount", 0)},
                ),
            )
            await s.flush()
            await s.refresh(srv)
            transport = srv.transport
        if transport == "stdio":
            await self.stop_sandboxed(server_id)
        return srv

    async def update_tool(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        tool_id: uuid.UUID,
        *,
        enabled: bool | None = None,
        risk_class: str | None = None,
        approval_policy: str | None = None,
        acknowledge_flag: bool | None = None,
    ) -> McpTool:
        async with self._tx(user, workspace_id, meta) as s:
            await self._admin(s, user, workspace_id)
            tool = await s.get(McpTool, tool_id, with_for_update=True)
            if tool is None:
                raise NotFoundError("Tool not found.")
            confirmed = False  # a grant is single use: one per change, however many fields
            if risk_class is not None:
                if risk_class not in RISK_ORDER:
                    raise InvalidInput("Unknown risk class.", code="invalid_risk")
                if RISK_ORDER[risk_class] < RISK_ORDER.get(tool.risk_class, 1):
                    # Lowering a tool's risk is the way round the rule below (mark a write
                    # "read", then "auto"), so it takes the second factor itself (review #9).
                    await second_factor.require(s, user, workspace_id, "mcp_tool_auto", meta)
                    confirmed = True
                tool.risk_class = risk_class
            if approval_policy is not None:
                if approval_policy not in ("always", "auto", "blocked"):
                    raise InvalidInput("Unknown policy.", code="invalid_policy")
                if approval_policy == "auto" and tool.risk_class != "read" and not confirmed:
                    # No human in the loop for a write: an Admin and a second factor.
                    await second_factor.require(s, user, workspace_id, "mcp_tool_auto", meta)
                tool.approval_policy = approval_policy
            if acknowledge_flag:
                tool.flag_acknowledged = True
            if enabled is not None:
                if enabled and tool.flag and not tool.flag_acknowledged:
                    raise ConflictError(
                        "Acknowledge the warning on this tool first.", code="tool_flagged"
                    )
                if enabled and tool.presence == "gone":
                    raise ConflictError("The server no longer offers this tool.", code="tool_gone")
                tool.enabled = enabled
                if enabled:
                    tool.presence = "present"
            tool.updated_at = datetime.now(UTC)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "mcp_tool.updated",
                    workspace_id=workspace_id,
                    subject_type="mcp_tool",
                    subject_id=tool.id,
                    after={
                        "enabled": tool.enabled,
                        "risk": tool.risk_class,
                        "policy": tool.approval_policy,
                    },
                ),
            )
            await s.flush()
            await s.refresh(tool)
            return tool

    async def set_bindings(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        tool_id: uuid.UUID,
        agent_keys: list[str],
        *,
        max_calls_per_run: int | None = None,
        max_calls_per_day: int | None = None,
    ) -> list[str]:
        keys = sorted(set(agent_keys))
        bad = [k for k in keys if k in NEVER_BIND]
        if bad:
            raise InvalidInput(
                f"The {bad[0]} agent can never use external tools.", code="unbindable_agent"
            )
        unknown = [k for k in keys if k not in AGENTS]
        if unknown:
            raise InvalidInput(f"Unknown agent: {unknown[0]}.", code="unknown_agent")
        async with self._tx(user, workspace_id, meta) as s:
            await self._admin(s, user, workspace_id)
            if await s.get(McpTool, tool_id) is None:
                raise NotFoundError("Tool not found.")
            current = list(
                (
                    await s.execute(
                        select(McpAgentBinding).where(McpAgentBinding.tool_id == tool_id)
                    )
                ).scalars()
            )
            for b in current:
                if b.agent_key not in keys:
                    await s.delete(b)
            have = {c.agent_key: c for c in current}
            for k in keys:
                found = have.get(k)
                if found is None:
                    found = McpAgentBinding(
                        id=uuid7(), workspace_id=workspace_id, tool_id=tool_id, agent_key=k
                    )
                    s.add(found)
                # P3-M6.2: the budget applies to every agent bound to this tool.
                found.max_calls_per_run = max_calls_per_run
                found.max_calls_per_day = max_calls_per_day
        return keys

    # ---------------------------------------------------------------- catalog (P3-M6.1)

    async def catalog(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, q: str | None
    ) -> list[dict[str, Any]]:
        from mera.application import mcp_catalog

        async with self._tx(user, workspace_id, meta) as s:
            await self._admin(s, user, workspace_id)
        entries = mcp_catalog.curated(q)
        entries += await self._registry_search(q)
        return [e.view() for e in entries]

    async def install(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        name: str,
        *,
        slug: str | None = None,
        credentials: dict[str, Any] | None = None,
    ) -> McpServer:
        """From the catalog to ``pending_review``: the same review as any other server."""
        from mera.application import mcp_catalog

        entry = mcp_catalog.find(name)
        if entry is None:
            entry = next((e for e in await self._registry_search(name) if e.name == name), None)
        if entry is None:
            raise NotFoundError("That server is not in the catalog.")
        return await self.register(
            user,
            workspace_id,
            meta,
            slug=slug or mcp_catalog.slug_for(entry.name),
            name=entry.title,
            transport=entry.transport,
            url=entry.url,
            package=entry.package,
            auth_method=entry.auth_method,
            credentials=credentials,
            egress_allowlist=list(entry.egress_allowlist),
            added_via="catalog",
        )

    async def _registry_search(self, q: str | None) -> list[Any]:
        from mera.application import mcp_catalog
        from mera.infrastructure.http.safe_fetch import FetchError, SafeFetcher

        base = self._settings.mcp_registry_search_url
        if not base or not q or len(q.strip()) < 2:
            return []
        url = f"{base}?{urlencode({'search': q.strip()[:80], 'limit': 30})}"
        try:
            res = await SafeFetcher(
                user_agent="MeraBot/1.0 (mcp catalog)",
                system_trust_store=self._settings.http_system_trust_store,
            ).get(url, headers={"Accept": "application/json"}, max_bytes=1_000_000)
        except FetchError as exc:
            log.info("mcp.catalog_search_failed", kind=exc.kind)
            return []
        return mcp_catalog.from_registry(res.body)

    # ---------------------------------------------------------------- reads

    async def list_servers(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta
    ) -> list[dict[str, Any]]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            servers = list(
                (await s.execute(select(McpServer).order_by(McpServer.created_at))).scalars()
            )
            tools = list((await s.execute(select(McpTool).order_by(McpTool.name))).scalars())
            bindings = list((await s.execute(select(McpAgentBinding))).scalars())
        by_tool: dict[uuid.UUID, list[str]] = {}
        budget: dict[uuid.UUID, tuple[int | None, int | None]] = {}
        for b in bindings:
            by_tool.setdefault(b.tool_id, []).append(b.agent_key)
            budget[b.tool_id] = (b.max_calls_per_run, b.max_calls_per_day)
        return [
            {
                **_server_view(srv),
                "tools": [
                    {
                        **_tool_view(t),
                        "agents": sorted(by_tool.get(t.id, [])),
                        "max_calls_per_run": budget.get(t.id, (None, None))[0],
                        "max_calls_per_day": budget.get(t.id, (None, None))[1],
                        "exposed_name": exposed_name(srv.slug, t.name),
                    }
                    for t in tools
                    if t.server_id == srv.id
                ],
            }
            for srv in servers
        ]

    async def list_calls(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        state: str | None = None,
    ) -> list[McpToolCall]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            q = select(McpToolCall).order_by(McpToolCall.created_at.desc()).limit(100)
            if state:
                q = q.where(McpToolCall.state == state)
            return list((await s.execute(q)).scalars())

    # ------------------------------------------------------------- the gate

    async def effective_tools(
        self, workspace_id: uuid.UUID, agent_key: str, group_ids: list[uuid.UUID]
    ) -> list[tuple[str, McpServer, McpTool]]:
        """What an agent step may call, computed at the start of the step (docs/05 §2.6)."""
        if agent_key in NEVER_BIND:
            return []
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            rows = (
                await s.execute(
                    select(McpServer, McpTool)
                    .join(McpTool, McpTool.server_id == McpServer.id)
                    .join(McpAgentBinding, McpAgentBinding.tool_id == McpTool.id)
                    .where(
                        McpServer.state == "active",
                        McpTool.enabled.is_(True),
                        McpTool.presence == "present",
                        McpTool.approval_policy != "blocked",
                        McpAgentBinding.agent_key == agent_key,
                    )
                )
            ).all()
        out = []
        for srv, tool in rows:
            if srv.scope_group_ids and not set(srv.scope_group_ids) & set(group_ids):
                continue  # scoped to other groups: invisible to this run
            out.append((exposed_name(srv.slug, tool.name), srv, tool))
        return out

    async def request_call(
        self,
        workspace_id: uuid.UUID,
        *,
        agent_key: str,
        group_ids: list[uuid.UUID],
        name: str,
        arguments: dict[str, Any],
        run_id: uuid.UUID | None = None,
    ) -> CallOutcome:
        allowed = {
            n: (srv, t)
            for n, srv, t in await self.effective_tools(workspace_id, agent_key, group_ids)
        }
        if name not in allowed:
            return CallOutcome("refused", None, "That tool is not available to this agent.")
        srv, tool = allowed[name]
        over = await self._over_budget(workspace_id, agent_key, tool.id, run_id)
        if over:
            return CallOutcome("refused", None, over)
        h = call_hash(srv.id, tool.name, tool.schema_hash, arguments)
        auto = tool.approval_policy == "auto"
        call_id = uuid7()
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            s.add(
                McpToolCall(
                    id=call_id,
                    workspace_id=workspace_id,
                    server_id=srv.id,
                    tool_id=tool.id,
                    tool_name=tool.name,
                    arguments=arguments,
                    content_hash=h,
                    agent_key=agent_key,
                    agent_run_id=run_id,
                    state="approved" if auto else "pending_approval",
                    expires_at=datetime.now(UTC) + CALL_TTL,
                )
            )
        if auto:
            return await self.execute_call(workspace_id, call_id)
        if self._deferrer is not None:
            await self._deferrer.defer(
                DISPATCH_TASK,
                queue="approvals",
                queueing_lock=f"mcp-card:{call_id}",
                kind="mcp_call",
                workspace_id=str(workspace_id),
                subject_id=str(call_id),
            )
        return CallOutcome("pending_approval", call_id, "This call waits for a person to allow it.")

    async def _decide_locked(
        self, s: AsyncSession, call: McpToolCall, user_id: uuid.UUID, allow: bool
    ) -> None:
        now = datetime.now(UTC)
        if call.state != "pending_approval":
            raise ConflictError("This call was already decided.", code="call_closed")
        if call.expires_at <= now:
            call.state = "expired"
            raise ConflictError("This call expired.", code="call_expired")
        call.state = "approved" if allow else "rejected"
        call.decided_by_user_id, call.decided_at = user_id, now
        if not allow:
            call.finished_at = now

    async def decide_call(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        call_id: uuid.UUID,
        *,
        allow: bool,
        content_hash: str,
    ) -> McpToolCall:
        self._require_human(user)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.APPROVE, workspace_id)
            call = await s.get(McpToolCall, call_id, with_for_update=True)
            if call is None:
                raise NotFoundError("Call not found.")
            if content_hash.lower() != call.content_hash.hex():
                raise ConflictError("The call changed since you opened it.", code="stale_hash")
            await self._decide_locked(s, call, user.user_id, allow)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "mcp_tool_call.allowed" if allow else "mcp_tool_call.denied",
                    workspace_id=workspace_id,
                    subject_type="mcp_tool_call",
                    subject_id=call.id,
                    subject_content_hash=call.content_hash,
                ),
            )
            await s.flush()
            await s.refresh(call)
        if allow and self._deferrer is not None:
            await self._deferrer.defer(
                EXEC_TASK,
                queue="integrations",
                queueing_lock=f"mcp-exec:{call_id}",
                workspace_id=str(workspace_id),
                call_id=str(call_id),
            )
        return call

    async def decide_from_channel(
        self,
        workspace_id: uuid.UUID,
        call_id: uuid.UUID,
        user_id: uuid.UUID,
        hash_prefix: str,
        allow: bool,
        surface: str,
    ) -> str:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            u = await s.get(User, user_id)
            call = await s.get(McpToolCall, call_id, with_for_update=True)
            if u is None or call is None:
                return "missing"
            who = acting_user(u, mfa_enforced=self._settings.mfa_enforced)
            try:
                principal = await load_user_principal(s, who, workspace_id)
                rbac.require(principal, Permission.APPROVE, workspace_id)
            except PermissionDenied:
                return "forbidden"
            if len(hash_prefix) < 8 or not call.content_hash.hex().startswith(hash_prefix.lower()):
                return "stale"
            try:
                await self._decide_locked(s, call, u.id, allow)
            except ConflictError as exc:
                return "expired" if exc.code == "call_expired" else "closed"
            await record(
                s,
                AuditEntry(
                    action="mcp_tool_call.allowed" if allow else "mcp_tool_call.denied",
                    actor_type="user",
                    actor_id=u.id,
                    actor_label=u.display_name,
                    surface=surface,
                    workspace_id=workspace_id,
                    subject_type="mcp_tool_call",
                    subject_id=call.id,
                    subject_content_hash=call.content_hash,
                ),
            )
        if allow and self._deferrer is not None:
            await self._deferrer.defer(
                EXEC_TASK,
                queue="integrations",
                queueing_lock=f"mcp-exec:{call_id}",
                workspace_id=str(workspace_id),
                call_id=str(call_id),
            )
        return "allowed" if allow else "denied"

    async def _over_budget(
        self,
        workspace_id: uuid.UUID,
        agent_key: str,
        tool_id: uuid.UUID,
        run_id: uuid.UUID | None,
    ) -> str | None:
        """P3-M6.2: an agent's calls to one tool, per run and per day (UTC)."""
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            b = (
                await s.execute(
                    select(McpAgentBinding).where(
                        McpAgentBinding.tool_id == tool_id, McpAgentBinding.agent_key == agent_key
                    )
                )
            ).scalar_one_or_none()
            per_run = (b.max_calls_per_run if b else None) or DEFAULT_CALLS_PER_RUN
            per_day = (b.max_calls_per_day if b else None) or DEFAULT_CALLS_PER_DAY
            base = (
                select(func.count())
                .select_from(McpToolCall)
                .where(McpToolCall.tool_id == tool_id, McpToolCall.agent_key == agent_key)
            )
            if run_id is not None:
                used = int(await s.scalar(base.where(McpToolCall.agent_run_id == run_id)) or 0)
                if used >= per_run:
                    return f"This run has used its {per_run} calls to this tool."
            day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            used_today = int(await s.scalar(base.where(McpToolCall.created_at >= day)) or 0)
            if used_today >= per_day:
                return f"This agent has used today's {per_day} calls to this tool."
        return None

    async def read_items(
        self, workspace_id: uuid.UUID, server_id: uuid.UUID, tool: str, arguments: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """P3-M6.6, the ``mcp`` source type: call an approved read tool that runs without a
        person (risk ``read``, policy ``auto``) and return its items. The result is data:
        a list of objects, or an object with ``items``; each needs a title and an http(s)
        url. Raises ``McpClientError`` when it cannot be used."""
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            srv = await s.get(McpServer, server_id)
            t = (
                await s.execute(
                    select(McpTool).where(McpTool.server_id == server_id, McpTool.name == tool)
                )
            ).scalar_one_or_none()
            if srv is None or srv.state != "active":
                raise McpClientError("the MCP server is not active")
            if (
                t is None
                or not t.enabled
                or t.risk_class != "read"
                or t.approval_policy != "auto"
                or t.presence != "present"
                or (t.flag is not None and not t.flag_acknowledged)
            ):
                raise McpClientError("the tool must be an enabled read tool with no approval")
            if len(json.dumps(arguments, default=str)) > MAX_SOURCE_ARGS:
                raise McpClientError("the arguments are too large")
            if srv.circuit_open_until is not None and srv.circuit_open_until > datetime.now(UTC):
                raise McpClientError("the server is failing; trying again shortly")
            client = self.client_for(srv)
        try:
            await self.ensure_started(srv)
            result = await client.call_tool(tool, arguments)
        except McpClientError:
            async with workspace_transaction(self._sm, ctx) as s:
                await self._breaker(s, server_id, ok=False)
            raise
        async with workspace_transaction(self._sm, ctx) as s:
            await self._breaker(s, server_id, ok=True)
        data: Any = result.get("structuredContent")
        if data is None:
            for c in result.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "text":
                    try:
                        data = json.loads(c.get("text") or "")
                    except ValueError:
                        continue
                    break
        if isinstance(data, dict):
            data = data.get("items")
        if not isinstance(data, list):
            raise McpClientError("the tool returned no list of items")
        return [d for d in data[:100] if isinstance(d, dict)]

    async def execute_call(self, workspace_id: uuid.UUID, call_id: uuid.UUID) -> CallOutcome:
        """Run an allowed call with the approved arguments, byte for byte, after checking the
        server and the tool have not changed under it."""
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            call = await s.get(McpToolCall, call_id, with_for_update=True)
            if call is None or call.state != "approved":
                return CallOutcome(call.state if call else "missing", call_id, "Not allowed.")
            srv = await s.get(McpServer, call.server_id)
            tool = await s.get(McpTool, call.tool_id)
            reason = None
            if srv is None or srv.state != "active":
                reason = "the server is not active"
            elif tool is None or not tool.enabled or tool.presence != "present":
                reason = "the tool is no longer enabled"
            elif (
                call_hash(srv.id, tool.name, tool.schema_hash, call.arguments) != call.content_hash
            ):
                reason = "the tool or its arguments changed after approval"
            elif call.expires_at <= datetime.now(UTC):
                reason = "the call expired"
            elif srv.circuit_open_until is not None and srv.circuit_open_until > datetime.now(UTC):
                reason = "the server is failing; Mera will try it again in a minute"
            if reason:
                call.state, call.error, call.finished_at = "failed", reason, datetime.now(UTC)
                return CallOutcome("failed", call_id, f"Not run: {reason}.")
            call.state = "running"
            assert srv is not None
            client = self.client_for(srv)
            name, args = call.tool_name, dict(call.arguments)
        try:
            await self.ensure_started(srv)
            result = await client.call_tool(name, args)
            ok = not result.get("isError")
            state, error = (
                ("succeeded" if ok else "failed"),
                (None if ok else "the tool reported an error"),
            )
        except McpClientError as exc:
            result, state, error = {}, "failed", str(exc)[:500]
        stored: dict[str, Any] | None = {"text": untrusted(result)} if result else None
        imgs = [
            {"data": c["data"], "mimeType": c.get("mimeType")}
            for c in (result.get("content") or [])
            if isinstance(c, dict) and c.get("type") == "image" and isinstance(c.get("data"), str)
        ][:4]
        if stored is not None and imgs:
            stored["images"] = imgs  # bytes are probed before use, like any upload
        async with workspace_transaction(self._sm, ctx) as s:
            call = await s.get(McpToolCall, call_id, with_for_update=True)
            assert call is not None
            call.state, call.error, call.result, call.finished_at = (
                state,
                error,
                stored,
                datetime.now(UTC),
            )
            await self._breaker(s, call.server_id, ok=state == "succeeded" or bool(result))
        return CallOutcome(
            state, call_id, stored["text"] if stored else f"The call failed: {error}."
        )

    @staticmethod
    async def _breaker(s: AsyncSession, server_id: uuid.UUID, *, ok: bool) -> None:
        """P3-M6.3: five failures in a row open the circuit for a minute; the call after
        that is the probe. A server that answered (even with a tool error) is up."""
        srv = await s.get(McpServer, server_id, with_for_update=True)
        if srv is None:
            return
        if ok:
            srv.consecutive_failures, srv.circuit_open_until = 0, None
            return
        srv.consecutive_failures += 1
        if srv.consecutive_failures >= BREAKER_FAILURES:
            srv.circuit_open_until = datetime.now(UTC) + BREAKER_OPEN
            srv.consecutive_failures = 0
            srv.last_error = "Five calls in a row failed; paused for a minute."

    async def call_image(
        self, workspace_id: uuid.UUID, call_id: uuid.UUID
    ) -> tuple[bytes, str] | None:
        """The first image a succeeded call returned (for the MCP media provider)."""
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            call = await s.get(McpToolCall, call_id)
        images = ((call.result or {}).get("images") or []) if call else []
        if not images:
            return None
        first = images[0]
        return base64.b64decode(first["data"]), str(first.get("mimeType") or "image/png")

    async def expire_calls(self, workspace_id: uuid.UUID) -> int:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            r = await s.execute(
                update(McpToolCall)
                .where(
                    McpToolCall.state == "pending_approval",
                    McpToolCall.expires_at <= datetime.now(UTC),
                )
                .values(state="expired", finished_at=datetime.now(UTC))
            )
        return int(getattr(r, "rowcount", 0) or 0)

    # ---------------------------------------------------------- OAuth client

    def client_metadata(self) -> dict[str, Any]:
        """Mera's own Client ID Metadata Document, as an MCP client."""
        base = self._settings.mera_api_url.rstrip("/")
        return {
            "client_id": f"{base}/api/v1/mcp-client/metadata.json",
            "client_name": "Mera",
            "redirect_uris": [f"{base}/api/v1/mcp-client/oauth/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }

    def _oauth_client(self) -> httpx.AsyncClient:
        """For hosts a remote server named (its metadata, its authorization server): the
        guarded transport, no redirects, no proxy settings from the environment."""
        transport = self._http_t or guarded_transport(
            system_trust_store=self._settings.http_system_trust_store
        )
        return httpx.AsyncClient(
            timeout=15, transport=transport, follow_redirects=False, trust_env=False
        )

    @staticmethod
    def _https(value: object, what: str, err: type[ConflictError] | type[InvalidInput]) -> str:
        url = str(value or "")
        try:
            parts = urlsplit(url)
        except ValueError:
            parts = urlsplit("")
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.fragment
        ):
            raise err(f"The server's {what} is not an https URL.", code="oauth_unsupported")
        return url

    @staticmethod
    async def _json(
        c: httpx.AsyncClient,
        method: str,
        url: str,
        err: type[ConflictError] | type[InvalidInput],
        **kw: Any,
    ) -> dict[str, Any]:
        """A small JSON document from a remote OAuth party: bounded, parsed defensively."""
        try:
            async with c.stream(method, url, **kw) as r:
                raw = bytearray()
                async for chunk in r.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 256 * 1024:
                        raise err("The server's answer is too large.", code="oauth_unsupported")
                status = r.status_code
        except (httpx.HTTPError, FetchError) as exc:
            raise err("The server could not be reached.", code="oauth_unreachable") from exc
        if status != 200:
            raise err(f"The server answered HTTP {status}.", code="oauth_refused")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise err("The server's answer is not JSON.", code="oauth_unsupported") from exc
        if not isinstance(data, dict):
            raise err("The server's answer is not a JSON object.", code="oauth_unsupported")
        return data

    async def start_oauth(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        server_id: uuid.UUID,
    ) -> str:
        """Discover the server's authorization server (RFC 9728 then RFC 8414) and return the
        authorization URL, with PKCE and ``resource`` bound to this one server."""
        async with self._tx(user, workspace_id, meta) as s:
            await self._admin(s, user, workspace_id)
            srv = await self._server(s, server_id)
            if srv.transport != "http" or not srv.url:
                raise ConflictError("OAuth is for remote servers.", code="not_http")
            resource = srv.url
        parts = urlsplit(resource)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self._oauth_client() as c:
            prm = await self._json(
                c, "GET", f"{origin}/.well-known/oauth-protected-resource", ConflictError
            )
            # RFC 9728 §3.3: the metadata must be about this server, not another one.
            if str(prm.get("resource") or "").rstrip("/") != resource.rstrip("/"):
                raise ConflictError(
                    "The server's metadata describes another resource.", code="oauth_resource"
                )
            servers = prm.get("authorization_servers") or []
            issuer = self._https(
                servers[0] if isinstance(servers, list) and servers else "",
                "authorization server",
                ConflictError,
            ).rstrip("/")
            asm = await self._json(
                c, "GET", f"{issuer}/.well-known/oauth-authorization-server", ConflictError
            )
        token_endpoint = self._https(asm.get("token_endpoint"), "token endpoint", ConflictError)
        authorize_endpoint = self._https(
            asm.get("authorization_endpoint"), "authorization endpoint", ConflictError
        )
        if not asm.get("client_id_metadata_document_supported"):
            raise ConflictError(
                "That authorization server does not accept Client ID Metadata Documents.",
                code="oauth_unsupported",
            )
        if str(asm.get("issuer") or "").rstrip("/") != issuer:
            raise ConflictError(
                "The authorization server's metadata names another issuer.", code="oauth_issuer"
            )
        meta_doc = self.client_metadata()
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(32)
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            flow = McpOAuthFlow(
                id=uuid7(),
                workspace_id=workspace_id,
                server_id=server_id,
                state_hash=hash_key(state),
                code_verifier_ct="",
                issuer=issuer,
                token_endpoint=token_endpoint,
                resource=resource,
                redirect_uri=meta_doc["redirect_uris"][0],
                client_id=meta_doc["client_id"],
                created_by_user_id=user.user_id,
                expires_at=datetime.now(UTC) + FLOW_TTL,
            )
            flow.code_verifier_ct = self._kp.encrypt(
                verifier.encode(),
                workspace_id=workspace_id,
                purpose=FLOW_PURPOSE,
                record_id=flow.id,
            )
            s.add(flow)
        query = {
            "response_type": "code",
            "client_id": meta_doc["client_id"],
            "redirect_uri": meta_doc["redirect_uris"][0],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "resource": resource,
            "scope": " ".join(str(x) for x in (prm.get("scopes_supported") or [])[:50]),
        }
        sep = "&" if "?" in authorize_endpoint else "?"
        return f"{authorize_endpoint}{sep}{urlencode(query)}"

    async def finish_oauth(self, *, state: str, code: str, iss: str | None) -> uuid.UUID:
        """The redirect back: ``iss`` must be the issuer the flow started with (RFC 9207)
        before the code is redeemed; tokens are stored under that issuer only."""
        h = hash_key(state)
        async with secret_lookup_transaction(self._sm, "mera.mcp_flow_state_hash", h) as s:
            flow = (
                await s.execute(select(McpOAuthFlow).where(McpOAuthFlow.state_hash == h))
            ).scalar_one_or_none()
        if flow is None:
            raise InvalidInput("Unknown or expired authorization.", code="invalid_state")
        ctx = WorkspaceContext(flow.workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            row = await s.get(McpOAuthFlow, flow.id, with_for_update=True)
            assert row is not None
            if row.used_at is not None or row.expires_at <= datetime.now(UTC):
                raise InvalidInput("Unknown or expired authorization.", code="invalid_state")
            if (iss or "").rstrip("/") != row.issuer:
                raise InvalidInput(
                    "The authorization came from another issuer.", code="issuer_mismatch"
                )
            row.used_at = datetime.now(UTC)
            verifier = self._kp.decrypt(
                row.code_verifier_ct,
                workspace_id=row.workspace_id,
                purpose=FLOW_PURPOSE,
                record_id=row.id,
            ).decode()
            form = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": row.redirect_uri,
                "client_id": row.client_id,
                "code_verifier": verifier,
                "resource": row.resource,
            }
            token_endpoint, issuer, server_id = row.token_endpoint, row.issuer, row.server_id
        async with self._oauth_client() as c:
            tok = await self._json(c, "POST", token_endpoint, InvalidInput, data=form)
        if not isinstance(tok.get("access_token"), str) or not tok["access_token"]:
            raise InvalidInput("The authorization server sent no token.", code="oauth_refused")
        async with workspace_transaction(self._sm, ctx) as s:
            srv = await self._server(s, server_id, lock=True)
            creds = self._creds(srv)
            oauth = dict(creds.get("oauth") or {})
            oauth[issuer] = {
                "access_token": tok["access_token"][:4096],
                "refresh_token": str(tok.get("refresh_token") or "")[:4096] or None,
                "expires_at": (
                    datetime.now(UTC) + timedelta(seconds=_seconds(tok.get("expires_in")))
                ).isoformat(),
                # Kept for refreshing ahead of expiry (P3-M7.4).
                "token_endpoint": token_endpoint,
                "client_id": form["client_id"],
                "resource": form["resource"],
            }
            creds["oauth"] = oauth
            srv.credentials_ct = self._seal(srv, creds)
            srv.auth_method, srv.oauth_issuer = "oauth", issuer
            if srv.state == "auth_required":
                srv.state = "active"
            srv.updated_at = datetime.now(UTC)
        return server_id

    async def refresh_tokens(self, workspace_id: uuid.UUID, now: datetime | None = None) -> int:
        """P3-M7.4: an external server's OAuth token is refreshed when it has under ten
        minutes left, with the same guarded client as the authorization. A server whose
        refresh is refused is marked ``auth_required`` so an Admin re-authorizes it, rather
        than its calls failing one by one."""
        now = now or datetime.now(UTC)
        ctx = WorkspaceContext(workspace_id)
        async with workspace_transaction(self._sm, ctx) as s:
            servers = list(
                (
                    await s.execute(
                        select(McpServer).where(
                            McpServer.auth_method == "oauth",
                            McpServer.oauth_issuer.is_not(None),
                            McpServer.state.not_in(("disabled", "rejected", "auth_required")),
                        )
                    )
                ).scalars()
            )
            due: list[tuple[uuid.UUID, str, dict[str, Any]]] = []
            for srv in servers:
                entry = (self._creds(srv).get("oauth") or {}).get(srv.oauth_issuer) or {}
                try:
                    expires = datetime.fromisoformat(str(entry.get("expires_at")))
                except ValueError:
                    continue
                if expires - now < timedelta(minutes=10) and entry.get("refresh_token"):
                    due.append((srv.id, str(srv.oauth_issuer), dict(entry)))
        refreshed = 0
        for server_id, issuer, entry in due:
            ok = False
            try:
                async with self._oauth_client() as c:
                    endpoint = entry.get("token_endpoint")
                    if not endpoint:
                        meta = await self._json(
                            c,
                            "GET",
                            f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server",
                            ConflictError,
                        )
                        endpoint = meta.get("token_endpoint")
                    endpoint = self._https(endpoint, "token endpoint", ConflictError)
                    form = {
                        "grant_type": "refresh_token",
                        "refresh_token": str(entry["refresh_token"]),
                        "client_id": str(
                            entry.get("client_id") or self.client_metadata()["client_id"]
                        ),
                    }
                    if entry.get("resource"):
                        form["resource"] = str(entry["resource"])
                    tok = await self._json(c, "POST", endpoint, InvalidInput, data=form)
                ok = isinstance(tok.get("access_token"), str) and bool(tok["access_token"])
            except (ConflictError, InvalidInput):
                ok = False
            async with workspace_transaction(self._sm, ctx) as s:
                srv = await self._server(s, server_id, lock=True)
                creds = self._creds(srv)
                oauth = dict(creds.get("oauth") or {})
                if ok:
                    oauth[issuer] = {
                        **entry,
                        "access_token": tok["access_token"][:4096],
                        "refresh_token": (
                            str(tok.get("refresh_token") or "")[:4096] or entry["refresh_token"]
                        ),
                        "expires_at": (
                            now + timedelta(seconds=_seconds(tok.get("expires_in")))
                        ).isoformat(),
                        "token_endpoint": endpoint,
                    }
                    creds["oauth"] = oauth
                    srv.credentials_ct = self._seal(srv, creds)
                    refreshed += 1
                else:
                    srv.state = "auth_required"
                    srv.last_error = "Its sign-in could not be renewed; authorize it again."
                srv.updated_at = now
        return refreshed


def _seconds(value: object) -> int:
    try:
        return max(60, min(int(str(value)), 365 * 86400))
    except (TypeError, ValueError):
        return 3600


def _server_view(srv: McpServer) -> dict[str, Any]:
    return {
        "id": srv.id,
        "slug": srv.slug,
        "name": srv.name,
        "transport": srv.transport,
        "url": srv.url,
        "package": srv.package,
        "state": srv.state,
        "protocol_era": srv.protocol_era,
        "protocol_version": srv.protocol_version,
        "auth_method": srv.auth_method,
        "has_credentials": srv.credentials_ct is not None,  # the values are never returned
        "egress_allowlist": list(srv.egress_allowlist),
        "scope_group_ids": list(srv.scope_group_ids),
        "added_via": srv.added_via,
        "approved_at": srv.approved_at,
        "last_discovered_at": srv.last_discovered_at,
        "last_error": srv.last_error,
        "created_at": srv.created_at,
        # P3-M6.3: the circuit is open; calls fail fast until the probe.
        "degraded": srv.circuit_open_until is not None
        and srv.circuit_open_until > datetime.now(UTC),
        "circuit_open_until": srv.circuit_open_until,
    }


def _tool_view(t: McpTool) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "title": t.title,
        "description": t.description,
        "input_schema": t.input_schema,
        "annotations": t.annotations,
        "risk_class": t.risk_class,
        "approval_policy": t.approval_policy,
        "enabled": t.enabled,
        "flag": t.flag,
        "flag_acknowledged": t.flag_acknowledged,
        "presence": t.presence,
        "schema_hash": t.schema_hash.hex(),
    }
