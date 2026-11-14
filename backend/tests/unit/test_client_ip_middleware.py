"""P2-M0.9: the browser's address is believed only from the web app, proven by its secret."""

from __future__ import annotations

from typing import Any

import pytest

from mera.api.middleware.client_ip import TrustedClientIPMiddleware

SECRET = "proxy-secret-for-tests-0123456789"


async def _client_after(headers: list[tuple[bytes, bytes]], kind: str = "http") -> Any:
    seen: dict[str, Any] = {}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen["client"] = scope.get("client")

    mw = TrustedClientIPMiddleware(app, proxy_secret=SECRET)
    await mw({"type": kind, "headers": headers, "client": ("10.0.0.5", 4000)}, None, None)  # type: ignore[arg-type]
    return seen["client"]


async def test_web_app_with_secret_sets_the_browser_address() -> None:
    client = await _client_after(
        [(b"x-mera-proxy-secret", SECRET.encode()), (b"x-mera-client-ip", b"203.0.113.9")]
    )
    assert client == ("203.0.113.9", 0)


async def test_ipv6_is_normalised() -> None:
    client = await _client_after(
        [(b"x-mera-proxy-secret", SECRET.encode()), (b"x-mera-client-ip", b"2001:DB8::0001")]
    )
    assert client == ("2001:db8::1", 0)


@pytest.mark.parametrize(
    "headers",
    [
        # No secret, or the wrong one: a script cannot claim another address.
        [(b"x-mera-client-ip", b"203.0.113.9")],
        [(b"x-mera-proxy-secret", b"wrong"), (b"x-mera-client-ip", b"203.0.113.9")],
        # The right secret but something that is not an address.
        [(b"x-mera-proxy-secret", SECRET.encode()), (b"x-mera-client-ip", b"203.0.113.9, 1.1.1.1")],
        [(b"x-mera-proxy-secret", SECRET.encode()), (b"x-mera-client-ip", b"\xff\xfe")],
        [(b"x-mera-proxy-secret", SECRET.encode()), (b"x-mera-client-ip", b"9" * 100)],
        [(b"x-mera-proxy-secret", SECRET.encode())],
        # X-Forwarded-For is never read here.
        [(b"x-mera-proxy-secret", SECRET.encode()), (b"x-forwarded-for", b"203.0.113.9")],
    ],
)
async def test_everything_else_keeps_the_socket_peer(headers: list[tuple[bytes, bytes]]) -> None:
    assert await _client_after(headers) == ("10.0.0.5", 4000)


async def test_non_http_scopes_pass_through() -> None:
    client = await _client_after(
        [(b"x-mera-proxy-secret", SECRET.encode()), (b"x-mera-client-ip", b"203.0.113.9")],
        kind="lifespan",
    )
    assert client == ("10.0.0.5", 4000)
