"""xAI 画像アダプタ(grok-imagine-image / -quality。plans/04 §6.6)。

quality はモデル切替で表現する: quality="high" のとき grok-imagine-image-quality に置換し、
ImageResult.model に実際に使った ID を記録する。
"""

from __future__ import annotations

import base64
import time
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.providers._common import base_url_override, classify_openai, to_png
from alinea_llm.types import ImageRequest, ImageResult

_DEFAULT_BASE_URL = "https://api.x.ai/v1"


class XAIImageProvider:
    name = "xai"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        url = base_url or base_url_override("xai", _DEFAULT_BASE_URL) or _DEFAULT_BASE_URL
        self._client = AsyncOpenAI(api_key=api_key, base_url=url, max_retries=0)

    async def generate_image(self, req: ImageRequest) -> ImageResult:
        t0 = time.monotonic()
        model = "grok-imagine-image-quality" if req.quality == "high" else "grok-imagine-image"
        kwargs: dict[str, Any] = {
            "model": model,
            "prompt": req.prompt,
            "response_format": "b64_json",
            "n": 1,
            "timeout": req.timeout_s,
        }
        try:
            resp = await self._client.images.generate(**kwargs)
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            raise classify_openai(e, self.name, model) from e
        data = resp.data or []
        if not data or not data[0].b64_json:
            raise ProviderError(ErrorKind.SERVER, self.name, model, "no image returned")
        img = base64.b64decode(data[0].b64_json)
        return ImageResult(
            image_bytes=to_png(img),
            provider=self.name,
            model=model,
            revised_prompt=getattr(data[0], "revised_prompt", None),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
