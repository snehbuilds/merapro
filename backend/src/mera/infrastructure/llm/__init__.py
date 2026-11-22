"""LLM providers (M4.5). ``build_llm`` provides dynamic switching between FakeLLM and LiteLLM."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from mera.domain.ports.llm import Embeddings, LLMProvider, Message, ModelConfig, Structured
from mera.infrastructure.llm.fake import FakeLLM
from mera.infrastructure.llm.litellm_provider import LiteLLMProvider

if TYPE_CHECKING:
    from mera.config import Settings

T = TypeVar("T", bound=BaseModel)


class SmartLLMProvider:
    """Dynamically routes between sandbox FakeLLM and real LiteLLMProvider."""

    def __init__(self, fake: FakeLLM, real: LiteLLMProvider) -> None:
        self._fake = fake
        self._real = real

    async def structured(
        self,
        config: ModelConfig,
        messages: Sequence[Message],
        schema: type[T],
        *,
        llm_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Structured[T]:
        cfg = llm_config or {}
        sandbox_val = cfg.get("sandbox_llm")
        if sandbox_val is None:
            sandbox_val = os.getenv("SANDBOX_LLM", "true").lower() in ("true", "1", "yes")

        has_keys = bool(
            cfg.get("gemini_api_key")
            or cfg.get("openrouter_api_key")
            or cfg.get("openai_api_key")
            or cfg.get("anthropic_api_key")
            or cfg.get("custom_api_key")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")
        )

        has_custom_url = bool(
            cfg.get("custom_base_url")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("LITELLM_PROXY_URL")
        )
        can_call_real = has_keys or has_custom_url

        # Only attempt real LLM calls if keys or custom endpoints are actually available
        if can_call_real and (not bool(sandbox_val) or not config.model.startswith("sandbox/")):
            return await self._real.structured(config, messages, schema, llm_config=cfg)
        return await self._fake.structured(config, messages, schema)

    async def embed(self, model: str, texts: Sequence[str]) -> Embeddings:
        try:
            if os.getenv("OPENAI_API_KEY"):
                return await self._real.embed(model, texts)
        except Exception:
            pass
        return await self._fake.embed(model, texts)


def build_llm(settings: Settings) -> LLMProvider:
    return SmartLLMProvider(FakeLLM(), LiteLLMProvider())
