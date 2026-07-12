"""Canonical table-grid and typed table-translation contract."""

from __future__ import annotations

import pytest
from alinea_core.translation.table_cells import (
    CanonicalTableGrid,
    TableTranslationContent,
    parse_table_grid,
    table_cells_complete,
    validate_table_translation_content,
)


def test_html_grid_uses_physical_cells_and_preserves_spans_and_math() -> None:
    grid = parse_table_grid(
        """
        <table>
          <thead><tr><th colspan="2">Method family</th></tr></thead>
          <tbody>
            <tr><td rowspan="2">Our <em>approach</em> $x_i$ improves results</td><td>92.1</td></tr>
            <tr><td>MODEL-X</td></tr>
          </tbody>
        </table>
        """
    )

    assert grid.supported is True
    assert grid.source_format == "html"
    assert [[cell.id for cell in row] for row in grid.rows] == [
        ["r0c0"],
        ["r1c0", "r1c1"],
        ["r2c0"],
    ]
    assert grid.rows[0][0].header is True
    assert grid.rows[0][0].colspan == 2
    assert grid.rows[1][0].rowspan == 2
    assert grid.rows[1][0].source == "Our approach $x_i$ improves results"
    assert grid.rows[1][0].math == ["$x_i$"]
    assert grid.rows[0][0].translatable is True
    assert grid.rows[1][0].translatable is True
    assert grid.rows[1][1].translatable is False
    assert grid.rows[2][0].translatable is False


@pytest.mark.parametrize(
    "markup",
    [
        '<math alttext="x_i"><mi>x</mi><mi>i</mi></math>',
        '<span class="ltx_Math" alttext="x_i"><span>x</span></span>',
    ],
)
def test_arxiv_html_math_alttext_is_preserved_as_protected_math(markup: str) -> None:
    grid = parse_table_grid(f"<table><tr><td>Accuracy {markup} after training</td></tr></table>")

    assert grid.supported is True
    assert grid.rows[0][0].source == "Accuracy $x_i$ after training"
    assert grid.rows[0][0].math == ["$x_i$"]
    assert grid.rows[0][0].translatable is True


def test_html_math_without_alttext_accumulates_all_presentation_text() -> None:
    grid = parse_table_grid(
        "<table><tr><td>Loss <math><mi>x</mi><mo>+</mo><mi>y</mi></math> improves</td></tr></table>"
    )

    assert grid.supported is True
    assert grid.rows[0][0].source == "Loss $x+y$ improves"
    assert grid.rows[0][0].math == ["$x+y$"]


def test_html_math_prefers_tex_annotation_when_alttext_is_absent() -> None:
    grid = parse_table_grid(
        "<table><tr><td>Loss <math><semantics><mrow><mi>x</mi></mrow>"
        '<annotation encoding="application/x-tex">x_i</annotation>'
        "</semantics></math> improves</td></tr></table>"
    )

    assert grid.supported is True
    assert grid.rows[0][0].source == "Loss $x_i$ improves"
    assert grid.rows[0][0].math == ["$x_i$"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("A descriptive baseline", True),
        ("Accuracy $a_t$ after training", True),
        ("日本語だけのセル", False),
        ("https://example.org/model", False),
        ("$x_i^2 + y_i^2$", False),
        ("93.7 ± 0.2", False),
        ("10 ms", False),
        ("BERT-LARGE", False),
        ("ResNet-50", False),
        ("A", False),
    ],
)
def test_html_grid_classifies_prose_without_corpus_values(source: str, expected: bool) -> None:
    grid = parse_table_grid(f"<table><tr><td>{source}</td></tr></table>")

    assert grid.supported is True
    assert grid.rows[0][0].translatable is expected


def test_latex_grid_preserves_structural_wrappers_and_body_offsets() -> None:
    raw = r"""\begin{tabularx}{\linewidth}{lcr}
\toprule
\multicolumn{2}{c}{Method family} & Score \\
\multirow{2}{*}{A prose baseline} & Value $x_i$ improves & 91.2 \\[2pt]
 & 日本語だけ & -- \\
\bottomrule
\end{tabularx}"""

    grid = parse_table_grid(raw)

    assert grid.supported is True
    assert grid.source_format == "latex"
    assert [[cell.id for cell in row] for row in grid.rows] == [
        ["r0c0", "r0c1"],
        ["r1c0", "r1c1", "r1c2"],
        ["r2c0", "r2c1", "r2c2"],
    ]
    multicolumn = grid.rows[0][0]
    multirow = grid.rows[1][0]
    assert (multicolumn.colspan, multicolumn.rowspan) == (2, 1)
    assert (multirow.colspan, multirow.rowspan) == (1, 2)
    assert multicolumn.latex_wrappers == ["multicolumn"]
    assert multirow.latex_wrappers == ["multirow"]
    assert raw[multicolumn.latex_body_start : multicolumn.latex_body_end] == "Method family"
    assert raw[multirow.latex_body_start : multirow.latex_body_end] == "A prose baseline"
    assert grid.rows[1][1].source == "Value $x_i$ improves"
    assert grid.rows[1][1].math == ["$x_i$"]
    assert grid.rows[1][2].translatable is False
    assert grid.rows[2][1].translatable is False


