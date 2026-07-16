"""PGroonga 全文検索の平文導出・索引再構築・クエリ/スニペット整形(plans/11)、
および S12 セマンティック検索の融合純関数(docs/10 §5)。"""

from alinea_core.search.fusion import (
    blend_lexical_semantic,
    cosine_similarity,
    rank_by_similarity,
    reciprocal_rank_fusion,
)
from alinea_core.search.pgroonga_query import (
    chat_qa_snippet,
    finalize_snippet_html,
    is_valid_query,
    matched_in,
    normalize_query,
    snippet_lang_for,
    truncate_plain,
)
from alinea_core.search.rebuild import rebuild_block_search_index

__all__ = [
    "blend_lexical_semantic",
    "chat_qa_snippet",
    "cosine_similarity",
    "finalize_snippet_html",
    "is_valid_query",
    "matched_in",
    "normalize_query",
    "rank_by_similarity",
    "rebuild_block_search_index",
    "reciprocal_rank_fusion",
    "snippet_lang_for",
    "truncate_plain",
]
