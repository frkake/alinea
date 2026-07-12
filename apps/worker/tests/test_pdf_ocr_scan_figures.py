"""OCR scan crops remain materializable PDF candidate assets."""

from __future__ import annotations

from typing import Any, cast

import fitz
import pytest
from alinea_core.document.blocks import Block, DocumentContent, Section
from alinea_core.ingest import DocumentCompleteness
from alinea_core.parsing import pdf_parser as pdf_parser_module
from alinea_core.parsing.pdf_parser import ParsedPdfDocument, parse_pdf
from alinea_worker import pipeline as worker_pipeline
from alinea_worker import source_candidates as source_candidate_module
from alinea_worker.figure_assets import FigureAssetPayload
from alinea_worker.pipeline import IngestRun, MaterializationDeadline
from alinea_worker.source_candidates import SourceCandidate


def _scan_with_visual() -> bytes:
    canvas = fitz.open()
    canvas_page = canvas.new_page(width=300, height=400)
    canvas_page.draw_rect(canvas_page.rect, color=None, fill=(0.96, 0.96, 0.96))
    visual = fitz.Rect(50, 215, 250, 295)
    canvas_page.draw_rect(
        visual,
        color=(0.1, 0.1, 0.1),
        fill=(0.35, 0.35, 0.35),
        width=2,
    )
    canvas_page.draw_line(
        visual.top_left,
        visual.bottom_right,
        color=(0.05, 0.05, 0.05),
        width=3,
    )
    png = canvas_page.get_pixmap(dpi=72, alpha=False).tobytes("png")
    canvas.close()

    document = fitz.open()
    page = document.new_page(width=300, height=400)
    page.insert_image(page.rect, stream=png)
    data = document.tobytes()
    document.close()
    return cast(bytes, data)


def _scan_with_table_grid() -> bytes:
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
    return cast(bytes, data)


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


async def test_scanned_figure_crop_materializes_without_unresolved_asset(
    monkeypatch: pytest.MonkeyPatch,
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
        _ocr_line(
            "Figure 1: A caption recovered beneath the visual region.",
            (40, 315, 260, 331),
        ),
    ]

    monkeypatch.setattr(
        fitz.Page,
        "get_textpage_ocr",
        lambda *_args, **_kwargs: object(),
    )

    def fake_get_text(_page: fitz.Page, *args: Any, **_kwargs: Any) -> Any:
        mode = args[0] if args else "text"
        if mode == "dict":
            return {"blocks": [{"type": 0, "lines": lines}]}
        return "\n".join(line["spans"][0]["text"] for line in lines)

    monkeypatch.setattr(fitz.Page, "get_text", fake_get_text)
    monkeypatch.setattr(
        pdf_parser_module,
        "_detect_table_candidates",
        lambda *_args: [],
    )

    source = _scan_with_visual()
    parsed = parse_pdf(source, use_ocr=True)
    candidate = source_candidate_module._pdf_candidate_from_parsed(
        source,
        pdf_text="",
        parsed=parsed,
        ocr_language="eng",
    )
    materialized_sources: list[bytes] = []

    async def materialize(
        data: bytes,
        _source_name: str,
        _content_type: str | None,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        materialized_sources.append(data)
        return FigureAssetPayload(data, "png", "image/png", 32, 24, len(data))

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", materialize)
    run = object.__new__(IngestRun)

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=MaterializationDeadline.start(timeout_s=30.0),
    )

    assert candidate.report.accepted is True
    assert candidate.report.code is None
    assert candidate.figure_asset_failures == []
    assert candidate.figure_materialization_validated is True
    assert set(candidate.materialized_figures) == set(parsed.figure_images)
    assert materialized_sources
    assert all(payload.startswith(b"\x89PNG") for payload in materialized_sources)


async def test_scanned_table_crop_materializes_without_unresolved_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = [
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
    monkeypatch.setattr(
        fitz.Page,
        "get_textpage_ocr",
        lambda *_args, **_kwargs: object(),
    )

    def fake_get_text(_page: fitz.Page, *args: Any, **_kwargs: Any) -> Any:
        mode = args[0] if args else "text"
        if mode == "dict":
            return {"blocks": [{"type": 0, "lines": lines}]}
        return "\n".join(line["spans"][0]["text"] for line in lines)

    monkeypatch.setattr(fitz.Page, "get_text", fake_get_text)
    monkeypatch.setattr(pdf_parser_module, "_detect_table_candidates", lambda *_args: [])

    source = _scan_with_table_grid()
    parsed = parse_pdf(source, use_ocr=True)
    candidate = source_candidate_module._pdf_candidate_from_parsed(
        source,
        pdf_text="",
        parsed=parsed,
        ocr_language="eng",
    )
    materialized_sources: list[bytes] = []

    async def materialize(
        data: bytes,
        _source_name: str,
        _content_type: str | None,
        **_kwargs: Any,
    ) -> FigureAssetPayload:
        materialized_sources.append(data)
        return FigureAssetPayload(data, "png", "image/png", 32, 24, len(data))

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", materialize)
    run = object.__new__(IngestRun)

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=MaterializationDeadline.start(timeout_s=30.0),
    )

    tables = [block for block in parsed.blocks if block.type == "table"]
    assert len(tables) == 1
    assert set(candidate.materialized_figures) == {tables[0].id}
    assert candidate.figure_asset_failures == []
    assert candidate.report.accepted is True
    assert materialized_sources and materialized_sources[0].startswith(b"\x89PNG")


def _pdf_table_candidate(*, raw: str | None) -> tuple[SourceCandidate, Block]:
    table = Block(id="table-1", type="table", raw=raw, number="1")
    content = DocumentContent(
        quality_level="B",
        sections=[
            Section(
                id="sec-1",
                blocks=[
                    Block(
                        id="paragraph-1",
                        type="paragraph",
                        inlines=[{"t": "text", "v": "First complete synthetic paragraph."}],
                    ),
                    Block(
                        id="paragraph-2",
                        type="paragraph",
                        inlines=[{"t": "text", "v": "Second complete synthetic paragraph."}],
                    ),
                    table,
                ],
            )
        ],
    )
    parsed = ParsedPdfDocument(sections=content.sections, figure_images={})
    return (
        SourceCandidate(
            source_format="pdf",
            content=content,
            parsed=parsed,
            report=DocumentCompleteness(True, None, 0, 70, 2, 0),
            source_bytes=b"%PDF synthetic",
            diagnostics=[],
        ),
        table,
    )


@pytest.mark.parametrize("raw", [None, "", "   "])
async def test_pdf_table_without_raw_or_image_fails_closed(raw: str | None) -> None:
    candidate, table = _pdf_table_candidate(raw=raw)
    run = object.__new__(IngestRun)

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=MaterializationDeadline.start(timeout_s=30.0),
    )

    assert candidate.report.accepted is False
    assert candidate.report.code == "figure_asset_unresolved"
    assert candidate.figure_asset_failures == [
        {
            "code": "missing_asset_key",
            "figure_id": table.id,
            "source": "pdf",
        }
    ]


async def test_pdf_table_with_nonempty_raw_does_not_require_image_asset() -> None:
    candidate, _table = _pdf_table_candidate(raw="<table><tr><td>structured</td></tr></table>")
    run = object.__new__(IngestRun)

    await run._materialize_candidate_figures(
        candidate,
        http=None,
        deadline=MaterializationDeadline.start(timeout_s=30.0),
    )

    assert candidate.report.accepted is True
    assert candidate.figure_asset_failures == []
    assert candidate.materialized_figures == {}
