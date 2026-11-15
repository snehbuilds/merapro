"""``LLMProvider`` over LiteLLM (M4.5, ADR-0010, docs/06 §7).

- **Fallbacks** are walked in order on any provider error (rate limit, outage, auth, a
  refused request); the model that served the call is recorded, and ``fallback_from`` names
  the configured primary when another model answered.
- **Structured output**: a JSON-schema ``response_format`` is requested (LiteLLM maps it to
  native schema mode or tool calling per provider, and ``drop_params`` drops it where
  neither exists). Whatever comes back is validated against the agent's Pydantic contract;
  one corrective retry per model appends the validation error.
- **Cost** comes from the pinned price table, never LiteLLM's estimate (docs/06 §8).
- LiteLLM is imported lazily with its remote price-map fetch disabled: importing it must
  not reach the network (and behind a TLS-inspecting proxy that fetch fails slowly).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from types import ModuleType
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from mera.domain.ports.llm import (
    Embeddings,
    LLMError,
    Message,
    ModelConfig,
    Structured,
    Usage,
)
from mera.domain.value_objects.content import EMBEDDING_DIM
from mera.infrastructure.llm import pricing

T = TypeVar("T", bound=BaseModel)
log = structlog.get_logger(__name__)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_MAX_ERROR_CHARS = 800

_litellm: ModuleType | None = None


def litellm_module() -> ModuleType:
    global _litellm
    if _litellm is None:
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        os.environ.setdefault("LITELLM_TELEMETRY", "False")
        import litellm

        litellm.suppress_debug_info = True
        litellm.drop_params = True
        litellm.telemetry = False
        _litellm = litellm
    return _litellm


def _extra_kwargs(model: str, llm_config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = llm_config or {}
    kwargs: dict[str, Any] = {}

    custom_base = cfg.get("custom_api_base") or os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL")
    # If custom_base is a known non-LLM endpoint like tinyfish, ignore it
    if custom_base and "tinyfish" in custom_base:
        custom_base = None

    custom_key = cfg.get("custom_api_key")

    if model.startswith("openrouter/") or (
        not model.startswith(("openai/", "gemini/", "anthropic/", "groq/"))
        and (cfg.get("openrouter_api_key") or os.getenv("OPENROUTER_API_KEY"))
    ):
        key = cfg.get("openrouter_api_key") or os.getenv("OPENROUTER_API_KEY")
        if key:
            kwargs["api_key"] = key
    elif model.startswith("gemini/"):
        key = cfg.get("gemini_api_key") or os.getenv("GEMINI_API_KEY")
        if key:
            kwargs["api_key"] = key
    elif model.startswith("anthropic/"):
        key = cfg.get("anthropic_api_key") or os.getenv("ANTHROPIC_API_KEY")
        if key:
            kwargs["api_key"] = key
    elif model.startswith("openai/"):
        key = cfg.get("openai_api_key") or os.getenv("OPENAI_API_KEY") or custom_key
        if key:
            kwargs["api_key"] = key
        if custom_base:
            kwargs["api_base"] = custom_base
    elif model.startswith("groq/"):
        key = cfg.get("groq_api_key") or os.getenv("GROQ_API_KEY")
        if key:
            kwargs["api_key"] = key
    elif custom_base:
        kwargs["api_base"] = custom_base
        if custom_key or os.getenv("OPENAI_API_KEY"):
            kwargs["api_key"] = custom_key or os.getenv("OPENAI_API_KEY")
        kwargs["custom_llm_provider"] = "openai"
    return kwargs


Completion = Callable[..., Awaitable[Any]]


class LiteLLMProvider:
    def __init__(
        self,
        *,
        acompletion: Completion | None = None,
        aembedding: Completion | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._acompletion = acompletion
        self._aembedding = aembedding
        self._clock = clock

    def _completion_fn(self) -> Completion:
        return self._acompletion or litellm_module().acompletion

    def _embedding_fn(self) -> Completion:
        return self._aembedding or litellm_module().aembedding

    async def structured(
        self,
        config: ModelConfig,
        messages: Sequence[Message],
        schema: type[T],
        *,
        llm_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Structured[T]:
        chain = [config.model, *config.fallbacks]
        spent = _Spend()
        last_error = "no model configured"
        last_kind = "unavailable"
        for raw_model in chain:
            model = raw_model
            if not any(model.startswith(p) for p in ("openai/", "gemini/", "anthropic/", "groq/", "openrouter/", "ollama/")):
                if (llm_config and llm_config.get("openrouter_api_key")) or os.getenv("OPENROUTER_API_KEY"):
                    model = f"openrouter/{model}"

            fmt: dict[str, Any]
            if model.startswith(("openrouter/", "groq/")):
                fmt = {"type": "json_object"}
            else:
                fmt = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": schema.model_json_schema(),
                        "strict": False,
                    },
                }

            convo: list[dict[str, Any]] = [
                {"role": "system", "content": _schema_instruction(schema)},
                *({"role": m.role, "content": _parts(m)} for m in messages),
            ]
            for attempt in (1, 2):
                started = time.perf_counter()
                try:
                    resp = await self._completion_fn()(
                        model=model,
                        messages=convo,
                        temperature=config.temperature,
                        max_tokens=max(config.max_tokens or 1024, 4096),
                        timeout=config.timeout_seconds,
                        response_format=fmt,
                        **_extra_kwargs(model, llm_config),
                    )
                except Exception as exc:  # provider failure: try the next model
                    last_error, last_kind = _describe(exc), "unavailable"
                    log.warning(
                        "llm.call_failed",
                        model=model,
                        error_type=type(exc).__name__,
                        detail=str(exc)[:300],
                    )
                    break
                spent.add(self, model, resp, started, sum(_text_len(m["content"]) for m in convo))
                content = _content(resp)
                try:
                    value = schema.model_validate_json(_strip_fence(content))
                except ValidationError as exc:
                    log.warning(
                        "llm.validation_error",
                        schema=schema.__name__,
                        errors=_errors(exc),
                        content=content[:500],
                    )
                    last_error, last_kind = (
                        f"output did not match {schema.__name__}",
                        "invalid_output",
                    )
                    if attempt == 2:
                        break
                    convo.append({"role": "assistant", "content": content[:4000]})
                    convo.append(
                        {
                            "role": "user",
                            "content": "That reply did not match the required JSON schema: "
                            f"{_errors(exc)}. Reply again with only the corrected JSON object.",
                        }
                    )
                    continue
                usage = spent.usage(
                    served_by=model,
                    fallback_from=config.model if model != config.model else None,
                )
                return Structured(value=value, usage=usage, raw=content)
        raise LLMError(last_error, kind=last_kind, usage=spent.usage_or_none(chain[-1]))

    async def embed(self, model: str, texts: Sequence[str]) -> Embeddings:
        if not texts:
            return Embeddings([], Usage(model, 0, 0, Decimal(0), 0))
        started = time.perf_counter()
        try:
            resp = await self._embedding_fn()(model=model, input=list(texts))
        except Exception as exc:
            raise LLMError(_describe(exc), kind="unavailable") from exc
        latency = int((time.perf_counter() - started) * 1000)
        vectors = [list(map(float, _vector(d))) for d in resp.data]
        tokens_in = int(getattr(resp.usage, "prompt_tokens", 0) or 0)
        usd, source = pricing.cost(model, tokens_in, 0, on=self._clock().date())
        usage = Usage(model, tokens_in, 0, usd, latency, price_source=source)
        if len(vectors) != len(texts) or any(len(v) != EMBEDDING_DIM for v in vectors):
            raise LLMError(
                f"embedding model returned an unexpected shape (want {EMBEDDING_DIM} dims)",
                kind="invalid_output",
                usage=usage,
            )
        return Embeddings(vectors, usage)


class _Spend:
    """Usage summed across attempts and fallbacks: a retry that fails still cost money."""

    def __init__(self) -> None:
        self.tokens_in = 0
        self.tokens_out = 0
        self.cached = 0
        self.cost = Decimal(0)
        self.latency = 0
        self.attempts = 0
        self.sources: set[str] = set()

    def add(
        self,
        provider: LiteLLMProvider,
        model: str,
        resp: Any,
        started: float,
        prompt_chars: int = 0,
    ) -> None:
        u = getattr(resp, "usage", None)
        tin = int(getattr(u, "prompt_tokens", 0) or 0)
        tout = int(getattr(u, "completion_tokens", 0) or 0)
        if not tin and not tout:  # no usage reported: estimate rather than record zero
            tin = max(1, prompt_chars // 4)
            tout = max(1, len(_content(resp)) // 4)
        details = getattr(u, "prompt_tokens_details", None)
        cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        usd, source = pricing.cost(model, tin, tout, on=provider._clock().date(), cached_in=cached)
        self.tokens_in += tin
        self.tokens_out += tout
        self.cached += cached
        self.cost += usd
        self.latency += int((time.perf_counter() - started) * 1000)
        self.attempts += 1
        self.sources.add(source)

    def _source(self) -> str:
        for s in ("default", "litellm"):
            if s in self.sources:
                return s
        return "pinned"

    def usage(self, *, served_by: str, fallback_from: str | None) -> Usage:
        return Usage(
            model=served_by,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            cost_usd=self.cost,
            latency_ms=self.latency,
            cached_tokens_in=self.cached,
            fallback_from=fallback_from,
            price_source=self._source(),
            attempts=self.attempts,
        )

    def usage_or_none(self, model: str) -> Usage | None:
        return self.usage(served_by=model, fallback_from=None) if self.attempts else None


def _schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "Reply with a single JSON object and nothing else. It must validate against this "
        f"JSON schema:\n{json.dumps(schema.model_json_schema(), separators=(',', ':'))}"
    )


def _content(resp: Any) -> str:
    try:
        msg = resp.choices[0].message
    except (AttributeError, IndexError):
        return ""
    content = getattr(msg, "content", None)
    if content:
        return str(content)
    calls = getattr(msg, "tool_calls", None) or []
    if calls:  # tool-calling translation of response_format
        return str(calls[0].function.arguments or "")
    return ""


def _strip_fence(text: str) -> str:
    cleaned = _THINK.sub("", text).strip()
    m = _FENCE.match(cleaned)
    return m.group(1).strip() if m else cleaned


def _errors(exc: ValidationError) -> str:
    parts = [f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in exc.errors()[:8]]
    return "; ".join(parts)[:_MAX_ERROR_CHARS]


def _vector(d: Any) -> list[float]:
    return list(d["embedding"] if isinstance(d, dict) else d.embedding)


def _describe(exc: Exception) -> str:
    # Provider messages can echo prompt content or masked key fragments: keep the class and
    # the HTTP status only (the full text goes to the application log at warning level).
    status = getattr(exc, "status_code", None)
    return f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "")


def _parts(m: Message) -> str | list[dict[str, Any]]:
    """Plain text, or text plus image parts for a vision call (P2-M1.8)."""
    if not m.images:
        return m.content
    return [
        {"type": "text", "text": m.content},
        *({"type": "image_url", "image_url": {"url": url}} for url in m.images),
    ]


def _text_len(content: str | list[dict[str, Any]]) -> int:
    if isinstance(content, str):
        return len(content)
    return sum(len(p.get("text", "")) for p in content)
