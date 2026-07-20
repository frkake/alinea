"""ソースファイルを symbol 境界(関数/クラス/メソッド)で chunk 化する(§9 手順5)。

**設計方針(USER DIRECTIVE)**: 素朴な行数分割ではなく、``tree-sitter`` + grammar pack
(``tree-sitter-language-pack``)で関数/クラス/メソッドの境界を検出して chunk 化する。
grammar pack が対応しない言語や parse 失敗時のみ、最大 :data:`MAX_CHUNK_LINES` 行の
窓へフォールバックする。

各 chunk は path・symbol 名・start/end 行(1 始まり)・本文を持つ。行番号は 1 始まりで、
:mod:`alinea_core.code_analysis.contracts` のサーバー検証(実バイト照合)と整合する。

tree-sitter は import 時に一度だけ解決し、利用不可なら全言語で行窓フォールバックへ縮退する
(オフライン/未インストール環境でも壊れない)。
"""

from __future__ import annotations

from dataclasses import dataclass

# 対象拡張子 → tree-sitter-language-pack の言語名。
_EXT_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "c_sharp",
    ".scala": "scala",
    ".kt": "kotlin",
    ".swift": "swift",
    ".lua": "lua",
}

# 各言語で「1 つの symbol chunk」とみなすトップレベル/ネストのノード型。
_SYMBOL_NODE_TYPES: frozenset[str] = frozenset(
    {
        # python
        "function_definition",
        "class_definition",
        "decorated_definition",
        # js/ts
        "function_declaration",
        "generator_function_declaration",
        "class_declaration",
        "method_definition",
        "lexical_declaration",  # const foo = () => {}(トップレベル関数束縛)
        "export_statement",
        "interface_declaration",
        "abstract_class_declaration",
        # java / c#
        "method_declaration",
        "constructor_declaration",
        "enum_declaration",
        "record_declaration",
        # go
        # (dup ok in a set)
        "type_declaration",
        # rust
        "function_item",
        "impl_item",
        "struct_item",
        "enum_item",
        "trait_item",
        # c / cpp
        "struct_specifier",
        "class_specifier",
        # ruby
        "method",
        "class",
        "module",
    }
)

# 行窓フォールバックの最大行数(brief / §8)。
MAX_CHUNK_LINES = 200

# tree-sitter が対応できない chunk が MAX_CHUNK_LINES を超える場合、行窓へ再分割する。


@dataclass(frozen=True)
class CodeChunk:
    """1 chunk。symbol 名は tree-sitter 由来(行窓は "lines:start-end")。"""

    path: str
    symbol: str
    start_line: int  # 1 始まり(含む)
    end_line: int  # 1 始まり(含む)
    text: str
    strategy: str  # "tree_sitter" | "line_window"

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


# --------------------------------------------------------------------------- #
# tree-sitter の遅延解決(利用不可なら None のまま = 行窓フォールバック)
# --------------------------------------------------------------------------- #
_TS_AVAILABLE: bool | None = None


def tree_sitter_available() -> bool:
    """tree-sitter + grammar pack が import 可能かを一度だけ判定してキャッシュする。"""
    global _TS_AVAILABLE
    if _TS_AVAILABLE is None:
        try:
            import tree_sitter  # noqa: F401
            from tree_sitter_language_pack import get_parser  # noqa: F401

            _TS_AVAILABLE = True
        except Exception:  # pragma: no cover - オフライン/未インストール
            _TS_AVAILABLE = False
    return _TS_AVAILABLE


def _get_parser(language: str):  # type: ignore[no-untyped-def]
    from tree_sitter_language_pack import get_parser

    return get_parser(language)


def language_for_path(path: str) -> str | None:
    base = path.rsplit("/", 1)[-1].lower()
    dot = base.rfind(".")
    if dot <= 0:
        return None
    return _EXT_TO_LANGUAGE.get(base[dot:])


def _symbol_name(node, source_bytes: bytes) -> str:  # type: ignore[no-untyped-def]
    """ノードから可能なら識別子名を取り出す。無ければノード型を使う。"""
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return source_bytes[name_node.start_byte : name_node.end_byte].decode(
            "utf-8", "replace"
        )
    # decorated_definition / export_statement 等は子に本体を持つ。
    for child in node.children:
        inner = child.child_by_field_name("name") if hasattr(child, "child_by_field_name") else None
        if inner is not None:
            return source_bytes[inner.start_byte : inner.end_byte].decode("utf-8", "replace")
    return str(node.type)


