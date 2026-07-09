"""arXiv e-print(LaTeX ソース)パーサ(plans/05 §5・M2-01。docs/01 §4・docs/02 §3)。

**決定(本タスクの deviations)**: plans/05 §5 は pandoc(JSON AST)+自前後処理を正としているが、
実行環境に pandoc バイナリが存在せず `uv sync`/新規依存追加が禁止されているため、
stdlib(`tarfile`/`gzip`)+自前の LaTeX トークナイザで docs/01 §4 の IR へ変換する
(担当タスク指示に基づく明示的な逸脱。deviations に記載)。

入力は `sources/{paper_id}/{sv}/latex.tar.gz`(複数ファイル tar.gz)または単一ファイル
gzip(1 ファイル投稿の arXiv 慣習)。メインファイルは `\\documentclass` + `\\begin{document}`
を持つ .tex を `ms.tex` → `main.tex` → 最大サイズの順で選ぶ(plans/05 §5)。

`\\input`/`\\include` を再帰展開し、`\\bibliography{...}` は同梱 `.bbl` があれば埋め込む。
出力は `alinea_core.parsing.html_parser.ParsedDocument`(既存 IR を再利用。重複定義しない)
で `quality_level="A"`, `source_format="latex"`, `parser_version="latex-1.0.0"`。

相互参照(`\\ref`/`\\eqref`)は 2 パスで解決する: 1 パス目で全ブロックを構築しつつ `\\label` を
label→kind map に記録し、2 パス目で保留中の `ref` インラインへ `kind` を確定する(HTML パーサの
DOM id パターン方式に対する LaTeX 版の等価物。ラベル名は自由文字列でパターン推定できないため)。
未解決は `section` へ縮退+warn(plans/05 §4.3 の HTML パーサと同方針)。
"""

from __future__ import annotations

import gzip
import io
import re
import tarfile
from typing import Any

from alinea_core.document.blocks import Block, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.parsing.block_ids import assign_block_ids
from alinea_core.parsing.html_parser import ParsedDocument

PARSER_VERSION = "latex-1.0.0"

_WS = re.compile(r"\s+")

__all__ = [
    "PARSER_VERSION",
    "LatexArchive",
    "LatexParseError",
    "ParsedDocument",
    "extract_latex_archive",
    "parse_arxiv_latex",
    "parse_latex_source",
    "select_main_tex",
]


