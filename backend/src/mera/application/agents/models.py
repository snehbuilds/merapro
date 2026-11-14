"""Per-workspace model choice for each agent (Sprint 8 carry-over, docs/06 §7).

Stored in ``workspace.settings["agent_models"][agent_key] = {"model", "fallbacks"}`` and
applied at the moment of each call, so a change takes effect on the next draft without a
restart. Supports curated presets for Anthropic, OpenAI, Gemini, and OpenRouter, as well
as any valid LiteLLM provider/model string.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Final

from mera.domain.ports.llm import ModelConfig
from mera.infrastructure.llm.pricing import PRICES

EDITABLE_AGENTS: Final = ("research", "writer", "editor", "hashtag")

CURATED_MODELS: Final = (
    # Anthropic
    "anthropic/claude-3-5-sonnet-20241022",
    "anthropic/claude-3-5-haiku-20241022",
    "anthropic/claude-3-opus-20240229",
    "anthropic/claude-sonnet-4-5",
    "anthropic/claude-haiku-4-5",
    # OpenAI
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/gpt-4.5-preview",
    "openai/o3-mini",
    "openai/gpt-5",
    "openai/gpt-5-mini",
    # Google Gemini
    "gemini/gemini-2.0-flash",
    "gemini/gemini-2.0-flash-lite-preview-02-05",
    "gemini/gemini-1.5-pro",
    "gemini/gemini-1.5-flash",
    # OpenRouter
    "openrouter/anthropic/claude-3.5-sonnet",
    "openrouter/openai/gpt-4o",
    "openrouter/openai/gpt-4o-mini",
    "openrouter/deepseek/deepseek-chat",
    "openrouter/deepseek/deepseek-r1",
    "openrouter/meta-llama/llama-3.3-70b-instruct",
    "openrouter/google/gemini-2.0-flash-001",
    # Groq & Local / Ollama
    "groq/llama-3.3-70b-versatile",
    "ollama/llama3.3",
)

CHAT_MODELS: Final = tuple(sorted(set(CURATED_MODELS) | {m for m in PRICES if "embedding" not in m}))
MAX_FALLBACKS: Final = 3


def is_valid_model(model: str) -> bool:
    if model in CHAT_MODELS or model in PRICES:
        return True
    parts = model.strip().split("/", 1)
    return len(parts) == 2 and len(parts[0]) > 0 and len(parts[1]) > 0


def override(base: ModelConfig, settings: dict[str, Any] | None, agent_key: str) -> ModelConfig:
    entry = ((settings or {}).get("agent_models") or {}).get(agent_key) or {}
    model = entry.get("model")
    fallbacks = entry.get("fallbacks")
    out = base
    if isinstance(model, str) and is_valid_model(model):
        out = replace(out, model=model)
    if (
        isinstance(fallbacks, list)
        and len(fallbacks) <= MAX_FALLBACKS
        and all(isinstance(f, str) and is_valid_model(f) for f in fallbacks)
    ):
        out = replace(out, fallbacks=tuple(f for f in fallbacks if f != out.model))
    return out
