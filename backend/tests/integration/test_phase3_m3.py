"""P3-M3 Assisted and Full Autopilot against real Postgres (docs/14 §2.3)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application import autopilot
from mera.application.approvals.publishable import NotPublishable, assert_publishable
from mera.domain.context import WorkspaceContext
from mera.infrastructure.db.models import (
    ApprovalDecision,
    AutopilotAuthorization,
    ScheduledPost,
)
from mera.infrastructure.db.session import workspace_transaction
from tests.integration.test_accounts_m3 import _setup
from tests.integration.test_approvals_m6 import _submit
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import PASSWORD, _h
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import _four, quiet  # noqa: F401
from tests.integration.test_telegram_m7 import _bot, _pair, _tap

pytestmark = pytest.mark.integration
PIN = "481739"


async def _grant(api: httpx.AsyncClient, s: dict[str, Any], ws: str) -> str:
    r = await api.post(
        f"/api/v1/workspaces/{ws}/second-factor",
        headers=_h(s),
        json={"action": "autopilot_enable", "method": "pin", "value": PIN},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["token"])


async def test_full_autopilot_is_earned_confirmed_bounded_and_stoppable(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s, ws = await _setup(api, "autopilot", second_factors=True)
    wsid = uuid.UUID(ws)
    ps, acc, _ = await _four(api, s, ws, deferrer)
    full = {"settings": {"autopilot_mode": "full"}}
    # Not earned yet: no approvals by people on these accounts.
    r = await api.patch(f"/api/v1/workspaces/{ws}", headers=_h(s), json=full)
    assert r.json()["error"]["code"] == "autopilot_not_earned", r.text
    monkeypatch.setattr(autopilot, "EARN_MIN_APPROVALS", 0)
    # Earned (for this test), but it takes a second factor.
    r = await api.patch(f"/api/v1/workspaces/{ws}", headers=_h(s), json=full)
    assert r.json()["error"]["code"] == "second_factor_required"
    assert (
        await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": PIN, "proof": PASSWORD})
    ).status_code == 204
    r = await api.patch(
        f"/api/v1/workspaces/{ws}",
        headers={**_h(s), "x-mera-second-factor": await _grant(api, s, ws)},
        json=full,
    )
    assert r.status_code == 200, r.text
    async with owner_engine.connect() as c:
        changed = (
            (
                await c.execute(
                    text(
                        "SELECT detail FROM security_event "
                        "WHERE workspace_id = :w AND kind = 'autopilot.changed'"
                    ),
                    {"w": ws},
                )
            )
            .scalars()
            .all()
        )
    assert changed
    assert changed[-1]["to"] == "full"

    bot, ch = _bot(api)
    chat = f"c{uuid.uuid4().hex[:8]}"
    await _pair(api, bot, s, ws, chat)
    req = await _submit(api, s, ws, ps["id"])
    by_account = {v["social_account_id"]: v["state"] for v in req["variants"]}
    # Personal accounts go out unattended; the company Page still waits for a person.
    assert by_account[acc["fb"]] == "pending"
    for k in ("li", "th", "xx"):
        assert by_account[acc[k]] == "approved", by_account
    async with workspace_transaction(_sm(api), WorkspaceContext(wsid)) as tx:
        auths = list(
            (
                await tx.execute(
                    select(AutopilotAuthorization).where(
                        AutopilotAuthorization.approval_request_id == uuid.UUID(req["id"])
                    )
                )
            ).scalars()
        )
        assert {str(a.social_account_id) for a in auths} == {acc["li"], acc["th"], acc["xx"]}
        assert all(a.mode == "full" for a in auths)
        decision = (
            await tx.execute(
                select(ApprovalDecision).where(
                    ApprovalDecision.approval_request_id == uuid.UUID(req["id"])
                )
            )
        ).scalar_one()
        assert (decision.channel_type, decision.external_id) == ("system", "autopilot")
        slots = {
            str(sp.social_account_id): sp
            for sp in (
                await tx.execute(
                    select(ScheduledPost).where(
                        ScheduledPost.approval_request_id == uuid.UUID(req["id"])
                    )
                )
            ).scalars()
        }
        assert set(slots) == {acc["li"], acc["th"], acc["xx"]}
        li_variant = slots[acc["li"]].post_variant_id
        await assert_publishable(tx, li_variant)  # the gate accepts the authorization

    # The notice names every unattended account, each with Stop while queued.
    assert await bot.dispatch("autopilot", wsid, uuid.UUID(req["id"])) == 1
    notice = ch.last(chat)
    assert "Autopilot approved" in notice.text
    stops = [b.data for row in notice.rows for b in row if b.label.startswith("⏹")]
    assert len(stops) == 3
    assert await bot.handle_update(_tap(chat, "1", stops[0])) == "autopilot_stopped"
    assert await bot.handle_update(_tap(chat, "1", stops[0])) == "autopilot_too_late"

    # Turning autopilot down needs no second factor, and stops what was queued on it.
    r = await api.patch(
        f"/api/v1/workspaces/{ws}", headers=_h(s), json={"settings": {"autopilot_mode": "manual"}}
    )
    assert r.status_code == 200, r.text
    async with workspace_transaction(_sm(api), WorkspaceContext(wsid)) as tx:
        with pytest.raises(NotPublishable) as stopped:
            await assert_publishable(tx, slots[acc["th"]].post_variant_id)
        assert stopped.value.code == "autopilot_withdrawn"


async def test_clearing_a_stricter_level_is_guarded_like_raising(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    """Security review #1: an explicit workspace manual over a group on Full. Clearing the
    workspace value would release Full, so it needs everything turning Full on needs."""
    s, ws = await _setup(api, "autopilotclear", second_factors=True)
    _ps, _acc, grp = await _four(api, s, ws, deferrer)
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE workspace SET settings = settings || CAST(:v AS jsonb) WHERE id = :w"),
            {"w": ws, "v": '{"autopilot_mode": "manual"}'},
        )
        await c.execute(
            text("UPDATE account_group SET settings = settings || CAST(:v AS jsonb) WHERE id = :g"),
            {"g": grp, "v": '{"autopilot_mode": "full"}'},
        )
    r = await api.patch(
        f"/api/v1/workspaces/{ws}", headers=_h(s), json={"settings": {"autopilot_mode": None}}
    )
    assert r.json()["error"]["code"] == "autopilot_not_earned", r.text
    me = (await api.get(f"/api/v1/workspaces/{ws}", headers=_h(s))).json()
    assert me["settings"]["autopilot_mode"] == "manual"  # unchanged
