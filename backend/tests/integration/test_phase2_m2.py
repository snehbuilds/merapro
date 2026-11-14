"""P2-M2 (docs/13): two-step sign-in, the approval PIN, second factors on high-risk
actions (web and bot), named escalation, out-of-office cover, per-account rate budgets."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application.services.security_service import SecurityService
from mera.domain.context import WorkspaceContext
from mera.infrastructure.crypto import totp
from mera.infrastructure.db.models import ApprovalRequest, ScheduledPost, User
from mera.infrastructure.db.session import global_transaction, workspace_transaction
from tests.integration.conftest import make_api
from tests.integration.test_accounts_m3 import _app, _connect, _setup
from tests.integration.test_approvals_m6 import _by, _decide, _submit
from tests.integration.test_drafting_m5 import (
    CapturingDeferrer,
    _group,
    _run_queued,
    _sets,
    deferrer,  # noqa: F401 - shared fixture
)
from tests.integration.test_identity_m2 import PASSWORD, _add_member, _h, _me, _register_login
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_publishing_m8 import _four, _publisher, _slots, quiet  # noqa: F401
from tests.integration.test_telegram_m7 import _bot, _btn, _card, _msg, _pair, _tap

pytestmark = pytest.mark.integration

PIN = "482916"


async def _secret_of(api: httpx.AsyncClient, user_id: str) -> str:
    """The user's TOTP secret, decrypted the way the service does (tests only)."""
    security: SecurityService = _app(api).state.security_service
    async with global_transaction(_sm(api)) as s:
        u = await s.get(User, uuid.UUID(user_id))
        assert u is not None
        ct = u.totp_secret_ct or u.totp_pending_ct
        assert ct is not None
        return security._open(u.id, ct)


async def _user_row(api: httpx.AsyncClient, email: str) -> User:
    async with global_transaction(_sm(api)) as tx:
        return (await tx.execute(select(User).where(User.email == email))).scalar_one()


async def _enrol(api: httpx.AsyncClient, s: dict[str, Any]) -> tuple[str, list[str]]:
    # A session alone cannot enrol an authenticator: the password first (release review #7).
    r = await api.post("/api/v1/auth/mfa/setup", headers=_h(s), json={"proof": "wrong pw"})
    assert r.json()["error"]["code"] == "invalid_password"
    r = await api.post("/api/v1/auth/mfa/setup", headers=_h(s), json={"proof": PASSWORD})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/Mera:")
    r = await api.post(
        "/api/v1/auth/mfa/confirm",
        headers=_h(s),
        json={"code": totp.code_at(secret, totp.counter_at())},
    )
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


async def _login(api: httpx.AsyncClient, email: str) -> httpx.Response:
    return await api.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})


