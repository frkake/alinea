"""Protocol 定義(plans/04 §5)。

抽象化層本体はドメイン DB を知らない。キー解決・計測記録は KeyStore / MeterHook の
小さな Protocol で apps/api / apps/worker から注入する。単体テストは Fake で完結。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from alinea_llm.types import (
    EmbeddingRequest,
    EmbeddingResult,
    ImageRequest,
    ImageResult,
    LLMRequest,
    LLMResponse,
    StreamEvent,
    Usage,
)


@runtime_checkable
class LLMProvider(Protocol):
    """テキスト生成プロバイダ。実装: OpenAI / Anthropic / Google / DeepSeek / xAI。"""

    name: str  # "openai" | "anthropic" | "google" | "deepseek" | "xai"

    async def generate(self, req: LLMRequest) -> LLMResponse:
        """非ストリーミング1回生成。失敗は ProviderError を送出。"""
        ...

    def generate_stream(self, req: LLMRequest) -> AsyncIterator[StreamEvent]:
        """正規化ストリーム(§12)。start → text_delta* → usage → end。"""
        ...

    async def generate_structured(self, req: LLMRequest) -> LLMResponse:
        """req.json_schema 必須。response.parsed に検証済み JSON を返す(§12)。"""
        ...

    async def count_tokens(self, req: LLMRequest) -> int:
        """入力トークン数の見積り(§14)。課金には使わない。"""
        ...


@runtime_checkable
class ImageProvider(Protocol):
    """ラスター画像生成プロバイダ。実装: OpenAI / Google / xAI(docs/07 §1.3)。"""

    name: str  # "openai" | "google" | "xai"

    async def generate_image(self, req: ImageRequest) -> ImageResult:
        """1枚生成して PNG バイト列+メタを返す。失敗は ProviderError を送出。"""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """埋め込み生成プロバイダ(S12 セマンティック検索。docs/10 §5)。

    テキスト生成(:class:`LLMProvider`)とは別系統の独立プロトコル。埋め込み非対応の
    プロバイダ(Anthropic 等)を壊さないため ``LLMProvider`` に ``embed`` を足さない。
    実装候補: OpenAI(text-embedding-3-*) / Google(gemini-embedding-001)。
    """

    name: str  # "openai" | "google" | ...

    async def embed(self, req: EmbeddingRequest) -> EmbeddingResult:
        """バッチをまとめて埋め込む。失敗は ProviderError を送出。"""
        ...


class ResolvedKey(BaseModel):
    provider: str
    api_key: str
    source: str  # "user" | "operator"


class UsageDraft(BaseModel):
    user_id: str | None = None
    library_item_id: str | None = None
    job_id: str | None = None
    task: str
    provider: str
    model: str
    key_source: str  # "user" | "operator"
    usage: Usage | None = None
    image_count: int = 0
    cost_usd: float = 0.0
    status: str  # "ok" | "error"
    attempt: int = 1
    fallback_rank: int = 0  # 0=primary, 1=第1フォールバック, …
    error_kind: str | None = None
    latency_ms: int | None = None
    request_id: str | None = None


class KeyStore(Protocol):
    """API キー解決。apps/api の DbKeyStore が実装(§11)。"""

    async def resolve(self, user_id: str | None, provider: str) -> ResolvedKey: ...

    async def mark_invalid(self, user_id: str, provider: str) -> None: ...


class MeterHook(Protocol):
    """使用量計測。apps/api の DbMeterHook が実装(§10)。"""

    async def record(self, record: UsageDraft) -> None: ...
