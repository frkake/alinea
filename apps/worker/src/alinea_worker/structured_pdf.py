"""共通ドキュメント IR から日本語 PDF 用の安全な LuaLaTeX を生成する。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from alinea_core.db.models import TranslationUnit
from alinea_core.document.blocks import Block, DocumentContent
from alinea_core.document.plaintext import block_to_plain
from alinea_core.text_safety import sanitize_untrusted_text
from alinea_core.translation.pipeline import BLOCKING_FLAGS, TRANSLATABLE_BLOCK_TYPES
from alinea_core.translation.table_cells import (
    TableTranslationContent,
    parse_table_grid,
    validate_table_translation_content,
)

_MATH_RE = re.compile(
    r"(?<!\\)(?:\$\$(?:\\.|[^$])*?\$\$|\$(?:\\.|[^$])*?\$)"
    r"|\\\((?:\\.|[^\\])*?\\\)|\\\[(?:\\.|[^\\])*?\\\]",
    re.DOTALL,
)
_EMPTY_MATH_DELIMITER_RE = re.compile(
    r"(?:\$\$\s*\$\$|\$\s*\$|\\\(\s*\\\)|\\\[\s*\\\])\Z",
    re.DOTALL,
)
_FORMATTING_COMMAND_RE = re.compile(
    r"\\(?:textbf|textit|texttt|textsc|emph|underline|mathrm|mathbf|mathit)\s*\{([^{}]*)\}"
)
_VISIBLE_COMMAND_RE = re.compile(r"\\[A-Za-z@]+\*?(?:\s*\[[^\]]*\])?")
_UNSAFE_MATH_COMMAND_RE = re.compile(
    r"\\(?:input|include|usepackage|documentclass|write|openout|read|catcode|"
    r"csname|def|gdef|edef|xdef|let|newcommand|renewcommand|special|directlua)\b"
)
_TEX_SIZE_SWITCH_RE = re.compile(
    r"\\(?:tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge)\b"
)
_INTERNAL_TEX_SIZE_HELPER_RE = re.compile(
    r"\\@setfontsize"
    r"(?:\s*\\(?:(?:tiny|scriptsize|footnotesize|small|normalsize|large|Large|LARGE|huge|Huge)\b"
    r"|@[ivxlcdm]+pt)){0,3}"
    r"(?:\s*\{[^{}\r\n]*\}){0,2}"
)
_INTERNAL_TEX_SIZE_OPERAND_RE = re.compile(r"\\@[ivxlcdm]+pt\b")
_INTERNAL_DISPLAY_SKIP_RE = re.compile(
    r"(?m)^[ \t]*\\(?:above|below)display(?:short)?skip\b[^\r\n]*(?:\r?\n|$)"
)
_NESTED_MATH_ENVIRONMENTS = {
    "split": "aligned",
    "align": "aligned",
    "align*": "aligned",
    "alignat": "alignedat",
    "alignat*": "alignedat",
    "eqnarray": "aligned",
    "eqnarray*": "aligned",
    "gather": "gathered",
    "gather*": "gathered",
    "multline": "gathered",
    "multline*": "gathered",
}
_EMPTY_ALIGNMENT_CELL_RE = re.compile(
    r"(?P<prefix>"
    r"\\begin\{(?:aligned|alignedat|array|cases|matrix|pmatrix|bmatrix|vmatrix|Vmatrix)\}"
    r"(?:\[[^\[\]]*\])?(?:\{[^{}]*\})?\s*"
    r"|\\\\(?:\s*\[[^\[\]]*\])?\s*"
    r")&"
)
_CONTROL_WORD_NAME_RE = re.compile(r"\\([A-Za-z]{1,64})(?![A-Za-z])")
_MATH_ENV_TOKEN_RE = re.compile(r"\\(?P<action>begin|end)\{(?P<name>[^{}]+)\}")
_MAX_GENERIC_MATH_COMMANDS = 4_096
_SAFE_ASSET_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".pdf"})


@dataclass(frozen=True)
class PdfRenderManifest:
    expected_block_ids: frozenset[str]
    translated_block_ids: frozenset[str]
    source_fallback_block_ids: frozenset[str]


@dataclass(frozen=True)
class StructuredLatexSource:
    main_tex_name: str
    main_tex: str
    support_text_files: dict[str, str]
    binary_files: dict[str, bytes]
    manifest: PdfRenderManifest
    warnings: list[str] = field(default_factory=list)


def _strip_visible_tex_commands(value: str) -> str:
    """モデル出力へ混入した可視 TeX 命令を内容だけ残して除去する。"""
    value = sanitize_untrusted_text(value)
    previous = None
    while previous != value:
        previous = value
        value = _FORMATTING_COMMAND_RE.sub(r"\1", value)
    return _VISIBLE_COMMAND_RE.sub("", value)


def _escape_text(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "^": r"\^{}",
        "~": r"\~{}",
    }
    cleaned = _strip_visible_tex_commands(value)
    return "".join(replacements.get(char, char) for char in cleaned)


def _safe_math(value: str) -> str:
    value = sanitize_untrusted_text(value)
    if _UNSAFE_MATH_COMMAND_RE.search(value):
        return _escape_text(value)
    for source, target in _NESTED_MATH_ENVIRONMENTS.items():
        value = value.replace(rf"\begin{{{source}}}", rf"\begin{{{target}}}")
        value = value.replace(rf"\end{{{source}}}", rf"\end{{{target}}}")
    return value


def _has_top_level_alignment_tab(value: str) -> bool:
    """Return whether ``&`` occurs outside every nested math environment."""

    environments: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\":
            environment = _MATH_ENV_TOKEN_RE.match(value, index)
            if environment is not None:
                name = environment.group("name")
                if environment.group("action") == "begin":
                    environments.append(name)
                elif environments and environments[-1] == name:
                    environments.pop()
                elif name in environments:
                    del environments[len(environments) - 1 - environments[::-1].index(name) :]
                index = environment.end()
                continue
            # Escaped ampersands and row separators are data, not alignment tabs.
            index += 2
            continue
        if value[index] == "&" and not environments:
            return True
        index += 1
    return False


def _normalize_display_math(value: str) -> str:
    """Make source display math safe to embed inside one bounded resize box."""

    math = _safe_math(value.strip())
    # Visual font-size switches sometimes arrive expanded from a class file in legacy
    # revisions.  They are presentation metadata, not mathematical source, and internal
    # ``@`` helpers are not valid in the standalone document generated here.
    math = _INTERNAL_TEX_SIZE_HELPER_RE.sub("", math)
    math = _TEX_SIZE_SWITCH_RE.sub("", math)
    math = _INTERNAL_TEX_SIZE_OPERAND_RE.sub("", math)
    math = _INTERNAL_DISPLAY_SKIP_RE.sub("", math)
    # A backslash followed by end-of-line is TeX's control-space spelling.  Some
    # submitted sources leave it on a line by itself before ``\end{equation}``.
    # It carries no mathematical content and would otherwise escape the ``$``
    # delimiter that this renderer adds around the normalized display.
    math = re.sub(r"(?m)^[ \t]*\\[ \t]*$", "", math).strip()
    # TeX turns a blank line into ``\par``, which is invalid inside aligned math.
    # Comments removed during source parsing can otherwise leave such a blank line.
    math = re.sub(r"\r?\n[ \t]*\r?\n+", "\n", math)
    for opener, closer in (("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)"), ("$", "$")):
        if (
            math.startswith(opener)
            and math.endswith(closer)
            and len(math) >= len(opener) + len(closer)
        ):
            math = math[len(opener) : -len(closer)].strip()
            break

    equation = re.fullmatch(
        r"\\begin\{(?:equation\*?|displaymath)\}(.*)"
        r"\\end\{(?:equation\*?|displaymath)\}",
        math,
        flags=re.DOTALL,
    )
    if equation is not None:
        math = equation.group(1).strip()

    math = re.sub(r"\\(?:notag|nonumber)\b", "", math)
    math = re.sub(r"\\(?:label|tag)\s*\{[^{}]*\}", "", math)

    if _has_top_level_alignment_tab(math):
        math = r"\begin{aligned}" + math + r"\end{aligned}"
    # LuaTeX alignment rows cannot start with a bare alignment tab. Source equations
    # split across blocks commonly begin with ``&`` to preserve visual indentation.
    math = _EMPTY_ALIGNMENT_CELL_RE.sub(r"\g<prefix>{}&", math)
    return math


def _text_with_math(value: str) -> str:
    rendered: list[str] = []
    cursor = 0
    for match in _MATH_RE.finditer(value):
        rendered.append(_escape_text(value[cursor : match.start()]))
        math = match.group(0)
        if _EMPTY_MATH_DELIMITER_RE.fullmatch(math) is None:
            rendered.append(_safe_math(math))
        cursor = match.end()
    rendered.append(_escape_text(value[cursor:]))
    return "".join(rendered)


def _inline_to_latex(inline: object) -> str:
    if not isinstance(inline, dict):
        return ""
    tag = inline.get("t")
    if tag == "text":
        return _text_with_math(str(inline.get("v") or ""))
    if tag == "emphasis":
        children = inline.get("children")
        body = (
            "".join(_inline_to_latex(child) for child in children)
            if isinstance(children, list)
            else _text_with_math(str(inline.get("v") or ""))
        )
        return rf"\emph{{{body}}}"
    if tag == "math_inline":
        return f"${_safe_math(str(inline.get('v') or ''))}$"
    if tag in {"citation", "ref"}:
        label = str(inline.get("v") or inline.get("ref") or "")
        return _escape_text(f"[{label}]" if tag == "citation" and label else label)
    if tag == "url":
        return _escape_text(str(inline.get("v") or inline.get("href") or ""))
    if tag == "code_inline":
        return rf"\texttt{{{_escape_text(str(inline.get('v') or ''))}}}"
    return ""


def _unit_to_latex(unit: TranslationUnit) -> str:
    content: object = unit.content_ja
    if isinstance(content, list):
        return "".join(_inline_to_latex(inline) for inline in content)
    return _text_with_math(unit.text_ja)


def _unit_is_displayable(unit: TranslationUnit, block: Block) -> bool:
    if set(unit.quality_flags or []) & BLOCKING_FLAGS:
        return False
    if block.type == "table" and isinstance(unit.content_ja, dict):
        return unit.content_ja.get("kind") == "table"
    return bool(unit.text_ja.strip())


def _unit_requires_rendering(unit: TranslationUnit, block: Block) -> bool:
    if set(unit.quality_flags or []) & BLOCKING_FLAGS:
        return True
    if block.type == "table" and isinstance(unit.content_ja, dict):
        return unit.content_ja.get("kind") == "table"
    return bool(unit.text_ja.strip())


def _heading_latex(block: Block, body: str) -> str:
    level = block.level or 1
    command = "section" if level <= 1 else "subsection" if level == 2 else "subsubsection"
    return rf"\{command}{{{body}}}" + "\n"


def _list_latex(unit: TranslationUnit, ordered: bool) -> str:
    parts = [
        part.strip()
        for part in re.split(r"(?:\r?\n\s*(?:[-*•]\s*)?|\s+[•]\s+)", unit.text_ja)
        if part.strip()
    ]
    if not parts:
        parts = [unit.text_ja]
    environment = "enumerate" if ordered else "itemize"
    items = "\n".join(rf"\item {_text_with_math(part)}" for part in parts)
    return rf"\begin{{{environment}}}" + "\n" + items + "\n" + rf"\end{{{environment}}}" + "\n"


def _figure_latex(block: Block, unit: TranslationUnit, asset_name: str | None) -> str:
    lines = [r"\begin{figure}[htbp]", r"\centering"]
    if asset_name is not None:
        lines.append(
            rf"\includegraphics[width=.94\linewidth,height=.58\textheight,keepaspectratio]{{{asset_name}}}"
        )
    lines.append(rf"\caption{{{_unit_to_latex(unit)}}}")
    lines.append(r"\end{figure}")
    return "\n".join(lines) + "\n"


def _table_rows(block: Block, translated: TableTranslationContent) -> list[list[str]]:
    raw = str(getattr(block, "raw", "") or "")
    grid = parse_table_grid(raw)
    validated = validate_table_translation_content(translated.model_dump(mode="json"), grid)
    if validated is None or validated.cells is None or not grid.supported:
        return []
    rows: list[list[str]] = []
    for source_row, translated_row in zip(grid.rows, validated.cells, strict=True):
        rows.append(
            [
                translated_value if translated_value is not None else source_cell.source
                for source_cell, translated_value in zip(source_row, translated_row, strict=True)
            ]
        )
    return rows


def _table_latex(block: Block, unit: TranslationUnit) -> str:
    try:
        translated = TableTranslationContent.model_validate(unit.content_ja)
    except (TypeError, ValueError):
        return _unit_to_latex(unit) + r"\par" + "\n"
    caption = (
        "".join(_inline_to_latex(inline) for inline in translated.caption)
        if translated.caption is not None
        else ""
    )
    rows = _table_rows(block, translated)
    if not rows:
        projection = _unit_to_latex(unit)
        return (rf"\paragraph{{{caption}}}" + "\n" if caption else "") + projection + r"\par" + "\n"
    columns = max(len(row) for row in rows)
    width_mm = 155.0 / max(columns, 1)
    specification = (
        "@{}"
        + "".join(rf">{{\raggedright\arraybackslash}}p{{{width_mm:.2f}mm}}" for _ in range(columns))
        + "@{}"
    )
    rendered_rows = []
    for row in rows:
        padded = [*row, *([""] * (columns - len(row)))]
        rendered_rows.append(" & ".join(_text_with_math(cell) for cell in padded) + r" \\")
    size = r"\scriptsize" if columns > 6 else r"\small"
    lines = [
        r"\begingroup",
        size,
        r"\setlength{\tabcolsep}{1pt}",
        rf"\begin{{longtable}}{{{specification}}}",
    ]
    if caption:
        lines.append(rf"\caption{{{caption}}}\\")
    lines.extend([r"\hline", *rendered_rows, r"\hline", r"\end{longtable}", r"\endgroup"])
    return "\n".join(lines) + "\n"


def _render_nontranslatable(block: Block) -> str:
    if block.type == "equation" and block.latex:
        math = _normalize_display_math(block.latex)
        if len(math) > 100 or r"\begin{" in math:
            return (
                "\n"
                + r"\begin{center}\resizebox{\linewidth}{!}{$\displaystyle "
                + math
                + r"$}\end{center}"
                + "\n"
            )
        return "\n" + r"\[" + math + r"\]" + "\n"
    if block.type == "code" and block.code:
        lines = [_escape_text(line) for line in block.code.splitlines()]
        return r"\begin{quote}\ttfamily " + r"\\".join(lines) + r"\end{quote}" + "\n"
    if block.type == "reference_entry":
        value = block.raw or block_to_plain(block)
        return rf"\noindent {_escape_text(value)}\par" + "\n"
    return ""


def _generic_math_command_definitions(body: str) -> str:
    """Provide a harmless operator fallback without overriding known TeX commands."""

    names = sorted(set(_CONTROL_WORD_NAME_RE.findall(body)))[:_MAX_GENERIC_MATH_COMMANDS]
    return "\n".join(
        rf"\ifcsname {name}\endcsname\else"
        rf"\expandafter\def\csname {name}\endcsname{{\operatorname{{{name}}}}}\fi"
        for name in names
    )


def render_structured_japanese_source(
    content: DocumentContent,
    units: dict[str, TranslationUnit],
    *,
    abstract_ja: str | None = None,
    binary_assets: dict[str, bytes] | None = None,
) -> StructuredLatexSource:
    """IR と翻訳単位から、原ソース形式に依存しない日本語文書を生成する。"""
    binary_assets = binary_assets or {}
    expected: set[str] = set()
    translated_ids: set[str] = set()
    fallback_ids: set[str] = set()
    binary_files: dict[str, bytes] = {}
    body: list[str] = []
    active_section_ids = {
        section.id for section, block in content.iter_blocks() if block.id in units
    }

    if abstract_ja:
        body.extend([r"\section*{概要}", _text_with_math(abstract_ja) + r"\par"])

    for section, block in content.iter_blocks():
        unit = units.get(block.id)
        if block.type not in TRANSLATABLE_BLOCK_TYPES or unit is None:
            rendered = (
                _render_nontranslatable(block)
                if section.id in active_section_ids or block.type == "reference_entry"
                else ""
            )
            if rendered:
                body.append(rendered)
            continue
        if not _unit_requires_rendering(unit, block):
            continue
        expected.add(block.id)
        if not _unit_is_displayable(unit, block):
            fallback_ids.add(block.id)
            continue
        translated_ids.add(block.id)
        if block.type == "heading":
            body.append(_heading_latex(block, _unit_to_latex(unit)))
        elif block.type in {"paragraph", "quote", "theorem", "footnote"}:
            rendered = _unit_to_latex(unit)
            if block.type in {"quote", "theorem"}:
                rendered = r"\begin{quote}" + rendered + r"\end{quote}"
            body.append(rendered + r"\par" + "\n")
        elif block.type == "list":
            body.append(_list_latex(unit, bool(block.ordered)))
        elif block.type == "figure":
            asset_name: str | None = None
            if block.asset_key and block.asset_key in binary_assets:
                suffix = PurePosixPath(block.asset_key).suffix.lower()
                if suffix in _SAFE_ASSET_SUFFIXES:
                    safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", block.id)
                    asset_name = f"assets/{safe_id}{suffix}"
                    binary_files[asset_name] = binary_assets[block.asset_key]
            body.append(_figure_latex(block, unit, asset_name))
        elif block.type == "table":
            body.append(_table_latex(block, unit))

    preamble = r"""\documentclass[a4paper,11pt]{article}
