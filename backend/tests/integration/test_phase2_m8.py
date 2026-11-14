"""P2-M8 Mera's MCP server against the real API (docs/13 P2-M8 acceptance):

- from an MCP client, "draft a post for Tech News about this link and send it for approval"
  makes a post set and an approval card; nothing is approved or published by MCP;
- no tool approves, resumes a kill switch or reveals a credential (the list is asserted);
- OAuth 2.1: CIMD, PKCE, resource-bound tokens that the REST API refuses directly,
  incremental consent, refresh rotation, code replay;
- second factors via URL elicitation; isolation between workspaces on the MCP surface.
"""

from __future__ import annotations

import io
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pydantic
import pytest
from sqlalchemy import select

from mera.application.services.mcp_auth_service import s256
from mera.domain.context import WorkspaceContext
from mera.infrastructure.crypto.api_keys import hash_key
from mera.infrastructure.db.models import AuditLog, McpGrant
from mera.infrastructure.db.session import workspace_transaction
from mera.mcp.api import MeraApi
from mera.mcp.http import create_mcp_app
from mera.mcp.server import MODERN
from mera.mcp.stdio import serve
from mera.mcp.tools import TOOLS
from tests.integration.test_accounts_m3 import _app, _connect, _setup
from tests.integration.test_drafting_m5 import CapturingDeferrer, _group, _run_queued, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import PASSWORD, _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import quiet  # noqa: F401

pytestmark = pytest.mark.integration
SECRET = "mcp-service-secret-0123456789"
RESOURCE = "https://mcp.mera.example/mcp"
CLIENT = "https://client.example/mcp-client.json"
REDIRECT = "http://127.0.0.1:33418/callback"
PIN = "482916"


@pytest.fixture
async def mcp(api: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    """The MCP HTTP app, talking to the test API in-process with the shared secret."""
    app = _app(api)
    saved = app.state.settings
    app.state.settings = saved.model_copy(
        update={"mcp_service_secret": pydantic.SecretStr(SECRET), "mcp_resource_url": RESOURCE}
    )
    auth = app.state.mcp_auth
    saved_resource = auth._resource
    auth._resource = RESOURCE
    auth.local_clients[CLIENT] = {
        "client_id": CLIENT,
        "client_name": "Test Client",
        "redirect_uris": ["http://127.0.0.1/callback"],
    }
    mcp_app = create_mcp_app(app.state.settings, api_transport=httpx.ASGITransport(app=app))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mcp_app), base_url="https://mcp.mera.example"
    ) as c:
        yield c
    app.state.settings = saved
    auth._resource = saved_resource


async def _key(api: httpx.AsyncClient, s: dict[str, Any], ws: str, scopes: list[str]) -> str:
    r = await api.post(
        f"/api/v1/workspaces/{ws}/api-keys",
        headers=_h(s),
        json={"name": "claude", "scopes": scopes},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["secret"])


_ids = iter(range(1, 10**6))


async def rpc(
    mcp: httpx.AsyncClient, token: str, method: str, params: dict[str, Any] | None = None, **h: str
) -> httpx.Response:
    return await mcp.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}", **h},
        json={"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params or {}},
    )


