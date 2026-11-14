"""HTTP plumbing shared by the real adapters: one client, one error classification.

Every platform response is mapped onto the port's error kinds so the publish worker can
decide without knowing the platform:

- 429, or a quota error the platform reports in its body → ``RateLimited`` (honours
  ``Retry-After``);
- 401, or an OAuth error code → ``AuthExpired`` (the account moves to NEEDS_REAUTH);
- 5xx, timeouts, connection errors → ``TransientError``;
- any other 4xx → ``PermanentError`` with the platform's own message, trimmed.

Platform error text is untrusted: it is trimmed and never interpolated into prompts.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from mera.domain.ports.platform_adapter import (
    AuthExpired,
    PermanentError,
    PublishError,
    RateLimited,
    TransientError,
)

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_ERROR = 300


def _retry_after(resp: httpx.Response) -> int | None:
    raw = resp.headers.get("retry-after") or resp.headers.get("x-rate-limit-reset")
    if not raw:
        return None
    try:
        value = int(float(raw))
    except ValueError:
        return None
    if value > 10**9:  # an epoch reset time (X): convert to a delay
        import time

        value = max(0, value - int(time.time()))
    return value


def platform_message(resp: httpx.Response) -> str:
    try:
        body: Any = resp.json()
    except (json.JSONDecodeError, ValueError):
        return resp.text[:MAX_ERROR] or f"HTTP {resp.status_code}"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("error_user_msg") or json.dumps(err)
            return str(msg)[:MAX_ERROR]
        for key in ("message", "detail", "title", "error_description"):
            if isinstance(body.get(key), str):
                return str(body[key])[:MAX_ERROR]
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            return str(errors[0].get("message") or errors[0])[:MAX_ERROR]
    return json.dumps(body)[:MAX_ERROR]


AuthDetector = Callable[[httpx.Response], bool]


def _default_auth(resp: httpx.Response) -> bool:
    return resp.status_code == 401


def raise_for(resp: httpx.Response, *, is_auth_error: AuthDetector = _default_auth) -> None:
    if resp.is_success:
        return
    msg = platform_message(resp)
    if resp.status_code == 429:
        raise RateLimited(msg, retry_after=_retry_after(resp))
    if is_auth_error(resp):
        raise AuthExpired(msg, status=resp.status_code)
    if resp.status_code >= 500:
        raise TransientError(msg, status=resp.status_code)
    raise PermanentError(msg, status=resp.status_code)


class PlatformHTTP:
    """A thin wrapper so tests can inject ``httpx.MockTransport`` (contract cassettes)."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        is_auth_error: AuthDetector = _default_auth,
        user_agent: str = "Mera/1.0",
    ) -> None:
        self._transport = transport
        self._is_auth = is_auth_error
        self._ua = user_agent

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        data: Mapping[str, Any] | None = None,
        content: bytes | None = None,
        files: Any = None,
        expect_json: bool = True,
    ) -> httpx.Response:
        hdrs = {"User-Agent": self._ua, **(headers or {})}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as c:
                resp = await c.request(
                    method,
                    url,
                    headers=hdrs,
                    params=params,
                    json=json_body,
                    data=data,
                    content=content,
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise TransientError(
                f"timed out talking to the platform ({type(exc).__name__})"
            ) from exc
        except httpx.TransportError as exc:
            raise TransientError(f"could not reach the platform ({type(exc).__name__})") from exc
        raise_for(resp, is_auth_error=self._is_auth)
        return resp

    async def json(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        resp = await self.request(method, url, **kw)
        if not resp.content:
            return {}
        try:
            body = resp.json()
        except ValueError as exc:
            raise PublishError("the platform returned something that is not JSON") from exc
        return body if isinstance(body, dict) else {"data": body}


def bearer(credentials: Mapping[str, Any]) -> dict[str, str]:
    token = credentials.get("access_token")
    if not token:
        raise AuthExpired("no access token stored for this account")
    return {"Authorization": f"Bearer {token}"}


def same_text(a: str | None, b: str | None) -> bool:
    """Reconciliation match: whitespace-insensitive equality of the start of the text."""
    if not a or not b:
        return False
    na, nb = " ".join(a.split()), " ".join(b.split())
    return na[:200] == nb[:200]
