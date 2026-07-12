"""PY-PARSE-03: PDF パーサ(品質 B。M1-18。plans/05 §6・plans/12 §12)。

自作 PDF フィクスチャ(``tests/fixtures/pdf_*.pdf``。pymupdf で生成)を用いて、
段落・見出し復元・全ブロックの page+bbox(pt)付与・段組み読み順・図表・数式・
参考文献・テキストレイヤ判定を検証する。外部ネットワーク通信は行わない。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from alinea_core.document.blocks import Block, DocumentContent
from alinea_core.ingest.bib_estimate import BibEstimate, estimate_bibliography
from alinea_core.parsing import pdf_parser as pdf_parser_module
from alinea_core.parsing.pdf_parser import (
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
    assert PARSER_VERSION == "pdf-1.2.4"
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


def test_section_ids_are_unique_across_the_entire_heading_tree() -> None:
    """A repeated top-level number must not collide with a later dotted child number."""
    parser = pdf_parser_module._PdfParser(b"%PDF- synthetic")
    parser._open_heading("2", "First section two", 1, [10, 10, 100, 20])
    parser._open_heading("2", "Repeated section two", 1, [10, 30, 100, 40])
    parser._open_heading("2.2", "Nested subsection", 1, [20, 50, 100, 60])

    pending = list(parser.top_sections)
    section_ids: list[str] = []
    while pending:
        section = pending.pop()
        section_ids.append(section.id)
        pending.extend(section.sections)

    assert len(section_ids) == len(set(section_ids))


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


def _vector_drawing_pdf(*, with_caption: bool) -> bytes:
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    page.insert_text(
        fitz.Point(72, 72),
        "This paragraph provides enough extractable text for PDF parsing and layout analysis.",
        fontsize=10,
    )
    page.draw_rect(fitz.Rect(150, 130, 270, 220), color=(0, 0, 0), width=2)
    page.draw_rect(fitz.Rect(340, 130, 460, 220), color=(0, 0, 0), width=2)
    page.draw_line(fitz.Point(270, 175), fitz.Point(340, 175), color=(0, 0, 0), width=2)
    page.draw_line(fitz.Point(330, 168), fitz.Point(340, 175), color=(0, 0, 0), width=2)
    page.draw_line(fitz.Point(330, 182), fitz.Point(340, 175), color=(0, 0, 0), width=2)
    if with_caption:
        page.insert_text(
            fitz.Point(72, 250),
            "Figure 1: A vector-only flow diagram rendered without an embedded image.",
            fontsize=10,
        )
    page.insert_text(
        fitz.Point(72, 300),
        "The discussion continues after the display with another complete sentence.",
        fontsize=10,
    )
    payload = document.tobytes()
    document.close()
    return payload


def test_vector_only_figure_is_cropped_from_pdf_drawing_commands() -> None:
    doc = parse_pdf(_vector_drawing_pdf(with_caption=True))

    assert len(doc.figures) == 1
    figure = doc.figures[0]
    assert figure.number == "1"
    assert figure.id in doc.figure_images
    assert doc.figure_images[figure.id].startswith(b"\x89PNG")
    assert doc.stats["figure_caption_match_rate"] == 1.0


def test_uncaptioned_vector_drawing_is_not_misclassified_as_a_figure() -> None:
    doc = parse_pdf(_vector_drawing_pdf(with_caption=False))

    assert doc.figures == []


def test_table_caption_claims_nearby_raster_region_when_cell_detection_fails() -> None:
    class Pixmap:
        def tobytes(self, _kind: str) -> bytes:
            return b"\x89PNG\r\n\x1a\nsynthetic"

    class Page:
        rect = SimpleNamespace(width=612.0, height=792.0)

        def get_pixmap(self, **_kwargs: object) -> Pixmap:
            return Pixmap()

    parser = pdf_parser_module._PdfParser(b"%PDF- synthetic")
    caption = _Line(1, "Table 1. Comparison of methods.", 90, 70, 500, 83, 10, False)
    match = pdf_parser_module._CAPTION_RE.match(caption.text)
    assert match is not None
    region = pdf_parser_module._Region(page=1, bbox=[85, 100, 510, 300])

    parser._handle_caption(Page(), 1, [caption], match, [region], [], [caption])

    table = parser.intro.blocks[0]
    assert table.type == "table"
    assert table.raw is None
    assert table.bbox == region.bbox
    assert region.claimed is True
    assert parser._pending_images == [(table, b"\x89PNG\r\n\x1a\nsynthetic")]


def test_figure_detection_does_not_expand_or_hash_image_xrefs() -> None:
    calls: list[dict[str, object]] = []

    class Page:
        rect = SimpleNamespace(width=612.0, height=792.0)

        def get_image_info(self, **kwargs: object) -> list[object]:
            calls.append(kwargs)
            return []

        def get_drawings(self) -> list[object]:
            return []

    assert pdf_parser_module._detect_figure_regions(Page(), 1) == []
    assert calls == [{}]


def test_complex_vector_form_preflight_skips_expensive_graphics_walk() -> None:
    class Parent:
        def xref_get_key(self, xref: int, key: str) -> tuple[str, str]:
            assert key == "Length"
            return ("int", str(900_000 if xref == 20 else 100))

    class Page:
        parent = Parent()

        def get_contents(self) -> list[int]:
            return [10]

        def get_xobjects(self) -> list[tuple[object, ...]]:
            return [(20, "Form1", 0, (0.0, 0.0, 100.0, 100.0))]

        def get_image_info(self, **_kwargs: object) -> list[object]:
            raise AssertionError("complex vector form must skip image expansion")

        def get_drawings(self) -> list[object]:
            raise AssertionError("complex vector form must skip drawing expansion")

    page = Page()

    assert pdf_parser_module._page_has_complex_graphics_stream(page) is True
    assert pdf_parser_module._detect_figure_regions(page, 1, inspect_graphics=False) == []


def test_caption_run_joins_label_and_text_split_on_the_same_visual_line() -> None:
    lines = [
        _Line(1, "Fig. 2.", 312, 238, 335, 246, 8, False),
        _Line(
            1, "Popularity paradox: share versus expected win rate", 343, 238, 560, 246, 8, False
        ),
        _Line(1, "normalized across all methods.", 312, 247, 455, 255, 8, False),
        _Line(1, "Body text in another column.", 49, 249, 280, 259, 10, False),
    ]

    run, end = pdf_parser_module._collect_caption_run(lines, 0, 10, 10)

    assert [line.text for line in run] == [line.text for line in lines[:3]]
    assert end == 3


def test_text_only_diagram_region_is_inferred_above_its_caption() -> None:
    diagram = [
        _Line(1, "67.3%", 163, 64, 184, 71, 7, False),
        _Line(1, "Raging Bolt  ---->  Mega Absol", 101, 67, 248, 77, 10, False),
        _Line(1, "51.0%              62.1%", 113, 79, 235, 90, 10, False),
        _Line(1, "Dragapult  <----  Grimmsnarl", 106, 94, 247, 105, 10, False),
    ]
    right_column_body = [
        _Line(
            1,
            "A full prose line in the other column should not be cropped.",
            312,
            69,
            563,
            79,
            10,
            False,
        )
    ]
    caption = [_Line(1, "Fig. 3. Directed interaction motif.", 49, 118, 198, 126, 8, False)]

    region = pdf_parser_module._infer_text_figure_region(
        caption,
        [*diagram, *right_column_body, *caption],
        page_no=1,
        page_width=612,
        line_height=10,
    )

    assert region is not None
    assert region.from_vector_graphics is True
    assert region.bbox[0] < 110
    assert region.bbox[2] > 240
    assert region.bbox[3] < caption[0].y0


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
    assert stats["extracted_chars"] >= stats["blocks"]


def test_extract_pdf_text_evidence_is_bounded_and_matches_parser_stats() -> None:
    data = _load("pdf_quality_b_sample.pdf")

    evidence = pdf_parser_module.extract_pdf_text_evidence(data)
    parsed = parse_pdf(data)

    assert evidence.pages == parsed.stats["pages"]
    assert evidence.extracted_chars == parsed.stats["extracted_chars"]
    assert len(evidence.text) >= evidence.extracted_chars


def test_count_pdf_text_evidence_retains_only_bounded_counts() -> None:
    data = _load("pdf_quality_b_sample.pdf")

    evidence = pdf_parser_module.count_pdf_text_evidence(data)
    parsed = parse_pdf(data)

    assert evidence.pages == parsed.stats["pages"]
    assert evidence.extracted_chars == parsed.stats["extracted_chars"]
    assert not hasattr(evidence, "text")


def test_extract_page_lines_removes_unsafe_pdf_font_controls() -> None:
    """Broken ToUnicode maps must not leak C0 controls into IR / IPC JSON."""

    page = SimpleNamespace(
        get_text=lambda *_args, **_kwargs: {
            "blocks": [
                {
                    "type": 0,
                    "lines": [
                        {
                            "spans": [
                                {
                                    "text": "visible\x00 text\x11",
                                    "bbox": [10.0, 20.0, 100.0, 32.0],
                                    "size": 10.0,
                                    "flags": 0,
                                }
                            ]
                        },
                        {
                            "spans": [
                                {
                                    "text": "\x01\x02",
                                    "bbox": [10.0, 40.0, 20.0, 52.0],
                                    "size": 10.0,
                                    "flags": 0,
                                }
                            ]
                        },
                    ],
                }
            ]
        }
    )

    lines = pdf_parser_module._extract_page_lines(page, 1)

    assert [line.text for line in lines] == ["visible text"]


def test_pdf_parser_rejects_figure_count_and_bytes_before_pending_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = pdf_parser_module._PdfParser(b"%PDF- synthetic")
    block = Block(id="", type="figure", page=1, bbox=[0, 0, 10, 10])
    monkeypatch.setattr(pdf_parser_module, "MAX_PDF_FIGURE_IMAGES", 1, raising=False)

    parser._append_pending_image(block, b"png")
    with pytest.raises(PdfParseError) as count_error:
        parser._append_pending_image(block, b"png")
    assert count_error.value.kind == "pdf_figure_limit"

    parser = pdf_parser_module._PdfParser(b"%PDF- synthetic")
    monkeypatch.setattr(pdf_parser_module, "MAX_PDF_SINGLE_FIGURE_BYTES", 2, raising=False)
    with pytest.raises(PdfParseError) as bytes_error:
        parser._append_pending_image(block, b"png")
    assert bytes_error.value.kind == "pdf_figure_bytes_limit"


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


def test_pdfplumber_table_fallback_reuses_document_and_releases_page_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePlumberPage:
        def __init__(self) -> None:
            self.closed = 0

        def find_tables(self) -> list[object]:
            return []

        def close(self) -> None:
            self.closed += 1

    class FakePlumberDocument:
        def __init__(self) -> None:
            self.pages = [FakePlumberPage(), FakePlumberPage()]
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    document = FakePlumberDocument()
    opened: list[object] = []

    def open_document(_stream: object) -> FakePlumberDocument:
        opened.append(_stream)
        return document

    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=open_document))
    fallback = pdf_parser_module._PdfPlumberTableFallback(b"%PDF synthetic")
    primary_page = SimpleNamespace(
        find_tables=lambda: SimpleNamespace(tables=[]),
    )

    assert pdf_parser_module._detect_table_candidates(primary_page, 1, fallback) == []
    assert pdf_parser_module._detect_table_candidates(primary_page, 2, fallback) == []
    fallback.close()

    assert len(opened) == 1
    assert [page.closed for page in document.pages] == [1, 1]
    assert document.closed == 1


def test_optional_table_detectors_never_abort_the_document() -> None:
    class BrokenPrimaryPage:
        def find_tables(self) -> object:
            raise LookupError("third-party table detector failed")

    fallback = SimpleNamespace(find=lambda _page_no: [])

    assert pdf_parser_module._detect_table_candidates(BrokenPrimaryPage(), 1, fallback) == []


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
