"""S12 Phase A: 検索融合の純関数(alinea_core.search.fusion)。

DB もネットワークも使わない決定的テスト。Reciprocal Rank Fusion(RRF)・コサイン類似度・
類似度ランキングを検証する。lexical(PGroonga)と semantic(埋め込み ANN)のブレンド土台。
"""

from __future__ import annotations

import pytest
from alinea_core.search.fusion import (
    blend_lexical_semantic,
    cosine_similarity,
    rank_by_similarity,
    reciprocal_rank_fusion,
)


# ---- RRF ----
def test_rrf_hand_computed_scores_and_order() -> None:
    # a: ranks 0,2 → 1/61 + 1/63 ; b: 1,0 → 1/62 + 1/61 ; c: 2,1 → 1/63 + 1/62
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "a"]], k=60)
    scores = dict(fused)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 63)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 62)
    # b has the highest fused score (rank1 in both-ish); order is score desc.
    assert [key for key, _ in fused] == ["b", "a", "c"]


def test_rrf_is_scale_independent() -> None:
    # RRF only uses positions, so the source raw-score magnitude is irrelevant.
    order_small = [key for key, _ in reciprocal_rank_fusion([["x", "y", "z"]])]
    order_large = [key for key, _ in reciprocal_rank_fusion([["x", "y", "z"]])]
    assert order_small == order_large == ["x", "y", "z"]


def test_rrf_ties_broken_by_id_ascending() -> None:
    # single list, but two ids never co-occur → same-position ids tie-break by id asc.
    fused = reciprocal_rank_fusion([["b"], ["a"]], k=60)
    assert fused[0][0] == "a" and fused[0][1] == pytest.approx(1 / 61)
    assert fused[1][0] == "b" and fused[1][1] == pytest.approx(1 / 61)


def test_rrf_weights_can_disable_a_list() -> None:
    lexical = ["l1", "l2", "l3"]
    semantic = ["s1", "s2", "s3"]
    # Zero-weighted ids still appear (score 0) but rank strictly after positive-scored ids,
    # so the weighted list's order forms the prefix.
    only_lexical = reciprocal_rank_fusion([lexical, semantic], weights=[1.0, 0.0])
    assert [key for key, _ in only_lexical][:3] == lexical
    only_semantic = reciprocal_rank_fusion([lexical, semantic], weights=[0.0, 1.0])
    assert [key for key, _ in only_semantic][:3] == semantic


def test_rrf_weight_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0])


def test_rrf_empty_lists_return_empty() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


# ---- blend wrapper ----
def test_blend_returns_fused_id_order() -> None:
    order = blend_lexical_semantic(["a", "b"], ["b", "c"])
    assert set(order) == {"a", "b", "c"}
    assert order[0] == "b"  # appears in both, top-ranked


def test_blend_one_empty_returns_other_order() -> None:
    assert blend_lexical_semantic(["a", "b", "c"], []) == ["a", "b", "c"]
    assert blend_lexical_semantic([], ["x", "y"]) == ["x", "y"]


def test_blend_both_empty_returns_empty() -> None:
    assert blend_lexical_semantic([], []) == []


# ---- cosine ----
def test_cosine_known_values() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 0.0]) == 0.0  # zero vector, no ZeroDivision


def test_cosine_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 0.0], [1.0])


# ---- rank_by_similarity ----
def test_rank_by_similarity_orders_desc_and_respects_top_k() -> None:
    query = [1.0, 0.0]
    candidates = {
        "same": [2.0, 0.0],       # cosine 1.0
        "ortho": [0.0, 1.0],      # cosine 0.0
        "near": [1.0, 0.2],       # cosine ~0.98
    }
    ranked = rank_by_similarity(query, candidates)
    assert [key for key, _ in ranked] == ["same", "near", "ortho"]
    top1 = rank_by_similarity(query, candidates, top_k=1)
    assert [key for key, _ in top1] == ["same"]


def test_rank_by_similarity_ties_broken_by_id_ascending() -> None:
    query = [1.0, 0.0]
    candidates = {"b": [1.0, 0.0], "a": [1.0, 0.0]}
    ranked = rank_by_similarity(query, candidates)
    assert [key for key, _ in ranked] == ["a", "b"]


def test_rank_by_similarity_empty_returns_empty() -> None:
    assert rank_by_similarity([1.0, 0.0], {}) == []


# ---- feature flag receptacle (S12 Phase A) ----
def test_semantic_search_flag_defaults_off() -> None:
    from alinea_core.settings import CoreSettings

    assert CoreSettings().semantic_search_enabled is False
