"""Bounded canonical table grids and the strict translated-table contract.

The grid deliberately describes *physical* source cells.  A rowspan or colspan is
metadata on its source cell; it never creates synthetic matrix entries.  This is
the one indexing contract shared by translation, rendering, and LaTeX rewriting.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_TABLE_RAW_BYTES = 256_000
MAX_TABLE_ROWS = 512
MAX_TABLE_CELLS = 4_096
MAX_TABLE_CELLS_PER_ROW = 512
MAX_TABLE_CELL_SOURCE_CHARS = 8_192
MAX_TABLE_NESTING = 32
MAX_TABLE_SPAN = 512
MAX_TABLE_INLINE_NODES = 4_096
MAX_TABLE_MATH_FRAGMENTS = 1_024
MAX_TRANSLATED_CELL_CHARS = 16_384
MAX_TRANSLATED_TABLE_CHARS = 512_000
MAX_CAPTION_INLINES = 2_048
MAX_CAPTION_DEPTH = 16
MAX_CAPTION_STRING_CHARS = 32_768

_SUPPORTED_INLINE_TYPES = frozenset(
    {
        "text",
        "math_inline",
        "citation",
        "ref",
        "footnote_ref",
        "url",
        "emphasis",
        "code_inline",
    }
)
_INLINE_KEYS = frozenset({"t", "v", "ref", "kind", "href", "children"})
_ACTIVE_HTML_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "template"})
_HTML_CONTAINER_TAGS = frozenset({"thead", "tbody", "tfoot", "colgroup", "col", "caption"})
_HTML_VOID_TAGS = frozenset({"br", "hr", "img", "input", "meta", "link", "source", "wbr"})
_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_COMPACT_IDENTIFIER_RE = re.compile(r"[A-Z][A-Z0-9_.:/+\-]{0,63}")
_NUMERIC_UNIT_RE = re.compile(r"[+\-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)\s*[A-Za-zµμ°%/]+", re.IGNORECASE)
_SEPARATED_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_.:/+][A-Za-z0-9]+)+")
_LATEX_BEGIN_RE = re.compile(r"\\begin\s*\{(tabularx|tabular\*|tabular)\}", re.IGNORECASE)
_MATH_RE = re.compile(
    r"(?<!\\)(?:\$\$(?:\\.|[^$])*?\$\$|\$(?:\\.|[^$])*?\$)"
    r"|\\\((?:\\.|[^\\])*?\\\)|\\\[(?:\\.|[^\\])*?\\\]",
    re.DOTALL,
)


class CanonicalTableCell(BaseModel):
    """One physical source cell in row/source order."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    source: str
    header: bool = False
    rowspan: int = Field(default=1, ge=1, le=MAX_TABLE_SPAN)
    colspan: int = Field(default=1, ge=1, le=MAX_TABLE_SPAN)
    translatable: bool
    math: list[str] = Field(default_factory=list)
    # Half-open offsets in the complete raw LaTeX string.  They are absent for HTML.
    latex_body_start: int | None = Field(default=None, ge=0)
    latex_body_end: int | None = Field(default=None, ge=0)
    # Structural wrappers only, outer to inner.  Formatting commands are not wrappers.
    latex_wrappers: list[Literal["multicolumn", "multirow"]] = Field(default_factory=list)


class CanonicalTableGrid(BaseModel):
    """A supported canonical grid or an explicit unsupported result."""

    model_config = ConfigDict(extra="forbid", strict=True)

    supported: bool
    source_format: Literal["html", "latex"] | None = None
    rows: list[list[CanonicalTableCell]] = Field(default_factory=list)
    reason: str | None = None


