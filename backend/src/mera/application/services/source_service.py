"""Sources and the groups they feed (M4.10, 3.10; docs/04 §4.4, docs/03 §5).

Authorization follows what a source *feeds*: a source linked to groups is configured with
``CONFIGURE_CONTENT`` on each of those groups, so a group-scoped Editor can run their own
group's feeds; a workspace-wide source (no links) feeds every group and needs the
permission without a scope. Reading the list is workspace-level (``READ``): a source's
configuration is a URL and keywords, nothing secret, and an Editor deciding what to draft
needs to see where items come from.

A fetch is never run inside a request. "Fetch now" and "Retry" set ``next_fetch_at`` to
now; the ingest dispatcher picks the source up within a minute.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import ConflictError, InvalidInput, NotFoundError, TooManyAttempts
from mera.application.ingest import scoring
from mera.application.ingest.pipeline import Ingestion, Preview
from mera.application.services.base import WorkspaceScopedService
from mera.application.services.principals import load_user_principal
from mera.domain.policies import keyword_filter, rbac
from mera.domain.policies.rbac import Permission
from mera.domain.policies.url_canon import InvalidUrl, canonicalize
from mera.domain.principal import Principal
from mera.domain.value_objects.content import (
    AVAILABLE_SOURCE_TYPES,
    ItemState,
    SourceStatus,
    SourceType,
)
from mera.domain.value_objects.ids import uuid7
from mera.domain.value_objects.role import Role
from mera.infrastructure.db.errors import constraint_name
from mera.infrastructure.db.models import (
    AccountGroup,
    ContentItem,
    Source,
    SourceGroupLink,
    Workspace,
)
from mera.infrastructure.db.rate_limit import Limit, take
from mera.infrastructure.db.repositories.audit import record
from mera.infrastructure.http.safe_fetch import validate_public_url

MAX_SOURCES_PER_WORKSPACE: Final = 100
FEED_TEST_LIMIT: Final = Limit(capacity=10, per_seconds=60)
INTERVAL_BOUNDS: Final = (900, 86_400)
MAX_KEYWORDS: Final = 50


@dataclass(frozen=True, slots=True)
class SourceView:
    id: uuid.UUID
    source_type: str
    name: str
    url: str | None
    include_keywords: list[str]
    exclude_keywords: list[str]
    language: str | None
    fetch_interval_seconds: int
    weight: float
    status: str
    group_ids: list[uuid.UUID]
    last_fetched_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    next_fetch_at: datetime | None
    items_7d: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source_id: uuid.UUID
    status: str
    last_fetched_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    last_http_status: int | None
    last_yield: int | None
    consecutive_failures: int
    failing_since: datetime | None
    next_fetch_at: datetime | None
    retrying: bool
    items_7d: int
    filtered_7d: int


@dataclass(frozen=True, slots=True)
class SourceInput:
    source_type: SourceType
    name: str
    url: str
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    language: str | None = None
    fetch_interval_seconds: int = 3600
    weight: float = 1.0
    group_ids: list[uuid.UUID] = field(default_factory=list)
    mcp_server_id: uuid.UUID | None = None  # P3-M6.6, for source_type mcp
    mcp_tool: str | None = None
    mcp_arguments: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class SourceChanges:
    name: str | None = None
    url: str | None = None
    include_keywords: list[str] | None = None
    exclude_keywords: list[str] | None = None
    language: str | None = None
    clear_language: bool = False
    fetch_interval_seconds: int | None = None
    weight: float | None = None
    status: SourceStatus | None = None


class SourceService(WorkspaceScopedService):
    def __init__(self, sm: Any, ingestion: Ingestion, *, half_life_hours: float) -> None:
        super().__init__(sm)
        self._ingestion = ingestion
        self._half_life = half_life_hours

    # ------------------------------------------------------------------ reads

    async def list_sources(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        meta: RequestMeta,
        *,
        group_id: uuid.UUID | None = None,
        status: SourceStatus | None = None,
        source_type: SourceType | None = None,
    ) -> list[SourceView]:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id, account_group_id=group_id)
            q = select(Source).order_by(Source.name)
            if status is not None:
                q = q.where(Source.status == status.value)
            if source_type is not None:
                q = q.where(Source.source_type == source_type.value)
            sources = list((await s.execute(q)).scalars())
            links = await _links(s)
            if group_id is not None:  # a source feeds a group if linked to it, or to nothing
                sources = [x for x in sources if not links[x.id] or group_id in links[x.id]]
            sources = [x for x in sources if _source_visible(principal, links[x.id])]
            counts = await _items_7d(s, [x.id for x in sources])
            full = _sees_full_url(principal)
            return [_view(x, links[x.id], counts.get(x.id, 0), full_url=full) for x in sources]

    async def get(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        meta: RequestMeta,
    ) -> SourceView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            src = await _get(s, source_id)
            links = await _links(s, source_id)
            if not _source_visible(principal, links[source_id]):
                raise NotFoundError("Source not found.")
            counts = await _items_7d(s, [source_id])
            return _view(
                src, links[source_id], counts.get(source_id, 0), full_url=_sees_full_url(principal)
            )

    async def health(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        meta: RequestMeta,
    ) -> SourceHealth:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            rbac.require(principal, Permission.READ, workspace_id)
            src = await _get(s, source_id)
            if not _source_visible(principal, (await _links(s, source_id))[source_id]):
                raise NotFoundError("Source not found.")
            since = datetime.now(UTC) - timedelta(days=7)
            by_state = dict(
                (
                    await s.execute(
                        select(ContentItem.state, func.count())
                        .where(ContentItem.source_id == source_id, ContentItem.fetched_at >= since)
                        .group_by(ContentItem.state)
                    )
                ).all()
            )
            return SourceHealth(
                source_id=src.id,
                status=src.status,
                last_fetched_at=src.last_fetched_at,
                last_success_at=src.last_success_at,
                last_error=src.last_error,
                last_http_status=src.last_http_status,
                last_yield=src.last_yield,
                consecutive_failures=src.consecutive_failures,
                failing_since=src.failing_since,
                next_fetch_at=src.next_fetch_at,
                retrying=src.status != SourceStatus.PAUSED.value and src.next_fetch_at is not None,
                items_7d=sum(n for st, n in by_state.items() if st != ItemState.FILTERED.value),
                filtered_7d=by_state.get(ItemState.FILTERED.value, 0),
            )

    # ----------------------------------------------------------------- writes

    async def create(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, data: SourceInput
    ) -> SourceView:
        if data.source_type not in AVAILABLE_SOURCE_TYPES:
            raise InvalidInput(
                f"{data.source_type.value} sources are not available yet; use RSS / Atom.",
                code="source_type_unavailable",
                field="source_type",
            )
        is_mcp = data.source_type == SourceType.MCP
        if is_mcp:
            if data.mcp_server_id is None or not data.mcp_tool:
                raise InvalidInput(
                    "Choose an MCP server and one of its read tools.",
                    code="mcp_tool_required",
                    field="mcp_tool",
                )
            canonical = f"mcp://{data.mcp_server_id}/{data.mcp_tool}"
            if data.mcp_arguments:
                digest = hashlib.sha256(
                    json.dumps(data.mcp_arguments, sort_keys=True, default=str).encode()
                ).hexdigest()[:16]
                canonical += f"?{digest}"
            if len(json.dumps(data.mcp_arguments or {}, default=str)) > 4096:
                raise InvalidInput(
                    "The tool arguments are too large.",
                    code="mcp_arguments_too_large",
                    field="mcp_arguments",
                )
            config: dict[str, Any] = {
                "url": canonical,
                "canonical_url": canonical,
                "mcp_server_id": str(data.mcp_server_id),
                "mcp_tool": data.mcp_tool,
                "mcp_arguments": data.mcp_arguments or {},
            }
        else:
            canonical = _checked_url(data.url)
            config = {"url": data.url.strip(), "canonical_url": canonical}
        _check_interval(data.fetch_interval_seconds)
        _check_weight(data.weight)
        include = _keywords(data.include_keywords, "include_keywords")
        exclude = _keywords(data.exclude_keywords, "exclude_keywords")
        group_ids = list(dict.fromkeys(data.group_ids))
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            _require_configure(principal, workspace_id, group_ids)
            await self._writable(s, workspace_id)
            await _check_groups(s, group_ids)
            if is_mcp:
                # Calls a credentialed, Admin-approved tool on the workspace's behalf, with
                # arguments the creator chooses: an MCP admin's decision (review #4).
                rbac.require(principal, Permission.MANAGE_MCP, workspace_id)
                await _check_mcp_tool(s, data.mcp_server_id, data.mcp_tool, group_ids)
            total = await s.scalar(select(func.count()).select_from(Source))
            if (total or 0) >= MAX_SOURCES_PER_WORKSPACE:
                raise ConflictError(
                    f"A workspace can have at most {MAX_SOURCES_PER_WORKSPACE} sources.",
                    code="source_limit_reached",
                )
            await _check_unique_url(s, canonical, exclude_id=None)
            src = Source(
                id=uuid7(),
                workspace_id=workspace_id,
                source_type=data.source_type.value,
                name=data.name.strip(),
                config=config,
                include_keywords=include,
                exclude_keywords=exclude,
                language=_language(data.language),
                fetch_interval_seconds=data.fetch_interval_seconds,
                weight=data.weight,
                status=SourceStatus.ACTIVE.value,
                next_fetch_at=datetime.now(UTC),  # first fetch on the dispatcher's next tick
                created_by=user.user_id,
            )
            s.add(src)
            try:
                await s.flush()
            except IntegrityError as exc:
                if constraint_name(exc) == "uq_source_canonical_url":
                    raise ConflictError(
                        "This workspace already has a source with that URL.",
                        code="duplicate_source",
                        field="url",
                    ) from exc
                raise
            for gid in group_ids:
                s.add(
                    SourceGroupLink(
                        id=uuid7(),
                        workspace_id=workspace_id,
                        source_id=src.id,
                        account_group_id=gid,
                    )
                )
            await s.flush()
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "source.created",
                    workspace_id=workspace_id,
                    subject_type="source",
                    subject_id=src.id,
                    after={
                        "name": src.name,
                        "type": src.source_type,
                        "url": redact_url(data.url),
                        "group_ids": [str(g) for g in group_ids],
                    },
                ),
            )
            await s.refresh(src)
            return _view(src, set(group_ids), 0)

    async def update(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        meta: RequestMeta,
        changes: SourceChanges,
    ) -> SourceView:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            src = await _get(s, source_id, lock=True)
            links = await _links(s, source_id)
            _require_configure(principal, workspace_id, sorted(links[source_id]))
            await self._writable(s, workspace_id)
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}

            def track(key: str, old: Any, new: Any) -> None:
                if old != new:
                    before[key], after[key] = old, new

            if changes.name is not None:
                track("name", src.name, changes.name.strip())
                src.name = changes.name.strip()
            if changes.url is not None and src.source_type == SourceType.MCP.value:
                raise InvalidInput(
                    "An MCP source has no URL; create a new source for another tool.",
                    code="mcp_source_url",
                    field="url",
                )
            if changes.url is not None:
                canonical = _checked_url(changes.url)
                await _check_unique_url(s, canonical, exclude_id=src.id)
                track(
                    "url", redact_url(src.config.get("url") or ""), redact_url(changes.url.strip())
                )
                if canonical != src.config.get("canonical_url"):
                    src.http_etag = None
                    src.http_last_modified = None
                    src.next_fetch_at = datetime.now(UTC)
                src.config = {**src.config, "url": changes.url.strip(), "canonical_url": canonical}
            if changes.include_keywords is not None:
                kw = _keywords(changes.include_keywords, "include_keywords")
                track("include_keywords", list(src.include_keywords), kw)
                src.include_keywords = kw
            if changes.exclude_keywords is not None:
                kw = _keywords(changes.exclude_keywords, "exclude_keywords")
                track("exclude_keywords", list(src.exclude_keywords), kw)
                src.exclude_keywords = kw
            if changes.clear_language or changes.language is not None:
                lang = None if changes.clear_language else _language(changes.language)
                track("language", src.language, lang)
                src.language = lang
            if changes.fetch_interval_seconds is not None:
                _check_interval(changes.fetch_interval_seconds)
                track(
                    "fetch_interval_seconds",
                    src.fetch_interval_seconds,
                    changes.fetch_interval_seconds,
                )
                src.fetch_interval_seconds = changes.fetch_interval_seconds
            reweighted = False
            if changes.weight is not None:
                _check_weight(changes.weight)
                reweighted = changes.weight != src.weight
                track("weight", src.weight, changes.weight)
                src.weight = changes.weight
            if changes.status is not None and changes.status.value != src.status:
                if changes.status is SourceStatus.FAILING:
                    raise InvalidInput(
                        "A source cannot be set to failing; it can be paused or resumed.",
                        code="invalid_status",
                        field="status",
                    )
                track("status", src.status, changes.status.value)
                if changes.status is SourceStatus.PAUSED:
                    src.status = SourceStatus.PAUSED.value
                else:  # resuming starts clean: fetch now, forget the failure streak
                    src.status = SourceStatus.ACTIVE.value
                    src.consecutive_failures = 0
                    src.failing_since = None
                    src.next_fetch_at = datetime.now(UTC)
            await s.flush()
            if reweighted:
                await scoring.rescore(
                    s,
                    workspace_id,
                    source_id=source_id,
                    since=datetime.now(UTC) - scoring.RESCORE_WINDOW,
                    half_life_hours=self._half_life,
                )
            if after:
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "source.updated",
                        workspace_id=workspace_id,
                        subject_type="source",
                        subject_id=source_id,
                        before=before,
                        after=after,
                    ),
                )
            await s.refresh(src)
            counts = await _items_7d(s, [source_id])
            return _view(src, links[source_id], counts.get(source_id, 0))

    async def delete(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        meta: RequestMeta,
    ) -> None:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            src = await _get(s, source_id, lock=True)
            links = await _links(s, source_id)
            _require_configure(principal, workspace_id, sorted(links[source_id]))
            await self._writable(s, workspace_id)
            # Items stay (their source_id becomes NULL): drafts made from them must not lose
            # their provenance, and "covered by N sources" still counts them.
            await s.delete(src)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "source.deleted",
                    workspace_id=workspace_id,
                    subject_type="source",
                    subject_id=source_id,
                    before={"name": src.name, "url": redact_url(src.config.get("url") or "")},
                ),
            )

    async def set_groups(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        meta: RequestMeta,
        group_ids: Sequence[uuid.UUID],
    ) -> SourceView:
        new = list(dict.fromkeys(group_ids))
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            src = await _get(s, source_id, lock=True)
            links = await _links(s, source_id)
            old = sorted(links[source_id])
            # Both what it fed and what it will feed must be within reach.
            _require_configure(principal, workspace_id, old)
            _require_configure(principal, workspace_id, new)
            await self._writable(s, workspace_id)
            await _check_groups(s, new)
            await s.execute(delete(SourceGroupLink).where(SourceGroupLink.source_id == source_id))
            for gid in new:
                s.add(
                    SourceGroupLink(
                        id=uuid7(),
                        workspace_id=workspace_id,
                        source_id=source_id,
                        account_group_id=gid,
                    )
                )
            await s.flush()
            await scoring.rescore(
                s,
                workspace_id,
                source_id=source_id,
                since=datetime.now(UTC) - scoring.RESCORE_WINDOW,
                half_life_hours=self._half_life,
            )
            if set(old) != set(new):
                await record(
                    s,
                    self._actor_entry(
                        user,
                        meta,
                        "source.groups_changed",
                        workspace_id=workspace_id,
                        subject_type="source",
                        subject_id=source_id,
                        before={"group_ids": [str(g) for g in old]},
                        after={"group_ids": [str(g) for g in new]},
                    ),
                )
            counts = await _items_7d(s, [source_id])
            return _view(src, set(new), counts.get(source_id, 0))

    async def fetch_now(
        self,
        user: AuthenticatedUser,
        workspace_id: uuid.UUID,
        source_id: uuid.UUID,
        meta: RequestMeta,
    ) -> datetime:
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            src = await _get(s, source_id, lock=True)
            links = await _links(s, source_id)
            _require_configure(principal, workspace_id, sorted(links[source_id]))
            await self._writable(s, workspace_id)
            if src.status == SourceStatus.PAUSED.value:
                raise ConflictError("Resume this source before fetching it.", code="source_paused")
            now = datetime.now(UTC)
            src.next_fetch_at = now
            if src.status == SourceStatus.FAILING.value:
                # A manual retry restarts the 48-hour retry window.
                src.failing_since = now
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "source.fetch_requested",
                    workspace_id=workspace_id,
                    subject_type="source",
                    subject_id=source_id,
                ),
            )
            return now

    async def test_feed(
        self, user: AuthenticatedUser, workspace_id: uuid.UUID, meta: RequestMeta, url: str
    ) -> Preview:
        _checked_url(url)
        async with self._tx(user, workspace_id, meta) as s:
            principal = await load_user_principal(s, user, workspace_id)
            scope = principal.member_scope
            # Anyone who could create some source may test one.
            _require_configure(
                principal, workspace_id, sorted(scope.group_ids)[:1] if scope.restricted else []
            )
            await self._writable(s, workspace_id)
            await record(
                s,
                self._actor_entry(
                    user,
                    meta,
                    "source.feed_tested",
                    workspace_id=workspace_id,
                    after={"url": redact_url(url)},
                ),
            )
        # A fetch-and-parse on demand is the costliest thing an Editor can trigger: its own,
        # tighter budget per actor (review H1).
        actor = user.api_key.api_key_id if user.api_key else user.user_id
        decision = await take(self._sm, f"feed_test:{actor}", FEED_TEST_LIMIT)
        if not decision.allowed:
            raise TooManyAttempts(
                "Too many feed tests; try again shortly.", retry_after=decision.retry_after
            )
        return await self._ingestion.preview(url.strip())

    async def _writable(self, s: AsyncSession, workspace_id: uuid.UUID) -> None:
        ws = await s.get(Workspace, workspace_id)
        if ws is None:
            raise NotFoundError("Workspace not found.")
        self._ensure_writable(ws)


def _source_visible(principal: Principal, linked: set[uuid.UUID]) -> bool:
    """A scoped member sees the sources that feed their groups: linked to one of them, or
    workspace-wide (review M3)."""
    scope = principal.member_scope
    return not scope.restricted or not linked or bool(linked & set(scope.group_ids))


def _sees_full_url(principal: Principal) -> bool:
    # Private feed URLs often carry a token in the query string; only people who configure
    # sources see it (review M3).
    return principal.role is not None and principal.role.at_least(Role.EDITOR)


def redact_url(url: str) -> str:
    """scheme://host/path, with any query string replaced by a marker."""
    try:
        parts = urlsplit(url)
        base = urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
    except ValueError:
        return "(invalid URL)"
    return f"{base}?…" if parts.query else base


