"""Builds the drafting pipeline from settings (API and workers)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.drafting.pipeline import Drafting, DraftingConfig
from mera.config import Settings
from mera.domain.ports.llm import LLMProvider, ModelConfig
from mera.infrastructure.llm import build_llm


def drafting_config(settings: Settings) -> DraftingConfig:
    return DraftingConfig(
        writer=ModelConfig(
            settings.writer_model,
            tuple(settings.writer_fallbacks),
            temperature=0.8,
            max_tokens=1200,
        ),
        editor=ModelConfig(
            settings.editor_model,
            tuple(settings.editor_fallbacks),
            temperature=0.1,
            max_tokens=1500,
        ),
        hashtag=ModelConfig(
            settings.hashtag_model,
            tuple(settings.hashtag_fallbacks),
            temperature=0.2,
            max_tokens=600,
        ),
        alt_text=ModelConfig(
            settings.alt_text_model,
            tuple(settings.alt_text_fallbacks),
            temperature=0.2,
            max_tokens=400,
        ),
        media=ModelConfig(
            settings.media_prompt_model,
            tuple(settings.media_prompt_fallbacks),
            temperature=0.7,
            max_tokens=1500,
        ),
        engagement=ModelConfig(
            settings.engagement_model,
            tuple(settings.engagement_fallbacks),
            temperature=0.3,
            max_tokens=700,
        ),
        embedding_model=settings.embedding_model,
        daily_cost_cap_usd=Decimal(str(settings.drafting_daily_cost_cap_usd)),
        monthly_cost_cap_usd=Decimal(str(settings.workspace_monthly_cost_cap_usd)),
    )


def build_drafting(
    settings: Settings, sm: async_sessionmaker[AsyncSession], llm: LLMProvider | None = None
) -> Drafting:
    return Drafting(sm, llm=llm or build_llm(settings), config=drafting_config(settings))