class TableTranslationContent(BaseModel):
    """Versioned value persisted in ``translation_units.content_ja`` for tables."""

    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["table"]
    version: Literal[1]
    caption: list[dict[str, Any]] | None
    cells: list[list[str | None]] | None

    @field_validator("caption")
    @classmethod
    def _validate_caption(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if value is None:
            return None
        if len(value) > MAX_CAPTION_INLINES:
            raise ValueError("caption has too many inline nodes")
        count = [0]
        total_chars = [0]
        for inline in value:
            _validate_inline(inline, depth=0, count=count, total_chars=total_chars)
        return value

    @field_validator("cells")
    @classmethod
    def _validate_cells(cls, value: list[list[str | None]] | None) -> list[list[str | None]] | None:
        if value is None:
            return None
        if len(value) > MAX_TABLE_ROWS:
            raise ValueError("translation has too many rows")
        total_cells = 0
        total_chars = 0
        for row in value:
            if len(row) > MAX_TABLE_CELLS_PER_ROW:
                raise ValueError("translation row has too many cells")
            total_cells += len(row)
            if total_cells > MAX_TABLE_CELLS:
                raise ValueError("translation has too many cells")
            for cell in row:
                if cell is None:
                    continue
                if len(cell) > MAX_TRANSLATED_CELL_CHARS:
                    raise ValueError("translated cell is too long")
                if _has_control(cell):
                    raise ValueError("translated cell contains control characters")
                total_chars += len(cell)
                if total_chars > MAX_TRANSLATED_TABLE_CHARS:
                    raise ValueError("translated table is too large")
        return value


class _Unsupported(ValueError):  # noqa: N818 - private sentinel, not a public exception API
    pass


def _unsupported(reason: str) -> CanonicalTableGrid:
    return CanonicalTableGrid(supported=False, reason=reason)


def _has_control(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def _validate_inline(
    inline: object,
    *,
    depth: int,
    count: list[int],
    total_chars: list[int],
) -> None:
    if depth > MAX_CAPTION_DEPTH:
        raise ValueError("caption inline nesting is too deep")
    if not isinstance(inline, dict):
        raise ValueError("caption inline must be an object")
    if not set(inline) <= _INLINE_KEYS:
        raise ValueError("caption inline has unknown keys")
    tag = inline.get("t")
    if not isinstance(tag, str) or tag not in _SUPPORTED_INLINE_TYPES:
        raise ValueError("caption inline has an unsupported tag")
    count[0] += 1
    if count[0] > MAX_CAPTION_INLINES:
        raise ValueError("caption has too many inline nodes")
    for key in ("v", "ref", "kind", "href"):
        item = inline.get(key)
        if item is not None and not isinstance(item, str):
            raise ValueError(f"caption inline {key} must be a string or null")
        if isinstance(item, str):
            if len(item) > MAX_CAPTION_STRING_CHARS:
                raise ValueError("caption inline string is too long")
            if _has_control(item):
                raise ValueError("caption inline contains control characters")
            total_chars[0] += len(item)
            if total_chars[0] > MAX_TRANSLATED_TABLE_CHARS:
                raise ValueError("translated caption is too large")
    children = inline.get("children")
    if children is not None:
        if tag != "emphasis" or not isinstance(children, list):
            raise ValueError("only emphasis may contain inline children")
        for child in children:
            _validate_inline(
                child,
                depth=depth + 1,
                count=count,
                total_chars=total_chars,
            )


def _collapse(value: str) -> str:
    return " ".join(html.unescape(value).split())


def _math_fragments(value: str) -> list[str]:
    return [match.group(0) for match in _MATH_RE.finditer(value)]


def _is_translatable_prose(source: str, _math_items: list[str]) -> bool:
    candidate = _MATH_RE.sub(" ", unicodedata.normalize("NFKC", source))
    candidate = _URL_RE.sub(" ", candidate).strip()
    if not candidate:
        return False
    if _NUMERIC_UNIT_RE.fullmatch(candidate):
        return False
    latin_words = re.findall(r"[A-Za-z]+", candidate)
    if not latin_words:
        # In particular, an already-Japanese-only cell does not become a target.
        return False
    compact = re.sub(r"\s+", "", candidate)
    if _COMPACT_IDENTIFIER_RE.fullmatch(compact):
        return False
    if (
        not any(char.isspace() for char in candidate)
        and _SEPARATED_IDENTIFIER_RE.fullmatch(candidate)
        and (
            any(char.isdigit() for char in candidate)
            or sum(char.isupper() for char in candidate) > 1
        )
    ):
        return False
    if len(latin_words) == 1 and len(latin_words[0]) == 1:
        return False
    # A Latin prose label is targetable even when the cell also contains numbers or Japanese.
    return any(len(word) >= 2 for word in latin_words)


@dataclass
class _HtmlCell:
    header: bool
    rowspan: int
    colspan: int
    parts: list[str] = field(default_factory=list)
    math: list[str] = field(default_factory=list)
    inline_stack: list[tuple[str, bool]] = field(default_factory=list)
    math_parts: list[str] | None = None
    math_annotation_parts: list[str] | None = None
    math_alt_authoritative: bool = False
    inline_nodes: int = 0
    text_chars: int = 0


class _CanonicalHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, bool, int, int, list[str]]]] = []
        self._row: list[tuple[str, bool, int, int, list[str]]] | None = None
        self._cell: _HtmlCell | None = None
        self._inside = False
        self._seen = False
        self._done = False
        self._cell_count = 0

    def _fail(self, reason: str) -> NoReturn:
        raise _Unsupported(reason)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attr = {key.casefold(): value for key, value in attrs}
        if tag == "table":
            if self._inside or self._seen:
                self._fail("nested or multiple HTML tables are unsupported")
            self._inside = True
            self._seen = True
            return
        if not self._inside:
            return
        if tag in _ACTIVE_HTML_TAGS:
            self._fail("active HTML is unsupported in table cells")
        if tag == "tr":
            if self._row is not None or self._cell is not None:
                self._fail("malformed HTML table row")
            self._row = []
            return
        if tag in {"td", "th"}:
            if self._row is None or self._cell is not None:
                self._fail("table cell is outside a row")
            rowspan = self._span(attr.get("rowspan"), "rowspan")
            colspan = self._span(attr.get("colspan"), "colspan")
            self._cell_count += 1
            if self._cell_count > MAX_TABLE_CELLS:
                self._fail("HTML table has too many cells")
            if len(self._row) >= MAX_TABLE_CELLS_PER_ROW:
                self._fail("HTML table row has too many cells")
            self._cell = _HtmlCell(tag == "th", rowspan, colspan)
            return
        if tag in _HTML_CONTAINER_TAGS:
            if self._cell is not None:
                self._fail("table container is nested in a cell")
            return
        if self._cell is None:
            # Textual wrappers around rows make physical indexing ambiguous.
            if tag not in _HTML_VOID_TAGS:
                self._fail("unsupported HTML table structure")
            return
        self._cell.inline_nodes += 1
        if self._cell.inline_nodes > MAX_TABLE_INLINE_NODES:
            self._fail("HTML cell has too many inline nodes")
        if len(self._cell.inline_stack) >= MAX_TABLE_NESTING:
            self._fail("HTML cell nesting is too deep")
        if tag == "br":
            self._append_cell_text(" ")
            return
        if tag == "img":
            alt = attr.get("alt")
            if alt:
                self._append_cell_text(alt)
            return
        css_class = (attr.get("class") or "").casefold()
        is_math = tag == "math" or "ltx_math" in css_class
        if is_math:
            if self._cell.math_parts is not None:
                self._fail("nested HTML math is unsupported")
            alt = attr.get("alttext") or attr.get("data-tex") or attr.get("alt")
            self._cell.math_parts = [alt] if alt else []
            self._cell.math_alt_authoritative = bool(alt)
        elif (
            tag == "annotation"
            and self._cell.math_parts is not None
            and not self._cell.math_alt_authoritative
            and "tex" in (attr.get("encoding") or "").casefold()
        ):
            self._cell.math_annotation_parts = []
        self._cell.inline_stack.append((tag, is_math))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _HTML_VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "table":
            if not self._inside or self._cell is not None or self._row is not None:
                self._fail("malformed HTML table ending")
            self._inside = False
            self._done = True
            return
        if not self._inside:
            return
        if tag in {"td", "th"}:
            if self._cell is None or self._row is None or self._cell.header != (tag == "th"):
                self._fail("mismatched HTML table cell")
            if self._cell.inline_stack:
                self._fail("unclosed inline element in table cell")
            source = _collapse("".join(self._cell.parts))
            if len(source) > MAX_TABLE_CELL_SOURCE_CHARS:
                self._fail("HTML table cell is too long")
            # Derive occurrences from the final visible source so repeated formulas retain
            # their exact multiplicity.  alttext math has already been inserted into it.
            math_items = _math_fragments(source)
            if len(math_items) > MAX_TABLE_MATH_FRAGMENTS:
                self._fail("HTML cell has too many math fragments")
            self._row.append(
                (
                    source,
                    self._cell.header,
                    self._cell.rowspan,
                    self._cell.colspan,
                    math_items,
                )
            )
            self._cell = None
            return
        if tag == "tr":
            if self._row is None or self._cell is not None or not self._row:
                self._fail("malformed or empty HTML table row")
            self.rows.append(self._row)
            if len(self.rows) > MAX_TABLE_ROWS:
                self._fail("HTML table has too many rows")
            self._row = None
            return
        if tag in _HTML_CONTAINER_TAGS:
            if self._cell is not None:
                self._fail("table container end tag is nested in a cell")
            return
        if self._cell is None:
            return
        if not self._cell.inline_stack or self._cell.inline_stack[-1][0] != tag:
            self._fail("mismatched inline element in table cell")
        _name, is_math = self._cell.inline_stack.pop()
        if tag == "annotation" and self._cell.math_annotation_parts is not None:
            annotation = _collapse("".join(self._cell.math_annotation_parts))
            self._cell.math_annotation_parts = None
            if annotation:
                self._cell.math_parts = [annotation]
                self._cell.math_alt_authoritative = True
        if is_math:
            assert self._cell.math_parts is not None
            raw_math = _collapse("".join(self._cell.math_parts))
            self._cell.math_parts = None
            self._cell.math_annotation_parts = None
            self._cell.math_alt_authoritative = False
            if raw_math:
                fragment = raw_math if _math_fragments(raw_math) else f"${raw_math.strip('$')}$"
                self._cell.math.append(fragment)
                self._cell.parts.append(fragment)

    def handle_data(self, data: str) -> None:
        if self._cell is None:
            return
        self._append_cell_text(data)

    def _append_cell_text(self, value: str) -> None:
        assert self._cell is not None
        if self._cell.math_parts is not None:
            # alttext is authoritative; descendants are presentation MathML.
            if self._cell.math_annotation_parts is not None:
                self._cell.math_annotation_parts.append(value)
            elif not self._cell.math_alt_authoritative:
                self._cell.math_parts.append(value)
            return
        self._cell.parts.append(value)
        self._cell.text_chars += len(value)
        if self._cell.text_chars > MAX_TABLE_CELL_SOURCE_CHARS * 2:
            self._fail("HTML table cell is too long")

    def _span(self, raw: str | None, name: str) -> int:
        if raw is None:
            return 1
        if not raw.isascii() or not raw.isdigit():
            self._fail(f"invalid HTML {name}")
        value = int(raw)
        if value < 1 or value > MAX_TABLE_SPAN:
            self._fail(f"invalid HTML {name}")
        return value

    def finish(self) -> None:
        if not self._seen:
            self._fail("HTML table element was not found")
        if self._inside or not self._done or self._cell is not None or self._row is not None:
            self._fail("unterminated HTML table")
        if not self.rows:
            self._fail("HTML table has no physical rows")


