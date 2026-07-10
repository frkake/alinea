from __future__ import annotations

import os
import tempfile
from pathlib import Path

import fitz
import pytest
from alinea_core.db.models import TranslationUnit
from alinea_core.document.blocks import DocumentContent
from alinea_core.parsing.latex_parser import (
    LatexArchive,
    extract_latex_archive,
    parse_arxiv_latex,
    parse_latex_source,
)
from alinea_worker.latex_pdf import (
    DEFAULT_TEXLIVE_IMAGE,
    LatexPdfBuildError,
    _compile_with_docker,
    _find_overfull_boxes,
    _find_pdf_page_bound_violations,
    _translation_units_digest,
    _validate_render_coverage,
    _validate_source_revision_match,
    _validate_translated_pdf,
    _write_rendered_source,
    render_translated_latex_source,
)


def _unit(
    block_id: str, text: str, content: list[dict[str, object]] | None = None
) -> TranslationUnit:
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

    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
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
    assert "% alinea-ja-pdf" in rendered.main_tex
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


def test_translation_digest_changes_with_pdf_visible_content() -> None:
    first = _unit("blk-a", "訳文A")
    second = _unit("blk-b", "訳文B")

    original = _translation_units_digest({"blk-a": first, "blk-b": second})
    assert original == _translation_units_digest({"blk-b": second, "blk-a": first})

    first.text_ja = "更新した訳文A"
    assert _translation_units_digest({"blk-a": first, "blk-b": second}) != original


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


def test_render_keeps_source_layout_and_translates_footnote() -> None:
    parsed_tex = r"""
\documentclass[twocolumn]{article}
\usepackage[a4paper,margin=22mm]{geometry}
\newcommand{\paperstyle}{kept}
\begin{document}
\section{Introduction}
Original paragraph\footnote{Original footnote.} continues.
\end{document}
"""
    raw_tex = parsed_tex.replace(
        r"\newcommand{\paperstyle}{kept}",
        "% source layout comment\n" + r"\newcommand{\paperstyle}{kept}",
    )
    archive = LatexArchive(
        {"paper/main.tex": parsed_tex},
        {},
        {"paper/main.tex": raw_tex},
    )
    content = parse_latex_source("paper/main.tex", archive.text_files).to_document_content()
    heading = next(block for _section, block in content.iter_blocks() if block.type == "heading")
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
    footnote = next(block for _section, block in content.iter_blocks() if block.type == "footnote")
    assert footnote.label
    units = {
        heading.id: _unit(heading.id, "はじめに"),
        paragraph.id: _unit(
            paragraph.id,
            "日本語の本文と脚注です。",
            [
                {"t": "text", "v": "日本語の本文"},
                {"t": "footnote_ref", "ref": footnote.label},
                {"t": "text", "v": "が続きます。"},
            ],
        ),
        footnote.id: _unit(footnote.id, "日本語の脚注。"),
    }

    rendered = render_translated_latex_source(archive, content, units)

    assert rendered.main_tex_name == "paper/main.tex"
    assert r"\documentclass[twocolumn]{article}" in rendered.main_tex
    assert r"\usepackage[a4paper,margin=22mm]{geometry}" in rendered.main_tex
    assert r"\newcommand{\paperstyle}{kept}" in rendered.main_tex
    assert "% source layout comment" in rendered.main_tex
    assert r"\footnote{日本語の脚注。}" in rendered.main_tex
    assert "Original footnote" not in rendered.main_tex
    assert r"\setmonojfont{Noto Sans Mono CJK JP}" in rendered.main_tex
    assert rendered.main_tex.index("% alinea-luatex-compat") < rendered.main_tex.index(
        r"\documentclass"
    )
    assert r"\let\pdfoutput\outputmode" in rendered.main_tex
    assert r"\sloppy" not in rendered.main_tex
    assert rendered.replacements == {"heading": 1, "footnote": 1, "paragraph": 1}
    _validate_render_coverage(rendered, content, units)


def test_render_translates_class_style_abstract_command() -> None:
    tex = r"""
\documentclass{article}
\newcommand{\abstract}[1]{}
\abstract{Original English abstract.}
\begin{document}
Body text.
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )

    rendered = render_translated_latex_source(
        archive,
        content,
        {paragraph.id: _unit(paragraph.id, "日本語の本文。")},
        abstract_ja="日本語の概要。",
    )

    assert r"\abstract{日本語の概要。}" in rendered.main_tex
    assert "Original English abstract" not in rendered.main_tex


def test_custom_environment_drops_translated_option_artifact() -> None:
    tex = r"""
