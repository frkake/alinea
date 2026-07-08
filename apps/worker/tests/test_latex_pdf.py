from __future__ import annotations

import fitz
from yakudoku_core.db.models import TranslationUnit
from yakudoku_core.document.blocks import DocumentContent
from yakudoku_core.parsing.latex_parser import LatexArchive, parse_latex_source
from yakudoku_worker.latex_pdf import (
    _build_bilingual_pdf,
    _find_overfull_boxes,
    _find_pdf_page_bound_violations,
    render_translated_latex_source,
)


def _unit(block_id: str, text: str, content: list[dict[str, object]] | None = None) -> TranslationUnit:
    return TranslationUnit(
        set_id="00000000-0000-0000-0000-000000000000",
        block_id=block_id,
        source_hash=f"h-{block_id}",
        content_ja=content or [{"t": "text", "v": text}],
        text_ja=text,
        state="machine",
        quality_flags=[],
        model="test",
    )


def test_render_translated_latex_source_preserves_figures_equations_links_and_refs() -> None:
    tex = r"""
\documentclass{article}
\usepackage{graphicx}
\usepackage{hyperref}
\begin{document}
\section{Introduction}
\label{sec:intro}
This paragraph has a \href{https://example.com}{link}, a reference to \ref{sec:intro},
and inline math $x^2$.

\begin{equation}
\label{eq:one}
E = mc^2
\end{equation}

\begin{figure}
\includegraphics[width=.8\linewidth]{figures/mock.pdf}
\caption{Original figure caption with $x$.}
\label{fig:mock}
\end{figure}
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {"figures/mock.pdf": b"%PDF-1.4\n%%EOF\n"})
    parsed = parse_latex_source("main.tex", archive.text_files)
    content = parsed.to_document_content()
    blocks = {block.type: block for _section, block in content.iter_blocks()}

    paragraph = next(block for _section, block in content.iter_blocks() if block.type == "paragraph")
    figure = next(block for _section, block in content.iter_blocks() if block.type == "figure")
    heading = blocks["heading"]
    units = {
        heading.id: _unit(heading.id, "はじめに"),
        paragraph.id: _unit(
            paragraph.id,
            "本文ですhttps://example.com sec:intro x^2",
            [
                {"t": "text", "v": "本文です。"},
                {"t": "url", "href": "https://example.com", "v": "リンク"},
                {"t": "text", "v": " を参照し、"},
                {"t": "ref", "ref": "sec:intro", "kind": "section"},
                {"t": "text", "v": " と "},
                {"t": "math_inline", "v": "x^2"},
                {"t": "text", "v": " を保ちます。"},
            ],
        ),
        figure.id: _unit(
            figure.id,
            "図の説明 x",
            [{"t": "text", "v": "図の説明。"}, {"t": "math_inline", "v": "x"}],
        ),
    }

    rendered = render_translated_latex_source(
        archive,
        DocumentContent.model_validate(content.model_dump()),
        units,
        abstract_ja=None,
    )

    assert "\\section{はじめに}" in rendered.main_tex
    assert "本文です。" in rendered.main_tex
    assert r"\href{https://example.com}{リンク}" in rendered.main_tex
    assert r"\ref{sec:intro}" in rendered.main_tex
    assert "$x^2$" in rendered.main_tex
    assert r"\begin{equation}" in rendered.main_tex
    assert "E = mc^2" in rendered.main_tex
    assert r"\includegraphics[width=.8\linewidth]{figures/mock.pdf}" in rendered.main_tex
    assert r"\caption{図の説明。$x$}" in rendered.main_tex
    assert "% yakudoku-ja-pdf" in rendered.main_tex
    assert rendered.replacements["heading"] == 1
    assert rendered.replacements["paragraph"] == 1
    assert rendered.replacements["figure"] == 1


def test_find_overfull_boxes_detects_material_latex_overflow() -> None:
    log = "\n".join(
        [
            r"Overfull \hbox (0.99998pt too wide) in paragraph at lines 1--2",
            r"Overfull \hbox (12.345pt too wide) in paragraph at lines 3--4",
            r"Overfull \vbox (2.0pt too high) has occurred while \output is active",
        ]
    )

    findings = _find_overfull_boxes(log)

    assert len(findings) == 2
    assert "12.345pt" in findings[0]
    assert r"\vbox" in findings[1]


def test_find_pdf_page_bound_violations_detects_text_outside_page() -> None:
    doc = fitz.open()
    try:
        page = doc.new_page(width=300, height=420)
        page.insert_text((292, 72), "outside page bounds", fontsize=12)

        findings = _find_pdf_page_bound_violations(doc)
    finally:
        doc.close()

    assert findings
    assert "kind=text" in findings[0]


def _pdf_with_text(*pages: str) -> bytes:
    doc = fitz.open()
    try:
        for text in pages:
            page = doc.new_page(width=300, height=420)
            page.insert_text((36, 72), text, fontsize=12)
        return bytes(doc.tobytes())
    finally:
        doc.close()


def test_build_bilingual_pdf_keeps_page_index_alignment_without_repeating_pages() -> None:
    original = _pdf_with_text("original page 1")
    translated = _pdf_with_text("translated page 1", "translated page 2")

    out = fitz.open(stream=_build_bilingual_pdf(original, translated), filetype="pdf")
    try:
        assert out.page_count == 2
        assert "original page 1" in out.load_page(0).get_text("text")
        page_two_text = out.load_page(1).get_text("text")
        assert "translated page 2" in page_two_text
        assert "original page 1" not in page_two_text
    finally:
        out.close()
