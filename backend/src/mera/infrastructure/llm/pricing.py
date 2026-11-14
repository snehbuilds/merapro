"""Pinned model prices (docs/06 §8: "Cost is computed at call time from a pinned price table
rather than taken from LiteLLM's own estimate, so a library update cannot silently restate
last month's spend. Prices are versioned with an effective date.").

USD per million tokens. A model missing from the table falls back to LiteLLM's bundled
estimate, and the step records ``price_source = "litellm"`` so the gap is visible; add the
model here to pin it. A model unknown to both is charged ``DEFAULT_PRICE`` (deliberately
high, ``price_source = "default"``): recording zero would let the daily cost cap fail
open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final


@dataclass(frozen=True, slots=True)
class Price:
    effective: date
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_input_per_mtok: Decimal | None = None


def _p(eff: str, i: str, o: str, c: str | None = None) -> Price:
    return Price(date.fromisoformat(eff), Decimal(i), Decimal(o), Decimal(c) if c else None)


# Newest last within each model. Keys are LiteLLM ids as configured (provider prefix kept).
PRICES: Final[dict[str, tuple[Price, ...]]] = {
    # Anthropic
    "anthropic/claude-haiku-4-5": (_p("2025-10-01", "1.00", "5.00", "0.10"),),
    "anthropic/claude-sonnet-4-5": (_p("2025-09-29", "3.00", "15.00", "0.30"),),
    "anthropic/claude-3-5-sonnet-20241022": (_p("2024-10-22", "3.00", "15.00", "0.30"),),
    "anthropic/claude-3-5-haiku-20241022": (_p("2024-10-22", "0.80", "4.00", "0.08"),),
    "anthropic/claude-3-opus-20240229": (_p("2024-02-29", "15.00", "75.00", "1.50"),),
    # OpenAI
    "openai/gpt-5-mini": (_p("2025-08-07", "0.25", "2.00", "0.025"),),
    "openai/gpt-5": (_p("2025-08-07", "1.25", "10.00", "0.125"),),
    "openai/gpt-4o": (_p("2024-05-13", "2.50", "10.00", "1.25"),),
    "openai/gpt-4o-mini": (_p("2024-07-18", "0.15", "0.60", "0.075"),),
    "openai/o3-mini": (_p("2025-01-31", "1.10", "4.40", "0.55"),),
    "openai/text-embedding-3-small": (_p("2024-01-25", "0.02", "0"),),
    "openai/text-embedding-3-large": (_p("2024-01-25", "0.13", "0"),),
    # Google Gemini
    "gemini/gemini-2.0-flash": (_p("2024-12-11", "0.10", "0.40", "0.025"),),
    "gemini/gemini-2.0-flash-lite-preview-02-05": (_p("2025-02-05", "0.075", "0.30", "0.02"),),
    "gemini/gemini-1.5-pro": (_p("2024-02-15", "1.25", "5.00", "0.30"),),
    "gemini/gemini-1.5-flash": (_p("2024-05-14", "0.075", "0.30", "0.02"),),
    # OpenRouter
    "openrouter/anthropic/claude-3.5-sonnet": (_p("2024-10-22", "3.00", "15.00", "0.30"),),
    "openrouter/openai/gpt-4o": (_p("2024-05-13", "2.50", "10.00", "1.25"),),
    "openrouter/openai/gpt-4o-mini": (_p("2024-07-18", "0.15", "0.60", "0.075"),),
    "openrouter/deepseek/deepseek-chat": (_p("2024-12-26", "0.14", "0.28", "0.035"),),
    "openrouter/deepseek/deepseek-r1": (_p("2025-01-20", "0.55", "2.19", "0.14"),),
    "openrouter/meta-llama/llama-3.3-70b-instruct": (_p("2024-12-06", "0.12", "0.30", "0.03"),),
    "openrouter/google/gemini-2.0-flash-001": (_p("2024-12-11", "0.10", "0.40", "0.025"),),
    # Groq
    "groq/llama-3.3-70b-versatile": (_p("2024-12-06", "0.59", "0.79"),),
    # Ollama (local)
    "ollama/llama3.3": (_p("2024-12-06", "0.00", "0.00"),),
}

DEFAULT_PRICE: Final = _p("2000-01-01", "15.00", "75.00")
_MTOK = Decimal(1_000_000)
_CENT_FRACTION = Decimal("0.00000001")


def price_for(model: str, on: date) -> Price | None:
    """Look up by the configured id, falling back to the bare model name (LiteLLM may
    report ``claude-haiku-4-5`` for a call configured as ``anthropic/claude-haiku-4-5``)."""
    history = PRICES.get(model)
    if history is None:
        bare = model.split("/", 1)[-1]
        history = next((h for k, h in PRICES.items() if k.split("/", 1)[-1] == bare), None)
    if not history:
        return None
    applicable = [p for p in history if p.effective <= on]
    return applicable[-1] if applicable else history[0]


def cost(
    model: str, tokens_in: int, tokens_out: int, *, on: date, cached_in: int = 0
) -> tuple[Decimal, str]:
    """Return ``(usd, price_source)``."""
    price = price_for(model, on)
    if price is None:
        return _litellm_estimate(model, tokens_in, tokens_out)
    uncached = max(tokens_in - cached_in, 0)
    cached_rate = (
        price.cached_input_per_mtok
        if price.cached_input_per_mtok is not None
        else price.input_per_mtok
    )
    usd = (
        Decimal(uncached) * price.input_per_mtok
        + Decimal(cached_in) * cached_rate
        + Decimal(tokens_out) * price.output_per_mtok
    ) / _MTOK
    return usd.quantize(_CENT_FRACTION), "pinned"


def _litellm_estimate(model: str, tokens_in: int, tokens_out: int) -> tuple[Decimal, str]:
    try:
        from mera.infrastructure.llm.litellm_provider import litellm_module

        p, c = litellm_module().cost_per_token(
            model=model, prompt_tokens=tokens_in, completion_tokens=tokens_out
        )
        total = Decimal(str(p + c)).quantize(_CENT_FRACTION)
    except Exception:  # unknown to LiteLLM as well
        total = Decimal(0)
    if total > 0:
        return total, "litellm"
    usd = (
        Decimal(tokens_in) * DEFAULT_PRICE.input_per_mtok
        + Decimal(tokens_out) * DEFAULT_PRICE.output_per_mtok
    ) / _MTOK
    return usd.quantize(_CENT_FRACTION), "default"
