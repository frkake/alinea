from __future__ import annotations

from alinea_core.db.models import TranslationUnit
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_worker.structured_pdf import render_structured_japanese_source


def _unit(block_id: str, text: str, content: object | None = None) -> TranslationUnit:
    return TranslationUnit(
        set_id="00000000-0000-0000-0000-000000000000",
        block_id=block_id,
        source_hash=f"hash-{block_id}",
        content_ja=content if content is not None else [{"t": "text", "v": text}],
        text_ja=text,
        state="machine",
        quality_flags=[],
        model="test",
    )


def _content() -> DocumentContent:
    table = Block(
        id="table-1",
        type="table",
        raw="<table><tr><th>Method</th><th>Score</th></tr>"
        "<tr><td>Baseline</td><td>95%</td></tr></table>",
        caption=[Inline(t="text", v="Original table")],
    )
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="section-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(id="heading-1", type="heading", level=1, title="Introduction"),
                    Block(
                        id="paragraph-1",
                        type="paragraph",
                        inlines=[Inline(t="text", v="Original prose")],
                    ),
                    Block(
                        id="list-1",
                        type="list",
                        items=[[Inline(t="text", v="First")], [Inline(t="text", v="Second")]],
                    ),
                    Block(
                        id="figure-1",
                        type="figure",
                        asset_key="figures/example.png",
                        caption=[Inline(t="text", v="Original figure")],
                    ),
                    table,
                    Block(id="equation-1", type="equation", latex=r"E = mc^2"),
                    Block(id="code-1", type="code", code="print('ok')", language="python"),
                    Block(id="reference-1", type="reference_entry", raw="Author. Paper title."),
                ],
            )
        ],
    )


def test_renders_translated_blocks_assets_tables_and_manifest() -> None:
    content = _content()
    units = {
        "heading-1": _unit("heading-1", "はじめに"),
        "paragraph-1": _unit("paragraph-1", r"結果 \@setfontsize \textbf{重要}"),
        "list-1": _unit("list-1", "第一項\n- 第二項"),
        "figure-1": _unit("figure-1", "日本語の図説明"),
        "table-1": _unit(
            "table-1",
            "日本語の表説明\n手法\n基準手法",
            {
                "kind": "table",
                "version": 1,
                "caption": [{"t": "text", "v": "日本語の表説明"}],
                "cells": [["手法", "スコア"], ["基準手法", None]],
            },
        ),
    }

    rendered = render_structured_japanese_source(
        content,
        units,
        abstract_ja="日本語要旨",
        binary_assets={"figures/example.png": b"png-bytes"},
    )

    assert "日本語要旨" in rendered.main_tex
    assert "はじめに" in rendered.main_tex
    assert "重要" in rendered.main_tex
    assert r"\@setfontsize" not in rendered.main_tex
    assert r"\textbf{重要}" not in rendered.main_tex
    assert r"\includegraphics" in rendered.main_tex
    assert r"\begin{longtable}" in rendered.main_tex
    assert "基準手法" in rendered.main_tex
    assert r"E = mc^2" in rendered.main_tex
    assert rendered.binary_files == {"assets/figure-1.png": b"png-bytes"}
    assert rendered.manifest.expected_block_ids == frozenset(units)
    assert rendered.manifest.translated_block_ids == frozenset(units)
    assert rendered.manifest.source_fallback_block_ids == frozenset()


def test_manifest_marks_empty_or_blocked_translation_as_fallback() -> None:
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="section-1",
                blocks=[Block(id="paragraph-1", type="paragraph")],
            )
        ],
    )
    unit = _unit("paragraph-1", "")
    unit.quality_flags = ["provider_refusal"]

    rendered = render_structured_japanese_source(content, {"paragraph-1": unit})

    assert rendered.manifest.expected_block_ids == frozenset({"paragraph-1"})
    assert rendered.manifest.translated_block_ids == frozenset()
    assert rendered.manifest.source_fallback_block_ids == frozenset({"paragraph-1"})


