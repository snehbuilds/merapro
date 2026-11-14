"""Sources and content items (M4, docs/03 §5)."""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Literal

# text-embedding-3-small; the column is vector(1536), so a model change is a migration.
EMBEDDING_DIM: Final = 1536


class SourceType(StrEnum):
    RSS = "rss"
    NEWSAPI = "newsapi"
    GNEWS = "gnews"
    GOOGLE_NEWS = "google_news"
    REDDIT = "reddit"
    YOUTUBE = "youtube"
    HACKERNEWS = "hackernews"
    EMAIL = "email"
    MCP = "mcp"


# Only types with a fetcher can be created (docs/12 §4: the rest render "Not yet available").
AVAILABLE_SOURCE_TYPES: Final[frozenset[SourceType]] = frozenset(
    {SourceType.RSS, SourceType.MCP}  # mcp: P3-M6.6
)


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    FAILING = "failing"


class ItemState(StrEnum):
    PENDING = "pending"  # stored, awaiting embedding and summary
    READY = "ready"  # enriched and scored
    DUPLICATE = "duplicate"  # semantic duplicate of another item (duplicate_of set)
    FILTERED = "filtered"  # failed the source's keyword or language filter; kept so it is
    #                        not re-fetched, never shown
    FAILED = "failed"  # enrichment gave up on it


RiskFlag = Literal[
    "paywalled", "opinion", "press_release", "possibly_outdated", "low_credibility_source"
]
RISK_FLAGS: Final[tuple[str, ...]] = (
    "paywalled",
    "opinion",
    "press_release",
    "possibly_outdated",
    "low_credibility_source",
)