async def test_two_step_sign_in(api: httpx.AsyncClient) -> None:
    s = await _register_login(api, "mfa")
    secret, codes = await _enrol(api, s)
    assert len(codes) == 10
    # Turning MFA on signs every earlier session out.
    assert (await api.get("/api/v1/auth/me", headers=_h(s))).status_code == 401
    r = await _login(api, s["email"])
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "mfa_required"
    challenge = r.json()["error"]["detail"]
    assert challenge.startswith("mera_mfa_")
    assert "access_token" not in r.text
    # The code used to confirm cannot be used again (replay), the next one works. The step
    # comes from the stored counter, not the clock, so a 30-second boundary cannot flake it.
    used = (await _user_row(api, s["email"])).totp_last_counter
    assert used is not None
    bad = await api.post(
        "/api/v1/auth/mfa/verify",
        json={"mfa_token": challenge, "code": totp.code_at(secret, used)},
    )
    assert bad.json()["error"]["code"] == "invalid_mfa_code"
    good = await api.post(
        "/api/v1/auth/mfa/verify",
        json={"mfa_token": challenge, "code": totp.code_at(secret, used + 1)},
    )
    assert good.status_code == 200, good.text
    session = {**s, **good.json()}
    assert (await api.get("/api/v1/auth/me", headers=_h(session))).status_code == 200
    # A challenge is single use.
    again = await api.post(
        "/api/v1/auth/mfa/verify",
        json={"mfa_token": challenge, "code": totp.code_at(secret, used + 1)},
    )
    assert again.json()["error"]["code"] == "invalid_mfa_challenge"
    # A recovery code works once.
    ch2 = (await _login(api, s["email"])).json()["error"]["detail"]
    r = await api.post("/api/v1/auth/mfa/verify", json={"mfa_token": ch2, "code": codes[0]})
    assert r.status_code == 200
    ch3 = (await _login(api, s["email"])).json()["error"]["detail"]
    r = await api.post("/api/v1/auth/mfa/verify", json={"mfa_token": ch3, "code": codes[0]})
    assert r.json()["error"]["code"] == "invalid_mfa_code"
    # Five wrong codes burn the challenge.
    for _ in range(4):
        await api.post("/api/v1/auth/mfa/verify", json={"mfa_token": ch3, "code": "000000"})
    r = await api.post("/api/v1/auth/mfa/verify", json={"mfa_token": ch3, "code": codes[1]})
    assert r.json()["error"]["code"] == "invalid_mfa_challenge"
    st = (await api.get("/api/v1/auth/security", headers=_h(session))).json()
    assert st["mfa_enabled"] is True
    assert st["recovery_codes_left"] == 9
    # ...and those were five wrong codes in a row for this person, across challenges: codes
    # are now refused everywhere for 15 minutes, even a right one (release review #8).
    assert st["codes_locked_until"] is not None
    ch4 = (await _login(api, s["email"])).json()["error"]["detail"]
    r = await api.post("/api/v1/auth/mfa/verify", json={"mfa_token": ch4, "code": codes[1]})
    assert r.status_code == 429
    r = await api.post("/api/v1/auth/mfa/disable", headers=_h(session), json={"proof": codes[1]})
    assert r.status_code == 429
    async with global_transaction(_sm(api)) as tx:  # the 15 minutes pass
        row = await tx.get(User, (await _user_row(api, s["email"])).id, with_for_update=True)
        assert row is not None
        row.totp_locked_until = None
    # Turning it off needs a current code.
    r = await api.post("/api/v1/auth/mfa/disable", headers=_h(session), json={"proof": "123"})
    assert r.status_code == 401
    r = await api.post("/api/v1/auth/mfa/disable", headers=_h(session), json={"proof": codes[2]})
    assert r.status_code == 204
    assert (await _login(api, s["email"])).status_code == 200


async def test_admins_without_mfa_act_as_editors_when_enforced(
    postgres_urls: tuple[str, str],
) -> None:
    async with make_api(
        postgres_urls, rate_limit_enabled=False, mfa_required_for_admins=True
    ) as api:
        s = await _register_login(api, "enforced")
        r = await api.post(
            "/api/v1/workspaces",
            headers=_h(s),
            json={"name": "Enforced", "slug": f"enf-{uuid.uuid4().hex[:6]}", "timezone": "UTC"},
        )
        assert r.status_code == 201, r.text
        ws = r.json()["id"]
        invite = {"email": "someone@example.com", "role": "viewer"}
        r = await api.post(
            f"/api/v1/workspaces/{ws}/members/invitations", headers=_h(s), json=invite
        )
        assert r.status_code == 403  # Owner, but no MFA yet: an Editor's reach
        assert (await api.get("/api/v1/auth/security", headers=_h(s))).json()["mfa_required"]
        secret, _ = await _enrol(api, s)
        ch = (await _login(api, s["email"])).json()["error"]["detail"]
        r = await api.post(
            "/api/v1/auth/mfa/verify",
            json={"mfa_token": ch, "code": totp.code_at(secret, totp.counter_at() + 1)},
        )
        s2 = {**s, **r.json()}
        r = await api.post(
            f"/api/v1/workspaces/{ws}/members/invitations", headers=_h(s2), json=invite
        )
        assert r.status_code == 201, r.text


