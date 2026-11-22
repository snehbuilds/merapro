"""``/api/v1`` router. Resource routers are added per milestone (M2 onward)."""

from __future__ import annotations

from fastapi import APIRouter

from mera.api.v1.routers import (
    ab,
    accounts,
    analytics,
    approvals,
    auth,
    channels,
    content,
    inbox,
    invitations,
    library,
    mcp,
    mcp_servers,
    media,
    media_generation,
    notifications,
    platforms,
    posts,
    privacy,
    publishing,
    workspaces,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(privacy.router)
router.include_router(notifications.router)
router.include_router(ab.router)
router.include_router(workspaces.router)
router.include_router(invitations.router)
router.include_router(accounts.router)
router.include_router(accounts.callbacks)
router.include_router(content.router)
router.include_router(posts.router)
router.include_router(posts.agents_router)
router.include_router(platforms.router)
router.include_router(library.router)
router.include_router(media_generation.router)
router.include_router(inbox.router)
router.include_router(mcp.router)
router.include_router(mcp_servers.router)
router.include_router(media.router)
router.include_router(media.files_router)
router.include_router(approvals.router)
router.include_router(approvals.automation_router)
router.include_router(publishing.router)
router.include_router(channels.router)
router.include_router(channels.webhook_router)
router.include_router(channels.sandbox_router)
router.include_router(analytics.router)
router.include_router(analytics.events_router)
