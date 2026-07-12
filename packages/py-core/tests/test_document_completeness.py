import alinea_core.ingest.completeness as completeness_module
import pytest
from alinea_core import ingest
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


@pytest.mark.parametrize(
    ("plain", "manifest_files", "expected"),
    [
        pytest.param("body.pdf", ("nested/body.pdf",), True, id="basename"),
        pytest.param("./body.pdf", ("nested/body.pdf",), True, id="dot-relative"),
        pytest.param("../body.pdf", ("nested/body.pdf",), True, id="parent-relative"),
        pytest.param("papers/body.pdf", ("nested/body.pdf",), True, id="other-directory"),
        pytest.param(
            "stored files/body copy.pdf",
            ("stored files/body copy.pdf",),
            True,
            id="exact-path-with-spaces",
        ),
        pytest.param(
            "body copy.pdf",
            ("stored files/body copy.pdf",),
            True,
            id="exact-basename-with-spaces",
        ),
        pytest.param("See papers/body.pdf", ("nested/body.pdf",), False, id="prose-prefix"),
        pytest.param(
            "https://example.org/body.pdf",
            ("nested/body.pdf", "https://example.org/body.pdf"),
            False,
            id="https-uri",
        ),
        pytest.param(
            "http://example.org/body.pdf",
            ("nested/body.pdf", "http://example.org/body.pdf"),
            False,
            id="http-uri",
        ),
        pytest.param(
            "s3://bucket/body.pdf",
            ("nested/body.pdf", "s3://bucket/body.pdf"),
            False,
            id="s3-uri",
        ),
        pytest.param(
            "/absolute/body.pdf",
            ("nested/body.pdf", "/absolute/body.pdf"),
            False,
            id="absolute-path",
        ),
        pytest.param(
            "//host/share/body.pdf",
            ("nested/body.pdf", "//host/share/body.pdf"),
            False,
            id="network-path",
        ),
        pytest.param(
            "C:/body.pdf",
            ("nested/body.pdf", "C:/body.pdf"),
            False,
            id="drive-prefix",
        ),
        pytest.param(
            r"\\host\share\body.pdf",
            ("nested/body.pdf", r"\\host\share\body.pdf"),
            False,
            id="windows-network-path",
        ),
        pytest.param(
            r"\\?\C:\body.pdf",
            ("nested/body.pdf", r"\\?\C:\body.pdf"),
            False,
            id="windows-device-path",
        ),
        pytest.param(
            r"folder\body.pdf",
            ("nested/body.pdf", r"folder\body.pdf"),
            False,
            id="windows-relative-path",
        ),
        pytest.param(
            "folder/\x00body.pdf",
            ("nested/body.pdf", "folder/\x00body.pdf"),
            False,
            id="nul-character",
        ),
        pytest.param(
            "folder/\x7fbody.pdf",
            ("nested/body.pdf", "folder/\x7fbody.pdf"),
            False,
            id="del-character",
        ),
        pytest.param(
            "Results are in papers/body.pdf.",
            ("nested/body.pdf",),
            False,
            id="sentence-punctuation",
        ),
        pytest.param(
            "Read papers/body.pdf now",
            ("nested/body.pdf",),
            False,
            id="prose-around-path",
        ),
        pytest.param(
            "other/body copy.pdf",
            ("stored files/body copy.pdf",),
            False,
            id="spaced-basename-in-different-path",
        ),
        pytest.param("other.pdf", ("nested/body.pdf",), False, id="different-pdf"),
        pytest.param("", ("nested/body.pdf",), False, id="empty"),
    ],
)
def test_is_bare_pdf_reference(plain: str, manifest_files: tuple[str, ...], expected: bool) -> None:
    assert completeness_module._is_bare_pdf_reference(plain, manifest_files) is expected


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


def test_rejects_embedded_pdf_filename_from_mapping_manifest() -> None:
    report = assess_document_completeness(
        _doc(_text_block("b1", "paragraph", "body.pdf")),
        pdf_text="",
        source_manifest={"binary_files": {"nested/body.pdf": b"PDF"}},
    )

    assert not report.accepted
    assert report.code == "embedded_pdf_wrapper"


@pytest.mark.parametrize("reference", ["./body.pdf", "papers/body.pdf"])
def test_rejects_normalized_embedded_pdf_reference(reference: str) -> None:
    report = assess_document_completeness(
        _doc(_text_block("b1", "paragraph", reference)),
        pdf_text="",
        source_manifest={"binary_files": ["nested/body.pdf"]},
    )

    assert not report.accepted
    assert report.code == "embedded_pdf_wrapper"


