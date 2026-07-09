"""OpenAI 画像アダプタ(gpt-image-2。plans/04 §6.6)。"""

from __future__ import annotations

import base64
import time
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.providers._common import base_url_override, classify_openai, to_png
from alinea_llm.types import ImageRequest, ImageResult

_QUALITY = {"standard": "medium", "high": "high"}


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        url = base_url or base_url_override("openai")
        self._client = AsyncOpenAI(api_key=api_key, base_url=url, max_retries=0)

    async def generate_image(self, req: ImageRequest) -> ImageResult:
        t0 = time.monotonic()
        kwargs: dict[str, Any] = {
            "model": req.model,
            "prompt": req.prompt,
            "size": req.size,
            "quality": _QUALITY[req.quality],
            "n": 1,
            "timeout": req.timeout_s,
        }
        try:
            resp = await self._client.images.generate(**kwargs)
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            raise classify_openai(e, self.name, req.model) from e
        data = resp.data or []
        if not data or not data[0].b64_json:
            raise ProviderError(ErrorKind.SERVER, self.name, req.model, "no image returned")
        img = base64.b64decode(data[0].b64_json)
        return ImageResult(
            image_bytes=to_png(img),
            provider=self.name,
            model=req.model,
            revised_prompt=data[0].revised_prompt,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