def test_manifest_ignores_intentionally_empty_unflagged_unit() -> None:
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="section-1",
                blocks=[Block(id="figure-1", type="figure", caption=[])],
            )
        ],
    )
    unit = _unit("figure-1", "", [])

    rendered = render_structured_japanese_source(content, {"figure-1": unit})

    assert rendered.manifest.expected_block_ids == frozenset()
    assert rendered.manifest.translated_block_ids == frozenset()
    assert rendered.manifest.source_fallback_block_ids == frozenset()


def test_normalizes_display_alignment_environments_for_resizebox_math() -> None:
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="section-1",
                blocks=[
                    Block(id="paragraph-1", type="paragraph"),
                    Block(
                        id="equation-split",
                        type="equation",
                        latex=r"\begin{split}a &= b \\ c &= d\end{split}",
                    ),
                    Block(
                        id="equation-tabs",
                        type="equation",
                        latex=r"\overline{W}(t) &=& e^{2G(t)} \notag \\ && + C",
                    ),
                    Block(
                        id="equation-empty-leading-cells",
                        type="equation",
                        latex=r"&\qquad a + b \\ &\qquad c + d",
                    ),
                    Block(
                        id="equation-outer-tab-nested-matrix",
                        type="equation",
                        latex=(
                            r"V &= \begin{pmatrix}"
                            r"0 & I \\ I & 0"
                            r"\end{pmatrix}"
                        ),
                    ),
                ],
            )
        ],
    )

    rendered = render_structured_japanese_source(
        content,
        {"paragraph-1": _unit("paragraph-1", "本文")},
    )

    assert r"\begin{split}" not in rendered.main_tex
    assert r"\usepackage{mathtools,amssymb}" in rendered.main_tex
    assert rendered.main_tex.count(r"\begin{aligned}") == 4
    assert rendered.main_tex.count(r"\end{aligned}") == 4
    assert r"\begin{aligned}{}&\qquad a + b \\ {}&\qquad c + d" in rendered.main_tex
    assert (
        r"\begin{aligned}V &= \begin{pmatrix}0 & I \\ I & 0\end{pmatrix}"
        r"\end{aligned}"
    ) in rendered.main_tex
    assert r"\notag" not in rendered.main_tex


def test_drops_standalone_control_space_at_end_of_display_math() -> None:
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="section-1",
                blocks=[
                    Block(id="paragraph-1", type="paragraph"),
                    Block(
                        id="equation-trailing-control-space",
                        type="equation",
                        latex="\\begin{aligned}\\mathcal{L}=1\\end{aligned}\n\\",
                    ),
                ],
            )
        ],
    )

    rendered = render_structured_japanese_source(
        content,
        {"paragraph-1": _unit("paragraph-1", "本文")},
    )

    assert r"\begin{aligned}\mathcal{L}=1\end{aligned}$}" in rendered.main_tex
    assert r"\$}\end{center}" not in rendered.main_tex


def test_drops_class_internal_font_size_expansion_from_display_math() -> None:
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="section-1",
                blocks=[
                    Block(id="paragraph-1", type="paragraph"),
                    Block(
                        id="equation-font-size",
                        type="equation",
                        latex=(
                            r"\begin{aligned}\@setfontsize\scriptsize\@ixpt\@xpt "
                            r"& g_{[n-1]} = 1\end{aligned}"
                        ),
                    ),
                ],
            )
        ],
    )

    rendered = render_structured_japanese_source(
        content,
        {"paragraph-1": _unit("paragraph-1", "本文")},
    )

    assert r"g_{[n-1]} = 1" in rendered.main_tex
    assert r"\@setfontsize" not in rendered.main_tex
    assert r"\scriptsize" not in rendered.main_tex
    assert r"\@ixpt" not in rendered.main_tex


