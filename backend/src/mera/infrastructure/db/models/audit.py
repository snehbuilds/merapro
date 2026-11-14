"""``audit_log`` (docs/03 §10, docs/09 §10): append-only, partitioned monthly by
``created_at``. ``workspace_id`` is nullable only for user-level events with no workspace
(login success and failure)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, LargeBinary, Text, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mera.infrastructure.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"
    # implicit_returning=False: RETURNING would have to pass the SELECT policy, which
    # workspace-less rows (login events) deliberately fail.
    __table_args__ = (
        {"postgresql_partition_by": "RANGE (created_at)", "implicit_returning": False},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, server_default=text("nextval('audit_log_id_seq')")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now()
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # no FK, see 0003
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_label: Mapped[str | None] = mapped_column(Text)
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str | None] = mapped_column(Text)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    account_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    subject_content_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    request_id: Mapped[str | None] = mapped_column(Text)
