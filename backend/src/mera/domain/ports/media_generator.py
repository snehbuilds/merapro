"""The ``MediaGenerator`` port (P2-M5.1, docs/06 §4.5, docs/03 §6).

A provider turns a prompt into image bytes. It is called only from the ``media`` queue's
job, never from a request, and only after the cost has been estimated and checked against
the per-request ceiling and the workspace's monthly budget: a request above either is
refused before any provider call.

``estimate`` must be pure (no network): it is what the person sees before they commit,
and what the ceilings are checked against. ``generate`` reports the cost it actually
incurred, which is recorded on the job whether or not the result was usable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    kind: str  # "image" | "video"
    prompt: str
    width: int = 1024
    height: int = 1024
    quality: str = "medium"  # provider-neutral: low | medium | high
    seed: int | None = None
    duration_s: int | None = None  # video only


@dataclass(frozen=True, slots=True)
class Estimate:
    cost_usd: Decimal
    seconds: int


@dataclass(frozen=True, slots=True)
class Generated:
    data: bytes
    content_type: str
    cost_usd: Decimal
    model: str
    revised_prompt: str | None = None
    meta: dict[str, str] = field(default_factory=dict)


class GenerationError(Exception):
    """The provider refused or failed. ``retryable`` is False for refusals (a safety
    block, a bad request): retrying would only spend money again."""

    def __init__(self, message: str, *, retryable: bool, cost_usd: Decimal = Decimal(0)) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.cost_usd = cost_usd


class MediaGenerator(Protocol):
    name: str
    kinds: frozenset[str]

    def estimate(self, req: GenerationRequest) -> Estimate: ...

    async def generate(self, req: GenerationRequest) -> Generated: ...
