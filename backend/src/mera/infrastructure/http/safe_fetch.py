"""Outbound fetching of user-supplied URLs (M4.9; docs/09 T12 and A10, SSRF).

Every connection goes through ``GuardedBackend``: the host is resolved once, **every**
resolved address must be public (a DNS answer mixing a public and a private address is
refused), and the socket connects to the address that was checked. Checking and connecting
the same address is what defeats DNS rebinding; TLS still verifies the original hostname,
because httpcore takes SNI from the request, not from the socket's address.

Redirects are followed by hand, at most ``MAX_REDIRECTS``, and each hop is a new request
through the same guard, so a public URL cannot bounce to an internal one. The client asks
for ``identity`` encoding and reads raw bytes; a server that compresses anyway is inflated
here with an explicit output bound (httpx's own decoders inflate a whole chunk at once, so
a 60 KB gzip bomb would become 60 MB before any size check). Other encodings are refused.
The client ignores proxy environment variables: an egress proxy would make the guard check
the proxy's address instead of the target's.

Errors carry a precise ``kind`` for logs; ``public_message`` collapses the ones that would
let a caller map the internal network (blocked vs unresolvable vs refused vs timed out)
into one answer. This guard is defence in depth: production should also restrict egress at
the network layer.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
import zlib
from collections.abc import Awaitable, Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx
import structlog
from httpcore._backends.anyio import AnyIOBackend

MAX_REDIRECTS: Final = 5
_REDIRECTS: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})
ALLOWED_PORTS: Final[frozenset[int]] = frozenset({80, 443, 8080, 8443})
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
# Addresses ``is_global`` accepts that must still never be fetched (cloud host endpoints,
# deprecated site-local IPv6, IETF protocol assignments).
_DENY: Final = tuple(
    ipaddress.ip_network(n)
    for n in (
        "168.63.129.16/32",  # Azure WireServer / host DNS
        "169.254.0.0/16",
        "100.64.0.0/10",
        "192.0.0.0/24",
        "198.18.0.0/15",
        "fec0::/10",
        "fe80::/10",
        "fc00::/7",
    )
)
DNS_TIMEOUT: Final = 5.0
# getaddrinfo blocks a thread; a stalling nameserver must not drain the default pool that
# article extraction also uses.
_DNS_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mera-dns")
_PUBLIC_KINDS: Final[frozenset[str]] = frozenset(
    {"invalid_url", "http_status", "too_large", "too_many_redirects", "not_a_feed"}
)

Resolver = Callable[[str, int], Awaitable[list[str]]]
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class FetchError(Exception):
    def __init__(self, kind: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind  # invalid_url, blocked_address, dns, connect, timeout, too_large,
        #                   too_many_redirects, http_status
        self.status = status


@dataclass(frozen=True, slots=True)
class AddressPolicy:
    """``allow_private`` exists for tests that serve feeds from 127.0.0.1. Nothing in the
    application sets it."""

    allow_private: bool = False

    def allows(self, ip: IPAddress) -> bool:
        if self.allow_private:
            return True
        return is_public(ip)


def is_public(ip: IPAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None:
            return is_public(embedded)
        if ip in _NAT64 or ip in _NAT64_LOCAL:
            return False
    if any(ip.version == n.version and ip in n for n in _DENY):
        return False
    return bool(ip.is_global) and not ip.is_multicast and not ip.is_reserved


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.run_in_executor(
            _DNS_POOL, lambda: socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ),
        DNS_TIMEOUT,
    )
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def public_message(kind: str, message: str) -> tuple[str, str]:
    """What a caller may be told about a failed fetch: precise for ordinary HTTP outcomes,
    one generic answer for anything that would reveal the network's shape."""
    if kind in _PUBLIC_KINDS:
        return kind, message
    return "unreachable", "The URL could not be reached."


class GuardedBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self, policy: AddressPolicy, resolver: Resolver, allowed_ports: frozenset[int]
    ) -> None:
        self._policy = policy
        self._resolver = resolver
        self._ports = allowed_ports
        self._inner = AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - httpcore's interface
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if port not in self._ports:
            raise FetchError("blocked_address", f"port {port} is not allowed")
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            try:
                addresses = [
                    ipaddress.ip_address(a.split("%", 1)[0])
                    for a in await self._resolver(host, port)
                ]
            except (OSError, ValueError, TimeoutError) as exc:
                raise FetchError("dns", f"could not resolve {host}") from exc
        if not addresses:
            raise FetchError("dns", f"could not resolve {host}")
        if not all(self._policy.allows(a) for a in addresses):
            raise FetchError("blocked_address", f"{host} resolves to a non-public address")
        last: Exception | None = None
        for address in addresses:  # every one was checked; try them in order
            try:
                return await self._inner.connect_tcp(
                    str(address),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last = exc
        assert last is not None
        raise last

    async def connect_unix_socket(self, *a: Any, **kw: Any) -> httpcore.AsyncNetworkStream:
        raise FetchError("blocked_address", "unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    def __init__(
        self,
        *,
        ssl_context: ssl.SSLContext,
        policy: AddressPolicy,
        resolver: Resolver,
        allowed_ports: frozenset[int] = ALLOWED_PORTS,
    ) -> None:
        super().__init__(verify=ssl_context, retries=0)
        # Same pool httpx would build, plus the guarded backend (httpx exposes no hook).
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=20,
            max_keepalive_connections=5,
            network_backend=GuardedBackend(policy, resolver, allowed_ports),
        )


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str  # final URL after redirects
    status: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)

    @property
    def not_modified(self) -> bool:
        return self.status == 304


def make_ssl_context(system_trust_store: bool) -> ssl.SSLContext:
    if system_trust_store:
        try:
            import truststore

            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception as exc:  # pragma: no cover - platform without a usable store
            structlog.get_logger(__name__).warning(
                "http.system_trust_store_unavailable", error_type=type(exc).__name__
            )
    return ssl.create_default_context()


class SafeFetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        policy: AddressPolicy | None = None,
        resolver: Resolver = system_resolver,
        system_trust_store: bool = True,
        connect_timeout: float = 5.0,
        read_timeout: float = 15.0,
        allowed_ports: frozenset[int] = ALLOWED_PORTS,
    ) -> None:
        self._ports = allowed_ports
        self._user_agent = user_agent
        self._policy = policy or AddressPolicy()
        self._resolver = resolver
        self._ssl = make_ssl_context(system_trust_store)
        self._timeout = httpx.Timeout(read_timeout, connect=connect_timeout)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=GuardedTransport(
                ssl_context=self._ssl,
                policy=self._policy,
                resolver=self._resolver,
                allowed_ports=self._ports,
            ),
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": self._user_agent},
        )

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        max_bytes: int,
        overall_timeout: float = 30.0,
        accept_statuses: frozenset[int] = frozenset({200, 304}),
    ) -> FetchResult:
        try:
            async with asyncio.timeout(overall_timeout), self._client() as client:
                return await self._get(client, url, headers or {}, max_bytes, accept_statuses)
        except FetchError:
            raise
        except TimeoutError as exc:
            raise FetchError("timeout", "the request timed out") from exc
        except httpx.TimeoutException as exc:
            raise FetchError("timeout", "the request timed out") from exc
        except httpx.TransportError as exc:
            cause = exc.__context__ or exc.__cause__
            if isinstance(cause, FetchError):
                raise cause from None
            raise FetchError("connect", f"connection failed ({type(exc).__name__})") from exc
        except (httpx.InvalidURL, httpx.HTTPError, ValueError) as exc:
            # Malformed hosts (octal literals, broken IPv6) and anything else httpx rejects.
            raise FetchError("invalid_url", "the URL is not valid") from exc

    async def _get(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: Mapping[str, str],
        max_bytes: int,
        accept_statuses: frozenset[int],
    ) -> FetchResult:
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            _check_url(current, self._ports)
            try:
                req_headers = {**headers, "Accept-Encoding": "identity"}
                async with client.stream("GET", current, headers=req_headers) as resp:
                    if resp.status_code in _REDIRECTS:
                        location = resp.headers.get("location")
                        if not location:
                            raise FetchError(
                                "http_status",
                                "redirect without a location",
                                status=resp.status_code,
                            )
                        try:
                            current = urljoin(current, location)
                        except ValueError as exc:
                            raise FetchError("invalid_url", "invalid redirect target") from exc
                        continue
                    if resp.status_code not in accept_statuses:
                        raise FetchError(
                            "http_status", f"HTTP {resp.status_code}", status=resp.status_code
                        )
                    body = await _read_bounded(resp, max_bytes)
                    return FetchResult(
                        url=str(resp.url),
                        status=resp.status_code,
                        headers={k.lower(): v for k, v in resp.headers.items()},
                        body=bytes(body),
                    )
            except FetchError:
                raise
            except (httpx.DecodingError, zlib.error) as exc:
                raise FetchError("connect", "the response could not be decoded") from exc
        raise FetchError("too_many_redirects", f"more than {MAX_REDIRECTS} redirects")


