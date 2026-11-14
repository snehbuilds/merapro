"""Per-workspace agent model choice (Sprint 8 carry-over)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.test_accounts_m3 import _setup
from tests.integration.test_identity_m2 import _add_member, _h, _me, _register_login

pytestmark = pytest.mark.integration


async def test_admins_choose_priced_models_and_the_next_call_uses_them(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "agent-models")
    base = f"/api/v1/workspaces/{ws}/agents"
    agents = {a["agent_key"]: a for a in (await api.get(base, headers=_h(s))).json()["data"]}
    writer = agents["writer"]
    assert writer["editable"] is True
    assert writer["model"] == writer["default_model"]
    other = next(m for m in writer["choices"] if m != writer["model"])

    r = await api.patch(f"{base}/writer", headers=_h(s), json={"model": "acme/unpriced-9"})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "unknown_model")
    editor = await _register_login(api, "agent-ed")
    await _add_member(owner_engine, ws, (await _me(api, editor))["id"], "editor")
    r = await api.patch(f"{base}/writer", headers=_h(editor), json={"model": other})
    assert r.status_code == 403

    r = await api.patch(f"{base}/writer", headers=_h(s), json={"model": other, "fallbacks": []})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == other

    # The next Writer call (here, a voice sample) runs on the chosen model.
    voice = (
        await api.post(
            f"/api/v1/workspaces/{ws}/brand-voices",
            headers=_h(s),
            json={"scope_type": "workspace", "name": "Plain"},
        )
    ).json()
    r = await api.post(
        f"/api/v1/workspaces/{ws}/brand-voices/{voice['id']}/sample",
        headers=_h(s),
        json={"topic": "Why we moved batch jobs to Postgres 18", "platform": "linkedin_member"},
    )
    assert r.status_code == 200, r.text
    async with owner_engine.begin() as conn:
        used = (
            await conn.execute(
                text(
                    "SELECT model FROM agent_step WHERE workspace_id = :ws "
                    "AND agent_key = 'writer' ORDER BY started_at DESC LIMIT 1"
                ),
                {"ws": ws},
            )
        ).scalar_one()
    assert used.endswith(other)

    # Back to the default.
    r = await api.patch(f"{base}/writer", headers=_h(s), json={"model": None})
    assert r.json()["model"] == writer["default_model"]
