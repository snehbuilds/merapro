"""The settings a workspace, group or account may set, and how each level's stored values
become input to ``settings_resolution`` (docs/03 §2.2: jsonb is always governed by a
Pydantic model, never a dumping ground)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from mera.application.errors import InvalidInput
from mera.domain.policies.settings_resolution import Level

_HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class PostingTime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: int = Field(ge=0, le=6, description="0 = Monday")
    time: str = Field(pattern=_HHMM)


class QuietHours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str = Field(pattern=_HHMM)
    end: str = Field(pattern=_HHMM)


class LevelSettings(BaseModel):
    """Overridable at workspace, group and account level. ``None`` means "inherit"."""

    model_config = ConfigDict(extra="forbid")

    daily_post_cap: int | None = Field(default=None, ge=1, le=50)
    # P3-M3: turning it above manual is guarded (mera.application.autopilot.guard_change).
    autopilot_mode: Literal["manual", "assisted", "full"] | None = None
    # Assisted publishes unattended at or above this Editor confidence (strictest: highest).
    assisted_threshold: float | None = Field(default=None, ge=0.5, le=0.99)
    required_approvers: int | None = Field(default=None, ge=1, le=5)
    quiet_hours: QuietHours | None = None


def parse_level_settings(raw: dict[str, Any] | None) -> LevelSettings:
    try:
        return LevelSettings.model_validate(raw or {})
    except ValueError as exc:
        raise InvalidInput("Invalid settings.", code="invalid_settings", field="settings") from exc


def merged(stored: dict[str, Any], patch: LevelSettings) -> dict[str, Any]:
    """Apply a PATCH: keys sent replace stored ones; sending ``null`` clears to inherit."""
    out = dict(stored)
    for key in patch.model_fields_set:
        value = getattr(patch, key)
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value.model_dump() if isinstance(value, BaseModel) else value
    return out


def level_values(
    settings: dict[str, Any] | None,
    *,
    brand_voice_id: uuid.UUID | None = None,
    posting_times: list[dict[str, Any]] | None = None,
    content_pillar_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    values = dict(settings or {})
    values["brand_voice_id"] = str(brand_voice_id) if brand_voice_id else None
    values["posting_times"] = posting_times or None
    values["content_pillar_ids"] = (
        [str(p) for p in content_pillar_ids] if content_pillar_ids else None
    )
    return values


__all__ = [
    "Level",
    "LevelSettings",
    "PostingTime",
    "QuietHours",
    "level_values",
    "merged",
    "parse_level_settings",
]