\usepackage[margin=18mm]{geometry}
\usepackage{iftex}
\ifLuaTeX
  \usepackage{luatexja}
  \usepackage{luatexja-fontspec}
  \IfFontExistsTF{Noto Serif CJK JP}{\setmainjfont{Noto Serif CJK JP}}{}
  \IfFontExistsTF{Noto Sans CJK JP}{\setsansjfont{Noto Sans CJK JP}}{}
  \IfFontExistsTF{Noto Sans Mono CJK JP}{\setmonojfont{Noto Sans Mono CJK JP}}{}
\else
  \PackageError{alinea-ja-pdf}{LuaLaTeX is required for Japanese PDF output}{}
\fi
\usepackage{graphicx}
\usepackage{longtable}
\usepackage{array}
\usepackage{booktabs}
\usepackage{mathtools,amssymb}
\usepackage{bm}
\usepackage[hidelinks]{hyperref}
\providecommand{\degree}{\ensuremath{^{\circ}}}
\setlength{\emergencystretch}{3em}
\sloppy
\begin{document}
"""
    body_source = "\n".join(body)
    fallback_definitions = _generic_math_command_definitions(body_source)
    main_tex = (
        preamble
        + fallback_definitions
        + ("\n" if fallback_definitions else "")
        + body_source
        + "\n"
        + r"\end{document}"
        + "\n"
    )
    return StructuredLatexSource(
        main_tex_name="main.tex",
        main_tex=main_tex,
        support_text_files={},
        binary_files=binary_files,
        manifest=PdfRenderManifest(
            expected_block_ids=frozenset(expected),
            translated_block_ids=frozenset(translated_ids),
            source_fallback_block_ids=frozenset(fallback_ids),
        ),
    )


__all__ = [
    "PdfRenderManifest",
    "StructuredLatexSource",
    "render_structured_japanese_source",
]
