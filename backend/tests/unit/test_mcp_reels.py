"""Unit tests for MCP Reel/video upload and publishing flow."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from mera.api.v1.routers.media import _TYPES
from mera.mcp.api import Caller, MeraApi, Sealer
from mera.mcp.server import Context, McpServer

WS_ID = str(uuid.uuid4())
ACC_ID = str(uuid.uuid4())
POST_SET_ID = str(uuid.uuid4())
VARIANT_ID = str(uuid.uuid4())
VERSION_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())


def _ctx(handler: Any, *, era: str = "modern", url_elicitation: bool = True) -> Context:
    api = MeraApi("http://test", "tok", transport=httpx.MockTransport(handler))
    caller = Caller(
        workspace_id=WS_ID,
        workspaces=((WS_ID, "Main"),),
        scopes=frozenset({"drafts:read", "drafts:write", "media:write", "publishing:write"}),
    )
    return Context(
        api=api,
        caller=caller,
        sealer=Sealer(b"0" * 32),
        era=era,
        url_elicitation=url_elicitation,
    )


async def _ctx_coro(handler: Any, *, era: str, url_elicitation: bool) -> Context:
    return _ctx(handler, era=era, url_elicitation=url_elicitation)


def test_signed_media_types_include_video() -> None:
    assert _TYPES["mp4"] == "video/mp4"
    assert _TYPES["mov"] == "video/quicktime"


async def test_mcp_upload_reel_and_create_post_flow() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        if request.url.path.endswith("/media/ingest"):
            assert body["remote_url"] == "https://cdn.example.com/reel.mp4"
            assert body["content_type"] == "video/mp4"
            return httpx.Response(201, json={"id": ASSET_ID, "media_type": "video"})
        if request.method == "POST" and request.url.path.endswith("/post-sets"):
            return httpx.Response(
                201,
                json={
                    "id": POST_SET_ID,
                    "state": "draft",
                    "variants": [{"id": VARIANT_ID, "nickname": "IG Reel"}],
                },
            )
        if request.method == "POST" and request.url.path.endswith("/versions"):
            return httpx.Response(201, json={"id": VERSION_ID})
        if request.method == "GET" and request.url.path.endswith(f"/post-sets/{POST_SET_ID}"):
            return httpx.Response(
                200,
                json={
                    "id": POST_SET_ID,
                    "variants": [{"id": VARIANT_ID, "current_version": {"id": VERSION_ID}}],
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/media"):
            assert body["media"] == [{"asset_id": ASSET_ID, "alt_text": None}]
            return httpx.Response(200, json={"id": POST_SET_ID})
        if request.method == "POST" and request.url.path.endswith("/request-approval"):
            return httpx.Response(201, json={"id": str(uuid.uuid4()), "state": "pending"})
        return httpx.Response(404)

    srv = McpServer()
    up = await srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "upload_media",
                "arguments": {
                    "file": {
                        "download_url": "https://cdn.example.com/reel.mp4",
                        "file_id": "file_123",
                        "mime_type": "video/mp4",
                        "file_name": "reel.mp4",
                    }
                },
            },
        },
        lambda era, url_ok, form_ok: _ctx_coro(handler, era=era, url_elicitation=url_ok),
    )
    assert up is not None
    assert up["result"]["structuredContent"]["id"] == ASSET_ID

    post = await srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "create_post",
                "arguments": {
                    "social_account_ids": [ACC_ID],
                    "body": "Watch our new Reel!",
                    "media_ids": [ASSET_ID],
                    "publish_mode": "immediate",
                },
            },
        },
        lambda era, url_ok, form_ok: _ctx_coro(handler, era=era, url_elicitation=url_ok),
    )
    assert post is not None
    assert post["result"]["isError"] is False
    assert post["result"]["structuredContent"]["approval_request_id"] is not None
