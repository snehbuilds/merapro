"""The egress proxy for MCP servers (P2-M9.5, docs/05 §2.4).

The sandbox network has no route out except this proxy. Each sandboxed server is started
with ``HTTPS_PROXY=http://<server-id>:<token>@mcp-egress:3128``; the proxy allows a
connection only when:

- the credentials name a known server (the token is checked in constant time),
- the target host is on **that server's** allowlist, exactly, and
- every address the host resolves to is public (no private ranges, no loopback, no link
  local, no metadata endpoint), and the connection goes to the address that was checked,
  so a DNS rebind between check and connect cannot reach an internal host.

``CONNECT`` (TLS) is tunnelled; plain ``http://`` requests are refused, because a server
has no business sending credentials in clear text.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import ipaddress
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Final

import structlog
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from mera.api.errors import install_error_handlers
from mera.api.middleware.request_id import RequestIdMiddleware
from mera.api.middleware.request_log import RequestLogMiddleware
from mera.infrastructure.http.safe_fetch import is_public as _safe_is_public
from mera.observability import (
    EnvironmentObservabilitySettings,
    configure_logging,
    configure_tracing,
    install_asyncio_exception_handler,
    instrument_fastapi,
)

log = structlog.get_logger(__name__)
METADATA: Final = frozenset({"169.254.169.254", "fd00:ec2::254", "100.100.100.200"})


@dataclass(frozen=True, slots=True)
class Grant:
    token: str
    hosts: frozenset[str]


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return sorted({str(i[4][0]) for i in infos})


def is_public(ip: str) -> bool:
    """The same deny-list as every other outbound fetch (``safe_fetch``): private, loopback,
    link local, CGNAT, NAT64, embedded IPv4 in IPv6, and the metadata endpoints."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return ip not in METADATA and _safe_is_public(addr)


