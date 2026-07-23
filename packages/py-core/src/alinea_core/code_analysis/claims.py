"""論文 DocumentContent から主張候補を block anchor 付きで抽出する(§9 手順4)。

最大 :data:`~alinea_core.code_analysis.contracts.MAX_CLAIMS` 件の主張を、本文段落・
定理・アルゴリズムブロックから選ぶ。各主張は所属 section_id / block_id を anchor に持つ。

素朴だが決定的な選択規則(実装/アルゴリズムに関係しそうな段落を優先):
- paragraph / theorem / algorithm ブロックを対象にする(figure caption / reference は除外)。
- 識別子らしい語・数式・「method / algorithm / propose / define」等の手がかり語を含む段落を優先。
"""

from __future__ import annotations

import re

from alinea_core.code_analysis.contracts import MAX_CLAIMS, AnalysisClaim, ClaimSet
from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import block_to_plain

_CLAIM_BLOCK_TYPES = frozenset({"paragraph", "theorem", "algorithm", "list", "equation"})
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CAMEL_OR_SNAKE = re.compile(r"[A-Za-z]+[A-Z][A-Za-z]*|[a-z]+_[a-z_]+")
_CUE_WORDS = frozenset(
    {
        "algorithm",
        "method",
        "propose",
        "proposed",
        "define",
        "defined",
        "compute",
        "loss",
        "gradient",
        "objective",
        "function",
        "layer",
        "network",
        "sample",
        "update",
        "optimize",
        "train",
        "encoder",
        "decoder",
        "attention",
        "equation",
        "theorem",
    }
)
_MIN_CLAIM_CHARS = 40


def _keywords(text: str) -> tuple[str, ...]:
    """chunk 検索に効く弁別語(camelCase/snake_case 識別子・手がかり語)を抽出する。"""
    idents = {m.group(0) for m in _CAMEL_OR_SNAKE.finditer(text)}
    tokens = [t for t in _IDENT_RE.findall(text)]
    cues = {t for t in tokens if t.lower() in _CUE_WORDS}
    return tuple(sorted(idents | cues))[:12]


def _score(text: str) -> float:
    lower = text.lower()
    cue_hits = sum(1 for w in _CUE_WORDS if w in lower)
    ident_hits = len(set(_CAMEL_OR_SNAKE.findall(text)))
    return cue_hits * 2.0 + ident_hits


def extract_claims(
    content: DocumentContent, revision_id: str, *, max_claims: int = MAX_CLAIMS
) -> ClaimSet:
    """DocumentContent から最大 max_claims 件の主張を anchor 付きで抽出する。"""
    scored: list[tuple[float, AnalysisClaim]] = []
    for section, block in content.iter_blocks():
        if block.type not in _CLAIM_BLOCK_TYPES:
            continue
        text = block_to_plain(block).strip()
        if len(text) < _MIN_CLAIM_CHARS:
            continue
        score = _score(text)
        if score <= 0.0:
            continue
        scored.append(
            (
                score,
                AnalysisClaim(
                    section_id=section.id,
                    block_id=block.id,
                    claim_text=text,
                    keywords=_keywords(text),
                ),
            )
        )
    # スコア降順、同点は文書順(block_id)で決定的に。
    scored.sort(key=lambda pair: (-pair[0], pair[1].block_id))
    claims = [claim for _score, claim in scored[:max_claims]]
    return ClaimSet(revision_id=revision_id, claims=claims)
