"""EmbeddingProvider 抽象 + FakeEmbeddingProvider の単体テスト(S12 Phase A / plans/04)。

実プロバイダへの実通信は行わない(FakeEmbeddingProvider のみ)。決定的応答規則
(同一入力→同一ベクトル・L2 正規化・共有トークンで高コサイン)を検証する。
セマンティック検索(docs/10 §5)の土台となる埋め込み抽象の第一スライス。
"""

from __future__ import annotations

import math

import pytest
from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.protocols import EmbeddingProvider
from alinea_llm.testing.fake_provider import FakeEmbeddingProvider
from alinea_llm.types import EmbeddingRequest


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def _cosine(a: list[float], b: list[float]) -> float:
    na, nb = _norm(a), _norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


async def test_fake_embedding_is_deterministic() -> None:
    p = FakeEmbeddingProvider()
    r1 = await p.embed(EmbeddingRequest(model="fake-embed", inputs=["rectified flow"]))
    r2 = await p.embed(EmbeddingRequest(model="fake-embed", inputs=["rectified flow"]))
    assert r1.vectors == r2.vectors


async def test_fake_embedding_shape_matches_request() -> None:
    p = FakeEmbeddingProvider(dim=16)
    req = EmbeddingRequest(model="fake-embed", inputs=["a", "b", "c"])
    res = await p.embed(req)
    assert len(res.vectors) == 3
    assert all(len(v) == 16 for v in res.vectors)
    assert res.dim == 16
    assert res.provider == "fake"
    assert res.model == "fake-embed"


async def test_fake_embedding_is_l2_normalized() -> None:
    p = FakeEmbeddingProvider()
    res = await p.embed(EmbeddingRequest(model="fake-embed", inputs=["straight transport paths"]))
    assert _norm(res.vectors[0]) == pytest.approx(1.0, abs=1e-6)


async def test_fake_embedding_empty_input_is_zero_vector() -> None:
    p = FakeEmbeddingProvider(dim=8)
    res = await p.embed(EmbeddingRequest(model="fake-embed", inputs=[""]))
    assert res.vectors[0] == [0.0] * 8


async def test_fake_embedding_shared_tokens_raise_cosine() -> None:
    p = FakeEmbeddingProvider(dim=64)
    res = await p.embed(
        EmbeddingRequest(
            model="fake-embed",
            inputs=[
                "rectified flow model",
                "rectified flow method",
                "banana bread recipe",
            ],
        )
    )
    related = _cosine(res.vectors[0], res.vectors[1])
    unrelated = _cosine(res.vectors[0], res.vectors[2])
    assert related > unrelated


async def test_fake_embedding_provider_satisfies_protocol() -> None:
    assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)


async def test_fake_embedding_fail_raises_provider_error() -> None:
    p = FakeEmbeddingProvider(fail=True)
    with pytest.raises(ProviderError) as exc:
        await p.embed(EmbeddingRequest(model="fake-embed", inputs=["x"]))
    assert exc.value.kind == ErrorKind.MODEL_NOT_FOUND


async def test_fake_embedding_reports_usage() -> None:
    p = FakeEmbeddingProvider()
    res = await p.embed(EmbeddingRequest(model="fake-embed", inputs=["abcdefgh"]))
    assert res.usage.input_tokens >= 1
