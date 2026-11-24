"""First comments, explicit scheduling metadata and general media assets."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "post_set", sa.Column("publish_mode", sa.Text(), nullable=False, server_default="draft")
    )
    op.add_column(
        "post_set", sa.Column("requested_scheduled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("post_set", sa.Column("requested_timezone", sa.Text(), nullable=True))
    op.add_column("post_version", sa.Column("first_comment", sa.Text(), nullable=True))
    op.add_column("media_asset", sa.Column("original_filename", sa.Text(), nullable=True))
    op.add_column(
        "media_asset", sa.Column("status", sa.Text(), nullable=False, server_default="ready")
    )
    op.create_table(
        "published_comment",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "published_post_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("published_post.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "social_account_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("social_account.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.LargeBinary(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("platform_comment_id", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('queued','publishing','published','failed','dead_lettered')",
            name="ck_published_comment_state",
        ),
    )
    op.create_index("ix_published_comment_due", "published_comment", ["state", "next_attempt_at"])
    op.create_index(
        "ix_published_comment_workspace", "published_comment", ["workspace_id", "created_at"]
    )
    op.execute(
        """
        CREATE FUNCTION mera_published_comment_same_workspace() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM published_post p
            WHERE p.id = NEW.published_post_id AND p.workspace_id = NEW.workspace_id
          ) OR NOT EXISTS (
            SELECT 1 FROM social_account a
            WHERE a.id = NEW.social_account_id AND a.workspace_id = NEW.workspace_id
          ) THEN
            RAISE EXCEPTION 'published_comment crosses workspaces';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER published_comment_same_workspace BEFORE INSERT OR UPDATE "
        "ON published_comment FOR EACH ROW EXECUTE FUNCTION "
        "mera_published_comment_same_workspace()"
    )
    enable_rls("published_comment")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS published_comment_same_workspace ON published_comment")
    op.execute("DROP FUNCTION IF EXISTS mera_published_comment_same_workspace()")
    op.drop_index("ix_published_comment_workspace", table_name="published_comment")
    op.drop_index("ix_published_comment_due", table_name="published_comment")
    op.drop_table("published_comment")
    op.drop_column("media_asset", "status")
    op.drop_column("media_asset", "original_filename")
    op.drop_column("post_version", "first_comment")
    op.drop_column("post_set", "requested_timezone")
    op.drop_column("post_set", "requested_scheduled_at")
    op.drop_column("post_set", "publish_mode")
