"""Build translated PDFs from arXiv LaTeX sources.

The builder keeps the original TeX commands/environments and replaces only
translatable text blocks with stored translation units. Figures, equations,
labels, citations, URLs, and hyperlinks remain LaTeX-native so the compiled
Japanese PDF stays as close as possible to the source layout.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import posixpath
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import pairwise
from pathlib import Path
from typing import Any

import fitz
from alinea_core.db.models import (
    DocumentRevision,
    Paper,
    SourceAsset,
    TranslationSet,
    TranslationUnit,
)
from alinea_core.document.blocks import Block, DocumentContent
from alinea_core.document.plaintext import block_to_plain
from alinea_core.parsing.latex_parser import (
    _BEGIN_RE,
    LatexArchive,
    LatexParseError,
    _read_braced,
    _read_environment,
    _resolve_bibliography,
    extract_latex_archive,
    parse_latex_source,
    select_main_tex,
)
from alinea_core.settings import CoreSettings
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_core.translation.pipeline import (
    BLOCKING_FLAGS,
    resolve_translation_set_units,
)
from alinea_core.translation.table_cells import (
    CanonicalTableGrid,
    TableTranslationContent,
    parse_table_grid,
    validate_table_translation_content,
)
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_worker.structured_pdf import (
    PdfRenderManifest,
    StructuredLatexSource,
    render_structured_japanese_source,
)

PDF_BUILD_VERSION = "japanese-pdf-3.0.22"
DEFAULT_TEXLIVE_IMAGE = "alinea-texlive-ja:latest"
_REPO_DOCKER_WRAPPER = Path(__file__).resolve().parents[4] / "scripts" / "dev-docker.sh"

_SECTION_CMD_RE = re.compile(r"\\(?:section|subsection|subsubsection)\*?(?:\s*\[[^\]]*\])?\s*\{")
_STANDALONE_PARAGRAPH_CMD_RE = re.compile(r"\\paragraph\*?(?:\s*\[[^\]]*\])?\s*\{")
_FRONTMATTER_TRANSLATABLE_CMD_RE = re.compile(
    r"\\(?P<name>contribution|correspondence)(?:\s*\[[^\]]*\])?\s*\{"
)
_CAPTION_CMD_RE = re.compile(r"\\caption\*?(?:\s*\[[^\]]*\])?\s*\{")
_INCLUDEGRAPHICS_CMD_RE = re.compile(r"\\includegraphics\*?(?:\s*\[[^\]]*\])?\s*\{")
_ITEM_RE = re.compile(r"\\item\b\s*(?:\[[^\]]*\])?")
_OVERFULL_RE = re.compile(r"Overfull \\[hv]box \((?P<pt>[0-9.]+)pt too")
# TeXLive2026 (minted v3) が frozencache/finalizecache キャッシュを読めない問題の
# 回避に使う。 \usepackage[<options>]{minted} 全体にマッチさせ、options 側は
# frozencache/finalizecache の有無をカンマ分割で判定する(順序・空白は問わない)。
_MINTED_USEPACKAGE_RE = re.compile(
    r"\\usepackage\s*\[(?P<options>[^\]]*)\]\s*\{\s*minted\s*\}"
)
_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
# 元の `\[A-Za-z@]{2,}` は単純すぎて、コードリスティングやファイルパス、URL 中の
# エスケープされたコマンド(例: `\Users\name`, `\_foo`, `\%`)にも誤検知する。
# 未展開のままだと明らかにレイアウトが壊れる構造コマンド(見出し・引用・
# 相互参照・環境・図表・脚注)のみに対象を限定し、引数の `{` を伴う場合だけ
# 「展開されずに残った生の TeX」と見なす。原文 PDF のテキストとの比較も
# 検討したが、このバリデータは PDF バイト列しか受け取らない(HTML 由来の
# リビジョンには比較対象の原文 PDF がない)ため、コマンド集合を絞る方式を採用する。
_VISIBLE_TEX_CONTROL_RE = re.compile(
    r"\\(?:section|subsection|subsubsection|chapter|paragraph|"
    r"cite|citet|citep|citeauthor|citeyear|citealt|citealp|"
    r"ref|eqref|autoref|cref|Cref|nameref|"
    r"begin|end|includegraphics|caption|label|footnote)\*?"
    r"(?:\s*\[[^\]\n]{0,80}\])?\s*\{"
)
# 日本語は原文の英語より字幅が広く、原文レイアウトを保ったまま再コンパイルすると
# 数 pt 程度の Overfull box が出るのは正常な範囲。約 1em(本文フォントサイズ
# 10-12pt 相当)までは許容し、それを超える食い込みだけを
# 「本当に破綻したレイアウト」として検出する。
_PDF_BOUNDS_TOLERANCE_PT = 12.0
_LATEX_SOURCE_KINDS = ("arxiv_latex", "latex")

_SKIP_ENVS = {
    "equation",
    "align",
    "gather",
    "multline",
    "eqnarray",
    "tabular",
    "tabularx",
    "array",
    "tikzpicture",
    "picture",
    "verbatim",
    "lstlisting",
    "minted",
    "thebibliography",
}
_FIGURE_ENVS = {"figure", "wrapfigure"}
_TABLE_ENVS = {"table", "wraptable"}
_LIST_ENVS = {"itemize", "enumerate"}
_QUOTE_ENVS = {"quote", "quotation"}
_TRANSPARENT_ENVS = {
    "center",
    "flushleft",
    "flushright",
    "minipage",
    "small",
    "footnotesize",
}
_THEOREM_ENVS = {
    "theorem",
    "assumption",
    "lemma",
    "corollary",
    "proposition",
    "definition",
    "remark",
    "claim",
    "example",
    "proof",
}
_FRONTMATTER_HINTS = (
    "\\maketitle",
    "\\title",
    "\\author",
    "\\date",
    "\\thanks",
    "\\affil",
    "\\institute",
)
_LEADING_PARAGRAPH_PREFIX_RE = re.compile(
    r"^(?P<prefix>\s*(?:(?:\\label\{[^}]*\}|"
    r"\\(?:vspace|hspace)\*?\{[^}]*\}|"
    r"\\(?:noindent|par|centering|raggedright|raggedleft|onecolumn|twocolumn|"
    r"newpage|clearpage|pagebreak|smallskip|medskip|bigskip|tiny|scriptsize|"
    r"footnotesize|small|normalsize|large|Large|LARGE|huge|Huge)\b)\s*)*)",
    re.DOTALL,
)
_LABEL_RE = re.compile(r"\\label\{[^}]*\}")
_FOOTNOTE_CMD_RE = re.compile(r"\\footnote(?:\s*\[[^\]]*\])?\s*\{")
_NONVISIBLE_MATCH_COMMAND_RE = re.compile(
    r"\\(?:todo[A-Za-z@]*|fxnote|fixme|noteToSelf)\b"
)
_COMMENT_MARKER_RE = re.compile(r"%__ALINEA_COMMENT_(\d+)__")
_PARAGRAPH_SEPARATOR_RE = re.compile(
    r"(\n(?:(?:[ \t]*\n)|(?:[ \t]*%__ALINEA_COMMENT_\d+__[ \t]*\n))+)"
)
_INLINE_MATH_RE = re.compile(
    r"(?P<dollar>(?<!\\)\$(?!\$)(?P<dollar_body>.*?)(?<!\\)\$)"
    r"|(?P<paren>\\\((?P<paren_body>.*?)\\\))"
    r"|(?P<bracket>\\\[(?P<bracket_body>.*?)\\\])",
    re.DOTALL,
)
_TABLE_CELL_MATH_RE = re.compile(
    r"(?<!\\)(?:\$\$(?:\\.|[^$])*?\$\$|\$(?:\\.|[^$])*?\$)"
    r"|\\\((?:\\.|[^\\])*?\\\)|\\\[(?:\\.|[^\\])*?\\\]",
    re.DOTALL,
)
_CITATION_CMD_RE = re.compile(
    r"\\(?:cite|citet|citep|citeauthor|citeyear|citealt|citealp)\*?"
    r"(?:\s*\[[^\]]*\])*\s*\{"
)
_REF_CMD_RE = re.compile(r"\\(?:ref|eqref|autoref|cref|Cref|nameref)\*?\s*\{")
_URL_CMD_RE = re.compile(r"\\url\s*\{")
_HREF_CMD_RE = re.compile(r"\\href\s*\{")
_CODE_CMD_RE = re.compile(r"\\(?:texttt|code)\s*\{")
_EMPHASIS_CMD_RE = re.compile(r"\\(emph|textit|textsc|textbf)\s*\{")
_COLORED_EMPHASIS_CMD_RE = re.compile(
    r"\\(?P<outer>emph|textit|textsc|textbf)\s*\{\s*"
    r"\\textcolor\s*\{(?P<color>[^{}]+)\}\s*\{"
)
_LINEBREAK_CMD_RE = re.compile(r"\\\\\s*(?P<option>\[[^\]]*\])?")
_HREF_LABEL_MARKER = "__ALINEA_HREF_LABEL__"
_INPUT_CMD_RE = re.compile(
    r"\\(?P<command>input|include)(?:\{(?P<braced_name>[^}]+)\}|\s+(?P<bare_name>[^\s%\\{}]+))"
)
_PRESERVED_BODY_COMMAND_RE = re.compile(
    r"\\(?:bibliography|bibliographystyle|addbibresource|nocite)\b"
    r"(?:\s*\[[^\]]*\])?\s*\{[^{}]*\}|"
    r"\\printbibliography\b(?:\s*\[[^\]]*\])?"
)
_LAYOUT_ARTIFACT_BLOCK_RE = re.compile(
    r"^\s*-\d+(?:\.\d+)?(?:mm|cm|pt|pc|in|ex|em)?"
    r"(?:\s+\d+(?:\.\d+)?(?:mm|cm|pt|pc|in|ex|em)?)*\s*$"
)
# verbatim 系環境は内部に空行があっても1つの不透明な断片として扱う。
# `_replace_paragraphs` の空行分割より前に丸ごと切り出し、段落境界や
# カーソル消費の対象から外す(本文中に直接書かれていても figure/table に
# 包まれていても同様に保護する)。
_VERBATIM_ENV_RE = re.compile(
    r"\\begin\{(verbatim\*?|Verbatim\*?|lstlisting|minted)\}.*?\\end\{\1\}",
    re.DOTALL,
)
_OPAQUE_PARAGRAPH_SEGMENT_RE = re.compile(
    r"\\begin\{(?:verbatim\*?|Verbatim\*?|lstlisting|minted)\}.*?"
    r"\\end\{(?:verbatim\*?|Verbatim\*?|lstlisting|minted)\}"
    r"|(?<!\\)\$\$.*?(?<!\\)\$\$"
    r"|\\\[.*?\\\]",
    re.DOTALL,
)

# _TranslationCursor.take() の内容検証で使う正規化・類似度判定。
# 構造化ブロックの平文(block_to_plain 等)と、レンダラーが実際に走査している
# 生 TeX 断片を同じ土台で比較できるよう、コマンド名・波括弧・数式記号・空白を
# 落として英数字主体の文字列へ簡約する。厳密な逆変換ではなく「まったく違う内容
# かどうか」を安価に見分けるための正規化であることに注意(逆に完全一致を要求
# すると、引用の任意引数や脚注の展開差のような正常な揺れで誤検知してしまう)。
_MATCH_COMMAND_RE = re.compile(r"\\[A-Za-z]+\*?")
_MATCH_NON_WORD_RE = re.compile(r"[^\w]+")
_ZERO_ARGUMENT_COMMAND_RE = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand)\s*\{\\(?P<name>[A-Za-z@]+)\}"
    r"(?:\s*\[\s*0\s*\])?\s*\{"
)
_COMMAND_USE_RE = re.compile(r"\\(?P<name>[A-Za-z@]+)\b")
_MATCH_PREFIX_CHARS = 400
# 実測(AutoDev 論文の再現)では正しい対応は 0.9 超、無関係な断片(色定義や
# マクロ定義の断片など)は 0.1 未満に分かれるため、余裕を持たせても十分安全。
_MATCH_MIN_SCORE = 0.6
# 同じ型の候補を何個先まで内容照合するか。これを超えても一致しなければ、
# その raw セグメントは「対応するブロックが無い(幻の段落)」とみなし、
# カーソルを進めずに原文のまま残す。
_RESYNC_WINDOW = 8


def _strip_footnote_bodies_for_match(text: str) -> str:
    """Remove footnote bodies because they are represented by separate IR blocks."""

    parts: list[str] = []
    position = 0
    for match in _FOOTNOTE_CMD_RE.finditer(text):
        try:
            _body, end = _read_braced(text, match.end() - 1)
        except LatexParseError:
            continue
        parts.append(text[position : match.start()])
        parts.append(" ")
        position = end
    parts.append(text[position:])
    return "".join(parts)


def _strip_nonvisible_macro_bodies_for_match(text: str) -> str:
    """Remove editorial macro payloads from matching without altering emitted TeX.

    Draft sources commonly put a long ``\\todo...{...}`` immediately before a
    paragraph. The payload has no PDF-visible text, but it used to consume the
    bounded comparison prefix and prevent the following visible paragraph from
    matching its parsed block.
    """

    parts: list[str] = []
    position = 0
    for match in _NONVISIBLE_MATCH_COMMAND_RE.finditer(text):
        cursor = match.end()
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor < len(text) and text[cursor] == "[":
            option_end = text.find("]", cursor + 1)
            if option_end == -1:
                continue
            cursor = option_end + 1
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
        if cursor >= len(text) or text[cursor] != "{":
            continue
        try:
            _body, end = _read_braced(text, cursor)
        except LatexParseError:
            continue
        parts.append(text[position : match.start()])
        parts.append(" ")
        position = end
    parts.append(text[position:])
    return "".join(parts)


def _normalize_for_match(text: str) -> str:
    normalized = _COMMENT_MARKER_RE.sub(
        " ",
        _strip_nonvisible_macro_bodies_for_match(_strip_footnote_bodies_for_match(text)),
    )
    normalized = _MATCH_COMMAND_RE.sub(" ", normalized)
    normalized = _MATCH_NON_WORD_RE.sub(" ", normalized)
    return " ".join(normalized.lower().split())[:_MATCH_PREFIX_CHARS]


def _match_source_text(block: Block) -> str:
    """位置対応の内容検証に使う平文。

    見出しは block_to_plain だと自動採番(``block.number``)が先頭に付くが、
    生 TeX の ``\\section{...}`` 引数には採番が含まれないため、比較は
    ``block.title`` 単体で行う。figure/table は block_to_plain がそもそも
    キャプションだけを返すため、呼び出し側でキャプション引数だけを渡せば
    そのまま比較できる。
    """

    if block.type == "heading":
        return block.title or ""
    return block_to_plain(block)


def _content_match_score(raw_norm: str, block_norm: str) -> float:
    """比較材料が無い側は判定不能として通す(退行防止)。両方ある場合のみ判定する。

    ただし非対称に扱う: ブロック側に平文が無い(キャプション無し figure/table や
    空見出しなど)場合は検証不能として通すが、raw 側がコマンド名だけで実体が無い
    (``\\abstract{`` や ``\\justifying`` だけの断片。正規化すると空文字列になる)のに
    ブロック側には実在する本文がある場合、それを自動一致とみなしてはならない。
    そうしないと、この「何も無い」raw 断片が実在段落を誤って消費してしまう
    (LingBot 論文 arXiv:2607.07534 の \\abstract{ / \\justifying で検出)。
    """

    if not block_norm:
        return 1.0
    if not raw_norm:
        return 0.0
    if raw_norm in block_norm or block_norm in raw_norm:
        return 1.0
    matcher = SequenceMatcher(None, raw_norm, block_norm, autojunk=False)
    longest = matcher.find_longest_match(0, len(raw_norm), 0, len(block_norm))
    coverage = longest.size / min(len(raw_norm), len(block_norm))
    return max(matcher.ratio(), coverage)


def _zero_argument_command_bodies(tex: str) -> dict[str, str]:
    """Return safe zero-argument command bodies for source-text matching only."""

    commands: dict[str, str] = {}
    for match in _ZERO_ARGUMENT_COMMAND_RE.finditer(tex):
        try:
            body, _end = _read_braced(tex, match.end() - 1)
        except LatexParseError:
            continue
        if "#" not in body:
            commands[match.group("name")] = body
    return commands


def _expand_zero_argument_commands(text: str, commands: dict[str, str]) -> str:
    """Expand only known zero-argument commands before comparing visible text."""

    expanded = text
    for _ in range(8):
        next_text = _COMMAND_USE_RE.sub(
            lambda match: commands.get(match.group("name"), match.group(0)), expanded
        )
        if next_text == expanded:
            break
        expanded = next_text
    return expanded


def _content_matches(raw_text: str, block: Block, commands: dict[str, str]) -> bool:
    raw_norm = _normalize_for_match(_expand_zero_argument_commands(raw_text, commands))
    block_norm = _normalize_for_match(_match_source_text(block))
    return _content_match_score(raw_norm, block_norm) >= _MATCH_MIN_SCORE


class LatexPdfBuildError(RuntimeError):
    """Translated PDF build failed. ``kind`` is stable for log classification."""

    def __init__(self, kind: str, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail or {}


@dataclass(frozen=True)
class RenderedLatexSource:
    main_tex_name: str
    main_tex: str
    support_text_files: dict[str, str]
    binary_files: dict[str, bytes]
    replacements: dict[str, int]
    replaced_block_ids: frozenset[str] = frozenset()
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LatexPdfBuildOutcome:
    built: bool
    translated_key: str | None = None
    warnings: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    renderer: str | None = None
    fallback_reason: str | None = None


class _TranslationCursor:
    """Consumes document blocks in source order while the TeX tree is transformed."""

    def __init__(
        self,
        content: DocumentContent,
        units: dict[str, TranslationUnit],
        *,
        zero_argument_commands: dict[str, str] | None = None,
        is_fairmeta: bool = False,
    ) -> None:
        tracked = {
            "heading",
            "paragraph",
            "figure",
            "table",
            "list",
            "quote",
            "theorem",
            "footnote",
        }
        self.blocks = [block for _section, block in content.iter_blocks() if block.type in tracked]
        self.units = units
        self.zero_argument_commands = zero_argument_commands or {}
        self.is_fairmeta = is_fairmeta
        self.pos = 0
        self.replacements: dict[str, int] = {}
        self.replaced_block_ids: set[str] = set()
        self.warnings: list[str] = []
        self.footnotes = {
            block.label: block
            for _section, block in content.iter_blocks()
            if block.type == "footnote" and block.label
        }

    def take(
        self, *types: str, match_text: str | None = None
    ) -> tuple[Block | None, TranslationUnit | None]:
        """次に消費すべきブロックを取り出す。

        ``match_text`` を渡すと、単純な「型が合う次のブロック」ではなく、
        その raw セグメントの内容とブロックの平文が一致するかどうかも検証する
        (内容非対応の消費フィックス)。呼び出し元がまだ raw セグメントの
        テキストを特定できない箇所(複数画像 figure の余剰ブロックを型だけで
        飛ばす等)では ``match_text=None`` のまま従来通りの位置ベース動作を使う。
        """

        self._consume_layout_artifacts()
        wanted = set(types)
        if match_text is None:
            return self._take_positional(wanted, types)
        return self._take_verified(wanted, types, match_text)

    def _resolve(self, block: Block) -> tuple[Block | None, TranslationUnit | None]:
        unit = self.units.get(block.id)
        if unit is not None and _unit_is_displayable_for_block(unit, block):
            return block, unit
        return block, None

    def _take_positional(
        self, wanted: set[str], types: tuple[str, ...]
    ) -> tuple[Block | None, TranslationUnit | None]:
        for idx in range(self.pos, len(self.blocks)):
            block = self.blocks[idx]
            if block.type not in wanted:
                continue
            self.pos = idx + 1
            return self._resolve(block)
        self.warnings.append(f"対応するブロックが見つかりません: {','.join(types)}")
        return None, None

    def _take_verified(
        self, wanted: set[str], types: tuple[str, ...], match_text: str
    ) -> tuple[Block | None, TranslationUnit | None]:
        """内容照合つきで次のブロックを取り出す(位置ズレの再同期)。

        同じ型の候補を ``_RESYNC_WINDOW`` 個先まで内容照合し、一致するものが
        見つかればそこへ再同期する(途中で内容不一致だったブロックは消費せず
        孤立した欠落として残す)。見つからなければこの raw セグメントを
        「対応するブロックが無い幻の断片」とみなし、カーソルを進めずに
        呼び出し元へ None を返す(原文をそのまま残させる)。どちらの場合も
        誤った位置への置換より、置換されずに原文が残ることを優先する。
        """

        idx = self.pos
        checked = 0
        skipped_ids: list[str] = []
        while idx < len(self.blocks) and checked < _RESYNC_WINDOW:
            block = self.blocks[idx]
            if block.type not in wanted:
                idx += 1
                continue
            checked += 1
            if _content_matches(match_text, block, self.zero_argument_commands):
                if skipped_ids:
                    self.warnings.append(
                        "内容不一致のブロックを飛び越えて再同期しました: "
                        f"skipped={','.join(skipped_ids)} matched={block.id} "
                        f"raw={match_text[:60]!r}"
                    )
                self.pos = idx + 1
                return self._resolve(block)
            skipped_ids.append(block.id)
            idx += 1
        self.warnings.append(
            "raw セグメントに対応するブロックが見つからないため原文のまま残しました"
            f"(内容不一致): types={','.join(types)} raw={match_text[:60]!r}"
        )
        return None, None

    def _consume_layout_artifacts(self) -> None:
        """Skip parser-only dimension fragments without emitting visible PDF text.

        Commands such as ``\\vspace{-6mm}`` are preserved in the source TeX, but the
        lightweight parser can also expose their arguments as tiny paragraph blocks.
        They are translation bookkeeping rather than prose and must not shift the
        positional mapping or be printed a second time.
        """

        while self.pos < len(self.blocks):
            block = self.blocks[self.pos]
            if not _is_layout_artifact_block(block):
                return
            self.pos += 1
            unit = self.units.get(block.id)
            if unit is not None and _unit_is_displayable(unit):
                self.replaced_block_ids.add(block.id)

    def mark(self, block: Block | None) -> None:
        if block is None or block.id in self.replaced_block_ids:
            return
        self.replaced_block_ids.add(block.id)
        self.replacements[block.type] = self.replacements.get(block.type, 0) + 1

    def render_footnote(self, ref: str, fallback: str = "") -> str:
        block = self.footnotes.get(ref)
        if block is None:
            return fallback
        unit = self.units.get(block.id)
        source_body = ""
        fallback_match = _FOOTNOTE_CMD_RE.match(fallback)
        if fallback_match:
            try:
                source_body, _end = _read_braced(fallback, fallback_match.end() - 1)
            except LatexParseError:
                source_body = ""
        replacement = _unit_to_latex(unit, self, source_latex=source_body)
        if replacement is None:
            return fallback
        self.mark(block)
        option_match = re.match(r"\\footnote(?P<option>\s*\[[^\]]*\])?", fallback)
        option = (option_match.group("option") or "") if option_match else ""
        return rf"\footnote{option}{{{replacement}}}"


def _unit_is_displayable(unit: TranslationUnit) -> bool:
    return bool(unit.text_ja) and not (set(unit.quality_flags or []) & BLOCKING_FLAGS)


def _unit_is_displayable_for_block(unit: TranslationUnit, block: Block) -> bool:
    # Captions are the only visible source text for a figure.  The translation
    # pipeline records a synthetic ``（図）`` unit for image-only figures so the
    # viewer can represent the block, but there is no TeX text to replace in a
    # source-preserving PDF.  Treating it as required would incorrectly force a
    # structured-PDF fallback for multi-panel figures.
    if block.type == "figure" and not block.caption and unit.text_ja.strip() in {"（図）", "(図)"}:
        return False
    typed_table = (
        block.type == "table"
        and isinstance(unit.content_ja, dict)
        and unit.content_ja.get("kind") == "table"
    )
    return bool(unit.text_ja or typed_table) and not (
        set(unit.quality_flags or []) & BLOCKING_FLAGS
    )


def _is_layout_artifact_block(block: Block) -> bool:
    if block.type != "paragraph" or not block.inlines:
        return False
    if any(inline.t != "text" for inline in block.inlines):
        return False
    text = "".join(inline.v for inline in block.inlines)
    return _LAYOUT_ARTIFACT_BLOCK_RE.fullmatch(text) is not None


def _latex_escape_text(text: str) -> str:
    text = "".join(ch for ch in text if ch >= " " or ch in "\n\t")
    repl = {
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
    return "".join(repl.get(ch, ch) for ch in text)


def _mask_latex_spans(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for index in range(start, min(end, len(chars))):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


class _SourceInlineContext:
    """Reuse the source's inline LaTeX commands around protected translation atoms."""

    def __init__(self, source: str) -> None:
        self.original = source
        self.math: dict[str, list[str]] = {}
        self.citations: dict[str, list[str]] = {}
        self.refs: dict[str, list[str]] = {}
        self.urls: dict[str, list[str]] = {}
        self.codes: dict[str, list[str]] = {}
        self.emphasis: list[tuple[str, str, str | None]] = []
        self.footnotes: list[str] = []
        self.linebreaks: dict[str, list[str]] = {}

        masked_spans: list[tuple[int, int]] = []
        for match in _INLINE_MATH_RE.finditer(source):
            body = next(
                (
                    match.group(group)
                    for group in ("dollar_body", "paren_body", "bracket_body")
                    if match.group(group) is not None
                ),
                "",
            ).strip()
            self._add(self.math, body, match.group(0))
            masked_spans.append(match.span())

        for match in _FOOTNOTE_CMD_RE.finditer(source):
            try:
                _body, end = _read_braced(source, match.end() - 1)
            except LatexParseError:
                continue
            self.footnotes.append(source[match.start() : end])
            masked_spans.append((match.start(), end))

        visible = _mask_latex_spans(source, masked_spans)
        for match in _LINEBREAK_CMD_RE.finditer(visible):
            option = match.group("option")
            if option:
                self._add(self.linebreaks, option, match.group(0))
        self._collect_braced_commands(visible, _CITATION_CMD_RE, self.citations, split=True)
        self._collect_braced_commands(visible, _REF_CMD_RE, self.refs)
        self._collect_braced_commands(visible, _URL_CMD_RE, self.urls)
        self._collect_braced_commands(visible, _CODE_CMD_RE, self.codes)
        self._collect_hrefs(visible)
        for match in _EMPHASIS_CMD_RE.finditer(visible):
            colored = _COLORED_EMPHASIS_CMD_RE.match(visible, match.start())
            if colored is not None:
                color = colored.group("color")
                self.emphasis.append(
                    (
                        rf"\{colored.group('outer')}{{\textcolor{{{color}}}{{",
                        "}}",
                        color,
                    )
                )
            else:
                self.emphasis.append((rf"\{match.group(1)}{{", "}", None))

    @staticmethod
    def _add(mapping: dict[str, list[str]], key: str, value: str) -> None:
        mapping.setdefault(key.strip(), []).append(value)

    @staticmethod
    def _take(mapping: dict[str, list[str]], key: str) -> str | None:
        values = mapping.get(key.strip())
        return values.pop(0) if values else None

    def _collect_braced_commands(
        self,
        source: str,
        pattern: re.Pattern[str],
        mapping: dict[str, list[str]],
        *,
        split: bool = False,
    ) -> None:
        for match in pattern.finditer(source):
            try:
                body, end = _read_braced(source, match.end() - 1)
            except LatexParseError:
                continue
            command = self.original[match.start() : end]
            keys = [key.strip() for key in body.split(",")] if split else [body.strip()]
            for index, key in enumerate(key for key in keys if key):
                self._add(mapping, key, command if index == 0 else "")

    def _collect_hrefs(self, source: str) -> None:
        for match in _HREF_CMD_RE.finditer(source):
            try:
                href, first_end = _read_braced(source, match.end() - 1)
                label_start = first_end
                while label_start < len(source) and source[label_start].isspace():
                    label_start += 1
                _label, end = _read_braced(source, label_start)
            except LatexParseError:
                continue
            styled = self.original[match.start() : label_start + 1] + _HREF_LABEL_MARKER + "}"
            self._add(self.urls, href, styled)

    def take_math(self, value: str) -> str | None:
        return self._take(self.math, value)

    def take_citation(self, ref: str) -> str | None:
        return self._take(self.citations, ref)

    def take_ref(self, ref: str) -> str | None:
        return self._take(self.refs, ref)

    def take_url(self, href: str) -> str | None:
        return self._take(self.urls, href)

    def take_code(self, value: str) -> str | None:
        return self._take(self.codes, value)

    def take_emphasis(self) -> tuple[str, str, str | None]:
        return self.emphasis.pop(0) if self.emphasis else (r"\emph{", "}", None)

    def take_footnote(self) -> str:
        return self.footnotes.pop(0) if self.footnotes else ""

    def restore_text_layout(self, value: str) -> str:
        escaped = _latex_escape_text(value)
        for option in list(self.linebreaks):
            if option not in escaped:
                continue
            command = self._take(self.linebreaks, option)
            if command is not None:
                escaped = escaped.replace(option, command, 1)
        return escaped


def _inline_to_latex(
    inline: dict[str, Any],
    cursor: _TranslationCursor | None = None,
    source: _SourceInlineContext | None = None,
) -> str:
    t = inline.get("t")
    if t == "text":
        value = str(inline.get("v") or "")
        return source.restore_text_layout(value) if source else _latex_escape_text(value)
    if t == "emphasis":
        children = inline.get("children")
        inner = (
            "".join(_inline_to_latex(child, cursor, source) for child in children)
            if isinstance(children, list)
            else _latex_escape_text(str(inline.get("v") or ""))
        )
        prefix, suffix, color = source.take_emphasis() if source else (r"\emph{", "}", None)
        if color:
            leaked_prefix = _latex_escape_text(color + "{")
            if inner.startswith(leaked_prefix) and inner.endswith(r"\}"):
                inner = inner[len(leaked_prefix) : -2]
        return prefix + inner + suffix
    if t == "math_inline":
        value = str(inline.get("v") or "")
        styled = source.take_math(value) if source else None
        return styled if styled is not None else f"${value}$"
    if t == "citation":
        ref = str(inline.get("ref") or "").strip()
        styled = source.take_citation(ref) if source else None
        if styled is not None:
            return styled
        return rf"\cite{{{ref}}}" if ref else ""
    if t == "ref":
        ref = str(inline.get("ref") or "").strip()
        if not ref:
            return _latex_escape_text(str(inline.get("v") or ""))
        styled = source.take_ref(ref) if source else None
        if styled is not None:
            return styled
        return rf"\eqref{{{ref}}}" if inline.get("kind") == "equation" else rf"\ref{{{ref}}}"
    if t == "url":
        href = str(inline.get("href") or inline.get("v") or "").strip()
        label = _latex_escape_text(str(inline.get("v") or href))
        if not href:
            return label
        styled = source.take_url(href) if source else None
        if styled is not None:
            return styled.replace(_HREF_LABEL_MARKER, label)
        if str(inline.get("v") or href).strip() == href:
            return rf"\url{{{href}}}"
        return rf"\href{{{href}}}{{{label}}}"
    if t == "code_inline":
        value = str(inline.get("v") or "")
        styled = source.take_code(value) if source else None
        if styled is not None:
            return styled
        return rf"\texttt{{{_latex_escape_text(value)}}}"
    if t == "footnote_ref":
        fallback = source.take_footnote() if source else ""
        if cursor is None:
            return fallback
        return cursor.render_footnote(str(inline.get("ref") or ""), fallback)
    return _latex_escape_text(str(inline.get("v") or ""))


def _table_caption_to_latex(
    caption: list[object] | None,
    cursor: _TranslationCursor,
    source_latex: str,
) -> str | None:
    if caption is None:
        return None
    source = _SourceInlineContext(source_latex)
    rendered: list[str] = []
    for inline in caption:
        if isinstance(inline, dict):
            data = inline
        else:
            dump = getattr(inline, "model_dump", None)
            if not callable(dump):
                return None
            data = dump(mode="json", exclude_none=True)
        if not isinstance(data, dict):
            return None
        rendered.append(_inline_to_latex(data, cursor, source))
    return "".join(rendered)


def _latex_escape_table_cell(text: str, protected_math: list[str]) -> str | None:
    """Escape prose while retaining exactly the canonical protected math multiset."""

    matches = list(_TABLE_CELL_MATH_RE.finditer(text))
    if Counter(match.group(0) for match in matches) != Counter(protected_math):
        return None
    rendered: list[str] = []
    cursor = 0
    for match in matches:
        rendered.append(_latex_escape_text(text[cursor : match.start()]))
        rendered.append(match.group(0))
        cursor = match.end()
    rendered.append(_latex_escape_text(text[cursor:]))
    return "".join(rendered)


def _overlay_latex_table_cells(
    raw: str,
    grid: CanonicalTableGrid,
    translated: TableTranslationContent,
) -> str | None:
    if translated.cells is None:
        return raw
    replacements: list[tuple[int, int, str]] = []
    for source_row, translated_row in zip(grid.rows, translated.cells, strict=True):
        for cell, value in zip(source_row, translated_row, strict=True):
            if value is None:
                continue
            start = cell.latex_body_start
            end = cell.latex_body_end
            if (
                not cell.translatable
                or start is None
                or end is None
                or start < 0
                or end < start
                or end > len(raw)
            ):
                return None
            replacement = _latex_escape_table_cell(value, cell.math)
            if replacement is None:
                return None
            replacements.append((start, end, replacement))

    ordered = sorted(replacements)
    if any(left[1] > right[0] for left, right in pairwise(ordered)):
        return None
    rendered = raw
    for start, end, replacement in reversed(ordered):
        rendered = rendered[:start] + replacement + rendered[end:]
    return rendered


def _unit_to_latex(
    unit: TranslationUnit | None,
    cursor: _TranslationCursor | None = None,
    *,
    source_latex: str = "",
    strip_leading_options: bool = False,
) -> str | None:
    if unit is None or not _unit_is_displayable(unit):
        return None
    content: object = unit.content_ja
    if strip_leading_options:
        content = _without_environment_option_artifact(content)
    content = _without_nested_environment_artifacts(content, source_latex)
    if isinstance(content, list) and all(isinstance(item, dict) for item in content):
        source = _SourceInlineContext(source_latex)
        return "".join(_inline_to_latex(item, cursor, source) for item in content)
    return _latex_escape_text(unit.text_ja)


def _without_environment_option_artifact(content: object) -> object:
    """Remove parser-leaked ``[tcolorbox options]`` from translated prose."""

    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return content
    first = content[0]
    if first.get("t") != "text":
        return content
    value = str(first.get("v") or "")
    match = re.match(r"^\s*\[(?P<options>.*?)\]\s*", value, flags=re.DOTALL)
    if match is None or not any(
        marker in match.group("options")
        for marker in ("colback=", "colframe=", "boxrule=", "breakable", "title=")
    ):
        return content
    cleaned = value[match.end() :]
    head = {**first, "v": cleaned}
    return ([head] if cleaned else []) + list(content[1:])


def _without_nested_environment_artifacts(content: object, source_latex: str) -> object:
    """Remove words produced by parsing ``\\begin{quote}`` as visible prose."""

    marker_count = sum(
        source_latex.count(rf"\begin{{{env}}}") + source_latex.count(rf"\end{{{env}}}")
        for env in _QUOTE_ENVS
    )
    if marker_count == 0 or not isinstance(content, list):
        return content

    remaining = marker_count

    def clean_inline(inline: object) -> object:
        nonlocal remaining
        if not isinstance(inline, dict):
            return inline
        cleaned = dict(inline)
        if cleaned.get("t") == "text" and remaining:
            value = str(cleaned.get("v") or "")
            for marker in ("引用", "quote"):
                while marker in value and remaining:
                    value = value.replace(marker, "", 1)
                    remaining -= 1
            cleaned["v"] = value
        children = cleaned.get("children")
        if isinstance(children, list):
            cleaned["children"] = [clean_inline(child) for child in children]
        return cleaned

    return [clean_inline(inline) for inline in content]


def _preserve_nested_quote(source_latex: str, replacement: str) -> str:
    for env in _QUOTE_ENVS:
        begin = rf"\begin{{{env}}}"
        end = rf"\end{{{env}}}"
        if begin not in source_latex or end not in source_latex:
            continue
        inner_start = source_latex.find(begin) + len(begin)
        inner_prefix_match = re.match(
            r"\s*(?P<size>\\(?:tiny|scriptsize|footnotesize|small|normalsize|large|Large)\b)?",
            source_latex[inner_start:],
        )
        size = inner_prefix_match.group("size") if inner_prefix_match else None
        return begin + (size or "") + "\n" + replacement + "\n" + end
    return replacement


def _inject_japanese_preamble(tex: str) -> str:
    if "% alinea-ja-pdf" in tex:
        return tex
    m = re.search(r"\\begin\{document\}", tex)
    if not m:
        raise LatexPdfBuildError("invalid_latex", "main TeX has no \\begin{document}")
    preamble = r"""
% alinea-ja-pdf: Japanese PDF build support
\usepackage{iftex}
\usepackage{adjustbox}
\ifLuaTeX
  \usepackage{luatexja}
  \usepackage{luatexja-fontspec}
  \IfFontExistsTF{Noto Serif CJK JP}{\setmainjfont{Noto Serif CJK JP}}{}
  \IfFontExistsTF{Noto Sans CJK JP}{\setsansjfont{Noto Sans CJK JP}}{}
  \IfFontExistsTF{Noto Sans Mono CJK JP}{\setmonojfont{Noto Sans Mono CJK JP}}{}
\else
  \PackageError{alinea-ja-pdf}{LuaLaTeX is required for Japanese PDF output}{}
\fi
"""
    return tex[: m.start()] + preamble + "\n" + tex[m.start() :]


def _inject_luatex_compat(tex: str) -> str:
    """Provide pdfTeX primitive aliases before a source document class is loaded."""

    if "% alinea-luatex-compat" in tex:
        return tex
    document_class = re.search(r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{", tex)
    if document_class is None:
        return tex
    legacy_pdfoutput = re.search(r"\\pdfoutput\b", tex)
    compat = r"""% alinea-luatex-compat: legacy pdfLaTeX classes under LuaLaTeX
\ifdefined\directlua
  \ifdefined\pdfoutput\else
    \let\pdfoutput\outputmode
  \fi
  \providecommand\pdfmapline[1]{}
\fi
"""
    insertion_at = document_class.start()
    if legacy_pdfoutput is not None:
        insertion_at = min(insertion_at, legacy_pdfoutput.start())
    return tex[:insertion_at] + compat + tex[insertion_at:]


def _replace_braced_command_arg(
    text: str, match: re.Match[str], replacement: str | None
) -> tuple[str, int]:
    _original, end = _read_braced(text, match.end() - 1)
    if replacement is None:
        return text[match.start() : end], end
    return text[match.start() : match.end() - 1] + "{" + replacement + "}", end


def _replace_abstract_command(tex: str, abstract_ja: str | None) -> str:
    """Translate class-style ``\\abstract{...}`` declarations in the preamble."""

    if not abstract_ja:
        return tex
    document = re.search(r"\\begin\{document\}", tex)
    search_end = document.start() if document else len(tex)
    match = re.search(r"\\abstract\s*\{", tex[:search_end])
    if match is None:
        return tex
    replacement, end = _replace_braced_command_arg(tex, match, _latex_escape_text(abstract_ja))
    return tex[: match.start()] + replacement + tex[end:]


def _extract_caption_text(inner: str) -> tuple[re.Match[str] | None, str | None]:
    """環境内の ``\\caption{...}`` 引数を取り出す。内容照合の match_text にも使う。"""

    match = _CAPTION_CMD_RE.search(inner)
    if match is None:
        return None, None
    try:
        caption_text, _end = _read_braced(inner, match.end() - 1)
    except LatexParseError:
        return match, None
    return match, caption_text


def _replace_caption(inner: str, cursor: _TranslationCursor, block_type: str) -> str:
    m, caption_text = _extract_caption_text(inner)
    # block_to_plain(figure/table) はキャプションの平文しか返さないため、内容照合も
    # 環境本体全体ではなくキャプション引数だけで行う(無いときは本体全体で妥協する)。
    match_text = caption_text if caption_text is not None else inner
    block, unit = cursor.take(block_type, match_text=match_text)
    # \includegraphics が複数枚ある figure/wrapfigure 環境は、パーサー側(_figure_env)が
    # 画像ごとに figure ブロックを1つずつ生成し、キャプションは先頭の画像のブロックにのみ
    # 付与する。ここで消費するブロック数を画像枚数に合わせないと、以降の figure ブロックの
    # 位置対応が1つずれ、無関係なキャプションが入れ替わって描画されてしまう。この余剰分は
    # 内容ではなく枚数で決まるため、内容照合はせず従来通り型だけで飛ばす。
    extra_images = max(0, len(_INCLUDEGRAPHICS_CMD_RE.findall(inner)) - 1)
    for _ in range(extra_images):
        cursor.take(block_type)
    replacement = _unit_to_latex(unit, cursor, source_latex=inner)
    if m is None or replacement is None:
        return inner
    repl, end = _replace_braced_command_arg(inner, m, replacement)
    cursor.mark(block)
    return inner[: m.start()] + repl + inner[end:]


def _table_mapping_warning(cursor: _TranslationCursor, block: Block | None, reason: str) -> None:
    block_id = block.id if block is not None else "unknown"
    cursor.warnings.append(f"表セルの対応が一致しないため原文を保持しました: {block_id} ({reason})")


def _source_tabular_fragment(inner: str) -> str | None:
    """Return the exact tabular fragment emitted by the source document.

    Parser ``block.raw`` is normalized for reading and may remove presentational
    wrappers such as ``\\textbf{...}``. Cell overlays need offsets in the actual
    TeX string, so they must be based on this source fragment instead.
    """

    for match in _BEGIN_RE.finditer(inner):
        name = match.group(1)
        if name.rstrip("*") not in {"tabular", "tabularx", "tabulary", "longtable"}:
            continue
        try:
            _body, end = _read_environment(inner, match.end(), name)
        except LatexParseError:
            return None
        return inner[match.start() : end]
    return None


def _constrain_source_tabular_width(inner: str) -> str:
    """Shrink translated tabulars only when they exceed their original line width."""

    raw = _source_tabular_fragment(inner)
    if raw is None or not re.match(r"\\begin\{(?:tabular|tabularx|tabulary)\*?\}", raw):
        return inner
    start = inner.find(raw)
    if start < 0 or inner.find(raw, start + 1) >= 0:
        return inner
    wrapped = rf"\begin{{adjustbox}}{{max width=\textwidth}}{raw}\end{{adjustbox}}"
    return inner[:start] + wrapped + inner[start + len(raw) :]


def _source_grid_with_translation_contract(
    source_grid: CanonicalTableGrid, contract_grid: CanonicalTableGrid
) -> CanonicalTableGrid | None:
    """Combine source offsets with the parser's persisted-cell translation contract."""

    if len(source_grid.rows) != len(contract_grid.rows):
        return None
    rows = []
    for source_row, contract_row in zip(source_grid.rows, contract_grid.rows, strict=True):
        if len(source_row) != len(contract_row):
            return None
        rows.append(
            [
                source_cell.model_copy(
                    update={
                        "translatable": contract_cell.translatable,
                        "math": contract_cell.math,
                    }
                )
                for source_cell, contract_cell in zip(source_row, contract_row, strict=True)
            ]
        )
    return source_grid.model_copy(update={"rows": rows})


def _replace_table(inner: str, cursor: _TranslationCursor) -> str:
    caption_match, caption_text = _extract_caption_text(inner)
    block, unit = cursor.take(
        "table", match_text=caption_text if caption_text is not None else inner
    )
    if block is None or unit is None:
        return inner

    # Legacy table units remain caption-only.
    if not isinstance(unit.content_ja, dict):
        replacement = _unit_to_latex(unit, cursor, source_latex=inner)
        if caption_match is None or replacement is None:
            return inner
        rendered_caption, end = _replace_braced_command_arg(inner, caption_match, replacement)
        cursor.mark(block)
        return inner[: caption_match.start()] + rendered_caption + inner[end:]

    raw = _source_tabular_fragment(inner) or block.raw or ""
    source_grid = parse_table_grid(raw)
    # Validate the persisted matrix against parser-normalized cells (macro names
    # such as ``\\llama{}`` are expanded there), then overlay it onto the exact
    # source grid whose offsets still include presentational macros.
    grid = parse_table_grid(block.raw or raw)
    if grid.supported:
        translated = validate_table_translation_content(unit.content_ja, grid)
        if translated is None:
            _table_mapping_warning(cursor, block, "invalid typed matrix")
            return inner
    else:
        try:
            translated = TableTranslationContent.model_validate(unit.content_ja)
        except (TypeError, ValueError):
            _table_mapping_warning(cursor, block, grid.reason or "unsupported source grid")
            return inner
        if translated.cells is not None:
            _table_mapping_warning(cursor, block, grid.reason or "unsupported source grid")
            return inner

    rendered = inner
    if grid.supported and translated.cells is not None:
        if not source_grid.supported:
            _table_mapping_warning(cursor, block, source_grid.reason or "source table grid")
            return inner
        overlay_grid = _source_grid_with_translation_contract(source_grid, grid)
        if overlay_grid is None:
            _table_mapping_warning(cursor, block, "source grid shape")
            return inner
        rendered_raw = _overlay_latex_table_cells(raw, overlay_grid, translated)
        raw_start = inner.find(raw) if raw else -1
        if rendered_raw is None or raw_start < 0 or inner.find(raw, raw_start + 1) >= 0:
            _table_mapping_warning(cursor, block, "source offsets")
            return inner
        rendered = inner[:raw_start] + rendered_raw + inner[raw_start + len(raw) :]

    caption_match = _CAPTION_CMD_RE.search(rendered)
    if caption_match is not None and translated.caption is not None:
        try:
            source_caption, _caption_end = _read_braced(rendered, caption_match.end() - 1)
        except LatexParseError:
            _table_mapping_warning(cursor, block, "caption")
            return inner
        caption = _table_caption_to_latex(list(translated.caption), cursor, source_caption)
        if caption is None:
            _table_mapping_warning(cursor, block, "caption")
            return inner
        rendered_caption, end = _replace_braced_command_arg(rendered, caption_match, caption)
        rendered = rendered[: caption_match.start()] + rendered_caption + rendered[end:]

    rendered = _constrain_source_tabular_width(rendered)
    cursor.mark(block)
    return rendered


def _split_list_translation(text: str) -> list[str]:
    items = [part.strip() for part in re.split(r"(?:\n\s*-\s*|\n+|\s+-\s+)", text) if part.strip()]
    return items or ([text.strip()] if text.strip() else [])


def _split_list_content(content: object) -> list[list[dict[str, Any]]]:
    if not isinstance(content, list) or not all(isinstance(item, dict) for item in content):
        return []
    items: list[list[dict[str, Any]]] = [[]]
    for inline in content:
        if inline.get("t") != "text":
            items[-1].append(inline)
            continue
        value = str(inline.get("v") or "")
        parts = re.split(r"(?:\n\s*-\s*|\s+-\s+)", value)
        for index, part in enumerate(parts):
            if index:
                items.append([])
            if part:
                items[-1].append({"t": "text", "v": part})
    return [item for item in items if item]


def _top_level_item_matches(inner: str) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    pos = 0
    while pos < len(inner):
        item = _ITEM_RE.search(inner, pos)
        env = _BEGIN_RE.search(inner, pos)
        if item is None:
            break
        if env is not None and env.start() < item.start():
            try:
                _env_inner, end = _read_environment(inner, env.end(), env.group(1))
            except LatexParseError:
                pos = env.end()
            else:
                pos = end
            continue
        matches.append(item)
        pos = item.end()
    return matches


def _replace_list_items(inner: str, cursor: _TranslationCursor) -> str:
    block, unit = cursor.take("list", match_text=inner)
    if unit is None or not _unit_is_displayable(unit):
        return inner
    matches = _top_level_item_matches(inner)
    structured_items = _split_list_content(unit.content_ja)
    if structured_items:
        translations: list[str] = []
        for index, item in enumerate(structured_items):
            item_end = matches[index + 1].start() if index + 1 < len(matches) else len(inner)
            source_text = inner[matches[index].end() : item_end] if index < len(matches) else ""
            source = _SourceInlineContext(source_text)
            translations.append(
                "".join(_inline_to_latex(inline, cursor, source) for inline in item)
            )
    else:
        translations = [_latex_escape_text(item) for item in _split_list_translation(unit.text_ja)]
    if not matches or not translations:
        return inner
    if len(translations) != len(matches):
        cursor.warnings.append(
            f"箇条書きの項目数が一致しません: source={len(matches)} translated={len(translations)}"
        )
        return inner
    out: list[str] = [inner[: matches[0].start()]]
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(inner)
        out.append(inner[match.start() : match.end()].rstrip())
        out.append(" ")
        if idx < len(translations):
            labels = "".join(
                label.group(0) for label in _LABEL_RE.finditer(inner[match.end() : end])
            )
            out.append(labels)
            out.append(translations[idx])
        else:
            out.append(inner[match.end() : end])
        out.append("\n")
    cursor.mark(block)
    return "".join(out)


def _replace_whole_env(inner: str, cursor: _TranslationCursor, block_type: str) -> str:
    block, unit = cursor.take(block_type, match_text=inner)
    replacement = _unit_to_latex(unit, cursor, source_latex=inner)
    if replacement is None:
        return inner
    prefix_match = re.match(
        r"\s*(?:\[[^\]]*\]\s*)?"
        r"(?:\\(?:tiny|scriptsize|footnotesize|small|normalsize|large|Large)\b\s*)?",
        inner,
    )
    prefix = prefix_match.group(0) if prefix_match else ""
    labels = "".join(m.group(0) + "\n" for m in _LABEL_RE.finditer(inner))
    qed = r"\qedhere" if r"\qedhere" in inner else ""
    cursor.mark(block)
    return prefix + labels + replacement + qed


def _replace_tcolorbox_title(prefix: str, cursor: _TranslationCursor) -> str:
    """Translate a visible ``title=...`` option without changing the box options."""

    match = re.search(r"(?<![A-Za-z])title\s*=\s*", prefix)
    if match is None:
        return prefix
    value_start = match.end()
    if value_start >= len(prefix):
        return prefix
    if prefix[value_start] == "{":
        try:
            source_title, value_end = _read_braced(prefix, value_start)
        except LatexParseError:
            return prefix
        wrapped = True
    else:
        delimiter = re.search(r"[,\]]", prefix[value_start:])
        value_end = value_start + (delimiter.start() if delimiter else len(prefix) - value_start)
        source_title = prefix[value_start:value_end].strip()
        wrapped = False

    block, unit = cursor.take("paragraph", match_text=source_title)
    replacement = _unit_to_latex(unit, cursor, source_latex=source_title)
    if replacement is None:
        return prefix
    cursor.mark(block)
    rendered = "{" + replacement + "}" if wrapped else replacement
    return prefix[:value_start] + rendered + prefix[value_end:]


def _layout_only_chunk(chunk: str) -> bool:
    probe = _COMMENT_MARKER_RE.sub("", chunk)
    probe = re.sub(r"%[^\n]*(?:\n|$)", "", probe)
    command = re.compile(
        r"\\(?:appendix|beginappendix|maketitle|tableofcontents|bibliography|bibliographystyle|"
        r"printbibliography|addbibresource|nocite|newpage|clearpage|pagebreak|noindent|"
        r"par|centering|raggedright|raggedleft|onecolumn|twocolumn|smallskip|"
        r"medskip|bigskip|vfill|hfill|label|FloatBarrier|enlargethispage|vspace|hspace)\b"
        r"(?:\s*\[[^\]]*\])?(?:\s*\{[^{}]*\})?"
    )
    previous = None
    while previous != probe:
        previous = probe
        probe = command.sub("", probe)
    return not probe.strip()


def _trailing_layout(chunk: str) -> str:
    match = re.search(
        r"(?P<suffix>(?:\s*\\(?:par|newpage|clearpage|pagebreak|smallskip|medskip|bigskip)\b\s*)+)$",
        chunk,
    )
    return match.group("suffix") if match else ""


def _split_opaque_paragraph_segments(text: str) -> list[tuple[bool, str]]:
    """Split verbatim and display-math fragments out of paragraph matching.

    内部に空行があっても後続の段落分割(_PARAGRAPH_SEPARATOR_RE)の対象にしない。
    figure/table に包まれた minted は _replace_caption 側でキャプション以外を
    素通しするため元々安全だが、地の文に直接書かれた verbatim/lstlisting/minted は
    段落分割にさらされると空行区切りで「幻の段落」を生み、位置対応の消費先を
    ズレさせてしまう(単純な begin/end のペアなので入れ子は想定しない)。
    """

    segments: list[tuple[bool, str]] = []
    pos = 0
    for match in _OPAQUE_PARAGRAPH_SEGMENT_RE.finditer(text):
        if match.start() > pos:
            segments.append((False, text[pos : match.start()]))
        segments.append((True, match.group(0)))
        pos = match.end()
    if pos < len(text):
        segments.append((False, text[pos:]))
    return segments


def _replace_paragraphs(
    text: str,
    cursor: _TranslationCursor,
    *,
    strip_leading_options: bool = False,
) -> str:
    segments = _split_opaque_paragraph_segments(text)
    if not any(is_verbatim for is_verbatim, _segment in segments):
        return _replace_paragraphs_segment(
            text, cursor, strip_leading_options=strip_leading_options
        )
    out: list[str] = []
    first = True
    for is_verbatim, segment in segments:
        if is_verbatim:
            out.append(segment)
            continue
        out.append(
            _replace_paragraphs_segment(
                segment,
                cursor,
                strip_leading_options=strip_leading_options if first else False,
            )
        )
        first = False
    return "".join(out)


def _replace_paragraphs_segment(
    text: str,
    cursor: _TranslationCursor,
    *,
    strip_leading_options: bool = False,
) -> str:
    parts = _PARAGRAPH_SEPARATOR_RE.split(text)
    out: list[str] = []
    for idx, chunk in enumerate(parts):
        if idx % 2 == 1:
            out.append(chunk)
            continue
        stripped = chunk.strip()
        if not stripped:
            out.append(chunk)
            continue
        if any(hint in stripped for hint in _FRONTMATTER_HINTS):
            out.append(chunk)
            continue
        if _layout_only_chunk(chunk):
            out.append(chunk)
            continue
        block, unit = cursor.take("paragraph", match_text=chunk)
        replacement = _unit_to_latex(
            unit,
            cursor,
            source_latex=chunk,
            strip_leading_options=strip_leading_options,
        )
        if block is not None:
            strip_leading_options = False
        if replacement is None:
            out.append(chunk)
            continue
        prefix_match = _LEADING_PARAGRAPH_PREFIX_RE.match(chunk)
        prefix = prefix_match.group("prefix") if prefix_match else ""
        preserved_labels = "".join(
            label.group(0) + "\n"
            for label in _LABEL_RE.finditer(chunk)
            if label.group(0) not in prefix
        )
        preserved_commands = "".join(
            "\n" + command.group(0) for command in _PRESERVED_BODY_COMMAND_RE.finditer(chunk)
        )
        trailing = _trailing_layout(chunk) or ("\n" if chunk.endswith("\n") else "")
        rendered = prefix + preserved_labels + replacement + preserved_commands + trailing
        out.append(_preserve_nested_quote(chunk, rendered))
        cursor.mark(block)
    return "".join(out)


def _transform_env(
    name: str, inner: str, cursor: _TranslationCursor, abstract_ja: str | None
) -> str:
    base = name.rstrip("*")
    if base in _FIGURE_ENVS:
        return _replace_caption(inner, cursor, "figure")
    if base in _TABLE_ENVS:
        return _replace_table(inner, cursor)
    if base in _LIST_ENVS:
        return _replace_list_items(inner, cursor)
    if base in _QUOTE_ENVS:
        return _replace_whole_env(inner, cursor, "quote")
    if base in _THEOREM_ENVS:
        return _replace_whole_env(inner, cursor, "theorem")
    if base == "abstract":
        return _latex_escape_text(abstract_ja) if abstract_ja else inner
    if base == "document":
        return _transform_latex_text(inner, cursor, abstract_ja)
    if base in _SKIP_ENVS:
        return inner
    if base in _TRANSPARENT_ENVS:
        prefix = ""
        body = inner
        if base == "minipage":
            # Preserve minipage's optional position/height/alignment and mandatory
            # width arguments before translating its body.
            match = re.match(r"\s*(?:\[[^\]]*\]\s*)*(?:\{[^{}]*\}\s*)", inner)
            if match:
                prefix = match.group(0)
                body = inner[match.end() :]
        return prefix + _transform_latex_text(body, cursor, abstract_ja)
    if base == "tcolorbox":
        prefix_match = re.match(r"\s*(?:\[[^\]]*\]\s*)?", inner)
        prefix = prefix_match.group(0) if prefix_match else ""
        body = inner[len(prefix) :]
        prefix = _replace_tcolorbox_title(prefix, cursor)
        return prefix + _transform_latex_text(body, cursor, abstract_ja)
    # The parser represents custom text environments (for example tcolorbox) as
    # paragraphs. Keep the outer environment/options so the author's page style is
    # retained. A nested quote is also retained as a layout wrapper; it must not make
    # the entire custom box untranslated or shift every following positional block.
    if r"\item" not in inner:
        prefix_match = re.match(r"\s*(?:\[[^\]]*\]\s*)?", inner)
        prefix = prefix_match.group(0) if prefix_match else ""
        body = inner[len(prefix) :]
        if any(rf"\begin{{{env}}}" in body for env in _QUOTE_ENVS):
            return prefix + _replace_paragraphs(
                body,
                cursor,
                strip_leading_options=bool(prefix),
            )
        if r"\begin{" not in body:
            return prefix + _replace_paragraphs(
                body,
                cursor,
                strip_leading_options=bool(prefix),
            )
    return inner


def _find_standalone_paragraph_command(text: str, position: int) -> re.Match[str] | None:
    """Find the next newline-delimited ``\\paragraph`` title, skipping run-ins."""

    for match in _STANDALONE_PARAGRAPH_CMD_RE.finditer(text, position):
        try:
            _title, end = _read_braced(text, match.end() - 1)
        except LatexParseError:
            continue
        if end < len(text) and text[end] == "\n":
            return match
    return None


def _transform_latex_text(text: str, cursor: _TranslationCursor, abstract_ja: str | None) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        m_sec = _SECTION_CMD_RE.search(text, i)
        m_paragraph = _find_standalone_paragraph_command(text, i)
        m_frontmatter = _FRONTMATTER_TRANSLATABLE_CMD_RE.search(text, i)
        m_env = _BEGIN_RE.search(text, i)
        matches = [m for m in (m_sec, m_paragraph, m_frontmatter, m_env) if m is not None]
        if not matches:
            out.append(_replace_paragraphs(text[i:], cursor))
            break
        match = min(matches, key=lambda m: m.start())
        out.append(_replace_paragraphs(text[i : match.start()], cursor))
        if match is m_sec:
            source_title, source_end = _read_braced(text, match.end() - 1)
            block, unit = cursor.take("heading", match_text=source_title)
            replacement = _unit_to_latex(unit, cursor, source_latex=source_title)
            repl, end = _replace_braced_command_arg(text, match, replacement)
            if replacement is not None:
                cursor.mark(block)
            out.append(repl)
            i = max(end, source_end)
            continue
        if match is m_paragraph:
            source_title, source_end = _read_braced(text, match.end() - 1)
            block, unit = cursor.take("paragraph", match_text=source_title)
            replacement = _unit_to_latex(unit, cursor, source_latex=source_title)
            repl, end = _replace_braced_command_arg(text, match, replacement)
            if replacement is not None:
                cursor.mark(block)
            out.append(repl)
            i = max(end, source_end)
            continue
        if match is m_frontmatter:
            source_body, source_end = _read_braced(text, match.end() - 1)
            name = match.group("name")
            match_text = source_body if name == "contribution" else f"Correspondence: {source_body}"
            block, unit = cursor.take("paragraph", match_text=match_text)
            replacement = _unit_to_latex(unit, cursor, source_latex=source_body)
            if replacement is not None and name == "correspondence" and cursor.is_fairmeta:
                body = re.sub(r"^\s*(?:連絡先|Correspondence)\s*[:：]\s*", "", replacement)
                replacement = rf"\metadata[連絡先]{{{body}}}"
            repl, end = _replace_braced_command_arg(text, match, replacement)
            if replacement is not None:
                cursor.mark(block)
            out.append(repl)
            i = max(end, source_end)
            continue
        name = match.group(1)
        inner, end = _read_environment(text, match.end(), name)
        end_token = rf"\end{{{name}}}"
        inner_end = end - len(end_token)
        out.append(text[match.start() : match.end()])
        out.append(_transform_env(name, inner, cursor, abstract_ja))
        out.append(text[inner_end:end])
        i = end
    return "".join(out)


def _find_comment_start(line: str) -> int | None:
    """行中で最初にコメントとして機能する "%" の位置を返す(直前の連続する
    "\\" が偶数個ならエスケープされていない実コメント開始とみなす)。無ければ None。"""

    for index, char in enumerate(line):
        if char != "%":
            continue
        slash_count = 0
        before = index - 1
        while before >= 0 and line[before] == "\\":
            slash_count += 1
            before -= 1
        if slash_count % 2 == 0:
            return index
    return None


def _protect_latex_comments(files: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    protected: dict[str, str] = {}
    comments: dict[str, str] = {}
    counter = 0
    verbatim_begin = re.compile(r"\\begin\{(?:verbatim\*?|lstlisting|minted)\}")
    verbatim_end = re.compile(r"\\end\{(?:verbatim\*?|lstlisting|minted)\}")
    for name, text in files.items():
        lines: list[str] = []
        in_verbatim = False
        for line in text.splitlines(keepends=True):
            if in_verbatim:
                lines.append(line)
                if verbatim_end.search(line):
                    in_verbatim = False
                continue
            if verbatim_begin.search(line):
                lines.append(line)
                in_verbatim = verbatim_end.search(line) is None
                continue
            comment_at = _find_comment_start(line)
            if comment_at is None:
                lines.append(line)
                continue
            newline = "\n" if line.endswith("\n") else ""
            comment_end = len(line) - len(newline)
            marker = f"%__ALINEA_COMMENT_{counter}__"
            comments[marker] = line[comment_at:comment_end]
            counter += 1
            lines.append(line[:comment_at] + marker + newline)
        protected[name] = "".join(lines)
    return protected, comments


def _restore_latex_comments(text: str, comments: dict[str, str]) -> str:
    for marker, original in comments.items():
        text = text.replace(marker, original)
    return text


def _rewrite_minted_frozencache_package(tex: str) -> str:
    """TeXLive2026 は minted v3.8.0 を同梱し、v2 専用の frozencache/finalizecache
    キャッシュ運用(\\pygtex / default.pygstyle を同梱する arXiv 標準構成)を解釈できず
    "Missing definition for highlighting style" で確定的にコンパイル失敗する。
    TeXLive2026 に同梱される legacy 互換パッケージ minted2(v2 と同じ環境名を提供)へ
    書き換えることで、同梱キャッシュを使う論文を確定的にコンパイルできるようにする。
    frozencache/finalizecache を使わない通常の \\usepackage{minted} は、v3 + 制限付き
    shell-escape の latexminted 経路が正しいため書き換えない。コメント行(% で始まる、
    または行中の実コメント以降)は対象外とし、_find_comment_start と同じ判定基準を使う。"""

    def rewrite_line(line: str) -> str:
        comment_at = _find_comment_start(line)
        pieces: list[str] = []
        pos = 0
        for match in _MINTED_USEPACKAGE_RE.finditer(line):
            if comment_at is not None and match.start() >= comment_at:
                break
            options = [opt.strip() for opt in match.group("options").split(",")]
            if "frozencache" not in options and "finalizecache" not in options:
                continue
            pieces.append(line[pos : match.start()])
            pieces.append(re.sub(r"\{\s*minted\s*\}", "{minted2}", match.group(0)))
            pos = match.end()
        pieces.append(line[pos:])
        return "".join(pieces)

    return "".join(rewrite_line(line) for line in tex.splitlines(keepends=True))


def _expand_project_includes(
    text: str,
    files: dict[str, str],
    visited: set[str],
    *,
    depth: int = 0,
) -> str:
    """Expand source files for one-pass translation while retaining include page breaks."""

    if depth > 20:
        return text

    def replace(match: re.Match[str]) -> str:
        name = (match.group("braced_name") or match.group("bare_name") or "").strip()
        candidates = [name] if name.endswith(".tex") else [name, f"{name}.tex"]
        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate in files and candidate not in visited
            ),
            None,
        )
        if selected is None:
            return match.group(0)
        visited.add(selected)
        expanded = _expand_project_includes(files[selected], files, visited, depth=depth + 1)
        if match.group("command") == "include":
            return "\n\\clearpage\n" + expanded + "\n\\clearpage\n"
        return expanded

    return _INPUT_CMD_RE.sub(replace, text)


def _resolve_pdf_bibliography(text: str, files: dict[str, str]) -> str:
    # A submitted .bbl already contains the author's chosen bibliography style and
    # is safe to inline.  With .bib-only projects, keep the original commands so
    # latexmk runs BibTeX/Biber instead of using the parser's simplified fallback.
    if any(name.lower().endswith(".bbl") for name in files):
        return _resolve_bibliography(text, files)
    return text


def render_translated_latex_source(
    archive: LatexArchive,
    content: DocumentContent,
    units: dict[str, TranslationUnit],
    *,
    abstract_ja: str | None = None,
) -> RenderedLatexSource:
    """Return translated TeX while retaining the source project and page style."""

    main_name, _parsed_main_tex = select_main_tex(archive.text_files)
    raw_files = archive.raw_text_files
    protected_files, comments = _protect_latex_comments(raw_files)
    main_tex = protected_files.get(main_name, archive.text_files[main_name])
    expanded = _expand_project_includes(main_tex, protected_files, {main_name})
    expanded = _resolve_pdf_bibliography(expanded, protected_files)
    expanded = _replace_abstract_command(expanded, abstract_ja)
    cursor = _TranslationCursor(
        content,
        units,
        zero_argument_commands=_zero_argument_command_bodies(expanded),
        is_fairmeta=bool(re.search(r"\\documentclass(?:\s*\[[^\]]*\])?\s*\{fairmeta\}", expanded)),
    )
    document_match = re.search(r"\\begin\{document\}", expanded)
    if document_match is None:
        raise LatexPdfBuildError("invalid_latex", "main TeX has no \\begin{document}")
    try:
        document_inner, document_end = _read_environment(expanded, document_match.end(), "document")
    except LatexParseError as exc:
        raise LatexPdfBuildError("invalid_latex", str(exc)) from exc
    closing_document = r"\end{document}"
    inner_end = document_end - len(closing_document)
    translated = (
        expanded[: document_match.end()]
        + _transform_latex_text(document_inner, cursor, abstract_ja)
        + expanded[inner_end:]
    )
    translated = _inject_luatex_compat(translated)
    translated = _inject_japanese_preamble(translated)
    translated = _restore_latex_comments(translated, comments)
    support_text = dict(raw_files)
    support_text.pop(main_name, None)
    return RenderedLatexSource(
        main_tex_name=main_name,
        main_tex=translated,
        support_text_files=support_text,
        binary_files=archive.binary_files,
        replacements=cursor.replacements,
        replaced_block_ids=frozenset(cursor.replaced_block_ids),
        warnings=cursor.warnings,
    )


def _safe_write_path(root: Path, name: str) -> Path | None:
    clean = posixpath.normpath(name.replace("\\", "/")).removeprefix("./")
    if clean in {"", "."} or clean == ".." or clean.startswith("../"):
        return None
    path = root / clean
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _write_rendered_source(
    root: Path, rendered: RenderedLatexSource | StructuredLatexSource
) -> None:
    main = _safe_write_path(root, rendered.main_tex_name)
    if main is None:
        raise LatexPdfBuildError("invalid_latex", "unsafe main TeX path")
    main.parent.mkdir(parents=True, exist_ok=True)
    # TeXLive2026=minted v3 は v2 frozencache キャッシュを読まないため、arXiv 標準の
    # 同梱キャッシュ論文は legacy の minted2 で確定的にコンパイルする。プリアンブルは
    # メイン .tex に限らず \input/\include で分割された support .tex にも書かれ得るため、
    # コンパイル workdir へ書き出す .tex 全てに同じ書き換えを適用する。
    main.write_text(_rewrite_minted_frozencache_package(rendered.main_tex), encoding="utf-8")
    for name, text in rendered.support_text_files.items():
        path = _safe_write_path(root, name)
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.lower().endswith(".tex"):
            text = _rewrite_minted_frozencache_package(text)
        path.write_text(text, encoding="utf-8")
    for name, data in rendered.binary_files.items():
        path = _safe_write_path(root, name)
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _find_overfull_boxes(log_text: str) -> list[str]:
    findings: list[str] = []
    for line in log_text.splitlines():
        m = _OVERFULL_RE.search(line)
        if m and float(m.group("pt")) > 1.0:
            findings.append(line.strip())
    return findings


def _compile_with_docker(workdir: Path, main_tex_name: str, *, image: str, timeout_s: int) -> bytes:
    output_pdf = workdir / Path(main_tex_name).with_suffix(".pdf")
    docker_command = (
        ["bash", str(_REPO_DOCKER_WRAPPER)] if _REPO_DOCKER_WRAPPER.is_file() else ["docker"]
    )
    cmd = [
        *docker_command,
        "run",
        "--rm",
        "--pull",
        "never",
        "--network",
        "none",
        "-v",
        f"{workdir}:/work",
        "-w",
        "/work",
        image,
        "latexmk",
        "-lualatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-file-line-error",
        main_tex_name,
    ]
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise LatexPdfBuildError("docker_unavailable", "docker command is not available") from exc
    except subprocess.TimeoutExpired as exc:
        raise LatexPdfBuildError("timeout", "LaTeX PDF build timed out") from exc

    log_path = workdir / Path(main_tex_name).with_suffix(".log")
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    if result.returncode != 0 or not output_pdf.exists():
        detail = {
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
            "log": log_text[-4000:],
        }
        raise LatexPdfBuildError("compile_failed", "LaTeX PDF build failed", detail=detail)
    # Some arXiv sources already contain long display math that reports Overfull boxes
    # while still rendering inside the PDF page. Validate the compiled PDF geometry below.
    return output_pdf.read_bytes()


def _rect_exceeds_page(rect: fitz.Rect, page_rect: fitz.Rect) -> bool:
    tolerance = _PDF_BOUNDS_TOLERANCE_PT
    return (
        float(rect.x0) < float(page_rect.x0) - tolerance
        or float(rect.y0) < float(page_rect.y0) - tolerance
        or float(rect.x1) > float(page_rect.x1) + tolerance
        or float(rect.y1) > float(page_rect.y1) + tolerance
    )


def _find_pdf_page_bound_violations(doc: fitz.Document) -> list[str]:
    violations: list[str] = []
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        page_rect = page.rect
        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            bbox = block.get("bbox")
            if not bbox:
                continue
            rect = fitz.Rect(bbox)
            if not rect.is_empty and _rect_exceeds_page(rect, page_rect):
                kind = "image" if block.get("type") == 1 else "text"
                violations.append(
                    f"page={page_index + 1} kind={kind} "
                    f"bbox=({rect.x0:.2f},{rect.y0:.2f},{rect.x1:.2f},{rect.y1:.2f}) "
                    f"page=({page_rect.x0:.2f},{page_rect.y0:.2f},"
                    f"{page_rect.x1:.2f},{page_rect.y1:.2f})"
                )
        for link in page.get_links():
            link_rect = link.get("from")
            if isinstance(link_rect, fitz.Rect) and _rect_exceeds_page(link_rect, page_rect):
                violations.append(
                    f"page={page_index + 1} kind=link "
                    f"bbox=({link_rect.x0:.2f},{link_rect.y0:.2f},"
                    f"{link_rect.x1:.2f},{link_rect.y1:.2f})"
                )
    return violations


def _block_signature(content: DocumentContent) -> list[tuple[str, str]]:
    """ID carryover を許しつつ、位置置換に必要な型と可視内容の一致を検証する。"""

    return [
        (
            block.type,
            hashlib.sha256(block_to_plain(block).encode("utf-8")).hexdigest()[:16],
        )
        for _section, block in content.iter_blocks()
    ]


def _normalize_unresolved_refs_for_revision_match(parsed: Any) -> None:
    """Apply the ingest-time unresolved-reference normalization before comparison."""

    labels = {block.label for block in parsed.blocks if block.label}
    element_kinds = {"equation", "figure", "table", "theorem", "algorithm"}

    def normalize(inlines: list[Any]) -> None:
        for inline in inlines:
            if (
                inline.t == "ref"
                and inline.kind in element_kinds
                and (inline.ref or "") not in labels
            ):
                inline.t = "text"
                inline.v = inline.v or ""
            elif inline.t == "emphasis" and getattr(inline, "children", None):
                normalize(inline.children)

    for block in parsed.blocks:
        normalize(block.inlines)
        normalize(block.caption)


def _validate_source_revision_match(archive: LatexArchive, content: DocumentContent) -> None:
    """Refuse positional replacement when the source and stored IR diverge."""

    main_name, _main_tex = select_main_tex(archive.text_files)
    parsed_document = parse_latex_source(main_name, archive.text_files)
    _normalize_unresolved_refs_for_revision_match(parsed_document)
    parsed = parsed_document.to_document_content()
    source_signature = _block_signature(parsed)
    revision_signature = _block_signature(content)
    if source_signature == revision_signature:
        return
    mismatch_at = next(
        (
            index
            for index, (source, revision) in enumerate(
                zip(source_signature, revision_signature, strict=False)
            )
            if source != revision
        ),
        min(len(source_signature), len(revision_signature)),
    )
    raise LatexPdfBuildError(
        "source_revision_mismatch",
        "LaTeX source does not match the structured revision",
        detail={
            "mismatch_at": mismatch_at,
            "source_blocks": len(source_signature),
            "revision_blocks": len(revision_signature),
            "source": source_signature[mismatch_at : mismatch_at + 3],
            "revision": revision_signature[mismatch_at : mismatch_at + 3],
        },
    )


# LaTeX 原文への位置置換(source レンダラー)は、\paragraph の再定義でパーサー側
# だけに段落境界が増える、複数画像 figure 環境の内部展開など、正規化のわずかな
# 不一致でごく少数のブロックだけがどうしても原文へ書き戻せないことがある。
# こうした僅少な欠落を理由に「原文レイアウトを維持した日本語PDF」を丸ごと
# 汎用レイアウト(structured)へ後退させるのは、体験としては明確な劣化になる
# (レイアウト維持+数ブロックだけ英語のまま残る > レイアウトを完全に放棄する)。
# 90% 以上のブロックが実際に原文へ書き戻せていれば、原文レイアウトを維持し
# 残りは原文(英語)のまま表示する。閾値未満は正規化の不一致では説明できない
# 本当のマッピング破綻とみなし、従来通り例外を投げて汎用レイアウトへ後退する。
_SOURCE_RENDER_MIN_COVERAGE = 1.0


def _validate_render_coverage(
    rendered: RenderedLatexSource,
    content: DocumentContent,
    units: dict[str, TranslationUnit],
) -> None:
    expected = {
        block.id
        for _section, block in content.iter_blocks()
        if (unit := units.get(block.id)) is not None and _unit_is_displayable_for_block(unit, block)
    }
    missing = sorted(expected - set(rendered.replaced_block_ids))
    if not missing:
        return
    by_id = {block.id: block.type for _section, block in content.iter_blocks()}
    replaced = len(expected) - len(missing)
    coverage = replaced / len(expected) if expected else 1.0
    if coverage < _SOURCE_RENDER_MIN_COVERAGE:
        raise LatexPdfBuildError(
            "translation_mapping_incomplete",
            "not every translated block could be mapped back to the LaTeX source",
            detail={
                "expected": len(expected),
                "replaced": replaced,
                "missing": [
                    f"{block_id}:{by_id.get(block_id, 'unknown')}" for block_id in missing[:20]
                ],
                "warnings": rendered.warnings[:10],
            },
        )


def _validate_render_manifest(manifest: PdfRenderManifest) -> None:
    """構造化レンダラーが全翻訳対象を日本語で描画したことを保証する。"""
    missing = sorted(manifest.expected_block_ids - manifest.translated_block_ids)
    fallback = sorted(manifest.source_fallback_block_ids)
    if missing or fallback:
        raise LatexPdfBuildError(
            "translated_pdf_incomplete",
            "not every translatable block was rendered in Japanese",
            detail={"missing": missing, "fallback": fallback},
        )


def _validate_translated_pdf(pdf_bytes: bytes) -> None:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise LatexPdfBuildError("invalid_pdf", "compiled PDF cannot be opened") from exc
    try:
        if doc.page_count < 1:
            raise LatexPdfBuildError("invalid_pdf", "compiled PDF has no pages")
        visible_text = "\n".join(
            doc.load_page(index).get_text("text") for index in range(doc.page_count)
        )
        if not _JAPANESE_RE.search(visible_text):
            raise LatexPdfBuildError("missing_japanese_text", "compiled PDF has no Japanese text")
        visible_control = _VISIBLE_TEX_CONTROL_RE.search(visible_text)
        if visible_control is not None:
            raise LatexPdfBuildError(
                "visible_latex",
                "compiled PDF exposes a raw TeX control sequence",
                detail={"command": visible_control.group(0)},
            )
        bound_violations = _find_pdf_page_bound_violations(doc)
        if bound_violations:
            raise LatexPdfBuildError(
                "page_bounds",
                "compiled PDF has content outside page bounds",
                detail={"violations": bound_violations[:20]},
            )
    finally:
        doc.close()


def _translation_units_digest(units: dict[str, TranslationUnit]) -> str:
    """Hash every PDF-relevant translation field for cache invalidation."""

    payload = [
        {
            "block_id": block_id,
            "source_hash": unit.source_hash,
            "content_ja": unit.content_ja,
            "text_ja": unit.text_ja,
            "quality_flags": sorted(unit.quality_flags or []),
        }
        for block_id, unit in sorted(units.items())
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _compile_rendered_source(
    rendered: RenderedLatexSource | StructuredLatexSource, *, image: str, timeout_s: int
) -> bytes:
    def _run() -> bytes:
        with tempfile.TemporaryDirectory(prefix="alinea-latex-") as tmp:
            root = Path(tmp)
            _write_rendered_source(root, rendered)
            return _compile_with_docker(
                root, rendered.main_tex_name, image=image, timeout_s=timeout_s
            )

    return await asyncio.to_thread(_run)


async def _find_asset(
    session: AsyncSession,
    *,
    paper_id: str,
    source_version: str,
    kind: str,
    storage_key: str | None = None,
) -> SourceAsset | None:
    conditions = [
        SourceAsset.paper_id == paper_id,
        SourceAsset.source_version == source_version,
        SourceAsset.kind == kind,
    ]
    if storage_key is not None:
        conditions.append(SourceAsset.storage_key == storage_key)
    return (
        (
            await session.execute(
                select(SourceAsset)
                .where(*conditions)
                .order_by(SourceAsset.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _record_pdf_asset(
    session: AsyncSession,
    *,
    paper_id: str,
    source_version: str,
    kind: str,
    storage_key: str,
    source_url: str,
    data: bytes,
) -> None:
    existing = await _find_asset(
        session,
        paper_id=paper_id,
        source_version=source_version,
        kind=kind,
        storage_key=storage_key,
    )
    if existing is not None:
        existing.storage_key = storage_key
        existing.byte_size = len(data)
        existing.sha256 = hashlib.sha256(data).hexdigest()
        existing.content_type = "application/pdf"
        existing.source_url = source_url
        return
    session.add(
        SourceAsset(
            paper_id=paper_id,
            kind=kind,
            source_url=source_url,
            source_version=source_version,
            storage_key=storage_key,
            content_type="application/pdf",
            byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
    )


async def _find_latex_asset(
    session: AsyncSession, *, paper_id: str, source_version: str
) -> SourceAsset | None:
    return (
        (
            await session.execute(
                select(SourceAsset)
                .where(
                    SourceAsset.paper_id == paper_id,
                    SourceAsset.source_version == source_version,
                    SourceAsset.kind.in_(_LATEX_SOURCE_KINDS),
                )
                .order_by(SourceAsset.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


def _is_missing_s3_object(exc: ClientError) -> bool:
    error = exc.response.get("Error", {})
    return str(error.get("Code") or "") in {"NoSuchKey", "NoSuchBucket", "404", "NotFound"}


def _source_render_manifest(
    rendered: RenderedLatexSource,
    content: DocumentContent,
    units: dict[str, TranslationUnit],
) -> PdfRenderManifest:
    expected = {
        block.id
        for _section, block in content.iter_blocks()
        if (unit := units.get(block.id)) is not None and _unit_is_displayable_for_block(unit, block)
    }
    translated = expected & set(rendered.replaced_block_ids)
    return PdfRenderManifest(
        expected_block_ids=frozenset(expected),
        translated_block_ids=frozenset(translated),
        source_fallback_block_ids=frozenset(expected - translated),
    )


def _manifest_digest(manifest: PdfRenderManifest) -> str:
    payload = {
        "expected": sorted(manifest.expected_block_ids),
        "translated": sorted(manifest.translated_block_ids),
        "fallback": sorted(manifest.source_fallback_block_ids),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _structured_replacements(
    content: DocumentContent, manifest: PdfRenderManifest
) -> dict[str, int]:
    by_id = {block.id: block.type for _section, block in content.iter_blocks()}
    return dict(Counter(by_id[block_id] for block_id in manifest.translated_block_ids))


async def _load_structured_binary_assets(
    storage: S3Storage,
    content: DocumentContent,
    units: dict[str, TranslationUnit],
) -> tuple[dict[str, bytes], list[str]]:
    assets: dict[str, bytes] = {}
    warnings: list[str] = []
    keys = {
        block.asset_key
        for _section, block in content.iter_blocks()
        if block.type == "figure"
        and block.asset_key
        and (unit := units.get(block.id)) is not None
        and _unit_is_displayable_for_block(unit, block)
    }
    for key in sorted(keys):
        try:
            assets[key] = await storage.get(storage.assets_bucket, key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code") or "storage_error")
            warnings.append(f"図アセットを日本語PDFへ埋め込めませんでした: {key} ({code})")
    return assets, warnings


async def _render_structured_pdf(
    storage: S3Storage,
    settings: CoreSettings,
    *,
    content: DocumentContent,
    units: dict[str, TranslationUnit],
    abstract_ja: str | None,
) -> tuple[StructuredLatexSource, bytes, list[str]]:
    binary_assets, asset_warnings = await _load_structured_binary_assets(storage, content, units)
    rendered = render_structured_japanese_source(
        content,
        units,
        abstract_ja=abstract_ja,
        binary_assets=binary_assets,
    )
    _validate_render_manifest(rendered.manifest)
    image = settings.alinea_texlive_image or DEFAULT_TEXLIVE_IMAGE
    translated_pdf = await _compile_rendered_source(
        rendered,
        image=image,
        timeout_s=settings.alinea_latex_build_timeout_s,
    )
    _validate_translated_pdf(translated_pdf)
    return rendered, translated_pdf, asset_warnings


async def build_translation_pdfs_if_ready(
    session: AsyncSession,
    storage: S3Storage,
    settings: CoreSettings,
    *,
    set_id: str,
    force: bool = False,
) -> LatexPdfBuildOutcome:
    """Build and persist a validated Japanese PDF for every supported source format."""

    tset = await session.get(TranslationSet, set_id)
    if tset is None:
        return LatexPdfBuildOutcome(False, skipped_reason="translation_set_missing")
    if tset.status != "complete":
        return LatexPdfBuildOutcome(False, skipped_reason="translation_set_not_complete")
    if tset.scope not in {"shared", "personal"}:
        return LatexPdfBuildOutcome(False, skipped_reason="unsupported_scope")
    revision = await session.get(DocumentRevision, str(tset.revision_id))
    if revision is None:
        return LatexPdfBuildOutcome(False, skipped_reason="revision_missing")
    paper = await session.get(Paper, str(revision.paper_id))
    if paper is None:
        return LatexPdfBuildOutcome(False, skipped_reason="paper_missing")

    paper_id = str(paper.id)
    source_version = revision.source_version
    style = tset.style
    translated_key = StorageKeys.translated_pdf(
        paper_id,
        source_version,
        style,
        translation_set_id=(set_id if tset.scope == "personal" else None),
    )
    stats_record_key = style if tset.scope == "shared" else f"{style}:{set_id}"

    existing_translated = await _find_asset(
        session,
        paper_id=paper_id,
        source_version=source_version,
        kind="translated_pdf",
        storage_key=translated_key,
    )
    units = await resolve_translation_set_units(session, tset)
    translation_digest = _translation_units_digest(units)
    existing_record = ((revision.stats or {}).get("translated_pdf") or {}).get(
        stats_record_key
    ) or {}
    if (
        not force
        and
        existing_translated is not None
        and existing_record.get("build_version") == PDF_BUILD_VERSION
        and existing_record.get("translation_set_id") == set_id
        and existing_record.get("storage_key") == translated_key
        and existing_record.get("translation_digest") == translation_digest
    ):
        return LatexPdfBuildOutcome(
            False,
            translated_key=existing_translated.storage_key,
            skipped_reason="already_built",
            renderer=str(existing_record.get("renderer") or "source"),
            fallback_reason=existing_record.get("fallback_reason"),
        )

    content = DocumentContent.model_validate(revision.content)
    renderer = "structured"
    fallback_reason: str | None = "not_latex"
    warnings: list[str] = []
    manifest: PdfRenderManifest
    replacements: dict[str, int]
    translated_pdf: bytes

    # 参考文献セクションは常に翻訳対象外、付録も設定次第で対象外になるため
    # (compute_translation_scope 参照)、実運用では units が全翻訳対象ブロックを
    # 覆っていない論文が大半である。それを理由に汎用レイアウトへ後退させると、
    # 原文レイアウト維持という機能の意味が失われる。ここでは訳がある
    # ブロックだけを置換し、訳のないブロックは原文(英語)のまま残す。
    if revision.source_format == "latex":
        fallback_reason = None
        try:
            latex_asset = await _find_latex_asset(
                session, paper_id=paper_id, source_version=source_version
            )
            if latex_asset is None:
                raise LatexPdfBuildError("latex_asset_missing", "LaTeX source asset is missing")
            try:
                latex_bytes = await storage.get(storage.sources_bucket, latex_asset.storage_key)
            except ClientError as exc:
                if _is_missing_s3_object(exc):
                    raise LatexPdfBuildError(
                        "latex_asset_object_missing", "LaTeX source object is missing"
                    ) from exc
                raise
            archive = extract_latex_archive(latex_bytes)
            source_rendered = render_translated_latex_source(
                archive,
                content,
                units,
                abstract_ja=paper.abstract_ja,
            )
            _validate_source_revision_match(archive, content)
            _validate_render_coverage(source_rendered, content, units)
            image = settings.alinea_texlive_image or DEFAULT_TEXLIVE_IMAGE
            translated_pdf = await _compile_rendered_source(
                source_rendered,
                image=image,
                timeout_s=settings.alinea_latex_build_timeout_s,
            )
            _validate_translated_pdf(translated_pdf)
        except LatexPdfBuildError as exc:
            if exc.kind not in {
                "compile_failed",
                "invalid_latex",
                "invalid_pdf",
                "latex_asset_missing",
                "latex_asset_object_missing",
                "missing_japanese_text",
                "page_bounds",
                "source_revision_mismatch",
                "translation_mapping_incomplete",
                "visible_latex",
            }:
                raise
            fallback_reason = exc.kind
        else:
            renderer = "source"
            warnings = list(source_rendered.warnings)
            manifest = _source_render_manifest(source_rendered, content, units)
            replacements = dict(source_rendered.replacements)

    if renderer == "structured":
        structured_rendered, translated_pdf, asset_warnings = await _render_structured_pdf(
            storage,
            settings,
            content=content,
            units=units,
            abstract_ja=paper.abstract_ja,
        )
        warnings = [*structured_rendered.warnings, *asset_warnings]
        manifest = structured_rendered.manifest
        replacements = _structured_replacements(content, manifest)

    await storage.put(
        storage.sources_bucket,
        translated_key,
        translated_pdf,
        content_type="application/pdf",
        metadata={
            "build": PDF_BUILD_VERSION,
            "translation_set_id": set_id,
            "translation_digest": translation_digest,
            "renderer": renderer,
            "manifest_digest": _manifest_digest(manifest),
        },
    )
    await _record_pdf_asset(
        session,
        paper_id=paper_id,
        source_version=source_version,
        kind="translated_pdf",
        storage_key=translated_key,
        source_url=f"translation-set:{set_id}",
        data=translated_pdf,
    )

    stats = dict(revision.stats or {})
    pdf_stats = dict(stats.get("translated_pdf") or {})
    pdf_stats[stats_record_key] = {
        "build_version": PDF_BUILD_VERSION,
        "translation_set_id": set_id,
        "translation_digest": translation_digest,
        "storage_key": translated_key,
        "renderer": renderer,
        "fallback_reason": fallback_reason,
        "manifest_digest": _manifest_digest(manifest),
        "manifest": {
            "expected": len(manifest.expected_block_ids),
            "translated": len(manifest.translated_block_ids),
            "source_fallback": len(manifest.source_fallback_block_ids),
        },
        "replacements": replacements,
        "built_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    stats["translated_pdf"] = pdf_stats
    failures = dict(stats.get("translated_pdf_failures") or {})
    failures.pop(stats_record_key, None)
    if failures:
        stats["translated_pdf_failures"] = failures
    else:
        stats.pop("translated_pdf_failures", None)
    revision.stats = stats
    await session.commit()
    return LatexPdfBuildOutcome(
        True,
        translated_key=translated_key,
        warnings=warnings,
        renderer=renderer,
        fallback_reason=fallback_reason,
    )


async def build_latex_translation_pdfs_if_ready(
    session: AsyncSession,
    storage: S3Storage,
    settings: CoreSettings,
    *,
    set_id: str,
) -> LatexPdfBuildOutcome:
    """Compatibility alias for callers deployed before the generic builder rename."""
    return await build_translation_pdfs_if_ready(session, storage, settings, set_id=set_id)