class EgressProxy:
    def __init__(
        self,
        grants: dict[str, Grant],
        *,
        resolver: Resolver = system_resolve,
        public: Callable[[str], bool] = is_public,
        ports: frozenset[int] = frozenset({443, 8443}),
        connect: Callable[..., Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]] = (
            asyncio.open_connection
        ),
    ) -> None:
        self._connect = connect
        self._grants = grants
        self._ports = ports
        self._resolve = resolver
        self._public = public
        self.decisions: list[tuple[str, str, str]] = []  # (server, host, outcome) for tests/logs

    def set_grant(self, server_id: str, grant: Grant) -> None:
        self._grants[server_id] = grant

    def revoke(self, server_id: str) -> None:
        self._grants.pop(server_id, None)

    def _who(self, header: str | None) -> str | None:
        if not header or not header.lower().startswith("basic "):
            return None
        try:
            user, _, token = base64.b64decode(header[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return None
        g = self._grants.get(user)
        if g is None or not hmac.compare_digest(g.token.encode(), token.encode()):
            return None
        return user

    async def decide(
        self, server: str | None, host: str, port: int
    ) -> tuple[bool, str, str | None]:
        """(allowed, reason, the checked address to connect to)."""
        grant = self._grants.get(server) if server is not None else None
        if grant is None:  # unknown, or revoked since the credentials were checked
            return False, "unauthenticated", None
        host = host.lower().rstrip(".")
        if host not in grant.hosts:
            return False, "not on this server's allowlist", None
        if port not in self._ports:
            return False, "only TLS ports", None
        try:
            addrs = await self._resolve(host, port)
        except OSError:
            return False, "does not resolve", None
        if not addrs or not all(self._public(a) for a in addrs):
            return False, "resolves to a private or reserved address", None
        return True, "ok", addrs[0]

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            writer.close()
            return
        lines = head.decode("latin-1").split("\r\n")
        method, target, *_ = [*lines[0].split(" "), "", ""][:3]
        headers = {
            k.strip().lower(): v.strip()
            for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)
        }
        server = self._who(headers.get("proxy-authorization"))
        if method.upper() != "CONNECT":
            self.decisions.append((server or "?", target, "plain http refused"))
            await self._reply(writer, 403, "Plain HTTP is refused; use https.")
            return
        host, _, port_s = target.rpartition(":")
        try:
            port = int(port_s)
        except ValueError:
            await self._reply(writer, 400, "Bad target.")
            return
        ok, reason, addr = await self.decide(server, host, port)
        self.decisions.append((server or "?", host, reason))
        if not ok or addr is None:
            log.info("egress.refused", server=server, host=host, reason=reason)
            await self._reply(writer, 407 if reason == "unauthenticated" else 403, reason)
            return
        try:
            up_r, up_w = await asyncio.wait_for(self._connect(addr, port), timeout=10)
        except (OSError, TimeoutError):
            await self._reply(writer, 502, "upstream unreachable")
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            try:
                while data := await r.read(65536):
                    w.write(data)
                    await w.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                w.close()

        await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: int, text: str) -> None:
        body = text.encode()
        writer.write(
            f"HTTP/1.1 {status} Refused\r\nContent-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()


async def serve(
    proxy: EgressProxy,
    host: str = "0.0.0.0",  # noqa: S104 - the proxy is the sandbox network's gateway
    port: int = 3128,
) -> asyncio.base_events.Server:
    return await asyncio.start_server(proxy.handle, host, port)


def admin_token_from_env(env: dict[str, str]) -> str:
    """The grant API's token: required, 16+ characters, and never the docker-compose
    default outside development (anyone who can read the compose file knows it)."""
    token = env.get("MCP_EGRESS_ADMIN_TOKEN", "").strip()
    if len(token) < 16:
        raise RuntimeError("MCP_EGRESS_ADMIN_TOKEN (16+ characters) is required")
    if token.startswith("change-me") and env.get("MERA_ENV", "development") != "development":
        raise RuntimeError("MCP_EGRESS_ADMIN_TOKEN is the docker-compose default; generate one")
    return token


class GrantIn(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    hosts: list[str] = Field(max_length=50)


def create_egress_app(proxy: EgressProxy, admin_token: str, *, port: int | None = 3128) -> FastAPI:
    """The grant admin API; with ``port``, the proxy itself starts with the app."""
    observability = EnvironmentObservabilitySettings()
    configure_logging(observability)
    configure_tracing(observability, "mera-mcp-egress")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        install_asyncio_exception_handler(asyncio.get_running_loop())
        server = await serve(proxy, port=port) if port is not None else None
        try:
            yield
        finally:
            if server is not None:
                server.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(RequestLogMiddleware, service="mera-mcp-egress")
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    instrument_fastapi(app)

    def _check(given: str | None) -> None:
        if not hmac.compare_digest((given or "").encode(), admin_token.encode()):
            raise HTTPException(status_code=401)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.put("/grants/{server_id}")
    async def put_grant(
        server_id: str, body: GrantIn, x_egress_admin: str | None = Header(None)
    ) -> dict[str, int]:
        _check(x_egress_admin)
        hosts = frozenset(h.strip().lower() for h in body.hosts if h.strip())
        proxy.set_grant(server_id, Grant(body.token, hosts))
        return {"hosts": len(hosts)}

    @app.delete("/grants/{server_id}")
    async def delete_grant(
        server_id: str, x_egress_admin: str | None = Header(None)
    ) -> dict[str, bool]:
        _check(x_egress_admin)
        proxy.revoke(server_id)
        return {"revoked": True}

    return app


def app_factory() -> FastAPI:
    """``mcp-egress``: the proxy on :3128 and the grant API on :3129. Only the API and the
    workers hold the admin token; the sandbox never does."""
    port = int(os.environ.get("MCP_EGRESS_PORT", "3128"))
    return create_egress_app(EgressProxy({}), admin_token_from_env(dict(os.environ)), port=port)
