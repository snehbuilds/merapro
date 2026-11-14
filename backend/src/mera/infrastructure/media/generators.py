"""Media generators (P2-M5.1): a deterministic sandbox fake and an OpenAI-compatible
images provider, chosen by configuration (``MEDIA_PROVIDER``).

The fake draws a gradient with the prompt's first words on it, seeded by the prompt, so a
regenerate gives a visibly different image and tests are reproducible. It costs what the
real provider would, so the estimate, ceilings and spend views behave the same in sandbox.

Prices are per image and configurable, because they change; the defaults follow the
provider's published per-image prices for a square image at each quality (re-verify on
change). The job records the provider-reported cost when there is one, else the estimate.
"""

from __future__ import annotations

import base64
import hashlib
import io
from decimal import ROUND_UP, Decimal
from typing import Any, Final

import httpx

from mera.domain.ports.media_generator import (
    Estimate,
    Generated,
    GenerationError,
    GenerationRequest,
    MediaGenerator,
)

DEFAULT_PRICES: Final[dict[str, Decimal]] = {
    "low": Decimal("0.011"),
    "medium": Decimal("0.042"),
    "high": Decimal("0.167"),
}
SIZES: Final = ((1024, 1024), (1536, 1024), (1024, 1536))


def _price(prices: dict[str, Decimal], req: GenerationRequest) -> Decimal:
    base = prices.get(req.quality, prices["medium"])
    # Non-square sizes cost about half again as much at every quality.
    # An estimate never rounds down: the ceiling is checked against it.
    return (
        base
        if req.width == req.height
        else (base * Decimal("1.5")).quantize(Decimal("0.001"), rounding=ROUND_UP)
    )


def nearest_size(width: int, height: int) -> tuple[int, int]:
    ratio = width / max(1, height)
    return min(SIZES, key=lambda s: abs(s[0] / s[1] - ratio))


class FakeMediaGenerator:
    """Sandbox images: no network, deterministic per prompt and seed."""

    name = "fake"
    kinds = frozenset({"image"})

    def __init__(self, prices: dict[str, Decimal] | None = None) -> None:
        self._prices = prices or DEFAULT_PRICES
        self.calls: list[GenerationRequest] = []

    def estimate(self, req: GenerationRequest) -> Estimate:
        return Estimate(_price(self._prices, req), 2)

    async def generate(self, req: GenerationRequest) -> Generated:
        from PIL import Image, ImageDraw

        self.calls.append(req)
        if "[[refuse]]" in req.prompt:  # lets tests exercise a provider refusal
            raise GenerationError("The provider refused this prompt.", retryable=False)
        digest = hashlib.sha256(f"{req.prompt}|{req.seed}".encode()).digest()
        a, b = digest[:3], digest[3:6]
        w, h = nearest_size(req.width, req.height)
        img = Image.new("RGB", (w, h))
        draw = ImageDraw.Draw(img)
        for y in range(h):
            t = y / max(1, h - 1)
            draw.line(
                [(0, y), (w, y)], fill=tuple(round(a[i] * (1 - t) + b[i] * t) for i in range(3))
            )
        words = " ".join(req.prompt.split()[:6])
        draw.rectangle([(40, h - 120), (w - 40, h - 40)], fill=(255, 255, 255))
        draw.text((60, h - 95), f"Sandbox: {words}"[:80], fill=(20, 20, 20))
        out = io.BytesIO()
        img.save(out, "PNG")
        return Generated(
            data=out.getvalue(),
            content_type="image/png",
            cost_usd=_price(self._prices, req),
            model="fake-image-1",
        )