async def test_pin_and_second_factor_grants(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "sfgrant", second_factors=True)
    kill = f"/api/v1/workspaces/{ws}/automation/kill-switch"
    assert (
        await api.post(kill, headers=_h(s), json={"scope_type": "workspace", "engaged": True})
    ).status_code == 200  # stopping never needs a second factor
    resume = {"scope_type": "workspace", "engaged": False}
    r = await api.post(kill, headers=_h(s), json=resume)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "second_factor_required"
    assert r.json()["error"]["field_errors"][0]["field"] == "kill_switch_resume"
    # A PIN needs a weak one refused and the password as proof.
    bad = await api.put(
        "/api/v1/auth/pin", headers=_h(s), json={"pin": "111111", "proof": PASSWORD}
    )
    assert bad.json()["error"]["code"] == "invalid_pin"
    wrong = await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": PIN, "proof": "nope"})
    assert wrong.status_code == 401
    assert (
        await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": PIN, "proof": PASSWORD})
    ).status_code == 204
    grant = f"/api/v1/workspaces/{ws}/second-factor"
    r = await api.post(
        grant, headers=_h(s), json={"action": "kill_switch_resume", "method": "pin", "value": PIN}
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    # A grant for another action does not work here; this one works once.
    other = (
        await api.post(
            grant,
            headers=_h(s),
            json={"action": "account_disconnect", "method": "pin", "value": PIN},
        )
    ).json()["token"]
    r = await api.post(kill, headers={**_h(s), "x-mera-second-factor": other}, json=resume)
    assert r.json()["error"]["code"] == "second_factor_required"
    r = await api.post(kill, headers={**_h(s), "x-mera-second-factor": token}, json=resume)
    assert r.status_code == 200, r.text
    await api.post(kill, headers=_h(s), json={"scope_type": "workspace", "engaged": True})
    r = await api.post(kill, headers={**_h(s), "x-mera-second-factor": token}, json=resume)
    assert r.json()["error"]["code"] == "second_factor_required"
    # Both refusals rolled their action back, and both are still on record (review #11).
    async with owner_engine.connect() as conn:
        rejected = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM security_event "
                    "WHERE workspace_id = :ws AND kind = 'second_factor.rejected'"
                ),
                {"ws": ws},
            )
        ).scalar_one()
    assert rejected == 2
    # Five wrong PINs lock it.
    for _ in range(5):
        r = await api.post(
            grant,
            headers=_h(s),
            json={"action": "kill_switch_resume", "method": "pin", "value": "999999"},
        )
    r = await api.post(
        grant, headers=_h(s), json={"action": "kill_switch_resume", "method": "pin", "value": PIN}
    )
    assert r.status_code == 429


async def test_policy_switching_off_takes_an_owner(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "sfpolicy", second_factors=True)
    admin = await _register_login(api, "sfadmin")
    await _add_member(owner_engine, ws, (await _me(api, admin))["id"], "admin")
    url = f"/api/v1/workspaces/{ws}/second-factor-policy"
    assert (await api.get(url, headers=_h(admin))).json()["data"]["account_disconnect"] is True
    r = await api.put(url, headers=_h(admin), json={"changes": {"account_disconnect": False}})
    assert r.status_code == 403
    r = await api.put(url, headers=_h(s), json={"changes": {"account_disconnect": False}})
    assert r.json()["data"]["account_disconnect"] is False
    r = await api.put(url, headers=_h(admin), json={"changes": {"account_disconnect": True}})
    assert r.json()["data"]["account_disconnect"] is True


async def test_company_page_approval_needs_a_second_factor_on_the_web_and_in_the_bot(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "sfpage", second_factors=True)
    await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": PIN, "proof": PASSWORD})
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    # A personal account approves without a second factor.
    assert (await _decide(api, s, ws, req, "approve", [by["li"]])).status_code == 200
    # The Facebook Page does not, on the web.
    r = await _decide(api, s, ws, req, "approve", [by["fb"]])
    assert r.json()["error"]["code"] == "second_factor_required"
    # In the bot: the tap parks the decision and asks for /pin.
    bot, ch = _bot(api)
    bot._security_svc = _app(api).state.security_service
    chat = f"c{uuid.uuid4().hex[:8]}"
    await _pair(api, bot, s, ws, chat)  # pairing delivers the waiting card
    mid, card = _card(ch, chat)
    assert await bot.handle_update(_tap(chat, mid, _btn(card, "✅ Approve all"))) == "pin_required"
    assert "/pin" in ch.sent[-1]["message"].text
    assert await bot.handle_update(_msg(chat, "/pin 000000")) == "pin_wrong"
    assert await bot.handle_update(_msg(chat, f"/pin {PIN}")) == "decided:approved"
    assert await bot.handle_update(_msg(chat, f"/pin {PIN}")) == "pin_nothing_waiting"
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        done = await tx.get(ApprovalRequest, uuid.UUID(req["id"]))
        assert done is not None
        assert done.state == "approved"


