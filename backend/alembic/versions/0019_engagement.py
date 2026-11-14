"""Unified inbox and Engagement agent (P2-M6, docs/03 §8, docs/06 §4.10).

- ``published_post.next_comments_at`` / ``comment_cursor_at``: an age-tapered comment
  poll per post (every few minutes when fresh, daily near a month, then never).
- ``comment``: one comment on one of our posts, with its triage. ``social_account_id`` is
  the account that owns the post: the only identity a reply may be sent as.
- ``reply``: a reply draft, bound to its comment's account and to a content hash; it is
  sent only once approved, and only as that account.
- ``engagement_rule``: workspace, group or account rules that triage before the agent.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def _ws() -> sa.Column[object]:
    return sa.Column(
        "workspace_id", _UUID, sa.ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )


def _ts(name: str, *, default: bool = False) -> sa.Column[object]:
    if default:
        return sa.Column(
            name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        )
    return sa.Column(name, sa.DateTime(timezone=True))


def upgrade() -> None:
    op.add_column("published_post", _ts("next_comments_at"))
    op.add_column("published_post", _ts("comment_cursor_at"))
    op.execute(
        "UPDATE published_post SET next_comments_at = now() "
        "WHERE deleted_at IS NULL AND published_at > now() - interval '30 days'"
    )
    op.create_index(
        "ix_published_post_comments_due",
        "published_post",
        ["next_comments_at"],
        postgresql_where=sa.text("next_comments_at IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_table(
        "comment",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column(
            "published_post_id",
            _UUID,
            sa.ForeignKey("published_post.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "social_account_id",
            _UUID,
            sa.ForeignKey("social_account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "account_group_id", _UUID, sa.ForeignKey("account_group.id", ondelete="SET NULL")
        ),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("parent_comment_id", _UUID, sa.ForeignKey("comment.id", ondelete="SET NULL")),
        sa.Column("platform_comment_id", sa.Text, nullable=False),
        sa.Column("platform_parent_id", sa.Text),
        sa.Column("author_platform_id", sa.Text),
        sa.Column("author_handle", sa.Text),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("permalink", sa.Text),
        sa.Column("extra", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("sentiment", sa.Text),
        sa.Column("intent", sa.Text),
        sa.Column("toxicity_score", sa.Numeric(4, 3)),
        sa.Column("spam_score", sa.Numeric(4, 3)),
        sa.Column("language", sa.Text),
        sa.Column("state", sa.Text, nullable=False, server_default="new"),
        sa.Column("escalation_reason", sa.Text),
        sa.Column("matched_rule_id", _UUID),
        sa.Column("assigned_to_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("is_own", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _ts("created_at_platform"),
        _ts("triaged_at"),
        _ts("deleted_at"),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.CheckConstraint(
            "state IN ('new', 'triaged', 'replied', 'hidden', 'ignored', 'escalated', 'deleted')",
            name="ck_comment_state",
        ),
        sa.CheckConstraint(
            "intent IS NULL OR intent IN "
            "('question', 'praise', 'complaint', 'spam', 'toxic', 'other')",
            name="ck_comment_intent",
        ),
        sa.CheckConstraint(
            "sentiment IS NULL OR sentiment IN ('positive', 'neutral', 'negative')",
            name="ck_comment_sentiment",
        ),
        sa.CheckConstraint("char_length(body) <= 20000", name="ck_comment_body"),
        sa.UniqueConstraint(
            "workspace_id", "social_account_id", "platform_comment_id", name="uq_comment_platform"
        ),
    )
    op.create_index("ix_comment_inbox", "comment", ["workspace_id", "state", "created_at"])
    op.create_index("ix_comment_post", "comment", ["published_post_id"])
    enable_rls("comment")

    op.create_table(
        "reply",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column(
            "comment_id", _UUID, sa.ForeignKey("comment.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "social_account_id",
            _UUID,
            sa.ForeignKey("social_account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("content_hash", sa.LargeBinary, nullable=False),
        sa.Column("state", sa.Text, nullable=False, server_default="draft"),
        sa.Column("source", sa.Text, nullable=False, server_default="human"),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("agent_run_id", _UUID, sa.ForeignKey("agent_run.id", ondelete="SET NULL")),
        sa.Column("created_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("approved_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column("approved_hash", sa.LargeBinary),
        _ts("approved_at"),
        sa.Column("platform_reply_id", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        _ts("sent_at"),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.CheckConstraint(
            "state IN ('draft', 'pending_approval', 'approved', 'sending', 'sent', 'failed', "
            "'rejected', 'discarded')",
            name="ck_reply_state",
        ),
        sa.CheckConstraint("source IN ('human', 'agent')", name="ck_reply_source"),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 3000", name="ck_reply_body"),
        sa.CheckConstraint("octet_length(content_hash) = 32", name="ck_reply_hash"),
    )
    op.create_index("ix_reply_comment", "reply", ["comment_id"])
    op.create_index("ix_reply_state", "reply", ["workspace_id", "state"])
    enable_rls("reply")

    op.create_table(
        "engagement_rule",
        sa.Column("id", _UUID, primary_key=True),
        _ws(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("scope_type", sa.Text, nullable=False, server_default="workspace"),
        sa.Column("scope_id", _UUID),
        sa.Column("match", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("reply_template", sa.Text),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.CheckConstraint(
            "scope_type IN ('workspace', 'account_group', 'social_account')",
            name="ck_engagement_rule_scope",
        ),
        sa.CheckConstraint(
            "(scope_type = 'workspace') = (scope_id IS NULL)", name="ck_engagement_rule_scope_id"
        ),
        sa.CheckConstraint(
            "action IN ('draft_reply', 'escalate', 'ignore', 'hide')",
            name="ck_engagement_rule_action",
        ),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="ck_engagement_rule_name"),
    )
    enable_rls("engagement_rule")


def downgrade() -> None:
    op.drop_table("engagement_rule")
    op.drop_table("reply")
    op.drop_table("comment")
    op.drop_index("ix_published_post_comments_due", table_name="published_post")
    op.drop_column("published_post", "comment_cursor_at")
    op.drop_column("published_post", "next_comments_at")