def _parse_html(raw: str) -> CanonicalTableGrid:
    parser = _CanonicalHTMLParser()
    parser.feed(raw)
    parser.close()
    parser.finish()
    rows: list[list[CanonicalTableCell]] = []
    for row_index, source_row in enumerate(parser.rows):
        row: list[CanonicalTableCell] = []
        for cell_index, (
            source,
            header_cell,
            rowspan,
            colspan,
            math_items,
        ) in enumerate(source_row):
            if row_index + rowspan > len(parser.rows):
                raise _Unsupported("HTML rowspan exceeds the physical table")
            row.append(
                CanonicalTableCell(
                    id=f"r{row_index}c{cell_index}",
                    source=source,
                    header=header_cell,
                    rowspan=rowspan,
                    colspan=colspan,
                    translatable=_is_translatable_prose(source, math_items),
                    math=math_items,
                )
            )
        rows.append(row)
    return CanonicalTableGrid(supported=True, source_format="html", rows=rows)


def _skip_space(raw: str, pos: int, end: int) -> int:
    while pos < end and raw[pos].isspace():
        pos += 1
    return pos


def _read_group(raw: str, pos: int, end: int) -> tuple[int, int, int]:
    pos = _skip_space(raw, pos, end)
    if pos >= end or raw[pos] != "{":
        raise _Unsupported("expected a bounded LaTeX group")
    depth = 1
    index = pos + 1
    content_start = index
    while index < end:
        char = raw[index]
        if char == "\\":
            index += 2
            continue
        if char == "%":
            newline = raw.find("\n", index + 1, end)
            index = end if newline < 0 else newline + 1
            continue
        if char == "{":
            depth += 1
            if depth > MAX_TABLE_NESTING:
                raise _Unsupported("LaTeX table nesting is too deep")
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content_start, index, index + 1
        index += 1
    raise _Unsupported("unterminated LaTeX group")