\documentclass{article}
\newenvironment{notice}[1][]{\begin{quote}}{\end{quote}}
\begin{document}
\begin{notice}[breakable,colback=blue!3]
Original notice.
\end{notice}
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
    unit = _unit(
        paragraph.id,
        "[ breakable, colback=blue!3 ] 日本語の注意。",
        [{"t": "text", "v": "[ breakable, colback=blue!3 ] 日本語の注意。"}],
    )

    rendered = render_translated_latex_source(archive, content, {paragraph.id: unit})

    assert r"\begin{notice}[breakable,colback=blue!3]" in rendered.main_tex
    assert "日本語の注意。" in rendered.main_tex
    assert "[ breakable, colback=blue!3 ]" not in rendered.main_tex


def test_render_list_preserves_items_and_structured_inline_latex() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\section{Method}\label{sec:method}
\begin{itemize}
\item[First] Original first item referring to Section~\ref{sec:method}.
\item Original second item with $x^2$.
\end{itemize}
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    heading = next(block for _section, block in content.iter_blocks() if block.type == "heading")
    list_block = next(block for _section, block in content.iter_blocks() if block.type == "list")
    units = {
        heading.id: _unit(heading.id, "手法"),
        list_block.id: _unit(
            list_block.id,
            "第一項。 - 第二項。",
            [
                {"t": "text", "v": "第一項は"},
                {"t": "ref", "ref": "sec:method", "kind": "section"},
                {"t": "text", "v": "を参照。 - 第二項は"},
                {"t": "math_inline", "v": "x^2"},
                {"t": "text", "v": "。"},
            ],
        ),
    }

    rendered = render_translated_latex_source(archive, content, units)

    assert r"\begin{itemize}" in rendered.main_tex
    assert r"\item[First] 第一項は\ref{sec:method}を参照。" in rendered.main_tex
    assert r"\item 第二項は$x^2$。" in rendered.main_tex
    assert r"\end{itemize}" in rendered.main_tex
    _validate_render_coverage(rendered, content, units)


def test_render_translates_flat_custom_environment_without_replacing_wrapper() -> None:
    tex = r"""
\documentclass{article}
\newenvironment{important}{\begin{quote}}{\end{quote}}
\begin{document}
\begin{important}
Original prose in a custom styled environment.
\end{important}
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
    units = {paragraph.id: _unit(paragraph.id, "カスタム環境内の日本語本文。")}

    rendered = render_translated_latex_source(archive, content, units)

    assert r"\begin{important}" in rendered.main_tex
    assert "カスタム環境内の日本語本文。" in rendered.main_tex
    assert r"\end{important}" in rendered.main_tex
    _validate_render_coverage(rendered, content, units)


def test_render_tcolorbox_title_and_nested_quote_match_structured_blocks() -> None:
    tex = r"""
\documentclass{article}
\usepackage[most]{tcolorbox}
\begin{document}
\begin{tcolorbox}[
  breakable,
  title={Visible example title},
  colback=blue!3
]
Question text.

\begin{quote}\small
Choice A or choice B.
\end{quote}

Answer text.
\end{tcolorbox}

Following paragraph.
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    tracked = [
        block
        for _section, block in content.iter_blocks()
        if block.type in {"paragraph", "quote"}
    ]
    assert [block.type for block in tracked] == [
        "paragraph",
        "paragraph",
        "quote",
        "paragraph",
        "paragraph",
    ]
    translations = ["表示例の題名", "質問文。", "選択肢AまたはB。", "回答文。", "後続段落。"]
    units = {
        block.id: _unit(block.id, translation, [{"t": "text", "v": translation}])
        for block, translation in zip(tracked, translations, strict=True)
    }

    rendered = render_translated_latex_source(archive, content, units)

    assert "title={表示例の題名}" in rendered.main_tex
    assert r"\begin{quote}\small" in rendered.main_tex
    assert "選択肢AまたはB。" in rendered.main_tex
    assert "後続段落。" in rendered.main_tex
    _validate_render_coverage(rendered, content, units)