class LatexParseError(Exception):
    """LaTeX ソースの取得・展開・解析に失敗(`kind` で分類。§2.4 の FetchError と同方針)。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class LatexArchive:
    """e-print 展開結果。テキスト(.tex/.bbl/.cls/.sty)とバイナリ(図等)を分けて保持する。"""

    __slots__ = ("binary_files", "text_files")

    def __init__(self, text_files: dict[str, str], binary_files: dict[str, bytes]) -> None:
        self.text_files = text_files
        self.binary_files = binary_files


def _collapse(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip()


# ============================================================================
# アーカイブ展開(tar.gz / 単一ファイル gzip / 無圧縮)
# ============================================================================

_TEXT_EXTS = (".tex", ".bbl", ".bib", ".cls", ".sty")


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_latex_archive(archive: bytes) -> LatexArchive:
    """e-print バイト列を展開する(tar.gz 優先 → 単一ファイル gzip → 無圧縮 .tex)。"""
    if not archive:
        raise LatexParseError("empty_archive", "e-print archive is empty")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
            text_files: dict[str, str] = {}
            binary_files: dict[str, bytes] = {}
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                name = member.name.removeprefix("./")
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                data = fh.read()
                if name.lower().endswith(_TEXT_EXTS):
                    text_files[name] = _strip_comments(_decode(data))
                else:
                    binary_files[name] = data
            if text_files:
                return LatexArchive(text_files, binary_files)
    except tarfile.ReadError:
        pass
    try:
        raw = gzip.decompress(archive)
    except OSError:
        raw = archive
    text = _strip_comments(_decode(raw))
    if "\\documentclass" not in text:
        raise LatexParseError("no_main_tex", "no .tex content found in e-print archive")
    return LatexArchive({"main.tex": text}, {})


def select_main_tex(text_files: dict[str, str]) -> tuple[str, str]:
    """メイン .tex の特定(plans/05 §5): `ms.tex` → `main.tex` → 最大サイズ。"""
    candidates = [
        name
        for name, content in text_files.items()
        if name.lower().endswith(".tex")
        and "\\documentclass" in content
        and "\\begin{document}" in content
    ]
    if not candidates:
        raise LatexParseError(
            "no_main_tex", "no file with \\documentclass + \\begin{document} found"
        )
    for preferred in ("ms.tex", "main.tex"):
        if preferred in candidates:
            return preferred, text_files[preferred]
    best = max(candidates, key=lambda n: len(text_files[n]))
    return best, text_files[best]


# ============================================================================
# コメント除去(verbatim/lstlisting 内は保護)
# ============================================================================

_LINE_COMMENT_RE = re.compile(r"(?<!\\)%.*")
_VERB_BEGIN_RE = re.compile(r"\\begin\{(verbatim\*?|lstlisting|minted)\}")
_VERB_END_TMPL = r"\\end\{{{}}}"


def _strip_comments(text: str) -> str:
    out_lines: list[str] = []
    in_verbatim = False
    verb_name = ""
    for line in text.split("\n"):
        if in_verbatim:
            out_lines.append(line)
            if re.search(_VERB_END_TMPL.format(re.escape(verb_name)), line):
                in_verbatim = False
            continue
        out_lines.append(_LINE_COMMENT_RE.sub("", line))
        m = _VERB_BEGIN_RE.search(line)
        if m:
            in_verbatim = True
            verb_name = m.group(1)
    return "\n".join(out_lines)


# ============================================================================
# \input/\include 展開・\bibliography{} の .bbl 埋め込み
# ============================================================================

_INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")
_BIBLIOGRAPHY_CMD_RE = re.compile(r"\\bibliography\{([^}]*)\}")
_PRINT_BIBLIOGRAPHY_RE = re.compile(r"\\printbibliography\b(?:\s*\[[^\]]*\])?")
_BIB_RESOURCE_RE = re.compile(r"\\(?:addbibresource|bibliography)\{([^}]*)\}")
_CITE_KEY_RE = re.compile(
    r"\\(?:cite|citet|citep|citeauthor|citeyear|citealt|citealp)\*?"
    r"(?:\s*\[[^\]]*\])*\s*\{([^}]*)\}"
)
_NOCITE_ALL_RE = re.compile(r"\\nocite\s*\{\s*\*\s*\}")
_BIB_ENTRY_START_RE = re.compile(
    r"@(?P<type>[A-Za-z]+)\s*(?P<open>[{(])\s*(?P<key>[^,\s]+)\s*,", re.IGNORECASE
)


def _expand_includes(text: str, files: dict[str, str], visited: set[str], depth: int = 0) -> str:
    if depth > 20:
        return text

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1).strip()
        candidates = [name] if name.endswith(".tex") else [name, f"{name}.tex"]
        for cand in candidates:
            if cand in files and cand not in visited:
                visited.add(cand)
                return _expand_includes(files[cand], files, visited, depth + 1)
        return ""  # 見つからない/循環 → 空文字(壊れた訳を見せないための安全側縮退。P3)

    return _INPUT_RE.sub(_sub, text)


def _resolve_bibliography(text: str, files: dict[str, str]) -> str:
    """`\\bibliography{}` を同梱 `.bbl` / `.bib` の内容で置換する。

    arXiv e-print は `.bbl` を含むことが多いが、`.bib` だけの投稿もある。`.bbl` が無い場合は
    cited key と `.bib` entry から最小限の `thebibliography` を合成し、既存の bibitem パーサへ流す。
    """
    if "\\begin{thebibliography}" in text:
        return text
    m = _BIBLIOGRAPHY_CMD_RE.search(text)
    m_print = _PRINT_BIBLIOGRAPHY_RE.search(text)
    if not m and not m_print:
        return text
    bbl_name = next((n for n in files if n.lower().endswith(".bbl")), None)
    replacement: str | None
    if bbl_name is not None:
        replacement = files[bbl_name]
    else:
        replacement = _build_thebibliography_from_bib(text, files)
    if not replacement:
        return text
    target = m or m_print
    if target is None:
        return text
    return text[: target.start()] + replacement + text[target.end() :]


def _bibliography_names(text: str) -> list[str]:
    names: list[str] = []
    for m in _BIB_RESOURCE_RE.finditer(text):
        names.extend(n.strip() for n in m.group(1).split(",") if n.strip())
    return names


def _matching_bib_files(names: list[str], files: dict[str, str]) -> list[str]:
    bib_files = [n for n in files if n.lower().endswith(".bib")]
    if not names:
        return bib_files
    wanted: set[str] = set()
    for name in names:
        normalized = name.strip().removeprefix("./")
        wanted.add(normalized)
        if not normalized.lower().endswith(".bib"):
            wanted.add(f"{normalized}.bib")
    out: list[str] = []
    for filename in bib_files:
        base = filename.rsplit("/", 1)[-1]
        if filename in wanted or base in wanted or any(filename.endswith(f"/{w}") for w in wanted):
            out.append(filename)
    return out or bib_files


def _cited_keys(text: str) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for m in _CITE_KEY_RE.finditer(text):
        for key in m.group(1).split(","):
            clean = key.strip()
            if clean and clean not in seen:
                keys.append(clean)
                seen.add(clean)
    return keys


def _read_bib_entry_body(text: str, start: int, opener: str) -> tuple[str, int] | None:
    closer = "}" if opener == "{" else ")"
    depth = 1
    quote = False
    escaped = False
    i = start
    while i < len(text):
        c = text[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if c == "\\":
            escaped = True
            i += 1
            continue
        if c == '"':
            quote = not quote
            i += 1
            continue
        if not quote:
            if c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    return text[start:i], i + 1
        i += 1
    return None


def _split_bib_top_level(text: str, sep: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote = False
    escaped = False
    for i, c in enumerate(text):
        if escaped:
            escaped = False
            continue
        if c == "\\":
            escaped = True
            continue
        if c == '"':
            quote = not quote
            continue
        if quote:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        elif c == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _balanced_outer(value: str, opener: str, closer: str) -> bool:
    if not (value.startswith(opener) and value.endswith(closer)):
        return False
    depth = 0
    quote = False
    escaped = False
    for i, c in enumerate(value):
        if escaped:
            escaped = False
            continue
        if c == "\\":
            escaped = True
            continue
        if c == '"' and opener != '"':
            quote = not quote
            continue
        if quote:
            continue
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0 and i != len(value) - 1:
                return False
    return depth == 0


def _clean_bib_value(raw: str) -> str:
    parts = _split_bib_top_level(raw.strip(), "#")
    joined = " ".join(p.strip() for p in parts if p.strip())
    changed = True
    while changed:
        changed = False
        s = joined.strip()
        if _balanced_outer(s, "{", "}"):
            joined = s[1:-1]
            changed = True
            continue
        if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
            joined = s[1:-1]
            changed = True
    joined = re.sub(r"\\[\"'`^~=.uvHtcbd]\s*\{?([A-Za-z])\}?", r"\1", joined)
    joined = joined.replace("\\&", "&").replace("\\_", "_")
    joined = joined.replace("{", "").replace("}", "")
    return _strip_markup(joined)


def _parse_bib_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for chunk in _split_bib_top_level(body, ","):
        if "=" not in chunk:
            continue
        name, raw_value = chunk.split("=", 1)
        clean_name = name.strip().lower()
        if not clean_name:
            continue
        value = _clean_bib_value(raw_value)
        if value:
            fields[clean_name] = value
    return fields


def _parse_bib_entries(text: str) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    pos = 0
    while True:
        m = _BIB_ENTRY_START_RE.search(text, pos)
        if m is None:
            break
        read = _read_bib_entry_body(text, m.end(), m.group("open"))
        if read is None:
            pos = m.end()
            continue
        body, pos = read
        typ = m.group("type").lower()
        if typ in {"comment", "preamble", "string"}:
            continue
        key = m.group("key").strip()
        if key:
            entries[key] = _parse_bib_fields(body)
            entries[key]["entry_type"] = typ
    return entries


def _sentence(text: str) -> str:
    s = text.strip()
    return s if not s or s.endswith((".", "?", "!")) else f"{s}."


def _bib_entry_to_bibitem(key: str, fields: dict[str, str]) -> str:
    authors_raw = fields.get("author") or fields.get("editor") or ""
    authors = [_collapse(a) for a in re.split(r"\s+and\s+", authors_raw) if _collapse(a)]
    authors_text = ", ".join(authors)
    title = fields.get("title") or ""
    venue = fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or ""
    year = fields.get("year") or fields.get("date", "")[:4]
    doi = fields.get("doi") or ""
    url = fields.get("url") or ""
    arxiv_id = fields.get("eprint") or ""
    archive = fields.get("archiveprefix") or fields.get("eprinttype") or ""

    parts: list[str] = []
    if authors_text:
        parts.append(_sentence(authors_text))
    if title:
        parts.append(_sentence(rf"\emph{{{title}}}"))
    if venue:
        parts.append(_sentence(venue))
    if arxiv_id and (archive.lower() == "arxiv" or re.match(r"\d{4}\.\d{4,5}", arxiv_id)):
        parts.append(_sentence(f"arXiv:{arxiv_id}"))
    if doi:
        parts.append(_sentence(rf"\url{{https://doi.org/{doi.removeprefix('https://doi.org/')}}}"))
    elif url:
        parts.append(_sentence(rf"\url{{{url}}}"))
    if year and not any(re.search(rf"\b{re.escape(year)}\b", part) for part in parts):
        parts.append(_sentence(year))
    raw = " ".join(parts).strip() or key
    return rf"\bibitem{{{key}}} {raw}"


def _build_thebibliography_from_bib(text: str, files: dict[str, str]) -> str | None:
    entries: dict[str, dict[str, str]] = {}
    for name in _matching_bib_files(_bibliography_names(text), files):
        entries.update(_parse_bib_entries(files[name]))
    if not entries:
        return None

    if _NOCITE_ALL_RE.search(text):
        ordered_keys = list(entries)
    else:
        cited = _cited_keys(text)
        ordered_keys = [key for key in cited if key in entries]
        if not ordered_keys and not cited:
            ordered_keys = list(entries)
    if not ordered_keys:
        return None

    items = [_bib_entry_to_bibitem(key, entries[key]) for key in ordered_keys]
    return (
        "\\begin{thebibliography}{"
        + str(len(items))
        + "}\n"
        + "\n".join(items)
        + "\n\\end{thebibliography}"
    )


def _extract_document_body(text: str) -> str:
    m = re.search(r"\\begin\{document\}", text)
    if not m:
        raise LatexParseError("no_main_tex", "no \\begin{document} found")
    inner, _end = _read_environment(text, m.end(), "document")
    return inner


_FRONTMATTER_CMDS = (
    "maketitle",
    "title",
    "author",
    "date",
    "thanks",
    "affil",
    "affiliation",
    "thispagestyle",
    "pagestyle",
    "tableofcontents",
    "and",
    "institute",
    "email",
    "IEEEauthorblockN",
    "IEEEauthorblockA",
)

_SETUP_CMDS = frozenset(
    {
        "addtolength",
        "bibliographystyle",
        "colorlet",
        "DeclareMathOperator",
        "DeclarePairedDelimiter",
        "DeclareRobustCommand",
        "DeclareTextFontCommand",
        "def",
        "definecolor",
        "graphicspath",
        "hypersetup",
        "newcommand",
        "newenvironment",
        "newlength",
        "newtheorem",
        "providecommand",
        "renewcommand",
        "renewenvironment",
        "setcounter",
        "setlength",
        "tikzset",
    }
)
_SETUP_CMD_RE = re.compile(
    r"\\("
    + "|".join(sorted((re.escape(cmd) for cmd in _SETUP_CMDS), key=len, reverse=True))
    + r")\*?(?![A-Za-z])"
)
_CONTROL_WORD_RE = re.compile(r"\\[A-Za-z]+\*?")


def _strip_frontmatter_commands(text: str) -> str:
    out = text
    changed = True
    while changed:
        changed = False
        for cmd in _FRONTMATTER_CMDS:
            m = re.search(rf"\\{cmd}\*?(?![A-Za-z])", out)
            if not m:
                continue
            end = m.end()
            if end < len(out) and out[end] == "[":
                j = out.find("]", end)
                if j != -1:
                    end = j + 1
            if end < len(out) and out[end] == "{":
                _, end = _read_braced(out, end)
            out = out[: m.start()] + out[end:]
            changed = True
    return out


def _consume_setup_command(text: str, start: int, cmd: str) -> int:
    """本文中に残ったマクロ定義・色定義など、表示しない setup command 全体を読む。"""
    i = start
    while i < len(text) and text[i].isspace():
        i += 1

    if cmd == "def":
        m = _CONTROL_WORD_RE.match(text, i)
        if m:
            i = m.end()
        while i < len(text) and text[i] != "{":
            i += 1
        if i < len(text) and text[i] == "{":
            try:
                _body, i = _read_braced(text, i)
            except LatexParseError:
                return i
        return i

    consumed_group = False
    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i < len(text) and text[i] == "[":
            end = _matching_square(text, i)
            if end is None:
                return i
            i = end + 1
            consumed_group = True
            continue
        if i < len(text) and text[i] == "{":
            try:
                _body, i = _read_braced(text, i)
            except LatexParseError:
                return i
            consumed_group = True
            continue
        break
    return i if consumed_group else start


def _strip_setup_commands(text: str) -> str:
    out: list[str] = []
    i = 0
    while True:
        m = _SETUP_CMD_RE.search(text, i)
        if m is None:
            out.append(text[i:])
            break
        out.append(text[i : m.start()])
        i = _consume_setup_command(text, m.end(), m.group(1))
    return "".join(out)


# ============================================================================
# 汎用ブレース/環境スキャナ
# ============================================================================


def _read_braced(text: str, open_pos: int) -> tuple[str, int]:
    """`{` の位置(``open_pos``)から対応する `}` までを読む(バックスラッシュエスケープ考慮)。"""
    if open_pos >= len(text) or text[open_pos] != "{":
        raise LatexParseError("unbalanced_braces", f"expected '{{' at {open_pos}")
    depth = 1
    i = open_pos + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_pos + 1 : i], i + 1
        i += 1
    raise LatexParseError("unbalanced_braces", "unbalanced braces in latex source")


def _matching_square(text: str, open_pos: int) -> int | None:
    depth = 1
    i = open_pos + 1
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            i += 2
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _read_optional_braced(text: str, pos: int) -> tuple[str | None, int]:
    """コマンド直後の空白と任意個の `[...]` を読み飛ばし、続く `{...}` があれば内容を返す。"""
    i = pos
    while i < len(text) and text[i].isspace():
        i += 1
    while i < len(text) and text[i] == "[":
        j = _matching_square(text, i)
        if j is None:
            return None, i
        i = j + 1
        while i < len(text) and text[i].isspace():
            i += 1
    if i < len(text) and text[i] == "{":
        content, end = _read_braced(text, i)
        return content, end
    return None, i


def _read_environment(text: str, start: int, name: str) -> tuple[str, int]:
    """`\\begin{name}` の直後(``start``)から対応する `\\end{name}` までを読む(同名の入れ子対応)。"""
    begin_pat = re.compile(rf"\\begin\{{{re.escape(name)}\}}")
    end_pat = re.compile(rf"\\end\{{{re.escape(name)}\}}")
    depth = 1
    i = start
    n = len(text)
    while i <= n:
        b = begin_pat.search(text, i)
        e = end_pat.search(text, i)
        if e is None:
            raise LatexParseError("unterminated_environment", f"unterminated environment: {name}")
        if b is not None and b.start() < e.start():
            depth += 1
            i = b.end()
            continue
        depth -= 1
        if depth == 0:
            return text[start : e.start()], e.end()
        i = e.end()
    raise LatexParseError("unterminated_environment", f"unterminated environment: {name}")


# 最上位走査の候補(文書順で最早マッチを選ぶ)。
_SECTION_RE = re.compile(r"\\(section|subsection|subsubsection)(\*)?\{")
_BEGIN_RE = re.compile(r"\\begin\{([a-zA-Z]+\*?)\}")
_APPENDIX_RE = re.compile(r"\\appendix\b")
_LABEL_AFTER_RE = re.compile(r"\s*\\label\{([^}]*)\}")

_LEVEL_OF = {"section": 1, "subsection": 2, "subsubsection": 3}


def _iter_top_level(text: str) -> list[tuple[Any, ...]]:
    """最上位ノード列(`section` / `appendix` / `env` / `text`)を文書順で返す。"""
    nodes: list[tuple[Any, ...]] = []
    i = 0
    n = len(text)
    while i < n:
        m_sec = _SECTION_RE.search(text, i)
        m_beg = _BEGIN_RE.search(text, i)
        m_app = _APPENDIX_RE.search(text, i)
        cands = [m for m in (m_sec, m_beg, m_app) if m is not None]
        if not cands:
            tail = text[i:]
            if tail.strip():
                nodes.append(("text", tail))
            break
        m = min(cands, key=lambda mm: mm.start())
        if m.start() > i:
            chunk = text[i : m.start()]
            if chunk.strip():
                nodes.append(("text", chunk))
        if m is m_app:
            nodes.append(("appendix",))
            i = m.end()
            continue
        if m is m_sec:
            level = _LEVEL_OF[m.group(1)]
            starred = m.group(2) == "*"
            title_raw, end = _read_braced(text, m.end() - 1)
            label: str | None = None
            lm = _LABEL_AFTER_RE.match(text, end)
            if lm:
                label = lm.group(1).strip()
                end = lm.end()
            nodes.append(("section", level, title_raw, label, starred))
            i = end
            continue
        name = m.group(1)
        inner, end = _read_environment(text, m.end(), name)
        nodes.append(("env", name, inner))
        i = end
    return nodes


# ============================================================================
# reference_entry 構造化 / 簡易マークアップ除去
# ============================================================================

_ARXIV_RE = re.compile(
    r"(?:arXiv:|arxiv\.org/abs/)\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.IGNORECASE
)
_YEAR_PAREN_RE = re.compile(r"\((19|20)\d{2}\)")
_YEAR_RE = re.compile(r"(19|20)\d{2}")
_EMPH_TITLE_RE = re.compile(r"\\(?:emph|textit)\{([^{}]+)\}")
_QUOTE_TITLE_RE = re.compile('[\u201c"\u2018]([^\u201d"\u2019]+)[\u201d"\u2019]')
_DOI_RE = re.compile(r"doi\.org/(\S+)", re.IGNORECASE)


def _structure_reference(raw: str) -> dict[str, str] | None:
    out: dict[str, str] = {}
    am = _ARXIV_RE.search(raw)
    if am:
        out["arxiv_id"] = am.group(1)
    ym = _YEAR_PAREN_RE.search(raw)
    if ym:
        out["year"] = ym.group()[1:-1]
    else:
        # 末尾に出版年が置かれる書式が多いため、複数マッチ時は最後の一致を採る
        # (DOI/arXiv ID に埋め込まれた年紀らしき数字列を誤って採らないため)。
        year_matches = list(_YEAR_RE.finditer(raw))
        if year_matches:
            out["year"] = year_matches[-1].group()
    tm = _EMPH_TITLE_RE.search(raw)
    if tm:
        out["title"] = _collapse(tm.group(1))
    else:
        qm = _QUOTE_TITLE_RE.search(raw)
        if qm:
            out["title"] = qm.group(1).strip()
        else:
            parts = re.split(r"\.\s+", raw)
            if len(parts) >= 2:
                out["title"] = parts[1].strip()
    dm = _DOI_RE.search(raw)
    if dm:
        out["doi"] = dm.group(1).rstrip(".,;}])\"'")
    return out or None


_CMD_WITH_ARG_RE = re.compile(r"\\(?:emph|textit|textbf|textsc|texttt|uline)\{([^{}]*)\}")
_BARE_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?")


def _strip_markup(text: str) -> str:
    """表示用の簡易マークアップ除去(`\\emph{X}` → `X` 等)。構造化失敗でも読める表示に。"""
    prev = None
    out = text
    while prev != out:
        prev = out
        out = _CMD_WITH_ARG_RE.sub(r"\1", out)
    out = out.replace("~", " ")
    out = out.replace("\\\\", " ").replace("\\ ", " ")
    out = _BARE_CMD_RE.sub(" ", out)
    return _collapse(out)


def _flatten_plain(inlines: list[Inline]) -> str:
    parts: list[str] = []
    for il in inlines:
        if il.t in ("text", "emphasis", "code_inline"):
            parts.append(il.v)
        elif il.t == "math_inline":
            parts.append(f"${il.v}$")
        elif il.t == "url":
            parts.append(il.v or il.href or "")
    return _collapse(" ".join(p for p in parts if p))


def _merge_text(inlines: list[Inline]) -> list[Inline]:
    out: list[Inline] = []
    for il in inlines:
        if il.t == "text" and out and out[-1].t == "text":
            out[-1] = Inline(t="text", v=_WS.sub(" ", out[-1].v + il.v))
        else:
            out.append(il)
    while out and out[0].t == "text" and out[0].v == " ":
        out.pop(0)
    while out and out[-1].t == "text" and out[-1].v == " ":
        out.pop()
    return out


def _append_text(out: list[Inline], raw: str) -> None:
    if raw.strip():
        out.append(Inline(t="text", v=_WS.sub(" ", raw)))
    elif raw:
        out.append(Inline(t="text", v=" "))


# 種別名 → 表示名(theorem 系。plans/05 §4.2 の「種別名+番号」を LaTeX でも保持)。
_THEOREM_ENVS = {
    "theorem": "Theorem",
    "lemma": "Lemma",
    "corollary": "Corollary",
    "proposition": "Proposition",
    "definition": "Definition",
    "remark": "Remark",
    "claim": "Claim",
    "example": "Example",
    "proof": "Proof",
}

_CITE_CMDS = {"cite", "citet", "citep", "citeauthor", "citeyear", "citealt", "citealp"}
_REF_CMDS = {"ref", "eqref", "autoref", "cref", "Cref", "nameref"}
_NO_OUTPUT_CMDS = {
    "noindent",
    "par",
    "newline",
    "clearpage",
    "newpage",
    "bigskip",
    "medskip",
    "smallskip",
    "vfill",
    "hfill",
    "centering",
    "label",
    "displaystyle",
    "textstyle",
    "scriptstyle",
    "scriptscriptstyle",
}
_SPACE_CMDS = {"quad", "qquad"}
_SYMBOL_CMDS = {
    "LaTeX": "LaTeX",
    "TeX": "TeX",
    "eg": "e.g.",
    "ie": "i.e.",
    "etal": "et al.",
    "ldots": "...",
    "cdots": "...",
    "dots": "...",
}

_SPECIAL_RE = re.compile(
    r"\$|\\\(|\\\)|\\\[|\\\]|\\\\|\\\s|\\[A-Za-z]+\*?|\\[%&_#{}$~^]|~"
)
_BIBITEM_RE = re.compile(r"\\bibitem(?:\[([^\]]*)\])?\{([^}]+)\}")
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics\*?(?:\[[^\]]*\])?\{([^}]+)\}")
_THEBIB_BEGIN_RE = re.compile(r"\\begin\{thebibliography\}")


def _build_bibliography_blocks(inner: str) -> list[Block]:
    """`thebibliography` の内容 → `reference_entry` ブロック列(状態を持たない純粋関数)。"""
    matches = list(_BIBITEM_RE.finditer(inner))
    blocks: list[Block] = []
    for idx, m in enumerate(matches):
        display_label = _strip_markup(m.group(1) or "")
        label = m.group(2).strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(inner)
        semi_raw = _collapse(inner[start:end])
        if not semi_raw:
            continue
        structured = _structure_reference(semi_raw) or {}
        if display_label:
            structured["citation_label"] = display_label
        blocks.append(
            Block(
                id="",
                type="reference_entry",
                raw=_strip_markup(semi_raw),
                label=label,
                structured=structured or None,
            )
        )
    return blocks


def _extract_bibliography(text: str) -> tuple[str, str | None]:
    """`\\begin{thebibliography}...\\end{thebibliography}` を本文から取り出す。

    参考文献は LaTeX 上は現在位置(しばしば `\\appendix` 後)にそのまま出現するが、
    HTML パーサ(`ltx_bibliography` は常に独立したトップレベルセクション)と同様、
    独立した `sec-refs` セクションへ常に昇格させる(plans/05 §4.2 と同方針)。
    """
    m = _THEBIB_BEGIN_RE.search(text)
    if not m:
        return text, None
    inner, end = _read_environment(text, m.end(), "thebibliography")
    return text[: m.start()] + text[end:], inner


class _LatexParser:
    """1 回のパースの状態(ラベル解決・脚注・warnings)を保持する。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self._fn_counter = 0
        self._fn_stack: list[list[Block]] = []
        self._label_targets: dict[str, str] = {}
        self._pending_refs: list[Inline] = []
        self._appendix = False
        self._level_counters = [0, 0, 0]
        self._theorem_counters: dict[str, int] = {}
        self._anon_counter = 0

    # -- 数式番号 -----------------------------------------------------------

    def _next_number(self, level: int) -> str:
        self._level_counters[level - 1] += 1
        for i in range(level, 3):
            self._level_counters[i] = 0
        if self._appendix:
            letters = chr(ord("A") + self._level_counters[0] - 1)
            rest = [str(c) for c in self._level_counters[1:level]]
            return ".".join([letters, *rest])
        return ".".join(str(c) for c in self._level_counters[:level])

    # -- トップレベル(文書木構築) --------------------------------------------

    def resolve_pending_refs(self) -> None:
        """2 パス目: `\\ref`/`\\eqref` の kind を label→kind map から確定する(plans/05 §4.3)。

        未解決は `section` へ縮退+warn(HTML パーサの未知パターン縮退と同方針)。
        """
        for il in self._pending_refs:
            if il.ref and il.ref in self._label_targets:
                il.kind = self._label_targets[il.ref]
            else:
                if il.kind is None:
                    il.kind = "section"
                self.warnings.append(f"未解決の相互参照を section に縮退: \\ref{{{il.ref}}}")

    def parse_top_level(self, body: str) -> list[Section]:
        nodes = _iter_top_level(body)
        sections: list[Section] = []
        pending: list[Block] = []
        order = 0
        self._fn_stack.append([])
        i = 0
        while i < len(nodes):
            node = nodes[i]
            if node[0] == "appendix":
                self._appendix = True
                self._level_counters = [0, 0, 0]
                i += 1
                continue
            if node[0] == "section" and node[1] == 1:
                if pending or self._fn_stack[-1]:
                    sections.append(self._make_intro_section(order, pending, self._fn_stack.pop()))
                    self._fn_stack.append([])
                    order += 1
                    pending = []
                sec, i = self._make_section(nodes, i)
                sections.append(sec)
                order += 1
                continue
            if node[0] == "section":  # レベル 2/3 が最上位に出現(異常系)→ 平坦化して受理
                sec, i = self._make_section(nodes, i)
                sections.append(sec)
                order += 1
                continue
            pending.extend(self._blocks_for_node(node))
            i += 1
        fns = self._fn_stack.pop()
        if pending or fns:
            sections.append(self._make_intro_section(order, pending, fns))
        return sections

    def _make_intro_section(self, order: int, blocks: list[Block], fns: list[Block]) -> Section:
        sec = Section(id=f"sec-s{order}", heading=SectionHeading())
        sec.blocks.extend(blocks)
        sec.blocks.extend(fns)
        return sec

    def _make_section(self, nodes: list[tuple[Any, ...]], idx: int) -> tuple[Section, int]:
        _, level, title_raw, label, starred = nodes[idx]
        if starred:
            self._anon_counter += 1
            number = ""
            path = f"s{self._anon_counter}"
        else:
            number = self._next_number(level)
            path = number.replace(".", "-")
        title = _flatten_plain(self._parse_inline(title_raw))
        sec = Section(id=f"sec-{path}", heading=SectionHeading(number=number, title=title))
        sec.blocks.append(
            Block(
                id="",
                type="heading",
                level=level,
                number=number or None,
                title=title or None,
                label=label,
            )
        )
        if label:
            self._label_targets[label] = "section"
        self._fn_stack.append([])
        i = idx + 1
        while i < len(nodes):
            nx = nodes[i]
            if nx[0] == "appendix":
                self._appendix = True
                self._level_counters = [0, 0, 0]
                i += 1
                continue
            if nx[0] == "section" and nx[1] <= level:
                break
            if nx[0] == "section":  # 子セクション(レベルが深い)
                child, i = self._make_section(nodes, i)
                sec.sections.append(child)
                continue
            sec.blocks.extend(self._blocks_for_node(nx))
            i += 1
        sec.blocks.extend(self._fn_stack.pop())
        return sec, i

    def _blocks_for_node(self, node: tuple[Any, ...]) -> list[Block]:
        if node[0] == "text":
            return self._paragraphs(node[1])
        if node[0] == "env":
            return self._env_block(node[1], node[2])
        return []

    def _flatten_env(self, inner: str) -> list[Block]:
        """透過コンテナ(center 等)の内容を再帰的にブロック化する。"""
        out: list[Block] = []
        for node in _iter_top_level(inner):
            if node[0] in ("section", "appendix"):
                continue
            out.extend(self._blocks_for_node(node))
        return out

    # -- ブロック種別ディスパッチ ---------------------------------------------

    def _env_block(self, name: str, inner: str) -> list[Block]:
        base = name.rstrip("*")
        if base in ("equation", "align", "gather", "multline", "eqnarray"):
            return self._equation_env(inner, grouped=base != "equation")
        if base in ("figure", "wrapfigure"):
            return [self._figure_env(inner)]
        if base == "table":
            return [self._table_env(inner)]
        if base in ("itemize", "enumerate"):
            return [self._list_env(inner, ordered=base == "enumerate")]
        if base in ("quote", "quotation"):
            return [Block(id="", type="quote", inlines=self._parse_inline(inner))]
        if base in _THEOREM_ENVS:
            return [self._theorem_env(base, inner)]
        if base in ("algorithm", "algorithmic"):
            return [self._algorithm_env(inner)]
        if base in ("verbatim", "lstlisting", "minted"):
            return [Block(id="", type="code", code=inner.strip("\n"), language=None)]
        if base == "thebibliography":
            return _build_bibliography_blocks(inner)
        if base == "abstract":
            return []  # papers.abstract が正(html パーサと同方針)
        if base in ("center", "flushleft", "flushright", "minipage", "small", "footnotesize"):
            return self._flatten_env(inner)
        # 未知 env: 段落として受理する(壊れた訳を見せないための安全側。P3)
        return self._paragraphs(inner)

    def _paragraphs(self, raw: str) -> list[Block]:
        out: list[Block] = []
        for chunk in re.split(r"\n\s*\n+", raw):
            if not chunk.strip():
                continue
            inl = self._parse_inline(chunk)
            if inl:
                out.append(Block(id="", type="paragraph", inlines=inl))
        return out

    def _equation_env(self, inner: str, *, grouped: bool) -> list[Block]:
        labels = re.findall(r"\\label\{([^}]*)\}", inner)
        text = re.sub(r"\\label\{[^}]*\}", "", inner).strip()
        if not grouped:
            label = labels[0].strip() if labels else None
            blk = Block(id="", type="equation", latex=text, label=label)
            if label:
                self._label_targets[label] = "equation"
            return [blk]
        rows = [r.strip() for r in re.split(r"\\\\", text) if r.strip()]
        if not rows and text:
            rows = [text]
        blocks = [Block(id="", type="equation", latex=row) for row in rows]
        if labels and blocks:
            label = labels[0].strip()
            blocks[0].label = label
            self._label_targets[label] = "equation"
        return blocks

    def _figure_env(self, inner: str) -> Block:
        label = None
        m_label = re.search(r"\\label\{([^}]*)\}", inner)
        if m_label:
            label = m_label.group(1).strip()
        asset_key = None
        m_img = _INCLUDEGRAPHICS_RE.search(inner)
        if m_img:
            asset_key = m_img.group(1).strip()
        caption_inlines: list[Inline] = []
        m_cap = re.search(r"\\caption\{", inner)
        if m_cap:
            raw_caption, _end = _read_braced(inner, m_cap.end() - 1)
            caption_inlines = self._parse_inline(raw_caption)
        blk = Block(id="", type="figure", asset_key=asset_key, caption=caption_inlines, label=label)
        if label:
            self._label_targets[label] = "figure"
        return blk

    def _table_env(self, inner: str) -> Block:
        label = None
        m_label = re.search(r"\\label\{([^}]*)\}", inner)
        if m_label:
            label = m_label.group(1).strip()
        raw = None
        m_tab = re.search(r"\\begin\{(tabular[xX*]?)\}", inner)
        if m_tab:
            try:
                _inner, end = _read_environment(inner, m_tab.end(), m_tab.group(1))
                raw = inner[m_tab.start() : end]
            except LatexParseError:
                raw = None
        caption_inlines: list[Inline] = []
        m_cap = re.search(r"\\caption\{", inner)
        if m_cap:
            raw_caption, _end = _read_braced(inner, m_cap.end() - 1)
            caption_inlines = self._parse_inline(raw_caption)
        blk = Block(id="", type="table", raw=raw, caption=caption_inlines, label=label)
        if label:
            self._label_targets[label] = "table"
        return blk

    def _list_env(self, inner: str, *, ordered: bool) -> Block:
        parts = re.split(r"\\item\b\s*(?:\[[^\]]*\])?", inner)
        items: list[list[Inline]] = []
        for part in parts[1:]:
            inl = self._parse_inline(part)
            if inl:
                items.append(inl)
        return Block(id="", type="list", ordered=ordered, items=items)

    def _theorem_env(self, base: str, inner: str) -> Block:
        label = None
        text = inner
        m_label = re.search(r"\\label\{([^}]*)\}", inner)
        if m_label:
            label = m_label.group(1).strip()
            text = text[: m_label.start()] + text[m_label.end() :]
        display = _THEOREM_ENVS.get(base, base.capitalize())
        if base == "proof":
            title = display
        else:
            self._theorem_counters[base] = self._theorem_counters.get(base, 0) + 1
            title = f"{display} {self._theorem_counters[base]}"
        blk = Block(
            id="", type="theorem", title=title, label=label, inlines=self._parse_inline(text)
        )
        if label:
            self._label_targets[label] = "theorem"
        return blk

    def _algorithm_env(self, inner: str) -> Block:
        label = None
        text = inner
        m_label = re.search(r"\\label\{([^}]*)\}", inner)
        if m_label:
            label = m_label.group(1).strip()
            text = text[: m_label.start()] + text[m_label.end() :]
        caption_inlines: list[Inline] = []
        m_cap = re.search(r"\\caption\{", text)
        if m_cap:
            raw_caption, end = _read_braced(text, m_cap.end() - 1)
            caption_inlines = self._parse_inline(raw_caption)
            text = text[: m_cap.start()] + text[end:]
        body_text = _collapse(text)
        blk = Block(
            id="",
            type="algorithm",
            inlines=[Inline(t="text", v=body_text)] if body_text else [],
            caption=caption_inlines,
            label=label,
        )
        if label:
            self._label_targets[label] = "algorithm"
        return blk

    # -- インライン -----------------------------------------------------------

    def _parse_inline(self, text: str) -> list[Inline]:
        out: list[Inline] = []
        i = 0
        n = len(text)
        while i < n:
            m = _SPECIAL_RE.search(text, i)
            if m is None:
                _append_text(out, text[i:])
                break
            if m.start() > i:
                _append_text(out, text[i : m.start()])
            tok = m.group(0)
            if tok == "$":
                end = text.find("$", m.end())
                if end == -1:
                    _append_text(out, text[m.start() :])
                    i = n
                    continue
                out.append(Inline(t="math_inline", v=text[m.end() : end].strip()))
                i = end + 1
                continue
            if tok == "\\(":
                end = text.find("\\)", m.end())
                if end == -1:
                    _append_text(out, text[m.start() :])
                    i = n
                    continue
                out.append(Inline(t="math_inline", v=text[m.end() : end].strip()))
                i = end + 2
                continue
            if tok == "\\[":
                end = text.find("\\]", m.end())
                if end == -1:
                    _append_text(out, text[m.start() :])
                    i = n
                    continue
                out.append(Inline(t="math_inline", v=text[m.end() : end].strip()))
                i = end + 2
                continue
            if tok == "\\)":
                i = m.end()
                continue
            if tok == "\\]":
                i = m.end()
                continue
            if tok == "~":
                _append_text(out, " ")
                i = m.end()
                continue
            if tok == "\\\\" or re.match(r"\\\s", tok):
                _append_text(out, " ")
                i = m.end()
                continue
            if len(tok) == 2 and tok[0] == "\\" and tok[1] in "%&_#{}$~^":
                _append_text(out, tok[1])
                i = m.end()
                continue
            cmd = tok[1:].rstrip("*")
            i, produced = self._dispatch_command(text, m.end(), cmd)
            out.extend(produced)
        return _merge_text(out)

    def _dispatch_command(self, text: str, pos: int, cmd: str) -> tuple[int, list[Inline]]:
        if cmd in _SYMBOL_CMDS:
            arg, end = _read_optional_braced(text, pos)
            return (end if arg == "" else pos), [Inline(t="text", v=_SYMBOL_CMDS[cmd])]
        if cmd in _CITE_CMDS:
            arg, end = _read_optional_braced(text, pos)
            keys = [k.strip() for k in (arg or "").split(",") if k.strip()]
            return end, [Inline(t="citation", ref=k) for k in keys]
        if cmd in _REF_CMDS:
            arg, end = _read_optional_braced(text, pos)
            label = (arg or "").strip()
            kind_hint = "equation" if cmd == "eqref" else None
            il = Inline(t="ref", ref=label, kind=kind_hint)
            self._pending_refs.append(il)
            return end, [il]
        if cmd == "footnote":
            arg, end = _read_optional_braced(text, pos)
            self._fn_counter += 1
            fn_no = self._fn_counter
            fn_block = Block(
                id="",
                type="footnote",
                label=f"footnote{fn_no}",
                inlines=self._parse_inline(arg or ""),
            )
            if self._fn_stack:
                self._fn_stack[-1].append(fn_block)
            return end, [Inline(t="footnote_ref", ref=f"footnote{fn_no}")]
        if cmd == "url":
            arg, end = _read_optional_braced(text, pos)
            href = (arg or "").strip()
            return end, [Inline(t="url", v=href, href=href)]
        if cmd == "href":
            arg1, mid = _read_optional_braced(text, pos)
            arg2, end = _read_optional_braced(text, mid)
            href = (arg1 or "").strip()
            label_txt = _flatten_plain(self._parse_inline(arg2 or "")) or href
            return end, [Inline(t="url", v=label_txt, href=href)]
        if cmd in ("emph", "textit", "textsc", "textbf"):
            arg, end = _read_optional_braced(text, pos)
            txt = _flatten_plain(self._parse_inline(arg or ""))
            return end, ([Inline(t="emphasis", v=txt)] if txt else [])
        if cmd in ("texttt", "code", "verb"):
            arg, end = _read_optional_braced(text, pos)
            return end, ([Inline(t="code_inline", v=arg)] if arg else [])
        if cmd in _NO_OUTPUT_CMDS:
            if cmd == "label":
                _arg, end = _read_optional_braced(text, pos)
                return end, []
            return pos, []
        if cmd in _SETUP_CMDS:
            return _consume_setup_command(text, pos, cmd), []
        if cmd in _SPACE_CMDS:
            return pos, [Inline(t="text", v=" ")]
        # 未知コマンド: 引数があれば透過(内容だけ残す)、無ければ読み飛ばす。
        arg, end = _read_optional_braced(text, pos)
        if arg is not None:
            return end, self._parse_inline(arg)
        return pos, []


