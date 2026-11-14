"""Builds the ``Ingestion`` pipeline from settings, for the API and the workers alike."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mera.application.ingest.pipeline import IngestConfig, Ingestion
from mera.config import Settings
from mera.domain.ports.llm import ModelConfig
from mera.infrastructure.http.safe_fetch import SafeFetcher
from mera.infrastructure.llm import build_llm


def ingest_config(settings: Settings) -> IngestConfig:
    return IngestConfig(
        research=ModelConfig(
            model=settings.research_model,
            fallbacks=tuple(settings.research_fallbacks),
            temperature=0.2,
            max_tokens=700,
        ),
        embedding_model=settings.embedding_model,
        daily_cost_cap_usd=Decimal(str(settings.research_daily_cost_cap_usd)),
        monthly_cost_cap_usd=Decimal(str(settings.workspace_monthly_cost_cap_usd)),
        dedupe_similarity=settings.dedupe_similarity,
        half_life_hours=settings.freshness_half_life_hours,
        max_new_items_per_fetch=settings.ingest_max_new_items_per_fetch,
    )


def build_ingestion(settings: Settings, sm: async_sessionmaker[AsyncSession]) -> Ingestion:
    fetcher = SafeFetcher(
        # A generic client name: no contact details in outbound requests.
        user_agent="MeraBot/1.0 (feed reader)",
        system_trust_store=settings.http_system_trust_store,
    )
    return Ingestion(sm, fetcher=fetcher, llm=build_llm(settings), config=ingest_config(settings))
