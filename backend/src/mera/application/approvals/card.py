"""Channel-neutral card content (docs/07 §3.1-3.2, §8.1): what every approval surface shows.

The web Approval Queue and the bot cards read the same payload, so the two can never
disagree about what is being approved. The renderer for each channel decides only layout.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mera.domain.value_objects.posts import GuardrailOutcome
from mera.infrastructure.db.models import (
    AccountGroup,
    ApprovalRequest,
    ContentItem,
    GuardrailResult,
    PostSet,
    PostVariant,
    Source,
    Workspace,
)


def _jsonable(v: Any) -> Any:
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: _jsonable(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    return v


async def card_extras(s: AsyncSession, view: Any) -> dict[str, Any]:
    """``view`` is an ``ApprovalRequestView``. Returns it as JSON plus the shared blocks."""
    req = await s.get(ApprovalRequest, view.id)
    ps = await s.get(PostSet, view.post_set_id)
    ws = await s.get(Workspace, req.workspace_id) if req else None
    group = (
        await s.get(AccountGroup, req.account_group_id) if req and req.account_group_id else None
    )
    source: dict[str, Any] | None = None
    if ps is not None and ps.content_item_id is not None:
        item = await s.get(ContentItem, ps.content_item_id)
        if item is not None:
            src = await s.get(Source, item.source_id) if item.source_id else None
            source = {
                "title": item.title,
                "url": item.url,
                "source_name": src.name if src else None,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "summary": (item.summary or "")[:400] or None,
            }
    elif ps is not None and ps.topic_brief:
        source = {
            "title": ps.title,
            "url": None,
            "source_name": None,
            "published_at": None,
            "summary": ps.topic_brief[:400],
        }
    # The set-scoped distinctness check (M5.9): the number an approver cannot work out by
    # skimming, and the one whose failure costs an account (docs/07 §3.2).
    distinct = (
        await s.execute(
            select(GuardrailResult)
            .where(
                GuardrailResult.post_set_id == view.post_set_id,
                GuardrailResult.subject_type == "post_set",
                GuardrailResult.guardrail_key == "cross_account_duplicate",
                GuardrailResult.superseded_at.is_(None),
            )
            .order_by(GuardrailResult.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    offsets: dict[str, int] = {}
    scheduled: dict[str, str | None] = {}
    for v in view.variants:
        variant = await s.get(PostVariant, v.post_variant_id)
        offsets[str(v.post_variant_id)] = variant.stagger_offset_seconds if variant else 0
        base = req.scheduled_for if req and req.scheduled_for else None
        at = (
            variant.scheduled_for
            if variant and variant.scheduled_for
            else (base + timedelta(seconds=offsets[str(v.post_variant_id)]) if base else None)
        )
        scheduled[str(v.post_variant_id)] = at.isoformat() if at else None
    span = (max(offsets.values()) - min(offsets.values())) if offsets else 0
    return {
        "request": _jsonable(view),
        "workspace": {"id": str(ws.id), "name": ws.name, "timezone": ws.timezone} if ws else None,
        "group": {"id": str(group.id), "name": group.name} if group else None,
        "source": source,
        "distinct": {
            "outcome": distinct.outcome if distinct else None,
            "max_similarity": distinct.score if distinct else None,
            "ok": distinct is None or distinct.outcome == GuardrailOutcome.PASS.value,
        },
        "stagger_span_seconds": span,
        "scheduled_for": scheduled,
        "requested_publish_at": req.scheduled_for.isoformat()
        if req and req.scheduled_for
        else None,
        "facts_verified_all": all(
            not any(
                f.get("category") == "factual" and f.get("severity") in ("warning", "block")
                for f in v.compliance_flags
            )
            for v in view.variants
        ),
    }
