"""Media generators, the Media agent's screen and plan validation (P2-M5)."""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from mera.application.agents.media import MediaPlan, screen
from mera.domain.ports.media_generator import GenerationError, GenerationRequest
from mera.infrastructure.media import images
from mera.infrastructure.media.generators import (
    FakeMediaGenerator,
    OpenAIImagesGenerator,
    build_media_generator,
    nearest_size,
)


async def test_fake_is_deterministic_and_a_real_png() -> None:
    gen = FakeMediaGenerator()
    a = await gen.generate(GenerationRequest("image", "a lighthouse", seed=1))
    b = await gen.generate(GenerationRequest("image", "a lighthouse", seed=1))
    c = await gen.generate(GenerationRequest("image", "a lighthouse", seed=2))
    assert a.data == b.data
    assert a.data != c.data
    info = images.probe(a.data)
    assert (info.width, info.height, info.content_type) == (1024, 1024, "image/png")
    assert a.cost_usd == Decimal("0.042")
    with pytest.raises(GenerationError) as exc:
        await gen.generate(GenerationRequest("image", "x [[refuse]]"))
    assert exc.value.retryable is False


def test_prices_and_sizes() -> None:
    gen = FakeMediaGenerator()
    assert gen.estimate(GenerationRequest("image", "p", quality="low")).cost_usd == Decimal("0.011")
    wide = GenerationRequest("image", "p", width=1536, height=1024, quality="high")
    assert gen.estimate(wide).cost_usd == Decimal("0.251")
    assert nearest_size(1080, 1350) == (1024, 1536)
    assert nearest_size(1200, 627) == (1536, 1024)


async def test_openai_provider_request_and_errors() -> None:
    seen: list[dict[str, object]] = []
    png = (await FakeMediaGenerator().generate(GenerationRequest("image", "x"))).data

    def ok(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer k"
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(png).decode(), "revised_prompt": "r"}]},
        )

    gen = OpenAIImagesGenerator(api_key="k", transport=httpx.MockTransport(ok))
    out = await gen.generate(GenerationRequest("image", "a fox", width=1080, height=1350))
    assert out.data == png
    assert out.revised_prompt == "r"
    assert seen[0]["size"] == "1024x1536"
    assert seen[0]["n"] == 1

    def refuse(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": "moderation_blocked"}})

    def busy(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    for handler, retryable in ((refuse, False), (busy, True)):
        g = OpenAIImagesGenerator(api_key="k", transport=httpx.MockTransport(handler))
        with pytest.raises(GenerationError) as exc:
            await g.generate(GenerationRequest("image", "a fox"))
        assert exc.value.retryable is retryable


def test_factory_never_uses_the_fake_outside_sandbox() -> None:
    def conf(**kw: object) -> SimpleNamespace:
        return SimpleNamespace(**{"media_provider": "fake", "sandbox_mode": True, **kw})

    assert isinstance(build_media_generator(conf()), FakeMediaGenerator)
    assert build_media_generator(conf(sandbox_mode=False)) is None
    assert build_media_generator(conf(media_provider="none")) is None
    assert build_media_generator(conf(media_provider="openai", media_api_key=None)) is None
    real = build_media_generator(conf(media_provider="openai", media_api_key=SecretStr("k")))
    assert isinstance(real, OpenAIImagesGenerator)


def test_screen_and_plan_shape() -> None:
    assert screen("A quiet harbour at dawn") is None
    assert screen("NSFW art") is not None
    assert screen("a deepfake of a CEO") is not None
    assert screen("in the style of flat design") is None
    assert screen("  ") is not None
    with pytest.raises(ValidationError):
        MediaPlan(prompts=["a", "b"], alt_texts=["only one"], safe=True)
    plan = MediaPlan(prompts=["  a  "], alt_texts=[" b "], safe=True)
    assert (plan.prompts, plan.alt_texts) == (["a"], ["b"])
