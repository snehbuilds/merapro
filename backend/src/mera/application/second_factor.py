"""Second factors for high-risk actions (P2-M2.5, docs/09 §5.3).

A guarded action needs a *grant*: proof that the person gave a second factor (their
authenticator code, their approval PIN, or a recovery code) for **this action, in this
workspace, in the last few minutes**. A grant is single use and bound to the user, so it
cannot be replayed for another action, another workspace or by anyone else. The web asks
for it with ``POST /workspaces/{id}/second-factor`` and sends it back in the
``X-Mera-Second-Factor`` header; the bots use ``/pin``.

Every action is on by default; a workspace can switch one off in its settings
(``second_factor: {action: false}``). Engaging a kill switch is deliberately *not* on the
list: stopping is always safe, resuming is a decision. API keys cannot give a second
factor, so a guarded action is refused for them while the action is on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.context import AuthenticatedUser, RequestMeta
from mera.application.errors import Forbidden
from mera.domain.context import WorkspaceContext
from mera.infrastructure.crypto.api_keys import hash_key
from mera.infrastructure.db.models import SecondFactorGrant, SecurityEvent, Workspace
from mera.infrastructure.db.session import workspace_transaction

log = structlog.get_logger(__name__)
GRANT_TTL: Final = timedelta(minutes=5)

# docs/09 §5.3. Media above the cost ceiling (P2-M5) uses video_generate; comment deletion
# (P2-M6) uses comment_delete.
ACTIONS: Final[dict[str, str]] = {
    "kill_switch_resume": "resuming automation after a stop",
    "account_disconnect": "disconnecting a social account",
    "company_page_approve": "approving a post for a company or organization page",
    "owner_remove": "removing an Owner",
    "credentials_rotate": "rotating or revealing credentials",
    "video_generate": "generating video or media above the cost ceiling",
    "comment_delete": "deleting a comment",
    "mcp_server_approve": "approving an external MCP server for agents",
    "mcp_tool_auto": "letting an agent call a write tool without asking",
    "autopilot_enable": "turning on autopilot (posts publish without asking)",
}

# Account types that are a company or organization's voice, not a person's.
COMPANY_ACCOUNT_TYPES: Final = frozenset({"page", "organization", "business"})


class SecondFactorRequired(Forbidden):
    """403 ``second_factor_required``; ``field`` names the action to ask a grant for."""

    code = "second_factor_required"

    def __init__(self, action: str) -> None:
        super().__init__(
            f"Confirm with your authenticator code or approval PIN: {ACTIONS[action]}.",
            code="second_factor_required",
            field=action,
        )
        self.action = action


def required(workspace: Workspace | None, action: str) -> bool:
    """On unless the workspace switched it off."""
    if action not in ACTIONS:
        raise ValueError(f"unknown second-factor action {action!r}")
    settings = (workspace.settings if workspace else None) or {}
    toggles = settings.get("second_factor") or {}
    return bool(toggles.get(action, True))


async def require(
    s: AsyncSession,
    user: AuthenticatedUser,
    workspace_id: uuid.UUID,
    action: str,
    meta: RequestMeta,
    *,
    now: datetime | None = None,
) -> None:
    """Consume a grant for ``action`` inside the guarded transaction, or refuse. Consuming
    in the same transaction as the action means a rolled-back action leaves the grant
    usable, and a committed one never lets it be used again."""
    ws = await s.get(Workspace, workspace_id)
    if not required(ws, action):
        return
    token = (meta.second_factor or "").strip()
    if user.is_api_key or not token:
        raise SecondFactorRequired(action)
    now = now or datetime.now(UTC)
    grant = (
        await s.execute(
            select(SecondFactorGrant)
            .where(SecondFactorGrant.token_hash == hash_key(token))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        grant is None
        or grant.user_id != user.user_id
        or grant.workspace_id != workspace_id
        or grant.action != action
        or grant.used_at is not None
        or grant.expires_at <= now
    ):
        await _record_rejection(s, user, workspace_id, action, meta)
        raise SecondFactorRequired(action)
    grant.used_at = now


async def _record_rejection(
    s: AsyncSession,
    user: AuthenticatedUser,
    workspace_id: uuid.UUID,
    action: str,
    meta: RequestMeta,
) -> None:
    """A refused grant, written in a transaction of its own: the guarded action's is about
    to roll back, and the attempt has to stay on record (release review #11)."""
    try:
        sm = async_sessionmaker(s.bind, expire_on_commit=False)
        async with workspace_transaction(sm, WorkspaceContext(workspace_id)) as own:
            own.add(
                SecurityEvent(
                    user_id=user.user_id,
                    workspace_id=workspace_id,
                    kind="second_factor.rejected",
                    detail={"action": action},
                    ip_address=meta.ip_address,
                )
            )
    except Exception:  # never let the record mask the refusal itself
        log.warning("second_factor.event_failed", action=action, exc_info=True)
