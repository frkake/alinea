from __future__ import annotations

import dataclasses
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

import alinea_worker.latex_pdf as latex_pdf
import fitz
import pytest
from alinea_core.db.models import (
    DocumentRevision,
    Paper,
    SourceAsset,
    TranslationSet,
    TranslationUnit,
    User,
)
from alinea_core.document.blocks import Block, DocumentContent, Section
from alinea_core.document.inlines import Inline
from alinea_core.parsing.latex_parser import (
    LatexArchive,
    extract_latex_archive,
    parse_arxiv_latex,
    parse_latex_source,
)
from alinea_core.settings import CoreSettings
from alinea_core.storage.s3 import StorageKeys
from alinea_worker.latex_pdf import (
    _SOURCE_RENDER_MIN_COVERAGE,
    DEFAULT_TEXLIVE_IMAGE,
    PDF_BUILD_VERSION,
    LatexPdfBuildError,
    RenderedLatexSource,
    _compile_with_docker,
    _find_overfull_boxes,
    _find_pdf_page_bound_violations,
    _source_render_manifest,
    _translation_units_digest,
    _validate_render_coverage,
    _validate_render_manifest,
    _validate_source_revision_match,
    _validate_translated_pdf,
    _write_rendered_source,
    render_translated_latex_source,
)
from alinea_worker.structured_pdf import PdfRenderManifest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def _unit(block_id: str, text: str, content: object | None = None) -> TranslationUnit:
    return TranslationUnit(
        set_id="00000000-0000-0000-0000-000000000000",
        block_id=block_id,
        source_hash=f"h-{block_id}",
        content_ja=content if content is not None else [{"t": "text", "v": text}],
        text_ja=text,
        state="machine",
        quality_flags=[],
        model="test",
    )


def _typed_table_unit(
    block_id: str,
    *,
    caption: str,
    cells: list[list[str | None]],
) -> TranslationUnit:
    projection = "\n".join(
        [caption, *(value for row in cells for value in row if value is not None)]
    )
    return _unit(
        block_id,
        projection,
        {
            "kind": "table",
            "version": 1,
            "caption": [{"t": "text", "v": caption}],
            "cells": cells,
        },
    )


def _complex_table_source() -> str:
    return r"""
\documentclass{article}
\usepackage{booktabs}
\usepackage{multirow}
\begin{document}
\begin{table}[t]
\caption[Short caption]{Original table caption.}
\label{tab:metrics}
\centering
\begin{tabular}{lll}
\toprule
\multicolumn{2}{c}{Method family} & Score \\
\cmidrule(lr){1-2}
\multirow{2}{*}{Baseline} & Fast mode $y_2$ & $x_1$ \\
 & Accurate mode \(z_3\) and \[w_4\] & 95\% \\[2pt]
\bottomrule
\end{tabular}
\end{table}
\end{document}
"""


def test_typed_table_rendering_invalidates_caption_only_pdf_cache() -> None:
    assert PDF_BUILD_VERSION == "japanese-pdf-3.0.14"


def test_render_manifest_rejects_missing_or_source_fallback_blocks() -> None:
    manifest = PdfRenderManifest(
        expected_block_ids=frozenset({"a", "b"}),
        translated_block_ids=frozenset({"a"}),
        source_fallback_block_ids=frozenset({"b"}),
    )

    with pytest.raises(LatexPdfBuildError) as captured:
        _validate_render_manifest(manifest)

    assert captured.value.kind == "translated_pdf_incomplete"
    assert captured.value.detail == {"missing": ["b"], "fallback": ["b"]}


def _paragraph_content_and_units(count: int) -> tuple[DocumentContent, dict[str, TranslationUnit]]:
    blocks = [Block(id=f"p{index}", type="paragraph") for index in range(count)]
    content = DocumentContent(quality_level="A", sections=[Section(id="sec-1", blocks=blocks)])
    units = {block.id: _unit(block.id, f"訳文{index}") for index, block in enumerate(blocks)}
    return content, units


def _rendered_source_stub(replaced_block_ids: frozenset[str]) -> RenderedLatexSource:
    return RenderedLatexSource(
        main_tex_name="main.tex",
        main_tex="",
        support_text_files={},
        binary_files={},
        replacements={"paragraph": len(replaced_block_ids)},
        replaced_block_ids=replaced_block_ids,
        warnings=[],
    )


def test_validate_render_coverage_accepts_fully_mapped_document() -> None:
    """全ブロックが原文へ書き戻せた場合は従来通り何も起きない(回帰確認)。"""
    content, units = _paragraph_content_and_units(5)
    rendered = _rendered_source_stub(frozenset(units))

    _validate_render_coverage(rendered, content, units)

    assert rendered.warnings == []


