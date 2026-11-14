"""P3-M6 external MCP refinements against real Postgres: the catalog, per-agent budgets,
the circuit breaker, and an approved read tool as a content source."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application.services.mcp_registry_service import BREAKER_FAILURES, McpRegistryService
from mera.domain.context import WorkspaceContext
from mera.infrastructure.db.models import ContentItem, McpServer
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.mcp_client import McpClientError
from tests.integration.test_accounts_m3 import _app, _setup
from tests.integration.test_identity_m2 import _h
from tests.integration.test_ingest_m4 import FakeFetcher, _ingestion, _sm
from tests.integration.test_phase2_m9 import Router

pytestmark = pytest.mark.integration

URL = "https://feeds.example/mcp"
LONG = "Postgres 18 ships asynchronous I/O and faster vacuum. " * 20


def feed_server() -> tuple[FastAPI, dict[str, Any]]:
    seen: dict[str, Any] = {"calls": 0, "fail": False}
    app = FastAPI()

    @app.post("/mcp")
    async def mcp(request: Request) -> Any:
        m = await request.json()
        method, mid = m.get("method"), m.get("id")
        if method == "server/discover":
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"protocolVersions": ["2026-07-28"], "serverInfo": {"name": "feeds"}},
            }
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "tools": [
                        {
                            "name": "latest",
                            "description": "Latest posts from the engineering blog.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"topic": {"description": "A topic to filter on"}},
                            },
                            "annotations": {"readOnlyHint": True},
                        }
                    ]
                },
            }
        if method == "tools/call":
            seen["calls"] += 1
            if seen["fail"]:
                return JSONResponse({"error": "down"}, status_code=500)
            items = [
                {
                    "title": "Postgres 18 is out",
                    "url": "https://blog.example/pg18",
                    "published_at": "2026-11-10T16:00:00Z",
                    "text": LONG,
                },
                {"title": "No link here", "url": "javascript:alert(1)"},  # skipped
                {"title": "Faster vacuum", "url": "https://blog.example/vacuum", "text": LONG},
            ]
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"items": items})}],
                },
            }
        if mid is None:
            return {}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "not found"}}

    return app, seen


@pytest.fixture
async def feeds(api: httpx.AsyncClient) -> AsyncIterator[tuple[McpRegistryService, dict[str, Any]]]:
    app, seen = feed_server()
    svc: McpRegistryService = _app(api).state.mcp_registry
    saved = svc._http_t
    svc._http_t = Router({"feeds.example": httpx.ASGITransport(app=app)})
    try:
        yield svc, seen
    finally:
        svc._http_t = saved


def _u(ws: str, path: str) -> str:
    return f"/api/v1/workspaces/{ws}/{path}"


async def _active_read_tool(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, owner_engine: AsyncEngine
) -> tuple[str, str]:
    """A discovered server whose ``latest`` tool an Admin has made read and automatic
    (the second factor for that is covered in test_phase2_m9)."""
    r = await api.post(
        _u(ws, "mcp-servers"),
        headers=_h(s),
        json={"slug": "blog", "name": "Blog", "transport": "http", "url": URL},
    )
    assert r.status_code == 201, r.text
    srv = r.json()
    r = await api.post(_u(ws, f"mcp-servers/{srv['id']}/discover"), headers=_h(s))
    assert r.status_code == 200, r.text
    tool = r.json()["tools"][0]
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE mcp_server SET state = 'active', approved_at = now() WHERE id = :i"),
            {"i": srv["id"]},
        )
        await c.execute(
            text(
                "UPDATE mcp_tool SET enabled = true, risk_class = 'read',"
                " approval_policy = 'auto' WHERE id = :i"
            ),
            {"i": tool["id"]},
        )
    return str(srv["id"]), str(tool["id"])


async def test_catalog_lists_curated_servers_and_installs_for_review(
    api: httpx.AsyncClient,
) -> None:
    s, ws = await _setup(api, "mcpcat")
    r = await api.get(_u(ws, "mcp-catalog"), headers=_h(s), params={"q": "time"})
    assert r.status_code == 200, r.text
    names = [e["name"] for e in r.json()["data"]]
    assert names == ["time"]
    r = await api.post(_u(ws, "mcp-catalog/brave-search/install"), headers=_h(s), json={})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["slug"] == "brave_search"
    assert out["state"] == "pending_review"  # the same review as any other server
    assert out["added_via"] == "catalog"
    assert out["egress_allowlist"] == ["api.search.brave.com"]
    r = await api.post(_u(ws, "mcp-catalog/nope/install"), headers=_h(s), json={})
    assert r.status_code == 404


async def test_budgets_refuse_calls_beyond_the_daily_limit(
    api: httpx.AsyncClient, feeds: Any, owner_engine: AsyncEngine
) -> None:
    svc, seen = feeds
    s, ws = await _setup(api, "mcpbudget")
    _srv, tool = await _active_read_tool(api, s, ws, owner_engine)
    r = await api.put(
        _u(ws, f"mcp-tools/{tool}/bindings"),
        headers=_h(s),
        json={"agent_keys": ["research"], "max_calls_per_day": 2},
    )
    assert r.status_code == 200, r.text
    assert r.json()["max_calls_per_day"] == 2
    outs = [
        await svc.request_call(
            uuid.UUID(ws), agent_key="research", group_ids=[], name="blog__latest", arguments={}
        )
        for _ in range(3)
    ]
    assert [o.state for o in outs] == ["succeeded", "succeeded", "refused"]
    assert "today's 2 calls" in outs[2].text
    assert seen["calls"] == 2  # the refused call never reached the server


async def test_the_breaker_opens_after_five_failures_and_closes_on_success(
    api: httpx.AsyncClient, feeds: Any, owner_engine: AsyncEngine
) -> None:
    svc, seen = feeds
    s, ws = await _setup(api, "mcpbreaker")
    srv, _tool = await _active_read_tool(api, s, ws, owner_engine)
    seen["fail"] = True
    for _ in range(BREAKER_FAILURES):
        with pytest.raises(McpClientError):
            await svc.read_items(uuid.UUID(ws), uuid.UUID(srv), "latest", {})
    reached = seen["calls"]
    with pytest.raises(McpClientError, match="failing"):
        await svc.read_items(uuid.UUID(ws), uuid.UUID(srv), "latest", {})
    assert seen["calls"] == reached  # fails fast: the server was not called
    listed = (await api.get(_u(ws, "mcp-servers"), headers=_h(s))).json()["data"][0]
    assert listed["degraded"] is True
    # After the pause, the next call is the probe; a success closes the circuit.
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE mcp_server SET circuit_open_until = now() - interval '1 second'")
        )
    seen["fail"] = False
    assert len(await svc.read_items(uuid.UUID(ws), uuid.UUID(srv), "latest", {})) == 3
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        row = await tx.get(McpServer, uuid.UUID(srv))
        assert row is not None
        assert (row.consecutive_failures, row.circuit_open_until) == (0, None)


async def test_an_mcp_read_tool_feeds_content_like_rss(
    api: httpx.AsyncClient, feeds: Any, owner_engine: AsyncEngine
) -> None:
    svc, _seen = feeds
    s, ws = await _setup(api, "mcpsource")
    srv, tool = await _active_read_tool(api, s, ws, owner_engine)
    body = {"source_type": "mcp", "name": "Blog", "mcp_server_id": srv, "mcp_tool": "latest"}
    r = await api.post(_u(ws, "sources"), headers=_h(s), json=body)
    assert r.status_code == 201, r.text
    source = r.json()
    assert source["url"].startswith("mcp://")
    # Only an enabled read tool with no approval can be a feed.
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE mcp_tool SET approval_policy = 'always' WHERE id = :i"), {"i": tool}
        )
    r = await api.post(
        _u(ws, "sources"), headers=_h(s), json={**body, "mcp_arguments": {"topic": "x"}}
    )
    assert r.json()["error"]["code"] == "mcp_tool_not_readable"
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE mcp_tool SET approval_policy = 'auto' WHERE id = :i"), {"i": tool}
        )
    ing = _ingestion(api, FakeFetcher())
    ing.mcp = svc
    out = await ing.fetch_source(uuid.UUID(ws), uuid.UUID(source["id"]))
    assert out.status == "fetched", out
    assert out.new_items == 2
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        items = list((await tx.execute(select(ContentItem).order_by(ContentItem.url))).scalars())
    assert [i.url for i in items] == ["https://blog.example/pg18", "https://blog.example/vacuum"]
    assert all(i.provenance["extractor"] == "mcp" for i in items)
    # Without a registry the fetch fails like any other: no crash.
    ing.mcp = None
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE source SET next_fetch_at = now() WHERE id = :i"), {"i": source["id"]}
        )
    assert (await ing.fetch_source(uuid.UUID(ws), uuid.UUID(source["id"]))).status == "failed"
