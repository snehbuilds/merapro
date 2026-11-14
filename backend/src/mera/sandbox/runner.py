"""The ``mcp-sandbox`` relay (P2-M9.4, docs/05 §2.4): runs stdio MCP servers, relays frames.

The API and the workers talk to it over an internal HTTP RPC; it relays JSON-RPC frames to
a server's stdin and reads the matching response from its stdout. Stderr is kept as the
server's log. What it enforces itself:

- **Every route needs the relay token** (``X-Sandbox-Token``, from ``MCP_SANDBOX_TOKEN``).
  A server running inside the sandbox can reach the relay's port, so the token is what
  stops it from starting other servers or talking to them.
- **One uid per server.** Run as root (with only the capabilities it needs, see
  docker-compose), the relay starts each server as its own unprivileged uid with a private
  home and ``umask 077``. A server then cannot read another server's environment
  (``/proc/<pid>/environ`` needs the same uid), its files, or the relay's own environment,
  and cannot signal it. Without root (local development) it warns and runs them as itself.
- **Only a pinned package** (``npm:name@1.2.3``, ``pypi:name==1.2.3``,
  ``oci:image@sha256:…``), through a fixed launcher, never a shell.
- **The environment is exactly the server's own**: its credentials and the proxy URL for
  its own egress grant. Nothing from the relay's environment is passed on. The relay holds
  no egress admin token: grants are registered by the API and workers, which hand the
  relay a proxy URL that only works for that server's allowlist.
- **Idle servers stop** after ten minutes; ``/stop`` stops one at once (the incident path).

The container supplies the rest (docker-compose ``mcp-sandbox``): gVisor (``runsc``) where
the host has it, read-only root, ``tmpfs`` scratch, CPU, memory and process limits, and
networks whose only way out is ``mcp-egress``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)

IDLE_SECONDS: Final = 600
UID_BASE: Final = 20000
HOME_ROOT: Final = "/tmp/mcp-home"  # noqa: S108 - the container's private tmpfs
PINNED: Final = re.compile(
    r"^(npm:(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*@\d+\.\d+\.\d+([-+][\w.-]+)?"
    r"|pypi:[A-Za-z0-9][\w.-]*==\d+(\.\d+)*([a-z0-9.+-]*)?"
    r"|oci:[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64})$"
)
SERVER_ID: Final = re.compile(r"^[0-9a-f-]{36}$")


def launcher(package: str, args: Sequence[str]) -> list[str]:
    kind, spec = package.split(":", 1)
    if kind == "npm":
        return ["npx", "--yes", "--prefer-offline", spec, *args]
    if kind == "pypi":
        return [
            "uvx",
            "--offline" if os.environ.get("MERA_SANDBOX_OFFLINE") else "--quiet",
            spec,
            *args,
        ]
    return ["runsc-run", spec, *args]  # OCI images: the host's runner, outside this process


def can_switch_users() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def private_home(path: str, uid: int) -> None:
    """A home directory only ``uid`` can enter (the relay runs as root on Linux)."""
    if sys.platform == "win32":  # pragma: no cover - per-server users need a POSIX root
        raise RuntimeError("per-server users need Linux")
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chown(path, uid, uid)
    os.chmod(path, 0o700)


@dataclass
class Running:
    proc: asyncio.subprocess.Process
    uid: int | None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used: float = field(default_factory=time.monotonic)
    stderr: list[str] = field(default_factory=list)


class StartIn(BaseModel):
    package: str = Field(max_length=300)
    args: list[str] = Field(default_factory=list, max_length=30)
    env: dict[str, str] = Field(default_factory=dict)
    egress_proxy: str | None = Field(default=None, max_length=300)


class Sandbox:
    def __init__(
        self, *, command_override: list[str] | None = None, isolate_users: bool | None = None
    ) -> None:
        self._running: dict[str, Running] = {}
        self._override = command_override  # tests only: a known local server
        self._isolate = can_switch_users() if isolate_users is None else isolate_users
        self._uids: dict[str, int] = {}
        if not self._isolate:
            log.warning("sandbox.shared_uid", reason="not running as root; servers share a uid")

    def uid_for(self, server_id: str) -> int:
        """A uid of its own for each server, stable for the relay's lifetime."""
        return self._uids.setdefault(server_id, UID_BASE + len(self._uids) + 1)

    def env_for(self, req: StartIn, *, home: str = "/tmp") -> dict[str, str]:  # noqa: S108
        env = {
            str(k): str(v)
            for k, v in req.env.items()
            if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", str(k))
        }
        env.update({"HOME": home, "PATH": "/usr/local/bin:/usr/bin:/bin", "NO_PROXY": ""})
        if req.egress_proxy:
            env.update({"HTTPS_PROXY": req.egress_proxy, "HTTP_PROXY": req.egress_proxy})
        return env

    async def start(self, server_id: str, req: StartIn) -> str:
        if not SERVER_ID.match(server_id):
            raise HTTPException(status_code=400, detail="bad server id")
        if server_id in self._running and self._running[server_id].proc.returncode is None:
            return "already_running"
        if not PINNED.match(req.package):
            raise HTTPException(status_code=400, detail="unpinned package")
        if any(re.search(r"[|;&`$<>]", a) for a in req.args):
            raise HTTPException(status_code=400, detail="shell syntax refused")
        cmd = self._override or launcher(req.package, req.args)
        kwargs: dict[str, Any] = {}
        uid: int | None = None
        home = "/tmp"  # noqa: S108
        if self._isolate:
            uid = self.uid_for(server_id)
            home = f"{HOME_ROOT}/{uid}"
            private_home(home, uid)
            kwargs = {"user": uid, "group": uid, "extra_groups": [], "umask": 0o077}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env_for(req, home=home),
            **kwargs,
        )
        run = Running(proc, uid)
        self._running[server_id] = run

        async def drain() -> None:
            assert proc.stderr is not None
            while line := await proc.stderr.readline():
                run.stderr.append(line.decode(errors="replace")[:500])
                del run.stderr[:-200]

        asyncio.get_running_loop().create_task(drain())
        return "started"

    async def rpc(
        self,
        server_id: str,
        message: dict[str, Any],
        timeout: float = 60,  # noqa: ASYNC109
    ) -> dict[str, Any] | None:
        run = self._running.get(server_id)
        if run is None or run.proc.returncode is not None:
            raise HTTPException(status_code=409, detail="not running")
        assert run.proc.stdin is not None
        assert run.proc.stdout is not None
        async with run.lock:
            run.last_used = time.monotonic()
            run.proc.stdin.write(json.dumps(message).encode() + b"\n")
            await run.proc.stdin.drain()
            if "id" not in message:
                return None  # a notification
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                line = await asyncio.wait_for(
                    run.proc.stdout.readline(), timeout=max(0.1, deadline - time.monotonic())
                )
                if not line:
                    raise HTTPException(status_code=502, detail="server exited")
                if len(line) > 4 * 1024 * 1024:
                    raise HTTPException(status_code=502, detail="frame too large")
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                if isinstance(frame, dict) and frame.get("id") == message["id"]:
                    return frame
            raise HTTPException(status_code=504, detail="timeout")

    async def stop(self, server_id: str) -> bool:
        run = self._running.pop(server_id, None)
        if run is None:
            return False
        with contextlib.suppress(ProcessLookupError):
            run.proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(run.proc.wait(), timeout=5)
        return True

    async def reap_idle(self) -> int:
        now = time.monotonic()
        idle = [
            k
            for k, r in self._running.items()
            if now - r.last_used > IDLE_SECONDS or r.proc.returncode is not None
        ]
        for k in idle:
            await self.stop(k)
        return len(idle)

    def logs(self, server_id: str) -> list[str]:
        run = self._running.get(server_id)
        return list(run.stderr) if run else []


