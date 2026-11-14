"""Platforms and account vocabulary (docs/03 §4, docs/08).

LinkedIn is two adapters with different access rules (docs/08 §1), so it is two platforms
here too: ``linkedin_member`` (Phase 1) and ``linkedin_organization`` (Phase 2, partner
approval). This resolves the enum split noted in docs/12 §1.1 C6.
"""

from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    BLUESKY = "bluesky"
    MASTODON = "mastodon"
    LINKEDIN_MEMBER = "linkedin_member"
    LINKEDIN_ORGANIZATION = "linkedin_organization"
    X = "x"
    THREADS = "threads"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"


class AccountType(StrEnum):
    PERSONAL = "personal"
    PAGE = "page"
    ORGANIZATION = "organization"
    BUSINESS = "business"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    NEEDS_REAUTH = "needs_reauth"
    SUSPENDED = "suspended"
    DISCONNECTED = "disconnected"


class StaggerMode(StrEnum):
    NONE = "none"
    FIXED = "fixed"
    RANDOM_SPREAD = "random_spread"