def _line_window_chunks(
    path: str, lines: list[str], *, start_offset: int = 0, max_lines: int = MAX_CHUNK_LINES
) -> list[CodeChunk]:
    """[start_offset..] の行を max_lines 窓で分割する(1 始まり行番号)。"""
    chunks: list[CodeChunk] = []
    n = len(lines)
    i = start_offset
    while i < n:
        end = min(i + max_lines, n)
        text = "\n".join(lines[i:end])
        if text.strip():
            chunks.append(
                CodeChunk(
                    path=path,
                    symbol=f"lines:{i + 1}-{end}",
                    start_line=i + 1,
                    end_line=end,
                    text=text,
                    strategy="line_window",
                )
            )
        i = end
    return chunks


def _split_oversized(chunk: CodeChunk, lines: list[str]) -> list[CodeChunk]:
    """MAX_CHUNK_LINES を超える symbol chunk を行窓へ再分割する(巨大クラス対策)。"""
    if chunk.line_count <= MAX_CHUNK_LINES:
        return [chunk]
    windows = _line_window_chunks(
        chunk.path, lines[: chunk.end_line], start_offset=chunk.start_line - 1
    )
    # symbol 名を保ちつつ strategy は line_window(境界が symbol でないため)。
    return [
        CodeChunk(
            path=w.path,
            symbol=f"{chunk.symbol}#{w.start_line}-{w.end_line}",
            start_line=w.start_line,
            end_line=w.end_line,
            text=w.text,
            strategy="line_window",
        )
        for w in windows
    ]


def chunk_source(path: str, source: str) -> list[CodeChunk]:
    """1 ファイルを chunk 化する。tree-sitter で symbol 境界、無理なら行窓へフォールバック。"""
    lines = source.split("\n")
    if not source.strip():
        return []

    language = language_for_path(path)
    if language is None or not tree_sitter_available():
        return _line_window_chunks(path, lines)

    try:
        parser = _get_parser(language)
        source_bytes = source.encode("utf-8")
        tree = parser.parse(source_bytes)
    except Exception:  # pragma: no cover - parse 失敗は行窓へ
        return _line_window_chunks(path, lines)

    root = tree.root_node
    symbol_chunks: list[CodeChunk] = []
    covered: list[tuple[int, int]] = []  # (start_line, end_line) 1 始まり

    def _emit(node) -> None:  # type: ignore[no-untyped-def]
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        text = "\n".join(lines[start_line - 1 : end_line])
        if not text.strip():
            return
        symbol_chunks.append(
            CodeChunk(
                path=path,
                symbol=_symbol_name(node, source_bytes),
                start_line=start_line,
                end_line=end_line,
                text=text,
                strategy="tree_sitter",
            )
        )
        covered.append((start_line, end_line))

    # トップレベル + クラス本体直下のメソッドを 1 段だけ潜って収集する。
    def _walk(node, depth: int) -> None:  # type: ignore[no-untyped-def]
        for child in node.children:
            if child.type in _SYMBOL_NODE_TYPES:
                _emit(child)
                # クラス/impl/module の中のメソッドも個別 chunk にする(1 段のみ深掘り)。
                if depth < 2:
                    _walk_body(child, depth + 1)
            elif depth == 0:
                # トップレベルの export/namespace 等はさらに潜る。
                _walk(child, depth)

    def _walk_body(node, depth: int) -> None:  # type: ignore[no-untyped-def]
        for child in node.children:
            # クラス本体(block/declaration_list/class_body)を潜る。
            if child.type in _SYMBOL_NODE_TYPES:
                _emit(child)
            else:
                for grand in child.children:
                    if grand.type in _SYMBOL_NODE_TYPES:
                        _emit(grand)

    _walk(root, 0)

    if not symbol_chunks:
        return _line_window_chunks(path, lines)

    # symbol 化されなかった行(トップレベル文・import 等)を行窓で拾う。
    symbol_chunks.sort(key=lambda c: c.start_line)
    result: list[CodeChunk] = []
    cursor = 1
    for chunk in symbol_chunks:
        if chunk.start_line > cursor:
            gap = _line_window_chunks(
                path, lines[: chunk.start_line - 1], start_offset=cursor - 1
            )
            result.extend(gap)
        result.extend(_split_oversized(chunk, lines))
        cursor = max(cursor, chunk.end_line + 1)
    if cursor <= len(lines):
        result.extend(_line_window_chunks(path, lines, start_offset=cursor - 1))

    # 重複除去(ネストで同一範囲を二重取得した場合)。
    seen: set[tuple[int, int]] = set()
    deduped: list[CodeChunk] = []
    for chunk in sorted(result, key=lambda c: (c.start_line, c.end_line)):
        key = (chunk.start_line, chunk.end_line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


def chunk_repository(files: dict[str, str]) -> list[CodeChunk]:
    """複数ファイルを chunk 化して 1 リストにまとめる(path 昇順で決定的)。"""
    chunks: list[CodeChunk] = []
    for path in sorted(files):
        chunks.extend(chunk_source(path, files[path]))
    return chunks