class OpenAIImagesGenerator:
    """``POST {base}/images/generations`` with ``b64_json``; works with OpenAI and with
    compatible gateways. One call per image; the job's timeout bounds it."""

    name = "openai"
    kinds = frozenset({"image"})

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-image-1",
        base_url: str = "https://api.openai.com/v1",
        prices: dict[str, Decimal] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key
        self._model = model
        self._url = base_url.rstrip("/") + "/images/generations"
        self._prices = prices or DEFAULT_PRICES
        self._t = transport

    def estimate(self, req: GenerationRequest) -> Estimate:
        return Estimate(_price(self._prices, req), 30 if req.quality == "high" else 15)

    async def generate(self, req: GenerationRequest) -> Generated:
        w, h = nearest_size(req.width, req.height)
        body = {
            "model": self._model,
            "prompt": req.prompt,
            "n": 1,
            "size": f"{w}x{h}",
            "quality": req.quality,
            "output_format": "png",
        }
        try:
            async with httpx.AsyncClient(timeout=180, transport=self._t) as c:
                resp = await c.post(
                    self._url, json=body, headers={"Authorization": f"Bearer {self._key}"}
                )
        except httpx.HTTPError as exc:
            raise GenerationError(
                f"could not reach the image provider ({type(exc).__name__})", retryable=True
            ) from exc
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            err = (data.get("error") or {}) if isinstance(data, dict) else {}
            code = str(err.get("code") or resp.status_code)
            # 400s (including safety refusals) are final; 429 and 5xx may pass.
            raise GenerationError(
                f"the image provider refused the request ({code})",
                retryable=resp.status_code == 429 or resp.status_code >= 500,
            )
        try:
            item = data["data"][0]
            raw = base64.b64decode(item["b64_json"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GenerationError("the image provider returned no image", retryable=True) from exc
        return Generated(
            data=raw,
            content_type="image/png",
            cost_usd=_price(self._prices, req),
            model=self._model,
            revised_prompt=item.get("revised_prompt"),
        )


def build_media_generator(settings: object) -> MediaGenerator | None:
    """``MEDIA_PROVIDER``: ``fake`` (sandbox default), ``openai``, or ``none``."""
    provider = getattr(settings, "media_provider", "fake")
    prices = {
        "low": Decimal(str(getattr(settings, "media_price_low_usd", "0.011"))),
        "medium": Decimal(str(getattr(settings, "media_price_medium_usd", "0.042"))),
        "high": Decimal(str(getattr(settings, "media_price_high_usd", "0.167"))),
    }
    if provider == "none":
        return None
    if provider == "fake" and not getattr(settings, "sandbox_mode", False):
        return None  # the fake never stands in for a real provider outside sandbox
    if provider == "openai":
        key = getattr(settings, "media_api_key", None)
        if key is None:
            return None
        return OpenAIImagesGenerator(
            api_key=key.get_secret_value(),
            model=getattr(settings, "media_model", "gpt-image-1"),
            base_url=getattr(settings, "media_base_url", "https://api.openai.com/v1"),
            prices=prices,
        )
    return FakeMediaGenerator(prices)


class McpMediaGenerator:
    """Images from an approved external MCP tool (P2-M9.9, docs/05 §2.10), for one workspace.

    The tool goes through the registry's gate like any agent call (the Media agent must be
    bound to it). Generation runs unattended in a job, so the tool must be set to ``auto``:
    a call that would wait for a person fails the job instead of hanging it.
    """

    name = "mcp"
    kinds = frozenset({"image"})

    def __init__(self, registry: Any, workspace_id: Any, tool: str, cost_usd: Decimal) -> None:
        self._registry = registry
        self._ws = workspace_id
        self._tool = tool
        self._cost = cost_usd

    def estimate(self, req: GenerationRequest) -> Estimate:
        return Estimate(self._cost, 30)

    async def generate(self, req: GenerationRequest) -> Generated:
        out = await self._registry.request_call(
            self._ws,
            agent_key="media",
            group_ids=[],
            name=self._tool,
            arguments={"prompt": req.prompt, "width": req.width, "height": req.height},
        )
        if out.state == "pending_approval":
            raise GenerationError(
                "the image tool asks for approval on each call; set it to auto for generation",
                retryable=False,
            )
        if out.state != "succeeded" or out.call_id is None:
            raise GenerationError(f"the image tool failed: {out.text[:200]}", retryable=False)
        image = await self._registry.call_image(self._ws, out.call_id)
        if image is None:
            raise GenerationError("the image tool returned no image", retryable=False)
        data, mime = image
        return Generated(
            data=data, content_type=mime, cost_usd=self._cost, model=f"mcp:{self._tool}"
        )
