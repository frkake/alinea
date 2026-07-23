"""claim → code chunk の検索(lexical retrieval + 埋め込み再順位付け)(§8・§9 手順6-7)。

1. **lexical retrieval**: 識別子・希少語・数式名の重なりで各 claim の候補 chunk を粗く絞る。
2. **埋め込み再順位付け**: Task 19 の :class:`EmbeddingProvider` で claim と候補 chunk を
   埋め込み、cosine 類似で上位 K を選ぶ。埋め込みが使えない場合は lexical スコアのみで順位付け。

この段は純ロジック(埋め込み呼び出しは注入された provider 経由)。ネットワーク非依存。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from alinea_llm.protocols import EmbeddingProvider
from alinea_llm.types import EmbeddingRequest

from alinea_core.code_analysis.chunks import CodeChunk
from alinea_core.code_analysis.contracts import AnalysisClaim

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
# ありふれた語は希少語スコアに寄与させない(識別子の弁別力を上げる)。
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "return",
        "self",
        "true",
        "false",
        "none",
        "null",
        "import",
        "class",
        "def",
        "function",
        "value",
        "result",
        "data",
        "list",
        "dict",
        "string",
        "number",
        "using",
        "based",
    }
)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _IDENT_RE.findall(text)]


def _lexical_score(claim_tokens: set[str], chunk_tokens: set[str]) -> float:
    """claim と chunk の弁別トークン重なりを Jaccard 風に採点する。"""
    if not claim_tokens or not chunk_tokens:
        return 0.0
    overlap = claim_tokens & chunk_tokens
    if not overlap:
        return 0.0
    return len(overlap) / math.sqrt(len(claim_tokens) * len(chunk_tokens))


@dataclass(frozen=True)
class RankedChunk:
    chunk: CodeChunk
    score: float


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def lexical_candidates(
    claim: AnalysisClaim, chunks: list[CodeChunk], *, top_n: int
) -> list[RankedChunk]:
    """claim に対して lexical スコア上位 ``top_n`` chunk を返す(0 スコアは除外)。"""
    claim_text = claim.claim_text + " " + " ".join(claim.keywords)
    claim_tokens = {t for t in tokenize(claim_text) if t not in _STOPWORDS}
    scored: list[RankedChunk] = []
    for chunk in chunks:
        chunk_tokens = {
            t for t in tokenize(chunk.symbol + " " + chunk.text) if t not in _STOPWORDS
        }
        score = _lexical_score(claim_tokens, chunk_tokens)
        if score > 0.0:
            scored.append(RankedChunk(chunk=chunk, score=score))
    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:top_n]


async def rerank_with_embeddings(
    claim: AnalysisClaim,
    candidates: list[RankedChunk],
    *,
    provider: EmbeddingProvider,
    model: str,
    dimensions: int | None,
    top_k: int,
) -> list[RankedChunk]:
    """埋め込み cosine で候補を再順位付けし上位 ``top_k`` を返す(§8 再順位付け)。

    provider が失敗した場合は例外を伝播する(呼び出し側=worker が握って lexical 順位へ縮退)。
    """
    if not candidates:
        return []
    inputs = [claim.claim_text] + [c.chunk.text for c in candidates]
    result = await provider.embed(
        EmbeddingRequest(model=model, inputs=inputs, dimensions=dimensions)
    )
    vectors = result.vectors
    if len(vectors) != len(inputs):
        # provider が件数不一致(異常)。lexical 順位のまま返す。
        return candidates[:top_k]
    claim_vec = vectors[0]
    reranked = [
        RankedChunk(chunk=cand.chunk, score=_cosine(claim_vec, vectors[i + 1]))
        for i, cand in enumerate(candidates)
    ]
    reranked.sort(key=lambda r: r.score, reverse=True)
    return reranked[:top_k]
