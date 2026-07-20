"""PDF 注釈埋め込みと対訳結合(Feature S3・Task 11。設計 決定 C-1 / D-1)。

- :func:`embed_block_annotations` — ``block_search_index.page/bbox`` を使って原文 PDF に
  ブロック粒度のハイライト矩形とコメント(popup=text annotation)を埋め込む。bbox を持たない
  注釈は :class:`AnnotationEmbedResult.skipped` に数え、黙って落とさない(P3)。
- :func:`interleave_bilingual_pdf` — 原文 PDF と訳文 PDF をページ交互(原文 p1 → 訳 p1 → …)に
  結合した対訳 PDF を返す。

いずれも ``fitz``(PyMuPDF)のみに依存し、外部ネットワークを一切使わない純関数。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import fitz

# ハイライト色(注釈の color ラベル → RGB。ck_annotations_color の 4 値に対応する近似値)。
_HIGHLIGHT_COLORS: dict[str, tuple[float, float, float]] = {
    "important": (1.0, 0.86, 0.4),  # 黄
    "question": (0.6, 0.82, 1.0),  # 青
    "idea": (0.7, 0.9, 0.6),  # 緑
    "term": (0.9, 0.75, 1.0),  # 紫
}
_DEFAULT_HIGHLIGHT_COLOR = (1.0, 0.95, 0.6)


@dataclass(frozen=True)
class BlockAnnotation:
    """1 ブロックへの注釈(DB 非依存の値オブジェクト)。

    - ``page``/``bbox`` は ``block_search_index`` 由来(1 起点ページ・PDF 座標系 bbox)。
      いずれかが欠けていれば「配置不能」として skip する。
    - ``comment`` があれば popup(text annotation)を追加する。
    """

    block_id: str
    kind: str
    color: str | None
    comment: str | None
    page: int | None
    bbox: list[float] | None


@dataclass
class AnnotationEmbedResult:
    embedded: int = 0
    skipped: int = 0
    skipped_block_ids: list[str] = field(default_factory=list)


def _is_placeable(annotation: BlockAnnotation, page_count: int) -> bool:
    if annotation.page is None or annotation.bbox is None:
        return False
    if len(annotation.bbox) != 4:
        return False
    # ページは 1 起点。範囲外は配置不能。
    return 1 <= annotation.page <= page_count


def embed_block_annotations(
    pdf_bytes: bytes, annotations: list[BlockAnnotation]
) -> tuple[bytes, AnnotationEmbedResult]:
    """原文 PDF にブロック粒度のハイライト + コメントを埋め込んで返す。

    Returns:
        (出力 PDF バイト列, 埋め込み/スキップ集計)。
    """
    result = AnnotationEmbedResult()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_count = doc.page_count
        for annotation in annotations:
            if not _is_placeable(annotation, page_count):
                result.skipped += 1
                result.skipped_block_ids.append(annotation.block_id)
                continue
            assert annotation.page is not None and annotation.bbox is not None
            page = doc[annotation.page - 1]
            rect = fitz.Rect(*annotation.bbox)
            highlight = page.add_highlight_annot(rect)
            color = _HIGHLIGHT_COLORS.get(
                annotation.color or "", _DEFAULT_HIGHLIGHT_COLOR
            )
            highlight.set_colors(stroke=color)
            highlight.update()

            comment = (annotation.comment or "").strip()
            if comment:
                # 付箋(text annotation)を矩形の右上に置き、本文をポップアップに格納する。
                point = fitz.Point(rect.x1, rect.y0)
                note = page.add_text_annot(point, comment)
                note.update()
            result.embedded += 1
        out: bytes = doc.tobytes()
        return out, result
    finally:
        doc.close()


def interleave_bilingual_pdf(source_pdf: bytes, translated_pdf: bytes) -> bytes:
    """原文 PDF と訳文 PDF をページ交互に結合した対訳 PDF を返す。

    ページ順は ``source-1, translated-1, source-2, translated-2, ...``。ページ数が異なる場合は
    多い方のページ数まで繰り返し、存在しない側は単に飛ばす。
    """
    merged = fitz.open()
    source = fitz.open(stream=source_pdf, filetype="pdf")
    translated = fitz.open(stream=translated_pdf, filetype="pdf")
    try:
        page_total = max(source.page_count, translated.page_count)
        for index in range(page_total):
            if index < source.page_count:
                merged.insert_pdf(source, from_page=index, to_page=index)
            if index < translated.page_count:
                merged.insert_pdf(translated, from_page=index, to_page=index)
        out: bytes = merged.tobytes()
        return out
    finally:
        merged.close()
        source.close()
        translated.close()