def _skip_optional_group(raw: str, pos: int, end: int) -> int:
    pos = _skip_space(raw, pos, end)
    if pos >= end or raw[pos] != "[":
        return pos
    depth = 1
    index = pos + 1
    while index < end:
        if raw[index] == "\\":
            index += 2
            continue
        if raw[index] == "[":
            depth += 1
        elif raw[index] == "]":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise _Unsupported("unterminated LaTeX optional argument")


def _trim_latex_segment(raw: str, start: int, end: int) -> tuple[int, int]:
    start = _skip_space(raw, start, end)
    while end > start and raw[end - 1].isspace():
        end -= 1
    rule_re = re.compile(
        r"\\(?:toprule|midrule|bottomrule|hline|addlinespace)\*?\b"
        r"|\\(?:cline|cmidrule)(?:\([^)]*\))?\s*\{[^{}]*\}",
        re.IGNORECASE,
    )
    while start < end:
        match = rule_re.match(raw, start, end)
        if match is None:
            break
        start = _skip_space(raw, match.end(), end)
    return start, end


def _unwrap_latex_cell(
    raw: str, start: int, end: int
) -> tuple[int, int, int, int, list[Literal["multicolumn", "multirow"]]]:
    start, end = _trim_latex_segment(raw, start, end)
    colspan = 1
    rowspan = 1
    wrappers: list[Literal["multicolumn", "multirow"]] = []
    while start < end:
        command = re.match(r"\\(multicolumn|multirow)\*?", raw[start:end], re.IGNORECASE)
        if command is None:
            break
        name = command.group(1).casefold()
        pos = start + command.end()
        if name == "multirow":
            pos = _skip_optional_group(raw, pos, end)
        first_start, first_end, pos = _read_group(raw, pos, end)
        raw_span = raw[first_start:first_end].strip()
        if not raw_span.isascii() or not raw_span.isdigit():
            raise _Unsupported(f"invalid LaTeX {name} span")
        span = int(raw_span)
        if span < 1 or span > MAX_TABLE_SPAN:
            raise _Unsupported(f"invalid LaTeX {name} span")
        _ignored_start, _ignored_end, pos = _read_group(raw, pos, end)
        if name == "multirow":
            pos = _skip_optional_group(raw, pos, end)
        body_start, body_end, after = _read_group(raw, pos, end)
        if raw[after:end].strip():
            raise _Unsupported(f"trailing material after LaTeX {name} wrapper")
        if name == "multicolumn":
            if colspan != 1:
                raise _Unsupported("duplicate LaTeX multicolumn wrapper")
            colspan = span
            wrappers.append("multicolumn")
        else:
            if rowspan != 1:
                raise _Unsupported("duplicate LaTeX multirow wrapper")
            rowspan = span
            wrappers.append("multirow")
        start, end = _trim_latex_segment(raw, body_start, body_end)
    return start, end, rowspan, colspan, wrappers


