"""Allow guarded remote-media ingestion to persist its source origin.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_media_asset_origin"
_WITH_REMOTE_URL = "origin IN ('upload', 'remote_url', 'og_image', 'generated', 'rendition')"
_WITHOUT_REMOTE_URL = "origin IN ('upload', 'og_image', 'generated', 'rendition')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "media_asset", type_="check")
    op.create_check_constraint(_CONSTRAINT, "media_asset", _WITH_REMOTE_URL)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "media_asset", type_="check")
    op.create_check_constraint(_CONSTRAINT, "media_asset", _WITHOUT_REMOTE_URL)
