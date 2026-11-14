"""Inputs the drafting agents share (docs/06 §4.3-4.6)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AccountSummary:
    nickname: str
    handle: str
    account_type: str  # personal, business, page, organization
    platform: str
    platform_name: str


@dataclass(frozen=True, slots=True)
class VoiceSummary:
    name: str
    tone: str | None = None
    target_audience: str | None = None
    cta_style: str | None = None
    emoji_policy: str = "sparing"
    banned_words: tuple[str, ...] = ()
    example_posts: tuple[str, ...] = ()
    learned_preferences: tuple[str, ...] = ()
    about: str | None = None
    story_bank: tuple[str, ...] = ()


NEUTRAL_VOICE = VoiceSummary(
    name="Default (no voice configured)",
    tone="Plain, specific and calm. No hype.",
    target_audience="Professionals who follow this account",
    cta_style="Point to the source; no sales language.",
)


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    max_chars: int
    counts_graphemes: bool
    supports_threads: bool
    conventional_hashtags: tuple[int, int]
    prohibits_duplicate_across_accounts: bool
    link_placement: str = "body"
    hook_chars: int | None = None
    target_chars: tuple[int, int] | None = None
    max_hashtags: int | None = None

    def describe(self) -> str:
        unit = "characters (counted as graphemes)" if self.counts_graphemes else "characters"
        lines = [f"max length: {self.max_chars} {unit}"]
        if self.target_chars:
            lo, hi = self.target_chars
            lines.append(f"target length: {lo}-{hi} characters unless the post earns more")
        if self.hook_chars:
            lines.append(f"the hook must land in the first {self.hook_chars} characters")
        lines.append(f"link placement: {self.link_placement.replace('_', ' ')}")
        lines.append("threads: allowed" if self.supports_threads else "threads: not supported")
        lo, hi = self.conventional_hashtags
        lines.append(f"hashtags: {lo}-{hi} are added separately; do not write any")
        if self.prohibits_duplicate_across_accounts:
            lines.append("near-identical posts from two accounts get both suspended")
        return "\n  ".join(lines)


@dataclass(frozen=True, slots=True)
class SourceMaterial:
    title: str
    url: str | None
    publication: str | None
    summary: str | None
    excerpt: str | None
    topics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Sibling:
    nickname: str
    body: str


@dataclass(frozen=True, slots=True)
class DraftRequest:
    account: AccountSummary
    group_name: str | None
    voice: VoiceSummary
    platform: PlatformSpec
    source: SourceMaterial | None
    topic_brief: str | None
    mode: str  # create, revise, differentiate
    prior_body: str | None = None
    instruction: str | None = None
    siblings: tuple[Sibling, ...] = ()
    recent_openings: tuple[str, ...] = ()
    too_similar_to: Sibling | None = None
    measured_similarity: float | None = None
    attempt: int = 0
    playbook: str | None = None
    playbook_name: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