def _split_latex_rows(raw: str, start: int, end: int) -> list[list[tuple[int, int]]]:
    rows: list[list[tuple[int, int]]] = []
    row: list[tuple[int, int]] = []
    cell_start = start
    depth = 0
    math_mode: str | None = None
    index = start
    while index < end:
        char = raw[index]
        if char == "%" and math_mode is None:
            newline = raw.find("\n", index + 1, end)
            index = end if newline < 0 else newline + 1
            continue
        if char == "$":
            if index > start and raw[index - 1] == "\\":
                index += 1
                continue
            if math_mode is None:
                math_mode = "$$" if raw.startswith("$$", index) else "$"
                index += len(math_mode)
                continue
            if math_mode in {"$", "$$"} and raw.startswith(math_mode, index):
                index += len(math_mode)
                math_mode = None
                continue
        if char == "\\":
            pair = raw[index : index + 2]
            if pair in {r"\(", r"\["} and math_mode is None:
                math_mode = r"\)" if pair == r"\(" else r"\]"
                index += 2
                continue
            if math_mode in {r"\)", r"\]"} and pair == math_mode:
                math_mode = None
                index += 2
                continue
            separator_end: int | None = None
            if depth == 0 and math_mode is None:
                if pair == r"\\":
                    separator_end = index + 2
                else:
                    for command in (r"\tabularnewline", r"\cr"):
                        command_end = index + len(command)
                        if raw.startswith(command, index) and (
                            command_end >= end
                            or not (raw[command_end].isalpha() or raw[command_end] == "@")
                        ):
                            separator_end = command_end
                            break
            if separator_end is not None:
                row.append((cell_start, index))
                rows.append(row)
                if len(rows) > MAX_TABLE_ROWS:
                    raise _Unsupported("LaTeX table has too many rows")
                row = []
                index = separator_end
                if index < end and raw[index] == "*":
                    index += 1
                index = _skip_optional_group(raw, index, end)
                cell_start = index
                continue
            index += 2
            continue
        if math_mode is None:
            if char == "{":
                depth += 1
                if depth > MAX_TABLE_NESTING:
                    raise _Unsupported("LaTeX table nesting is too deep")
            elif char == "}":
                depth -= 1
                if depth < 0:
                    raise _Unsupported("unbalanced LaTeX table braces")
            elif char == "&" and depth == 0:
                row.append((cell_start, index))
                if len(row) >= MAX_TABLE_CELLS_PER_ROW:
                    raise _Unsupported("LaTeX table row has too many cells")
                index += 1
                cell_start = index
                continue
        index += 1
    if depth != 0 or math_mode is not None:
        raise _Unsupported("unbalanced LaTeX table cell")
    if row or raw[cell_start:end].strip():
        row.append((cell_start, end))
        rows.append(row)
    return rows


