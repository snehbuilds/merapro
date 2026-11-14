"""The engagement side of a platform (P2-M6): read comments on our posts, reply, hide,
delete. Separate from ``PlatformAdapter`` because not every platform offers it to an app
like Mera (X's search needs a paid tier; LinkedIn's comments need Community Management
API approval), and a missing capability must be visible, not a runtime surprise.

Everything a comment says is untrusted. The adapter returns it as data; nothing in a
comment can choose which account replies, which is fixed by the post it is on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class PostRef:
    """Our published post, as the platform knows it."""

    platform_post_id: str
    platform_account_id: str
    extra_ids: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlatformComment:
    platform_comment_id: str
    body: str
    author_platform_id: str | None
    author_handle: str | None
    created_at: datetime | None
    platform_parent_id: str | None = None  # another comment, when it is a reply in a thread
    permalink: str | None = None
    is_own: bool = False  # written by the account itself
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommentRef:
    platform_comment_id: str
    extra: dict[str, Any] = field(default_factory=dict)


class EngagementAdapter(Protocol):
    platform: str
    can_hide: bool
    can_delete: bool

    async def fetch_comments(
        self, credentials: dict[str, Any], post: PostRef, since: datetime | None
    ) -> list[PlatformComment]:
        """Comments newer than ``since`` (all, when None), oldest first."""
        ...

    async def reply(
        self,
        credentials: dict[str, Any],
        post: PostRef,
        comment: CommentRef,
        body: str,
        idempotency_key: str,
    ) -> str:
        """Post ``body`` as a reply to ``comment``, as the account that owns ``post``.
        Returns the platform's id for the reply."""
        ...

    async def hide(
        self, credentials: dict[str, Any], comment: CommentRef, hidden: bool
    ) -> None: ...

    async def delete(self, credentials: dict[str, Any], comment: CommentRef) -> None: ...
