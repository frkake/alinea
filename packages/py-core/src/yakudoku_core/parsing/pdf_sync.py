"""品質 A の page+bbox 同期(PDF 位置同期。plans/05 §4.6)。

M0 は arXiv HTML が主経路のため最小実装(インターフェースと素通しデータ構造)。
本経路では原文 PDF の単語 bbox 列(PyMuPDF `page.get_text("words")` 相当)を外部から
受け取り、各ブロックの先頭テキストを単調前進で探索して page/bbox を導出する。
PyMuPDF・rapidfuzz は未導入のため、抽出は呼び出し側の責務とし、照合は標準ライブラリ
difflib.SequenceMatcher の部分一致で行う。単語列が無ければ全ブロック NULL を返す(素通し)。

導出結果は document_revisions.content には入れず block_search_index.page/bbox へ格納する
(派生・再生成可能な値のため。plans/05 §4.6・§13-5)。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from yakudoku_core.document.blocks import Block, Section
from yakudoku_core.document.plaintext import block_to_plain

_WS = re.compile(r"\s+")

# 位置同期の対象となるブロック種別(plans/05 §4.6)。
SYNC_BLOCK_TYPES = ("paragraph", "heading", "figure", "table", "theorem", "quote", "list")
# 先頭何文字を照合キーにするか。
PREFIX_CHARS = 80
# マッチ採用スコア。
MATCH_THRESHOLD = 0.85


@dataclass(frozen=True)
class PdfWord:
    """PDF 1 単語の位置(PyMuPDF `page.get_text("words")` 相当)。"""

    page: int  # 1 起点
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class BlockPosition:
    """ブロックの PDF 位置。未同期は page/bbox とも None。"""

    block_id: str
    page: int | None = None
    bbox: list[float] | None = None  # [x0, y0, x1, y1] pt


@dataclass
class PdfSyncResult:
    """同期結果(block_search_index 反映用)。"""

    positions: list[BlockPosition] = field(default_factory=list)
    matched: int = 0
    target: int = 0

    @property
    def sync_rate(self) -> float:
        return self.matched / self.target if self.target else 0.0


def _normalize(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip().lower()


def _iter_sync_blocks(sections: list[Section]) -> list[Block]:
    out: list[Block] = []

    def walk(sec: Section) -> None:
        for blk in sec.blocks:
            if blk.type in SYNC_BLOCK_TYPES:
                out.append(blk)
        for sub in sec.sections:
            walk(sub)

    for sec in sections:
        walk(sec)
    return out


def _page_text(words: list[PdfWord]) -> str:
    return _normalize(" ".join(w.text for w in words))


def _match_prefix(prefix: str, words: list[PdfWord]) -> tuple[float, list[float]] | None:
    """ページ単語列内で prefix に最も近い連続区間の外接矩形を返す(素朴な部分一致)。"""
    if not prefix or not words:
        return None
    page_text = _page_text(words)
    if not page_text:
        return None
    ratio = SequenceMatcher(None, prefix, page_text[: max(len(prefix) * 3, PREFIX_CHARS)]).ratio()
    # partial 相当: prefix がページ内に部分含有されるかを別途評価。
    contained = SequenceMatcher(None, prefix, page_text).find_longest_match(
        0, len(prefix), 0, len(page_text)
    )
    coverage = contained.size / len(prefix) if prefix else 0.0
    score = max(ratio, coverage)
    if score < MATCH_THRESHOLD:
        return None
    x0 = min(w.x0 for w in words)
    y0 = min(w.y0 for w in words)
    x1 = max(w.x1 for w in words)
    y1 = max(w.y1 for w in words)
    return score, [x0, y0, x1, y1]


def sync_block_positions(
    sections: list[Section],
    pages: list[list[PdfWord]] | None = None,
) -> PdfSyncResult:
    """各ブロックに page/bbox を導出する。

    Args:
        sections: 構造化ドキュメントのセクションツリー。
        pages: ページごとの単語 bbox 列(1 起点順)。None なら全ブロック NULL(素通し)。

    Returns:
        PdfSyncResult(positions + 同期率)。
    """
    targets = _iter_sync_blocks(sections)
    result = PdfSyncResult(target=len(targets))
    if not pages:
        result.positions = [BlockPosition(block_id=b.id) for b in targets]
        return result

    cursor = 0  # 単調前進(直前マッチ以降のページのみ探索)
    for block in targets:
        prefix = _normalize(block_to_plain(block))[:PREFIX_CHARS]
        found: BlockPosition | None = None
        for page_idx in range(cursor, len(pages)):
            hit = _match_prefix(prefix, pages[page_idx])
            if hit is not None:
                _score, bbox = hit
                found = BlockPosition(block_id=block.id, page=page_idx + 1, bbox=bbox)
                cursor = page_idx
                result.matched += 1
                break
        result.positions.append(found or BlockPosition(block_id=block.id))
    return result