def _latex_visible_text(value: str) -> tuple[str, list[str]]:
    math_items: list[str] = []

    def protect_math(match: re.Match[str]) -> str:
        math_items.append(match.group(0))
        if len(math_items) > MAX_TABLE_MATH_FRAGMENTS:
            raise _Unsupported("LaTeX cell has too many math fragments")
        # The marker must not begin with a letter: a preceding command such as
        # ``\\quad$...$`` would otherwise consume it as one control word during
        # visible-text normalization.
        return f"__ALINEA_MATH_{len(math_items) - 1}__"

    protected = _MATH_RE.sub(protect_math, value)
    protected = _replace_latex_visible_wrappers(protected)
    protected = _replace_latex_accents(protected)
    protected = re.sub(r"(?<!\\)%[^\n]*(?:\n|$)", " ", protected)
    protected = re.sub(r"\\(?:toprule|midrule|bottomrule|hline|addlinespace)\*?\b", " ", protected)
    protected = re.sub(r"\\(?:cline|cmidrule)(?:\([^)]*\))?\s*\{[^{}]*\}", " ", protected)
    protected = re.sub(r"\\(?:label|footnotemark)\s*\{[^{}]*\}", " ", protected)
    protected = protected.replace(r"\\", " ")
    protected = re.sub(r"\\([#$%&_{}])", r"\1", protected)
    protected = re.sub(r"\\[A-Za-z@]+\*?", "", protected)
    protected = protected.replace("~", " ")
    protected = protected.replace("{", "").replace("}", "")
    protected = re.sub(
        r"__ALINEA_MATH_(\d+)__",
        lambda match: math_items[int(match.group(1))],
        protected,
    )
    return _collapse(protected), math_items


