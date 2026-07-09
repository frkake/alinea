"""Google(Gemini)アダプタ(plans/04 §6.3)。

対象モデル: gemini-3.5-flash / gemini-3.1-pro-preview。出力トークンには思考トークンを
合算する(課金対象)。暗黙キャッシュは自動(cached_content_token_count を計測に反映)。

注: 思考制御は plans/04 が想定する `thinking_level`(新 SDK)ではなく、導入済み
google-genai 1.24.0 の `ThinkingConfig.thinking_budget` にマップする(§6.7 の effort 意図を
保ちつつ導入版 API に整合。deviations 参照)。
"""

from __future__ import annotations

import base64
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from google import genai
from google.genai import errors as gerrors
from google.genai import types as gt

from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.providers._common import base_url_override, system_text
from alinea_llm.structured import attach_parsed
from alinea_llm.types import LLMRequest, LLMResponse, Message, StopReason, StreamEvent, Usage

_FILTER_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}


def _classify(err: Exception, model: str) -> ProviderError:
    if isinstance(err, httpx.TimeoutException):
        return ProviderError(ErrorKind.TIMEOUT, "google", model, str(err))
    if isinstance(err, httpx.ConnectError):
        return ProviderError(ErrorKind.CONNECTION, "google", model, str(err))
    code = getattr(err, "code", None)
    text = str(getattr(err, "message", "") or err).lower()
    kind: ErrorKind
    if code == 429:
        kind = ErrorKind.BILLING if "quota" in text else ErrorKind.RATE_LIMIT
    elif code in (401, 403):
        kind = ErrorKind.AUTH
    elif code == 404:
        kind = ErrorKind.MODEL_NOT_FOUND
    elif code == 400:
        kind = ErrorKind.CONTEXT_LENGTH if "token" in text else ErrorKind.INVALID_REQUEST
    elif isinstance(err, gerrors.ServerError):
        kind = ErrorKind.SERVER
    else:
        kind = ErrorKind.SERVER
    return ProviderError(kind, "google", model, str(err), status_code=code)


def _stop_reason(resp: Any) -> StopReason:
    candidates = resp.candidates or []
    if candidates:
        fr = candidates[0].finish_reason
        name = getattr(fr, "name", str(fr)) if fr is not None else ""
        if name in _FILTER_REASONS:
            return "content_filter"
        if name == "MAX_TOKENS":
            return "max_tokens"
    return "end"


class GoogleProvider:
    name = "google"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        url = base_url or base_url_override("google")
        if url:
            self._client = genai.Client(api_key=api_key, http_options=gt.HttpOptions(base_url=url))
        else:
            self._client = genai.Client(api_key=api_key)

    def _contents(self, messages: list[Message]) -> gt.ContentListUnion:
        result: list[gt.ContentUnion] = []
        for m in messages:
            parts: list[gt.Part] = []
            for p in m.parts:
                if p.type == "image" and p.image_b64:
                    parts.append(
                        gt.Part.from_bytes(
                            data=base64.b64decode(p.image_b64),
                            mime_type=p.image_media_type or "image/png",
                        )
                    )
                else:
                    parts.append(gt.Part.from_text(text=p.text or ""))
            result.append(
                gt.Content(role="model" if m.role == "assistant" else "user", parts=parts)
            )
        return result

    def _config(self, req: LLMRequest) -> gt.GenerateContentConfig:
        cfg = gt.GenerateContentConfig(
            system_instruction=system_text(req),
            max_output_tokens=req.max_output_tokens,
            stop_sequences=req.stop_sequences or None,
            http_options=gt.HttpOptions(timeout=int(req.timeout_s * 1000)),
        )
        if req.effort in ("none", "low"):
            cfg.thinking_config = gt.ThinkingConfig(thinking_budget=0)
        if req.json_schema:
            cfg.response_mime_type = "application/json"
            cfg.response_json_schema = req.json_schema.json_schema
        return cfg

    def _to_response(self, resp: Any, req: LLMRequest, t0: float) -> LLMResponse:
        usage = Usage()
        um = resp.usage_metadata
        if um is not None:
            cached = um.cached_content_token_count or 0
            usage = Usage(
                input_tokens=max(0, (um.prompt_token_count or 0) - cached),
                cached_input_tokens=cached,
                output_tokens=(um.candidates_token_count or 0) + (um.thoughts_token_count or 0),
            )
        return LLMResponse(
            text=resp.text or "",
            usage=usage,
            provider=self.name,
            model=req.model,
            stop_reason=_stop_reason(resp),
            request_id=resp.response_id,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def generate(self, req: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        try:
            resp = await self._client.aio.models.generate_content(
                model=req.model,
                contents=self._contents(req.messages),
                config=self._config(req),
            )
        except gerrors.APIError as e:
            raise _classify(e, req.model) from e
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            raise _classify(e, req.model) from e
        return self._to_response(resp, req, t0)

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        assert req.json_schema is not None
        resp = await self.generate(req)
        return attach_parsed(resp, req.json_schema)

    async def generate_stream(self, req: LLMRequest) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="start")
        agg: list[str] = []
        last: Any = None
        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=req.model,
                contents=self._contents(req.messages),
                config=self._config(req),
            )
            async for chunk in stream:
                last = chunk
                if chunk.text:
                    agg.append(chunk.text)
                    yield StreamEvent(type="text_delta", delta=chunk.text)
        except gerrors.APIError as e:
            err = _classify(e, req.model)
            yield StreamEvent(type="error", error_kind=str(err.kind), error_message=err.message)
            return
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            err = _classify(e, req.model)
            yield StreamEvent(type="error", error_kind=str(err.kind), error_message=err.message)
            return
        text = "".join(agg)
        usage = Usage()
        if last is not None and last.usage_metadata is not None:
            um = last.usage_metadata
            cached = um.cached_content_token_count or 0
            usage = Usage(
                input_tokens=max(0, (um.prompt_token_count or 0) - cached),
                cached_input_tokens=cached,
                output_tokens=(um.candidates_token_count or 0) + (um.thoughts_token_count or 0),
            )
        resp = LLMResponse(text=text, usage=usage, provider=self.name, model=req.model)
        yield StreamEvent(type="usage", usage=usage)
        yield StreamEvent(type="end", response=resp)

    async def count_tokens(self, req: LLMRequest) -> int:
        res = await self._client.aio.models.count_tokens(
            model=req.model, contents=self._contents(req.messages)
        )
        return res.total_tokens or 0
