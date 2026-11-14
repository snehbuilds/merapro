"""Hashtag attribution (P3-M1.1, docs/02 metrics flow, docs/14).

Daily, per workspace and per connected account: every published post of the last ninety
days, the tags it carried (the version's hashtags and any ``#tag`` in its text, with
registry aliases folded into their canonical tag), and its latest engagement. The
account's rows in ``hashtag_stat`` are replaced in one transaction, so a reader sees
yesterday's numbers or today's, never half of each.

Nothing here decides anything. The Hashtag agent and the Composer read these rows and
must treat a row under ``MIN_HASHTAG_SAMPLES`` posts as "not enough data" (P3-M1.2).
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.scheduling import HISTORY, post_engagement
from mera.domain.context import WorkspaceContext
from mera.domain.policies.hashtag_lift import tag_stats
from mera.domain.value_objects.ids import uuid7
from mera.infrastructure.db.models import (
    Hashtag,
    HashtagStat,
    PostVersion,
    PublishedPost,
    SocialAccount,
    Workspace,
)
from mera.infrastructure.db.session import system_scan_transaction, workspace_transaction

log = structlog.get_logger(__name__)

_BODY_TAG = re.compile(r"(?<!\w)#(\w{1,100})")


def clean(tag: str) -> str | None:
    """The stored form: lower case, no ``#``, one word. None for anything else."""
    t = tag.strip().lstrip("#").strip().lower()
    return t if t and not re.search(r"[\s#,]", t) and len(t) <= 100 else None


def tags_of(version: PostVersion, aliases: dict[str, str]) -> frozenset[str]:
    found = [*(version.hashtags or []), *_BODY_TAG.findall(version.body or "")]
    out = set()
    for raw in found:
        t = clean(raw)
        if t:
            out.add(aliases.get(t, t))
    return frozenset(out)


class HashtagStats:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    async def run(self, now: datetime | None = None) -> dict[str, int]:
        now = now or datetime.now(UTC)
        async with system_scan_transaction(self._sm) as s:
            ws_ids = list(
                (
                    await s.execute(
                        select(Workspace.id).where(
                            Workspace.deleted_at.is_(None), Workspace.archived_at.is_(None)
                        )
                    )
                ).scalars()
            )
        counts: Counter[str] = Counter()
        for ws in ws_ids:
            try:
                counts.update(await self.refresh_workspace(ws, now))
            except Exception as exc:  # one workspace must not stop the others
                log.error(
                    "hashtag_stats.workspace_failed",
                    workspace_id=str(ws),
                    error_type=type(exc).__name__,
                )
                counts["error"] += 1
        log.info("hashtag_stats.refreshed", **counts)
        return dict(counts)

    async def refresh_workspace(self, workspace_id: uuid.UUID, now: datetime) -> Counter[str]:
        """Every account, connected or not: a disconnected account's history still says
        what worked on it, and a reconnect picks the same rows back up."""
        counts: Counter[str] = Counter()
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            accounts = list(
                (await s.execute(select(SocialAccount.id, SocialAccount.platform))).all()
            )
            aliases: dict[str, str] = {}
            for tag, alias_list in (await s.execute(select(Hashtag.tag, Hashtag.aliases))).all():
                for a in alias_list or []:
                    if (c := clean(a)) is not None:
                        aliases[c] = tag
        for account_id, platform in accounts:
            counts["rows"] += await self.refresh_account(
                workspace_id, account_id, str(platform), aliases, now
            )
            counts["accounts"] += 1
        return counts

    async def refresh_account(
        self,
        workspace_id: uuid.UUID,
        account_id: uuid.UUID,
        platform: str,
        aliases: dict[str, str],
        now: datetime,
    ) -> int:
        async with workspace_transaction(self._sm, WorkspaceContext(workspace_id)) as s:
            engagement = await post_engagement(s, account_id, now=now)
            versions: dict[uuid.UUID, PostVersion] = {}
            if engagement:
                rows = (
                    await s.execute(
                        select(PublishedPost.id, PostVersion)
                        .join(PostVersion, PostVersion.id == PublishedPost.post_version_id)
                        .where(PublishedPost.id.in_(list(engagement)))
                    )
                ).all()
                versions = {pid: v for pid, v in rows}
            posts = [
                (tags_of(versions[pid], aliases), e)
                for pid, (_at, e) in engagement.items()
                if pid in versions
            ]
            stats = tag_stats(posts)
            await s.execute(delete(HashtagStat).where(HashtagStat.social_account_id == account_id))
            for st in stats:
                s.add(
                    HashtagStat(
                        id=uuid7(),
                        workspace_id=workspace_id,
                        social_account_id=account_id,
                        tag=st.tag,
                        platform=platform,
                        post_count=st.post_count,
                        avg_engagement=Decimal(str(st.avg_engagement)),
                        lift_vs_account_median=Decimal(str(st.lift)),
                        window_start=now - HISTORY,
                        window_end=now,
                        computed_at=now,
                    )
                )
        return len(stats)
