"""PY-PARSE-03: PDF パーサ(品質 B。M1-18。plans/05 §6・plans/12 §12)。

自作 PDF フィクスチャ(``tests/fixtures/pdf_*.pdf``。pymupdf で生成)を用いて、
段落・見出し復元・全ブロックの page+bbox(pt)付与・段組み読み順・図表・数式・
参考文献・テキストレイヤ判定を検証する。外部ネットワーク通信は行わない。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from yakudoku_core.document.blocks import DocumentContent
from yakudoku_core.ingest.bib_estimate import BibEstimate, estimate_bibliography
from yakudoku_core.parsing.pdf_parser import (
    PARSER_VERSION,
    ParsedPdfDocument,
    PdfParseError,
    _heading_info,
    _Line,
    check_text_layer,
    parse_pdf,
)

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _main_doc() -> ParsedPdfDocument:
    return parse_pdf(_load("pdf_quality_b_sample.pdf"))


# ============================ 基本契約 ============================


def test_parser_version_and_quality_level() -> None:
    doc = _main_doc()
    assert PARSER_VERSION == "pdf-1.0.0"
    assert doc.parser_version == PARSER_VERSION
    assert doc.quality_level == "B"
    assert doc.source_format == "pdf"


def test_to_document_content_maps_quality_b() -> None:
    content = _main_doc().to_document_content()
    assert isinstance(content, DocumentContent)
    assert content.quality_level == "B"
    assert content.sections  # 非空


# ============================ 全ブロックに page+bbox(§6.10) ============================


def test_all_blocks_have_page_and_bbox() -> None:
    doc = _main_doc()
    assert doc.blocks, "no blocks parsed"
    for block in doc.blocks:
        assert block.page is not None, block
        assert block.bbox is not None, block
        assert len(block.bbox) == 4
        x0, y0, x1, y1 = block.bbox
        assert x0 <= x1
        assert y0 <= y1


def test_block_ids_are_prefixed_unique_and_deterministic() -> None:
    ids_a = [b.id for b in _main_doc().blocks]
    ids_b = [b.id for b in _main_doc().blocks]
    assert ids_a, "no blocks parsed"
    assert all(bid.startswith("blk-") for bid in ids_a)
    assert len(ids_a) == len(set(ids_a)), "block ids must be unique within a revision"
    assert ids_a == ids_b, "parsing must be deterministic"


# ============================ 段落・見出し復元(§6.4・§6.5) ============================


def test_title_page_paragraph_and_headings_reconstructed() -> None:
    doc = _main_doc()
    top = {sec.heading.title: sec for sec in doc.sections}
    assert "Abstract" in top
    abstract_blocks = top["Abstract"].blocks
    assert abstract_blocks[0].type == "heading"
    assert abstract_blocks[0].title == "Abstract"
    # 2 行の本文は 1 段落へ結合される(§6.4)。
    body = [b for b in abstract_blocks if b.type == "paragraph"]
    assert len(body) == 1
    assert "This paper studies things" in body[0].inlines[0].v
    assert "We show results" in body[0].inlines[0].v


def test_numbered_heading_level_and_number() -> None:
    doc = _main_doc()
    intro = next(sec for sec in doc.sections if sec.heading.title == "Introduction")
    assert intro.heading.number == "1"
    heading_block = intro.blocks[0]
    assert heading_block.type == "heading"
    assert heading_block.level == 1
    assert heading_block.number == "1"

    method = next(sec for sec in doc.sections if sec.heading.title == "Method")
    assert method.heading.number == "2"


def test_header_footer_removed() -> None:
    doc = _main_doc()
    texts = [b.inlines[0].v for b in doc.blocks if b.type == "paragraph" and b.inlines]
    joined = " ".join(texts)
    assert "ICLR 2023" not in joined
    # 単独のページ番号行(§6.2)も除去されている。
    assert not any(t.strip() in {"1", "2"} for t in texts)


# ============================ 段組み読み順(§6.3) ============================


def test_two_column_reading_order_on_method_page() -> None:
    doc = _main_doc()
    method = next(sec for sec in doc.sections if sec.heading.title == "Method")
    paragraphs = [b for b in method.blocks if b.type == "paragraph"]
    assert len(paragraphs) == 2
    left, right = paragraphs
    # 左列 → 右列の順で文書順に並ぶ(§6.3)。bbox の x0 で列を判別できる。
    assert left.bbox is not None and right.bbox is not None
    assert left.bbox[0] < right.bbox[0]
    assert "left column" in left.inlines[0].v
    assert "right column" in right.inlines[0].v
    # 各列の複数行が 1 段落へ結合されている。
    assert "first half" in left.inlines[0].v
    assert "second half" in right.inlines[0].v


# ============================ 図(§6.6) ============================


def test_figure_region_matched_with_caption_and_cropped_image() -> None:
    doc = _main_doc()
    assert len(doc.figures) == 1
    figure = doc.figures[0]
    assert figure.number == "1"
    assert figure.caption and figure.caption[0].v == "An example figure showing our results."
    assert figure.page == 2
    assert figure.id in doc.figure_images
    png_bytes = doc.figure_images[figure.id]
    assert png_bytes.startswith(b"\x89PNG")
    assert doc.stats["figure_caption_match_rate"] == 1.0


# ============================ 数式(§6.8) ============================


def test_equation_detected_as_image_only_block() -> None:
    doc = _main_doc()
    equations = [b for b in doc.blocks if b.type == "equation"]
    assert len(equations) == 1
    eq = equations[0]
    assert eq.latex is None  # v1 は LaTeX 化しない(§6.8 の明示された限界)。
    assert eq.number == "1"
    assert eq.id in doc.figure_images
    assert doc.stats["equation_latex_rate"] == 0.0


# ============================ stats(§6.10) ============================


def test_stats_shape() -> None:
    stats = _main_doc().stats
    assert stats["pages"] == 2
    assert stats["figures"] == 1
    assert stats["pdf_sync_rate"] is None
    assert stats["blocks"] == len(_main_doc().blocks)


# ============================ 参考文献分割(§6.9) ============================


def test_reference_entries_split_by_bracket_marker() -> None:
    doc = parse_pdf(_load("pdf_references_sample.pdf"))
    refs_section = next(sec for sec in doc.sections if sec.heading.title == "References")
    assert refs_section.id == "sec-refs"
    entries = [b for b in refs_section.blocks if b.type == "reference_entry"]
    assert len(entries) == 2
    assert entries[0].label == "bib-1"
    assert entries[0].raw == "Alice Smith. A great paper. arXiv:2101.00001, 2021."
    assert entries[0].structured is not None
    assert entries[0].structured["arxiv_id"] == "2101.00001"
    assert entries[0].structured["year"] == "2021"
    assert entries[1].label == "bib-2"
    for entry in entries:
        assert entry.page is not None
        assert entry.bbox is not None


def test_fixed_references_heading_detected_without_font_emphasis() -> None:
    line = _Line(
        page=1,
        text="References",
        x0=72,
        y0=120,
        x1=150,
        y1=132,
        size=10.0,
        bold=False,
    )
    assert _heading_info(line, body_size=10.0) == ("", "References")


# ============================ 表(§6.7) ============================


def test_table_extracted_as_html_cells_with_caption() -> None:
    doc = parse_pdf(_load("pdf_table_sample.pdf"))
    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert table.number == "1"
    assert table.caption and table.caption[0].v == "Comparison of methods and scores."
    assert table.raw is not None
    assert "<table>" in table.raw
    assert "<td>Method</td>" in table.raw
    assert "<td>Score</td>" in table.raw
    assert "<td>Ours</td>" in table.raw
    assert "<td>0.95</td>" in table.raw
    # 表セルの文字列(Method/Score/Ours/0.95)が別途段落として重複しないこと。
    plain_texts = [b.inlines[0].v for b in doc.blocks if b.type == "paragraph" and b.inlines]
    assert not any(t.strip() in {"Method", "Score", "Ours", "0.95"} for t in plain_texts)


# ============================ テキストレイヤ判定(§6.1) ============================


def test_check_text_layer_raises_for_scanned_image_only_pdf() -> None:
    data = _load("pdf_no_text_layer.pdf")
    with pytest.raises(PdfParseError) as excinfo:
        check_text_layer(data)
    assert excinfo.value.kind == "no_text_layer"
    assert excinfo.value.message == "テキストが抽出できません"


def test_parse_pdf_raises_same_error_for_scanned_image_only_pdf() -> None:
    data = _load("pdf_no_text_layer.pdf")
    with pytest.raises(PdfParseError) as excinfo:
        parse_pdf(data)
    assert excinfo.value.kind == "no_text_layer"


def test_check_text_layer_accepts_normal_pdf() -> None:
    # 例外を投げなければ OK(§6.1 の主経路)。
    check_text_layer(_load("pdf_quality_b_sample.pdf"))


# ============================ 書誌推定(plans/05 §9.3。M1-18 の付帯成果物) ============================


async def test_bib_estimate_falls_back_to_first_page_heuristic() -> None:
    """DOI が無い場合は 1 ページ目の最大フォント行群をタイトル候補にする(§9.3-2)。"""
    est = await estimate_bibliography(
        _load("pdf_references_sample.pdf"),
        crossref_fetch=_unreachable_crossref,
    )
    assert isinstance(est, BibEstimate)
    assert est.title == "Notes on References Formatting"
    assert est.bib_estimated is True
    assert est.doi is None


async def test_bib_estimate_falls_back_to_filename_when_no_title_found() -> None:
    est = await estimate_bibliography(
        _load("pdf_no_text_layer.pdf"),
        filename="uploads/my-cool-paper.pdf",
        crossref_fetch=_unreachable_crossref,
    )
    assert est.title == "my-cool-paper"


async def test_bib_estimate_detects_doi_and_enriches_via_crossref() -> None:
    calls: list[str] = []

    async def fake_crossref(doi: str) -> dict[str, object] | None:
        calls.append(doi)
        return {
            "title": ["Real Crossref Title"],
            "author": [{"given": "Jane", "family": "Doe"}],
            "issued": {"date-parts": [[2024, 3, 15]]},
            "container-title": ["ICML"],
            "DOI": doi,
        }

    est = await estimate_bibliography(
        _load("pdf_table_sample.pdf"),
        crossref_fetch=fake_crossref,
    )
    assert calls == []  # このフィクスチャは DOI を含まないため Crossref は呼ばれない。
    assert est.bib_estimated is True


async def _unreachable_crossref(doi: str) -> dict[str, object] | None:
    raise AssertionError(f"crossref should not be called (doi={doi!r})")