def test_drops_legacy_display_skip_assignments_from_display_math() -> None:
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="section-1",
                blocks=[
                    Block(id="paragraph-1", type="paragraph"),
                    Block(
                        id="equation-font-size-expansion",
                        type="equation",
                        latex=(
                            r"\@setfontsize\scriptsize{10bp}{12bp}"
                            "\n"
                            r"\abovedisplayskip 12\p@ \@plus2\p@ \@minus1\p@"
                            "\n"
                            r"\abovedisplayshortskip \z@ \@plus3\p@"
                            "\n"
                            r"\belowdisplayshortskip 3\p@ \@plus3\p@ \@minus3\p@"
                            "\n"
                            r"\belowdisplayskip \abovedisplayskip"
                            "\n"
                            r"A &= \Gamma"
                        ),
                    ),
                ],
            )
        ],
    )

    rendered = render_structured_japanese_source(
        content,
        {"paragraph-1": _unit("paragraph-1", "本文")},
    )

    assert r"\begin{aligned}A &= \Gamma\end{aligned}" in rendered.main_tex
    assert "{10bp}{12bp}" not in rendered.main_tex
    assert r"\abovedisplayskip" not in rendered.main_tex
    assert r"\belowdisplayskip" not in rendered.main_tex
    assert r"\p@" not in rendered.main_tex


def test_collapses_blank_lines_inside_display_math() -> None:
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="section-1",
                blocks=[
                    Block(id="paragraph-1", type="paragraph"),
                    Block(
                        id="equation-blank-line",
                        type="equation",
                        latex="\\begin{split}a &= b\\\\\nc &= d\n  \n\\end{split}",
                    ),
                ],
            )
        ],
    )

    rendered = render_structured_japanese_source(
        content,
        {"paragraph-1": _unit("paragraph-1", "本文")},
    )

    assert "c &= d\n\\end{aligned}" in rendered.main_tex
    assert "c &= d\n  \n\\end{aligned}" not in rendered.main_tex


def test_normalizes_display_only_environment_inside_inline_math() -> None:
    content = DocumentContent(
        quality_level="A",
        sections=[Section(id="section-1", blocks=[Block(id="paragraph-1", type="paragraph")])],
    )
    unit = _unit(
        "paragraph-1",
        "式",
        [
            {"t": "text", "v": "式 "},
            {"t": "math_inline", "v": r"\begin{split}a&=b\\c&=d\end{split}"},
            {"t": "text", "v": " を得る。"},
        ],
    )

    rendered = render_structured_japanese_source(content, {"paragraph-1": unit})

    assert r"$\begin{aligned}a&=b\\c&=d\end{aligned}$" in rendered.main_tex
    assert r"\begin{split}" not in rendered.main_tex


def test_drops_empty_math_delimiters_left_in_translated_text() -> None:
    content = DocumentContent(
        quality_level="A",
        sections=[Section(id="section-1", blocks=[Block(id="paragraph-1", type="paragraph")])],
    )
    unit = _unit(
        "paragraph-1",
        "式を示す: $$",
        [{"t": "text", "v": "式を示す: $$"}],
    )

    rendered = render_structured_japanese_source(content, {"paragraph-1": unit})

    assert "式を示す: " + r"\par" in rendered.main_tex
    assert "$$" not in rendered.main_tex


def test_defines_generic_fallback_for_source_math_macros() -> None:
    content = DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="section-1",
                blocks=[
                    Block(id="paragraph-1", type="paragraph"),
                    Block(id="equation-custom-command", type="equation", latex=r"\trace M^2"),
                ],
            )
        ],
    )

    rendered = render_structured_japanese_source(
        content,
        {"paragraph-1": _unit("paragraph-1", "本文")},
    )

    assert r"\ifcsname trace\endcsname\else" in rendered.main_tex
    assert r"\def\csname trace\endcsname{\operatorname{trace}}" in rendered.main_tex
    assert r"\trace M^2" in rendered.main_tex
