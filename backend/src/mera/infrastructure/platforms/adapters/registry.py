"""Adapter registry (M8.1): which ``PlatformAdapter`` publishes for each platform.

Sandbox mode publishes through the fake for every platform, whatever is configured, so a
local stack never posts to a real account by accident. Outside sandbox, every platform
has its real adapter (Bluesky and Mastodon since P2-M3).
"""

from __future__ import annotations

from mera.config import Settings
from mera.domain.ports.platform_adapter import PlatformAdapter
from mera.domain.value_objects.platform import Platform
from mera.infrastructure.platforms.adapters.fake import FakePlatformAdapter


def build_adapters(settings: Settings) -> dict[str, PlatformAdapter]:
    if settings.sandbox_mode:
        return {p.value: FakePlatformAdapter(p.value) for p in Platform}
    from mera.infrastructure.http.safe_fetch import guarded_transport
    from mera.infrastructure.platforms.adapters.bluesky import BlueskyAdapter
    from mera.infrastructure.platforms.adapters.linkedin import LinkedInAdapter
    from mera.infrastructure.platforms.adapters.mastodon import MastodonAdapter
    from mera.infrastructure.platforms.adapters.meta import FacebookPageAdapter, InstagramAdapter
    from mera.infrastructure.platforms.adapters.threads import ThreadsAdapter
    from mera.infrastructure.platforms.adapters.x import XAdapter

    guard = guarded_transport(system_trust_store=settings.http_system_trust_store)

    return {
        Platform.LINKEDIN_MEMBER.value: LinkedInAdapter(Platform.LINKEDIN_MEMBER.value),
        Platform.LINKEDIN_ORGANIZATION.value: LinkedInAdapter(Platform.LINKEDIN_ORGANIZATION.value),
        Platform.FACEBOOK.value: FacebookPageAdapter(),
        Platform.INSTAGRAM.value: InstagramAdapter(),
        Platform.THREADS.value: ThreadsAdapter(),
        Platform.X.value: XAdapter(),
        # User-chosen hosts (a PDS, a Mastodon instance): the guarded transport.
        Platform.BLUESKY.value: BlueskyAdapter(transport=guard),
        Platform.MASTODON.value: MastodonAdapter(transport=guard),
    }
