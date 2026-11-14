"""The LLM port (M4.5, docs/06 §7-8, ADR-0010).

Agents depend on this protocol, never on LiteLLM. Every call returns ``Usage`` and the
caller records it before using the result (docs/06 §8: "Nothing is optional about this
path"). An agent's contract is a Pydantic model; how the provider coaxes a model into it
(native schema mode, or validate-and-retry) is the provider's business.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ModelConfig:
    model: str  # LiteLLM model id, e.g. "anthropic/claude-haiku-4-5"
    fallbacks: tuple[str, ...] = ()
    temperature: float = 0.2
    max_tokens: int = 1024
    timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class Usage:
    model: str  # the model that actually served the call
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    latency_ms: int
    cached_tokens_in: int = 0
    fallback_from: str | None = None
    price_source: str = "pinned"  # "pinned", "litellm" (estimate), or "sandbox"
    attempts: int = 1


@dataclass(frozen=True, slots=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str
    # Images as data: URLs (P2-M1.8), for vision-capable models only.
    images: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Embeddings:
    vectors: list[list[float]]
    usage: Usage


@dataclass(frozen=True, slots=True)
class Structured(Generic[T]):
    value: T
    usage: Usage
    raw: str = field(default="", repr=False)


class LLMError(Exception):
    """A call failed after every model in the chain was tried."""

    def __init__(self, message: str, *, kind: str, usage: Usage | None = None) -> None:
        super().__init__(message)
        self.kind = kind  # "unavailable", "invalid_output", "rejected"
        self.usage = usage  # spend incurred before failing, if any


class LLMProvider(Protocol):
    async def structured(
        self, config: ModelConfig, messages: Sequence[Message], schema: type[T], **kwargs: Any
    ) -> Structured[T]: ...

    async def embed(self, model: str, texts: Sequence[str]) -> Embeddings: ...
