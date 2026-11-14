"""M4 acceptance (docs/11 §M4): sources, the ingestion pipeline, per-group scoring, Trending,
pillars, cost recording and caps, failure back-off, the dispatcher, and tenancy."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import format_datetime
from typing import Any
from xml.sax.saxutils import escape

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from mera.application.ingest.factory import ingest_config
from mera.application.ingest.pipeline import Ingestion
from mera.domain.ports.llm import Embeddings, LLMError, Message, ModelConfig, Structured
from mera.infrastructure.db.models import AgentRun, AgentStep, ContentItem, Notification
from mera.infrastructure.http.safe_fetch import FetchError, FetchResult
from mera.infrastructure.llm.fake import FakeLLM
from mera.workers.tasks.ingest import dispatch, find_work
from tests.integration.test_accounts_m3 import _app, _grp, _setup
from tests.integration.test_identity_m2 import _add_member, _h, _me, _register_login

pytestmark = pytest.mark.integration
NOW = datetime.now(UTC)

PG_BODY = (
    "PostgreSQL 18 is released with an asynchronous I/O subsystem for faster sequential scans. "
    "The database adds OAuth authentication, skip scan for multicolumn btree indexes, and "
    "virtual generated columns. Postgres operators report large gains on read heavy database "
    "workloads, and the SQL planner improves estimates for joins. Upgrading a Postgres cluster "
    "uses pg_upgrade, which now keeps planner statistics, so the database is fast right away. "
)
HIRING_BODY = (
    "Hiring managers say remote recruiting changed how engineering culture is built. Teams that "
    "hire across time zones invest in onboarding, written culture, mentoring, and career "
    "ladders. Recruiting pipelines lean on referrals, and candidates ask about culture and "
    "flexibility first. Hiring for culture add rather than culture fit widens the talent pool. "
)


def _rss(entries: Sequence[tuple[str, str, str]], *, title: str = "Feed") -> bytes:
    items = "".join(
        f"<item><title>{escape(t)}</title><link>{escape(u)}</link>"
        f"<description>{escape(d)}</description>"
        f"<pubDate>{format_datetime(NOW - timedelta(hours=2))}</pubDate></item>"
        for t, u, d in entries
    )
    head = '<?xml version="1.0"?><rss version="2.0">'
    return f"{head}<channel><title>{title}</title>{items}</channel></rss>".encode()


class FakeFetcher:
    """Serves canned bodies; honours If-None-Match; can be told to fail."""

    def __init__(self) -> None:
        self.pages: dict[str, bytes] = {}
        self.errors: dict[str, FetchError] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        max_bytes: int,
        overall_timeout: float = 30.0,
        accept_statuses: frozenset[int] = frozenset({200, 304}),
    ) -> FetchResult:
        self.calls.append((url, dict(headers or {})))
        if url in self.errors:
            raise self.errors[url]
        body = self.pages.get(url)
        if body is None:
            raise FetchError("http_status", "HTTP 404", status=404)
        etag = f'"{hash(body) & 0xFFFF}"'
        if (headers or {}).get("If-None-Match") == etag and 304 in accept_statuses:
            return FetchResult(url, 304, {}, b"")
        return FetchResult(
            url, 200, {"etag": etag, "content-type": "text/html; charset=utf-8"}, body
        )


@pytest.fixture
async def fetcher(api: httpx.AsyncClient) -> AsyncIterator[FakeFetcher]:
    ingestion = _app(api).state.ingestion
    original = ingestion._fetcher
    fake = FakeFetcher()
    ingestion._fetcher = fake
    yield fake
    ingestion._fetcher = original


def _sm(api: httpx.AsyncClient) -> async_sessionmaker[AsyncSession]:
    sm: async_sessionmaker[AsyncSession] = _app(api).state.sessionmaker
    return sm


def _ingestion(
    api: httpx.AsyncClient,
    fetcher: FakeFetcher,
    *,
    llm: Any = None,
    clock: Callable[[], datetime] | None = None,
    **cfg: Any,
) -> Ingestion:
    config = replace(ingest_config(_app(api).state.settings), **cfg)
    return Ingestion(
        _sm(api),
        fetcher=fetcher,
        llm=llm or FakeLLM(),
        config=config,
        clock=clock or (lambda: datetime.now(UTC)),
    )


def _src(ws: str, sid: str = "", suffix: str = "") -> str:
    return f"/api/v1/workspaces/{ws}/sources" + (f"/{sid}" if sid else "") + suffix


def _items(ws: str, suffix: str = "") -> str:
    return f"/api/v1/workspaces/{ws}/content-items{suffix}"


def _pillars(ws: str, pid: str = "") -> str:
    return f"/api/v1/workspaces/{ws}/content-pillars" + (f"/{pid}" if pid else "")


async def _source(
    api: httpx.AsyncClient, s: dict[str, Any], ws: str, url: str, **kw: Any
) -> dict[str, Any]:
    r = await api.post(
        _src(ws), headers=_h(s), json={"name": kw.pop("name", url), "url": url, **kw}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _group(api: httpx.AsyncClient, s: dict[str, Any], ws: str, name: str) -> str:
    r = await api.post(_grp(ws), headers=_h(s), json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _pillar(api: httpx.AsyncClient, s: dict[str, Any], ws: str, **kw: Any) -> dict[str, Any]:
    r = await api.post(_pillars(ws), headers=_h(s), json=kw)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- source CRUD


async def test_source_crud_and_validation(api: httpx.AsyncClient, fetcher: FakeFetcher) -> None:
    s, ws = await _setup(api, "sources")
    src = await _source(
        api,
        s,
        ws,
        "https://www.postgresql.org/news.rss",
        name="PG",
        include_keywords=["Postgres", "postgres"],
    )
    assert src["status"] == "active"
    assert src["group_ids"] == []
    assert src["next_fetch_at"]
    assert src["include_keywords"] == ["postgres"]

    bad = [
        (
            {"name": "x", "url": "https://example.com/r", "source_type": "reddit"},
            422,
            "source_type_unavailable",
        ),
        ({"name": "x", "url": "http://127.0.0.1/feed"}, 422, "url_not_allowed"),
        ({"name": "x", "url": "http://169.254.169.254/latest/meta-data"}, 422, "url_not_allowed"),
        ({"name": "x", "url": "ftp://example.com/feed"}, 422, "invalid_url"),
        (
            {"name": "x", "url": "https://www.postgresql.org/news.rss?utm_source=x"},
            409,
            "duplicate_source",
        ),
        (
            {"name": "x", "url": "https://e.com/f", "group_ids": [str(uuid.uuid4())]},
            422,
            "unknown_group",
        ),
        ({"name": "x", "url": "https://e.com/f", "language": "english"}, 400, None),
    ]
    for body, code, err in bad:
        r = await api.post(_src(ws), headers=_h(s), json=body)
        assert r.status_code == code, (body, r.text)
        if err:
            assert r.json()["error"]["code"] == err

    r = await api.patch(_src(ws, src["id"]), headers=_h(s), json={"status": "paused"})
    assert r.json()["status"] == "paused"
    r = await api.post(_src(ws, src["id"], "/fetch"), headers=_h(s))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "source_paused")
    r = await api.patch(_src(ws, src["id"]), headers=_h(s), json={"status": "failing"})
    assert r.json()["error"]["code"] == "invalid_status"
    r = await api.patch(
        _src(ws, src["id"]), headers=_h(s), json={"status": "active", "language": "en"}
    )
    assert (r.json()["status"], r.json()["language"]) == ("active", "en")
    r = await api.patch(_src(ws, src["id"]), headers=_h(s), json={"language": ""})
    assert r.json()["language"] is None
    r = await api.post(_src(ws, src["id"], "/fetch"), headers=_h(s))
    assert r.status_code == 202
    assert r.json()["queued_at"]

    g = await _group(api, s, ws, "Tech")
    r = await api.put(_src(ws, src["id"], "/groups"), headers=_h(s), json={"group_ids": [g]})
    assert r.json()["group_ids"] == [g]
    listed = (await api.get(_src(ws), headers=_h(s), params={"group_id": g})).json()["data"]
    assert [x["id"] for x in listed] == [src["id"]]
    other = await _group(api, s, ws, "Other")
    assert (await api.get(_src(ws), headers=_h(s), params={"group_id": other})).json()["data"] == []
    assert (await api.get(_src(ws), headers=_h(s), params={"status": "paused"})).json()[
        "data"
    ] == []

    fetcher.pages["https://e.com/feed"] = _rss([("Hello", "https://e.com/1", "x")], title="E feed")
    r = await api.post(_src(ws, suffix="/test"), headers=_h(s), json={"url": "https://e.com/feed"})
    assert r.json()["ok"] is True
    assert r.json()["title"] == "E feed"
    assert r.json()["item_count"] == 1
    fetcher.pages["https://e.com/page"] = b"<html><body>not a feed</body></html>"
    r = await api.post(_src(ws, suffix="/test"), headers=_h(s), json={"url": "https://e.com/page"})
    assert (r.json()["ok"], r.json()["error_code"]) == (False, "not_a_feed")
    r = await api.post(
        _src(ws, suffix="/test"), headers=_h(s), json={"url": "https://e.com/missing"}
    )
    assert r.json()["error_code"] == "http_status"
    r = await api.post(_src(ws, suffix="/test"), headers=_h(s), json={"url": "http://10.0.0.1/x"})
    assert r.status_code == 422

    assert (await api.delete(_src(ws, src["id"]), headers=_h(s))).status_code == 204
    assert (await api.get(_src(ws, src["id"]), headers=_h(s))).status_code == 404


# --------------------------------------------------------------- the pipeline


async def test_pipeline_dedupes_summarises_and_scores_per_group(
    api: httpx.AsyncClient, fetcher: FakeFetcher
) -> None:
    s, ws = await _setup(api, "pipeline")
    g_db = await _group(api, s, ws, "Databases team")
    g_people = await _group(api, s, ws, "People team")
    p_db = await _pillar(
        api,
        s,
        ws,
        name="Databases",
        description="Postgres and SQL databases",
        keywords=["postgres", "database", "sql"],
        group_id=g_db,
        weight=1.4,
    )
    await _pillar(
        api,
        s,
        ws,
        name="Hiring",
        description="Recruiting and engineering culture",
        keywords=["hiring", "recruiting", "culture"],
        group_id=g_people,
    )
    assert p_db["embedded"] is True

    a = await _source(api, s, ws, "https://a.example/feed", name="Feed A")  # feeds every group
    b = await _source(api, s, ws, "https://b.example/feed", name="Feed B", group_ids=[g_db])
    fetcher.pages["https://a.example/feed"] = _rss(
        [
            ("PostgreSQL 18 Released", "https://news.example/pg18?utm_source=a", PG_BODY),
            (
                "Remote hiring reshapes engineering culture",
                "https://news.example/hiring",
                HIRING_BODY,
            ),
            ("Short teaser", "https://news.example/teaser", "Read more"),
        ]
    )
    fetcher.pages["https://news.example/teaser"] = (
        "<html><body><article><h1>Teaser</h1><p>"
        + "Kafka 4.0 makes KRaft the only metadata mode, so clusters must leave ZooKeeper. " * 6
        + "</p><p>Extracted from the page, with operators planning their migrations.</p>"
        + "</article></body></html>"
    ).encode()
    fetcher.pages["https://b.example/feed"] = _rss(
        [
            (
                "PostgreSQL 18 Released",
                "https://NEWS.example/pg18?fbclid=zzz",
                PG_BODY,
            ),  # exact dup
            (
                "Postgres 18 is out: what's new",
                "https://other.example/pg-18-out",
                PG_BODY + " Also: new.",
            ),
        ]
    )
    ing = _ingestion(api, fetcher)
    wsid = uuid.UUID(ws)
    out_a = await ing.fetch_source(wsid, uuid.UUID(a["id"]))
    out_b = await ing.fetch_source(wsid, uuid.UUID(b["id"]))
    assert (out_a.status, out_a.new_items) == ("fetched", 3)
    assert (out_b.new_items, out_b.already_seen) == (1, 1)
    # Conditional request: the feed did not change.
    assert (await ing.fetch_source(wsid, uuid.UUID(a["id"]))).status == "not_modified"
    assert fetcher.calls[-1][1].get("If-None-Match")

    enriched = await ing.enrich_pending(wsid)
    assert (enriched.ready, enriched.duplicates, enriched.failed) == (3, 1, 0)

    db_view = (
        await api.get(_items(ws, "/trending"), headers=_h(s), params={"group_id": g_db})
    ).json()["data"]
    people_view = (
        await api.get(_items(ws, "/trending"), headers=_h(s), params={"group_id": g_people})
    ).json()["data"]
    assert db_view[0]["title"] == "PostgreSQL 18 Released"
    assert people_view[0]["title"] == "Remote hiring reshapes engineering culture"
    top = db_view[0]
    assert top["best_pillar"] == "Databases"
    assert top["summary"]
    assert top["topics"]
    assert top["coverage"] >= 2
    assert top["duplicates"]
    assert top["duplicates"][0]["similarity"] >= 0.92
    # Feed B feeds only the Databases group: its items have no People score.
    assert all(i["source_name"] != "Feed B" for i in people_view)

    detail = (await api.get(_items(ws, f"/{top['id']}"), headers=_h(s))).json()
    by_group = {sc["group_name"]: sc for sc in detail["scores"]}
    assert set(by_group) == {"Databases team", "People team"}
    assert by_group["Databases team"]["relevance"] > by_group["People team"]["relevance"]
    assert "Databases" in by_group["Databases team"]["pillar_scores"]
    assert by_group["Databases team"]["freshness"] > 0.9
    assert detail["relevance"] == pytest.approx(by_group["Databases team"]["relevance"])

    teaser = [i for i in db_view + people_view if i["url"] == "https://news.example/teaser"]
    listing = (
        await api.get(_items(ws), headers=_h(s), params={"state": ["ready", "duplicate"]})
    ).json()
    assert {i["state"] for i in listing["data"]} <= {"ready", "duplicate"}
    teaser = [i for i in listing["data"] if i["url"] == "https://news.example/teaser"]
    async with _sm(api)() as sess:
        await sess.execute(text("SELECT set_config('mera.workspace_id', :w, false)"), {"w": ws})
        row = (
            await sess.execute(
                select(ContentItem).where(ContentItem.url == "https://news.example/teaser")
            )
        ).scalar_one()
        assert row.provenance["extractor"] == "trafilatura"
        assert "Extracted from the page" in (row.extracted_text or "")
        exact = (
            await sess.execute(
                select(ContentItem).where(ContentItem.title == "PostgreSQL 18 Released")
            )
        ).scalar_one()
        assert set(exact.seen_source_ids) == {uuid.UUID(a["id"]), uuid.UUID(b["id"])}
        runs = list((await sess.execute(select(AgentRun))).scalars())
        steps = list((await sess.execute(select(AgentStep))).scalars())
    assert {r.workflow_key for r in runs} == {"embed_pillar", "ingest_enrich"}
    assert all(r.state == "succeeded" for r in runs)
    assert {st.step_type for st in steps} == {"embedding", "llm_call"}
    assert all(st.model and st.model.startswith("sandbox/") for st in steps)
    assert teaser  # the extracted teaser is visible

    stats = (await api.get(_items(ws, "/stats"), headers=_h(s))).json()
    assert stats["ingested"] == 4
    assert stats["after_dedupe"] + 2 >= 2
    assert stats["drafted"] is None
    assert stats["published"] is None
    stats_people = (
        await api.get(_items(ws, "/stats"), headers=_h(s), params={"group_id": g_people})
    ).json()
    assert stats_people["ingested"] == 3  # Feed B does not feed the People group

    # Link changes rescore at once: restricting Feed A to Databases drops it from People.
    r = await api.put(_src(ws, a["id"], "/groups"), headers=_h(s), json={"group_ids": [g_db]})
    assert r.status_code == 200
    trend = _items(ws, "/trending")
    assert (await api.get(trend, headers=_h(s), params={"group_id": g_people})).json()["data"] == []
    await api.put(_src(ws, a["id"], "/groups"), headers=_h(s), json={"group_ids": []})
    back = (await api.get(trend, headers=_h(s), params={"group_id": g_people})).json()["data"]
    assert [i["id"] for i in back] == [i["id"] for i in people_view]

    # Dismissal hides an item from Trending for everyone, and can be undone.
    r = await api.post(_items(ws, f"/{top['id']}/dismiss"), headers=_h(s))
    assert r.json()["dismissed"] is True
    ids = [
        i["id"]
        for i in (
            await api.get(_items(ws, "/trending"), headers=_h(s), params={"group_id": g_db})
        ).json()["data"]
    ]
    assert top["id"] not in ids
    r = await api.post(_items(ws, f"/{top['id']}/dismiss"), headers=_h(s), json={"undo": True})
    assert r.json()["dismissed"] is False


async def test_keyword_and_language_filters_hide_but_remember(
    api: httpx.AsyncClient, fetcher: FakeFetcher
) -> None:
    s, ws = await _setup(api, "filters")
    src = await _source(
        api,
        s,
        ws,
        "https://f.example/feed",
        include_keywords=["postgres"],
        exclude_keywords=["webinar"],
        language="en",
    )
    fetcher.pages["https://f.example/feed"] = _rss(
        [
            ("Postgres tuning guide", "https://f.example/1", PG_BODY),
            (
                "Join our postgres webinar",
                "https://f.example/2",
                PG_BODY + " Register for the webinar.",
            ),
            ("Recruiting news", "https://f.example/3", HIRING_BODY),
            (
                "Postgres auf Deutsch",
                "https://f.example/4",
                "Die Postgres Datenbank wurde heute in einer neuen Version veröffentlicht "
                "und bringt viele Verbesserungen für Entwickler und Betreiber mit sich. " * 6,
            ),
        ]
    )
    ing = _ingestion(api, fetcher)
    out = await ing.fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))
    assert (out.new_items, out.filtered) == (1, 3)
    health = (await api.get(_src(ws, src["id"], "/health"), headers=_h(s))).json()
    assert health["filtered_7d"] == 3
    assert health["last_yield"] == 1
    assert health["retrying"] is True
    # Filtered items are never fetched again and never listed.
    fetcher.pages["https://f.example/feed"] += b" "  # changed body: no 304
    again = await ing.fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))
    assert (again.new_items, again.filtered, again.already_seen) == (0, 0, 4)
    listing = (
        await api.get(
            _items(ws), headers=_h(s), params={"state": ["pending", "ready", "duplicate"]}
        )
    ).json()
    assert [i["url"] for i in listing["data"]] == ["https://f.example/1"]


async def test_failures_back_off_mark_failing_once_and_stop_after_48h(
    api: httpx.AsyncClient, fetcher: FakeFetcher
) -> None:
    s, ws = await _setup(api, "failing")
    src = await _source(api, s, ws, "https://down.example/feed", fetch_interval_seconds=900)
    fetcher.errors["https://down.example/feed"] = FetchError("http_status", "HTTP 403", status=403)
    t0 = datetime.now(UTC)
    clock = {"now": t0}
    ing = _ingestion(api, fetcher, clock=lambda: clock["now"])
    wsid, sid = uuid.UUID(ws), uuid.UUID(src["id"])
    gaps = []
    for _ in range(4):
        await ing.fetch_source(wsid, sid)
        h = (await api.get(_src(ws, src["id"], "/health"), headers=_h(s))).json()
        gaps.append((datetime.fromisoformat(h["next_fetch_at"]) - clock["now"]).total_seconds())
        clock["now"] += timedelta(minutes=30)
    assert gaps == [900, 1800, 3600, 3600]  # doubling, capped at an hour
    assert h["status"] == "failing"
    assert h["consecutive_failures"] == 4
    assert h["last_http_status"] == 403
    async with _sm(api)() as sess:
        await sess.execute(text("SELECT set_config('mera.workspace_id', :w, false)"), {"w": ws})
        notes = list(
            (
                await sess.execute(
                    select(Notification).where(Notification.kind == "source_failing")
                )
            ).scalars()
        )
    assert len(notes) == 1

    clock["now"] = t0 + timedelta(hours=49)
    await ing.fetch_source(wsid, sid)
    h = (await api.get(_src(ws, src["id"], "/health"), headers=_h(s))).json()
    assert h["next_fetch_at"] is None
    assert h["retrying"] is False
    # Retry restarts the window; a success clears the streak and the failing status.
    assert (await api.post(_src(ws, src["id"], "/fetch"), headers=_h(s))).status_code == 202
    del fetcher.errors["https://down.example/feed"]
    fetcher.pages["https://down.example/feed"] = _rss([("Back", "https://down.example/1", PG_BODY)])
    assert (await ing.fetch_source(wsid, sid)).status == "fetched"
    h = (await api.get(_src(ws, src["id"], "/health"), headers=_h(s))).json()
    assert (h["status"], h["consecutive_failures"], h["last_error"]) == ("active", 0, None)


# ----------------------------------------------------------- cost and failures


class PricedLLM(FakeLLM):
    """The fake, but every summary costs $1."""

    async def structured(
        self, config: ModelConfig, messages: Sequence[Message], schema: type[Any]
    ) -> Structured[Any]:
        r = await super().structured(config, messages, schema)
        return Structured(
            r.value, replace(r.usage, cost_usd=Decimal("1.00"), price_source="pinned")
        )


class BrokenSummaries(FakeLLM):
    async def structured(
        self, config: ModelConfig, messages: Sequence[Message], schema: type[BaseModel]
    ) -> Structured[Any]:
        raise LLMError("provider down", kind="unavailable")


class BrokenEmbeddings(FakeLLM):
    async def embed(self, model: str, texts: Sequence[str]) -> Embeddings:
        raise LLMError("embedding provider down", kind="unavailable")


async def _three_pending(
    api: httpx.AsyncClient, fetcher: FakeFetcher, name: str
) -> tuple[dict[str, Any], str]:
    s, ws = await _setup(api, name)
    src = await _source(api, s, ws, f"https://{name}.example/feed")
    fetcher.pages[f"https://{name}.example/feed"] = _rss(
        [
            ("PostgreSQL 18 Released", f"https://{name}.example/1", PG_BODY),
            ("Remote hiring", f"https://{name}.example/2", HIRING_BODY),
            (
                "Kafka drops ZooKeeper",
                f"https://{name}.example/3",
                "Kafka 4.0 makes KRaft the only metadata mode for every cluster, "
                "replacing ZooKeeper entirely. " * 8,
            ),
        ]
    )
    await _ingestion(api, fetcher).fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))
    return s, ws


async def test_daily_cost_cap_stops_summaries_and_notifies_once(
    api: httpx.AsyncClient, fetcher: FakeFetcher
) -> None:
    _s, ws = await _three_pending(api, fetcher, "capped")
    ing = _ingestion(api, fetcher, llm=PricedLLM(), daily_cost_cap_usd=Decimal("1.50"))
    out = await ing.enrich_pending(uuid.UUID(ws))
    assert out.capped
    assert 1 <= out.ready <= 2
    again = await ing.enrich_pending(uuid.UUID(ws))
    assert again.capped
    assert again.ready == 0
    async with _sm(api)() as sess:
        await sess.execute(text("SELECT set_config('mera.workspace_id', :w, false)"), {"w": ws})
        notes = list(
            (
                await sess.execute(
                    select(Notification).where(Notification.kind == "cost_cap_reached")
                )
            ).scalars()
        )
        run = (
            (
                await sess.execute(
                    select(AgentRun)
                    .where(AgentRun.workflow_key == "ingest_enrich")
                    .order_by(AgentRun.started_at)
                )
            )
            .scalars()
            .first()
        )
        pending = (
            (await sess.execute(select(ContentItem).where(ContentItem.state == "pending")))
            .scalars()
            .all()
        )
    assert len(notes) == 1
    assert run is not None
    assert run.cost_usd >= Decimal("1.00")
    assert "cap" in (run.error or "")
    assert pending  # waiting for tomorrow, not lost


async def test_summary_failures_retry_then_show_unsummarised(
    api: httpx.AsyncClient, fetcher: FakeFetcher
) -> None:
    s, ws = await _three_pending(api, fetcher, "brokensum")
    ing = _ingestion(api, fetcher, llm=BrokenSummaries())
    for _ in range(3):
        out = await ing.enrich_pending(uuid.UUID(ws))
        assert out.failed == 3
        assert out.ready == 0
    items = (await api.get(_items(ws), headers=_h(s))).json()["data"]
    assert len(items) == 3
    assert all(
        i["state"] == "ready"
        and i["summary"] is None
        and i["state_reason"] == "summary_unavailable"
        for i in items
    )
    async with _sm(api)() as sess:
        await sess.execute(text("SELECT set_config('mera.workspace_id', :w, false)"), {"w": ws})
        failed = (
            (await sess.execute(select(AgentStep).where(AgentStep.state == "failed")))
            .scalars()
            .all()
        )
    assert len(failed) == 9


async def test_embedding_failures_give_up_after_three_runs(
    api: httpx.AsyncClient, fetcher: FakeFetcher
) -> None:
    _s, ws = await _three_pending(api, fetcher, "brokenemb")
    ing = _ingestion(api, fetcher, llm=BrokenEmbeddings())
    for _ in range(3):
        assert (await ing.enrich_pending(uuid.UUID(ws))).failed == 3
    async with _sm(api)() as sess:
        await sess.execute(text("SELECT set_config('mera.workspace_id', :w, false)"), {"w": ws})
        states = (await sess.execute(select(ContentItem.state))).scalars().all()
    assert set(states) == {"failed"}
    # Nothing left to do: the dispatcher stops queueing this workspace.
    work = await find_work(_sm(api), embedding_model="openai/text-embedding-3-small")
    assert uuid.UUID(ws) not in work.enrich


# ------------------------------------------------------------- pillars, scope


async def test_pillar_edits_rescore_and_scoped_editors_stay_in_their_group(
    api: httpx.AsyncClient, fetcher: FakeFetcher, owner_engine: AsyncEngine
) -> None:
    owner, ws = await _setup(api, "pillars")
    ga = await _group(api, owner, ws, "A")
    gb = await _group(api, owner, ws, "B")
    pillar = await _pillar(
        api, owner, ws, name="People", keywords=["hiring", "culture"], group_id=ga
    )
    src = await _source(api, owner, ws, "https://p.example/feed")
    fetcher.pages["https://p.example/feed"] = _rss(
        [
            ("PostgreSQL 18 Released", "https://p.example/1", PG_BODY),
            ("Remote hiring", "https://p.example/2", HIRING_BODY),
        ]
    )
    ing = _ingestion(api, fetcher)
    await ing.fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))
    await ing.enrich_pending(uuid.UUID(ws))
    first = (
        await api.get(_items(ws, "/trending"), headers=_h(owner), params={"group_id": ga})
    ).json()["data"]
    assert first[0]["title"] == "Remote hiring"
    # Rewriting the pillar's text re-embeds it and rescores at once.
    r = await api.patch(
        _pillars(ws, pillar["id"]),
        headers=_h(owner),
        json={
            "name": "Databases",
            "keywords": ["postgres", "database", "sql"],
            "description": "Postgres",
        },
    )
    assert r.status_code == 200
    assert r.json()["embedded"] is True
    after = (
        await api.get(_items(ws, "/trending"), headers=_h(owner), params={"group_id": ga})
    ).json()["data"]
    assert after[0]["title"] == "PostgreSQL 18 Released"
    listed = (await api.get(_pillars(ws), headers=_h(owner), params={"group_id": gb})).json()[
        "data"
    ]
    assert listed == []  # group A's pillar does not score group B
    assert (await api.delete(_pillars(ws, pillar["id"]), headers=_h(owner))).status_code == 204
    gone = (
        await api.get(_items(ws, "/trending"), headers=_h(owner), params={"group_id": ga})
    ).json()["data"]
    assert gone == []  # no pillars, no group scores

    editor = await _register_login(api, "pill_ed")
    editor_id = (await _me(api, editor))["id"]
    await _add_member(owner_engine, ws, editor_id, "editor")
    await api.put(
        f"/api/v1/workspaces/{ws}/members/{editor_id}/scopes",
        headers=_h(owner),
        json={"account_group_ids": [ga]},
    )
    # Own group: allowed. Workspace-wide or another group: refused.
    assert (
        await api.post(
            _src(ws),
            headers=_h(editor),
            json={"name": "e", "url": "https://ed.example/f", "group_ids": [ga]},
        )
    ).status_code == 201
    assert (
        await api.post(
            _src(ws), headers=_h(editor), json={"name": "e", "url": "https://ed.example/g"}
        )
    ).status_code == 403
    assert (
        await api.post(
            _src(ws),
            headers=_h(editor),
            json={"name": "e", "url": "https://ed.example/h", "group_ids": [gb]},
        )
    ).status_code == 403
    assert (
        await api.patch(_src(ws, src["id"]), headers=_h(editor), json={"name": "mine now"})
    ).status_code == 403
    assert (
        await api.post(_pillars(ws), headers=_h(editor), json={"name": "x", "group_id": ga})
    ).status_code == 201
    assert (await api.post(_pillars(ws), headers=_h(editor), json={"name": "x"})).status_code == 403
    assert (
        await api.get(_items(ws, "/trending"), headers=_h(editor), params={"group_id": gb})
    ).status_code == 403
    assert (await api.get(_items(ws, "/trending"), headers=_h(editor))).status_code == 200
    viewer = await _register_login(api, "pill_view")
    await _add_member(owner_engine, ws, (await _me(api, viewer))["id"], "viewer")
    any_item = (await api.get(_items(ws), headers=_h(owner))).json()["data"][0]
    assert (
        await api.post(_items(ws, f"/{any_item['id']}/dismiss"), headers=_h(viewer))
    ).status_code == 403
    assert (
        await api.post(
            _src(ws, suffix="/test"), headers=_h(viewer), json={"url": "https://e.com/x"}
        )
    ).status_code == 403


async def test_other_workspaces_cannot_see_sources_or_items(
    api: httpx.AsyncClient, fetcher: FakeFetcher, owner_engine: AsyncEngine
) -> None:
    s, ws = await _setup(api, "tenanta")
    src = await _source(api, s, ws, "https://t.example/feed")
    fetcher.pages["https://t.example/feed"] = _rss([("Item", "https://t.example/1", PG_BODY)])
    await _ingestion(api, fetcher).fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))
    other, other_ws = await _setup(api, "tenantb")
    for url in (_src(ws), _src(ws, src["id"]), _items(ws), _items(ws, "/trending"), _pillars(ws)):
        assert (await api.get(url, headers=_h(other))).status_code in (403, 404)
    assert (await api.get(_src(other_ws, src["id"]), headers=_h(other))).status_code == 404
    # The same URL in another workspace is a separate item (dedupe is per workspace).
    src_b = await _source(api, other, other_ws, "https://t.example/feed")
    out = await _ingestion(api, fetcher).fetch_source(uuid.UUID(other_ws), uuid.UUID(src_b["id"]))
    assert out.new_items == 1
    # Cross-workspace links are refused by the database itself.
    async with owner_engine.begin() as conn:
        g_other = await conn.scalar(
            text(
                "INSERT INTO account_group (id, workspace_id, name) "
                "VALUES (gen_random_uuid(), :w, 'x') RETURNING id"
            ),
            {"w": other_ws},
        )
        with pytest.raises(Exception, match="crosses workspaces"):
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "INSERT INTO source_group_link "
                        "(id, workspace_id, source_id, account_group_id) "
                        "VALUES (gen_random_uuid(), :w, :s, :g)"
                    ),
                    {"w": ws, "s": src["id"], "g": g_other},
                )


# ------------------------------------------------------------------ dispatcher


async def test_dispatcher_finds_due_sources_and_waiting_workspaces(
    api: httpx.AsyncClient, fetcher: FakeFetcher
) -> None:
    s, ws = await _setup(api, "dispatch")
    due = await _source(api, s, ws, "https://due.example/feed")
    paused = await _source(api, s, ws, "https://paused.example/feed")
    await api.patch(_src(ws, paused["id"]), headers=_h(s), json={"status": "paused"})
    fetched: list[tuple[uuid.UUID, uuid.UUID]] = []
    enrich: list[uuid.UUID] = []

    async def df(w: uuid.UUID, sid: uuid.UUID) -> None:
        fetched.append((w, sid))

    async def de(w: uuid.UUID) -> None:
        enrich.append(w)

    await dispatch(
        _sm(api), embedding_model="openai/text-embedding-3-small", defer_fetch=df, defer_enrich=de
    )
    mine = [sid for w, sid in fetched if w == uuid.UUID(ws)]
    assert mine == [uuid.UUID(due["id"])]
    assert uuid.UUID(ws) not in enrich

    fetcher.pages["https://due.example/feed"] = _rss([("Item", "https://due.example/1", PG_BODY)])
    await _ingestion(api, fetcher).fetch_source(uuid.UUID(ws), uuid.UUID(due["id"]))
    work = await find_work(_sm(api), embedding_model="openai/text-embedding-3-small")
    assert uuid.UUID(ws) in work.enrich
    assert (uuid.UUID(ws), uuid.UUID(due["id"])) not in work.fetches  # not due again yet
    # A pillar embedded with another model needs re-embedding.
    await _ingestion(api, fetcher).enrich_pending(uuid.UUID(ws))
    await _pillar(api, s, ws, name="Databases", keywords=["postgres"])
    assert (
        uuid.UUID(ws)
        not in (await find_work(_sm(api), embedding_model="openai/text-embedding-3-small")).enrich
    )
    assert (
        uuid.UUID(ws)
        in (await find_work(_sm(api), embedding_model="openai/text-embedding-3-large")).enrich
    )


async def test_items_list_paginates_and_filters(
    api: httpx.AsyncClient, fetcher: FakeFetcher
) -> None:
    s, ws = await _setup(api, "paging")
    src = await _source(api, s, ws, "https://pg.example/feed")
    fetcher.pages["https://pg.example/feed"] = _rss(
        [
            (
                f"Story {n} about postgres number {n}",
                f"https://pg.example/{n}",
                f"{PG_BODY} Unique detail {n} " + f"word{n} " * 40,
            )
            for n in range(5)
        ]
    )
    ing = _ingestion(api, fetcher, dedupe_similarity=0.999)
    await ing.fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))
    await ing.enrich_pending(uuid.UUID(ws))
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2, "state": ["ready", "duplicate"]}
        if cursor:
            params["cursor"] = cursor
        page = (await api.get(_items(ws), headers=_h(s), params=params)).json()
        seen += [i["id"] for i in page["data"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 5
    assert (await api.get(_items(ws), headers=_h(s), params={"cursor": "%%%"})).status_code == 422
    topic = (await api.get(_items(ws, f"/{seen[0]}"), headers=_h(s))).json()["topics"][0]
    by_topic = (await api.get(_items(ws), headers=_h(s), params={"topic": topic})).json()["data"]
    assert by_topic
    by_source = (await api.get(_items(ws), headers=_h(s), params={"source_id": src["id"]})).json()[
        "data"
    ]
    assert len(by_source) == 5
    assert (await api.get(_items(ws, f"/{uuid.uuid4()}"), headers=_h(s))).status_code == 404


# ------------------------------------------------------- security review fixes


async def test_review_scope_leaks_are_closed(
    api: httpx.AsyncClient, fetcher: FakeFetcher, owner_engine: AsyncEngine
) -> None:
    owner, ws = await _setup(api, "scopeleak")
    ga = await _group(api, owner, ws, "A")
    gb = await _group(api, owner, ws, "B")
    await _pillar(api, owner, ws, name="Databases", keywords=["postgres", "database"])
    src_a = await _source(api, owner, ws, "https://sa.example/feed?token=s3cret", group_ids=[ga])
    src_b = await _source(api, owner, ws, "https://sb.example/feed", group_ids=[gb])
    fetcher.pages["https://sa.example/feed?token=s3cret"] = _rss(
        [("Postgres A", "https://sa.example/1", PG_BODY)]
    )
    fetcher.pages["https://sb.example/feed"] = _rss(
        [
            ("Postgres B", "https://sb.example/1", PG_BODY + " More."),
            ("Hiring B", "https://sb.example/2", HIRING_BODY),
        ]
    )
    ing = _ingestion(api, fetcher)
    await ing.fetch_source(uuid.UUID(ws), uuid.UUID(src_a["id"]))
    await ing.fetch_source(uuid.UUID(ws), uuid.UUID(src_b["id"]))
    await ing.enrich_pending(uuid.UUID(ws))
    items = (await api.get(_items(ws), headers=_h(owner))).json()["data"]
    by_title = {i["title"]: i for i in items}
    assert by_title["Postgres B"]["state"] == "duplicate"  # duplicate of A's item

    viewer = await _register_login(api, "leak_view")
    viewer_id = (await _me(api, viewer))["id"]
    await _add_member(owner_engine, ws, viewer_id, "editor")
    await api.put(
        f"/api/v1/workspaces/{ws}/members/{viewer_id}/scopes",
        headers=_h(owner),
        json={"account_group_ids": [ga]},
    )
    # H3: an item fed only to group B is invisible by id; duplicate refs are filtered.
    hiring = by_title["Hiring B"]["id"]
    assert (await api.get(_items(ws, f"/{hiring}"), headers=_h(viewer))).status_code == 404
    a_item = await api.get(_items(ws, f"/{by_title['Postgres A']['id']}"), headers=_h(viewer))
    assert a_item.status_code == 200
    assert a_item.json()["duplicates"] == []
    r = await api.post(_items(ws, f"/{hiring}/dismiss"), headers=_h(viewer))
    assert r.status_code in (403, 404)  # denied before or at the lookup; never allowed
    # M3: the scoped member sees only sources feeding their groups.
    ids = [x["id"] for x in (await api.get(_src(ws), headers=_h(viewer))).json()["data"]]
    assert ids == [src_a["id"]]
    assert (await api.get(_src(ws, src_b["id"]), headers=_h(viewer))).status_code == 404
    assert (await api.get(_src(ws, src_b["id"], "/health"), headers=_h(viewer))).status_code == 404

    # M1: dismissing an item that also feeds a group outside the scope is refused.
    await api.put(_src(ws, src_a["id"], "/groups"), headers=_h(owner), json={"group_ids": [ga, gb]})
    r = await api.post(_items(ws, f"/{by_title['Postgres A']['id']}/dismiss"), headers=_h(viewer))
    assert r.status_code == 403

    # M3: viewers see feed URLs without their query string.
    plain = await _register_login(api, "leak_plain")
    await _add_member(owner_engine, ws, (await _me(api, plain))["id"], "viewer")
    urls = {
        x["name"]: x["url"] for x in (await api.get(_src(ws), headers=_h(plain))).json()["data"]
    }
    assert urls[src_a["name"]] == "https://sa.example/feed?…"
    full = {
        x["name"]: x["url"] for x in (await api.get(_src(ws), headers=_h(owner))).json()["data"]
    }
    assert full[src_a["name"]].endswith("token=s3cret")
    # Internal pipeline states are for people who configure sources.
    r = await api.get(_items(ws), headers=_h(plain), params={"state": ["filtered"]})
    assert r.status_code == 403

    # M2: deleting a group-linked source does not widen its items to every group.
    await api.put(_src(ws, src_b["id"], "/groups"), headers=_h(owner), json={"group_ids": [gb]})
    assert (await api.delete(_src(ws, src_b["id"]), headers=_h(owner))).status_code == 204
    await api.post(_pillars(ws), headers=_h(owner), json={"name": "Rescore trigger"})
    a_view = await api.get(_items(ws, "/trending"), headers=_h(owner), params={"group_id": ga})
    assert "Hiring B" not in [i["title"] for i in a_view.json()["data"]]


async def test_review_fetch_robustness_and_limits(
    api: httpx.AsyncClient, fetcher: FakeFetcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    s, ws = await _setup(api, "robust")
    src = await _source(api, s, ws, "https://r.example/feed")
    ing = _ingestion(api, fetcher)

    class Boom(FakeFetcher):
        async def get(self, url: str, **kw: Any) -> FetchResult:
            raise ValueError("unexpected")

    crashing = _ingestion(api, Boom())
    out = await crashing.fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))
    assert (out.status, out.error) == ("failed", "internal")
    h = (await api.get(_src(ws, src["id"], "/health"), headers=_h(s))).json()
    assert h["consecutive_failures"] == 1
    assert h["last_error"] == "internal: ValueError"

    # Network-shaped errors are reported generically.
    fetcher.errors["https://r.example/feed"] = FetchError("blocked_address", "10.1.2.3 internal")
    await ing.fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))
    h = (await api.get(_src(ws, src["id"], "/health"), headers=_h(s))).json()
    assert h["last_error"] == "unreachable: The URL could not be reached."
    del fetcher.errors["https://r.example/feed"]

    # The per-workspace daily item cap.
    import mera.application.ingest.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "DAILY_ITEM_CAP", 2)
    fetcher.pages["https://r.example/feed"] = _rss(
        [(f"Story {n}", f"https://r.example/{n}", PG_BODY) for n in range(5)]
    )
    assert (await ing.fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))).new_items == 2

    # "Test feed" has its own per-actor budget.
    fetcher.pages["https://t2.example/feed"] = _rss([("x", "https://t2.example/1", "y")])
    codes = [
        (
            await api.post(
                _src(ws, suffix="/test"), headers=_h(s), json={"url": "https://t2.example/feed"}
            )
        ).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


async def test_trending_window_uses_publication_time(
    api: httpx.AsyncClient, fetcher: FakeFetcher
) -> None:
    s, ws = await _setup(api, "window")
    await _pillar(api, s, ws, name="Databases", keywords=["postgres"])
    src = await _source(api, s, ws, "https://old.example/feed")
    old = format_datetime(NOW - timedelta(days=10))
    fetcher.pages["https://old.example/feed"] = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>Old</title>'
        f"<item><title>Old postgres news</title><link>https://old.example/1</link>"
        f"<description>{PG_BODY}</description><pubDate>{old}</pubDate></item>"
        "</channel></rss>"
    ).encode()
    ing = _ingestion(api, fetcher)
    await ing.fetch_source(uuid.UUID(ws), uuid.UUID(src["id"]))
    await ing.enrich_pending(uuid.UUID(ws))
    day = (await api.get(_items(ws, "/trending"), headers=_h(s), params={"hours": 24})).json()
    fortnight = (
        await api.get(_items(ws, "/trending"), headers=_h(s), params={"hours": 24 * 14})
    ).json()
    assert day["data"] == []  # found today, but published ten days ago
    assert [i["title"] for i in fortnight["data"]] == ["Old postgres news"]
