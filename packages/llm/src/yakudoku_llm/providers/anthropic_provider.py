"""Anthropic アダプタ(plans/04 §6.2)。

実装上の注意: temperature/top_p/top_k は送信しない(型に存在しない)。JSON 強制は
structured outputs(output_config.format)。thinking は adaptive のみ。大きな
max_output_tokens はストリーミング必須(非ストリームは 10 分制限で切れる)。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import anthropic
from anthropic.types import TextBlock

from yakudoku_llm.errors import ErrorKind, ProviderError
from yakudoku_llm.providers._common import (
    base_url_override,
    to_anthropic_messages,
)
from yakudoku_llm.structured import attach_parsed
from yakudoku_llm.types import LLMRequest, LLMResponse, StopReason, StreamEvent, Usage

_EFFORT = {"none": "low", "low": "low", "medium": "medium", "high": "high"}
_STREAM_THRESHOLD = 16384

_STOP_MAP: dict[str, StopReason] = {
    "end_turn": "end",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
    "refusal": "content_filter",
    "tool_use": "end",
    "pause_turn": "end",
}


def _classify(err: Exception, model: str) -> ProviderError:
    status = getattr(err, "status_code", None)
    text = str(getattr(err, "message", "") or err).lower()
    kind: ErrorKind
    if isinstance(err, anthropic.APITimeoutError):
        kind = ErrorKind.TIMEOUT
    elif isinstance(err, anthropic.APIConnectionError):
        kind = ErrorKind.CONNECTION
    elif isinstance(err, anthropic.RateLimitError):
        kind = ErrorKind.RATE_LIMIT
    elif isinstance(err, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        kind = ErrorKind.AUTH
    elif isinstance(err, anthropic.NotFoundError):
        kind = ErrorKind.MODEL_NOT_FOUND
    elif isinstance(err, anthropic.BadRequestError):
        if "too long" in text or "context" in text:
            kind = ErrorKind.CONTEXT_LENGTH
        elif "billing" in text or "credit" in text:
            kind = ErrorKind.BILLING
        else:
            kind = ErrorKind.INVALID_REQUEST
    elif status == 529:
        kind = ErrorKind.OVERLOADED
    else:
        kind = ErrorKind.SERVER
    return ProviderError(
        kind,
        "anthropic",
        model,
        str(err),
        status_code=status,
        request_id=getattr(err, "request_id", None),
    )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        url = base_url or base_url_override("anthropic")
        self._client = anthropic.AsyncAnthropic(api_key=api_key, base_url=url, max_retries=0)

    def _system_blocks(self, req: LLMRequest) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for p in req.system:
            block: dict[str, Any] = {"type": "text", "text": p.text or ""}
            if p.cache_hint:
                block["cache_control"] = {"type": "ephemeral"}  # §13 のキャッシュ境界
            blocks.append(block)
        return blocks

    def _kwargs(self, req: LLMRequest) -> dict[str, Any]:
        output_config: dict[str, Any] = {"effort": _EFFORT[req.effort]}
        if req.json_schema:
            output_config["format"] = {
                "type": "json_schema",
                "schema": req.json_schema.json_schema,
            }
        kw: dict[str, Any] = {
            "model": req.model,
            "system": self._system_blocks(req),
            "messages": to_anthropic_messages(req.messages),
            "max_tokens": req.max_output_tokens,
            "output_config": output_config,
            "timeout": req.timeout_s,
        }
        if req.effort in ("medium", "high"):
            kw["thinking"] = {"type": "adaptive"}
        if req.stop_sequences:
            kw["stop_sequences"] = req.stop_sequences
        return kw

    async def generate(self, req: LLMRequest) -> LLMResponse:
        if req.max_output_tokens > _STREAM_THRESHOLD:
            return await self._drain_stream(req)  # 注意4: 大きな max_tokens はストリーミング
        t0 = time.monotonic()
        try:
            msg = await self._client.messages.create(**self._kwargs(req))
        except anthropic.APIError as e:
            raise _classify(e, req.model) from e
        return self._message_to_response(msg, req, t0)

    def _message_to_response(self, msg: Any, req: LLMRequest, t0: float) -> LLMResponse:
        text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        usage = Usage(
            input_tokens=msg.usage.input_tokens,
            cached_input_tokens=msg.usage.cache_read_input_tokens or 0,
            cache_write_input_tokens=msg.usage.cache_creation_input_tokens or 0,
            output_tokens=msg.usage.output_tokens,
        )
        return LLMResponse(
            text=text,
            usage=usage,
            provider=self.name,
            model=req.model,
            stop_reason=_STOP_MAP.get(msg.stop_reason or "end_turn", "end"),
            request_id=getattr(msg, "_request_id", None),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        assert req.json_schema is not None
        resp = await self.generate(req)  # output_config.format がネイティブ強制
        return attach_parsed(resp, req.json_schema)

    async def generate_stream(self, req: LLMRequest) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type="start")
        agg: list[str] = []
        try:
            async with self._client.messages.stream(**self._kwargs(req)) as stream:
                async for text in stream.text_stream:
                    agg.append(text)
                    yield StreamEvent(type="text_delta", delta=text)
                final = await stream.get_final_message()
        except anthropic.APIError as e:
            err = _classify(e, req.model)
            yield StreamEvent(type="error", error_kind=str(err.kind), error_message=err.message)
            return
        resp = self._message_to_response(final, req, time.monotonic())
        resp.text = "".join(agg) or resp.text
        yield StreamEvent(type="usage", usage=resp.usage)
        yield StreamEvent(type="end", response=resp)

    async def _drain_stream(self, req: LLMRequest) -> LLMResponse:
        async for ev in self.generate_stream(req):
            if ev.type == "end" and ev.response is not None:
                return ev.response
            if ev.type == "error":
                raise ProviderError(
                    ErrorKind(ev.error_kind or "server_error"),
                    self.name,
                    req.model,
                    ev.error_message or "stream error",
                )
        return LLMResponse(text="", provider=self.name, model=req.model)

    async def count_tokens(self, req: LLMRequest) -> int:
        res = await self._client.messages.count_tokens(
            model=req.model,
            system=self._system_blocks(req),  # type: ignore[arg-type]
            messages=to_anthropic_messages(req.messages),  # type: ignore[arg-type]
        )
        return res.input_tokens