async def _read_bounded(resp: httpx.Response, max_bytes: int) -> bytes:
    encoding = resp.headers.get("content-encoding", "identity").strip().lower()
    if encoding in ("", "identity"):
        inflater = None
    elif encoding in ("gzip", "x-gzip"):
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        inflater = zlib.decompressobj()
    else:
        raise FetchError("too_large", f"unsupported content encoding {encoding[:20]}")
    body = bytearray()

    def take(data: bytes) -> None:
        body.extend(data)
        if len(body) > max_bytes:
            raise FetchError("too_large", f"response exceeds {max_bytes} bytes")

    async for raw in resp.aiter_raw():
        if inflater is None:
            take(raw)
            continue
        data = raw
        while data:  # never inflate more than the remaining budget plus one byte
            take(inflater.decompress(data, max_bytes - len(body) + 1))
            data = inflater.unconsumed_tail
    if inflater is not None:
        take(inflater.flush())
    return bytes(body)


def _check_url(url: str, allowed_ports: frozenset[int] = ALLOWED_PORTS) -> None:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError as exc:
        raise FetchError("invalid_url", "the URL is not valid") from exc
    if parts.scheme not in ("http", "https"):
        raise FetchError("invalid_url", "only http and https URLs can be fetched")
    if parts.username or parts.password:
        raise FetchError("invalid_url", "URLs with credentials are not allowed")
    if not hostname:
        raise FetchError("invalid_url", "the URL has no host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise FetchError("invalid_url", "the URL has an invalid port") from exc
    if port not in allowed_ports:
        raise FetchError("blocked_address", f"port {port} is not allowed")


def validate_public_url(url: str) -> str | None:
    """A save-time check for obviously internal URLs, for a friendly error. The fetch-time
    guard is the real control; this returns an error message or ``None``."""
    try:
        _check_url(url)
    except FetchError as exc:
        return str(exc)
    try:
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return "the URL is not valid"
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return "the URL points at a local host"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    return None if is_public(ip) else "the URL points at a non-public address"


def guarded_transport(
    *,
    system_trust_store: bool = True,
    allowed_ports: frozenset[int] = frozenset({443, 8443}),
    policy: AddressPolicy | None = None,
    resolver: Resolver = system_resolver,
) -> GuardedTransport:
    """An httpx transport for requests to hosts someone else chose (an MCP server, a
    Mastodon instance, an OAuth server's metadata): it connects only to the address it
    checked, so a DNS answer that changes between check and connect cannot reach inside."""
    return GuardedTransport(
        ssl_context=make_ssl_context(system_trust_store),
        policy=policy or AddressPolicy(),
        resolver=resolver,
        allowed_ports=allowed_ports,
    )
