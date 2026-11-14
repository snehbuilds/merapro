"""Platform capability descriptors (docs/08; ADR-0008: platform knowledge lives here, never
in callers). The UI reads these to drive affordances — a greyed Edit button is a
descriptor saying ``supports_edit=False``, never a hardcoded platform check.

Phase 1 publishing order (product decision, Sprint 6): LinkedIn (profiles and Pages),
Facebook Pages, Instagram and Threads first, then X; Bluesky and Mastodon move to Phase 2.

Values are the design-time snapshot from docs/08 (verified 2026-11 against public docs,
not against live APIs). Each adapter re-verifies and dates its own values in M8.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from mera.domain.value_objects.platform import AccountType, Platform

AccountAnalytics = Literal["full", "counts_only", "paid", "none"]
# Where the source link goes when a post is published (M8 places it): in the body, as the
# first comment, as a reply to the post, or nowhere (credit the source by name instead).
LinkPlacement = Literal["body", "first_comment", "reply", "none"]


class MediaAspect(BaseModel):
    """One image shape a platform shows well: ``key`` is the ratio (``4x5``), the size is
    the rendition Mera derives (never upscaled)."""

    model_config = ConfigDict(frozen=True)

    key: str
    width: int
    height: int


def _a(key: str, width: int, height: int) -> MediaAspect:
    return MediaAspect(key=key, width=width, height=height)


LANDSCAPE = _a("16x9", 1600, 900)
SQUARE = _a("1x1", 1080, 1080)
PORTRAIT = _a("4x5", 1080, 1350)
LINK_CARD = _a("1.91x1", 1200, 628)


class PlatformCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    platform: Platform
    display_name: str
    phase: int  # the docs/11 phase in which an adapter ships
    # How an account connects: an OAuth redirect, an OAuth redirect on a server the person
    # names (Mastodon), or a Bluesky app password (P2-M3).
    connect_method: Literal["oauth", "oauth_instance", "app_password"] = "oauth"
    # Posts the platform lets one account publish through its API in 24 hours (P2-M2.9).
    # A conservative default an account can override in ``rate_budget.posts_per_24h``.
    posts_per_24h: int | None = None
    account_types: tuple[AccountType, ...]
    # composition
    max_chars: int
    counts_graphemes: bool = False
    conventional_hashtags: tuple[int, int]
    # A platform-enforced ceiling (Threads rejects a second tag), unlike the convention.
    max_hashtags: int | None = None
    link_placement: LinkPlacement = "body"
    # Characters shown before "… more": the hook has to land inside them.
    hook_chars: int | None = None
    # The length the platform's writing skill aims for by default (not a limit).
    target_chars: tuple[int, int] | None = None
    supports_threads: bool
    requires_media: bool = False
    supports_alt_text: bool = True
    # Images per post (a carousel above one) and the shapes Mera derives; the first aspect
    # is the default rendition.
    max_media: int = 4
    media_aspects: tuple[MediaAspect, ...] = (LANDSCAPE, SQUARE)
    max_image_bytes: int = 5_000_000
    # lifecycle
    supports_native_scheduling: bool = False
    supports_edit: bool
    supports_delete: bool = True
    # engagement
    supports_hide_comments: bool
    # analytics
    account_analytics: AccountAnalytics
    # operational
    posts_per_day_limit: int | None = None
    rate_limit_shared_across_accounts: bool = False
    # multi-account (docs/08 §3.3): staggering is mandatory with two or more accounts on
    # one platform in one group; X additionally suspends for near-duplicate content.
    prohibits_duplicate_across_accounts: bool = False
    requires_stagger_within_platform: bool = True
    notes: tuple[str, ...] = ()


_P = Platform
_T = AccountType

CAPABILITIES: Final[dict[Platform, PlatformCapabilities]] = {
    c.platform: c
    for c in (
        PlatformCapabilities(
            platform=_P.BLUESKY,
            display_name="Bluesky",
            phase=2,
            connect_method="app_password",
            account_types=(_T.PERSONAL,),
            max_chars=300,
            counts_graphemes=True,
            conventional_hashtags=(1, 2),
            max_image_bytes=950_000,
            supports_threads=True,
            supports_edit=False,
            supports_hide_comments=False,
            account_analytics="none",
            notes=("Link facets are computed over UTF-8 byte offsets.",),
        ),
        PlatformCapabilities(
            platform=_P.MASTODON,
            display_name="Mastodon",
            phase=2,
            connect_method="oauth_instance",
            account_types=(_T.PERSONAL,),
            max_chars=500,
            conventional_hashtags=(2, 3),
            supports_threads=True,
            supports_edit=True,
            supports_hide_comments=False,
            account_analytics="none",
            notes=("The character limit is set per instance and read at connect time.",),
        ),
        PlatformCapabilities(
            platform=_P.LINKEDIN_MEMBER,
            max_media=20,
            media_aspects=(LINK_CARD, SQUARE, PORTRAIT),
            max_image_bytes=8_000_000,
            display_name="LinkedIn",
            phase=1,
            account_types=(_T.PERSONAL,),
            max_chars=3000,
            conventional_hashtags=(0, 2),
            link_placement="first_comment",
            hook_chars=210,
            target_chars=(900, 1300),
            supports_threads=False,
            supports_edit=False,
            supports_hide_comments=False,
            account_analytics="counts_only",
            notes=("Personal profiles expose almost no analytics through the API.",),
        ),
        PlatformCapabilities(
            platform=_P.LINKEDIN_ORGANIZATION,
            max_media=20,
            media_aspects=(LINK_CARD, SQUARE, PORTRAIT),
            max_image_bytes=8_000_000,
            display_name="LinkedIn Page",
            phase=1,
            account_types=(_T.ORGANIZATION,),
            max_chars=3000,
            conventional_hashtags=(0, 2),
            link_placement="first_comment",
            hook_chars=210,
            target_chars=(900, 1300),
            supports_threads=False,
            supports_edit=False,
            supports_hide_comments=True,
            account_analytics="full",
            notes=("Requires LinkedIn partner approval.",),
        ),
        PlatformCapabilities(
            platform=_P.X,
            posts_per_24h=100,
            display_name="X",
            phase=1,
            account_types=(_T.PERSONAL, _T.BUSINESS),
            max_chars=280,
            conventional_hashtags=(0, 1),
            link_placement="reply",
            supports_threads=True,
            supports_edit=False,
            supports_hide_comments=False,
            account_analytics="paid",
            rate_limit_shared_across_accounts=True,
            prohibits_duplicate_across_accounts=True,
            notes=(
                "Posting quota is shared by every account on the app's tier.",
                "Near-duplicate content across accounts is enforced by suspension.",
            ),
        ),
        PlatformCapabilities(
            platform=_P.THREADS,
            posts_per_24h=250,
            max_media=20,
            media_aspects=(PORTRAIT, SQUARE),
            max_image_bytes=8_000_000,
            display_name="Threads",
            phase=1,
            account_types=(_T.PERSONAL, _T.BUSINESS),
            max_chars=500,
            conventional_hashtags=(0, 1),
            max_hashtags=1,
            link_placement="reply",
            supports_threads=True,
            supports_edit=False,
            supports_hide_comments=True,
            account_analytics="full",
        ),
        PlatformCapabilities(
            platform=_P.INSTAGRAM,
            posts_per_24h=50,
            max_media=10,
            media_aspects=(PORTRAIT, SQUARE, _a("1.91x1", 1080, 566)),
            max_image_bytes=8_000_000,
            display_name="Instagram",
            phase=1,
            account_types=(_T.BUSINESS,),
            max_chars=2200,
            conventional_hashtags=(3, 5),
            link_placement="none",
            hook_chars=125,
            supports_threads=False,
            requires_media=True,
            supports_edit=True,
            supports_hide_comments=True,
            account_analytics="full",
            posts_per_day_limit=25,
            notes=("Only the caption can be edited after publishing.",),
        ),
        PlatformCapabilities(
            platform=_P.FACEBOOK,
            max_media=10,
            media_aspects=(LINK_CARD, SQUARE, PORTRAIT),
            max_image_bytes=8_000_000,
            display_name="Facebook Page",
            phase=1,
            account_types=(_T.PAGE,),
            max_chars=63206,
            conventional_hashtags=(0, 2),
            link_placement="body",
            target_chars=(0, 80),
            supports_threads=False,
            supports_native_scheduling=True,
            supports_edit=True,
            supports_hide_comments=True,
            account_analytics="full",
        ),
    )
}


def capabilities_for(platform: Platform) -> PlatformCapabilities:
    return CAPABILITIES[platform]