def test_substantive_prose_before_pdf_reference_is_not_a_wrapper() -> None:
    report = assess_document_completeness(
        _doc(
            _text_block("p1", "paragraph", "Substantive introduction."),
            _text_block("p2", "paragraph", "papers/body.pdf"),
        ),
        pdf_text="",
        source_manifest={"binary_files": ["nested/body.pdf"]},
    )

    assert report.code != "embedded_pdf_wrapper"
    assert report.accepted


def test_meaningful_heading_before_pdf_reference_is_not_a_wrapper() -> None:
    report = assess_document_completeness(
        _doc(
            Block(id="h1", type="heading", level=1, title="Supplemental material"),
            _text_block("p1", "paragraph", "papers/body.pdf"),
        ),
        pdf_text="",
        source_manifest={"binary_files": ["nested/body.pdf"]},
    )

    assert report.code != "embedded_pdf_wrapper"
    assert report.accepted


@pytest.mark.parametrize(
    "paragraphs",
    [
        pytest.param(("See papers/body.pdf",), id="one-prose-block"),
        pytest.param(
            ("See papers/body.pdf", "Also papers/body.pdf"),
            id="two-prose-blocks",
        ),
        pytest.param(("Results are in papers/body.pdf.",), id="sentence-punctuation"),
    ],
)
def test_pdf_mentions_in_free_text_are_not_wrappers(paragraphs: tuple[str, ...]) -> None:
    blocks = tuple(
        _text_block(f"p{index}", "paragraph", paragraph)
        for index, paragraph in enumerate(paragraphs, start=1)
    )
    report = assess_document_completeness(
        _doc(*blocks),
        pdf_text="",
        source_manifest={"binary_files": ["nested/body.pdf"]},
    )

    assert report.code != "embedded_pdf_wrapper"


@pytest.mark.parametrize(
    ("references", "manifest_files"),
    [
        pytest.param(
            ("body.pdf", "./body.pdf"),
            ("nested/body.pdf",),
            id="same-basename",
        ),
        pytest.param(
            ("papers/body.pdf", "appendix/supp.pdf"),
            ("nested/body.pdf", "assets/supp.pdf"),
            id="distinct-basenames",
        ),
    ],
)
def test_rejects_multiple_path_only_pdf_blocks(
    references: tuple[str, ...], manifest_files: tuple[str, ...]
) -> None:
    blocks = tuple(
        _text_block(f"p{index}", "paragraph", reference)
        for index, reference in enumerate(references, start=1)
    )
    report = assess_document_completeness(
        _doc(*blocks),
        pdf_text="",
        source_manifest={"binary_files": manifest_files},
    )

    assert report.code == "embedded_pdf_wrapper"


def test_rejects_exact_manifest_pdf_path_with_spaces() -> None:
    reference = "stored files/body copy.pdf"
    report = assess_document_completeness(
        _doc(_text_block("p1", "paragraph", reference)),
        pdf_text="",
        source_manifest={"binary_files": [reference]},
    )

    assert report.code == "embedded_pdf_wrapper"


def test_empty_parser_blocks_do_not_hide_embedded_pdf_wrapper() -> None:
    report = assess_document_completeness(
        _doc(
            _text_block("b1", "paragraph", "body.pdf"),
            _text_block("empty-p", "paragraph", ""),
            Block(id="empty-h", type="heading", level=1),
            Block(id="empty-fig", type="figure"),
        ),
        pdf_text="",
        source_manifest={"binary_files": ["nested/body.pdf"]},
    )

    assert not report.accepted
    assert report.code == "embedded_pdf_wrapper"


@pytest.mark.parametrize(
    "binary_files",
    [
        pytest.param("nested/body.pdf", id="string"),
        pytest.param(("nested/body.pdf",), id="tuple"),
        pytest.param({"nested/body.pdf"}, id="set"),
    ],
)
def test_rejects_embedded_pdf_filename_from_common_manifest_forms(binary_files: object) -> None:
    report = assess_document_completeness(
        _doc(_text_block("b1", "paragraph", "body.pdf")),
        pdf_text="",
        source_manifest={"binary_files": binary_files},
    )

    assert report.code == "embedded_pdf_wrapper"


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


def test_rejects_empty_heading_with_only_one_non_empty_paragraph() -> None:
    report = assess_document_completeness(
        _doc(
            Block(id="h1", type="heading", level=1),
            _text_block("p1", "paragraph", "Only visible paragraph."),
        ),
        pdf_text="",
        source_manifest={},
    )

    assert not report.accepted
    assert report.code == "document_incomplete"
    assert report.structured_chars == len("Only visible paragraph.")
    assert report.paragraph_count == 1


