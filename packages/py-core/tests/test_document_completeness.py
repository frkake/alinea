from alinea_core.document.blocks import Block, BlockType, DocumentContent, Section, SectionHeading
from alinea_core.ingest.completeness import (
    DocumentCompleteness,
    assess_document_completeness,
)


def _doc(*blocks: Block) -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Body"),
                blocks=list(blocks),
            )
        ],
    )


def _text_block(block_id: str, block_type: BlockType, text: str) -> Block:
    return Block(id=block_id, type=block_type, inlines=[{"t": "text", "v": text}])


def test_rejects_single_embedded_pdf_filename() -> None:
    pdf_text = "A long PDF body " * 100

    report = assess_document_completeness(
        _doc(_text_block("b1", "paragraph", "paper.pdf")),
        pdf_text=pdf_text,
        source_manifest={"binary_files": ["sources/paper.pdf"]},
    )

    assert report == DocumentCompleteness(
        accepted=False,
        code="embedded_pdf_wrapper",
        source_chars=len(pdf_text),
        structured_chars=len("paper.pdf"),
        paragraph_count=1,
        figure_count=0,
    )


def test_accepts_short_but_structured_note() -> None:
    visible = "Method\nA concise method.\nA concise result."

    report = assess_document_completeness(
        _doc(
            Block(id="h1", type="heading", level=1, title="Method"),
            _text_block("p1", "paragraph", "A concise method."),
            _text_block("p2", "paragraph", "A concise result."),
        ),
        pdf_text="",
        source_manifest={},
    )

    assert report.as_dict() == {
        "accepted": True,
        "code": None,
        "source_chars": 0,
        "structured_chars": len(visible),
        "paragraph_count": 2,
        "figure_count": 0,
        "unresolved_figures": 0,
    }


def test_rejects_unresolved_figure_assets_and_preserves_counts() -> None:
    report = assess_document_completeness(
        _doc(
            _text_block("p1", "paragraph", "First paragraph."),
            _text_block("p2", "paragraph", "Second paragraph."),
            Block(
                id="fig-1",
                type="figure",
                caption=[{"t": "text", "v": "Resolved caption"}],
            ),
            Block(
                id="fig-2",
                type="figure",
                caption=[{"t": "text", "v": "Missing caption asset"}],
            ),
        ),
        pdf_text="",
        source_manifest={},
        unresolved_figures=1,
    )

    assert not report.accepted
    assert report.code == "figure_asset_unresolved"
    assert report.paragraph_count == 2
    assert report.figure_count == 2
    assert report.unresolved_figures == 1


def test_rejects_structured_text_far_shorter_than_pdf_text() -> None:
    pdf_text = "x" * 1_000

    report = assess_document_completeness(
        _doc(
            Block(id="h1", type="heading", level=1, title="Method"),
            _text_block("p1", "paragraph", "Brief body."),
        ),
        pdf_text=pdf_text,
        source_manifest={},
    )

    assert not report.accepted
    assert report.code == "document_incomplete"
    assert report.source_chars == 1_000
    assert report.structured_chars == len("Method\nBrief body.")
    assert report.paragraph_count == 1
    assert report.figure_count == 0


def test_rejects_empty_document() -> None:
    report = assess_document_completeness(
        DocumentContent(quality_level="A", sections=[]),
        pdf_text="",
        source_manifest={},
    )

    assert report == DocumentCompleteness(
        accepted=False,
        code="document_incomplete",
        source_chars=0,
        structured_chars=0,
        paragraph_count=0,
        figure_count=0,
    )


def test_rejects_document_without_visible_structured_prose() -> None:
    report = assess_document_completeness(
        _doc(Block(id="eq-1", type="equation", latex=r"E=mc^2")),
        pdf_text="",
        source_manifest={},
    )

    assert not report.accepted
    assert report.code == "document_incomplete"
    assert report.structured_chars == 0


def test_counts_only_prose_bearing_blocks_as_paragraphs_and_all_figures() -> None:
    report = assess_document_completeness(
        _doc(
            Block(id="h1", type="heading", level=1, title="Results"),
            _text_block("p1", "paragraph", "Paragraph."),
            Block(
                id="list-1",
                type="list",
                items=[[{"t": "text", "v": "List item."}]],
            ),
            _text_block("quote-1", "quote", "Quoted result."),
            _text_block("theorem-1", "theorem", "A theorem."),
            _text_block("footnote-1", "footnote", "A footnote."),
            _text_block("algorithm-1", "algorithm", "An algorithm."),
            Block(id="fig-1", type="figure"),
            Block(id="fig-2", type="figure"),
        ),
        pdf_text="",
        source_manifest={},
    )

    assert report.accepted
    assert report.paragraph_count == 4
    assert report.figure_count == 2
