"""Workspace bot credentials and webhook routing."""

from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from mera.api.v1.routers import channels
from mera.application.channels.bot import BotRuntime
from mera.application.channels.workspace_telegram import KEY, seal
from mera.domain.ports.key_provider import DecryptionError
from mera.infrastructure.crypto.envelope import LocalKeyProvider


def test_workspace_bot_token_is_encrypted_and_bound_to_workspace() -> None:
    key = LocalKeyProvider.from_base64(base64.b64encode(b"x" * 32).decode())
    workspace = uuid.uuid4()
    ciphertext = seal(key, workspace, {"token": "123:secret", "username": "sample_bot"})
    assert "123:secret" not in ciphertext
    assert key.decrypt(ciphertext, workspace_id=workspace, purpose=KEY).startswith(b'{"token"')
    with pytest.raises(DecryptionError):
        key.decrypt(ciphertext, workspace_id=uuid.uuid4(), purpose=KEY)


def test_binding_scope_keeps_workspace_bots_separate() -> None:
    own = uuid.uuid4()
    runtime = object.__new__(BotRuntime)
    runtime._workspace_bot_id = own
    assert runtime._paired_here(SimpleNamespace(preferences={"workspace_id": str(own)}))
    assert not runtime._paired_here(
        SimpleNamespace(preferences={"workspace_id": str(uuid.uuid4())})
    )
    assert not runtime._paired_here(SimpleNamespace(preferences={}))


@pytest.mark.asyncio
async def test_workspace_webhook_checks_secret_and_queues_scoped_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = uuid.uuid4()
    queued: list[tuple[tuple, dict]] = []

    async def fake_credentials(*_args: object) -> dict[str, str]:
        return {"webhook_secret": "correct-secret"}

    class Deferrer:
        async def defer(self, *args: object, **kwargs: object) -> None:
            queued.append((args, kwargs))

    monkeypatch.setattr(channels, "credentials", fake_credentials)
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        mera_master_key=SecretStr(base64.b64encode(b"x" * 32).decode())
    )
    app.state.sessionmaker = object()
    app.state.deferrer = Deferrer()
    app.include_router(channels.webhook_router, prefix="/api/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        url = f"/api/v1/webhooks/telegram/{workspace}"
        body = {"update_id": 42, "message": {"text": "/start"}}
        rejected = await client.post(
            url, json=body, headers={"x-telegram-bot-api-secret-token": "wrong"}
        )
        assert rejected.status_code == 401
        accepted = await client.post(
            url, json=body, headers={"x-telegram-bot-api-secret-token": "correct-secret"}
        )
        assert accepted.status_code == 200
    assert len(queued) == 1
    assert queued[0][0] == ("approvals:telegram_update",)
    assert queued[0][1]["workspace_id"] == str(workspace)
    assert queued[0][1]["queueing_lock"] == f"tg:{workspace}:42"
