"""Media assets: one master, many per-platform renditions (Sprint 7, M5.14; docs/03 §6).

``media_asset`` holds an uploaded image (or a source's OG image) and the renditions Mera
derives from it for each platform's aspect ratio. Renditions are rows, not files conjured
at publish time, so the approver approves the exact image that goes out. A version refers
to media by **checksum** (it is inside the content hash), never only by id.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

from mera.infrastructure.db.rls import enable_rls

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "media_asset",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "workspace_id",
            _UUID,
            sa.ForeignKey("workspace.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("media_type", sa.Text, nullable=False, server_default="image"),
        sa.Column("object_key", sa.Text, nullable=False),
        sa.Column("content_type", sa.Text, nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("byte_size", sa.BigInteger, nullable=False),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("source_url", sa.Text),
        sa.Column("content_item_id", _UUID, sa.ForeignKey("content_item.id", ondelete="SET NULL")),
        sa.Column("parent_asset_id", _UUID, sa.ForeignKey("media_asset.id", ondelete="CASCADE")),
        sa.Column("rendition_key", sa.Text, nullable=False, server_default="master"),
        sa.Column("checksum", sa.LargeBinary, nullable=False),
        sa.Column("default_alt_text", sa.Text),
        sa.Column("uploaded_by_user_id", _UUID, sa.ForeignKey("user.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("media_type IN ('image', 'video')", name="ck_media_asset_type"),
        sa.CheckConstraint(
            "origin IN ('upload', 'og_image', 'generated', 'rendition')",
            name="ck_media_asset_origin",
        ),
        sa.CheckConstraint("octet_length(checksum) = 32", name="ck_media_asset_checksum"),
        sa.CheckConstraint(
            "(parent_asset_id IS NULL) = (rendition_key = 'master')",
            name="ck_media_asset_rendition",
        ),
        sa.CheckConstraint(
            "width > 0 AND height > 0 AND byte_size > 0", name="ck_media_asset_dims"
        ),
        sa.CheckConstraint(
            "default_alt_text IS NULL OR char_length(default_alt_text) <= 1000",
            name="ck_media_asset_alt",
        ),
    )
    # Uploading the same bytes twice returns the existing master (docs/03 §12).
    op.create_index(
        "uq_media_asset_master_checksum",
        "media_asset",
        ["workspace_id", "checksum"],
        unique=True,
        postgresql_where=sa.text("parent_asset_id IS NULL"),
    )
    op.create_index(
        "uq_media_asset_rendition",
        "media_asset",
        ["parent_asset_id", "rendition_key"],
        unique=True,
        postgresql_where=sa.text("parent_asset_id IS NOT NULL"),
    )
    op.create_index("ix_media_asset_ws_created", "media_asset", ["workspace_id", "created_at"])
    op.execute(
        """
        CREATE FUNCTION mera_media_asset_same_workspace() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.parent_asset_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM media_asset p
                WHERE p.id = NEW.parent_asset_id AND p.workspace_id = NEW.workspace_id))
             OR (NEW.content_item_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM content_item c
                WHERE c.id = NEW.content_item_id AND c.workspace_id = NEW.workspace_id)) THEN
            RAISE EXCEPTION 'media_asset crosses workspaces';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER media_asset_same_workspace BEFORE INSERT OR UPDATE ON media_asset "
        "FOR EACH ROW EXECUTE FUNCTION mera_media_asset_same_workspace()"
    )
    # Bytes behind an id never change: the checksum is what an approval binds to, so a
    # swapped object_key or checksum would defeat it. Only the default alt text may change.
    op.execute(
        """
        CREATE FUNCTION mera_media_asset_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.checksum IS DISTINCT FROM OLD.checksum
             OR NEW.object_key IS DISTINCT FROM OLD.object_key
             OR NEW.parent_asset_id IS DISTINCT FROM OLD.parent_asset_id
             OR NEW.rendition_key IS DISTINCT FROM OLD.rendition_key
             OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id THEN
            RAISE EXCEPTION 'media_asset content is immutable';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER media_asset_immutable BEFORE UPDATE ON media_asset "
        "FOR EACH ROW EXECUTE FUNCTION mera_media_asset_immutable()"
    )
    enable_rls("media_asset")


def downgrade() -> None:
    op.drop_table("media_asset")
    op.execute("DROP FUNCTION IF EXISTS mera_media_asset_immutable()")
    op.execute("DROP FUNCTION IF EXISTS mera_media_asset_same_workspace()")