def _replace_latex_visible_wrappers(value: str) -> str:
    """Remove common non-visible arguments while retaining only displayed bodies."""

    specs: tuple[tuple[str, int, int, bool], ...] = (
        ("textcolor", 2, 1, False),
        ("colorbox", 2, 1, False),
        ("fcolorbox", 3, 2, False),
        ("href", 2, 1, False),
        ("makecell", 1, 0, True),
        ("shortstack", 1, 0, True),
    )
    for command, argument_count, visible_index, optional in specs:
        pattern = re.compile(rf"\\{command}\*?\b")
        replacements = 0
        search_from = 0
        while replacements <= MAX_TABLE_NESTING:
            match = pattern.search(value, search_from)
            if match is None:
                break
            pos = match.end()
            if optional:
                pos = _skip_optional_group(value, pos, len(value))
            groups: list[tuple[int, int]] = []
            try:
                for _ in range(argument_count):
                    group_start, group_end, pos = _read_group(value, pos, len(value))
                    groups.append((group_start, group_end))
            except _Unsupported:
                raise _Unsupported(f"malformed LaTeX {command} wrapper") from None
            visible_start, visible_end = groups[visible_index]
            visible = value[visible_start:visible_end]
            value = value[: match.start()] + visible + value[pos:]
            search_from = match.start()
            replacements += 1
        if replacements > MAX_TABLE_NESTING:
            raise _Unsupported("too many nested LaTeX formatting wrappers")
    return value


_ACCENT_MARKS = {
    '"': "\u0308",
    "'": "\u0301",
    "`": "\u0300",
    "^": "\u0302",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
    "u": "\u0306",
    "v": "\u030c",
    "H": "\u030b",
    "r": "\u030a",
    "c": "\u0327",
    "k": "\u0328",
    "b": "\u0331",
}
_ACCENT_RE = re.compile(r"\\([\"'`^~=\.uvHrcbk])\s*\{?([A-Za-z])\}?")


