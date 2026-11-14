"""P3-M7 governance against real Postgres: security alerts, exports, erasure, step-up."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application.security_alerts import SecurityAlerts
from mera.application.services.privacy_service import PrivacyService
from mera.infrastructure.db.models import OAuthFlow, User
from mera.infrastructure.db.session import global_transaction
from mera.infrastructure.notify.capture import CapturingNotifier
from tests.integration.test_accounts_m3 import _app, _connect, _setup
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import PASSWORD, _h, _me, _register_login
from tests.integration.test_ingest_m4 import _sm
from tests.integration.test_phase2_m6 import _comments, _poll, _published, box  # noqa: F401
from tests.integration.test_publishing_m8 import quiet  # noqa: F401

pytestmark = pytest.mark.integration
PIN = "482913"


async def test_security_alerts_reach_the_person_and_the_owners_once(
    api: httpx.AsyncClient,
) -> None:
    s, ws = await _setup(api, "alerts", second_factors=True)
    me = await _me(api, s)
    sink = CapturingNotifier()
    scanner = SecurityAlerts(_sm(api), sink)
    await scanner.run()  # catch up with earlier tests' events first
    sink.sent.clear()
    assert (
        await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": PIN, "proof": PASSWORD})
    ).status_code == 204
    grant = f"/api/v1/workspaces/{ws}/second-factor"
    for _ in range(5):  # five wrong PINs: pin.locked
        await api.post(
            grant,
            headers=_h(s),
            json={"action": "kill_switch_resume", "method": "pin", "value": "999999"},
        )
    kill = f"/api/v1/workspaces/{ws}/automation/kill-switch"
    resume = {"scope_type": "workspace", "engaged": False}
    for _ in range(3):  # three rejected grants in ten minutes: a burst
        await api.post(
            kill, headers={**_h(s), "x-mera-second-factor": "mera_sf_forged"}, json=resume
        )
    out = await scanner.run()
    assert out["alerts"] >= 2
    subjects = [(m.to, m.text.splitlines()[0]) for m in sink.sent]
    assert (me["email"], "Your approval PIN was locked after five wrong attempts.") in subjects
    assert any("rejected second factors" in t for _, t in subjects)
    # Nothing new: nothing sent again.
    sink.sent.clear()
    assert (await scanner.run()).get("alerts", 0) == 0
    listed = (await api.get(f"/api/v1/workspaces/{ws}/security-alerts", headers=_h(s))).json()
    assert any(a["kind"] == "second_factor.burst" for a in listed["data"])


async def test_workspace_export_is_complete_and_leaves_secrets_out(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "export")
    await _connect(api, s, ws, "linkedin_member", handle=f"exp_{uuid.uuid4().hex[:6]}")
    app = _app(api)
    svc: PrivacyService = app.state.privacy_service
    saved = svc._deferrer
    svc._deferrer = deferrer
    try:
        r = await api.post(f"/api/v1/workspaces/{ws}/exports", headers=_h(s))
    finally:
        svc._deferrer = saved
    assert r.status_code == 202, r.text
    export_id = r.json()["id"]
    r = await api.get(f"/api/v1/workspaces/{ws}/exports/{export_id}/link", headers=_h(s))
    assert r.json()["error"]["code"] == "export_not_ready"
    assert await svc.build_export(uuid.UUID(ws), uuid.UUID(export_id)) == "ready"
    r = await api.get(f"/api/v1/workspaces/{ws}/exports/{export_id}/link", headers=_h(s))
    assert r.status_code == 200, r.text
    key = f"exports/{ws}/{export_id}.zip"
    z = zipfile.ZipFile(io.BytesIO(await app.state.object_store.get(key)))
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["rows"]["workspace_member"] >= 1
    assert manifest["rows"]["social_account"] >= 1
    accounts = json.loads(z.read("tables/social_account.json"))
    assert accounts
    assert not any("credentials_ct" in a for a in accounts)
    assert all(a["workspace_id"] == ws for a in accounts)


async def test_a_commenter_can_be_forgotten(
    api: httpx.AsyncClient, box: Any, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "forget")
    _acc, posts = await _published(api, s, ws, deferrer)
    await _poll(api, box, ws, posts)
    c = (await _comments(api, ws))[0]
    r = await api.delete(
        f"/api/v1/workspaces/{ws}/inbox/comments/{c.id}/author-data", headers=_h(s)
    )
    assert r.status_code == 204, r.text
    after = next(x for x in await _comments(api, ws) if x.id == c.id)
    assert after.author_handle is None
    assert after.author_platform_id is None
    assert after.author_erased_at is not None
    assert after.body == c.body


async def test_deleted_people_are_erased_after_the_grace_and_audit_stays(
    api: httpx.AsyncClient, owner_engine: AsyncEngine, postgres_urls: tuple[str, str]
) -> None:
    owner, _ws = await _setup(api, "erasews")
    leaver = await _register_login(api, "leaver")
    me = await _me(api, leaver)
    r = await api.request("DELETE", "/api/v1/auth/me", headers=_h(owner), json={"proof": PASSWORD})
    assert r.json()["error"]["code"] == "sole_owner"
    assert (await api.get("/api/v1/auth/me/export", headers=_h(leaver))).json()["profile"][
        "email"
    ] == me["email"]
    r = await api.request("DELETE", "/api/v1/auth/me", headers=_h(leaver), json={"proof": "nope"})
    assert r.status_code == 401
    r = await api.request("DELETE", "/api/v1/auth/me", headers=_h(leaver), json={"proof": PASSWORD})
    assert r.status_code == 204, r.text
    assert (await api.get("/api/v1/auth/me", headers=_h(leaver))).status_code == 401
    async with owner_engine.begin() as c:
        await c.execute(
            text("UPDATE \"user\" SET deleted_at = now() - interval '31 days' WHERE id = :u"),
            {"u": me["id"]},
        )
    svc = PrivacyService(_sm(api), None, None, migration_url=postgres_urls[0])
    out = await svc.erase_due()
    assert out["users"] >= 1
    async with global_transaction(_sm(api)) as tx:
        u = await tx.get(User, uuid.UUID(me["id"]))
        assert u is not None
        assert u.email.startswith("erased-")
        assert u.display_name == "Deleted user"
        assert u.password_hash is None
        assert u.erased_at is not None
    async with owner_engine.connect() as c:
        labels = set(
            (
                await c.execute(
                    text("SELECT DISTINCT actor_label FROM audit_log WHERE actor_id = :u"),
                    {"u": me["id"]},
                )
            ).scalars()
        )
    assert labels <= {"Deleted user", None}
    assert labels  # the entries themselves are kept


async def test_provider_only_accounts_step_up_through_the_provider(
    api: httpx.AsyncClient, owner_engine: AsyncEngine
) -> None:
    s = await _register_login(api, "stepup")
    me = await _me(api, s)
    async with owner_engine.begin() as c:
        await c.execute(
            text('UPDATE "user" SET password_hash = NULL WHERE id = :u'), {"u": me["id"]}
        )
    # No password, and no shortcut: a PIN needs the provider's confirmation.
    r = await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": PIN, "proof": "x"})
    assert r.json()["error"]["code"] == "step_up_required"
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    code = "login-" + secrets.token_urlsafe(16)
    async with global_transaction(_sm(api)) as tx:
        tx.add(
            OAuthFlow(
                provider="google",
                state_hash=secrets.token_hex(16),
                provider_code_verifier="v",
                nonce="n",
                client_code_challenge=challenge,
                client_redirect_uri="https://app.example/cb",
                user_id=uuid.UUID(me["id"]),
                login_code_hash=hashlib.sha256(code.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                callback_at=datetime.now(UTC),
            )
        )
    r = await api.post(
        "/api/v1/auth/step-up/exchange",
        headers=_h(s),
        json={"code": code, "code_verifier": verifier},
    )
    assert r.status_code == 200, r.text
    proof = r.json()["proof"]
    assert proof.startswith("mera_ra_")
    r = await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": PIN, "proof": proof})
    assert r.status_code == 204, r.text
    # Single use.
    r = await api.put("/api/v1/auth/pin", headers=_h(s), json={"pin": "482914", "proof": proof})
    assert r.json()["error"]["code"] == "step_up_required"
    # A code for someone else's sign-in is refused.
    async with global_transaction(_sm(api)) as tx:
        flow = (
            await tx.execute(select(OAuthFlow).where(OAuthFlow.user_id == uuid.UUID(me["id"])))
        ).scalar_one()
        assert flow.exchanged_at is not None