def test_nested_quote_in_custom_environment_does_not_shift_following_blocks() -> None:
    tex = r"""
\documentclass{article}
\newenvironment{important}{\begin{quote}}{\end{quote}}
\begin{document}
\begin{important}
Question heading.

\begin{quote}
Choice A or choice B.
\end{quote}

Answer text.
\end{important}

Following paragraph.
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    paragraphs = [block for _section, block in content.iter_blocks() if block.type == "paragraph"]
    units = {
        block.id: _unit(
            block.id,
            f"訳文{index}。",
            [
                {
                    "t": "text",
                    "v": "引用 選択肢A、選択肢B。 引用" if index == 2 else f"訳文{index}。",
                }
            ],
        )
        for index, block in enumerate(paragraphs, start=1)
    }

    rendered = render_translated_latex_source(archive, content, units)

    assert r"\begin{important}" in rendered.main_tex
    assert r"\begin{quote}" in rendered.main_tex
    assert "訳文4。" in rendered.main_tex
    assert "引用" not in rendered.main_tex
    _validate_render_coverage(rendered, content, units)


def test_nested_textcolor_style_is_preserved_without_leaking_color_name() -> None:
    tex = r"""
\documentclass{article}
\usepackage{xcolor}
\begin{document}
\textbf{\textcolor{orange}{Question.}} Original question.
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
    unit = _unit(
        paragraph.id,
        "問い。",
        [
            {
                "t": "emphasis",
                "children": [{"t": "text", "v": "orange{問い。}"}],
            },
            {"t": "text", "v": " 日本語の設問。"},
        ],
    )

    rendered = render_translated_latex_source(archive, content, {paragraph.id: unit})

    assert r"\textbf{\textcolor{orange}{問い。}}" in rendered.main_tex
    assert r"orange\{" not in rendered.main_tex


def test_render_reuses_source_inline_styles_and_math_delimiters() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\textbf{Important} result \citep[see][p.~2]{paper} uses \(x+1\) and \[y+2\].
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
    units = {
        paragraph.id: _unit(
            paragraph.id,
            "重要な結果。",
            [
                {
                    "t": "emphasis",
                    "children": [{"t": "text", "v": "重要"}],
                },
                {"t": "text", "v": "な結果"},
                {"t": "citation", "ref": "paper"},
                {"t": "text", "v": "は"},
                {"t": "math_inline", "v": "x+1"},
                {"t": "text", "v": "と"},
                {"t": "math_inline", "v": "y+2"},
                {"t": "text", "v": "を使う。"},
            ],
        )
    }

    rendered = render_translated_latex_source(archive, content, units)

    assert r"\textbf{重要}" in rendered.main_tex
    assert r"\citep[see][p.~2]{paper}" in rendered.main_tex
    assert r"\(x+1\)" in rendered.main_tex
    assert r"\[y+2\]" in rendered.main_tex
    _validate_render_coverage(rendered, content, units)


def test_render_restores_optional_linebreak_instead_of_printing_dimension() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
First line.\\[1pt]
Second line.
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
    unit = _unit(
        paragraph.id,
        "1行目。[1pt]2行目。",
        [{"t": "text", "v": "1行目。[1pt]2行目。"}],
    )

    rendered = render_translated_latex_source(archive, content, {paragraph.id: unit})

    assert "1行目。" + r"\\[1pt]" + "2行目。" in rendered.main_tex


