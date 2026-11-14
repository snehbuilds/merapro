"""``assert_reply_sendable`` (P2-M6.4): the last check before a reply leaves, run inside
the send transaction like ``assert_publishable`` for posts.

The reply's identity is the comment's account, which is the published post's account.
All three must agree, and the hash the approver approved covers the account id, the
comment id and the text, so a reply edited after approval, moved to another comment, or
re-pointed at another account (a forged id) no longer matches and is refused.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from mera.application.approvals.core import kill_switch_scope
from mera.infrastructure.db.models import Comment, PublishedPost, Reply, SocialAccount


class NotSendable(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def reply_hash(social_account_id: uuid.UUID, comment_id: uuid.UUID, body: str) -> bytes:
    return hashlib.sha256(f"reply|{social_account_id}|{comment_id}|{body}".encode()).digest()


async def assert_reply_sendable(
    s: AsyncSession, reply: Reply, *, states: tuple[str, ...] = ("approved", "sending")
) -> tuple[Comment, PublishedPost, SocialAccount]:
    if reply.state not in states:
        raise NotSendable("not_approved", "This reply has not been approved.")
    comment = await s.get(Comment, reply.comment_id)
    if comment is None or comment.deleted_at is not None or comment.state == "deleted":
        raise NotSendable("comment_gone", "The comment no longer exists.")
    post = await s.get(PublishedPost, comment.published_post_id)
    if post is None or post.deleted_at is not None:
        raise NotSendable("post_gone", "The post was deleted.")
    # Identity: the reply, its comment and the post must all name one account.
    if not (reply.social_account_id == comment.social_account_id == post.social_account_id):
        raise NotSendable(
            "identity_mismatch", "A reply can only be sent as the account that was commented on."
        )
    expected = reply_hash(reply.social_account_id, reply.comment_id, reply.body)
    if reply.content_hash != expected or reply.approved_hash != expected:
        raise NotSendable("hash_mismatch", "The reply changed after it was approved.")
    account = await s.get(SocialAccount, reply.social_account_id)
    if account is None or account.status != "active" or account.credentials_ct is None:
        raise NotSendable("account_unavailable", "The account is not connected.")
    if await kill_switch_scope(
        s, [account.id], [comment.account_group_id] if comment.account_group_id else []
    ):
        raise NotSendable("kill_switch_engaged", "Automation is stopped for this account.")
    return comment, post, account