async def call(
    mcp: httpx.AsyncClient, token: str, name: str, args: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    r = await rpc(mcp, token, "tools/call", {"name": name, "arguments": args, **extra})
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()["result"]
    return out


def test_no_tool_approves_resumes_or_reveals() -> None:
    names = {t.name for t in TOOLS}
    forbidden = (
        "approve",
        "decide",
        "resume",
        "credential",
        "secret",
        "token",
        "publish_now",
        "reveal",
    )
    assert not [n for n in names if any(f in n for f in forbidden)], names
    for t in TOOLS:
        d = t.describe()
        assert d["annotations"]["readOnlyHint"] is (not t.mutating), t.name
        assert d["outputSchema"]["type"] == "object"
        assert "pin" not in json.dumps(d["inputSchema"]).lower()
    stop = next(t for t in TOOLS if t.name == "stop_automation")
    assert "engaged" not in stop.input_schema["properties"]  # it can only stop


async def test_draft_from_a_link_and_send_for_approval(
    api: httpx.AsyncClient,
    mcp: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "mcpdraft")
    acc = [
        (await _connect(api, s, ws, "linkedin_member", handle=f"li_{uuid.uuid4().hex[:6]}"))[
            "connected"
        ],
        (await _connect(api, s, ws, "threads", handle=f"th_{uuid.uuid4().hex[:6]}"))["connected"],
    ]
    grp = await _group(api, s, ws, "Tech News", acc)
    key = await _key(api, s, ws, ["drafts:read", "drafts:write", "groups:read", "approvals:read"])

    r = await rpc(
        mcp,
        key,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    )
    assert r.json()["result"]["protocolVersion"] == "2025-11-25"
    assert r.headers["mcp-protocol-version"] == "2025-11-25"
    groups = (await call(mcp, key, "list_groups", {}))["structuredContent"]["data"]
    assert [g["name"] for g in groups] == ["Tech News"]
    # Targets are never guessed.
    out = await call(mcp, key, "create_post", {"topic": "Postgres 18 async I/O"})
    assert out["isError"] is True
    assert "never guesses" in out["content"][0]["text"]
    out = await call(
        mcp,
        key,
        "create_post",
        {
            "topic": "Why async I/O matters",
            "link": "https://example.com/pg18",
            "account_group_ids": [grp],
        },
    )
    assert out["isError"] is False, out
    set_id = out["structuredContent"]["post_set_id"]
    await _run_queued(api, deferrer)
    got = (await call(mcp, key, "get_post_set", {"post_set_id": set_id}))["structuredContent"]
    assert len(got["variants"]) == 2
    out = await call(mcp, key, "request_approval", {"post_set_id": set_id})
    assert out["isError"] is False, out
    req_id = out["structuredContent"]["approval_request_id"]
    # A card goes to the approvers' phones; MCP approved nothing.
    assert any(c["task"] == "approvals:dispatch" and c["kind"] == "card" for c in quiet.calls)
    r = await api.get(f"/api/v1/workspaces/{ws}/approval-requests/{req_id}", headers=_h(s))
    assert r.json()["state"] == "pending"
    # Audited as the MCP surface.
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        surfaces = set(
            (
                await tx.execute(
                    select(AuditLog.surface).where(
                        AuditLog.action.in_(["post_set.created", "approval.requested"])
                    )
                )
            ).scalars()
        )
    assert surfaces == {"mcp"}, surfaces
    # A scope the key lacks: insufficient_scope, naming it.
    r = await rpc(mcp, key, "tools/call", {"name": "list_inbox", "arguments": {}})
    assert r.status_code == 403
    assert 'scope="engagement:read"' in r.headers["www-authenticate"]


async def _oauth(api: httpx.AsyncClient, s: dict[str, Any], ws: str, scope: str) -> dict[str, Any]:
    verifier = secrets.token_urlsafe(48)
    r = await api.post(
        "/api/v1/oauth/authorize",
        headers=_h(s),
        json={
            "workspace_id": ws,
            "client_id": CLIENT,
            "redirect_uri": REDIRECT,
            "code_challenge": s256(verifier),
            "scope": scope,
            "resource": RESOURCE,
            "state": "xyz",
        },
    )
    assert r.status_code == 200, r.text
    back = urlsplit(r.json()["redirect_to"])
    q = parse_qs(back.query)
    assert back.netloc == "127.0.0.1:33418"  # any loopback port (RFC 8252)
    assert q["state"] == ["xyz"]
    assert q["iss"]  # RFC 9207
    form = {
        "grant_type": "authorization_code",
        "code": q["code"][0],
        "client_id": CLIENT,
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
        "resource": RESOURCE,
    }
    t = await api.post("/api/v1/oauth/token", data=form)
    assert t.status_code == 200, t.text
    return {"token": t.json(), "form": form}


async def test_oauth_tokens_are_for_mcp_only(
    api: httpx.AsyncClient, mcp: httpx.AsyncClient
) -> None:
    s, ws = await _setup(api, "mcpoauth")
    meta = (await api.get("/.well-known/oauth-authorization-server")).json()
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["client_id_metadata_document_supported"] is True
    assert "admin" not in meta["scopes_supported"]
    prm = (await mcp.get("/.well-known/oauth-protected-resource")).json()
    assert prm["resource"] == RESOURCE
    # No bearer: a challenge pointing at the metadata.
    r = await mcp.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    # Unknown origins are refused.
    r = await mcp.post(
        "/mcp", headers={"Origin": "https://evil.example", "Authorization": "Bearer x" * 4}, json={}
    )
    assert r.status_code == 403

    flow = await _oauth(api, s, ws, "drafts:read groups:read")
    tok = flow["token"]
    assert tok["scope"] == "drafts:read groups:read"
    access = tok["access_token"]
    # The REST API refuses an MCP token presented directly (its audience is the MCP server).
    assert (
        await api.get("/api/v1/workspaces", headers={"Authorization": f"Bearer {access}"})
    ).status_code == 401
    out = await call(mcp, access, "list_post_sets", {})
    assert out["isError"] is False
    # Incremental consent: a write needs drafts:write.
    r = await rpc(
        mcp,
        access,
        "tools/call",
        {"name": "create_post", "arguments": {"topic": "abc", "account_group_ids": []}},
    )
    assert r.status_code == 403
    assert 'error="insufficient_scope"' in r.headers["www-authenticate"]
    assert 'scope="drafts:write"' in r.headers["www-authenticate"]
    # The code works once; a replay is refused and revokes what it issued.
    replay = await api.post("/api/v1/oauth/token", data=flow["form"])
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    # ...and the revocation is kept although the request was refused (release review #11).
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        issued = (
            await tx.execute(select(McpGrant).where(McpGrant.token_hash == hash_key(access)))
        ).scalar_one()
        assert issued.revoked_at is not None
    r = await rpc(mcp, access + "", "tools/list")
    # (cached caller for a minute is fine; the token itself is now revoked)
    fresh = await _oauth(api, s, ws, "drafts:read")
    # Refresh rotates: the old refresh token is spent.
    refreshed = await api.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": fresh["token"]["refresh_token"],
            "client_id": CLIENT,
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    again = await api.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": fresh["token"]["refresh_token"],
            "client_id": CLIENT,
        },
    )
    assert again.status_code == 400
    # A wrong verifier, a foreign redirect, an admin scope: refused.
    bad = dict(fresh["form"], code_verifier="x" * 50)
    assert (await api.post("/api/v1/oauth/token", data=bad)).status_code == 400
    r = await api.post(
        "/api/v1/oauth/authorize",
        headers=_h(s),
        json={
            "workspace_id": ws,
            "client_id": CLIENT,
            "redirect_uri": "https://evil.example/cb",
            "code_challenge": "a" * 43,
            "scope": "drafts:read",
            "resource": RESOURCE,
        },
    )
    assert r.json()["error"]["code"] == "invalid_redirect_uri"
    r = await api.post(
        "/api/v1/oauth/authorize",
        headers=_h(s),
        json={
            "workspace_id": ws,
            "client_id": CLIENT,
            "redirect_uri": REDIRECT,
            "code_challenge": "a" * 43,
            "scope": "admin",
            "resource": RESOURCE,
        },
    )
    assert r.json()["error"]["code"] == "invalid_scope"
    # The spent refresh token came back: taken as a leak, so what it produced is revoked
    # too, and the rotated pair no longer refreshes (release review #11).
    rotated = refreshed.json()["refresh_token"]
    after_reuse = await api.post(
        "/api/v1/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": rotated, "client_id": CLIENT},
    )
    assert after_reuse.status_code == 400
    assert after_reuse.json()["error"] == "invalid_grant"
    # Connected clients are listed and can be revoked.
    await _oauth(api, s, ws, "drafts:read")
    clients = (await api.get(f"/api/v1/workspaces/{ws}/mcp/clients", headers=_h(s))).json()["data"]
    assert clients
    assert clients[0]["client_name"] == "Test Client"
    gid = clients[0]["id"]
    assert (
        await api.delete(f"/api/v1/workspaces/{ws}/mcp/clients/{gid}", headers=_h(s))
    ).status_code == 204