def test_latex_tabular_star_preserves_width_and_optional_position_structure() -> None:
    raw = (
        r"\begin{tabular*}{\linewidth}[t]{@{\extracolsep{\fill}}ll}"
        r"Method description & 91.2 \\"
        r"\end{tabular*}"
    )

    grid = parse_table_grid(raw)

    assert grid.supported is True
    assert grid.source_format == "latex"
    assert [cell.source for cell in grid.rows[0]] == ["Method description", "91.2"]
    cell = grid.rows[0][0]
    assert raw[cell.latex_body_start : cell.latex_body_end] == "Method description"


def test_latex_tabularnewline_and_starred_row_separator_keep_physical_rows() -> None:
    raw = (
        r"\begin{tabular}{ll}"
        r"Method description & 91.2 \tabularnewline "
        r"Baseline prose & 90.1 \\*"
        r"Final method & 89.0 \\"
        r"\end{tabular}"
    )

    grid = parse_table_grid(raw)

    assert grid.supported is True
    assert [[cell.source for cell in row] for row in grid.rows] == [
        ["Method description", "91.2"],
        ["Baseline prose", "90.1"],
        ["Final method", "89.0"],
    ]


def test_latex_grid_cleans_nested_formatting_without_losing_math() -> None:
    raw = (
        r"\begin{tabular}{ll}"
        r"Best \textbf{language \emph{model}} & Gain $\Delta = 2.1$ after tuning \\"
        r"\end{tabular}"
    )

    grid = parse_table_grid(raw)

    assert grid.supported is True
    assert [cell.source for cell in grid.rows[0]] == [
        "Best language model",
        r"Gain $\Delta = 2.1$ after tuning",
    ]
    assert grid.rows[0][1].math == [r"$\Delta = 2.1$"]


def test_latex_visible_source_strips_common_formatting_arguments_and_accents() -> None:
    raw = (
        r"\begin{tabular}{lll}"
        r"\textcolor{red}{Best method} & \makecell[l]{First line\\Second line} & G\"{o}del method \\"
        r"\end{tabular}"
    )

    grid = parse_table_grid(raw)

    assert grid.supported is True
    assert [cell.source for cell in grid.rows[0]] == [
        "Best method",
        "First line Second line",
        "Gödel method",
    ]


def test_latex_structural_wrapper_with_trailing_material_fails_closed() -> None:
    grid = parse_table_grid(
        r"\begin{tabular}{ll}\multicolumn{2}{c}{Method} trailing & Score \\\end{tabular}"
    )

    assert grid.supported is False
    assert grid.reason


@pytest.mark.parametrize(
    "raw",
    [
        "<table><tr><td colspan='0'>bad</td></tr></table>",
        "<table><tr><td>outer<table><tr><td>inner</td></tr></table></td></tr></table>",
        "<table><tr><td><script>alert(1)</script></td></tr></table>",
        r"\begin{tabular}{ll}missing end & prose \\",
        r"\begin{tabular}{ll}\multicolumn{x}{c}{bad} & prose \\\end{tabular}",
        "x" * 1_000_000,
    ],
)
def test_malformed_or_oversized_table_is_explicitly_unsupported(raw: str) -> None:
    grid = parse_table_grid(raw)

    assert grid == CanonicalTableGrid(supported=False, reason=grid.reason)
    assert grid.reason


@pytest.mark.parametrize(
    "raw",
    [
        "<table>" + "<tr><td>prose</td></tr>" * 513 + "</table>",
        "<table><tr>" + "<td>prose</td>" * 513 + "</tr></table>",
        "<table><tr><td>" + "<span>" * 33 + "prose" + "</span>" * 33 + "</td></tr></table>",
        "<table><tr><td>" + "<span></span>" * 5_000 + "prose</td></tr></table>",
        r"\begin{tabular}{l}" + "$x$" * 1_500 + r" \\\end{tabular}",
    ],
)
def test_structure_limits_apply_before_raw_byte_limit(raw: str) -> None:
    assert len(raw.encode()) < 256_000

    grid = parse_table_grid(raw)

    assert grid.supported is False
    assert grid.reason