def test_validate_render_coverage_tolerates_small_missing_fraction() -> None:
    """20件中1件だけ原文へ書き戻せなくても(95% >= 90%のしきい値)例外にはならず、
    原文レイアウトを維持したまま manifest の source_fallback に計上される。
    """
    content, units = _paragraph_content_and_units(20)
    replaced_block_ids = frozenset(block_id for block_id in units if block_id != "p0")
    rendered = _rendered_source_stub(replaced_block_ids)

    _validate_render_coverage(rendered, content, units)

    assert any("p0" in warning for warning in rendered.warnings)
    manifest = _source_render_manifest(rendered, content, units)
    assert manifest.expected_block_ids == frozenset(units)
    assert manifest.translated_block_ids == replaced_block_ids
    assert manifest.source_fallback_block_ids == frozenset({"p0"})


def test_validate_render_coverage_raises_when_missing_fraction_is_large() -> None:
    """20件中10件しか原文へ書き戻せない(50% < 90%のしきい値)場合は、
    正規化の不一致では説明できない本当のマッピング破綻として従来通り例外を投げる。
    """
    content, units = _paragraph_content_and_units(20)
    replaced_block_ids = frozenset(f"p{index}" for index in range(10))
    rendered = _rendered_source_stub(replaced_block_ids)

    with pytest.raises(LatexPdfBuildError) as captured:
        _validate_render_coverage(rendered, content, units)

    assert captured.value.kind == "translation_mapping_incomplete"
    assert captured.value.detail["expected"] == 20
    assert captured.value.detail["replaced"] == 10


def test_source_render_min_coverage_threshold_is_defensible() -> None:
    assert _SOURCE_RENDER_MIN_COVERAGE == 0.9


def test_render_typed_table_replaces_only_physical_cell_bodies() -> None:
    tex = _complex_table_source()
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    table = next(block for _section, block in content.iter_blocks() if block.type == "table")
    unit = _typed_table_unit(
        table.id,
        caption="日本語キャプション 100%",
        cells=[
            ["手法群", "得点"],
            ["基準_法", "高速 & 安全 $y_2$", None],
            [None, r"高精度 \[w_4\] と \(z_3\)", None],
        ],
    )
    unit.text_ja = ""

    rendered = render_translated_latex_source(archive, content, {table.id: unit})

    assert r"\caption[Short caption]{日本語キャプション 100\%}" in rendered.main_tex
    assert r"\label{tab:metrics}" in rendered.main_tex
    assert r"\multicolumn{2}{c}{手法群}" in rendered.main_tex
    assert r"\multirow{2}{*}{基準\_法}" in rendered.main_tex
    assert "高速 " + r"\&" + r" 安全 $y_2$" in rendered.main_tex
    assert "高精度" in rendered.main_tex
    assert r"\(z_3\)" in rendered.main_tex
    assert r"\[w_4\]" in rendered.main_tex
    assert r"\toprule" in rendered.main_tex
    assert r"\cmidrule(lr){1-2}" in rendered.main_tex
    assert r"\bottomrule" in rendered.main_tex
    assert r"$x_1$" in rendered.main_tex
    assert r"95\% \\[2pt]" in rendered.main_tex
    assert "Method family" not in rendered.main_tex
    assert "Fast mode" not in rendered.main_tex
    assert rendered.replacements["table"] == 1
    assert table.id in rendered.replaced_block_ids
    assert rendered.warnings == []


def test_render_typed_table_shape_mismatch_keeps_entire_table_and_warns() -> None:
    tex = _complex_table_source()
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    table = next(block for _section, block in content.iter_blocks() if block.type == "table")
    unit = _typed_table_unit(
        table.id,
        caption="置換してはいけないキャプション",
        cells=[["行数が不一致"]],
    )
    source_table = tex[tex.index(r"\begin{table}") : tex.index(r"\end{table}") + 11]

    rendered = render_translated_latex_source(archive, content, {table.id: unit})
    rendered_table = rendered.main_tex[
        rendered.main_tex.index(r"\begin{table}") : rendered.main_tex.index(r"\end{table}") + 11
    ]

    assert rendered_table == source_table
    assert table.id in rendered.replaced_block_ids
    assert any(table.id in warning and "セル" in warning for warning in rendered.warnings)
    _validate_render_coverage(rendered, content, {table.id: unit})


def test_render_typed_table_rejects_unprotected_math_atomically() -> None:
    tex = _complex_table_source()
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    table = next(block for _section, block in content.iter_blocks() if block.type == "table")
    unit = _typed_table_unit(
        table.id,
        caption="置換してはいけないキャプション",
        cells=[
            ["手法 $not_in_source$", "得点"],
            ["基準法", "高速 $y_2$", None],
            [None, r"高精度 \(z_3\) と \[w_4\]", None],
        ],
    )
    source_table = tex[tex.index(r"\begin{table}") : tex.index(r"\end{table}") + 11]

    rendered = render_translated_latex_source(archive, content, {table.id: unit})
    rendered_table = rendered.main_tex[
        rendered.main_tex.index(r"\begin{table}") : rendered.main_tex.index(r"\end{table}") + 11
    ]

    assert rendered_table == source_table
    assert any(table.id in warning and "セル" in warning for warning in rendered.warnings)


