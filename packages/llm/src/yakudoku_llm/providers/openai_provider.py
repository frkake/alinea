"""OpenAI アダプタ(Responses API に統一。plans/04 §6.1)。

Chat Completions は使わない(structured・ストリーミング・prompt_cache_key を1系統で
扱うため)。リトライは Router が一元管理するため max_retries=0。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

from yakudoku_llm.providers._common import (
    base_url_override,
    classify_openai,
    system_text,
    to_responses_input,
)
from yakudoku_llm.structured import attach_parsed
from yakudoku_llm.tokens import estimate_tokens_o200k
from yakudoku_llm.types import LLMRequest, LLMResponse, StopReason, StreamEvent, Usage

_EFFORT = {"none": "none", "low": "low", "medium": "medium", "high": "high"}


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        url = base_url or base_url_override("openai")
        self._client = AsyncOpenAI(api_key=api_key, base_url=url, max_retries=0)

    def _kwargs(self, req: LLMRequest) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "model": req.model,
            "instructions": system_text(req),
            "input": to_responses_input(req.messages),
            "max_output_tokens": req.max_output_tokens,
            "reasoning": {"effort": _EFFORT[req.effort]},
            "timeout": req.timeout_s,
        }
        # prompt_cache_key は openai==1.93.0 の Responses API signature に無い。
        # raw body に混ぜると API 側の invalid_request になり得るため、機能より成功を優先して省く。
        if req.json_schema:
            kw["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": req.json_schema.name,
                    "schema": req.json_schema.json_schema,
                    "strict": req.json_schema.strict,
                }
            }
        return kw

    async def generate(self, req: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        try:
            resp = await self._client.responses.create(**self._kwargs(req))
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            raise classify_openai(e, self.name, req.model) from e
        usage = Usage()
        if resp.usage is not None:
            cached = resp.usage.input_tokens_details.cached_tokens
            usage = Usage(
                input_tokens=max(0, resp.usage.input_tokens - cached),
                cached_input_tokens=cached,
                output_tokens=resp.usage.output_tokens,
            )
        stop: StopReason = "max_tokens" if resp.status == "incomplete" else "end"
        return LLMResponse(
            text=resp.output_text,
            usage=usage,
            provider=self.name,
            model=req.model,
            stop_reason=stop,
            request_id=resp.id,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        assert req.json_schema is not None
        resp = await self.generate(req)  # text.format=json_schema がネイティブ強制
        return attach_parsed(resp, req.json_schema)

    async def generate_stream(self, req: LLMRequest) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="start")
        agg: list[str] = []
        try:
            async with self._client.responses.stream(**self._kwargs(req)) as stream:
                async for event in stream:
                    if event.type == "response.output_text.delta":
                        delta = getattr(event, "delta", "")
                        agg.append(delta)
                        yield StreamEvent(type="text_delta", delta=delta)
                final = await stream.get_final_response()
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            err = classify_openai(e, self.name, req.model)
            yield StreamEvent(type="error", error_kind=str(err.kind), error_message=err.message)
            return
        usage = Usage()
        if final.usage is not None:
            cached = final.usage.input_tokens_details.cached_tokens
            usage = Usage(
                input_tokens=max(0, final.usage.input_tokens - cached),
                cached_input_tokens=cached,
                output_tokens=final.usage.output_tokens,
            )
        resp = LLMResponse(
            text="".join(agg) or final.output_text,
            usage=usage,
            provider=self.name,
            model=req.model,
            request_id=final.id,
        )
        yield StreamEvent(type="usage", usage=usage)
        yield StreamEvent(type="end", response=resp)

    async def count_tokens(self, req: LLMRequest) -> int:
        return estimate_tokens_o200k(req)  # 公式カウント API なし → tiktoken 見積り(§14)
