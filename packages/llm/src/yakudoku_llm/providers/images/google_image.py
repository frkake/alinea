"""Google 画像アダプタ(gemini-3.1-flash-image / gemini-3-pro-image。plans/04 §6.6)。

generate_content を IMAGE モダリティで呼び、inline_data の PNG を取り出す。

注: サイズ/品質のきめ細かい制御(aspect_ratio / image_size)は plans/04 が想定する
新 SDK の ImageConfig を前提とするが、導入済み google-genai 1.24.0 には未提供のため
v1 では既定サイズで生成する(deviations 参照)。
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from google import genai
from google.genai import errors as gerrors
from google.genai import types as gt

from yakudoku_llm.errors import ErrorKind, ProviderError
from yakudoku_llm.providers._common import base_url_override, to_png
from yakudoku_llm.types import ImageRequest, ImageResult


class GoogleImageProvider:
    name = "google"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        url = base_url or base_url_override("google")
        if url:
            self._client = genai.Client(api_key=api_key, http_options=gt.HttpOptions(base_url=url))
        else:
            self._client = genai.Client(api_key=api_key)

    async def generate_image(self, req: ImageRequest) -> ImageResult:
        t0 = time.monotonic()
        config = gt.GenerateContentConfig(response_modalities=["IMAGE"])
        try:
            resp = await self._client.aio.models.generate_content(
                model=req.model, contents=req.prompt, config=config
            )
        except gerrors.APIError as e:
            raise ProviderError(
                ErrorKind.SERVER, self.name, req.model, str(e), status_code=getattr(e, "code", None)
            ) from e
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise ProviderError(ErrorKind.CONNECTION, self.name, req.model, str(e)) from e
        raw = _extract_image(resp)
        if raw is None:
            raise ProviderError(ErrorKind.SERVER, self.name, req.model, "no image returned")
        return ImageResult(
            image_bytes=to_png(raw),
            provider=self.name,
            model=req.model,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


def _extract_image(resp: Any) -> bytes | None:
    candidates = resp.candidates or []
    for candidate in candidates:
        content = candidate.content
        if content is None:
            continue
        for part in content.parts or []:
            inline = part.inline_data
            if inline is not None and inline.data:
                return bytes(inline.data)
    return None