def test_render_unsupported_typed_cell_grid_keeps_caption_and_cells_unchanged() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\begin{table}
\caption{Original unsupported caption.}
\begin{tabular}{ll}
\multicolumn{x}{c}{Method name} & Score \\
\end{tabular}
\end{table}
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    table = next(block for _section, block in content.iter_blocks() if block.type == "table")
    unit = _typed_table_unit(
        table.id,
        caption="置換してはいけないキャプション",
        cells=[["手法名", "得点"]],
    )
    source_table = tex[tex.index(r"\begin{table}") : tex.index(r"\end{table}") + 11]

    rendered = render_translated_latex_source(archive, content, {table.id: unit})
    rendered_table = rendered.main_tex[
        rendered.main_tex.index(r"\begin{table}") : rendered.main_tex.index(r"\end{table}") + 11
    ]

    assert rendered_table == source_table
    assert any(table.id in warning and "セル" in warning for warning in rendered.warnings)


def test_render_typed_tabular_star_preserves_width_and_column_specification() -> None:
    tex = r"""
\documentclass{article}
\begin{document}
\begin{table}
\caption{Original tabular star caption.}
\begin{tabular*}{\linewidth}{@{\extracolsep{\fill}}ll}
Method name & Score \\
Baseline method & 95 \\
\end{tabular*}
\end{table}
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    table = next(block for _section, block in content.iter_blocks() if block.type == "table")
    unit = _typed_table_unit(
        table.id,
        caption="日本語の評価表。",
        cells=[["手法名", "得点"], ["基準手法", None]],
    )

    rendered = render_translated_latex_source(archive, content, {table.id: unit})

    assert r"\begin{tabular*}{\linewidth}{@{\extracolsep{\fill}}ll}" in rendered.main_tex
    assert r"\end{tabular*}" in rendered.main_tex
    assert r"\caption{日本語の評価表。}" in rendered.main_tex
    assert "手法名 & 得点" in rendered.main_tex
    assert "基準手法 & 95" in rendered.main_tex
    assert rendered.warnings == []


def test_empty_projection_typed_content_is_displayable_only_for_table_blocks() -> None:
    tex = r"\documentclass{article}\begin{document}Original paragraph.\end{document}"
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
    corrupt = _unit(
        paragraph.id,
        "",
        {"kind": "table", "version": 1, "caption": None, "cells": None},
    )

    rendered = render_translated_latex_source(archive, content, {paragraph.id: corrupt})

    assert "Original paragraph." in rendered.main_tex
    assert paragraph.id not in rendered.replaced_block_ids


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


def test_render_multi_image_figure_does_not_shift_following_figure_captions() -> None:
    """1つの figure* に複数枚の \\includegraphics があると、パーサー(_figure_env)は
    画像ごとに figure ブロックを1枚ずつ作り、キャプションは先頭の画像だけに付与する。
    レンダラー側がこれに合わせて複数ブロックを消費しないと、後続の figure の位置対応が
    1つずれ、無関係なキャプションが入れ替わって描画されてしまう
    (Attention Is All You Need 論文の翻訳PDFで検出された回帰)。
    """
    tex = r"""
\documentclass{article}
\usepackage{graphicx}
\begin{document}
\begin{figure*}
\includegraphics{a1.pdf}
\includegraphics{a2.pdf}
\caption{First multi-image caption.}
\end{figure*}