def test_bib_only_project_keeps_original_bibliography_style_for_latexmk() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\section{Intro}
Source text \cite{paper}.
\bibliographystyle{plainnat}
\bibliography{refs}
\end{document}
"""
    bib = "@article{paper, author={A. Author}, title={Paper}, year={2026}}"
    archive = LatexArchive({"main.tex": tex, "refs.bib": bib}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    heading = next(block for _section, block in content.iter_blocks() if block.type == "heading")
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
    units = {
        heading.id: _unit(heading.id, "序論"),
        paragraph.id: _unit(
            paragraph.id,
            "日本語本文。",
            [
                {"t": "text", "v": "日本語本文"},
                {"t": "citation", "ref": "paper"},
                {"t": "text", "v": "。"},
            ],
        ),
    }

    rendered = render_translated_latex_source(archive, content, units)

    assert r"\bibliographystyle{plainnat}" in rendered.main_tex
    assert r"\bibliography{refs}" in rendered.main_tex
    assert r"\begin{thebibliography}" not in rendered.main_tex
    assert rendered.support_text_files["refs.bib"] == bib


def test_source_revision_mismatch_is_rejected_before_positional_replacement() -> None:
    source = r"\documentclass{article}\begin{document}Source A.\end{document}"
    other = r"\documentclass{article}\begin{document}Different source B.\end{document}"
    archive = LatexArchive({"main.tex": source}, {})
    content = parse_latex_source("main.tex", {"main.tex": other}).to_document_content()

    try:
        _validate_source_revision_match(archive, content)
    except LatexPdfBuildError as exc:
        assert exc.kind == "source_revision_mismatch"
    else:
        raise AssertionError("source/revision mismatch was accepted")


def test_source_revision_match_accepts_carried_over_block_ids() -> None:
    source = r"\documentclass{article}\begin{document}\section{Intro}Source A.\end{document}"
    archive = LatexArchive({"main.tex": source}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    for index, (_section, block) in enumerate(content.iter_blocks(), start=1):
        block.id = f"blk-carried-{index}"

    _validate_source_revision_match(archive, content)


def test_real_multifile_project_maps_every_translated_block_and_keeps_layout() -> None:
    fixture = (
        Path(__file__).parents[3] / "packages/py-core/tests/fixtures/latex_rectified_flow.tar.gz"
    )
    source_bytes = fixture.read_bytes()
    archive = extract_latex_archive(source_bytes)
    content = parse_arxiv_latex(source_bytes).to_document_content()

    def translated_inlines(inlines: list[object]) -> list[dict[str, object]]:
        translated: list[dict[str, object]] = []
        for inline in inlines:
            data = inline.model_dump(exclude_none=True)  # type: ignore[union-attr]
            if data.get("t") == "text":
                data["v"] = "訳" + str(data.get("v") or "")
            translated.append(data)
        return translated

    units: dict[str, TranslationUnit] = {}
    for _section, block in content.iter_blocks():
        if block.type == "heading":
            if block.title == "References":
                continue
            inlines = [{"t": "text", "v": "訳" + str(block.title or "")}]
        elif block.type in {"figure", "table"}:
            inlines = translated_inlines(list(block.caption))
        elif block.type == "list":
            inlines = []
            for index, item in enumerate(block.items):
                if index:
                    inlines.append({"t": "text", "v": "\n- "})
                inlines.extend(translated_inlines(list(item)))
        elif block.type in {"paragraph", "quote", "theorem", "footnote"}:
            inlines = translated_inlines(list(block.inlines))
        else:
            continue
        units[block.id] = _unit(block.id, "日本語訳", inlines)

    _validate_source_revision_match(archive, content)
    rendered = render_translated_latex_source(
        archive,
        content,
        units,
        abstract_ja="日本語の概要。",
    )

    _validate_render_coverage(rendered, content, units)
    assert len(rendered.replaced_block_ids) == len(units) == 15
    assert r"\documentclass{article}" in rendered.main_tex
    assert r"\begin{equation}" in rendered.main_tex
    assert r"\begin{table}[t]" in rendered.main_tex
    assert r"\includegraphics{x1.png}" in rendered.main_tex
    assert r"\begin{thebibliography}{9}" in rendered.main_tex


@pytest.mark.skipif(
    os.getenv("ALINEA_TEST_LATEX_DOCKER") != "1",
    reason="set ALINEA_TEST_LATEX_DOCKER=1 with the TeX Live image available",
)
def test_lualatex_container_compiles_searchable_japanese_pdf() -> None:
    tex = r"""
\documentclass[twocolumn,a4paper]{article}
\begin{document}
\section{Introduction}
Original body text.
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    heading = next(block for _section, block in content.iter_blocks() if block.type == "heading")
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
    units = {
        heading.id: _unit(heading.id, "はじめに"),
        paragraph.id: _unit(paragraph.id, "検索可能な日本語PDFの本文です。"),
    }
    rendered = render_translated_latex_source(archive, content, units)

    with tempfile.TemporaryDirectory(prefix="alinea-latex-test-") as tmp:
        root = Path(tmp)
        _write_rendered_source(root, rendered)
        pdf = _compile_with_docker(
            root,
            rendered.main_tex_name,
            image=os.getenv("ALINEA_TEXLIVE_IMAGE", DEFAULT_TEXLIVE_IMAGE),
            timeout_s=180,
        )

    _validate_translated_pdf(pdf)
    document = fitz.open(stream=pdf, filetype="pdf")
    try:
        text = "\n".join(page.get_text("text") for page in document)
        assert "はじめに" in text
        assert "検索可能な日本語PDF" in text
        assert "本文です" in text
    finally:
        document.close()