async def test_named_escalation_and_out_of_office(
    api: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "away")
    alex = await _register_login(api, "alexaway")
    sam = await _register_login(api, "samcover")
    alex_id, sam_id = (await _me(api, alex))["id"], (await _me(api, sam))["id"]
    await _add_member(owner_engine, ws, alex_id, "approver")
    await _add_member(owner_engine, ws, sam_id, "approver")
    li = (await _connect(api, s, ws, "linkedin_member", handle="away_li"))["connected"]
    grp = await _group(api, s, ws, "Named", [li])
    r = await api.put(
        f"/api/v1/workspaces/{ws}/approval-policies",
        headers=_h(s),
        json={
            "scope_type": "account_group",
            "scope_id": grp,
            "approver_user_ids": [alex_id],
            "escalate_to_user_ids": [sam_id],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["escalate_to_user_ids"] == [sam_id]
    # Alex is away for a week; Sam covers.
    back = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    r = await api.put(
        f"/api/v1/workspaces/{ws}/members/{alex_id}/away",
        headers=_h(alex),
        json={"away_until": back, "delegate_user_id": sam_id},
    )
    assert r.status_code == 200, r.text
    r = await api.post(
        _sets(ws), headers=_h(s), json={"topic": "Covered", "targets": {"social_account_ids": [li]}}
    )
    await _run_queued(api, deferrer)
    req = await _submit(api, s, ws, r.json()["id"])
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        snap = (await tx.get(ApprovalRequest, uuid.UUID(req["id"]))).policy_snapshot  # type: ignore[union-attr]
    assert snap["escalate_to"] == [sam_id]
    variant = next(iter(snap["variants"].values()))
    assert variant["approvers"] == [sam_id]  # the delegate stands in for Alex
    sam_view = (
        await api.get(f"/api/v1/workspaces/{ws}/approval-requests/{req['id']}", headers=_h(sam))
    ).json()
    assert sam_view["variants"][0]["can_decide"] is True
    alex_view = (
        await api.get(f"/api/v1/workspaces/{ws}/approval-requests/{req['id']}", headers=_h(alex))
    ).json()
    assert alex_view["variants"][0]["can_decide"] is False
    # Someone else cannot set Alex's away; Alex can come back early.
    r = await api.put(
        f"/api/v1/workspaces/{ws}/members/{alex_id}/away",
        headers=_h(sam),
        json={"away_until": None},
    )
    assert r.status_code == 403
    r = await api.put(
        f"/api/v1/workspaces/{ws}/members/{alex_id}/away",
        headers=_h(alex),
        json={"away_until": None},
    )
    assert r.json()["away_until"] is None


async def test_rate_budget_holds_instead_of_failing(
    api: httpx.AsyncClient,
    owner_engine: AsyncEngine,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
) -> None:
    s, ws = await _setup(api, "budget")
    x = (await _connect(api, s, ws, "x", handle=f"bud_{uuid.uuid4().hex[:4]}"))["connected"]
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE social_account SET rate_budget = '{\"posts_per_24h\": 1}' WHERE id = :i"),
            {"i": x},
        )
    pub, _ = _publisher(api)
    outcomes = []
    for topic in ("First post today about queues", "Second post today about indexes"):
        r = await api.post(
            _sets(ws), headers=_h(s), json={"topic": topic, "targets": {"social_account_ids": [x]}}
        )
        await _run_queued(api, deferrer)
        req = await _submit(api, s, ws, r.json()["id"])
        assert (await _decide(api, s, ws, req, "approve", None)).status_code == 200
        for sp in await _slots(api, ws, r.json()["id"]):
            outcomes.append(
                await pub.publish(uuid.UUID(ws), sp.id, now=sp.scheduled_for + timedelta(seconds=1))
            )
    assert outcomes == ["published", "budget"]
    async with workspace_transaction(_sm(api), WorkspaceContext(uuid.UUID(ws))) as tx:
        held = (
            await tx.execute(
                select(ScheduledPost).where(
                    ScheduledPost.social_account_id == uuid.UUID(x),
                    ScheduledPost.state == "queued",
                )
            )
        ).scalar_one()
        assert "24 hours" in (held.last_error or "")
        assert held.next_attempt_at is not None
        assert held.next_attempt_at > held.scheduled_for + timedelta(hours=23)