\begin{figure*}
\includegraphics{b1.pdf}
\caption{Second single-image caption.}
\end{figure*}
\end{document}
"""
    archive = LatexArchive(
        {"main.tex": tex},
        {"a1.pdf": b"%PDF-1.4\n%%EOF\n", "a2.pdf": b"%PDF-1.4\n%%EOF\n", "b1.pdf": b"%PDF-1.4\n%%EOF\n"},
    )
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    figures = [block for _section, block in content.iter_blocks() if block.type == "figure"]
    # 1枚目の figure* は2枚の画像で2ブロックに分かれ、先頭だけキャプションを持つ。
    assert len(figures) == 3
    captioned_first, captionless, captioned_second = figures
    units = {
        captioned_first.id: _unit(
            captioned_first.id,
            "最初のキャプション。",
            [{"t": "text", "v": "最初のキャプション。"}],
        ),
        captioned_second.id: _unit(
            captioned_second.id,
            "二番目のキャプション。",
            [{"t": "text", "v": "二番目のキャプション。"}],
        ),
    }

    rendered = render_translated_latex_source(archive, content, units)

    assert r"\caption{最初のキャプション。}" in rendered.main_tex
    assert r"\caption{二番目のキャプション。}" in rendered.main_tex
    assert "First multi-image caption" not in rendered.main_tex
    assert "Second single-image caption" not in rendered.main_tex
    assert rendered.replaced_block_ids == {captioned_first.id, captioned_second.id}
    _validate_render_coverage(rendered, content, units)


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
    # 12pt 程度のわずかな食い込みは _PDF_BOUNDS_TOLERANCE_PT の許容範囲に収まるため、
    # 本当にページ外へ大きく突き出したケース(32pt 分の overflow)で検証する。
    doc = fitz.open()
    try:
        page = doc.new_page(width=300, height=420)
        page.insert_text((292, 200), "outside page bounds", fontsize=72)

        findings = _find_pdf_page_bound_violations(doc)
    finally:
        doc.close()

    assert findings
    assert "kind=text" in findings[0]


def test_find_pdf_page_bound_violations_ignores_small_overfull_drift() -> None:
    """日本語は原文の英語より字幅が広く、原文レイアウトを保ったまま再コンパイルすると
    数 pt 程度の Overfull box が出るのは正常な範囲(§_PDF_BOUNDS_TOLERANCE_PT)。
    このようなわずかな食い込みだけで汎用レイアウトへフォールバックしてはならない。
    """
    doc = fitz.open()
    try:
        page = doc.new_page(width=300, height=420)
        # 通常サイズのフォントでページ右端からわずかにあふれる程度の食い込み。
        page.insert_text((292, 72), "outside page bounds", fontsize=12)

        findings = _find_pdf_page_bound_violations(doc)
    finally:
        doc.close()

    assert findings == []


def _build_pdf_with_japanese_and_backslash_text(*, extra: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "日本語の本文です。", fontsize=12, fontname="japan-s")
    page.insert_text((72, 100), extra, fontsize=12)
    data = bytes(doc.tobytes())
    doc.close()
    return data


def test_validate_translated_pdf_ignores_code_and_url_backslash_tokens() -> None:
    """コードリスティングやファイルパス、URL 中の
    エスケープされたコマンドは、生の TeX が漏れた
    証拠ではないので visible_latex では検知しない。
    """
    pdf_bytes = _build_pdf_with_japanese_and_backslash_text(
        extra=r"See C:\Users\name and \_foo or \% for details, plus \alpha.",
    )

    _validate_translated_pdf(pdf_bytes)


def test_validate_translated_pdf_still_rejects_leaked_section_command() -> None:
    r"""未展開の `\section{...}` が可視テキストへ残っている場合は
    本当のレイアウト破綻(LuaLaTeX がマクロを展開できなかった)
    なので、引き続き検知する。
    """
    pdf_bytes = _build_pdf_with_japanese_and_backslash_text(
        extra=r"\section{Leaked heading}",
    )

    with pytest.raises(LatexPdfBuildError) as captured:
        _validate_translated_pdf(pdf_bytes)

    assert captured.value.kind == "visible_latex"


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


def test_comment_only_source_line_preserves_parser_paragraph_boundaries() -> None:
    parsed_tex = r"""
\documentclass{article}
\begin{document}
First source paragraph.