def _replace_latex_accents(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return unicodedata.normalize("NFC", match.group(2) + _ACCENT_MARKS[match.group(1)])

    return _ACCENT_RE.sub(replace, value)


def _parse_latex(raw: str) -> CanonicalTableGrid:
    begin_matches = list(_LATEX_BEGIN_RE.finditer(raw))
    if len(begin_matches) != 1:
        raise _Unsupported("LaTeX table must contain exactly one supported environment")
    begin = begin_matches[0]
    if raw[: begin.start()].strip():
        raise _Unsupported("content outside the LaTeX table environment")
    env = begin.group(1)
    pos = _skip_space(raw, begin.end(), len(raw))
    pos = _skip_optional_group(raw, pos, len(raw))
    if env.casefold() in {"tabularx", "tabular*"}:
        _width_start, _width_end, pos = _read_group(raw, pos, len(raw))
        pos = _skip_optional_group(raw, pos, len(raw))
    _spec_start, _spec_end, body_start = _read_group(raw, pos, len(raw))
    end_re = re.compile(rf"\\end\s*\{{{re.escape(env)}\}}", re.IGNORECASE)
    end_matches = list(end_re.finditer(raw, body_start))
    if len(end_matches) != 1:
        raise _Unsupported("unterminated or repeated LaTeX table environment")
    end_match = end_matches[0]
    if raw[end_match.end() :].strip():
        raise _Unsupported("content outside the LaTeX table environment")
    source_rows = _split_latex_rows(raw, body_start, end_match.start())
    rows: list[list[CanonicalTableCell]] = []
    total_cells = 0
    for segments in source_rows:
        parsed_segments: list[
            tuple[
                int,
                int,
                int,
                int,
                list[Literal["multicolumn", "multirow"]],
                str,
                list[str],
                bool,
            ]
        ] = []
        for segment_start, segment_end in segments:
            body_s, body_e, rowspan, colspan, wrappers = _unwrap_latex_cell(
                raw, segment_start, segment_end
            )
            source, math_items = _latex_visible_text(raw[body_s:body_e])
            if len(source) > MAX_TABLE_CELL_SOURCE_CHARS:
                raise _Unsupported("LaTeX table cell is too long")
            raw_body = raw[body_s:body_e]
            header = bool(re.search(r"\\(?:textbf|bfseries|thead)\b", raw_body))
            parsed_segments.append(
                (body_s, body_e, rowspan, colspan, wrappers, source, math_items, header)
            )
        # A trailing rule after the last row separator is not a physical row.
        # Do not use visible text as the discriminator: a source macro such as
        # ``\\textbf{\\PEvideo}`` can expand only while parsing the complete
        # document, yet it remains a physical cell whose source offsets are
        # required when writing the translated table back to the original TeX.
        if len(parsed_segments) == 1 and not re.sub(
            r"(?<!\\)%[^\n]*(?:\n|$)",
            "",
            raw[parsed_segments[0][0] : parsed_segments[0][1]],
        ).strip():
            continue
        if not parsed_segments:
            continue
        total_cells += len(parsed_segments)
        if total_cells > MAX_TABLE_CELLS:
            raise _Unsupported("LaTeX table has too many cells")
        row_index = len(rows)
        row: list[CanonicalTableCell] = []
        for cell_index, item in enumerate(parsed_segments):
            body_s, body_e, rowspan, colspan, wrappers, source, math_items, header = item
            row.append(
                CanonicalTableCell(
                    id=f"r{row_index}c{cell_index}",
                    source=source,
                    header=header,
                    rowspan=rowspan,
                    colspan=colspan,
                    translatable=_is_translatable_prose(source, math_items),
                    math=math_items,
                    latex_body_start=body_s,
                    latex_body_end=body_e,
                    latex_wrappers=wrappers,
                )
            )
        rows.append(row)
    if not rows:
        raise _Unsupported("LaTeX table has no physical rows")
    for row_index, row in enumerate(rows):
        for cell in row:
            if row_index + cell.rowspan > len(rows):
                raise _Unsupported("LaTeX multirow exceeds the physical table")
    return CanonicalTableGrid(supported=True, source_format="latex", rows=rows)


def parse_table_grid(raw: str | None) -> CanonicalTableGrid:
    """Parse bounded HTML or LaTeX into physical cells, failing closed on ambiguity."""

    if not isinstance(raw, str) or not raw.strip():
        return _unsupported("table source is missing")
    if len(raw) > MAX_TABLE_RAW_BYTES:
        return _unsupported("table source exceeds the byte limit")
    if len(raw.encode("utf-8")) > MAX_TABLE_RAW_BYTES:
        return _unsupported("table source exceeds the byte limit")
    try:
        if _LATEX_BEGIN_RE.search(raw):
            return _parse_latex(raw)
        if re.search(r"<\s*table\b", raw, re.IGNORECASE):
            return _parse_html(raw)
        return _unsupported("supported table markup was not found")
    except (_Unsupported, UnicodeError, ValueError) as exc:
        return _unsupported(str(exc) or "table source is malformed")


def validate_table_translation_content(
    value: object,
    grid: CanonicalTableGrid,
) -> TableTranslationContent | None:
    """Validate the strict typed object and its exact physical matrix shape.

    ``cells=None`` is a valid caption-only result.  When a matrix is present, every
    target cell must contain a non-empty string and every non-target cell must be null.
    """

    try:
        content = TableTranslationContent.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        return None
    if not grid.supported:
        return content if content.cells is None else None
    if content.cells is None:
        return content
    if len(content.cells) != len(grid.rows):
        return None
    for source_row, translated_row in zip(grid.rows, content.cells, strict=True):
        if len(translated_row) != len(source_row):
            return None
        for source_cell, translated in zip(source_row, translated_row, strict=True):
            if source_cell.translatable:
                if translated is None or not translated.strip():
                    return None
                if Counter(_math_fragments(translated)) != Counter(source_cell.math):
                    return None
            elif translated is not None:
                return None
    return content


def table_cells_complete(value: object, grid: CanonicalTableGrid) -> bool:
    """Return whether cell work is satisfied for this source grid.

    Unsupported/malformed grids and supported grids without targets are vacuously
    complete.  Callers still require the table's one primary TranslationUnit.
    """

    targets = [cell for row in grid.rows for cell in row if cell.translatable]
    if not grid.supported or not targets:
        return True
    content = validate_table_translation_content(value, grid)
    return content is not None and content.cells is not None


__all__ = [
    "CanonicalTableCell",
    "CanonicalTableGrid",
    "TableTranslationContent",
    "parse_table_grid",
    "table_cells_complete",
    "validate_table_translation_content",
]