def create_sandbox_app(
    sandbox: Sandbox | None = None, *, relay_token: str | None = None, reap: bool = True
) -> FastAPI:
    token = relay_token or os.environ.get("MCP_SANDBOX_TOKEN") or ""
    if len(token) < 16:
        raise RuntimeError("MCP_SANDBOX_TOKEN (16+ characters) is required")
    sb = sandbox or Sandbox()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async def reaper() -> None:
            while True:
                await asyncio.sleep(60)
                with contextlib.suppress(Exception):
                    await sb.reap_idle()

        task = asyncio.create_task(reaper()) if reap else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            for server_id in list(sb._running):
                await sb.stop(server_id)

    app = FastAPI(
        title="mcp-sandbox", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.sandbox = sb

    @app.middleware("http")
    async def require_token(request: Request, call_next: Any) -> Response:
        if request.url.path == "/healthz":
            out: Response = await call_next(request)
            return out
        given = request.headers.get("x-sandbox-token", "")
        if not hmac.compare_digest(given.encode(), token.encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        out = await call_next(request)
        return out

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/servers/{server_id}/start")
    async def start(server_id: str, body: StartIn) -> dict[str, str]:
        return {"state": await sb.start(server_id, body)}

    @app.post("/servers/{server_id}/rpc")
    async def rpc(server_id: str, body: dict[str, Any]) -> Any:
        out = await sb.rpc(server_id, body)
        return out if out is not None else {}

    @app.post("/servers/{server_id}/stop")
    async def stop(server_id: str) -> dict[str, bool]:
        return {"stopped": await sb.stop(server_id)}

    @app.get("/servers/{server_id}/logs")
    async def logs(server_id: str) -> dict[str, list[str]]:
        return {"lines": sb.logs(server_id)}

    return app


def app_factory() -> FastAPI:
    """``mcp-sandbox``: stdio servers, one uid each, egress through ``mcp-egress`` only."""
    return create_sandbox_app(Sandbox())
