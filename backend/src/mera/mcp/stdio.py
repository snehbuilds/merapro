"""stdio transport for Mera's MCP server (P2-M8.1): one JSON-RPC message per line.

For Claude Desktop on your own machine. The credential is a workspace API key from the
environment (``MERA_API_KEY``), the API from ``MERA_API_URL``. Logs go to stderr; stdout
carries protocol messages only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from typing import Any, TextIO

from mera.mcp.api import ApiError, Caller, MeraApi, Sealer
from mera.mcp.server import Context, InsufficientScope, McpServer


async def serve(
    api: MeraApi,
    *,
    reader: TextIO = sys.stdin,
    writer: TextIO = sys.stdout,
    state_key: bytes,
    default_workspace: str | None = None,
) -> None:
    server = McpServer(default_workspace=default_workspace)
    sealer = Sealer(state_key)
    caller: Caller | None = None
    negotiated: str | None = None
    loop = asyncio.get_running_loop()

    async def ctx_factory(era: str, url_ok: bool, form_ok: bool) -> Context:
        nonlocal caller
        if caller is None:
            caller = await api.caller(default_workspace)
        return Context(api, caller, sealer, era, url_ok, form_ok)

    while True:
        line = await loop.run_in_executor(None, reader.readline)
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        try:
            message: Any = json.loads(line)
        except ValueError:
            _write(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error."},
                },
            )
            continue
        if isinstance(message, dict) and message.get("method") == "initialize":
            negotiated = str((message.get("params") or {}).get("protocolVersion") or "")
        try:
            out = await server.handle(message, ctx_factory, negotiated=negotiated)
        except InsufficientScope as exc:
            out = {
                "jsonrpc": "2.0",
                "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32001, "message": f"This key lacks the {exc.scope} scope."},
            }
        except ApiError as exc:
            out = {
                "jsonrpc": "2.0",
                "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32002, "message": exc.message},
            }
        if out is not None:
            _write(writer, out)


def _write(writer: TextIO, data: dict[str, Any]) -> None:
    writer.write(json.dumps(data, default=str, ensure_ascii=False) + "\n")
    writer.flush()


def main() -> None:
    key = os.environ.get("MERA_API_KEY", "")
    if not key:
        print("MERA_API_KEY is required (a workspace API key).", file=sys.stderr)
        raise SystemExit(2)
    api = MeraApi(
        os.environ.get("MERA_API_URL", "http://localhost:8000"),
        key,
        service_secret=os.environ.get("MCP_SERVICE_SECRET") or None,
    )
    state_key = hashlib.sha256(b"mcp-stdio|" + key.encode()).digest()
    asyncio.run(
        serve(api, state_key=state_key, default_workspace=os.environ.get("MERA_WORKSPACE_ID"))
    )


if __name__ == "__main__":
    main()