def _typed_grid() -> CanonicalTableGrid:
    return parse_table_grid(
        "<table><tr><th>Method name</th><th>$F_1$</th></tr>"
        "<tr><td>A descriptive baseline</td><td>91.2</td></tr></table>"
    )


def test_typed_table_translation_requires_exact_shape_and_target_coverage() -> None:
    grid = _typed_grid()
    value = {
        "kind": "table",
        "version": 1,
        "caption": [{"t": "text", "v": "結果の概要"}],
        "cells": [["手法名", None], ["説明的なベースライン", None]],
    }

    parsed = validate_table_translation_content(value, grid)

    assert parsed == TableTranslationContent.model_validate(value)
    assert table_cells_complete(value, grid) is True


def test_typed_target_cell_preserves_exact_math_multiplicity() -> None:
    grid = parse_table_grid(
        "<table><tr><td>Accuracy $x^2$ and again $x^2$ after training</td></tr></table>"
    )
    valid = {
        "kind": "table",
        "version": 1,
        "caption": None,
        "cells": [["学習後の精度 $x^2$ と再び $x^2$"]],
    }

    assert grid.rows[0][0].math == ["$x^2$", "$x^2$"]
    assert validate_table_translation_content(valid, grid) is not None
    for translated in [
        "学習後の精度",
        "学習後の精度 $x^3$ と再び $x^2$",
        "学習後の精度 $x^2$",
        "学習後の精度 $x^2$ と再び $x^2$、さらに $x^2$",
    ]:
        invalid = {**valid, "cells": [[translated]]}
        assert validate_table_translation_content(invalid, grid) is None


@pytest.mark.parametrize(
    "value",
    [
        {
            "kind": "table",
            "version": 2,
            "caption": None,
            "cells": [["手法名", None], ["ベースライン", None]],
        },
        {
            "kind": "table",
            "version": 1,
            "caption": None,
            "cells": [["手法名", None]],
        },
        {
            "kind": "table",
            "version": 1,
            "caption": None,
            "cells": [["手法名"], ["ベースライン", None]],
        },
        {
            "kind": "table",
            "version": 1,
            "caption": None,
            "cells": [["手法名", "91.2"], ["ベースライン", None]],
        },
        {
            "kind": "table",
            "version": 1,
            "caption": None,
            "cells": [["手法名\u0000", None], ["ベースライン", None]],
        },
        {
            "kind": "table",
            "version": 1,
            "caption": None,
            "cells": [["", None], ["ベースライン", None]],
        },
        {
            "kind": "table",
            "version": 1,
            "caption": None,
            "cells": [["訳" * 16_385, None], ["ベースライン", None]],
        },
        {
            "kind": "table",
            "version": 1,
            "caption": [{"t": "text", "v": "結果", "surprise": True}],
            "cells": [["手法名", None], ["ベースライン", None]],
        },
        {
            "kind": "table",
            "version": 1,
            "caption": None,
            "cells": [["手法名", None], ["ベースライン", None]],
            "unknown": True,
        },
    ],
)
def test_typed_table_translation_fails_closed(value: object) -> None:
    grid = _typed_grid()

    assert validate_table_translation_content(value, grid) is None
    assert table_cells_complete(value, grid) is False


def test_typed_table_translation_rejects_aggregate_oversized_caption() -> None:
    value = {
        "kind": "table",
        "version": 1,
        "caption": [{"t": "text", "v": "訳" * 32_000} for _ in range(17)],
        "cells": [["手法名", None], ["ベースライン", None]],
    }

    assert validate_table_translation_content(value, _typed_grid()) is None


def test_legacy_caption_is_not_cell_complete_for_a_supported_target_grid() -> None:
    legacy = [{"t": "text", "v": "従来のキャプション訳"}]

    assert table_cells_complete(legacy, _typed_grid()) is False


def test_unsupported_or_no_target_grid_is_vacuously_cell_complete() -> None:
    unsupported = parse_table_grid("not a table")
    no_targets = parse_table_grid("<table><tr><td>91.2</td><td>$x$</td></tr></table>")

    assert table_cells_complete(None, unsupported) is True
    assert table_cells_complete(None, no_targets) is True