# ============================================================================
# 公開エントリポイント
# ============================================================================


def parse_latex_source(main_name: str, files: dict[str, str]) -> ParsedDocument:
    """展開済みファイル群 + メインファイル名 → 構造化ドキュメント(plans/05 §5)。"""
    main_tex = files[main_name]
    visited = {main_name}
    expanded = _expand_includes(main_tex, files, visited)
    expanded = _resolve_bibliography(expanded, files)
    body = _extract_document_body(expanded)
    body = _strip_frontmatter_commands(body)
    body = _strip_setup_commands(body)
    body, bib_inner = _extract_bibliography(body)

    parser = _LatexParser()
    sections = parser.parse_top_level(body)
    parser.resolve_pending_refs()

    if bib_inner is not None:
        ref_blocks = _build_bibliography_blocks(bib_inner)
        if ref_blocks:
            refs_section = Section(
                id="sec-refs", heading=SectionHeading(number="", title="References")
            )
            refs_section.blocks.append(Block(id="", type="heading", level=1, title="References"))
            refs_section.blocks.extend(ref_blocks)
            sections.append(refs_section)

    assign_block_ids(sections)
    return ParsedDocument(
        quality_level="A",
        source_format="latex",
        parser_version=PARSER_VERSION,
        sections=sections,
        warnings=parser.warnings,
    )


def parse_arxiv_latex(archive: bytes) -> ParsedDocument:
    """arXiv e-print バイト列(tar.gz / 単一ファイル gzip)→ 構造化ドキュメント(plans/05 §5)。"""
    extracted = extract_latex_archive(archive)
    name, _content = select_main_tex(extracted.text_files)
    return parse_latex_source(name, extracted.text_files)