def _require_configure(
    principal: Principal, workspace_id: uuid.UUID, group_ids: Sequence[uuid.UUID]
) -> None:
    if not group_ids:
        rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id)
        return
    for gid in group_ids:
        rbac.require(principal, Permission.CONFIGURE_CONTENT, workspace_id, account_group_id=gid)


async def _get(s: AsyncSession, source_id: uuid.UUID, *, lock: bool = False) -> Source:
    src = await s.get(Source, source_id, with_for_update=lock)
    if src is None:
        raise NotFoundError("Source not found.")
    return src


async def _links(
    s: AsyncSession, source_id: uuid.UUID | None = None
) -> defaultdict[uuid.UUID, set[uuid.UUID]]:
    q = select(SourceGroupLink.source_id, SourceGroupLink.account_group_id)
    if source_id is not None:
        q = q.where(SourceGroupLink.source_id == source_id)
    out: defaultdict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for sid, gid in (await s.execute(q)).all():
        out[sid].add(gid)
    return out


async def _items_7d(s: AsyncSession, source_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not source_ids:
        return {}
    since = datetime.now(UTC) - timedelta(days=7)
    rows = await s.execute(
        select(ContentItem.source_id, func.count())
        .where(
            ContentItem.source_id.in_(source_ids),
            ContentItem.fetched_at >= since,
            ContentItem.state != ItemState.FILTERED.value,
        )
        .group_by(ContentItem.source_id)
    )
    return {sid: int(n) for sid, n in rows.all() if sid is not None}


async def _check_groups(s: AsyncSession, group_ids: Sequence[uuid.UUID]) -> None:
    if not group_ids:
        return
    found = set(
        (
            await s.execute(select(AccountGroup.id).where(AccountGroup.id.in_(list(group_ids))))
        ).scalars()
    )
    if missing := [g for g in group_ids if g not in found]:
        raise InvalidInput(f"Unknown group {missing[0]}.", code="unknown_group", field="group_ids")


async def _check_unique_url(
    s: AsyncSession, canonical: str, *, exclude_id: uuid.UUID | None
) -> None:
    q = select(Source.id).where(Source.config["canonical_url"].astext == canonical)
    if exclude_id is not None:
        q = q.where(Source.id != exclude_id)
    if await s.scalar(q.limit(1)):
        raise ConflictError(
            "This workspace already has a source with that URL.",
            code="duplicate_source",
            field="url",
        )


async def _check_mcp_tool(
    s: AsyncSession,
    server_id: uuid.UUID | None,
    tool: str | None,
    group_ids: Sequence[uuid.UUID],
) -> None:
    """An MCP source needs an active server and an enabled read tool that runs without a
    person (P3-M6.6); anything that writes can never be a feed."""
    from mera.infrastructure.db.models import McpServer, McpTool

    srv = await s.get(McpServer, server_id)
    t = (
        await s.execute(select(McpTool).where(McpTool.server_id == server_id, McpTool.name == tool))
    ).scalar_one_or_none()
    if srv is None or srv.state != "active":
        raise InvalidInput(
            "That MCP server is not active.", code="mcp_server_inactive", field="mcp_server_id"
        )
    if srv is not None and srv.scope_group_ids and not set(group_ids) <= set(srv.scope_group_ids):
        raise InvalidInput(
            "This MCP server is limited to some groups; feed only those groups.",
            code="mcp_server_scope",
            field="group_ids",
        )
    if (
        t is None
        or not t.enabled
        or t.risk_class != "read"
        or t.approval_policy != "auto"
        or t.presence != "present"
        or (t.flag is not None and not t.flag_acknowledged)
    ):
        raise InvalidInput(
            "Only an enabled read tool that needs no approval can be a source.",
            code="mcp_tool_not_readable",
            field="mcp_tool",
        )


def _checked_url(url: str) -> str:
    url = url.strip()
    if len(url) > 2048:
        raise InvalidInput("The URL is too long.", code="invalid_url", field="url")
    try:
        canonical = canonicalize(url)
    except InvalidUrl as exc:
        raise InvalidInput(
            f"Enter a full http(s) URL ({exc}).", code="invalid_url", field="url"
        ) from exc
    if problem := validate_public_url(url):
        raise InvalidInput(
            f"This URL cannot be fetched: {problem}.", code="url_not_allowed", field="url"
        )
    return canonical


def _keywords(raw: Sequence[str], field_name: str) -> list[str]:
    kws = keyword_filter.normalize_keywords(raw)
    if len(kws) > MAX_KEYWORDS or any(len(k) > 60 for k in kws):
        raise InvalidInput(
            f"At most {MAX_KEYWORDS} keywords of up to 60 characters.",
            code="invalid_keywords",
            field=field_name,
        )
    return kws


def _language(code: str | None) -> str | None:
    if code is None or not code.strip():
        return None
    code = code.strip().lower()
    if not (2 <= len(code) <= 3 and code.isalpha()):
        raise InvalidInput(
            "Use a two-letter language code such as en.", code="invalid_language", field="language"
        )
    return code


def _check_interval(seconds: int) -> None:
    lo, hi = INTERVAL_BOUNDS
    if not lo <= seconds <= hi:
        raise InvalidInput(
            "Fetch interval must be between 15 minutes and 24 hours.",
            code="invalid_fetch_interval",
            field="fetch_interval_seconds",
        )


def _check_weight(weight: float) -> None:
    if not 0 <= weight <= 4:
        raise InvalidInput("Weight must be between 0 and 4.", code="invalid_weight", field="weight")


def _view(
    src: Source, group_ids: set[uuid.UUID], items_7d: int, *, full_url: bool = True
) -> SourceView:
    url = src.config.get("url")
    return SourceView(
        id=src.id,
        source_type=src.source_type,
        name=src.name,
        url=url if full_url or not url else redact_url(url),
        include_keywords=list(src.include_keywords),
        exclude_keywords=list(src.exclude_keywords),
        language=src.language,
        fetch_interval_seconds=src.fetch_interval_seconds,
        weight=float(src.weight),
        status=src.status,
        group_ids=sorted(group_ids),
        last_fetched_at=src.last_fetched_at,
        last_success_at=src.last_success_at,
        last_error=src.last_error,
        consecutive_failures=src.consecutive_failures,
        next_fetch_at=src.next_fetch_at,
        items_7d=items_7d,
        created_at=src.created_at,
    )
