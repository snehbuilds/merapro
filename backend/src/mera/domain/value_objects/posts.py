"""Post sets, variants, versions (M5, docs/03 §6)."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class PostSetState(StrEnum):
    DRAFTING = "drafting"
    NEEDS_APPROVAL = "needs_approval"  # M6
    APPROVED = "approved"  # M6: every account approved, not yet scheduled
    PARTIALLY_APPROVED = "partially_approved"  # M6
    SCHEDULED = "scheduled"  # M8
    PUBLISHED = "published"  # M8
    PARTIALLY_PUBLISHED = "partially_published"  # M8
    REJECTED = "rejected"  # M6
    BLOCKED = "blocked"
    ARCHIVED = "archived"


class VariantState(StrEnum):
    DRAFTING = "drafting"
    NEEDS_APPROVAL = "needs_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class PostOrigin(StrEnum):
    AGENT = "agent"
    MANUAL = "manual"
    MCP = "mcp"
    BOT = "bot"
    RECYCLED = "recycled"


class GenerationStatus(StrEnum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    FAILED = "failed"


class VoiceScope(StrEnum):
    WORKSPACE = "workspace"
    ACCOUNT_GROUP = "account_group"
    SOCIAL_ACCOUNT = "social_account"


class EmojiPolicy(StrEnum):
    NONE = "none"
    SPARING = "sparing"
    LIBERAL = "liberal"


class GuardrailOutcome(StrEnum):
    PASS = "pass"  # noqa: S105 - a guardrail outcome, not a secret
    WARN = "warn"
    BLOCK = "block"


class WriterMode(StrEnum):
    CREATE = "create"
    REVISE = "revise"
    DIFFERENTIATE = "differentiate"


# docs/06 §5 thresholds.
CROSS_ACCOUNT_BLOCK: Final = 0.90
CROSS_ACCOUNT_WARN: Final = 0.80
DUPLICATE_POST_BLOCK: Final = 0.95
DUPLICATE_POST_WARN: Final = 0.85
# How many times the Writer may retry in differentiate mode before the set is blocked.
DIFFERENTIATE_ATTEMPTS: Final = 2
