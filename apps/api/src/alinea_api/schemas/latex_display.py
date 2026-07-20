"""LaTeX 表示クリーニング — Web版 latex-display-clean.ts の Python 移植。

``apps/web/src/components/viewer/latex-display-clean.ts`` と同じロジックを Python で実装する。
共有 JSON フィクスチャ (apps/api/tests/fixtures/latex_display_cases.json) により
TypeScript / Python の双方が同一 input/output を検査する。
"""

from __future__ import annotations

import re


# ============================================================================
# TeX シンボル置換 (replaceLatexSymbols)
# ============================================================================

def _replace_latex_symbols(value: str) -> str:
    value = re.sub(r"\\pm\b", "±", value)
    value = re.sub(r"\\mp\b", "∓", value)
    value = re.sub(r"\\times\b", "×", value)
    value = re.sub(r"\\cdot\b", "·", value)
    value = re.sub(r"\\leq?\b", "≤", value)
    value = re.sub(r"\\geq?\b", "≥", value)
    value = re.sub(r"\\neq\b", "≠", value)
    value = re.sub(r"\\approx\b", "≈", value)
    value = re.sub(r"\\infty\b", "∞", value)
    value = re.sub(r"\\emptyset\b", "∅", value)
    value = re.sub(r"\\uparrow\b", "↑", value)
    value = re.sub(r"\\downarrow\b", "↓", value)
    value = re.sub(r"\\rightarrow\b|\\to\b", "→", value)
    value = re.sub(r"\\leftarrow\b", "←", value)
    value = re.sub(r"\\alpha\b", "α", value)
    value = re.sub(r"\\beta\b", "β", value)
    value = re.sub(r"\\gamma\b", "γ", value)
    value = re.sub(r"\\delta\b", "δ", value)
    value = re.sub(r"\\lambda\b", "λ", value)
    value = re.sub(r"\\mu\b", "μ", value)
    value = re.sub(r"\\pi\b", "π", value)
    value = re.sub(r"\\sigma\b", "σ", value)
    value = re.sub(r"\\theta\b", "θ", value)
    value = re.sub(r"\\dots\b|\\ldots\b", "…", value)
    return value


# ============================================================================
# 既知コマンドのアンラップ (unwrapKnownCommands)
# ============================================================================

_KNOWN_COMMANDS_RE = re.compile(
    r"\\href\{[^{}]*\}\{([^{}]*)\}"
    r"|\\(?:url|doi)\{([^{}]*)\}"
    r"|\\mybox(?:\[[^\]]*])?\{[^{}]*\}\{([^{}]*)\}"
    r"|\\(?:textbf|textit|texttt|emph|mathrm|mathbf|mathit|operatorname|textsuperscript|textsubscript|underline|textsc|text)\{([^{}]*)\}"
)


def _unwrap_known_commands(value: str) -> str:
    for _ in range(6):
        prev = value
        def _replace(m: re.Match) -> str:
            # Return the first non-None group
            for g in m.groups():
                if g is not None:
                    return g
            return ""
        value = _KNOWN_COMMANDS_RE.sub(_replace, value)
        if value == prev:
            break
    return value


# ============================================================================
# セグメントクリーニング (cleanLatexSegment)
# ============================================================================

def _item_replace(m: re.Match) -> str:
    label = m.group(1)
    return f" {label} " if label else " • "


