"""DeepSeek / xAI 共通基底(OpenAI 互換 Chat Completions。plans/04 §6.4・§6.5)。"""

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

from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.providers._common import classify_openai, system_text, to_chat_messages
from alinea_llm.structured import attach_parsed, structured_with_json_mode
from alinea_llm.tokens import estimate_tokens_o200k
from alinea_llm.types import LLMRequest, LLMResponse, StopReason, StreamEvent, Usage

_FINISH: dict[str, StopReason] = {
    "stop": "end",
    "length": "max_tokens",
    "content_filter": "content_filter",
}


class OpenAICompatProvider:
    name: str = "openai-compat"
    supports_native_json_schema: bool = False

    def __init__(self, api_key: str, base_url: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    def _messages(self, req: LLMRequest) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = [{"role": "system", "content": system_text(req)}]
        msgs += to_chat_messages(req.messages)
        return msgs

    def _extra_body(self, req: LLMRequest) -> dict[str, Any]:
        return {}

    def _format_kwargs(self, req: LLMRequest) -> dict[str, Any]:
        return {}

    def _kwargs(self, req: LLMRequest) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "model": req.model,
            "messages": self._messages(req),
            "max_tokens": req.max_output_tokens,
            "timeout": req.timeout_s,
        }
        if req.stop_sequences:
            kw["stop"] = req.stop_sequences
        kw.update(self._extra_body(req))
        kw.update(self._format_kwargs(req))
        return kw

    def _to_response(self, resp: Any, req: LLMRequest, t0: float) -> LLMResponse:
        choice = resp.choices[0]
        finish = choice.finish_reason
        if finish == "content_filter":
            raise ProviderError(
                ErrorKind.CONTENT_FILTER, self.name, req.model, "output blocked by content filter"
            )
        usage = Usage()
        if resp.usage is not None:
            hit = getattr(resp.usage, "prompt_cache_hit_tokens", None)
            miss = getattr(resp.usage, "prompt_cache_miss_tokens", None)
            if hit is not None and miss is not None:
                usage = Usage(
                    input_tokens=miss,
                    cached_input_tokens=hit,
                    output_tokens=resp.usage.completion_tokens,
                )
            else:
                details = resp.usage.prompt_tokens_details
                cached = details.cached_tokens if details and details.cached_tokens else 0
                usage = Usage(
                    input_tokens=max(0, resp.usage.prompt_tokens - cached),
                    cached_input_tokens=cached,
                    output_tokens=resp.usage.completion_tokens,
                )
        return LLMResponse(
            text=choice.message.content or "",
            usage=usage,
            provider=self.name,
            model=req.model,
            stop_reason=_FINISH.get(finish or "stop", "end"),
            request_id=resp.id,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def generate(self, req: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        try:
            resp = await self._client.chat.completions.create(**self._kwargs(req))
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            raise classify_openai(e, self.name, req.model) from e
        return self._to_response(resp, req, t0)

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        assert req.json_schema is not None
        if self.supports_native_json_schema:
            resp = await self.generate(req)
            return attach_parsed(resp, req.json_schema)
        return await structured_with_json_mode(self, req)

    async def generate_stream(self, req: LLMRequest) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="start")
        agg: list[str] = []
        last_usage = Usage()
        kw = self._kwargs(req)
        kw["stream"] = True
        kw["stream_options"] = {"include_usage": True}
        try:
            stream = await self._client.chat.completions.create(**kw)
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    piece = chunk.choices[0].delta.content
                    agg.append(piece)
                    yield StreamEvent(type="text_delta", delta=piece)
                if chunk.usage is not None:
                    last_usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                    )
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            err = classify_openai(e, self.name, req.model)
            yield StreamEvent(type="error", error_kind=str(err.kind), error_message=err.message)
            return
        text = "".join(agg)
        resp = LLMResponse(text=text, usage=last_usage, provider=self.name, model=req.model)
        yield StreamEvent(type="usage", usage=last_usage)
        yield StreamEvent(type="end", response=resp)

    async def count_tokens(self, req: LLMRequest) -> int:
        return int(estimate_tokens_o200k(req) * 1.1)  # tiktoken 見積り +10% マージン(§14)
