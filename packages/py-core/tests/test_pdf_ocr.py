"""PDF OCR parsing keeps one OCR text page per page and reuses normal layout logic."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import fitz
import pytest
from alinea_core.parsing import pdf_parser as pdf_parser_module
from alinea_core.parsing.pdf_parser import PdfParseError, parse_pdf

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _reset_ocr_readiness_cache() -> Any:
    clear = getattr(pdf_parser_module, "clear_pdf_ocr_readiness_cache", None)
    if clear is not None:
        clear()
    yield
    if clear is not None:
        clear()


def _load(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _scan_background_pdf(*, pages: int = 1, tiled: bool = False) -> bytes:
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    image.clear_with(245)
    png = image.tobytes("png")
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page(width=300, height=400)
        if tiled:
            for rect in (
                fitz.Rect(10, 10, 150, 200),
                fitz.Rect(150, 10, 290, 200),
                fitz.Rect(10, 200, 150, 390),
                fitz.Rect(150, 200, 290, 390),
            ):
                page.insert_image(rect, stream=png)
        else:
            page.insert_image(fitz.Rect(10, 10, 290, 390), stream=png)
    data = document.tobytes()
    document.close()
    return data


def _gapped_tiled_scan_pdf() -> bytes:
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    image.clear_with(245)
    png = image.tobytes("png")
    document = fitz.open()
    page = document.new_page(width=300, height=400)
    for rect in (
        fitz.Rect(5, 5, 140, 190),
        fitz.Rect(160, 5, 295, 190),
        fitz.Rect(5, 210, 140, 395),
        fitz.Rect(160, 210, 295, 395),
    ):
        page.insert_image(rect, stream=png)
    data = document.tobytes()
    document.close()
    return data


def _scan_visual_pdf(*visual_bboxes: tuple[float, float, float, float]) -> bytes:
    canvas = fitz.open()
    canvas_page = canvas.new_page(width=300, height=400)
    canvas_page.draw_rect(canvas_page.rect, color=None, fill=(0.96, 0.96, 0.96))
    for bbox in visual_bboxes:
        rect = fitz.Rect(*bbox)
        canvas_page.draw_rect(rect, color=(0.1, 0.1, 0.1), fill=(0.35, 0.35, 0.35), width=2)
        canvas_page.draw_line(rect.top_left, rect.bottom_right, color=(0.05, 0.05, 0.05), width=3)
        canvas_page.draw_line(rect.bottom_left, rect.top_right, color=(0.05, 0.05, 0.05), width=3)
    png = canvas_page.get_pixmap(dpi=72, alpha=False).tobytes("png")
    canvas.close()

    document = fitz.open()
    page = document.new_page(width=300, height=400)
    page.insert_image(page.rect, stream=png)
    data = document.tobytes()
    document.close()
    return data


def _scan_table_grid_pdf() -> bytes:
    canvas = fitz.open()
    canvas_page = canvas.new_page(width=300, height=400)
    canvas_page.draw_rect(canvas_page.rect, color=None, fill=(0.96, 0.96, 0.96))
    table = fitz.Rect(45, 205, 255, 295)
    canvas_page.draw_rect(table, color=(0.05, 0.05, 0.05), width=2)
    for x in (115, 185):
        canvas_page.draw_line(
            fitz.Point(x, table.y0),
            fitz.Point(x, table.y1),
            color=(0.05, 0.05, 0.05),
            width=2,
        )
    for y in (235, 265):
        canvas_page.draw_line(
            fitz.Point(table.x0, y),
            fitz.Point(table.x1, y),
            color=(0.05, 0.05, 0.05),
            width=2,
        )
    png = canvas_page.get_pixmap(dpi=72, alpha=False).tobytes("png")
    canvas.close()

    document = fitz.open()
    page = document.new_page(width=300, height=400)
    page.insert_image(page.rect, stream=png)
    data = document.tobytes()
    document.close()
    return data


def _inset_scan_background_pdf() -> bytes:
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    image.clear_with(245)
    document = fitz.open()
    page = document.new_page(width=300, height=400)
    page.insert_image(
        fitz.Rect(34, 10, 266, 390),
        stream=image.tobytes("png"),
    )
    data = document.tobytes()
    document.close()
    return data


def _multi_page_raster_rect_pdf(
    rects: list[tuple[float, float, float, float]],
) -> bytes:
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    image.clear_with(245)
    png = image.tobytes("png")
    document = fitz.open()
    for bbox in rects:
        page = document.new_page(width=300, height=400)
        page.insert_image(fitz.Rect(*bbox), stream=png)
    data = document.tobytes()
    document.close()
    return data


def _smaller_image_pdf() -> bytes:
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    image.clear_with(90)
    document = fitz.open()
    page = document.new_page(width=300, height=400)
    page.insert_image(fitz.Rect(50, 115, 250, 235), stream=image.tobytes("png"))
    data = document.tobytes()
    document.close()
    return data


def _ocr_line(text: str, bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    return {
        "spans": [
            {
                "text": text,
                "bbox": list(bbox),
                "size": 10,
                "flags": 0,
            }
        ]
    }


def _install_ocr_pages(
    monkeypatch: pytest.MonkeyPatch,
    pages_lines: list[list[dict[str, Any]]],
) -> None:
    def fake_ocr(_page: fitz.Page, **_kwargs: Any) -> object:
        return object()

    def fake_get_text(page: fitz.Page, *args: Any, **_kwargs: Any) -> Any:
        lines = pages_lines[page.number]
        mode = args[0] if args else "text"
        if mode == "dict":
            return {"blocks": [{"type": 0, "lines": lines}] if lines else []}
        return "\n".join(line["spans"][0]["text"] for line in lines)

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", fake_ocr)
    monkeypatch.setattr(fitz.Page, "get_text", fake_get_text)
    monkeypatch.setattr(pdf_parser_module, "_detect_table_candidates", lambda *_args: [])


def _install_scan_ocr_text(
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_caption: bool = False,
) -> None:
    lines = [
        _ocr_line(
            "This first recovered paragraph contains substantive visible prose.",
            (30, 80, 270, 96),
        ),
        _ocr_line(
            "This second recovered paragraph provides enough independent content.",
            (30, 180, 270, 196),
        ),
    ]
    if include_caption:
        lines.append(
            _ocr_line(
                "Figure 1: A caption recovered from the scanned page.",
                (40, 315, 260, 331),
            )
        )
    _install_ocr_pages(monkeypatch, [lines])


def test_parse_pdf_threads_one_ocr_textpage_through_every_text_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_get_text = fitz.Page.get_text
    textpages: dict[int, Any] = {}
    ocr_calls: list[tuple[int, str, int, bool]] = []
    extraction_calls: list[tuple[int, Any]] = []

    def fake_ocr(page: fitz.Page, *, language: str, dpi: int, full: bool) -> Any:
        textpage = page.get_textpage()
        textpages[page.number] = textpage
        ocr_calls.append((page.number, language, dpi, full))
        return textpage

    def tracked_get_text(page: fitz.Page, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else "text"
        if mode in {"text", "dict"}:
            extraction_calls.append((page.number, kwargs.get("textpage")))
        return original_get_text(page, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", fake_ocr)
    monkeypatch.setattr(fitz.Page, "get_text", tracked_get_text)

    parsed = parse_pdf(
        _load("pdf_quality_b_sample.pdf"),
        use_ocr=True,
        ocr_language="eng",
    )

    assert ocr_calls == [(0, "eng", 200, True), (1, "eng", 200, True)]
    assert len(extraction_calls) == 4
    assert all(textpage is textpages[page_no] for page_no, textpage in extraction_calls)
    assert parsed.stats["ocr"] is True


def test_parse_pdf_releases_each_ocr_textpage_before_starting_the_next_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gc
    import weakref

    class TextPageSentinel:
        pass

    previous: weakref.ReferenceType[TextPageSentinel] | None = None
    calls: list[int] = []

    def fake_ocr(page: fitz.Page, **_kwargs: Any) -> TextPageSentinel:
        nonlocal previous
        gc.collect()
        assert previous is None or previous() is None
        textpage = TextPageSentinel()
        previous = weakref.ref(textpage)
        calls.append(page.number)
        return textpage

    def fake_get_text(_page: fitz.Page, *args: Any, **_kwargs: Any) -> Any:
        mode = args[0] if args else "text"
        if mode == "dict":
            return {"blocks": []}
        return "x" * 100

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", fake_ocr)
    monkeypatch.setattr(fitz.Page, "get_text", fake_get_text)
    monkeypatch.setattr(pdf_parser_module, "_detect_figure_regions", lambda *_args: [])
    monkeypatch.setattr(pdf_parser_module, "_detect_table_candidates", lambda *_args: [])

    parsed = parse_pdf(_load("pdf_quality_b_sample.pdf"), use_ocr=True)

    assert calls == [0, 1]
    assert parsed.stats["ocr"] is True


def test_parse_pdf_non_ocr_path_never_creates_or_threads_ocr_textpage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_get_text = fitz.Page.get_text
    seen_textpage_arguments: list[Any] = []

    def unexpected_ocr(_page: fitz.Page, **_kwargs: Any) -> Any:
        raise AssertionError("non-OCR parsing must not create an OCR text page")

    def tracked_get_text(page: fitz.Page, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else "text"
        if mode in {"text", "dict"}:
            seen_textpage_arguments.append(kwargs.get("textpage"))
        return original_get_text(page, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", unexpected_ocr)
    monkeypatch.setattr(fitz.Page, "get_text", tracked_get_text)

    parsed = parse_pdf(_load("pdf_quality_b_sample.pdf"))

    assert seen_textpage_arguments == [None, None, None, None]
    assert parsed.stats["ocr"] is False


def test_parse_pdf_non_ocr_rejects_empty_text_before_dict_layout_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_get_text = fitz.Page.get_text
    dict_calls: list[int] = []

    def empty_text_broken_dict(page: fitz.Page, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else "text"
        if mode == "dict":
            dict_calls.append(page.number)
            raise RuntimeError("layout extraction must not run without a text layer")
        if mode == "text":
            return ""
        return original_get_text(page, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_text", empty_text_broken_dict)

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_load("pdf_no_text_layer.pdf"))

    assert exc_info.value.kind == "no_text_layer"
    assert dict_calls == []


@pytest.mark.parametrize("use_ocr", [False, True])
def test_parse_pdf_rejects_page_bomb_before_text_or_ocr_extraction(
    monkeypatch: pytest.MonkeyPatch,
    use_ocr: bool,
) -> None:
    document = fitz.open()
    for _ in range(5_000):
        document.new_page(width=300, height=400)
    data = document.tobytes()
    document.close()

    def unexpected_text(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("page limit must run before text/layout extraction")

    def unexpected_ocr(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("page limit must run before OCR")

    monkeypatch.setattr(fitz.Page, "get_text", unexpected_text)
    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", unexpected_ocr)

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(data, use_ocr=use_ocr)

    assert exc_info.value.kind == "pdf_page_limit"


def test_parse_pdf_rejects_text_bomb_before_dict_layout_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dict_calls = 0

    def text_bomb(_page: fitz.Page, *args: Any, **_kwargs: Any) -> Any:
        nonlocal dict_calls
        mode = args[0] if args else "text"
        if mode == "dict":
            dict_calls += 1
            raise AssertionError("text limit must run before dict extraction")
        return "x" * 101

    monkeypatch.setattr(pdf_parser_module, "MAX_PDF_EXTRACTED_CHARS", 100, raising=False)
    monkeypatch.setattr(fitz.Page, "get_text", text_bomb)

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_load("pdf_no_text_layer.pdf"))

    assert exc_info.value.kind == "pdf_text_limit"
    assert dict_calls == 0


def test_parse_pdf_rejects_line_bomb_during_bounded_layout_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def bomb(_page: fitz.Page, *args: Any, **_kwargs: Any) -> Any:
        mode = args[0] if args else "text"
        if mode != "dict":
            return "x" * 100
        line = {
            "spans": [{"text": "visible prose", "bbox": [10, 10, 100, 20], "size": 10, "flags": 0}]
        }
        return {"blocks": [{"type": 0, "lines": [line, line]}]}

    monkeypatch.setattr(pdf_parser_module, "MAX_PDF_LAYOUT_LINES", 1, raising=False)
    monkeypatch.setattr(fitz.Page, "get_text", bomb)

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_load("pdf_no_text_layer.pdf"))

    assert exc_info.value.kind == "pdf_layout_limit"


def test_parse_pdf_rejects_page_geometry_before_text_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text_calls = 0

    def unexpected_text(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal text_calls
        text_calls += 1
        raise AssertionError("geometry limit must run before text extraction")

    monkeypatch.setattr(pdf_parser_module, "MAX_PDF_PAGE_DIMENSION", 100.0, raising=False)
    monkeypatch.setattr(fitz.Page, "get_text", unexpected_text)

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_load("pdf_no_text_layer.pdf"))

    assert exc_info.value.kind == "pdf_geometry_limit"
    assert text_calls == 0


def test_parse_pdf_ocr_retains_existing_pdf_figure_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_ocr(page: fitz.Page, *, language: str, dpi: int, full: bool) -> Any:
        assert (language, dpi, full) == ("eng", 200, True)
        return page.get_textpage()

    baseline = parse_pdf(_load("pdf_quality_b_sample.pdf"))
    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", fake_ocr)

    parsed = parse_pdf(_load("pdf_quality_b_sample.pdf"), use_ocr=True)

    assert parsed.figure_images == baseline.figure_images
    assert parsed.figure_images
    assert all(payload.startswith(b"\x89PNG") for payload in parsed.figure_images.values())


@pytest.mark.parametrize("tiled", [False, True])
def test_parse_pdf_ocr_excludes_dominant_scan_raster_from_semantic_figures(
    monkeypatch: pytest.MonkeyPatch,
    tiled: bool,
) -> None:
    _install_scan_ocr_text(monkeypatch)

    parsed = parse_pdf(_scan_background_pdf(tiled=tiled), use_ocr=True)

    assert parsed.stats["figures"] == 0
    assert parsed.figure_images == {}
    assert len([block for block in parsed.blocks if block.type == "paragraph"]) == 2


def test_parse_pdf_ocr_scan_raster_does_not_claim_recovered_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scan_ocr_text(monkeypatch, include_caption=True)

    parsed = parse_pdf(_scan_background_pdf(), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert len(figures) == 1
    assert figures[0].caption[0].v == "A caption recovered from the scanned page."
    assert figures[0].id not in parsed.figure_images


def test_parse_pdf_ocr_recovers_scanned_table_grid_as_image_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grid_bbox = (45.0, 205.0, 255.0, 295.0)
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "This first recovered paragraph contains substantive visible prose.",
                    (30, 80, 270, 96),
                ),
                _ocr_line(
                    "This second recovered paragraph ends before the scanned table.",
                    (30, 180, 270, 196),
                ),
                _ocr_line(
                    "Table 1: A comparison recovered beneath the scanned grid.",
                    (40, 315, 260, 331),
                ),
            ]
        ],
    )

    parsed = parse_pdf(_scan_table_grid_pdf(), use_ocr=True)

    tables = [block for block in parsed.blocks if block.type == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table.raw is None
    assert table.id in parsed.figure_images
    assert parsed.figure_images[table.id].startswith(b"\x89PNG")
    assert table.bbox is not None
    retained_width = max(0.0, min(table.bbox[2], grid_bbox[2]) - max(table.bbox[0], grid_bbox[0]))
    retained_height = max(
        0.0,
        min(table.bbox[3], grid_bbox[3]) - max(table.bbox[1], grid_bbox[1]),
    )
    assert retained_width / (grid_bbox[2] - grid_bbox[0]) >= 0.80
    assert retained_height / (grid_bbox[3] - grid_bbox[1]) >= 0.80
    assert table.bbox[3] < 315
    assert parsed.stats["figures"] == 0
    assert parsed.stats["tables"] == 1


def test_parse_pdf_ocr_recovers_bounded_scan_crop_above_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scan_ocr_text(monkeypatch, include_caption=True)

    parsed = parse_pdf(_scan_visual_pdf((50, 215, 250, 295)), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert len(figures) == 1
    figure = figures[0]
    assert figure.caption[0].v == "A caption recovered from the scanned page."
    assert figure.id in parsed.figure_images
    assert parsed.figure_images[figure.id].startswith(b"\x89PNG")
    assert figure.bbox is not None
    assert 0 <= figure.bbox[0] < figure.bbox[2] <= 300
    assert 0 <= figure.bbox[1] < figure.bbox[3] < 315
    assert (figure.bbox[2] - figure.bbox[0]) * (figure.bbox[3] - figure.bbox[1]) < 0.60 * 300 * 400


def test_parse_pdf_ocr_short_centered_caption_retains_wide_visual_extent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual_bbox = (20.0, 205.0, 280.0, 295.0)
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "This first recovered paragraph contains substantive visible prose.",
                    (30, 80, 270, 96),
                ),
                _ocr_line(
                    "This second recovered paragraph ends immediately before the visual.",
                    (30, 180, 270, 196),
                ),
                _ocr_line("Figure 1: Wide result.", (115, 315, 185, 331)),
            ]
        ],
    )

    parsed = parse_pdf(_scan_visual_pdf(visual_bbox), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert len(figures) == 1
    crop = figures[0].bbox
    assert crop is not None
    retained_width = max(0.0, min(crop[2], visual_bbox[2]) - max(crop[0], visual_bbox[0]))
    retained_height = max(0.0, min(crop[3], visual_bbox[3]) - max(crop[1], visual_bbox[1]))
    assert retained_width / (visual_bbox[2] - visual_bbox[0]) >= 0.80
    assert retained_height / (visual_bbox[3] - visual_bbox[1]) >= 0.80
    assert crop[3] < 315
    assert figures[0].id in parsed.figure_images


def test_parse_pdf_ocr_centered_caption_unions_separated_figure_panels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    left_panel = (25.0, 210.0, 135.0, 290.0)
    right_panel = (165.0, 210.0, 275.0, 290.0)
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "This first recovered paragraph contains substantive visible prose.",
                    (30, 80, 270, 96),
                ),
                _ocr_line(
                    "This second recovered paragraph ends before the two-panel figure.",
                    (30, 180, 270, 196),
                ),
                _ocr_line(
                    "Figure 1: A centered caption describes both separated panels.",
                    (65, 315, 235, 331),
                ),
            ]
        ],
    )

    parsed = parse_pdf(_scan_visual_pdf(left_panel, right_panel), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert len(figures) == 1
    crop = figures[0].bbox
    assert crop is not None
    for panel in (left_panel, right_panel):
        retained_width = max(0.0, min(crop[2], panel[2]) - max(crop[0], panel[0]))
        retained_height = max(0.0, min(crop[3], panel[3]) - max(crop[1], panel[1]))
        assert retained_width / (panel[2] - panel[0]) >= 0.80
        assert retained_height / (panel[3] - panel[1]) >= 0.80
    assert 0 <= crop[0] < crop[2] <= 300
    assert 0 <= crop[1] < crop[3] < 315
    assert (crop[2] - crop[0]) * (crop[3] - crop[1]) <= 0.58 * 300 * 400
    assert figures[0].id in parsed.figure_images
    assert len(parsed.figure_images) == 1


def test_parse_pdf_ocr_panel_union_excludes_distant_unrelated_visual_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unrelated_noise = (20.0, 115.0, 80.0, 155.0)
    left_panel = (25.0, 210.0, 135.0, 290.0)
    right_panel = (165.0, 210.0, 275.0, 290.0)
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "Recovered page text is masked before raster components are grouped.",
                    (30, 80, 270, 96),
                ),
                _ocr_line(
                    "Figure 1: This caption belongs to the aligned lower panels.",
                    (65, 315, 235, 331),
                ),
            ]
        ],
    )

    parsed = parse_pdf(
        _scan_visual_pdf(unrelated_noise, left_panel, right_panel),
        use_ocr=True,
    )

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert len(figures) == 1
    crop = figures[0].bbox
    assert crop is not None
    assert crop[1] > unrelated_noise[3]
    assert crop[0] <= left_panel[0] and crop[2] >= right_panel[2]


@pytest.mark.parametrize(
    "figure_label",
    [
        "Accuracy 90 percent",
        "Accuracy across all validation categories remains above ninety percent",
    ],
)
def test_parse_pdf_ocr_figure_label_does_not_truncate_crop_or_leak_as_paragraph(
    monkeypatch: pytest.MonkeyPatch,
    figure_label: str,
) -> None:
    visual_bbox = (75.0, 210.0, 225.0, 295.0)
    first_prose = "This first recovered paragraph contains substantive visible prose."
    second_prose = "This second recovered paragraph ends before the complete visual region."
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(first_prose, (30, 80, 270, 96)),
                _ocr_line(second_prose, (30, 180, 270, 196)),
                _ocr_line(figure_label, (90, 255, 210, 267)),
                _ocr_line(
                    "Figure 1: The complete chart remains above this caption.",
                    (40, 315, 260, 331),
                ),
            ]
        ],
    )

    parsed = parse_pdf(_scan_visual_pdf(visual_bbox), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert len(figures) == 1
    crop = figures[0].bbox
    assert crop is not None
    retained_height = max(0.0, min(crop[3], visual_bbox[3]) - max(crop[1], visual_bbox[1]))
    assert retained_height / (visual_bbox[3] - visual_bbox[1]) >= 0.80
    paragraph_text = " ".join(
        inline.v for block in parsed.blocks if block.type == "paragraph" for inline in block.inlines
    )
    assert first_prose in paragraph_text
    assert second_prose in paragraph_text
    assert figure_label not in paragraph_text
    assert parsed.stats["extracted_chars"] >= len(first_prose + second_prose + figure_label)


def test_parse_pdf_ocr_two_column_captions_claim_distinct_side_by_side_visuals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    left_visual = (20.0, 180.0, 135.0, 260.0)
    right_visual = (165.0, 180.0, 280.0, 260.0)
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "Substantive recovered prose establishes the left text column.",
                    (20, 50, 135, 66),
                ),
                _ocr_line("Figure 1: Left visual.", (20, 275, 135, 291)),
                _ocr_line(
                    "Substantive recovered prose establishes the right text column.",
                    (165, 50, 280, 66),
                ),
                _ocr_line("Figure 2: Right visual.", (165, 275, 280, 291)),
            ]
        ],
    )

    parsed = parse_pdf(
        _scan_visual_pdf(left_visual, right_visual),
        use_ocr=True,
    )

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert [figure.number for figure in figures] == ["1", "2"]
    assert figures[0].bbox is not None and figures[1].bbox is not None
    assert figures[0].bbox[2] <= 150 <= figures[1].bbox[0]
    assert all(figure.id in parsed.figure_images for figure in figures)


def test_parse_pdf_ocr_left_caption_promotes_continuous_full_width_visual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual_bbox = (20.0, 180.0, 280.0, 265.0)
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "Substantive recovered prose establishes the left text column.",
                    (20, 50, 135, 66),
                ),
                _ocr_line(
                    "A second left-column line confirms the inferred page layout.",
                    (20, 80, 135, 96),
                ),
                _ocr_line("Figure 1: Result.", (20, 280, 100, 296)),
                _ocr_line(
                    "Substantive recovered prose establishes the right text column.",
                    (165, 50, 280, 66),
                ),
                _ocr_line(
                    "A second right-column line confirms the inferred page layout.",
                    (165, 80, 280, 96),
                ),
            ]
        ],
    )

    parsed = parse_pdf(_scan_visual_pdf(visual_bbox), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert parsed.stats["columns"] == 2
    assert len(figures) == 1
    crop = figures[0].bbox
    assert crop is not None
    retained_width = max(0.0, min(crop[2], visual_bbox[2]) - max(crop[0], visual_bbox[0]))
    retained_height = max(0.0, min(crop[3], visual_bbox[3]) - max(crop[1], visual_bbox[1]))
    assert retained_width / (visual_bbox[2] - visual_bbox[0]) >= 0.80
    assert retained_height / (visual_bbox[3] - visual_bbox[1]) >= 0.80
    assert crop[3] < 280
    assert len(parsed.figure_images) == 1
    assert figures[0].id in parsed.figure_images


def test_parse_pdf_ocr_left_caption_promotes_full_width_panel_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    left_panel = (20.0, 180.0, 135.0, 265.0)
    right_panel = (165.0, 180.0, 280.0, 265.0)
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line("Left-column body text establishes this layout.", (20, 50, 135, 66)),
                _ocr_line("A second left-column body line confirms it.", (20, 80, 135, 96)),
                _ocr_line("Figure 1: Result.", (20, 280, 100, 296)),
                _ocr_line(
                    "Right-column body text establishes the opposite layout.",
                    (165, 50, 280, 66),
                ),
                _ocr_line("A second right-column body line confirms it.", (165, 80, 280, 96)),
            ]
        ],
    )

    parsed = parse_pdf(_scan_visual_pdf(left_panel, right_panel), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert parsed.stats["columns"] == 2
    assert len(figures) == 1
    crop = figures[0].bbox
    assert crop is not None
    assert crop[0] <= left_panel[0] and crop[2] >= right_panel[2]
    assert len(parsed.figure_images) == 1


def test_parse_pdf_ocr_left_column_visual_does_not_promote_for_right_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    left_visual = (20.0, 180.0, 125.0, 265.0)
    right_noise = (205.0, 205.0, 245.0, 240.0)
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line("Left-column body text establishes this layout.", (20, 50, 135, 66)),
                _ocr_line("A second left-column body line confirms it.", (20, 80, 135, 96)),
                _ocr_line("Figure 1: Left result.", (20, 280, 110, 296)),
                _ocr_line(
                    "Right-column body text establishes the opposite layout.",
                    (165, 50, 280, 66),
                ),
                _ocr_line("A second right-column body line confirms it.", (165, 80, 280, 96)),
            ]
        ],
    )

    parsed = parse_pdf(_scan_visual_pdf(left_visual, right_noise), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert len(figures) == 1
    crop = figures[0].bbox
    assert crop is not None
    assert crop[2] < 150
    assert crop[2] < right_noise[0]


def test_parse_pdf_ocr_recovers_bounded_scan_crop_below_caption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "Figure 1: The visual region follows this recovered caption.",
                    (40, 75, 260, 91),
                ),
                _ocr_line(
                    "Substantive recovered prose resumes beneath the visual region.",
                    (30, 270, 270, 286),
                ),
                _ocr_line(
                    "A second recovered prose line keeps the page text meaningful.",
                    (30, 310, 270, 326),
                ),
            ]
        ],
    )

    parsed = parse_pdf(_scan_visual_pdf((50, 110, 250, 245)), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert len(figures) == 1
    figure = figures[0]
    assert figure.id in parsed.figure_images
    assert figure.bbox is not None
    assert 91 < figure.bbox[1] < figure.bbox[3] < 270
    assert (figure.bbox[2] - figure.bbox[0]) * (figure.bbox[3] - figure.bbox[1]) < 0.60 * 300 * 400


def test_parse_pdf_ocr_recovers_distinct_scan_crops_for_multiple_captions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "Recovered introductory prose establishes enough text for parsing.",
                    (30, 25, 270, 41),
                ),
                _ocr_line(
                    "Figure 1: First recovered visual.",
                    (40, 180, 260, 196),
                ),
                _ocr_line(
                    "Figure 2: Second recovered visual.",
                    (40, 310, 260, 326),
                ),
            ]
        ],
    )

    parsed = parse_pdf(
        _scan_visual_pdf((50, 80, 250, 165), (50, 215, 250, 295)),
        use_ocr=True,
    )

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert [figure.number for figure in figures] == ["1", "2"]
    assert all(figure.id in parsed.figure_images for figure in figures)
    assert figures[0].bbox is not None and figures[1].bbox is not None
    assert figures[0].bbox[3] <= figures[1].bbox[1]
    assert figures[0].bbox != figures[1].bbox


def test_parse_pdf_ocr_drops_scan_background_on_blank_second_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "The substantive first page contains enough recovered text to keep "
                    "the complete two-page document above its text threshold.",
                    (25, 80, 275, 100),
                )
            ],
            [],
        ],
    )

    parsed = parse_pdf(_scan_background_pdf(pages=2), use_ocr=True)

    assert parsed.stats["pages"] == 2
    assert parsed.stats["figures"] == 0
    assert parsed.figure_images == {}


def test_parse_pdf_ocr_drops_scan_background_on_one_long_line_cover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "A single long recovered cover line is text, not a semantic figure.",
                    (25, 185, 275, 205),
                )
            ]
        ],
    )

    parsed = parse_pdf(_scan_background_pdf(), use_ocr=True)

    assert parsed.stats["figures"] == 0
    assert parsed.figure_images == {}


def test_parse_pdf_ocr_uses_text_containment_to_drop_inset_scan_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "This first recovered paragraph sits inside the inset scan raster.",
                    (50, 80, 250, 96),
                ),
                _ocr_line(
                    "This second recovered paragraph confirms page-level OCR containment.",
                    (50, 180, 250, 196),
                ),
            ]
        ],
    )

    parsed = parse_pdf(_inset_scan_background_pdf(), use_ocr=True)

    assert parsed.stats["figures"] == 0
    assert parsed.figure_images == {}
    assert len([block for block in parsed.blocks if block.type == "paragraph"]) == 2


@pytest.mark.parametrize("text_page", [0, 1], ids=["forward", "reversed"])
def test_parse_pdf_ocr_inherits_confirmed_inset_scan_profile_across_blank_page(
    monkeypatch: pytest.MonkeyPatch,
    text_page: int,
) -> None:
    substantive_lines = [
        _ocr_line(
            "This first recovered paragraph identifies the repeated inset scan geometry.",
            (50, 80, 250, 96),
        ),
        _ocr_line(
            "This second recovered paragraph provides document-level OCR confirmation.",
            (50, 180, 250, 196),
        ),
    ]
    pages_lines = [[], []]
    pages_lines[text_page] = substantive_lines
    _install_ocr_pages(monkeypatch, pages_lines)
    inset = (34.0, 10.0, 266.0, 390.0)

    parsed = parse_pdf(_multi_page_raster_rect_pdf([inset, inset]), use_ocr=True)

    assert parsed.stats["pages"] == 2
    assert parsed.stats["figures"] == 0
    assert parsed.figure_images == {}
    assert len([block for block in parsed.blocks if block.type == "paragraph"]) == 2


def test_parse_pdf_ocr_scan_profile_does_not_claim_different_large_native_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "This first recovered paragraph confirms the inset scan background.",
                    (50, 80, 250, 96),
                ),
                _ocr_line(
                    "This second recovered paragraph supplies enough document evidence.",
                    (50, 180, 250, 196),
                ),
            ],
            [],
        ],
    )
    inset = (34.0, 10.0, 266.0, 390.0)
    native = (20.0, 100.0, 280.0, 300.0)

    parsed = parse_pdf(_multi_page_raster_rect_pdf([inset, native]), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert len(figures) == 1
    assert figures[0].page == 2
    assert figures[0].bbox == list(native)
    assert figures[0].id in parsed.figure_images


def test_parse_pdf_ocr_drops_gapped_tiles_covering_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "Recovered cover text remains independent from the tiled scan raster.",
                    (25, 185, 275, 205),
                )
            ]
        ],
    )

    parsed = parse_pdf(_gapped_tiled_scan_pdf(), use_ocr=True)

    assert parsed.stats["figures"] == 0
    assert parsed.figure_images == {}


def test_parse_pdf_ocr_retains_smaller_native_semantic_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_ocr_pages(
        monkeypatch,
        [
            [
                _ocr_line(
                    "Recovered text accompanies a smaller native semantic image on this page.",
                    (25, 40, 275, 60),
                )
            ]
        ],
    )

    parsed = parse_pdf(_smaller_image_pdf(), use_ocr=True)

    figures = [block for block in parsed.blocks if block.type == "figure"]
    assert len(figures) == 1
    assert figures[0].bbox == [50.0, 115.0, 250.0, 235.0]
    assert figures[0].id in parsed.figure_images


def test_parse_pdf_ocr_many_scan_pages_do_not_exhaust_semantic_figure_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = _ocr_line(
        "One sufficiently long recovered scan line must not become a page-sized figure.",
        (25, 185, 275, 205),
    )
    _install_ocr_pages(monkeypatch, [[line] for _ in range(201)])
    monkeypatch.setattr(pdf_parser_module._PdfParser, "_crop", lambda *_args: b"x")

    parsed = parse_pdf(_scan_background_pdf(pages=201), use_ocr=True)

    assert parsed.stats["pages"] == 201
    assert parsed.stats["figures"] == 0
    assert parsed.figure_images == {}


def test_parse_pdf_reports_stable_missing_ocr_engine_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_engine(_page: fitz.Page, **_kwargs: Any) -> Any:
        raise RuntimeError("No tessdata specified and Tesseract is not installed")

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", missing_engine)

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_load("pdf_no_text_layer.pdf"), use_ocr=True)

    assert exc_info.value.kind == "ocr_engine_unavailable"
    assert str(exc_info.value) == "PDF OCR engine is unavailable"


def test_pdf_ocr_readiness_and_execution_fail_closed_on_unsupported_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_parser_module.sys, "platform", "darwin")

    readiness = pdf_parser_module.check_pdf_ocr_readiness()
    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_load("pdf_no_text_layer.pdf"), use_ocr=True)

    assert readiness.code == "ocr_platform_unsupported"
    assert exc_info.value.kind == "ocr_platform_unsupported"


def test_parse_pdf_rejects_invalid_ocr_language_before_invoking_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_ocr(_page: fitz.Page, **_kwargs: Any) -> Any:
        raise AssertionError("invalid language must be rejected before OCR")

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", unexpected_ocr)

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(
            _load("pdf_no_text_layer.pdf"),
            use_ocr=True,
            ocr_language="../eng",
        )

    assert exc_info.value.kind == "ocr_language_invalid"
    assert str(exc_info.value) == "PDF OCR language is invalid"


@pytest.mark.parametrize(
    ("error", "expected_kind", "expected_message"),
    [
        (
            RuntimeError(
                "Error opening data file /usr/share/tessdata/deu.traineddata; "
                "Failed loading language 'deu'"
            ),
            "ocr_language_unavailable",
            "PDF OCR language data is unavailable",
        ),
        (TimeoutError("OCR deadline expired"), "ocr_timeout", "PDF OCR timed out"),
        (RuntimeError("internal OCR failure"), "ocr_failed", "PDF OCR failed"),
    ],
)
def test_parse_pdf_reports_stable_ocr_creation_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_kind: str,
    expected_message: str,
) -> None:
    monkeypatch.setattr(
        pdf_parser_module,
        "check_pdf_ocr_readiness",
        lambda **_kwargs: pdf_parser_module.PdfOcrReadiness(True, "ready", "eng"),
    )

    def fail(_page: fitz.Page, **_kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", fail)

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_load("pdf_no_text_layer.pdf"), use_ocr=True, ocr_language="deu")

    assert exc_info.value.kind == expected_kind
    assert str(exc_info.value) == expected_message


def test_parse_pdf_normalizes_ocr_text_extraction_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_get_text = fitz.Page.get_text
    monkeypatch.setattr(
        pdf_parser_module,
        "check_pdf_ocr_readiness",
        lambda **_kwargs: pdf_parser_module.PdfOcrReadiness(True, "ready", "eng"),
    )

    def fake_ocr(page: fitz.Page, **_kwargs: Any) -> Any:
        return page.get_textpage()

    def fail_ocr_extraction(page: fitz.Page, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("textpage") is not None:
            raise RuntimeError("OCR text extraction failed unexpectedly")
        return original_get_text(page, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", fake_ocr)
    monkeypatch.setattr(fitz.Page, "get_text", fail_ocr_extraction)

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_load("pdf_quality_b_sample.pdf"), use_ocr=True)

    assert exc_info.value.kind == "ocr_failed"
    assert str(exc_info.value) == "PDF OCR failed"


def test_parse_pdf_uses_readiness_probe_to_classify_ambiguous_ocr_initialization_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def ambiguous_failure(_page: fitz.Page, **_kwargs: Any) -> Any:
        raise RuntimeError("OCR initialisation failed")

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", ambiguous_failure)
    monkeypatch.setattr(
        pdf_parser_module,
        "check_pdf_ocr_readiness",
        lambda **_kwargs: pdf_parser_module.PdfOcrReadiness(
            False,
            "ocr_language_unavailable",
            "eng",
        ),
    )

    with pytest.raises(PdfParseError) as exc_info:
        parse_pdf(_load("pdf_no_text_layer.pdf"), use_ocr=True)

    assert exc_info.value.kind == "ocr_language_unavailable"
    assert str(exc_info.value) == "PDF OCR language data is unavailable"


def test_pdf_ocr_readiness_reports_missing_tesseract_without_running_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    def unexpected_run(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("no subprocess should run without a tesseract binary")

    monkeypatch.setattr(subprocess, "run", unexpected_run)

    readiness = pdf_parser_module.check_pdf_ocr_readiness()

    assert readiness.as_dict() == {
        "available": False,
        "code": "ocr_engine_unavailable",
        "language": "eng",
    }


def test_pdf_ocr_readiness_requires_requested_language_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tesseract")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["tesseract", "--list-langs"],
            returncode=0,
            stdout='List of available languages in "/usr/share/tessdata/" (1):\nosd\n',
            stderr="",
        ),
    )

    readiness = pdf_parser_module.check_pdf_ocr_readiness(language="eng")

    assert readiness.available is False
    assert readiness.code == "ocr_language_unavailable"


def test_pdf_ocr_readiness_accepts_binary_with_requested_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tesseract")
    seen: list[tuple[Any, ...]] = []

    def list_languages(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=["tesseract", "--list-langs"],
            returncode=0,
            stdout='List of available languages in "/usr/share/tessdata/" (2):\neng\nosd\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", list_languages)

    readiness = pdf_parser_module.check_pdf_ocr_readiness(language="eng", timeout_s=1.5)

    assert readiness.as_dict() == {"available": True, "code": "ready", "language": "eng"}
    assert seen[0][0] == (["/usr/bin/tesseract", "--list-langs"],)
    assert seen[0][1]["timeout"] == 1.5
    assert seen[0][1]["shell"] is False


def test_pdf_ocr_readiness_timeout_is_stable_and_non_throwing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tesseract")

    def timeout(*_args: Any, **_kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("tesseract", 0.1)

    monkeypatch.setattr(subprocess, "run", timeout)

    readiness = pdf_parser_module.check_pdf_ocr_readiness(timeout_s=0.1)

    assert readiness.available is False
    assert readiness.code == "ocr_readiness_timeout"


@pytest.mark.parametrize(
    ("stage", "error"),
    [
        ("which", RuntimeError("PATH lookup failed unexpectedly")),
        ("run", ValueError("invalid subprocess configuration")),
        (
            "run",
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        ),
    ],
)
def test_pdf_ocr_readiness_normalizes_ordinary_probe_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    error: Exception,
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    if stage == "which":
        monkeypatch.setattr(shutil, "which", fail)
    else:
        monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tesseract")
        monkeypatch.setattr(subprocess, "run", fail)

    readiness = pdf_parser_module.check_pdf_ocr_readiness()

    assert readiness.as_dict() == {
        "available": False,
        "code": "ocr_readiness_failed",
        "language": "eng",
    }


def test_pdf_ocr_readiness_rejects_invalid_mocked_process_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tesseract")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["tesseract", "--list-langs"],
            returncode=0,
            stdout=b"\xff",
            stderr="",
        ),
    )

    readiness = pdf_parser_module.check_pdf_ocr_readiness()

    assert readiness.available is False
    assert readiness.code == "ocr_readiness_failed"


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(2)])
def test_pdf_ocr_readiness_does_not_swallow_process_control_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tesseract")

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(type(error)):
        pdf_parser_module.check_pdf_ocr_readiness()


def test_pdf_ocr_readiness_is_cached_and_has_explicit_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tesseract")
    calls = 0

    def list_languages(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            args=["tesseract", "--list-langs"],
            returncode=0,
            stdout="List of available languages (1):\neng\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", list_languages)

    first = pdf_parser_module.check_pdf_ocr_readiness()
    second = pdf_parser_module.check_pdf_ocr_readiness()
    pdf_parser_module.clear_pdf_ocr_readiness_cache()
    third = pdf_parser_module.check_pdf_ocr_readiness()

    assert first == second == third
    assert calls == 2
