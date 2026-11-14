"""M5 acceptance (docs/11 §M5, Checkpoint A): post sets, per-account variants, immutable
versions and hashes, the Writer/Hashtag/Editor pipeline, guardrails, voices, cost."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from mera.application.drafting.factory import drafting_config
from mera.application.drafting.pipeline import Drafting
from mera.domain.policies.content_hash import compute_content_hash
from mera.domain.ports.llm import Message, ModelConfig, Structured
from mera.infrastructure.db.models import AgentStep
from mera.infrastructure.llm.fake import FakeLLM
from tests.integration.test_accounts_m3 import _app, _connect, _grp, _setup
from tests.integration.test_identity_m2 import _add_member, _h, _me, _register_login
from tests.integration.test_ingest_m4 import (
    PG_BODY,
    FakeFetcher,
    _ingestion,
    _rss,
    _sm,
    _source,
    fetcher,  # noqa: F401 - shared fixture
)

pytestmark = pytest.mark.integration


class CapturingDeferrer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def defer(
        self,
        task_name: str,
        *,
        queue: str,
        lock: str | None = None,
        queueing_lock: str | None = None,
        **kwargs: Any,
    ) -> bool:
        if any(c["queueing_lock"] == queueing_lock and not c.get("ran") for c in self.calls):
            return False
        self.calls.append(
            {"task": task_name, "queue": queue, "queueing_lock": queueing_lock, **kwargs}
        )
        return True


@pytest.fixture
async def deferrer(api: httpx.AsyncClient) -> AsyncIterator[CapturingDeferrer]:
    svc = _app(api).state.post_service
    original = svc._deferrer
    fake = CapturingDeferrer()
    svc._deferrer = fake
    yield fake
    svc._deferrer = original


def _drafting(api: httpx.AsyncClient, llm: Any = None, **cfg: Any) -> Drafting:
    config = replace(drafting_config(_app(api).state.settings), **cfg)
    return Drafting(_sm(api), llm=llm or FakeLLM(), config=config)


async def _run_queued(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, drafting: Drafting | None = None
) -> None:
    """Play the worker: run every queued drafting job once."""
    d = drafting or _app(api).state.drafting
    for call in deferrer.calls:
        if call.get("ran"):
            continue
        call["ran"] = True
        await d.draft(
            uuid.UUID(call["workspace_id"]),
            uuid.UUID(call["post_set_id"]),
            variant_ids=[uuid.UUID(v) for v in call["variant_ids"]]
            if call["variant_ids"]
            else None,
            mode=call["mode"],
            instruction=call["instruction"],
        )


def _sets(ws: str, sid: str = "", suffix: str = "") -> str:
    return f"/api/v1/workspaces/{ws}/post-sets" + (f"/{sid}" if sid else "") + suffix


async def _story(api: httpx.AsyncClient, s: dict[str, Any], ws: str, fetcher: FakeFetcher) -> str:
    """One ingested, summarised article."""
    url = f"https://story-{uuid.uuid4().hex[:6]}.example/feed"
    src = await _source(api, s, ws, url, name="PG News")
    fetcher.pages[url] = _rss([("PostgreSQL 18 Released", url.replace("/feed", "/pg18"), PG_BODY)])
    ing = _ingestion(api, fetcher)
    await ing.fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))
    await ing.enrich_pending(uuid.UUID(ws))
    items = (await api.get(f"/api/v1/workspaces/{ws}/content-items", headers=_h(s))).json()["data"]
    return str(items[0]["id"])


async def _accounts(api: httpx.AsyncClient, s: dict[str, Any], ws: str) -> dict[str, str]:
    out = {}
    for name, platform, kind in [
        ("x_main", "x", "personal"),
        ("x_tech", "x", "personal"),
        ("li_work", "linkedin_member", "personal"),
        ("bsky", "bluesky", "personal"),
        ("masto", "mastodon", "personal"),
    ]:
        out[name] = (await _connect(api, s, ws, platform, handle=name, kind=kind))["connected"]
    return out


async def _group(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, name: str, ids: list[str], **kw: Any
) -> str:
    r = await api.post(
        _grp(ws), headers=_h(s), json={"name": name, "social_account_ids": ids, **kw}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# --------------------------------------------------------------- Checkpoint A


async def test_one_story_becomes_five_distinct_variants(
    api: httpx.AsyncClient, fetcher: FakeFetcher, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "checkpointa")
    acc = await _accounts(api, s, ws)
    tech = await _group(api, s, ws, "Tech News", list(acc.values()))
    other = await _group(api, s, ws, "Other", [acc["x_main"]])  # x_main reachable twice
    item = await _story(api, s, ws, fetcher)

    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={"content_item_id": item, "targets": {"account_group_ids": [tech, other]}},
    )
    assert r.status_code == 201, r.text
    ps = r.json()
    assert len(ps["variants"]) == 5  # deduplicated
    assert all(v["via_account_group_id"] == tech for v in ps["variants"])
    offsets = [v["stagger_offset_seconds"] for v in ps["variants"]]
    assert offsets[0] == 0
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == 5
    assert ps["generation_status"] == "queued"
    assert deferrer.calls[-1]["task"] == "drafting:draft"
    assert deferrer.calls[-1]["queue"] == "agents"
    # A second request while one is queued is refused, not doubled.
    r = await api.post(_sets(ws, ps["id"], "/generate"), headers=_h(s))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "generation_in_progress")

    await _run_queued(api, deferrer)
    ps = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    assert ps["generation_status"] == "idle"
    assert ps["generation_error"] is None
    bodies = [v["current_version"]["body"] for v in ps["variants"]]
    assert len(set(bodies)) == 5  # five distinct posts
    for v in ps["variants"]:
        cv = v["current_version"]
        assert v["state"] == "drafting", (v["nickname"], v["guardrails"])
        assert len(cv["content_hash"]) == 64
        assert cv["source"] == "agent"
        assert cv["char_count"] <= cv["char_limit"]
        # The source always travels with the post; only body-placement platforms put the
        # link in the text (LinkedIn: first comment; X: reply), per the platform skills.
        assert (cv["link_url"] or "").startswith("https://")
        assert ("https://" in cv["body"]) == (v["platform"] in ("bluesky", "mastodon", "facebook"))
        assert 0 <= cv["confidence_score"] <= 1
        assert cv["agent_notes"]["angle"]
        assert cv["agent_notes"]["voice_source"] == "built-in"
        assert {g["guardrail_key"] for g in v["guardrails"]} >= {
            "banned_words",
            "pii",
            "platform_policy",
            "duplicate_post",
        }
        assert all(
            t["basis"] == "content_only" and t["sample_size"] == 0
            for t in cv["hashtag_suggestions"]
        )
    x = [v for v in ps["variants"] if v["platform"] == "x"]
    assert x[0]["current_version"]["body"] != x[1]["current_version"]["body"]
    cross = [g for g in ps["set_guardrails"] if g["guardrail_key"] == "cross_account_duplicate"]
    assert cross
    assert all(g["outcome"] != "block" for g in cross)
    assert ps["state"] == "drafting"
    assert len(ps["composite_hash"]) == 64

    # Per-account cost attribution: every agent step names its variant.
    async with _sm(api)() as sess:
        await sess.execute(text("SELECT set_config('mera.workspace_id', :w, false)"), {"w": ws})
        steps = list(
            (
                await sess.execute(
                    select(AgentStep).where(
                        AgentStep.agent_key.in_(["writer", "editor", "hashtag"])
                    )
                )
            ).scalars()
        )
    assert {st.post_variant_id for st in steps} == {uuid.UUID(v["id"]) for v in ps["variants"]}
    costs = (await api.get(f"/api/v1/workspaces/{ws}/agent-runs/costs", headers=_h(s))).json()
    labels = " ".join(row["label"] for row in costs["by_account"])
    assert all(h in labels for h in ("x_main", "x_tech", "li_work", "bsky", "masto"))
    assert {row["key"] for row in costs["by_agent"]} >= {"writer", "editor", "hashtag", "research"}
    runs = (
        await api.get(
            f"/api/v1/workspaces/{ws}/agent-runs",
            headers=_h(s),
            params={"workflow": "draft_from_content"},
        )
    ).json()["data"]
    assert len(runs) == 1
    assert runs[0]["state"] == "succeeded"
    run = (
        await api.get(f"/api/v1/workspaces/{ws}/agent-runs/{runs[0]['id']}", headers=_h(s))
    ).json()
    assert run["step_count"] == len(run["steps"]) >= 20  # 5 x (writer, embedding, hashtag, editor)
    agents = (await api.get(f"/api/v1/workspaces/{ws}/agents", headers=_h(s))).json()["data"]
    assert {a["agent_key"] for a in agents} == {"research", "writer", "editor", "hashtag"}


# ---------------------------------------------------------- versions and hashes


async def test_versions_are_immutable_and_bind_the_account(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "immutable")
    a = (await _connect(api, s, ws, "mastodon", handle="m1"))["connected"]
    ps = (
        await api.post(
            _sets(ws),
            headers=_h(s),
            json={
                "topic": "Postgres 18 is out",
                "targets": {"social_account_ids": [a]},
                "generate": False,
            },
        )
    ).json()
    vid = ps["variants"][0]["id"]
    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{vid}/versions"),
        headers=_h(s),
        json={
            "body": "First take on Postgres 18. https://e.com/pg",
            "hashtags": ["#Postgres", "postgres"],
        },
    )
    assert r.status_code == 201, r.text
    v1 = r.json()["variants"][0]["current_version"]
    assert v1["version_no"] == 1
    assert v1["hashtags"] == ["postgres"]
    assert v1["source"] == "human"
    expected = compute_content_hash(
        platform="mastodon",
        social_account_id=uuid.UUID(a),
        body=v1["body"],
        thread_parts=None,
        hashtags=["postgres"],
        link_url=None,
        media=[],
    )
    assert v1["content_hash"] == expected.hex()
    composite_1 = r.json()["composite_hash"]

    async with owner_engine.begin() as conn:
        with pytest.raises(Exception, match="immutable"):
            async with conn.begin_nested():
                await conn.execute(
                    text("UPDATE post_version SET body = 'tampered' WHERE id = :i"), {"i": v1["id"]}
                )
        with pytest.raises(Exception, match="immutable"):
            async with conn.begin_nested():
                await conn.execute(text("DELETE FROM post_version WHERE id = :i"), {"i": v1["id"]})

    # Optimistic concurrency is required once there is text, then a second version.
    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{vid}/versions"), headers=_h(s), json={"body": "Edit"}
    )
    assert r.json()["error"]["code"] == "based_on_version_required"
    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{vid}/versions"),
        headers=_h(s),
        json={"body": "Edit", "based_on_version_id": str(uuid.uuid4())},
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "version_conflict")
    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{vid}/versions"),
        headers=_h(s),
        json={"body": "Second take. https://e.com/pg", "based_on_version_id": v1["id"]},
    )
    assert r.json()["variants"][0]["current_version"]["version_no"] == 2
    assert r.json()["composite_hash"] != composite_1
    history = (
        await api.get(_sets(ws, ps["id"], f"/variants/{vid}/versions"), headers=_h(s))
    ).json()["data"]
    assert [h["version_no"] for h in history] == [2, 1]
    # Re-saving identical content is not a new version (an approval would survive it).
    v2 = r.json()["variants"][0]["current_version"]
    r2 = await api.post(
        _sets(ws, ps["id"], f"/variants/{vid}/versions"),
        headers=_h(s),
        json={"body": "Second take. https://e.com/pg", "based_on_version_id": v2["id"]},
    )
    assert r2.json()["variants"][0]["current_version"]["id"] == v2["id"]
    r2 = await api.post(
        _sets(ws, ps["id"], f"/variants/{vid}/versions"),
        headers=_h(s),
        json={"body": "x", "link_url": "http://169.254.169.254/", "based_on_version_id": v2["id"]},
    )
    assert r2.json()["error"]["code"] == "invalid_link"
    # Disabling changes the composite (a set approval no longer covers it); re-enabling restores it.
    composite_2 = r.json()["composite_hash"]
    r = await api.patch(
        _sets(ws, ps["id"], f"/variants/{vid}"), headers=_h(s), json={"enabled": False}
    )
    assert r.json()["variants"][0]["state"] == "skipped"
    assert r.json()["composite_hash"] != composite_2
    r = await api.patch(
        _sets(ws, ps["id"], f"/variants/{vid}"), headers=_h(s), json={"enabled": True}
    )
    assert r.json()["composite_hash"] == composite_2
    # PATCH on the set is metadata only; DELETE archives.
    r = await api.patch(_sets(ws, ps["id"]), headers=_h(s), json={"title": "Renamed"})
    assert r.json()["title"] == "Renamed"
    assert r.json()["composite_hash"] == composite_2
    assert (await api.delete(_sets(ws, ps["id"]), headers=_h(s))).status_code == 204
    r = await api.post(_sets(ws, ps["id"], "/generate"), headers=_h(s))
    assert r.json()["error"]["code"] == "post_set_archived"
    assert ps["id"] not in [
        x["id"] for x in (await api.get(_sets(ws), headers=_h(s))).json()["data"]
    ]


async def test_human_edits_meet_the_same_guardrails(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "humanguard")
    xa = (await _connect(api, s, ws, "x", handle="xa"))["connected"]
    xb = (await _connect(api, s, ws, "x", handle="xb"))["connected"]
    r = await api.post(
        f"/api/v1/workspaces/{ws}/brand-voices",
        headers=_h(s),
        json={
            "scope_type": "workspace",
            "name": "House",
            "banned_words": ["game changer"],
            "is_default": True,
        },
    )
    assert r.status_code == 201
    ps = (
        await api.post(
            _sets(ws),
            headers=_h(s),
            json={
                "topic": "Kafka 4",
                "targets": {"social_account_ids": [xa, xb]},
                "generate": False,
            },
        )
    ).json()
    va, vb = (v["id"] for v in ps["variants"])

    current: dict[str, str | None] = {}

    async def write(vid: str, body: str) -> dict[str, Any]:
        r = await api.post(
            _sets(ws, ps["id"], f"/variants/{vid}/versions"),
            headers=_h(s),
            json={"body": body, "based_on_version_id": current.get(vid)},
        )
        if r.status_code == 201:
            cv = next(v for v in r.json()["variants"] if v["id"] == vid)["current_version"]
            current[vid] = cv["id"]
        assert r.status_code == 201, r.text
        return next(v for v in r.json()["variants"] if v["id"] == vid) | {"_set": r.json()}

    v = await write(va, "Kafka 4 is a Game Changer for streaming. https://e.com/k")
    assert v["state"] == "blocked"
    assert any(
        g["guardrail_key"] == "banned_words" and g["outcome"] == "block" for g in v["guardrails"]
    )
    v = await write(va, "Questions? Email jane.doe@example.com about Kafka 4.")
    assert any(g["guardrail_key"] == "pii" and g["outcome"] == "block" for g in v["guardrails"])
    assert "jane.doe@example.com" not in str(v["guardrails"])  # the audit trail masks it
    v = await write(va, "Kafka 4 " + "word " * 80)
    assert any(
        g["guardrail_key"] == "platform_policy" and g["outcome"] == "block" for g in v["guardrails"]
    )
    # Identical text on two X accounts blocks the whole set; making one distinct clears it.
    same = "Kafka 4 drops ZooKeeper; KRaft is now the only metadata mode. https://e.com/k"
    await write(va, same)
    v = await write(vb, same)
    assert v["_set"]["state"] == "blocked"
    assert any(
        g["guardrail_key"] == "cross_account_duplicate" and g["outcome"] == "block"
        for g in v["_set"]["set_guardrails"]
    )
    v = await write(
        vb,
        "Migration plans matter more than the release notes this time: test KRaft in staging first. https://e.com/k",
    )
    assert v["_set"]["state"] == "drafting"
    assert v["state"] == "drafting"


# ------------------------------------------------------------ the Writer loop


class ParrotLLM(FakeLLM):
    """A Writer that ignores the siblings and writes the same post for everyone."""

    async def structured(
        self, config: ModelConfig, messages: Sequence[Message], schema: type[BaseModel]
    ) -> Structured[Any]:
        r = await super().structured(config, messages, schema)
        if schema.__name__ == "WriterOutput":
            post = r.value.post.model_copy(
                update={"body": "Postgres 18 ships async I/O for faster scans. https://e.com/pg18"}
            )
            return Structured(r.value.model_copy(update={"post": post}), r.usage)
        return r


class PricedLLM(FakeLLM):
    async def structured(
        self, config: ModelConfig, messages: Sequence[Message], schema: type[BaseModel]
    ) -> Structured[Any]:
        r = await super().structured(config, messages, schema)
        return Structured(
            r.value, replace(r.usage, cost_usd=Decimal("1.00"), price_source="pinned")
        )


async def test_differentiate_retries_then_blocks_the_set(
    api: httpx.AsyncClient, fetcher: FakeFetcher, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "parrot")
    xa = (await _connect(api, s, ws, "x", handle="pa"))["connected"]
    xb = (await _connect(api, s, ws, "x", handle="pb"))["connected"]
    item = await _story(api, s, ws, fetcher)
    ps = (
        await api.post(
            _sets(ws),
            headers=_h(s),
            json={"content_item_id": item, "targets": {"social_account_ids": [xa, xb]}},
        )
    ).json()
    await _run_queued(api, deferrer, _drafting(api, ParrotLLM()))
    ps = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    second = ps["variants"][1]["current_version"]["agent_notes"]
    assert second["differentiate_attempts"] == 2
    assert second["closest_sibling_similarity"] >= 0.9
    assert ps["state"] == "blocked"


async def test_rewrite_touches_only_the_chosen_account_and_cost_cap_stops_runs(
    api: httpx.AsyncClient, fetcher: FakeFetcher, deferrer: CapturingDeferrer
) -> None:
    s, ws = await _setup(api, "rewrite")
    a = (await _connect(api, s, ws, "linkedin_member", handle="lw"))["connected"]
    b = (await _connect(api, s, ws, "mastodon", handle="mw"))["connected"]
    item = await _story(api, s, ws, fetcher)
    ps = (
        await api.post(
            _sets(ws),
            headers=_h(s),
            json={"content_item_id": item, "targets": {"social_account_ids": [a, b]}},
        )
    ).json()
    await _run_queued(api, deferrer)
    ps = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    va, vb = ps["variants"]
    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{va['id']}/rewrite"),
        headers=_h(s),
        json={"instruction": "Make it shorter"},
    )
    assert r.status_code == 202
    assert deferrer.calls[-1]["mode"] == "revise"
    await _run_queued(api, deferrer)
    after = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    na, nb = after["variants"]
    assert na["current_version"]["version_no"] == 2
    assert na["current_version"]["revision_instruction"] == "Make it shorter"
    assert len(na["current_version"]["body"]) < len(va["current_version"]["body"])
    assert nb["current_version"]["id"] == vb["current_version"]["id"]  # untouched

    tags = (
        await api.post(_sets(ws, ps["id"], f"/variants/{vb['id']}/hashtags/suggest"), headers=_h(s))
    ).json()["data"]
    assert tags
    assert all(t["basis"] == "content_only" and t["lift_vs_account_median"] is None for t in tags)

    # The drafting budget: a $1 call against a $1.50 cap stops after the first account.
    r = await api.post(_sets(ws, ps["id"], "/generate"), headers=_h(s))
    assert r.status_code == 202
    await _run_queued(
        api, deferrer, _drafting(api, PricedLLM(), daily_cost_cap_usd=Decimal("1.50"))
    )
    capped = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    assert "cost cap" in (capped["generation_error"] or "")

    # The workspace's monthly budget covers every agent: today's spend already exceeds a
    # $1 month, so nothing more is drafted even with daily budget to spare.
    r = await api.post(_sets(ws, ps["id"], "/generate"), headers=_h(s))
    assert r.status_code == 202
    await _run_queued(
        api,
        deferrer,
        _drafting(
            api,
            PricedLLM(),
            daily_cost_cap_usd=Decimal("100"),
            monthly_cost_cap_usd=Decimal("1"),
        ),
    )
    monthly = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    assert "Monthly workspace cost cap" in (monthly["generation_error"] or "")


# --------------------------------------------------------------- voices, scope


async def test_voices_resolve_most_specific_and_scope_is_enforced(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, owner_engine: AsyncEngine
) -> None:
    owner, ws = await _setup(api, "voices")
    a = (await _connect(api, owner, ws, "bluesky", handle="va"))["connected"]
    b = (await _connect(api, owner, ws, "bluesky", handle="vb"))["connected"]
    ga = await _group(api, owner, ws, "A", [a])
    gb = await _group(api, owner, ws, "B", [b])
    base = f"/api/v1/workspaces/{ws}/brand-voices"
    wsv = (
        await api.post(
            base,
            headers=_h(owner),
            json={"scope_type": "workspace", "name": "House", "is_default": True},
        )
    ).json()
    gv = (
        await api.post(
            base,
            headers=_h(owner),
            json={
                "scope_type": "account_group",
                "scope_id": ga,
                "name": "Group A voice",
                "is_default": True,
                "tone": "Dry",
            },
        )
    ).json()
    r = await api.post(
        base, headers=_h(owner), json={"scope_type": "workspace", "scope_id": ga, "name": "bad"}
    )
    assert r.json()["error"]["code"] == "invalid_scope"
    # A second default in the same scope replaces the first.
    wsv2 = (
        await api.post(
            base,
            headers=_h(owner),
            json={"scope_type": "workspace", "name": "House 2", "is_default": True},
        )
    ).json()
    listed = {
        v["id"]: v["is_default"] for v in (await api.get(base, headers=_h(owner))).json()["data"]
    }
    assert listed[wsv["id"]] is False
    assert listed[wsv2["id"]] is True

    ps = (
        await api.post(
            _sets(ws),
            headers=_h(owner),
            json={"topic": "x", "targets": {"account_group_ids": [ga, gb]}, "generate": False},
        )
    ).json()
    voices = {v["social_account_id"]: v["voice_name"] for v in ps["variants"]}
    assert voices == {a: "Group A voice", b: "House 2"}
    vb_id = next(v["id"] for v in ps["variants"] if v["social_account_id"] == b)
    r = await api.patch(
        _sets(ws, ps["id"], f"/variants/{vb_id}"),
        headers=_h(owner),
        json={"brand_voice_id": wsv["id"]},
    )
    assert next(v for v in r.json()["variants"] if v["id"] == vb_id)["voice_name"] == "House"
    r = await api.patch(
        _sets(ws, ps["id"], f"/variants/{vb_id}"),
        headers=_h(owner),
        json={"brand_voice_id": gv["id"]},
    )
    assert r.json()["error"]["code"] == "voice_not_applicable"
    sample = (
        await api.post(
            f"{base}/{gv['id']}/sample",
            headers=_h(owner),
            json={
                "topic": "Postgres 18 ships async I/O for read-heavy workloads",
                "platform": "bluesky",
            },
        )
    ).json()
    assert sample["body"]
    assert sample["voice_name"] == "Group A voice"

    editor = await _register_login(api, "voice_ed")
    editor_id = (await _me(api, editor))["id"]
    await _add_member(owner_engine, ws, editor_id, "editor")
    await api.put(
        f"/api/v1/workspaces/{ws}/members/{editor_id}/scopes",
        headers=_h(owner),
        json={"account_group_ids": [ga]},
    )
    assert (
        await api.post(base, headers=_h(editor), json={"scope_type": "workspace", "name": "x"})
    ).status_code == 403
    assert (
        await api.post(
            base,
            headers=_h(editor),
            json={"scope_type": "account_group", "scope_id": ga, "name": "ok"},
        )
    ).status_code == 201
    assert (
        await api.post(
            base,
            headers=_h(editor),
            json={"scope_type": "social_account", "scope_id": b, "name": "no"},
        )
    ).status_code == 403
    # Post sets: only their accounts, and only their variants are visible.
    assert (
        await api.post(
            _sets(ws),
            headers=_h(editor),
            json={"topic": "x", "targets": {"account_group_ids": [gb]}},
        )
    ).status_code == 403
    seen = (await api.get(_sets(ws, ps["id"]), headers=_h(editor))).json()
    assert [v["social_account_id"] for v in seen["variants"]] == [a]
    assert (await api.post(_sets(ws, ps["id"], "/generate"), headers=_h(editor))).status_code == 403
    r = await api.post(
        _sets(ws, ps["id"], "/generate"),
        headers=_h(editor),
        json={
            "variant_ids": [next(v["id"] for v in ps["variants"] if v["social_account_id"] == a)]
        },
    )
    assert r.status_code == 202

    other, other_ws = await _setup(api, "voicesother")
    assert (await api.get(_sets(ws, ps["id"]), headers=_h(other))).status_code in (403, 404)
    assert (await api.get(_sets(other_ws, ps["id"]), headers=_h(other))).status_code == 404


async def test_targets_validation(api: httpx.AsyncClient, deferrer: CapturingDeferrer) -> None:
    s, ws = await _setup(api, "targets")
    a = (await _connect(api, s, ws, "mastodon", handle="t1"))["connected"]
    b = (await _connect(api, s, ws, "mastodon", handle="t2"))["connected"]
    g = await _group(api, s, ws, "G", [a, b])
    r = await api.post(_sets(ws), headers=_h(s), json={"targets": {"account_group_ids": [g]}})
    assert r.json()["error"]["code"] == "no_subject"
    r = await api.post(_sets(ws), headers=_h(s), json={"topic": "t", "targets": {}})
    assert r.json()["error"]["code"] == "no_targets"
    r = await api.post(
        _sets(ws),
        headers=_h(s),
        json={
            "topic": "t",
            "targets": {"account_group_ids": [g], "exclude_social_account_ids": [b]},
            "generate": False,
        },
    )
    body = r.json()
    assert [v["social_account_id"] for v in body["variants"]] == [a]
    assert body["skipped"] == [{"social_account_id": b, "reason": "excluded"}]
    r = await api.post(
        _sets(ws, body["id"], "/variants"),
        headers=_h(s),
        json={"social_account_id": b, "generate": False},
    )
    assert len(r.json()["variants"]) == 2
    r = await api.post(
        _sets(ws, body["id"], "/variants"), headers=_h(s), json={"social_account_id": b}
    )
    assert r.json()["error"]["code"] == "duplicate_target"
    v = (await api.post(_sets(ws, body["id"], "/validate"), headers=_h(s))).json()["data"]
    assert all(x["issues"][0]["code"] == "not_drafted" for x in v)
    r = await api.post(
        _sets(ws, body["id"], "/rewrite"), headers=_h(s), json={"instruction": "shorter"}
    )
    assert r.json()["error"]["code"] == "nothing_to_revise"


# ------------------------------------------------------- security review fixes


async def test_review_status_never_wedges_and_locked_variants_stay_put(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "wedge")
    a = (await _connect(api, s, ws, "mastodon", handle="w1"))["connected"]
    ps = (
        await api.post(
            _sets(ws),
            headers=_h(s),
            json={"topic": "Kafka 4", "targets": {"social_account_ids": [a]}},
        )
    ).json()
    assert ps["generation_status"] == "queued"
    vid = ps["variants"][0]["id"]
    # The only account is disabled before the worker runs: the job settles to idle.
    await api.patch(_sets(ws, ps["id"], f"/variants/{vid}"), headers=_h(s), json={"enabled": False})
    await _run_queued(api, deferrer)
    after = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    assert after["generation_status"] == "idle"

    # A worker crash mid-run leaves "failed", not "running".
    await api.patch(_sets(ws, ps["id"], f"/variants/{vid}"), headers=_h(s), json={"enabled": True})
    assert (await api.post(_sets(ws, ps["id"], "/generate"), headers=_h(s))).status_code == 202

    class Exploding(FakeLLM):
        async def structured(self, *a: Any, **kw: Any) -> Any:
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await _run_queued(api, deferrer, _drafting(api, Exploding()))
    after = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    assert after["generation_status"] == "failed"
    assert "unexpectedly" in after["generation_error"]

    # A variant in flight (publishing, published) cannot be edited, regenerated or toggled
    # here. (Approved, rejected and, since M8, scheduled ones can be edited: the edit is a
    # new version the old approval does not cover, and it cancels the queued slot.)
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE post_variant SET state = 'publishing' WHERE id = :v"), {"v": vid}
        )
    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{vid}/versions"), headers=_h(s), json={"body": "new"}
    )
    assert r.json()["error"]["code"] == "variant_locked"
    r = await api.patch(
        _sets(ws, ps["id"], f"/variants/{vid}"), headers=_h(s), json={"enabled": False}
    )
    assert r.json()["error"]["code"] == "variant_locked"
    assert (await api.post(_sets(ws, ps["id"], "/generate"), headers=_h(s))).json()["error"][
        "code"
    ] == "no_targets"
    # The database refuses to move a variant to another account or point it elsewhere.
    async with owner_engine.begin() as conn:
        with pytest.raises(Exception, match="fixed"):
            async with conn.begin_nested():
                await conn.execute(
                    text("UPDATE post_variant SET platform = 'x' WHERE id = :v"), {"v": vid}
                )
        with pytest.raises(Exception, match="own versions"):
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "UPDATE post_variant SET current_version_id = gen_random_uuid() "
                        "WHERE id = :v"
                    ),
                    {"v": vid},
                )


async def test_review_voice_and_scope_rules(
    api: httpx.AsyncClient, deferrer: CapturingDeferrer, owner_engine: AsyncEngine
) -> None:
    owner, ws = await _setup(api, "voicerules")
    a = (await _connect(api, owner, ws, "bluesky", handle="r1"))["connected"]
    b = (await _connect(api, owner, ws, "bluesky", handle="r2"))["connected"]
    ga = await _group(api, owner, ws, "RA", [a])
    gb = await _group(api, owner, ws, "RB", [b])
    base = f"/api/v1/workspaces/{ws}/brand-voices"
    house = (
        await api.post(
            base,
            headers=_h(owner),
            json={
                "scope_type": "workspace",
                "name": "House",
                "banned_words": ["synergy"],
                "is_default": True,
            },
        )
    ).json()
    gbv = (
        await api.post(
            base,
            headers=_h(owner),
            json={"scope_type": "account_group", "scope_id": gb, "name": "B only"},
        )
    ).json()
    ps = (
        await api.post(
            _sets(ws),
            headers=_h(owner),
            json={"topic": "x", "targets": {"account_group_ids": [ga, gb]}, "generate": False},
        )
    ).json()
    va = next(v for v in ps["variants"] if v["social_account_id"] == a)
    # A cleared voice pin still enforces the resolved voice's banned words (review H1).
    await api.patch(
        _sets(ws, ps["id"], f"/variants/{va['id']}"),
        headers=_h(owner),
        json={"brand_voice_id": None},
    )
    r = await api.post(
        _sets(ws, ps["id"], f"/variants/{va['id']}/versions"),
        headers=_h(owner),
        json={"body": "Real s\u200bynergy here."},
    )
    v = next(x for x in r.json()["variants"] if x["id"] == va["id"])
    assert v["voice_name"] == "House (from workspace)"  # what actually applies
    assert any(
        g["guardrail_key"] == "banned_words" and g["outcome"] == "block" for g in v["guardrails"]
    )
    # Group B's voice cannot be borrowed by an account outside group B.
    r = await api.patch(
        _sets(ws, ps["id"], f"/variants/{va['id']}"),
        headers=_h(owner),
        json={"brand_voice_id": gbv["id"]},
    )
    assert r.json()["error"]["code"] == "voice_not_applicable"
    assert house["is_default"]

    editor = await _register_login(api, "rule_ed")
    editor_id = (await _me(api, editor))["id"]
    await _add_member(owner_engine, ws, editor_id, "editor")
    await api.put(
        f"/api/v1/workspaces/{ws}/members/{editor_id}/scopes",
        headers=_h(owner),
        json={"account_group_ids": [ga]},
    )
    seen = (await api.get(_sets(ws, ps["id"]), headers=_h(editor))).json()
    assert seen["target_account_group_ids"] == [ga]
    assert seen["last_run_id"] is None
    names = {x["name"] for x in (await api.get(base, headers=_h(editor))).json()["data"]}
    assert names == {"House"}
    for url in (f"/api/v1/workspaces/{ws}/agent-runs", f"/api/v1/workspaces/{ws}/agent-runs/costs"):
        assert (await api.get(url, headers=_h(editor))).status_code == 403
    listed = (await api.get(_sets(ws), headers=_h(editor), params={"limit": 1})).json()["data"]
    assert [x["id"] for x in listed] == [ps["id"]]


async def test_review_version_references_can_still_be_nulled_by_cascades(
    api: httpx.AsyncClient,
    fetcher: FakeFetcher,
    deferrer: CapturingDeferrer,
    owner_engine: AsyncEngine,
) -> None:
    s, ws = await _setup(api, "cascade")
    a = (await _connect(api, s, ws, "mastodon", handle="c1"))["connected"]
    item = await _story(api, s, ws, fetcher)
    ps = (
        await api.post(
            _sets(ws),
            headers=_h(s),
            json={"content_item_id": item, "targets": {"social_account_ids": [a]}},
        )
    ).json()
    await _run_queued(api, deferrer)
    ps = (await api.get(_sets(ws, ps["id"]), headers=_h(s))).json()
    version = ps["variants"][0]["current_version"]
    async with owner_engine.begin() as conn:
        run_id = await conn.scalar(
            text("SELECT created_by_agent_run_id FROM post_version WHERE id = :v"),
            {"v": version["id"]},
        )
        assert run_id is not None
        # A direct UPDATE that only nulls a reference is still refused...
        with pytest.raises(Exception, match="immutable"):
            async with conn.begin_nested():
                await conn.execute(
                    text("UPDATE post_version SET created_by_agent_run_id = NULL WHERE id = :v"),
                    {"v": version["id"]},
                )
        # ...but deleting the run lets the foreign key null it (and the guardrail rows').
        await conn.execute(text("DELETE FROM agent_run WHERE id = :r"), {"r": run_id})
        row = (
            await conn.execute(
                text("SELECT created_by_agent_run_id, body FROM post_version WHERE id = :v"),
                {"v": version["id"]},
            )
        ).one()
    assert row[0] is None
    assert row[1] == version["body"]