def test_rejects_empty_paragraphs_with_only_figure_caption_visible() -> None:
    report = assess_document_completeness(
        _doc(
            _text_block("p1", "paragraph", ""),
            _text_block("p2", "paragraph", ""),
            Block(
                id="fig-1",
                type="figure",
                caption=[{"t": "text", "v": "Only a figure caption."}],
            ),
        ),
        pdf_text="",
        source_manifest={},
    )

    assert not report.accepted
    assert report.code == "document_incomplete"
    assert report.structured_chars == len("Only a figure caption.")
    assert report.paragraph_count == 2
    assert report.figure_count == 1


def test_empty_blocks_do_not_add_visible_text_separators() -> None:
    visible = "Heading\nVisible paragraph."
    report = assess_document_completeness(
        _doc(
            Block(id="h1", type="heading", level=1, title="Heading"),
            _text_block("empty-p", "paragraph", ""),
            _text_block("p1", "paragraph", "Visible paragraph."),
        ),
        pdf_text="",
        source_manifest={},
    )

    assert report.accepted
    assert report.structured_chars == len(visible)
    assert report.paragraph_count == 2


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


def test_rejects_negative_unresolved_figure_count() -> None:
    with pytest.raises(ValueError, match="unresolved_figures must be non-negative"):
        assess_document_completeness(
            _doc(
                _text_block("p1", "paragraph", "First paragraph."),
                _text_block("p2", "paragraph", "Second paragraph."),
            ),
            pdf_text="",
            source_manifest={},
            unresolved_figures=-1,
        )


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


def test_accepts_structured_text_at_exactly_thirty_five_percent() -> None:
    report = assess_document_completeness(
        _doc(Block(id="h1", type="heading", level=1, title="x" * 350)),
        pdf_text="y" * 1_000,
        source_manifest={},
    )

    assert report.accepted
    assert report.code is None
    assert report.source_chars == 1_000
    assert report.structured_chars == 350


def test_reference_entries_count_toward_recovered_text_coverage() -> None:
    body = "b" * 100
    report = assess_document_completeness(
        _doc(
            _text_block("p1", "paragraph", body),
            _text_block("p2", "paragraph", body),
            Block(id="ref-1", type="reference_entry", raw="r" * 200),
        ),
        pdf_text="x" * 1_000,
        source_manifest={},
    )

    assert report.accepted
    assert report.code is None
    # Translation-facing diagnostics continue to describe translatable prose only.
    assert report.structured_chars == len(f"{body}\n{body}")


def test_explicit_source_char_count_replaces_untrusted_pdf_text_length() -> None:
    report = assess_document_completeness(
        _doc(
            _text_block("p1", "paragraph", "First recovered OCR paragraph." * 12),
            _text_block("p2", "paragraph", "Second recovered OCR paragraph." * 12),
        ),
        pdf_text="x" * 10_000,
        source_char_count=677,
        source_manifest={},
    )

    assert report.accepted
    assert report.source_chars == 677


def test_explicit_source_char_count_still_rejects_low_structured_coverage() -> None:
    report = assess_document_completeness(
        _doc(
            _text_block("p1", "paragraph", "Short OCR paragraph."),
            _text_block("p2", "paragraph", "Another short OCR paragraph."),
        ),
        pdf_text="",
        source_char_count=10_000,
        source_manifest={},
    )

    assert not report.accepted
    assert report.code == "document_incomplete"
    assert report.source_chars == 10_000


@pytest.mark.parametrize("source_char_count", [-1, True])
def test_explicit_source_char_count_must_be_non_negative_integer(
    source_char_count: object,
) -> None:
    with pytest.raises(ValueError, match="source_char_count"):
        assess_document_completeness(
            _doc(_text_block("p1", "paragraph", "Visible paragraph.")),
            pdf_text="",
            source_char_count=source_char_count,  # type: ignore[arg-type]
            source_manifest={},
        )


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


def test_includes_nested_section_blocks_in_text_counts_and_acceptance() -> None:
    visible = "Nested first.\nNested second.\nNested figure."
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-root",
                heading=SectionHeading(number="1", title="Root"),
                sections=[
                    Section(
                        id="sec-nested",
                        heading=SectionHeading(number="1.1", title="Nested"),
                        blocks=[
                            _text_block("p1", "paragraph", "Nested first."),
                            Block(
                                id="list-1",
                                type="list",
                                items=[[{"t": "text", "v": "Nested second."}]],
                            ),
                            Block(
                                id="fig-1",
                                type="figure",
                                caption=[{"t": "text", "v": "Nested figure."}],
                            ),
                        ],
                    )
                ],
            )
        ],
    )

    report = assess_document_completeness(content, pdf_text="", source_manifest={})

    assert report.accepted
    assert report.structured_chars == len(visible)
    assert report.paragraph_count == 2
    assert report.figure_count == 1


def test_completeness_classifier_is_publicly_exported() -> None:
    assert ingest.DocumentCompleteness is DocumentCompleteness
    assert ingest.assess_document_completeness is assess_document_completeness