Second source paragraph.
\end{document}
"""
    raw_tex = parsed_tex.replace(
        "First source paragraph.\n\nSecond source paragraph.",
        "First source paragraph.\n% editorial note retained for rebuild\nSecond source paragraph.",
    )
    archive = LatexArchive(
        {"main.tex": parsed_tex},
        {},
        {"main.tex": raw_tex},
    )
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    paragraphs = [block for _section, block in content.iter_blocks() if block.type == "paragraph"]
    assert len(paragraphs) == 2
    units = {
        paragraphs[0].id: _unit(paragraphs[0].id, "最初の訳文。"),
        paragraphs[1].id: _unit(paragraphs[1].id, "二番目の訳文。"),
    }

    rendered = render_translated_latex_source(archive, content, units)

    assert "最初の訳文。" in rendered.main_tex
    assert "二番目の訳文。" in rendered.main_tex
    assert "% editorial note retained for rebuild" in rendered.main_tex
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
        block for _section, block in content.iter_blocks() if block.type in {"paragraph", "quote"}
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

    def translated_inlines(inlines: list[Inline]) -> list[dict[str, object]]:
        translated: list[dict[str, object]] = []
        for inline in inlines:
            data = inline.model_dump(exclude_none=True)
            if data.get("t") == "text":
                data["v"] = "訳" + str(data.get("v") or "")
            translated.append(data)
        return translated

    units: dict[str, TranslationUnit] = {}
    for _section, block in content.iter_blocks():
        inlines: list[dict[str, object]]
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


async def test_personal_translation_pdf_uses_set_scoped_key_and_shared_base_units(
    db_session: AsyncSession,
    settings: CoreSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # この論文は見出し 1 個しか翻訳されていない(典型的な部分翻訳スコープ)。
    # 以前は partial_translation_scope ゲートが常に汎用レイアウトへ後退させていたが、
    # それでは実運用のほとんどの論文で原文レイアウト維持が失われてしまうため、
    # 訳がある見出しだけ置換し、原文レイアウトを維持する source レンダラーを使う。
    source_bytes = (
        Path(__file__).parents[3] / "packages/py-core/tests/fixtures/latex_rectified_flow.tar.gz"
    ).read_bytes()
    content = parse_arxiv_latex(source_bytes).to_document_content()
    heading = next(
        block
        for _section, block in content.iter_blocks()
        if block.type == "heading" and block.title != "References"
    )

    user = User(id=str(uuid.uuid4()), email=f"pdf-{uuid.uuid4().hex}@test.invalid")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(
        id=str(uuid.uuid4()),
        title="Personal translated PDF",
        visibility="private",
        owner_user_id=user.id,
    )
    db_session.add(paper)
    await db_session.flush()
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(mode="json"),
        stats={},
    )
    db_session.add(revision)
    await db_session.flush()
    paper.latest_revision_id = revision.id
    shared_set = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision.id,
        style="natural",
        scope="shared",
        status="complete",
        glossary_snapshot=[],
    )
    db_session.add(shared_set)
    await db_session.flush()
    personal_set = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision.id,
        style="natural",
        scope="personal",
        user_id=user.id,
        base_set_id=shared_set.id,
        status="complete",
        glossary_snapshot=[],
    )
    source_key = StorageKeys.latex_tar(str(paper.id), "v1")
    db_session.add_all(
        [
            personal_set,
            TranslationUnit(
                set_id=shared_set.id,
                block_id=heading.id,
                source_hash="heading-source-hash",
                content_ja=[{"t": "text", "v": "共有基底の見出し"}],
                text_ja="共有基底の見出し",
                state="machine",
                quality_flags=[],
                model="test",
            ),
            SourceAsset(
                paper_id=paper.id,
                kind="arxiv_latex",
                source_version="v1",
                storage_key=source_key,
                content_type="application/gzip",
                byte_size=len(source_bytes),
            ),
        ]
    )
    await db_session.commit()

    class MemoryStorage:
        sources_bucket = "sources"

        def __init__(self) -> None:
            self.puts: list[tuple[str, bytes, dict[str, Any]]] = []

        async def get(self, bucket: str, key: str) -> bytes:
            assert bucket == self.sources_bucket
            assert key == source_key
            return source_bytes

        async def put(
            self,
            bucket: str,
            key: str,
            data: bytes,
            **kwargs: Any,
        ) -> None:
            assert bucket == self.sources_bucket
            self.puts.append((key, data, kwargs))

    rendered_sources: list[str] = []

    async def fake_compile(rendered: Any, *, image: str, timeout_s: int) -> bytes:
        del image, timeout_s
        rendered_sources.append(rendered.main_tex)
        return b"personal-pdf"

    monkeypatch.setattr(latex_pdf, "_compile_rendered_source", fake_compile)
    monkeypatch.setattr(latex_pdf, "_validate_translated_pdf", lambda _data: None)
    storage = MemoryStorage()

    outcome = await latex_pdf.build_latex_translation_pdfs_if_ready(
        db_session,
        storage,  # type: ignore[arg-type]
        settings,
        set_id=str(personal_set.id),
    )

    expected_key = StorageKeys.translated_pdf(
        str(paper.id),
        "v1",
        "natural",
        translation_set_id=str(personal_set.id),
    )
    assert outcome.built is True
    assert outcome.renderer == "source"
    assert outcome.fallback_reason is None
    assert outcome.translated_key == expected_key
    assert storage.puts[0][0] == expected_key
    assert storage.puts[0][1] == b"personal-pdf"
    assert storage.puts[0][2]["metadata"]["translation_set_id"] == str(personal_set.id)
    assert "共有基底の見出し" in rendered_sources[0]
    assert StorageKeys.translated_pdf(str(paper.id), "v1", "natural") != expected_key

    asset = (
        await db_session.execute(select(SourceAsset).where(SourceAsset.storage_key == expected_key))
    ).scalar_one()
    await db_session.refresh(revision)
    assert asset.source_url == f"translation-set:{personal_set.id}"
    assert (
        revision.stats["translated_pdf"][f"natural:{personal_set.id}"]["storage_key"]
        == expected_key
    )


async def test_build_translation_pdf_uses_source_renderer_with_untranslated_references_and_appendix(
    db_session: AsyncSession,
    settings: CoreSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """compute_translation_scope(packages/py-core 側)は参考文献セクションを常に翻訳対象外にし、
    付録も既定設定次第で対象外にする。そのため実運用では units が全 TRANSLATABLE_BLOCK_TYPES
    ブロックを覆わない論文がほとんどであり、それを理由に汎用レイアウトへ後退してはならない
    (partial_translation_scope ゲート撤廃の回帰テスト)。
    """
    source_bytes = (
        Path(__file__).parents[3] / "packages/py-core/tests/fixtures/latex_rectified_flow.tar.gz"
    ).read_bytes()
    content = parse_arxiv_latex(source_bytes).to_document_content()

    def translated_inlines(inlines: list[Inline]) -> list[dict[str, object]]:
        translated: list[dict[str, object]] = []
        for inline in inlines:
            data = inline.model_dump(exclude_none=True)
            if data.get("t") == "text":
                data["v"] = "訳" + str(data.get("v") or "")
            translated.append(data)
        return translated

    # 参考文献(References)と付録(Proofs, \appendix 配下)は未翻訳のまま残す。
    excluded_headings = {"References", "Proofs"}
    skip_section = False
    units_payload: dict[str, list[dict[str, object]]] = {}
    for _section, block in content.iter_blocks():
        if block.type == "heading":
            skip_section = block.title in excluded_headings
            if skip_section:
                continue
            units_payload[block.id] = [{"t": "text", "v": "訳" + str(block.title or "")}]
            continue
        if skip_section:
            continue
        if block.type in {"figure", "table"}:
            units_payload[block.id] = translated_inlines(list(block.caption))
        elif block.type == "list":
            inlines: list[dict[str, object]] = []
            for index, item in enumerate(block.items):
                if index:
                    inlines.append({"t": "text", "v": "\n- "})
                inlines.extend(translated_inlines(list(item)))
            units_payload[block.id] = inlines
        elif block.type in {"paragraph", "quote", "theorem", "footnote"}:
            units_payload[block.id] = translated_inlines(list(block.inlines))
        # equation / algorithm / code / reference_entry はそもそも翻訳対象外。

    user = User(id=str(uuid.uuid4()), email=f"pdf-scope-{uuid.uuid4().hex}@test.invalid")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(
        id=str(uuid.uuid4()),
        title="Untranslated references and appendix",
        visibility="public",
        owner_user_id=user.id,
    )
    db_session.add(paper)
    await db_session.flush()
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(mode="json"),
        stats={},
    )
    db_session.add(revision)
    await db_session.flush()
    paper.latest_revision_id = revision.id
    tset = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision.id,
        style="natural",
        scope="shared",
        status="complete",
        glossary_snapshot=[],
    )
    db_session.add(tset)
    await db_session.flush()
    source_key = StorageKeys.latex_tar(str(paper.id), "v1")
    db_session.add_all(
        [
            TranslationUnit(
                set_id=tset.id,
                block_id=block_id,
                source_hash=f"hash-{block_id}",
                content_ja=content_ja,
                text_ja="".join(str(item.get("v") or "") for item in content_ja),
                state="machine",
                quality_flags=[],
                model="test",
            )
            for block_id, content_ja in units_payload.items()
        ]
        + [
            SourceAsset(
                paper_id=paper.id,
                kind="arxiv_latex",
                source_version="v1",
                storage_key=source_key,
                content_type="application/gzip",
                byte_size=len(source_bytes),
            )
        ]
    )
    await db_session.commit()

    class MemoryStorage:
        sources_bucket = "sources"
        assets_bucket = "assets"

        def __init__(self) -> None:
            self.puts: list[tuple[str, bytes, dict[str, Any]]] = []

        async def get(self, bucket: str, key: str) -> bytes:
            assert bucket == self.sources_bucket
            assert key == source_key
            return source_bytes

        async def put(self, bucket: str, key: str, data: bytes, **kwargs: Any) -> None:
            self.puts.append((key, data, kwargs))

    rendered_sources: list[str] = []

    async def fake_compile(rendered: Any, *, image: str, timeout_s: int) -> bytes:
        del image, timeout_s
        rendered_sources.append(rendered.main_tex)
        return b"scoped-source-pdf"

    monkeypatch.setattr(latex_pdf, "_compile_rendered_source", fake_compile)
    monkeypatch.setattr(latex_pdf, "_validate_translated_pdf", lambda _data: None)
    storage = MemoryStorage()

    outcome = await latex_pdf.build_translation_pdfs_if_ready(
        db_session,
        storage,  # type: ignore[arg-type]
        settings,
        set_id=str(tset.id),
    )

    assert outcome.built is True
    assert outcome.renderer == "source"
    assert outcome.fallback_reason is None
    rendered_main_tex = rendered_sources[0]
    assert r"\section{訳Introduction}" in rendered_main_tex
    # 付録(Proofs)は units が無いため原文(英語)のまま残る。
    assert r"\section{Proofs}" in rendered_main_tex
    assert "Appendix proof text that should not be auto translated at all" in rendered_main_tex
    # 参考文献は compute_translation_scope で常に翻訳対象外であり、原文のまま残る。
    assert r"\begin{thebibliography}{9}" in rendered_main_tex
    assert storage.puts[0][1] == b"scoped-source-pdf"


async def test_build_translation_pdf_stays_source_renderer_when_missing_fraction_is_small(
    db_session: AsyncSession,
    settings: CoreSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1/15 ブロック(93% >= 90%のしきい値)だけ原文へ書き戻せなくても、汎用レイアウトへ
    後退せず source レンダラーを維持し、manifest の source_fallback に計上されることを
    確認する(translation_mapping_incomplete の全か無かのグレースフルデグラデーション回帰)。
    """
    source_bytes = (
        Path(__file__).parents[3] / "packages/py-core/tests/fixtures/latex_rectified_flow.tar.gz"
    ).read_bytes()
    content = parse_arxiv_latex(source_bytes).to_document_content()

    def translated_inlines(inlines: list[Inline]) -> list[dict[str, object]]:
        translated: list[dict[str, object]] = []
        for inline in inlines:
            data = inline.model_dump(exclude_none=True)
            if data.get("t") == "text":
                data["v"] = "訳" + str(data.get("v") or "")
            translated.append(data)
        return translated

    units_payload: dict[str, list[dict[str, object]]] = {}
    for _section, block in content.iter_blocks():
        if block.type == "heading":
            if block.title == "References":
                continue
            units_payload[block.id] = [{"t": "text", "v": "訳" + str(block.title or "")}]
        elif block.type in {"figure", "table"}:
            units_payload[block.id] = translated_inlines(list(block.caption))
        elif block.type == "list":
            inlines: list[dict[str, object]] = []
            for index, item in enumerate(block.items):
                if index:
                    inlines.append({"t": "text", "v": "\n- "})
                inlines.extend(translated_inlines(list(item)))
            units_payload[block.id] = inlines
        elif block.type in {"paragraph", "quote", "theorem", "footnote"}:
            units_payload[block.id] = translated_inlines(list(block.inlines))

    user = User(id=str(uuid.uuid4()), email=f"pdf-partial-{uuid.uuid4().hex}@test.invalid")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(
        id=str(uuid.uuid4()),
        title="Small missing mapping fraction",
        visibility="public",
        owner_user_id=user.id,
    )
    db_session.add(paper)
    await db_session.flush()
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="latex",
        content=content.model_dump(mode="json"),
        stats={},
    )
    db_session.add(revision)
    await db_session.flush()
    paper.latest_revision_id = revision.id
    tset = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision.id,
        style="natural",
        scope="shared",
        status="complete",
        glossary_snapshot=[],
    )
    db_session.add(tset)
    await db_session.flush()
    source_key = StorageKeys.latex_tar(str(paper.id), "v1")
    db_session.add_all(
        [
            TranslationUnit(
                set_id=tset.id,
                block_id=block_id,
                source_hash=f"hash-{block_id}",
                content_ja=content_ja,
                text_ja="".join(str(item.get("v") or "") for item in content_ja),
                state="machine",
                quality_flags=[],
                model="test",
            )
            for block_id, content_ja in units_payload.items()
        ]
        + [
            SourceAsset(
                paper_id=paper.id,
                kind="arxiv_latex",
                source_version="v1",
                storage_key=source_key,
                content_type="application/gzip",
                byte_size=len(source_bytes),
            )
        ]
    )
    await db_session.commit()
    assert len(units_payload) == 15

    class MemoryStorage:
        sources_bucket = "sources"
        assets_bucket = "assets"

        def __init__(self) -> None:
            self.puts: list[tuple[str, bytes, dict[str, Any]]] = []

        async def get(self, bucket: str, key: str) -> bytes:
            assert bucket == self.sources_bucket
            assert key == source_key
            return source_bytes

        async def put(self, bucket: str, key: str, data: bytes, **kwargs: Any) -> None:
            self.puts.append((key, data, kwargs))

    dropped_ids: list[str] = []
    original_render = latex_pdf.render_translated_latex_source

    def flaky_render(
        archive: LatexArchive,
        rendered_content: DocumentContent,
        units: dict[str, TranslationUnit],
        *,
        abstract_ja: str | None = None,
    ) -> RenderedLatexSource:
        rendered = original_render(archive, rendered_content, units, abstract_ja=abstract_ja)
        # 15件中1件だけ「原文へ書き戻せなかった」状況を人為的に作り、しきい値以上の
        # カバレッジでは source レンダラーを維持できることを確認する。
        dropped_id = sorted(rendered.replaced_block_ids)[0]
        dropped_ids.append(dropped_id)
        return dataclasses.replace(
            rendered, replaced_block_ids=frozenset(rendered.replaced_block_ids - {dropped_id})
        )

    rendered_sources: list[str] = []

    async def fake_compile(rendered: Any, *, image: str, timeout_s: int) -> bytes:
        del image, timeout_s
        rendered_sources.append(rendered.main_tex)
        return b"partial-source-pdf"

    monkeypatch.setattr(latex_pdf, "render_translated_latex_source", flaky_render)
    monkeypatch.setattr(latex_pdf, "_compile_rendered_source", fake_compile)
    monkeypatch.setattr(latex_pdf, "_validate_translated_pdf", lambda _data: None)
    storage = MemoryStorage()

    outcome = await latex_pdf.build_translation_pdfs_if_ready(
        db_session,
        storage,  # type: ignore[arg-type]
        settings,
        set_id=str(tset.id),
    )

    assert outcome.built is True
    assert outcome.renderer == "source"
    assert outcome.fallback_reason is None
    assert len(dropped_ids) == 1
    assert any(dropped_ids[0] in warning for warning in outcome.warnings)
    assert storage.puts[0][1] == b"partial-source-pdf"

    await db_session.refresh(revision)
    manifest = revision.stats["translated_pdf"]["natural"]["manifest"]
    assert manifest["expected"] == 15
    assert manifest["translated"] == 14
    assert manifest["source_fallback"] == 1


