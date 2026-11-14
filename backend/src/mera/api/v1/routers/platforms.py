"""The platform catalogue and the vendored writing skills (Sprint 7).

One place for what the web app used to copy by hand from ``capabilities.py`` (limits,
hashtag conventions, link placement, phase), plus which vendored skill each drafting step
follows on that platform, pinned to an upstream commit. ``…/skills/{role}`` returns the
text exactly as an agent receives it, so a person can review what shapes the drafts.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from mera.api.deps import CurrentUser
from mera.application.errors import NotFoundError, ServiceUnavailableError
from mera.domain.value_objects.platform import Platform
from mera.infrastructure.platforms.capabilities import CAPABILITIES
from mera.skills import loader as skills

router = APIRouter(tags=["platforms"])

# Which drafting step uses which role (docs/06 agents).
STEP_FOR_ROLE = {
    "repurposer": "Writer, drafting from an article",
    "writer": "Writer, drafting from a topic brief",
    "humanizer": "Editor, platform audit checklist",
    "hashtags": "Hashtag agent",
    "carousel": "Carousel planning (with media)",
    "thread": "Thread building",
}


class SkillRoleOut(BaseModel):
    role: str
    skill: str
    used_by: str
    chars: int = Field(description="Characters an agent receives after cleanup")


class SkillBundleOut(BaseModel):
    name: str
    repo: str
    commit: str
    licence: str
    roles: list[SkillRoleOut]


class MediaAspectOut(BaseModel):
    key: str = Field(description="Ratio, e.g. 4x5")
    width: int
    height: int


class PlatformOut(BaseModel):
    platform: Platform
    display_name: str
    phase: int
    connect_method: Literal["oauth", "oauth_instance", "app_password"] = Field(
        description="oauth (redirect), oauth_instance (ask for the server first), "
        "or app_password (Bluesky)"
    )
    max_chars: int
    counts_graphemes: bool
    conventional_hashtags: tuple[int, int]
    max_hashtags: int | None
    link_placement: Literal["body", "first_comment", "reply", "none"]
    hook_chars: int | None
    target_chars: tuple[int, int] | None
    supports_threads: bool
    requires_media: bool
    max_media: int = Field(description="Images per post; above one is a carousel")
    media_aspects: list[MediaAspectOut] = Field(description="First is the default rendition")
    max_image_bytes: int
    prohibits_duplicate_across_accounts: bool
    skill: SkillBundleOut | None = Field(description="Null where no writing skill is vendored")


class PlatformList(BaseModel):
    data: list[PlatformOut]


class SkillTextOut(BaseModel):
    platform: Platform
    role: str
    skill: str
    repo: str
    commit: str
    text: str = Field(description="Exactly what the agent receives (tooling lines removed)")


def _bundle(platform: Platform) -> SkillBundleOut | None:
    b = skills.bundle_for(platform.value)
    if b is None:
        return None
    roles = []
    for role, skill in b.roles.items():
        rendered = skills.playbook(platform.value, role) or ""
        roles.append(
            SkillRoleOut(
                role=role, skill=skill, used_by=STEP_FOR_ROLE.get(role, role), chars=len(rendered)
            )
        )
    return SkillBundleOut(name=b.name, repo=b.repo, commit=b.commit, licence=b.licence, roles=roles)


@router.get("/platforms", response_model=PlatformList)
async def list_platforms(user: CurrentUser) -> PlatformList:
    """Every platform's limits and conventions, in priority order, with its writing skill."""
    out = []
    try:
        bundles = [_bundle(c.platform) for c in CAPABILITIES.values()]
    except skills.SkillTampered as exc:
        raise ServiceUnavailableError("The platform skills failed their integrity check.") from exc
    by_platform = {c.platform: b for c, b in zip(CAPABILITIES.values(), bundles, strict=True)}
    for c in sorted(CAPABILITIES.values(), key=lambda c: (c.phase, _ORDER.index(c.platform))):
        out.append(
            PlatformOut(
                platform=c.platform,
                display_name=c.display_name,
                phase=c.phase,
                connect_method=c.connect_method,
                max_chars=c.max_chars,
                counts_graphemes=c.counts_graphemes,
                conventional_hashtags=c.conventional_hashtags,
                max_hashtags=c.max_hashtags,
                link_placement=c.link_placement,
                hook_chars=c.hook_chars,
                target_chars=c.target_chars,
                supports_threads=c.supports_threads,
                requires_media=c.requires_media,
                max_media=c.max_media,
                media_aspects=[
                    MediaAspectOut(key=a.key, width=a.width, height=a.height)
                    for a in c.media_aspects
                ],
                max_image_bytes=c.max_image_bytes,
                prohibits_duplicate_across_accounts=c.prohibits_duplicate_across_accounts,
                skill=by_platform[c.platform],
            )
        )
    return PlatformList(data=out)


@router.get("/platforms/{platform}/skills/{role}", response_model=SkillTextOut)
async def platform_skill(platform: Platform, role: str, user: CurrentUser) -> SkillTextOut:
    b = skills.bundle_for(platform.value)
    try:
        text = skills.playbook(platform.value, role) if b else None
    except skills.SkillTampered as exc:
        raise ServiceUnavailableError("The platform skills failed their integrity check.") from exc
    if b is None or text is None or role not in b.roles:
        raise NotFoundError("No such skill for this platform.")
    return SkillTextOut(
        platform=platform, role=role, skill=b.roles[role], repo=b.repo, commit=b.commit, text=text
    )


# Priority order (product decision, Sprint 6).
_ORDER = [
    Platform.LINKEDIN_MEMBER,
    Platform.LINKEDIN_ORGANIZATION,
    Platform.FACEBOOK,
    Platform.INSTAGRAM,
    Platform.THREADS,
    Platform.X,
    Platform.BLUESKY,
    Platform.MASTODON,
]
