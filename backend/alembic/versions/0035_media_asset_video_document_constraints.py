"""Allow zero dimensions for video/document assets and document media_type.

Revision ID: 0035
Revises: 0034
"""

from __future__ import annotations

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_TYPE_CONSTRAINT = "ck_media_asset_type"
_WITH_DOCUMENT = "media_type IN ('image', 'video', 'document')"
_WITHOUT_DOCUMENT = "media_type IN ('image', 'video')"

_DIMS_CONSTRAINT = "ck_media_asset_dims"
_DIMS_V2 = (
    "(media_type = 'image' AND width > 0 AND height > 0 AND byte_size > 0) "
    "OR (media_type IN ('video', 'document') AND width >= 0 AND height >= 0 AND byte_size > 0)"
)
_DIMS_V1 = "width > 0 AND height > 0 AND byte_size > 0"


def upgrade() -> None:
    op.drop_constraint(_TYPE_CONSTRAINT, "media_asset", type_="check")
    op.create_check_constraint(_TYPE_CONSTRAINT, "media_asset", _WITH_DOCUMENT)
    op.drop_constraint(_DIMS_CONSTRAINT, "media_asset", type_="check")
    op.create_check_constraint(_DIMS_CONSTRAINT, "media_asset", _DIMS_V2)


def downgrade() -> None:
    op.drop_constraint(_DIMS_CONSTRAINT, "media_asset", type_="check")
    op.create_check_constraint(_DIMS_CONSTRAINT, "media_asset", _DIMS_V1)
    op.drop_constraint(_TYPE_CONSTRAINT, "media_asset", type_="check")
    op.create_check_constraint(_TYPE_CONSTRAINT, "media_asset", _WITHOUT_DOCUMENT)