async def test_builds_structured_pdf_for_html_revision(
    db_session: AsyncSession,
    settings: CoreSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = User(id=str(uuid.uuid4()), email=f"html-pdf-{uuid.uuid4().hex}@test.invalid")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(
        id=str(uuid.uuid4()),
        title="HTML translated PDF",
        visibility="public",
        owner_user_id=user.id,
        abstract_ja="日本語の要旨です。",
    )
    db_session.add(paper)
    await db_session.flush()
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="section-1",
                blocks=[
                    Block(id="heading-1", type="heading", level=1, title="Introduction"),
                    Block(id="paragraph-1", type="paragraph"),
                ],
            )
        ],
    )
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        source_version="v1",
        parser_version="html-test",
        quality_level="A",
        source_format="arxiv_html",
        content=content.model_dump(mode="json"),
        stats={},
    )
    db_session.add(revision)
    await db_session.flush()
    paper.latest_revision_id = revision.id
    tset = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision.id,
        style="natural",
        scope="shared",
        status="complete",
        glossary_snapshot=[],
    )
    db_session.add(tset)
    await db_session.flush()
    db_session.add_all(
        [
            TranslationUnit(
                set_id=tset.id,
                block_id="heading-1",
                source_hash="h1",
                content_ja=[{"t": "text", "v": "はじめに"}],
                text_ja="はじめに",
                state="machine",
                quality_flags=[],
                model="test",
            ),
            TranslationUnit(
                set_id=tset.id,
                block_id="paragraph-1",
                source_hash="p1",
                content_ja=[{"t": "text", "v": "日本語の本文です。"}],
                text_ja="日本語の本文です。",
                state="machine",
                quality_flags=[],
                model="test",
            ),
        ]
    )
    await db_session.commit()

    class MemoryStorage:
        sources_bucket = "sources"
        assets_bucket = "assets"

        def __init__(self) -> None:
            self.puts: list[tuple[str, bytes, dict[str, Any]]] = []

        async def get(self, _bucket: str, _key: str) -> bytes:
            raise AssertionError("HTML fixture has no external assets")

        async def put(self, _bucket: str, key: str, data: bytes, **kwargs: Any) -> None:
            self.puts.append((key, data, kwargs))

    rendered_sources: list[str] = []

    async def fake_compile(rendered: Any, *, image: str, timeout_s: int) -> bytes:
        del image, timeout_s
        rendered_sources.append(rendered.main_tex)
        return b"structured-pdf"

    monkeypatch.setattr(latex_pdf, "_compile_rendered_source", fake_compile)
    monkeypatch.setattr(latex_pdf, "_validate_translated_pdf", lambda _data: None)
    storage = MemoryStorage()

    outcome = await latex_pdf.build_translation_pdfs_if_ready(
        db_session,
        storage,  # type: ignore[arg-type]
        settings,
        set_id=str(tset.id),
    )

    assert outcome.built is True
    assert outcome.renderer == "structured"
    assert outcome.fallback_reason == "not_latex"
    assert "日本語の本文です" in rendered_sources[0]
    assert storage.puts[0][1] == b"structured-pdf"
    await db_session.refresh(revision)
    assert revision.stats["translated_pdf"]["natural"]["renderer"] == "structured"


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
\begin{table}
\caption{Original metrics.}
\begin{tabular}{lr}
Method name & Score \\
Baseline method & 95 \\
\end{tabular}
\end{table}
\end{document}
"""
    archive = LatexArchive({"main.tex": tex}, {})
    content = parse_latex_source("main.tex", archive.text_files).to_document_content()
    heading = next(block for _section, block in content.iter_blocks() if block.type == "heading")
    paragraph = next(
        block for _section, block in content.iter_blocks() if block.type == "paragraph"
    )
    table = next(block for _section, block in content.iter_blocks() if block.type == "table")
    units = {
        heading.id: _unit(heading.id, "はじめに"),
        paragraph.id: _unit(paragraph.id, "検索可能な日本語PDFの本文です。"),
        table.id: _typed_table_unit(
            table.id,
            caption="日本語の評価表。",
            cells=[["手法名", "得点"], ["基準手法", None]],
        ),
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
        assert "日本語の評価表" in text
        assert "手法名" in text
        assert "基準手法" in text
    finally:
        document.close()
