"""P3-M2 A/B tests against real Postgres."""

from __future__ import annotations

import httpx
import pytest

from tests.integration.test_accounts_m3 import _setup
from tests.integration.test_approvals_m6 import _submit
from tests.integration.test_drafting_m5 import CapturingDeferrer, deferrer  # noqa: F401
from tests.integration.test_identity_m2 import _h
from tests.integration.test_publishing_m8 import _four, quiet  # noqa: F401

pytestmark = pytest.mark.integration


async def test_version_b_is_its_own_approved_post_and_results_wait_for_evidence(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, quiet: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "abtest")
    ps, acc, _ = await _four(api, s, ws, deferrer)
    li = next(v for v in ps["variants"] if v["social_account_id"] == acc["li"])
    base = f"/api/v1/workspaces/{ws}/post-sets"
    r = await api.post(
        f"{base}/{ps['id']}/variants/{li['id']}/ab",
        headers=_h(s),
        json={"hypothesis": "A question as the hook", "body": "What if your replicas read faster?"},
    )
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["id"] != ps["id"]
    assert [v["social_account_id"] for v in b["variants"]] == [acc["li"]]
    assert b["variants"][0]["current_version"]["body"].startswith("What if")
    again = await api.post(
        f"{base}/{ps['id']}/variants/{li['id']}/ab", headers=_h(s), json={"hypothesis": "x y z"}
    )
    assert again.json()["error"]["code"] == "ab_exists"
    # B goes through approval like any post.
    req = await _submit(api, s, ws, b["id"])
    assert req["state"] == "pending"
    results = (await api.get(f"{base}/{ps['id']}/ab", headers=_h(s))).json()
    assert results["min_impressions"] == 500
    [test] = results["tests"]
    assert test["hypothesis"] == "A question as the hook"
    assert test["outcome"] == "too_early"  # nothing published yet
    from_b = (await api.get(f"{base}/{b['id']}/ab", headers=_h(s))).json()
    assert from_b["tests"][0]["b"]["post_set_id"] == b["id"]
