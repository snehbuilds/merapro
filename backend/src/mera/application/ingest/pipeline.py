"""The ingestion pipeline (M4.2-4.9; docs/06 §4.2 "LLM use kept as late and as cheap as possible").

Two jobs, each idempotent and safe to retry:

``fetch_source`` (queue ``ingest``, no model)
    1. Conditional GET of the feed through ``SafeFetcher`` (ETag / Last-Modified).
    2. Parse; canonicalise each URL; **exact dedupe** against the workspace. A URL already
       stored only records that this source also carried it (``seen_source_ids``).
    3. For new entries whose feed text is thin, fetch the article page and extract it.
    4. Language detection and the source's keyword filter. Rejected items are stored as
       ``filtered`` so they are not fetched again, and are never shown.
    5. Insert the rest as ``pending``.

``enrich_pending`` (queue ``agents``)
    6. Embed pending items in one batch, then **semantic dedupe** against the last 14 days.
    7. Summarise and classify each survivor (Research agent, one cheap call per item).
    8. Score per group (arithmetic) and mark ``ready``.

Failures back off per source and stop retrying after ``STOP_RETRYING_AFTER`` (a person can
still press Retry). Spend is recorded per call and checked against the workspace's daily
cap before each summary, so a runaway feed cannot run up a bill.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Protocol

import structlog
from sqlalchemy import all_, func, literal, select, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.agents import models as agent_models
from mera.application.ingest import agent_runs, research, scoring
from mera.domain.context import WorkspaceContext
from mera.domain.policies import keyword_filter
from mera.domain.policies.url_canon import InvalidUrl, canonicalize, url_hash
from mera.domain.ports.llm import LLMError, LLMProvider, ModelConfig
from mera.domain.value_objects.content import ItemState, SourceStatus, SourceType
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    ContentItem,
    ContentPillar,
    Notification,
    Source,
    Workspace,
)
from mera.infrastructure.db.session import workspace_transaction
from mera.infrastructure.http.safe_fetch import FetchError, FetchResult, public_message
from mera.infrastructure.ingest.feeds import (
    FeedEntry,
    NotAFeed,
    decode,
    detect_language,
    extract_article_async,
    parse_feed_async,
)

log = structlog.get_logger(__name__)

FEED_MAX_BYTES: Final = 2 * 1024 * 1024
# New items stored per workspace per UTC day, across all its sources (review M4): a feed
# minting fresh URLs cannot grow the table, the dedupe window or rescoring without bound.
DAILY_ITEM_CAP: Final = 1000
# Items still pending after this long (e.g. the cost cap held them) are given up on.
PENDING_TTL: Final = timedelta(days=3)
_IN_CHUNK: Final = 1000
ARTICLE_MAX_BYTES: Final = 2 * 1024 * 1024
THIN_TEXT_CHARS: Final = 600
FAILING_AFTER: Final = 3
STOP_RETRYING_AFTER: Final = timedelta(hours=48)
MAX_ENRICH_ATTEMPTS: Final = 3
DEDUPE_WINDOW: Final = timedelta(days=14)
EMBED_TEXT_CHARS: Final = 4000
ARTICLE_CONCURRENCY: Final = 4
# One at a time: the daily cost cap is checked before each call, and parallel calls would
# all pass the check before any of them recorded its cost.
SUMMARY_CONCURRENCY: Final = 1


class Fetcher(Protocol):
    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        max_bytes: int,
        overall_timeout: float = 30.0,
        accept_statuses: frozenset[int] = frozenset({200, 304}),
    ) -> FetchResult: ...


@dataclass(frozen=True, slots=True)
class IngestConfig:
    research: ModelConfig
    embedding_model: str
    daily_cost_cap_usd: Decimal
    dedupe_similarity: float = 0.92
    monthly_cost_cap_usd: Decimal | None = None
    half_life_hours: float = 24.0
    max_new_items_per_fetch: int = 30


@dataclass(slots=True)
class FetchOutcome:
    status: str  # fetched, not_modified, failed, skipped
    new_items: int = 0
    filtered: int = 0
    already_seen: int = 0
    error: str | None = None


@dataclass(slots=True)
class EnrichOutcome:
    processed: int = 0
    ready: int = 0
    duplicates: int = 0
    failed: int = 0
    capped: bool = False
    remaining: bool = False


@dataclass(frozen=True, slots=True)
class PreviewItem:
    title: str
    url: str
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class Preview:
    ok: bool
    title: str | None = None
    item_count: int = 0
    items: list[PreviewItem] = field(default_factory=list)
    error_code: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _SourceSnap:
    id: uuid.UUID
    name: str
    url: str
    etag: str | None
    last_modified: str | None
    include: list[str]
    exclude: list[str]
    language: str | None
    interval: int
    mcp: dict[str, Any] | None = None  # P3-M6.6: server_id, tool, arguments


class McpItemReader(Protocol):
    async def read_items(
        self, workspace_id: uuid.UUID, server_id: uuid.UUID, tool: str, arguments: dict[str, Any]
    ) -> list[dict[str, Any]]: ...


@dataclass(slots=True)
class _NewItem:
    url: str
    canonical: str
    hash: bytes
    title: str
    author: str | None
    published_at: datetime | None
    text: str
    image_url: str | None
    provenance: dict[str, Any]


class Ingestion:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        *,
        fetcher: Fetcher,
        llm: LLMProvider,
        config: IngestConfig,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sm = sm
        self._fetcher = fetcher
        self._llm = llm
        self._cfg = config
        self._clock = clock
        self.mcp: McpItemReader | None = None  # set where an MCP registry exists

    def _tx(self, workspace_id: uuid.UUID) -> Any:
        return workspace_transaction(self._sm, WorkspaceContext(workspace_id))

    # ------------------------------------------------------------------ fetch

    async def fetch_source(self, workspace_id: uuid.UUID, source_id: uuid.UUID) -> FetchOutcome:
        now = self._clock()
        async with self._tx(workspace_id) as s:
            src = await s.get(Source, source_id)
            if (
                src is None
                or src.status == SourceStatus.PAUSED.value
                or src.source_type not in (SourceType.RSS.value, SourceType.MCP.value)
            ):
                return FetchOutcome("skipped")
            snap = _SourceSnap(
                id=src.id,
                name=src.name,
                url=str(src.config.get("url", "")),
                etag=src.http_etag,
                last_modified=src.http_last_modified,
                include=list(src.include_keywords),
                exclude=list(src.exclude_keywords),
                language=src.language,
                interval=src.fetch_interval_seconds,
                mcp=(
                    {
                        "server_id": src.config.get("mcp_server_id"),
                        "tool": src.config.get("mcp_tool"),
                        "arguments": src.config.get("mcp_arguments") or {},
                    }
                    if src.source_type == SourceType.MCP.value
                    else None
                ),
            )
            # Claim the slot so the dispatcher does not queue it again mid-fetch.
            src.last_fetched_at = now
            src.next_fetch_at = now + timedelta(seconds=snap.interval)
        try:
            return await self._fetch_claimed(workspace_id, snap, now)
        except Exception as exc:
            # Anything unexpected (a database error, a malformed value) still counts as a
            # failed fetch: back-off, the failure streak and the notification all apply.
            log.exception("ingest.fetch_crashed", source_id=str(snap.id))
            await self._record_failure(
                workspace_id, snap, now, f"internal: {type(exc).__name__}", None
            )
            return FetchOutcome("failed", error="internal")

    async def _fetch_claimed(
        self, workspace_id: uuid.UUID, snap: _SourceSnap, now: datetime
    ) -> FetchOutcome:
        if snap.mcp is not None:
            return await self._fetch_mcp(workspace_id, snap, now)
        headers: dict[str, str] = {}
        if snap.etag:
            headers["If-None-Match"] = snap.etag
        if snap.last_modified:
            headers["If-Modified-Since"] = snap.last_modified
        try:
            res = await self._fetcher.get(snap.url, headers=headers, max_bytes=FEED_MAX_BYTES)
            if res.not_modified:
                await self._record_success(workspace_id, snap, now, res, new_items=0)
                return FetchOutcome("not_modified")
            feed = await parse_feed_async(res.body, base_url=res.url)
        except FetchError as exc:
            log.info("ingest.fetch_error", source_id=str(snap.id), kind=exc.kind, detail=str(exc))
            kind, message = public_message(exc.kind, str(exc))
            await self._record_failure(workspace_id, snap, now, f"{kind}: {message}", exc.status)
            return FetchOutcome("failed", error=kind)
        except NotAFeed as exc:
            await self._record_failure(workspace_id, snap, now, f"not_a_feed: {exc}", res.status)
            return FetchOutcome("failed", error="not_a_feed")

        return await self._store(workspace_id, snap, now, res, feed.entries, res.url)

    async def _fetch_mcp(
        self, workspace_id: uuid.UUID, snap: _SourceSnap, now: datetime
    ) -> FetchOutcome:
        """P3-M6.6: an approved MCP read tool is the feed. Its items are data, never
        instructions: they go through the same canonicalisation, filters and scoring."""
        assert snap.mcp is not None
        from mera.infrastructure.mcp_client import McpClientError

        try:
            if self.mcp is None:
                raise McpClientError("MCP is not available here")
            raw = await self.mcp.read_items(
                workspace_id,
                uuid.UUID(str(snap.mcp["server_id"])),
                str(snap.mcp["tool"]),
                dict(snap.mcp["arguments"]),
            )
        except (McpClientError, ValueError) as exc:
            await self._record_failure(workspace_id, snap, now, f"mcp: {exc}"[:500], None)
            return FetchOutcome("failed", error="mcp")
        entries = [e for e in (_mcp_entry(d) for d in raw) if e is not None]
        source = f"mcp://{snap.mcp['server_id']}/{snap.mcp['tool']}"
        res = FetchResult(url=source, status=200, headers={}, body=b"")
        return await self._store(workspace_id, snap, now, res, entries, source)

    async def _store(
        self,
        workspace_id: uuid.UUID,
        snap: _SourceSnap,
        now: datetime,
        res: FetchResult,
        entries: Sequence[FeedEntry],
        origin: str,
    ) -> FetchOutcome:
        candidates: dict[bytes, _NewItem] = {}
        for e in entries:
            try:
                canonical = canonicalize(e.url)
            except InvalidUrl:
                continue
            h = url_hash(canonical)
            if h in candidates:
                continue
            candidates[h] = _NewItem(
                url=e.url[:2048],
                canonical=canonical[:2048],
                hash=h,
                title=e.title,
                author=e.author,
                published_at=e.published_at,
                text=e.text,
                image_url=e.image_url,
                provenance={
                    "feed_url": origin,
                    "http_status": res.status,
                    "extractor": "mcp" if snap.mcp is not None else "feed",
                },
            )

        async with self._tx(workspace_id) as s:
            existing: list[Any] = []
            hashes = list(candidates)
            for i in range(0, len(hashes), _IN_CHUNK):
                existing += (
                    await s.execute(
                        select(ContentItem.id, ContentItem.url_hash, ContentItem.state).where(
                            ContentItem.url_hash.in_(hashes[i : i + _IN_CHUNK])
                        )
                    )
                ).all()
            # Atomic append (no read-modify-write race with a concurrent fetch).
            appended = list(
                (
                    await s.execute(
                        update(ContentItem)
                        .where(
                            ContentItem.id.in_([r.id for r in existing]),
                            literal(snap.id, PG_UUID(as_uuid=True))
                            != all_(ContentItem.seen_source_ids),
                        )
                        .values(
                            seen_source_ids=func.array_append(ContentItem.seen_source_ids, snap.id)
                        )
                        .returning(ContentItem.id)
                    )
                ).scalars()
            )
            if appended:
                # Another source carrying the item can widen the groups it feeds.
                await scoring.rescore(
                    s, workspace_id, item_ids=appended, half_life_hours=self._cfg.half_life_hours
                )
            stored_today = await s.scalar(
                select(func.count())
                .select_from(ContentItem)
                .where(ContentItem.fetched_at >= agent_runs.utc_day_start(now))
            )
        room = max(DAILY_ITEM_CAP - int(stored_today or 0), 0)
        seen = {bytes(r.url_hash) for r in existing}
        unseen = [c for h, c in candidates.items() if h not in seen]
        fresh = unseen[: min(self._cfg.max_new_items_per_fetch, room)]
        if len(unseen) > len(fresh) and room < self._cfg.max_new_items_per_fetch:
            log.warning("ingest.daily_item_cap", workspace_id=str(workspace_id), cap=DAILY_ITEM_CAP)

        sem = asyncio.Semaphore(ARTICLE_CONCURRENCY)
        await asyncio.gather(*(self._extract(sem, item) for item in fresh))

        rows: list[dict[str, Any]] = []
        filtered = 0
        for item in fresh:
            lang = detect_language(f"{item.title}. {item.text}")
            verdict = keyword_filter.apply(
                f"{item.title}\n{item.text}",
                include=snap.include,
                exclude=snap.exclude,
                language=snap.language,
                detected_language=lang,
            )
            if not verdict.admitted:
                filtered += 1
            rows.append(
                {
                    "id": uuid7(),
                    "workspace_id": workspace_id,
                    "source_id": snap.id,
                    "url": item.url,
                    "canonical_url": item.canonical,
                    "url_hash": item.hash,
                    "title": item.title[:500],
                    "author": item.author,
                    "published_at": item.published_at,
                    "extracted_text": item.text or None,
                    "language": lang,
                    "og_image_url": _https_or_none(item.image_url),
                    "provenance": {**item.provenance, "fetched_at": now.isoformat()},
                    "seen_source_ids": [snap.id],
                    "state": (ItemState.PENDING if verdict.admitted else ItemState.FILTERED).value,
                    "state_reason": verdict.reason,
                    "fetched_at": now,
                }
            )
        async with self._tx(workspace_id) as s:
            if rows:
                # A concurrent fetch of another source may have inserted the same URL.
                await s.execute(
                    insert(ContentItem)
                    .values(rows)
                    .on_conflict_do_nothing(constraint="uq_content_item_url")
                )
        await self._record_success(workspace_id, snap, now, res, new_items=len(rows) - filtered)
        log.info(
            "ingest.fetched",
            source_id=str(snap.id),
            new=len(rows) - filtered,
            filtered=filtered,
            already_seen=len(seen),
        )
        return FetchOutcome(
            "fetched", new_items=len(rows) - filtered, filtered=filtered, already_seen=len(seen)
        )

    async def _extract(self, sem: asyncio.Semaphore, item: _NewItem) -> None:
        if len(item.text) >= THIN_TEXT_CHARS:
            return
        async with sem:
            try:
                page = await self._fetcher.get(
                    item.url,
                    max_bytes=ARTICLE_MAX_BYTES,
                    overall_timeout=20.0,
                    accept_statuses=frozenset({200}),
                )
            except FetchError as exc:
                item.provenance["article_error"] = exc.kind
                return
        try:
            extracted = await extract_article_async(
                decode(page.body, page.headers.get("content-type")), url=page.url
            )
        except Exception as exc:  # a parser crash on one page must not lose the feed
            item.provenance["article_error"] = f"extract:{type(exc).__name__}"
            return
        if len(extracted.text) > len(item.text):
            item.text = extracted.text
            item.provenance["extractor"] = "trafilatura"
        item.image_url = item.image_url or extracted.image_url
        item.published_at = item.published_at or extracted.published_at
        item.author = item.author or (extracted.author[:200] if extracted.author else None)

    async def _record_success(
        self,
        workspace_id: uuid.UUID,
        snap: _SourceSnap,
        now: datetime,
        res: FetchResult,
        *,
        new_items: int,
    ) -> None:
        async with self._tx(workspace_id) as s:
            values: dict[str, Any] = {
                "status": func.coalesce(
                    func.nullif(Source.status, SourceStatus.FAILING.value),
                    SourceStatus.ACTIVE.value,
                ),
                "last_success_at": now,
                "last_error": None,
                "last_http_status": res.status,
                "last_yield": new_items,
                "consecutive_failures": 0,
                "failing_since": None,
                "next_fetch_at": now + timedelta(seconds=snap.interval),
            }
            if not res.not_modified:
                values["http_etag"] = res.headers.get("etag") or None
                values["http_last_modified"] = res.headers.get("last-modified") or None
            await s.execute(update(Source).where(Source.id == snap.id).values(**values))

    async def _record_failure(
        self,
        workspace_id: uuid.UUID,
        snap: _SourceSnap,
        now: datetime,
        error: str,
        http_status: int | None,
    ) -> None:
        async with self._tx(workspace_id) as s:
            src = await s.get(Source, snap.id, with_for_update=True)
            if src is None:
                return
            src.consecutive_failures += 1
            src.failing_since = src.failing_since or now
            src.last_error = error[:500]
            src.last_http_status = http_status
            src.last_yield = 0
            became_failing = (
                src.consecutive_failures >= FAILING_AFTER
                and src.status == SourceStatus.ACTIVE.value
            )
            if became_failing:
                src.status = SourceStatus.FAILING.value
                s.add(
                    Notification(
                        id=uuid7(),
                        workspace_id=workspace_id,
                        kind="source_failing",
                        severity="warning",
                        title=f"{src.name} is failing",
                        body=f"{src.consecutive_failures} fetches in a row failed ({error[:160]}).",
                        subject_type="source",
                        subject_id=src.id,
                    )
                )
            if now - src.failing_since >= STOP_RETRYING_AFTER:
                src.next_fetch_at = None  # stop until someone presses Retry
            else:
                backoff = min(
                    snap.interval * 2 ** (src.consecutive_failures - 1), max(snap.interval, 3600)
                )
                src.next_fetch_at = now + timedelta(seconds=backoff)
        log.warning("ingest.fetch_failed", source_id=str(snap.id), error=error[:120])

    # --------------------------------------------------------------- preview

    async def preview(self, url: str) -> Preview:
        """Fetch and parse without storing anything (the "Test feed" button)."""
        try:
            res = await self._fetcher.get(
                url, max_bytes=FEED_MAX_BYTES, accept_statuses=frozenset({200})
            )
            feed = await parse_feed_async(res.body, base_url=res.url)
        except FetchError as exc:
            cause = exc.__cause__ or exc.__context__
            log.info(
                "ingest.preview_failed",
                kind=exc.kind,
                detail=str(exc),
                cause=type(cause).__name__ if cause else None,
                cause_detail=str(cause)[:200] if cause else None,
            )
            code, message = public_message(exc.kind, str(exc))
            return Preview(ok=False, error_code=code, error=message)
        except NotAFeed as exc:
            return Preview(ok=False, error_code="not_a_feed", error=str(exc))
        return Preview(
            ok=True,
            title=feed.title,
            item_count=len(feed.entries),
            items=[PreviewItem(e.title, e.url, e.published_at) for e in feed.entries[:5]],
        )

    # --------------------------------------------------------------- pillars

    async def embed_text(
        self,
        workspace_id: uuid.UUID,
        text: str,
        *,
        trigger: str,
        subject_id: uuid.UUID | None = None,
    ) -> list[float] | None:
        """Embed one pillar's text as its own recorded run. ``None`` when the provider
        fails: the pillar is kept and ``embed_missing_pillars`` retries it later."""
        async with self._tx(workspace_id) as s:
            if await self._over_cap(s, workspace_id, self._clock()):
                return None  # embedded later, once the day's budget allows
        run = await agent_runs.start_run(
            self._sm,
            workspace_id,
            workflow_key="embed_pillar",
            trigger=trigger,
            subject_type="content_pillar" if subject_id else None,
            subject_id=subject_id,
        )
        try:
            emb = await self._llm.embed(self._cfg.embedding_model, [text])
        except LLMError as exc:
            async with self._tx(workspace_id) as s:
                await agent_runs.record_step(
                    s,
                    run,
                    agent_key=research.AGENT_KEY,
                    step_type="embedding",
                    usage=exc.usage,
                    state="failed",
                    error=str(exc)[:300],
                    input_redacted={"chars": len(text)},
                )
            await agent_runs.finish_run(self._sm, run, state="failed", error="embedding failed")
            return None
        async with self._tx(workspace_id) as s:
            await agent_runs.record_step(
                s,
                run,
                agent_key=research.AGENT_KEY,
                step_type="embedding",
                usage=emb.usage,
                state="succeeded",
                input_redacted={"chars": len(text)},
            )
        await agent_runs.finish_run(self._sm, run, state="succeeded")
        return emb.vectors[0]

    @property
    def embedding_model(self) -> str:
        return self._cfg.embedding_model

    async def embed_missing_pillars(self, workspace_id: uuid.UUID) -> int:
        """Pillars saved while the provider was down, or embedded with an older model."""
        async with self._tx(workspace_id) as s:
            todo = (
                await s.execute(
                    select(
                        ContentPillar.id,
                        ContentPillar.name,
                        ContentPillar.description,
                        ContentPillar.keywords,
                    ).where(
                        (ContentPillar.embedding.is_(None))
                        | (
                            ContentPillar.embedding_model.is_distinct_from(
                                self._cfg.embedding_model
                            )
                        )
                    )
                )
            ).all()
        done = 0
        for p in todo:
            vec = await self.embed_text(
                workspace_id,
                pillar_text(p.name, p.description, p.keywords),
                trigger="schedule",
                subject_id=p.id,
            )
            if vec is None:
                break
            async with self._tx(workspace_id) as s:
                await s.execute(
                    update(ContentPillar)
                    .where(ContentPillar.id == p.id)
                    .values(embedding=vec, embedding_model=self._cfg.embedding_model)
                )
            done += 1
        if done:
            async with self._tx(workspace_id) as s:
                await scoring.rescore_recent(
                    s, workspace_id, now=self._clock(), half_life_hours=self._cfg.half_life_hours
                )
        return done

    # ---------------------------------------------------------------- enrich

    async def enrich_pending(self, workspace_id: uuid.UUID, *, limit: int = 25) -> EnrichOutcome:
        await self.embed_missing_pillars(workspace_id)
        now = self._clock()
        out = EnrichOutcome()
        async with self._tx(workspace_id) as s:
            await s.execute(
                update(ContentItem)
                .where(
                    ContentItem.state == ItemState.PENDING.value,
                    ContentItem.fetched_at < now - PENDING_TTL,
                )
                .values(state=ItemState.FAILED.value, state_reason="expired_pending")
            )
            if await self._over_cap(s, workspace_id, now):
                out.capped = True
                return out
            items = (
                await s.execute(
                    select(
                        ContentItem.id,
                        ContentItem.title,
                        ContentItem.extracted_text,
                        ContentItem.source_id,
                        ContentItem.embedding,
                    )
                    .where(ContentItem.state == ItemState.PENDING.value)
                    .order_by(ContentItem.fetched_at)
                    .limit(limit + 1)
                )
            ).all()
            out.remaining = len(items) > limit
            items = items[:limit]
            if not items:
                return out
            pillars = (
                await s.execute(
                    # Workspace-wide pillars only: the summary is shared by every group, so
                    # one group's pillar text must not steer what other groups read.
                    select(ContentPillar.name, ContentPillar.description, ContentPillar.keywords)
                    .where(ContentPillar.account_group_id.is_(None))
                    .order_by(ContentPillar.name)
                )
            ).all()
            names = dict((await s.execute(select(Source.id, Source.name))).all())
        pillar_prompt = list(
            dict.fromkeys((p.name, p.description, tuple(p.keywords)) for p in pillars)
        )

        run = await agent_runs.start_run(
            self._sm, workspace_id, workflow_key="ingest_enrich", trigger="schedule"
        )
        # 6. Embed (items embedded on an earlier, interrupted run keep their vector).
        to_embed = [i for i in items if i.embedding is None]
        vectors: dict[uuid.UUID, list[float]] = {}
        if to_embed:
            texts = [
                f"{i.title}\n\n{(i.extracted_text or '')[:EMBED_TEXT_CHARS]}" for i in to_embed
            ]
            try:
                emb = await self._llm.embed(self._cfg.embedding_model, texts)
            except LLMError as exc:
                async with self._tx(workspace_id) as s:
                    await agent_runs.record_step(
                        s,
                        run,
                        agent_key=research.AGENT_KEY,
                        step_type="embedding",
                        usage=exc.usage,
                        state="failed",
                        error=str(exc)[:300],
                        input_redacted={"items": len(texts)},
                    )
                    await self._bump_attempts(s, [i.id for i in to_embed])
                await agent_runs.finish_run(self._sm, run, state="failed", error="embedding failed")
                out.failed = len(to_embed)
                return out
            async with self._tx(workspace_id) as s:
                await agent_runs.record_step(
                    s,
                    run,
                    agent_key=research.AGENT_KEY,
                    step_type="embedding",
                    usage=emb.usage,
                    state="succeeded",
                    input_redacted={"items": len(texts)},
                )
            vectors = {i.id: v for i, v in zip(to_embed, emb.vectors, strict=True)}

        survivors = []
        for item in items:
            vec = vectors.get(item.id)
            if vec is None:
                if item.embedding is None:
                    continue
                vec = [float(x) for x in item.embedding]
            async with self._tx(workspace_id) as s:
                dup = await self._nearest(s, item.id, vec, now)
                if dup is not None and dup[1] >= self._cfg.dedupe_similarity:
                    await s.execute(
                        update(ContentItem)
                        .where(ContentItem.id == item.id)
                        .values(
                            embedding=vec,
                            state=ItemState.DUPLICATE.value,
                            duplicate_of=dup[0],
                            duplicate_similarity=round(dup[1], 4),
                            enriched_at=now,
                        )
                    )
                    out.duplicates += 1
                    out.processed += 1
                    continue
                if item.id in vectors:
                    await s.execute(
                        update(ContentItem).where(ContentItem.id == item.id).values(embedding=vec)
                    )
            survivors.append(item)

        # 7-8. Summarise, classify, score.
        sem = asyncio.Semaphore(SUMMARY_CONCURRENCY)
        results = await asyncio.gather(
            *(
                self._summarise(sem, workspace_id, run, item, names, pillar_prompt)
                for item in survivors
            )
        )
        for r in results:
            if r == "capped":
                out.capped = True
                continue
            out.processed += 1
            if r == "ready":
                out.ready += 1
            else:
                out.failed += 1
        await agent_runs.finish_run(
            self._sm,
            run,
            state="failed" if out.failed and not out.ready and not out.duplicates else "succeeded",
            error=(
                "; ".join(
                    x
                    for x in (
                        f"{out.failed} item(s) failed and will be retried" if out.failed else "",
                        "daily cost cap reached" if out.capped else "",
                    )
                    if x
                )
                or None
            ),
        )
        log.info(
            "ingest.enriched",
            workspace_id=str(workspace_id),
            ready=out.ready,
            duplicates=out.duplicates,
            failed=out.failed,
            capped=out.capped,
        )
        return out

    async def _summarise(
        self,
        sem: asyncio.Semaphore,
        workspace_id: uuid.UUID,
        run: agent_runs.RunHandle,
        item: Any,
        names: dict[uuid.UUID, str],
        pillar_prompt: list[tuple[str, str | None, tuple[str, ...]]],
    ) -> str:
        async with sem:
            now = self._clock()
            async with self._tx(workspace_id) as s:
                if await self._over_cap(s, workspace_id, now):
                    return "capped"
                ws_row = await s.get(Workspace, workspace_id)
                research_cfg = agent_models.override(
                    self._cfg.research, ws_row.settings if ws_row else None, research.AGENT_KEY
                )
            messages = research.build_messages(
                pillars=pillar_prompt,
                title=item.title,
                source_name=names.get(item.source_id, "unknown source")
                if item.source_id
                else "unknown source",
                text=item.extracted_text or item.title,
            )
            redacted = {
                "content_item_id": str(item.id),
                "article_chars": len(item.extracted_text or ""),
            }
            try:
                result = await self._llm.structured(
                    research_cfg, messages, research.ResearchSummary
                )
            except LLMError as exc:
                async with self._tx(workspace_id) as s:
                    await agent_runs.record_step(
                        s,
                        run,
                        agent_key=research.AGENT_KEY,
                        step_type="llm_call",
                        usage=exc.usage,
                        state="failed",
                        error=f"{exc.kind}: {str(exc)[:240]}",
                        input_redacted=redacted,
                        subject_type="content_item",
                        subject_id=item.id,
                    )
                    attempts = await self._bump_attempts(s, [item.id])
                    if attempts.get(item.id, 0) >= MAX_ENRICH_ATTEMPTS:
                        # Scoring is arithmetic and does not need the summary: show the item.
                        await s.execute(
                            update(ContentItem)
                            .where(ContentItem.id == item.id)
                            .values(
                                state=ItemState.READY.value,
                                state_reason="summary_unavailable",
                                enriched_at=now,
                            )
                        )
                        await scoring.rescore(
                            s,
                            workspace_id,
                            item_ids=[item.id],
                            half_life_hours=self._cfg.half_life_hours,
                        )
                return "failed"
            v = result.value
            async with self._tx(workspace_id) as s:
                await agent_runs.record_step(
                    s,
                    run,
                    agent_key=research.AGENT_KEY,
                    step_type="llm_call",
                    usage=result.usage,
                    state="succeeded",
                    input_redacted=redacted,
                    output={
                        "topics": v.topics,
                        "engagement_potential": v.engagement_potential,
                        "risk_flags": list(v.risk_flags),
                    },
                    subject_type="content_item",
                    subject_id=item.id,
                )
                await s.execute(
                    update(ContentItem)
                    .where(ContentItem.id == item.id)
                    .values(
                        summary=v.summary,
                        topics=v.topics,
                        engagement_potential=v.engagement_potential,
                        engagement_rationale=v.engagement_rationale or None,
                        risk_flags=list(v.risk_flags),
                        state=ItemState.READY.value,
                        state_reason=None,
                        enriched_at=now,
                    )
                )
                await scoring.rescore(
                    s, workspace_id, item_ids=[item.id], half_life_hours=self._cfg.half_life_hours
                )
            return "ready"

    async def _nearest(
        self, s: AsyncSession, item_id: uuid.UUID, vec: list[float], now: datetime
    ) -> tuple[uuid.UUID, float] | None:
        # An exact scan of the workspace's 14-day window (hundreds of rows): MATERIALIZED
        # keeps the planner from switching to the HNSW index and filtering afterwards,
        # which could drop every candidate in a busy multi-tenant table.
        recent = (
            select(ContentItem.id, ContentItem.embedding)
            .where(
                ContentItem.id != item_id,
                ContentItem.embedding.is_not(None),
                ContentItem.duplicate_of.is_(None),
                ContentItem.state.in_([ItemState.READY.value, ItemState.PENDING.value]),
                ContentItem.fetched_at > now - DEDUPE_WINDOW,
            )
            .cte("recent")
            .prefix_with("MATERIALIZED")
        )
        distance = recent.c.embedding.cosine_distance(vec)
        row = (
            await s.execute(select(recent.c.id, distance.label("d")).order_by(distance).limit(1))
        ).first()
        if row is None:
            return None
        return row.id, 1.0 - float(row.d)

    async def _bump_attempts(self, s: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        rows = (
            await s.execute(
                update(ContentItem)
                .where(ContentItem.id.in_(ids))
                .values(enrich_attempts=ContentItem.enrich_attempts + 1)
                .returning(ContentItem.id, ContentItem.enrich_attempts)
            )
        ).all()
        stuck = [r.id for r in rows if r.enrich_attempts >= MAX_ENRICH_ATTEMPTS]
        if stuck:  # embedding keeps failing: give up on these rather than loop forever
            await s.execute(
                update(ContentItem)
                .where(ContentItem.id.in_(stuck), ContentItem.embedding.is_(None))
                .values(state=ItemState.FAILED.value, state_reason="enrichment_failed")
            )
        return {r.id: r.enrich_attempts for r in rows}

    async def _over_cap(self, s: AsyncSession, workspace_id: uuid.UUID, now: datetime) -> bool:
        spent = await agent_runs.spent_today(s, agent_key=research.AGENT_KEY, now=now)
        monthly = self._cfg.monthly_cost_cap_usd
        under_month = monthly is None or await agent_runs.spent_this_month(s, now=now) < monthly
        if spent < self._cfg.daily_cost_cap_usd and under_month:
            return False
        day = agent_runs.utc_day_start(now)
        already = await s.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.kind == "cost_cap_reached", Notification.created_at >= day)
        )
        if not already:
            s.add(
                Notification(
                    id=uuid7(),
                    workspace_id=workspace_id,
                    kind="cost_cap_reached",
                    severity="warning",
                    title="Research paused: daily cost cap reached"
                    if under_month
                    else "Research paused: monthly AI budget reached",
                    body=(
                        f"Summarising new items has spent ${spent:.2f} today (cap "
                        f"${self._cfg.daily_cost_cap_usd:.2f}). New items wait until "
                        "tomorrow (UTC)."
                        if under_month
                        else "This workspace has spent its monthly AI budget. New items wait "
                        "until the next month (UTC) or a higher cap."
                    ),
                )
            )
        return True


def pillar_text(name: str, description: str | None, keywords: Sequence[str]) -> str:
    parts = [name.strip()]
    if description:
        parts.append(description.strip())
    if keywords:
        parts.append("Keywords: " + ", ".join(keywords))
    return ". ".join(parts)


def _https_or_none(url: str | None) -> str | None:
    if url and url.startswith("https://") and len(url) <= 2048:
        return url
    return None


def _mcp_entry(d: dict[str, Any]) -> FeedEntry | None:
    """One MCP item as a feed entry: a title and an http(s) url are required."""
    url, title = d.get("url"), d.get("title")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    published: datetime | None = None
    if isinstance(d.get("published_at"), str):
        try:
            published = datetime.fromisoformat(d["published_at"].replace("Z", "+00:00"))
        except ValueError:
            published = None
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
    text = d.get("text") or d.get("summary") or ""
    image = d.get("image_url")
    author = d.get("author")
    return FeedEntry(
        url=url[:2048],
        title=title.strip()[:500],
        author=author[:200] if isinstance(author, str) else None,
        published_at=published,
        text=text[:20_000] if isinstance(text, str) else "",
        image_url=image if isinstance(image, str) and image.startswith("https://") else None,
    )
