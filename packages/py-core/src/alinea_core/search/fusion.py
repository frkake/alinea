"""検索結果の融合とベクトル類似度の純関数(S12 セマンティック検索。docs/10 §5)。

lexical(PGroonga 全文検索)と semantic(埋め込み ANN)の 2 つのランク済みリストを、
スケール非依存の Reciprocal Rank Fusion(RRF)で合流させる。DB も LLM も要らない純関数
なので、pgvector 導入前でも決定的に単体テストできる(設計 §7)。

RRF: 各リストで各要素の順位 rank(0 始まり)から ``Σ weight_i / (k + rank + 1)`` を集計する。
スコアの絶対値ではなく順位のみを使うため、PGroonga スコアとコサイン類似度のように単位が
異なるスコアでも公平にブレンドできる(ハイブリッド検索の標準手法。k=60 が定番)。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

__all__ = [
    "blend_lexical_semantic",
    "cosine_similarity",
    "rank_by_similarity",
    "reciprocal_rank_fusion",
]

# RRF の平滑化定数(定番値)。上位順位の寄与を穏やかにする。
DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = DEFAULT_RRF_K,
    weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """複数のランク済み ID リストを RRF で融合する。

    - ``ranked_lists``: 各要素は関連度降順の ID 列(重複は初出順位のみ採用)。
    - ``k``: 平滑化定数。
    - ``weights``: リスト別重み(既定は全 1.0)。長さは ``ranked_lists`` と一致必須。
    返り値は ``(id, fused_score)`` の融合スコア降順。同点は ID 昇順で決定的。
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights length must match ranked_lists length")

    scores: dict[str, float] = {}
    for ranked, weight in zip(ranked_lists, weights, strict=True):
        seen: set[str] = set()
        for rank, key in enumerate(ranked):
            if key in seen:
                continue  # 同一リスト内の重複は最上位順位のみ採用
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank + 1)

    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def blend_lexical_semantic(
    lexical_ids: Sequence[str],
    semantic_ids: Sequence[str],
    *,
    k: int = DEFAULT_RRF_K,
    w_lexical: float = 1.0,
    w_semantic: float = 1.0,
) -> list[str]:
    """lexical / semantic の 2 リストを RRF で融合し、ID の順序のみ返す薄いラッパ。

    片方が空でも他方の順序をそのまま返す(縮退)。両方空なら空リスト。セマンティック検索が
    未整備・埋め込み失敗のときに lexical のみへ縮退する経路(設計 §6.1)で使う。
    """
    fused = reciprocal_rank_fusion(
        [lexical_ids, semantic_ids], k=k, weights=[w_lexical, w_semantic]
    )
    return [key for key, _ in fused]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """コサイン類似度。零ベクトルは 0.0 を返す(ZeroDivision を避ける)。"""
    if len(a) != len(b):
        raise ValueError("vectors must have the same length")
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def rank_by_similarity(
    query: Sequence[float],
    candidates: Mapping[str, Sequence[float]],
    *,
    top_k: int | None = None,
) -> list[tuple[str, float]]:
    """クエリベクトルへのコサイン類似度で候補を降順ランクする(Python 側 ANN 代替)。

    pgvector 導入前の検証・小規模フォールバック用。返り値は ``(id, cosine)`` の降順、
    同点は ID 昇順。``top_k`` 指定時は上位のみ。
    """
    scored = [(key, cosine_similarity(query, vec)) for key, vec in candidates.items()]
    scored.sort(key=lambda kv: (-kv[1], kv[0]))
    if top_k is not None:
        return scored[:top_k]
    return scored