_LABEL_SECTION_RE = re.compile(r"LABEL:section:\\?[A-Za-z]*(?:\{\})?[A-Za-z0-9:_-]+")
_LABEL_FIGURE_RE = re.compile(r"LABEL:figure:\\?[A-Za-z]*(?:\{\})?[A-Za-z0-9:_-]+")
_LABEL_TABLE_RE = re.compile(r"LABEL:table:\\?[A-Za-z]*(?:\{\})?[A-Za-z0-9:_-]+")
_LABEL_EQ_RE = re.compile(r"LABEL:(?:eq|equation):\\?[A-Za-z]*(?:\{\})?[A-Za-z0-9:_-]+")
_BEGIN_END_ENV_BASIC_RE = re.compile(
    r"\\(?:begin|end)\{(?:itemize|enumerate|description|center|flushleft|flushright)\}(?:\[[^\]]*])?"
)
_BEGIN_END_ENV_RE = re.compile(r"\\(?:begin|end)\{[^{}]*\}(?:\[[^\]]*])?")
_STYLE_CMD_OPEN_RE = re.compile(r"\\(?:texttt|textbf|textit|emph|underline|textsc|text)\{")
_ITEM_RE = re.compile(r"\\item(?:\[([^\]]+)])?")
_CITE_RE = re.compile(r"\\(?:citep?|citet|citealp|citeauthor|citeyear)\{[^{}]*\}")
_SPACE_CMD_RE = re.compile(
    r"\\(?:vspace|hspace|addlinespace|smallskip|medskip|bigskip)\*?(?:\[[^\]]*])?\{[^{}]*\}"
)
_RULE_RE = re.compile(r"\\(?:hrule|toprule|midrule|bottomrule|hline)\b")
_THIN_SPACE_RE = re.compile(r"\\[!,;:]")
_ESCAPED_SPECIAL_RE = re.compile(r"\\([%&_#$])")
_GENERIC_CMD_RE = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*])?(?:\{[^{}]*\})*")
_SCRIPT_BRACE_RE = re.compile(r"[_^]\{[^{}]*\}")
_TRAILING_PUNCT_RE = re.compile(r"\s+([,.;:)])")
_LEADING_PUNCT_RE = re.compile(r"([([])\s+")
_WHITESPACE_RE = re.compile(r"\s+")


def _clean_latex_segment(value: str) -> str:
    value = _unwrap_known_commands(value)
    value = _replace_latex_symbols(value)
    value = _LABEL_SECTION_RE.sub("the referenced section", value)
    value = _LABEL_FIGURE_RE.sub("the referenced figure", value)
    value = _LABEL_TABLE_RE.sub("the referenced table", value)
    value = _LABEL_EQ_RE.sub("the referenced equation", value)
    value = _BEGIN_END_ENV_BASIC_RE.sub(" ", value)
    value = _BEGIN_END_ENV_RE.sub(" ", value)
    value = _STYLE_CMD_OPEN_RE.sub("", value)
    value = _ITEM_RE.sub(_item_replace, value)
    value = _CITE_RE.sub("", value)
    value = _SPACE_CMD_RE.sub(" ", value)
    value = _RULE_RE.sub(" ", value)
    value = _THIN_SPACE_RE.sub("", value)
    value = _ESCAPED_SPECIAL_RE.sub(r"\1", value)
    value = _GENERIC_CMD_RE.sub("", value)
    value = _SCRIPT_BRACE_RE.sub("", value)
    value = value.replace("{", "").replace("}", "")
    value = value.replace("~", " ")
    value = _TRAILING_PUNCT_RE.sub(r"\1", value)
    value = _LEADING_PUNCT_RE.sub(r"\1", value)
    value = _WHITESPACE_RE.sub(" ", value)
    return value.strip()


# ============================================================================
# パブリック API
# ============================================================================

_NEEDS_CLEAN_RE = re.compile(r"[\\{}]|LABEL:")
_MATH_RE = re.compile(r"(\$[^$]+\$|\\\([\s\S]*?\\\))")


def clean_latex_display_text(value: str) -> str:
    """LaTeX マーカーがない平文はそのまま返す。

    Web 版 ``cleanLatexDisplayText`` に相当する。
    """
    if not _NEEDS_CLEAN_RE.search(value):
        return value
    return _clean_latex_segment(value)


def clean_latex_display_text_outside_math(value: str) -> str:
    """数式 ($...$ / \\(…\\)) の外側だけクリーニングする。

    Web 版 ``cleanLatexDisplayTextOutsideMath`` に相当する。
    """
    if not _NEEDS_CLEAN_RE.search(value):
        return value
    parts: list[str] = []
    index = 0
    for match in _MATH_RE.finditer(value):
        start = match.start()
        if start > index:
            parts.append(_clean_latex_segment(value[index:start]))
        parts.append(match.group(0))
        index = match.end()
    if index < len(value):
        parts.append(_clean_latex_segment(value[index:]))
    return _WHITESPACE_RE.sub(" ", " ".join(parts)).strip()