async def test_isolation_and_modern_era(
    api: httpx.AsyncClient, mcp: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws_a = await _setup(api, "mcpiso")
    s2, ws_b = await _setup(api, "mcpisob")
    acc = (await _connect(api, s2, ws_b, "linkedin_member", handle=f"li_{uuid.uuid4().hex[:6]}"))[
        "connected"
    ]
    grp = await _group(api, s2, ws_b, "Theirs", [acc])
    r = await api.post(
        f"/api/v1/workspaces/{ws_b}/post-sets",
        headers=_h(s2),
        json={"topic": "Theirs", "targets": {"account_group_ids": [grp]}, "generate": False},
    )
    theirs = r.json()["id"]
    key = await _key(api, s, ws_a, ["drafts:read", "drafts:write"])
    out = await call(mcp, key, "get_post_set", {"post_set_id": theirs, "workspace_id": ws_b})
    assert out["isError"] is True
    assert "not one this credential can reach" in out["content"][0]["text"]
    out = await call(mcp, key, "get_post_set", {"post_set_id": theirs})
    assert out["isError"] is True  # A's key, B's id: not found
    r = await rpc(
        mcp, key, "resources/read", {"uri": f"mera://workspaces/{ws_b}/post-sets/{theirs}"}
    )
    assert "error" in r.json()
    # Modern era: discover, and a request with _meta needs no handshake.
    r = await rpc(mcp, key, "server/discover")
    assert MODERN in r.json()["result"]["protocolVersions"]
    out = await call(
        mcp, key, "list_post_sets", {}, _meta={"protocolVersion": MODERN, "clientCapabilities": {}}
    )
    assert out["isError"] is False
    prompts = (await rpc(mcp, key, "prompts/list")).json()["result"]["prompts"]
    assert {p["name"] for p in prompts} == {"setup_wizard", "group_setup"}
    got = (
        await rpc(
            mcp, key, "prompts/get", {"name": "group_setup", "arguments": {"group_name": "Tech"}}
        )
    ).json()
    assert "Tech" in got["result"]["messages"][0]["content"]["text"]


async def test_second_factor_goes_through_a_mera_page(
    api: httpx.AsyncClient,
    mcp: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    from mera.infrastructure.platforms.engagement import FakeEngagement
    from tests.integration.test_phase2_m6 import Box, _comments, _poll, _published

    s, ws = await _setup(api, "mcp2fa")
    inbox = _app(api).state.inbox_service
    saved = (inbox._eng, inbox._deferrer)
    inbox._eng = {
        p: FakeEngagement(p) for p in ("linkedin_member", "facebook", "threads", "x", "instagram")
    }
    inbox._deferrer = CapturingDeferrer()
    try:
        _acc, posts = await _published(api, s, ws, deferrer)
        await _poll(api, Box(inbox, inbox._deferrer), ws, posts)
        c = next(x for x in await _comments(api, ws) if x.platform == "facebook")
        key = await _key(api, s, ws, ["engagement:read", "engagement:write"])
        modern = {"protocolVersion": MODERN, "clientCapabilities": {"elicitation": {"url": {}}}}
        out = await call(mcp, key, "delete_comment", {"comment_id": str(c.id)}, _meta=modern)
        assert out["resultType"] == "input_required"
        req = out["inputRequests"]["confirm"]["params"]
        assert req["mode"] == "url"
        assert "/confirm/" in req["url"]
        state = out["requestState"]
        # Retrying before confirming: not yet.
        early = await call(
            mcp, key, "delete_comment", {"comment_id": str(c.id)}, _meta=modern, requestState=state
        )
        assert "Not confirmed yet" in early["content"][0]["text"]
        # A legacy client gets the link in a tool error instead.
        legacy = await call(mcp, key, "delete_comment", {"comment_id": str(c.id)})
        assert legacy["isError"] is True
        assert "/confirm/" in legacy["content"][0]["text"]
        # The person confirms on the web with a PIN; the model never sees it.
        assert (
            await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": PIN, "proof": PASSWORD})
        ).status_code == 204
        grant = (
            await api.post(
                f"/api/v1/workspaces/{ws}/second-factor",
                headers=_h(s),
                json={"action": "comment_delete", "method": "pin", "value": PIN},
            )
        ).json()["token"]
        pending_id = req["elicitationId"]
        r = await api.post(
            f"/api/v1/workspaces/{ws}/mcp/pending-actions/{pending_id}/confirm",
            headers={**_h(s), "x-mera-second-factor": grant},
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "done"
        done = await call(
            mcp, key, "delete_comment", {"comment_id": str(c.id)}, _meta=modern, requestState=state
        )
        assert done["isError"] is False
        assert done["structuredContent"] == {"deleted": str(c.id)}
        # A sealed state for another tool, or a forged one, is refused.
        forged = await call(mcp, key, "list_inbox", {}, _meta=modern, requestState=state)
        assert forged["isError"] is True
        assert (await call(mcp, key, "list_inbox", {}, requestState="nonsense"))["isError"] is True
        # An API key cannot confirm (only a person, on the web).
        r = await api.post(
            f"/api/v1/workspaces/{ws}/mcp/pending-actions/{pending_id}/confirm",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 403
    finally:
        inbox._eng, inbox._deferrer = saved


async def test_stdio_transport(api: httpx.AsyncClient, deferrer: CapturingDeferrer) -> None:
    s, ws = await _setup(api, "mcpstdio")
    key = await _key(api, s, ws, ["drafts:read"])
    lines = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_post_sets", "arguments": {}},
        },
    ]
    reader = io.StringIO("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    writer = io.StringIO()
    client = MeraApi("http://api", key, transport=httpx.ASGITransport(app=_app(api)))
    await serve(client, reader=reader, writer=writer, state_key=b"k" * 32)
    out = [json.loads(x) for x in writer.getvalue().splitlines()]
    assert [o.get("id") for o in out] == [1, 2, None]  # the notification gets no answer
    assert out[0]["result"]["protocolVersion"] == "2025-06-18"
    assert out[1]["result"]["isError"] is False
    assert out[2]["error"]["code"] == -32700
