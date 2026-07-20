"""EmbeddingProvider 抽象 + FakeEmbeddingProvider の単体テスト(S12 Phase A / plans/04)。

実プロバイダへの実通信は行わない(FakeEmbeddingProvider のみ)。決定的応答規則
(同一入力→同一ベクトル・L2 正規化・共有トークンで高コサイン)を検証する。
セマンティック検索(docs/10 §5)の土台となる埋め込み抽象の第一スライス。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from alinea_llm.errors import ErrorKind, ProviderError
from alinea_llm.protocols import EmbeddingProvider
from alinea_llm.providers.openai_embeddings import (
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    OpenAIEmbeddingProvider,
)
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


# --------------------------------------------------------------------------- #
# OpenAIEmbeddingProvider(実プロバイダ。text-embedding-3-small / 1536d)
#
# 実 OpenAI へは接続しない。SDK と同形の Fake クライアントを注入し、応答の検証
# (件数一致・順序・次元・有限値)と不正応答の拒否(保存しない)を確かめる。
# --------------------------------------------------------------------------- #


@dataclass
class _FakeEmbeddingItem:
    embedding: list[float]
    index: int = 0


@dataclass
class _FakeUsage:
    prompt_tokens: int = 0
    total_tokens: int = 0


@dataclass
class _FakeEmbeddingResponse:
    data: list[_FakeEmbeddingItem]
    model: str = DEFAULT_EMBEDDING_MODEL
    usage: _FakeUsage = field(default_factory=_FakeUsage)


class _FakeEmbeddingsNamespace:
    """openai SDK の ``client.embeddings`` を模す(create のみ)。"""

    def __init__(self, owner: _FakeOpenAIClient) -> None:
        self._owner = owner

    async def create(self, **kwargs: Any) -> _FakeEmbeddingResponse:
        self._owner.calls.append(kwargs)
        return self._owner.builder(kwargs)


class _FakeOpenAIClient:
    """OpenAIEmbeddingProvider に注入する async クライアント。"""

    def __init__(self, builder: Any) -> None:
        self.builder = builder
        self.calls: list[dict[str, Any]] = []
        self.embeddings = _FakeEmbeddingsNamespace(self)


def _ordered_builder(kwargs: dict[str, Any]) -> _FakeEmbeddingResponse:
    """入力順どおりに決定的な単純ベクトル(先頭に長さ、残りは 0)を返す。"""
    dim = kwargs.get("dimensions") or DEFAULT_EMBEDDING_DIM
    items = [
        _FakeEmbeddingItem(embedding=[float(len(text))] + [0.0] * (dim - 1), index=i)
        for i, text in enumerate(kwargs["input"])
    ]
    return _FakeEmbeddingResponse(data=items, model=kwargs["model"])


def _make_provider(builder: Any) -> OpenAIEmbeddingProvider:
    return OpenAIEmbeddingProvider(api_key="sk-test", client=_FakeOpenAIClient(builder))


async def test_openai_embedding_defaults_are_small_1536() -> None:
    assert DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"
    assert DEFAULT_EMBEDDING_DIM == 1536


async def test_openai_embedding_provider_satisfies_protocol() -> None:
    p = _make_provider(_ordered_builder)
    assert isinstance(p, EmbeddingProvider)
    assert p.name == "openai"


async def test_openai_embedding_returns_vectors_in_input_order() -> None:
    p = _make_provider(_ordered_builder)
    res = await p.embed(
        EmbeddingRequest(model=DEFAULT_EMBEDDING_MODEL, inputs=["ab", "abcd", "a"])
    )
    assert len(res.vectors) == 3
    # builder は各ベクトル先頭に入力長を入れる → 入力順が保たれていることを確認。
    assert [v[0] for v in res.vectors] == [2.0, 4.0, 1.0]
    assert res.dim == DEFAULT_EMBEDDING_DIM
    assert all(len(v) == DEFAULT_EMBEDDING_DIM for v in res.vectors)
    assert res.provider == "openai"
    assert res.model == DEFAULT_EMBEDDING_MODEL


async def test_openai_embedding_passes_dimensions_and_model() -> None:
    client = _FakeOpenAIClient(_ordered_builder)
    p = OpenAIEmbeddingProvider(api_key="sk-test", client=client)
    await p.embed(EmbeddingRequest(model=DEFAULT_EMBEDDING_MODEL, inputs=["x"]))
    assert client.calls[0]["model"] == DEFAULT_EMBEDDING_MODEL
    assert client.calls[0]["dimensions"] == DEFAULT_EMBEDDING_DIM
    assert client.calls[0]["input"] == ["x"]


async def test_openai_embedding_reorders_by_response_index() -> None:
    """SDK が index 順を保証しなくても index に従って並べ替える。"""

    def shuffled(kwargs: dict[str, Any]) -> _FakeEmbeddingResponse:
        dim = kwargs.get("dimensions") or DEFAULT_EMBEDDING_DIM
        items = [
            _FakeEmbeddingItem(embedding=[float(len(t))] + [0.0] * (dim - 1), index=i)
            for i, t in enumerate(kwargs["input"])
        ]
        items.reverse()  # 逆順で返す(index は保持)
        return _FakeEmbeddingResponse(data=items, model=kwargs["model"])

    p = _make_provider(shuffled)
    res = await p.embed(
        EmbeddingRequest(model=DEFAULT_EMBEDDING_MODEL, inputs=["ab", "abcd", "a"])
    )
    assert [v[0] for v in res.vectors] == [2.0, 4.0, 1.0]


async def test_openai_embedding_rejects_count_mismatch() -> None:
    def short(kwargs: dict[str, Any]) -> _FakeEmbeddingResponse:
        dim = kwargs.get("dimensions") or DEFAULT_EMBEDDING_DIM
        # 2 入力に 1 ベクトルしか返さない不正応答。
        return _FakeEmbeddingResponse(
            data=[_FakeEmbeddingItem(embedding=[0.1] * dim, index=0)], model=kwargs["model"]
        )

    p = _make_provider(short)
    with pytest.raises(ProviderError) as exc:
        await p.embed(EmbeddingRequest(model=DEFAULT_EMBEDDING_MODEL, inputs=["a", "b"]))
    assert exc.value.kind == ErrorKind.SCHEMA_VALIDATION


async def test_openai_embedding_rejects_wrong_dimension() -> None:
    def wrong_dim(kwargs: dict[str, Any]) -> _FakeEmbeddingResponse:
        # 要求 1536 なのに 8 次元しか返さない。
        return _FakeEmbeddingResponse(
            data=[_FakeEmbeddingItem(embedding=[0.1] * 8, index=0)], model=kwargs["model"]
        )

    p = _make_provider(wrong_dim)
    with pytest.raises(ProviderError) as exc:
        await p.embed(EmbeddingRequest(model=DEFAULT_EMBEDDING_MODEL, inputs=["a"]))
    assert exc.value.kind == ErrorKind.SCHEMA_VALIDATION


async def test_openai_embedding_rejects_non_finite_values() -> None:
    def nan_vec(kwargs: dict[str, Any]) -> _FakeEmbeddingResponse:
        dim = kwargs.get("dimensions") or DEFAULT_EMBEDDING_DIM
        vec = [0.0] * dim
        vec[0] = float("nan")
        return _FakeEmbeddingResponse(
            data=[_FakeEmbeddingItem(embedding=vec, index=0)], model=kwargs["model"]
        )

    p = _make_provider(nan_vec)
    with pytest.raises(ProviderError) as exc:
        await p.embed(EmbeddingRequest(model=DEFAULT_EMBEDDING_MODEL, inputs=["a"]))
    assert exc.value.kind == ErrorKind.SCHEMA_VALIDATION


async def test_openai_embedding_rejects_empty_input() -> None:
    p = _make_provider(_ordered_builder)
    with pytest.raises(ProviderError) as exc:
        await p.embed(EmbeddingRequest(model=DEFAULT_EMBEDDING_MODEL, inputs=[]))
    assert exc.value.kind == ErrorKind.INVALID_REQUEST


# --------------------------------------------------------------------------- #
# models.yaml / routing.yaml に埋め込み設定が載っていること(既存タスク/モデルは不変)
# --------------------------------------------------------------------------- #

_PKG = Path(__file__).resolve().parents[1]


def _yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((_PKG / name).read_text(encoding="utf-8"))


def test_models_yaml_registers_embedding_model() -> None:
    data = _yaml("models.yaml")
    embed_models = data.get("embedding_models") or []
    ids = {m["id"] for m in embed_models}
    assert DEFAULT_EMBEDDING_MODEL in ids
    small = next(m for m in embed_models if m["id"] == DEFAULT_EMBEDDING_MODEL)
    assert small["provider"] == "openai"
    assert small["dimensions"] == DEFAULT_EMBEDDING_DIM
    # 既存のテキスト/画像モデル定義は壊さない(登録済みモデルは従来どおり)。
    assert any(m["id"] == "gpt-5.5" for m in data.get("text_models") or [])


def test_routing_yaml_registers_embedding_task() -> None:
    data = _yaml("routing.yaml")
    embedding = (data.get("embedding") or {})
    assert embedding.get("model") == DEFAULT_EMBEDDING_MODEL
    assert embedding.get("provider") == "openai"
    assert embedding.get("dimensions") == DEFAULT_EMBEDDING_DIM
    # 生成タスクは tasks: 配下(embedding は tasks に混ぜない = CHECK 制約を壊さない)。
    # Task 28 で presentation を追加(8→9 タスク)。
    assert set(data.get("tasks", {})) == {
        "translation",
        "retranslation_escalation",
        "chat",
        "summary",
        "article",
        "overview_figure_dsl",
        "vocab",
        "explainer_image",
        "presentation",
    }
