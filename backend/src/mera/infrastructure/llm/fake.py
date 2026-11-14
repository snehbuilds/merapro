"""``FakeLLM``: the sandbox provider (M4.5, docs/06 §7, SANDBOX_LLM).

Deterministic and offline, so tests and local development need no keys and every run
produces the same output:

- **Embeddings** are hashed bags of words (lower-cased, stop words dropped, a crude plural
  strip, sub-linear term frequency), L2-normalised to ``EMBEDDING_DIM``. Texts that share
  vocabulary really are closer, so dedupe and pillar scoring behave plausibly.
- **Structured output** is produced by a responder registered for the schema's class name.
  The Research agent's responder summarises with the article's first sentences, tags its
  most frequent words, and derives risk flags from obvious markers.

Cost is always zero with ``price_source = "sandbox"``.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any, Final, TypeVar

from pydantic import BaseModel, ValidationError

from mera.domain.ports.llm import Embeddings, LLMError, Message, ModelConfig, Structured, Usage
from mera.domain.value_objects.content import EMBEDDING_DIM

T = TypeVar("T", bound=BaseModel)
Responder = Callable[[Sequence[Message]], dict[str, Any]]

_WORD = re.compile(r"[a-z][a-z0-9+#-]{2,}")
STOP_WORDS: Final[frozenset[str]] = frozenset(
    """
    about above after again against all also and any are because been before being below
    between both but can could did does doing down during each few for from further had
    has have having her here hers him his how into its itself just more most much must
    new not now off once only other our ours out over own same she should some such than
    that the their theirs them then there these they this those through too under until
    very was were what when where which while who whom why will with would you your
    yours said says one two three its it's into via per may might use used using get
    gets got like make made many well way year years week today yesterday
    """.split()  # noqa: SIM905
)


def tokens(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if w in STOP_WORDS:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def fake_embedding(text: str) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    for word, n in Counter(tokens(text)).items():
        h = hashlib.blake2b(word.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "big") % EMBEDDING_DIM
        sign = 1.0 if h[4] & 1 else -1.0
        vec[idx] += sign * (1.0 + math.log(n))
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


_ARTICLE = re.compile(r"<untrusted_article_content>(.*?)</untrusted_article_content>", re.DOTALL)
_TITLE = re.compile(r"^Title: (.*)$", re.MULTILINE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def research_responder(messages: Sequence[Message]) -> dict[str, Any]:
    text = "\n".join(m.content for m in messages if m.role == "user")
    m = _ARTICLE.search(text)
    block = m.group(1) if m else text
    title_m = _TITLE.search(block)
    title = title_m.group(1).strip() if title_m else ""
    body = "\n".join(
        line for line in block.splitlines() if not line.startswith(("Title: ", "Source: "))
    ).strip()
    sentences = [s.strip() for s in _SENTENCE.split(" ".join(body.split())) if s.strip()]
    summary = " ".join(sentences[:3])[:1200] or title or "No article text was available."
    counts = Counter(tokens(f"{title} {title} {body}"))
    topics = [w for w, _ in counts.most_common(5)]
    lowered = f"{title} {body}".lower()
    flags = []
    if re.search(r"\b(i think|in my opinion|why we|why i)\b", lowered):
        flags.append("opinion")
    if re.search(r"\b(press release|announces partnership|is pleased to announce)\b", lowered):
        flags.append("press_release")
    if re.search(r"\b(subscribe to continue|subscribers only|paywall)\b", lowered):
        flags.append("paywalled")
    engagement = round(min(0.15 + len(set(counts)) / 400, 0.85), 2)
    return {
        "summary": summary,
        "topics": topics,
        "engagement_potential": engagement,
        "engagement_rationale": "Sandbox estimate from vocabulary breadth; not a model judgement.",
        "risk_flags": flags,
    }


class FakeLLM:
    def __init__(self, responders: dict[str, Responder] | None = None) -> None:
        from mera.infrastructure.llm.fake_drafting import RESPONDERS

        self._responders: dict[str, Responder] = {
            "ResearchSummary": research_responder,
            **RESPONDERS,
        }
        self._responders.update(responders or {})
        self.calls: list[tuple[str, str]] = []  # (kind, model), for tests

    def register(self, schema_name: str, responder: Responder) -> None:
        self._responders[schema_name] = responder

    async def structured(
        self, config: ModelConfig, messages: Sequence[Message], schema: type[T], **kwargs: Any
    ) -> Structured[T]:
        self.calls.append(("structured", config.model))
        responder = self._responders.get(schema.__name__)
        prompt = "".join(m.content for m in messages)
        usage = Usage(
            model=f"sandbox/{config.model}",
            tokens_in=_approx_tokens(prompt),
            tokens_out=200,
            cost_usd=Decimal(0),
            latency_ms=0,
            price_source="sandbox",
        )
        if responder is None:
            raise LLMError(
                f"no sandbox responder for {schema.__name__}", kind="invalid_output", usage=usage
            )
        try:
            value = schema.model_validate(responder(messages))
        except ValidationError as exc:
            raise LLMError(str(exc)[:200], kind="invalid_output", usage=usage) from exc
        return Structured(value=value, usage=usage)

    async def embed(self, model: str, texts: Sequence[str]) -> Embeddings:
        self.calls.append(("embed", model))
        return Embeddings(
            [fake_embedding(t) for t in texts],
            Usage(
                model=f"sandbox/{model}",
                tokens_in=sum(_approx_tokens(t) for t in texts),
                tokens_out=0,
                cost_usd=Decimal(0),
                latency_ms=0,
                price_source="sandbox",
            ),
        )
