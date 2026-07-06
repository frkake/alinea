"""実行エンジン LLMRouter / ImageRouter(plans/04 §9)。

リトライ(RETRYABLE を同一モデルで最大 max_attempts 回)・フォールバック
(FALLBACK_ELIGIBLE で次モデルへ)・fatal 中断(ProviderChainExhausted)・計測記録を
一元管理する。

M0 の呼び出し契約(2026-07-06 実装計画 Task 7 Step 1 が正)は明示チェーン形:
    LLMRouter(chain=[(provider_name, model_id, provider), ...])
    await router.complete(task="translate", prompt=...) -> LLMResponse
provider=None のエントリは運営キー未設定(§11.1-3)としてチェーンから除外する。
DB ベースのルート解決・KeyStore/MeterHook 統合は apps/api 側(M0-13)で拡張する。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence

from yakudoku_llm.errors import ProviderChainExhausted, ProviderError
from yakudoku_llm.protocols import ImageProvider, LLMProvider, MeterHook, UsageDraft
from yakudoku_llm.registry import ModelRegistry
from yakudoku_llm.routing import RetryConfig
from yakudoku_llm.types import (
    ContentPart,
    ImageRequest,
    ImageResult,
    JsonSchemaSpec,
    LLMRequest,
    LLMResponse,
    Message,
)

__all__ = ["ImageRouter", "LLMRouter", "RetryConfig"]

ChainEntry = tuple[str, str, LLMProvider | None]
ImageChainEntry = tuple[str, str, ImageProvider | None]
Mode = str  # "generate" | "structured"


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class LLMRouter:
    def __init__(
        self,
        chain: Sequence[ChainEntry],
        *,
        retry: RetryConfig | None = None,
        registry: ModelRegistry | None = None,
        meter: MeterHook | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._chain: list[ChainEntry] = list(chain)
        self._retry = retry or RetryConfig()
        self._registry = registry
        self._meter = meter
        self._clock = clock
        self._sleep: Callable[[float], Awaitable[None]] = sleep or _default_sleep

    def _active(self) -> list[tuple[str, str, LLMProvider]]:
        # provider=None(キー未設定)を除外(§11.1-3)
        return [(name, model, p) for (name, model, p) in self._chain if p is not None]

    def _prepare(
        self,
        request: LLMRequest | None,
        task: str,
        prompt: str | None,
        model: str,
        schema: JsonSchemaSpec | None,
    ) -> LLMRequest:
        if request is not None:
            meta = dict(request.metadata)
            meta.setdefault("task", task)
            update: dict[str, object] = {"model": model, "metadata": meta}
            if schema is not None:
                update["json_schema"] = schema
            return request.model_copy(update=update)
        parts = [ContentPart(type="text", text=prompt or "")]
        return LLMRequest(
            model=model,
            messages=[Message(role="user", parts=parts)],
            json_schema=schema,
            metadata={"task": task},
        )

    def _backoff(self, err: ProviderError, attempt: int) -> float:
        if self._retry.respect_retry_after and err.retry_after_s is not None:
            return min(err.retry_after_s, self._retry.retry_after_cap_s)
        base = self._retry.backoff_base_s * (self._retry.backoff_factor ** (attempt - 1))
        return base + random.uniform(0.0, self._retry.jitter_s)  # noqa: S311 (バックオフ jitter・非暗号用途)

    async def _record(self, draft: UsageDraft) -> None:
        if self._meter is not None:
            await self._meter.record(draft)

    def _cost(self, model: str, resp: LLMResponse) -> float:
        if self._registry is None:
            return 0.0
        try:
            return self._registry.text_cost_usd(model, resp.usage)
        except KeyError:
            return 0.0

    async def complete(
        self,
        task: str,
        prompt: str | None = None,
        *,
        schema: JsonSchemaSpec | None = None,
        mode: Mode = "generate",
        request: LLMRequest | None = None,
        user_id: str | None = None,
        library_item_id: str | None = None,
        job_id: str | None = None,
    ) -> LLMResponse:
        errors: list[ProviderError] = []
        for rank, (name, model, provider) in enumerate(self._active()):
            req = self._prepare(request, task, prompt, model, schema)
            for attempt in range(1, self._retry.max_attempts + 1):
                try:
                    if mode == "structured":
                        resp = await provider.generate_structured(req)
                    else:
                        resp = await provider.generate(req)
                except ProviderError as err:
                    await self._record(
                        UsageDraft(
                            user_id=user_id,
                            library_item_id=library_item_id,
                            job_id=job_id,
                            task=task,
                            provider=name,
                            model=model,
                            key_source="operator",
                            status="error",
                            attempt=attempt,
                            fallback_rank=rank,
                            error_kind=str(err.kind),
                            request_id=err.request_id,
                        )
                    )
                    if err.retryable and attempt < self._retry.max_attempts:
                        await self._sleep(self._backoff(err, attempt))
                        continue
                    errors.append(err)
                    if not err.fallback_eligible:
                        raise ProviderChainExhausted(task, errors) from err
                    break  # 次モデルへフォールバック
                else:
                    resp.fallback_rank = rank
                    await self._record(
                        UsageDraft(
                            user_id=user_id,
                            library_item_id=library_item_id,
                            job_id=job_id,
                            task=task,
                            provider=name,
                            model=model,
                            key_source="operator",
                            usage=resp.usage,
                            cost_usd=self._cost(model, resp),
                            status="ok",
                            attempt=attempt,
                            fallback_rank=rank,
                            latency_ms=resp.latency_ms,
                            request_id=resp.request_id,
                        )
                    )
                    return resp
        raise ProviderChainExhausted(task, errors)

    async def count_tokens(
        self,
        task: str,
        prompt: str | None = None,
        *,
        request: LLMRequest | None = None,
    ) -> int:
        active = self._active()
        if not active:
            raise ProviderChainExhausted(task, [])
        _name, model, provider = active[0]
        req = self._prepare(request, task, prompt, model, None)
        return await provider.count_tokens(req)


class ImageRouter:
    """explainer_image チェーンを LLMRouter と同一規則で実行(§9.2)。"""

    def __init__(
        self,
        chain: Sequence[ImageChainEntry],
        *,
        retry: RetryConfig | None = None,
        registry: ModelRegistry | None = None,
        meter: MeterHook | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._chain: list[ImageChainEntry] = list(chain)
        self._retry = retry or RetryConfig()
        self._registry = registry
        self._meter = meter
        self._sleep: Callable[[float], Awaitable[None]] = sleep or _default_sleep

    def _active(self) -> list[tuple[str, str, ImageProvider]]:
        return [(name, model, p) for (name, model, p) in self._chain if p is not None]

    def _backoff(self, err: ProviderError, attempt: int) -> float:
        if self._retry.respect_retry_after and err.retry_after_s is not None:
            return min(err.retry_after_s, self._retry.retry_after_cap_s)
        base = self._retry.backoff_base_s * (self._retry.backoff_factor ** (attempt - 1))
        return base + random.uniform(0.0, self._retry.jitter_s)  # noqa: S311

    async def generate(
        self,
        prompt: str,
        *,
        task: str = "explainer_image",
        request: ImageRequest | None = None,
        user_id: str | None = None,
        library_item_id: str | None = None,
        job_id: str | None = None,
    ) -> ImageResult:
        errors: list[ProviderError] = []
        for rank, (name, model, provider) in enumerate(self._active()):
            req = (
                request.model_copy(update={"model": model})
                if request
                else ImageRequest(model=model, prompt=prompt)
            )
            for attempt in range(1, self._retry.max_attempts + 1):
                try:
                    result = await provider.generate_image(req)
                except ProviderError as err:
                    if self._meter is not None:
                        await self._meter.record(
                            UsageDraft(
                                user_id=user_id,
                                library_item_id=library_item_id,
                                job_id=job_id,
                                task=task,
                                provider=name,
                                model=model,
                                key_source="operator",
                                status="error",
                                attempt=attempt,
                                fallback_rank=rank,
                                error_kind=str(err.kind),
                            )
                        )
                    if err.retryable and attempt < self._retry.max_attempts:
                        await self._sleep(self._backoff(err, attempt))
                        continue
                    errors.append(err)
                    if not err.fallback_eligible:
                        raise ProviderChainExhausted(task, errors) from err
                    break
                else:
                    result.fallback_rank = rank
                    if self._registry is not None:
                        try:
                            result.cost_usd = self._registry.image_cost_usd(model, req.quality)
                        except KeyError:
                            pass
                    if self._meter is not None:
                        await self._meter.record(
                            UsageDraft(
                                user_id=user_id,
                                library_item_id=library_item_id,
                                job_id=job_id,
                                task=task,
                                provider=name,
                                model=model,
                                key_source="operator",
                                image_count=1,
                                cost_usd=result.cost_usd,
                                status="ok",
                                attempt=attempt,
                                fallback_rank=rank,
                                latency_ms=result.latency_ms,
                                request_id=result.request_id,
                            )
                        )
                    return result
        raise ProviderChainExhausted(task, errors)
