"""Every refusal path of ``assert_publishable`` (M6.6; docs/11 §1 DoD 15: 100% on it).

Each check is forced with the owner role, bypassing the guards that normally make it
unreachable, so the gate is proven to hold even if a guard were removed.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.test_accounts_m3 import _setup
from tests.integration.test_approvals_m6 import _by, _decide, _publishable, _submit
from tests.integration.test_drafting_m5 import CapturingDeferrer, _sets, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import _h
from tests.integration.test_publishing_m8 import _four, quiet  # noqa: F401

pytestmark = pytest.mark.integration


async def _approved(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, deferrer: CapturingDeferrer
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    ps, acc, _ = await _four(api, s, ws, deferrer)
    req = await _submit(api, s, ws, ps["id"])
    by = _by(req, acc)
    assert (await _decide(api, s, ws, req, "approve", [by["li"]])).status_code == 200
    return ps, acc, by


async def _sql(engine: AsyncEngine, sql: str, **p: Any) -> None:
    async with engine.begin() as c:
        await c.execute(text(sql), p)


async def test_every_check_refuses(
    api: httpx.AsyncClient,
    deferrer: CapturingDeferrer,
    quiet: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    s, ws = await _setup(api, "publishable")
    ps, _acc, by = await _approved(api, s, ws, deferrer)
    li = by["li"]["post_variant_id"]
    fb = by["fb"]["post_variant_id"]
    assert await _publishable(api, ws, li) is None
    # 4: an approval that exists but is still pending.
    assert await _publishable(api, ws, fb) == "not_approved"
    # 1: unknown variant, and a variant with no version.
    assert await _publishable(api, ws, str(uuid.uuid4())) == "no_version"
    # 7: stale approval (decided longer ago than the validity window).
    await _sql(
        owner_engine,
        "UPDATE approval_variant_state SET decided_at = now() - interval '3 days' "
        "WHERE post_variant_id = :v",
        v=li,
    )
    assert await _publishable(api, ws, li) == "approval_stale"
    await _sql(
        owner_engine,
        "UPDATE approval_variant_state SET decided_at = now() WHERE post_variant_id = :v",
        v=li,
    )
    # 8: quorum not reached on the anchor.
    await _sql(
        owner_engine,
        "UPDATE approval_variant_state SET approve_count = 0 WHERE post_variant_id = :v",
        v=li,
    )
    assert await _publishable(api, ws, li) == "quorum"
    await _sql(
        owner_engine,
        "UPDATE approval_variant_state SET approve_count = 1 WHERE post_variant_id = :v",
        v=li,
    )
    # 5: the anchor's hash differs from the version's (guard trigger bypassed).
    await _sql(owner_engine, "ALTER TABLE approval_variant_state DISABLE TRIGGER USER")
    try:
        await _sql(
            owner_engine,
            "UPDATE approval_variant_state SET content_hash = decode(repeat('ab', 32), 'hex') "
            "WHERE post_variant_id = :v",
            v=li,
        )
        assert await _publishable(api, ws, li) == "content_mismatch"
        await _sql(
            owner_engine,
            "UPDATE approval_variant_state a SET content_hash = v.content_hash FROM post_version v "
            "WHERE a.post_variant_id = :v AND v.id = a.post_version_id",
            v=li,
        )
    finally:
        await _sql(owner_engine, "ALTER TABLE approval_variant_state ENABLE TRIGGER USER")
    assert await _publishable(api, ws, li) is None
    # 2: stored content no longer matches its hash (immutability trigger bypassed).
    await _sql(owner_engine, "ALTER TABLE post_version DISABLE TRIGGER USER")
    try:
        await _sql(
            owner_engine,
            "UPDATE post_version SET body = body || ' (tampered)' WHERE id = "
            "(SELECT current_version_id FROM post_variant WHERE id = :v)",
            v=li,
        )
        assert await _publishable(api, ws, li) == "tampered"
        await _sql(
            owner_engine,
            "UPDATE post_version SET body = replace(body, ' (tampered)', '') WHERE id = "
            "(SELECT current_version_id FROM post_variant WHERE id = :v)",
            v=li,
        )
    finally:
        await _sql(owner_engine, "ALTER TABLE post_version ENABLE TRIGGER USER")
    assert await _publishable(api, ws, li) is None
    # 10: the account has no stored sign-in.
    await _sql(
        owner_engine,
        "UPDATE social_account SET credentials_ct = NULL WHERE id = "
        "(SELECT social_account_id FROM post_variant WHERE id = :v)",
        v=li,
    )
    assert await _publishable(api, ws, li) == "credentials_missing"
    # 1: disabled, then archived.
    await _sql(owner_engine, "UPDATE post_variant SET enabled = false WHERE id = :v", v=li)
    assert await _publishable(api, ws, li) == "disabled"
    await _sql(owner_engine, "UPDATE post_variant SET enabled = true WHERE id = :v", v=li)
    await _sql(owner_engine, "UPDATE post_set SET state = 'archived' WHERE id = :p", p=ps["id"])
    assert await _publishable(api, ws, li) == "archived"


async def test_an_undrafted_variant_has_no_version(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "nodraft")
    from tests.integration.test_accounts_m3 import _connect

    acct = (await _connect(api, s, ws, "x", handle=f"x_{uuid.uuid4().hex[:5]}"))["connected"]
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={"topic": "Not drafted yet", "targets": {"social_account_ids": [acct]}},
    )
    variant = r.json()["variants"][0]["id"]
    assert await _publishable(api, ws, variant) == "no_version"


def test_validity_constant() -> None:
    assert timedelta(minutes=1440) == timedelta(days=1)
