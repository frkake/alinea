"""OpenAI 埋め込みアダプタ(S12 セマンティック検索。docs/10 §5・spec §D1)。

``text-embedding-3-small``(1536 次元)を既定にする。テキスト生成の :class:`OpenAIProvider`
とは別系統で、:class:`~alinea_llm.protocols.EmbeddingProvider` だけを満たす(embed のみ)。

fail-closed 方針: OpenAI 応答は信用しない。件数(入力と同数)・順序(index 昇順に整列)・
次元(要求次元と一致)・有限値(NaN/Inf を含まない)を検証し、少しでも不正なら
``ProviderError(SCHEMA_VALIDATION)`` を送出して **保存しない**(呼び出し側=インデクシング
ジョブは storage への upsert を行わない)。ベクトル空間はモデルが規定するため、次元不一致は
既存インデックスとの非互換を意味し、絶対に混ぜてはならない。

リトライは Router が一元管理する前提で max_retries=0。ネットワーク例外は
:func:`~alinea_llm.providers._common.classify_openai` で ErrorKind に正規化する。
"""

from __future__ import annotations

import math
import time
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.providers._common import base_url_override, classify_openai
from alinea_llm.types import EmbeddingRequest, EmbeddingResult, Usage

# 既定の埋め込みモデルと次元(spec §D1 推奨。1536 は models.yaml / Alembic の vector(1536) と一致)。
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_EMBEDDING_DIM = 1536


class OpenAIEmbeddingProvider:
    """OpenAI 埋め込みプロバイダ(EmbeddingProvider 準拠)。"""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        *,
        dim: int = DEFAULT_EMBEDDING_DIM,
        client: Any | None = None,
    ) -> None:
        # client は単体テストで SDK 同形の Fake を注入するための注入口(実運用は None)。
        self._dim = dim
        if client is not None:
            self._client = client
        else:
            url = base_url or base_url_override("openai")
            self._client = AsyncOpenAI(api_key=api_key, base_url=url, max_retries=0)

    async def embed(self, req: EmbeddingRequest) -> EmbeddingResult:
        if not req.inputs:
            # 空バッチは実装バグ(呼び出し側が入力を用意していない)。API を叩かない。
            raise ProviderError(
                ErrorKind.INVALID_REQUEST, self.name, req.model, "empty embedding input batch"
            )
        dim = req.dimensions or self._dim
        model = req.model or DEFAULT_EMBEDDING_MODEL
        t0 = time.monotonic()
        try:
            response = await self._client.embeddings.create(
                model=model,
                input=req.inputs,
                dimensions=dim,
            )
        except (APITimeoutError, APIConnectionError, APIStatusError) as e:
            raise classify_openai(e, self.name, model) from e

        vectors = self._validated_vectors(
            response, expected_count=len(req.inputs), dim=dim, model=model
        )
        return EmbeddingResult(
            vectors=vectors,
            dim=dim,
            provider=self.name,
            model=getattr(response, "model", None) or model,
            usage=self._usage(response),
            request_id=getattr(response, "id", None),
            latency_ms=int((time.monotonic() - t0) * 1000),
        )

    def _validated_vectors(
        self, response: Any, *, expected_count: int, dim: int, model: str
    ) -> list[list[float]]:
        """応答を fail-closed 検証し、index 昇順に整列したベクトル列を返す。"""
        data = list(getattr(response, "data", None) or [])
        if len(data) != expected_count:
            raise ProviderError(
                ErrorKind.SCHEMA_VALIDATION,
                self.name,
                model,
                f"embedding count mismatch: got {len(data)} for {expected_count} inputs",
            )
        # index が付いていれば昇順に整列する(SDK が順序を保証しない場合の保険)。
        # index を持たない/欠落する要素は元の位置を使う。
        def _index(item: Any, fallback: int) -> int:
            idx = getattr(item, "index", None)
            return idx if isinstance(idx, int) else fallback

        ordered = sorted(enumerate(data), key=lambda pair: _index(pair[1], pair[0]))
        vectors: list[list[float]] = []
        for _pos, item in ordered:
            raw = getattr(item, "embedding", None)
            if raw is None:
                raise ProviderError(
                    ErrorKind.SCHEMA_VALIDATION, self.name, model, "embedding row missing vector"
                )
            vec = [float(x) for x in raw]
            if len(vec) != dim:
                raise ProviderError(
                    ErrorKind.SCHEMA_VALIDATION,
                    self.name,
                    model,
                    f"embedding dim mismatch: got {len(vec)} expected {dim}",
                )
            if any(not math.isfinite(x) for x in vec):
                raise ProviderError(
                    ErrorKind.SCHEMA_VALIDATION,
                    self.name,
                    model,
                    "embedding contains non-finite values",
                )
            vectors.append(vec)
        return vectors

    @staticmethod
    def _usage(response: Any) -> Usage:
        usage = getattr(response, "usage", None)
        if usage is None:
            return Usage()
        return Usage(input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0))


__all__ = [
    "DEFAULT_EMBEDDING_DIM",
    "DEFAULT_EMBEDDING_MODEL",
    "OpenAIEmbeddingProvider",
]
