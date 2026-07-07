"""PDF パイプライン(品質 B。plans/05 §6)。

PyMuPDF(fitz)を主、表セル抽出のみ pdfplumber を併用する(spec-decisions C7)。
`parser_version='pdf-1.0.0'` / `source_format='pdf'` / `quality_level='B'`。
数値はすべて pt(1/72 インチ、PyMuPDF の既定単位のまま)。

処理順は §6 の節番号のとおり: 6.1 抽出 → 6.2 ヘッダ/フッタ除去 → 6.3 段組み判定・
読み順復元 → 6.4 段落組み立て → 6.5 見出し検出 → 6.6 図 → 6.7 表 → 6.8 数式 →
6.9 参考文献 → 6.10 stats。

出力の中間表現は既存の `yakudoku_core.document`(Block/Section/Inline)を再利用し、
ブロック安定 ID も `parsing.block_ids.assign_block_ids` を再利用する(重複定義しない)。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import fitz  # PyMuPDF

from yakudoku_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from yakudoku_core.document.inlines import Inline
from yakudoku_core.parsing.block_ids import assign_block_ids

PARSER_VERSION = "pdf-1.0.0"

_WS = re.compile(r"\s+")

# --- 例外 -----------------------------------------------------------------------


class PdfParseError(Exception):
    """PDF パース失敗(§6.1 の non-retryable 分類。plans/05 §2.4 の kind と揃える)。"""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        self.message = message
        super().__init__(message)


# --- 見出し検出(§6.5) -----------------------------------------------------------

# 確定規則: 番号パターンにマッチした候補のみ見出しにする(全大文字短行は候補止まりで
# heading にしない。plans/05 §6.5 の「番号なし候補は段落先頭の強調とみなし heading に
# しない」)。そのため候補判定と抽出を 1 本の正規表現(番号 + 区切り + タイトル本体)で行う。
_HEADING_SPLIT_RE = re.compile(
    r"^(?:(?P<num>\d+(?:\.\d+){0,3})|(?:Appendix\s+)?(?P<letter>[A-Z]))[.\s]+(?P<title>\S.*)$"
)
_FIXED_HEADINGS = {
    "abstract": "Abstract",
    "references": "References",
    "bibliography": "References",
    "acknowledgments": "Acknowledgments",
    "acknowledgements": "Acknowledgements",
    "appendix": "Appendix",
}

# --- 図表キャプション(§6.6.2・§6.7) ---------------------------------------------

_CAPTION_RE = re.compile(r"^(Figure|Fig\.|Table)\s*~?\s*(\d+|[IVXL]+)\s*[.:]")

# --- 数式ヒューリスティクス(§6.8) -----------------------------------------------

_MATH_CHARS = frozenset("∑∫∂√±≤≥≈∈∀∃αβγ=+−/^_{}")  # noqa: RUF001 — 数式記号の意図的な列挙
_EQ_NUM_RE = re.compile(r"\((\d+)\)\s*$")

# --- 参考文献分割(§6.9) ---------------------------------------------------------

_BIB_MARKER_RE = re.compile(r"^\[(\d+)\]\s*")
_ARXIV_RE = re.compile(
    r"(?:arXiv:|arxiv\.org/abs/)\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.IGNORECASE
)
_YEAR_RE = re.compile(r"(19|20)\d{2}")
_DOI_RE = re.compile(r"doi\.org/(\S+)", re.IGNORECASE)
_TITLE_QUOTE_RE = re.compile('[“"‘]([^”"’]+)[”"’]')  # noqa: RUF001 — 引用符の意図的な列挙

# --- ヘッダ/フッタ(§6.2) --------------------------------------------------------

_PAGENUM_RE = re.compile(r"^\d{1,4}$")
_DIGIT_RE = re.compile(r"\d")

_SENTENCE_END = frozenset('.?!”’")')  # noqa: RUF001 — 文末記号の意図的な列挙


# ============================ データ構造 ============================


@dataclass
class _Line:
    """PDF 1 行の位置・書式(PyMuPDF `get_text('dict')` の line 相当)。"""

    page: int  # 1 起点
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0


@dataclass
class _Region:
    """図候補領域(ラスター画像の連結成分。§6.6.1)。"""

    page: int
    bbox: list[float]
    claimed: bool = False


@dataclass
class _TableCandidate:
    """表候補(§6.7。`find_tables()` 由来のセル構造つき領域)。"""

    page: int
    bbox: list[float]
    rows: list[list[Any]] | None
    claimed: bool = False


@dataclass
class ParsedPdfDocument:
    """PDF パース結果。品質 B。html_parser の `ParsedDocument` と対の API。"""

    quality_level: str = "B"
    source_format: str = "pdf"
    parser_version: str = PARSER_VERSION
    sections: list[Section] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    # block.id -> 切り出し済み PNG バイト列(figure/table/equation)。
    # 呼び出し側(worker)が paper_id/revision_id を使って S3 へ PUT し、
    # 実ストレージキーで block.asset_key を更新する(plans/05 §6.6.3)。
    figure_images: dict[str, bytes] = field(default_factory=dict)

    def _all_blocks(self) -> list[Block]:
        return list(_iter_blocks(self.sections))

    @property
    def blocks(self) -> list[Block]:
        return self._all_blocks()

    @property
    def figures(self) -> list[Block]:
        return [b for b in self._all_blocks() if b.type == "figure"]

    @property
    def tables(self) -> list[Block]:
        return [b for b in self._all_blocks() if b.type == "table"]

    @property
    def references(self) -> list[Block]:
        return [b for b in self._all_blocks() if b.type == "reference_entry"]

    def to_document_content(self) -> DocumentContent:
        return DocumentContent(quality_level="B", sections=self.sections)


def _iter_blocks(sections: list[Section]) -> list[Block]:
    out: list[Block] = []

    def walk(sec: Section) -> None:
        out.extend(sec.blocks)
        for sub in sec.sections:
            walk(sub)

    for sec in sections:
        walk(sec)
    return out


# ============================ §6.1 抽出・テキストレイヤ判定 ============================


def _count_extractable_chars(doc: fitz.Document) -> int:
    return sum(len(page.get_text().strip()) for page in doc)


def check_text_layer(data: bytes) -> None:
    """テキストレイヤ判定(§6.1)。抽出文字数 < 40 x ページ数 なら :class:`PdfParseError`。

    受け口(``POST /api/ingest/pdf``)が同期的に呼ぶ軽量チェック(dict 抽出は行わない)。
    """
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        n_pages = doc.page_count
        if n_pages == 0 or _count_extractable_chars(doc) < 40 * n_pages:
            raise PdfParseError("no_text_layer", "テキストが抽出できません")
    finally:
        doc.close()


def _extract_page_lines(page: fitz.Page, page_no: int) -> list[_Line]:
    raw = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    lines: list[_Line] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(str(s.get("text", "")) for s in spans)
            if not text.strip():
                continue
            x0 = min(float(s["bbox"][0]) for s in spans)
            y0 = min(float(s["bbox"][1]) for s in spans)
            x1 = max(float(s["bbox"][2]) for s in spans)
            y1 = max(float(s["bbox"][3]) for s in spans)
            size = max(float(s.get("size", 0.0)) for s in spans)
            bold = any(int(s.get("flags", 0)) & (2**4) for s in spans)
            lines.append(
                _Line(page=page_no, text=text, x0=x0, y0=y0, x1=x1, y1=y1, size=size, bold=bold)
            )
    return lines


def _compute_body_metrics(pages_lines: list[list[_Line]]) -> tuple[float, float]:
    """本文フォントサイズ(最頻値)と本文行高(中央値。§6.1)。"""
    sizes: Counter[float] = Counter()
    for lines in pages_lines:
        for ln in lines:
            sizes[round(ln.size, 1)] += 1
    body_size = sizes.most_common(1)[0][0] if sizes else 10.0
    heights = [
        ln.height for lines in pages_lines for ln in lines if abs(ln.size - body_size) <= 0.5
    ]
    if not heights:
        heights = [ln.height for lines in pages_lines for ln in lines] or [12.0]
    heights.sort()
    line_h = heights[len(heights) // 2]
    return body_size, line_h


# ============================ §6.2 ヘッダ・フッタ除去 ============================


def _normalize_repeat(text: str) -> str:
    t = _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip()
    return _DIGIT_RE.sub("#", t)


def _band_candidate(line: _Line, height: float) -> bool:
    return line.y1 <= 56.0 or line.y0 >= height - 48.0


def _remove_headers_footers(pages_lines: list[list[_Line]], page_heights: list[float]) -> None:
    n_pages = len(pages_lines)
    if n_pages == 0:
        return
    groups: dict[str, list[tuple[int, int]]] = {}
    to_remove: set[tuple[int, int]] = set()
    for pi, lines in enumerate(pages_lines):
        height = page_heights[pi]
        for li, ln in enumerate(lines):
            if not _band_candidate(ln, height):
                continue
            stripped = ln.text.strip()
            if _PAGENUM_RE.match(stripped):
                to_remove.add((pi, li))
                continue
            norm = _normalize_repeat(ln.text)
            if norm:
                groups.setdefault(norm, []).append((pi, li))
    threshold = -(-6 * n_pages // 10)  # ceil(0.6 * n_pages)
    for occurrences in groups.values():
        pages_hit = {pi for pi, _ in occurrences}
        if len(pages_hit) >= threshold:
            to_remove.update(occurrences)
    # ページ内でインデックスの大きい方から削除(削除で以降のインデックスがずれないように)。
    for pi, li in sorted(to_remove, key=lambda t: (t[0], -t[1])):
        del pages_lines[pi][li]


# ============================ §6.3 段組み判定・読み順復元 ============================


def _is_structural_line(line: _Line, body_size: float) -> bool:
    """見出し・図表キャプション行か(§6.3 実装補足: 列のガター計測を乱すため除外する)。

    見出しは列を問わず左寄りの短い行になりがちで、キャプションは列を跨いで
    幅広になりがちなため、素朴な左右クラスタの min/max だけに頼るとガター幅の
    見積もりが崩れる。本文行(段落)だけでガターを測るのが§6.3 の意図に近い。
    """
    if _heading_info(line, body_size) is not None:
        return True
    return bool(_CAPTION_RE.match(line.text.strip()))


def _body_column_lines(
    lines: list[_Line], width: float, body_size: float
) -> tuple[list[_Line], list[_Line]]:
    """列統計(ガター・列中心)の代表行を左右クラスタに分ける。

    見出し・キャプション・中央帯に重心が乗る行(中央寄せの数式など)は列の代表
    行として不適切なため除外する(§6.3 実装補足。素朴な cx 判定だけでは
    ガター計測や列中心が乱れ、以後の数式判定(§6.8)にも波及するため)。
    """
    xc = width / 2.0
    mid_lo = xc - 0.12 * width
    mid_hi = xc + 0.12 * width
    body_lines = [
        ln
        for ln in lines
        if not _is_structural_line(ln, body_size) and not (mid_lo <= ln.cx <= mid_hi)
    ]
    left = [ln for ln in body_lines if ln.cx < xc]
    right = [ln for ln in body_lines if ln.cx >= xc]
    return left, right


def _detect_columns(lines: list[_Line], width: float, body_size: float = 0.0) -> int:
    if not lines or width <= 0:
        return 1
    xc = width / 2.0
    mid_lo = xc - 0.12 * width
    mid_hi = xc + 0.12 * width
    non_crossing = [ln for ln in lines if not (ln.x0 < mid_lo and ln.x1 > mid_hi)]
    r = len(non_crossing) / len(lines)
    if r < 0.85:
        return 1
    left, right = _body_column_lines(non_crossing, width, body_size)
    if not left or not right:
        return 1
    gutter = min(ln.x0 for ln in right) - max(ln.x1 for ln in left)
    return 2 if gutter >= 16.0 else 1


def _column_centers(
    lines: list[_Line], width: float, columns: int, body_size: float = 0.0
) -> tuple[float, float]:
    xc = width / 2.0
    if columns == 1 or not lines:
        return xc, xc
    left, right = _body_column_lines(lines, width, body_size)
    left_c = sum(ln.cx for ln in left) / len(left) if left else xc / 2.0
    right_c = sum(ln.cx for ln in right) / len(right) if right else xc * 1.5
    return left_c, right_c


def _reading_order(
    lines: list[_Line], width: float, body_size: float = 0.0
) -> tuple[list[_Line], int]:
    """§6.3 の読み順復元。1 段組は y→x 昇順、2 段組は左列 y 昇順→右列 y 昇順。"""
    columns = _detect_columns(lines, width, body_size)
    if columns == 1:
        return sorted(lines, key=lambda ln: (round(ln.y0, 1), ln.x0)), 1
    xc = width / 2.0
    mid_lo = xc - 0.12 * width
    mid_hi = xc + 0.12 * width
    # 完全に中央帯を横切る行に加え、重心が中央帯に乗る行(中央寄せの数式・図など)も
    # 素朴な cx 判定では左右どちらのクラスタにも属さないため、同じ「列外」扱いにする。
    crossing = [
        ln for ln in lines if (ln.x0 < mid_lo and ln.x1 > mid_hi) or (mid_lo <= ln.cx <= mid_hi)
    ]
    crossing_ids = {id(ln) for ln in crossing}
    left = sorted(
        (ln for ln in lines if id(ln) not in crossing_ids and ln.cx < xc), key=lambda ln: ln.y0
    )
    right = sorted(
        (ln for ln in lines if id(ln) not in crossing_ids and ln.cx >= xc), key=lambda ln: ln.y0
    )
    col_top = min((ln.y0 for ln in (*left, *right)), default=0.0)
    before = sorted((ln for ln in crossing if ln.y1 <= col_top + 1e-6), key=lambda ln: ln.y0)
    after = sorted((ln for ln in crossing if ln.y1 > col_top + 1e-6), key=lambda ln: ln.y0)
    return [*before, *left, *right, *after], 2


# ============================ §6.4 段落組み立て ============================


def _merge_line_texts(lines: list[_Line]) -> str:
    """ハイフネーション結合(行末 `-` + 次行先頭小文字 → 除去して連結)。それ以外は空白 1 個。"""
    parts: list[str] = []
    for ln in lines:
        t = _WS.sub(" ", ln.text).strip()
        if not t:
            continue
        if parts and parts[-1].endswith("-") and t[:1].islower():
            parts[-1] = parts[-1][:-1] + t
        else:
            parts.append(t)
    return " ".join(p for p in parts if p).strip()


def _union_bbox(lines: list[_Line]) -> list[float]:
    return [
        round(min(ln.x0 for ln in lines), 2),
        round(min(ln.y0 for ln in lines), 2),
        round(max(ln.x1 for ln in lines), 2),
        round(max(ln.y1 for ln in lines), 2),
    ]


# ============================ §6.5 見出し検出 ============================


def _font_ok(line: _Line, body_size: float) -> bool:
    return line.size >= body_size + 1.4 or (line.bold and line.size >= body_size)


def _heading_info(line: _Line, body_size: float) -> tuple[str, str] | None:
    """見出しなら (number, title) を返す。§6.5 の確定規則(番号 or 固定語のみ)。"""
    text = _WS.sub(" ", line.text).strip()
    if not text or not _font_ok(line, body_size):
        return None
    m = _HEADING_SPLIT_RE.match(text)
    if m:
        number = m.group("num") or m.group("letter") or ""
        title = m.group("title").strip()
        return number, title
    key = re.sub(r"[.:\s]+$", "", text).strip().lower()
    fixed = _FIXED_HEADINGS.get(key)
    if fixed:
        return "", fixed
    return None


# ============================ §6.6 図領域 ============================


def _area(bbox: tuple[float, float, float, float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _boxes_close(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float], gap: float
) -> bool:
    dx = max(a[0], b[0]) - min(a[2], b[2])
    dy = max(a[1], b[1]) - min(a[3], b[3])
    return max(dx, 0.0) <= gap and max(dy, 0.0) <= gap


def _union_box(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _cluster_boxes(
    boxes: list[tuple[float, float, float, float]], gap: float
) -> list[tuple[float, float, float, float]]:
    """距離 `gap` 以内の bbox を連結成分としてまとめる(§6.6.1)。"""
    current = list(boxes)
    changed = True
    while changed:
        changed = False
        out: list[tuple[float, float, float, float]] = []
        used = [False] * len(current)
        for i in range(len(current)):
            if used[i]:
                continue
            merged = current[i]
            used[i] = True
            for j in range(i + 1, len(current)):
                if used[j]:
                    continue
                if _boxes_close(merged, current[j], gap):
                    merged = _union_box(merged, current[j])
                    used[j] = True
                    changed = True
            out.append(merged)
        current = out
    return current


def _detect_figure_regions(page: fitz.Page, page_no: int) -> list[_Region]:
    try:
        infos = page.get_image_info(xrefs=True)
    except (RuntimeError, ValueError):
        infos = []
    boxes: list[tuple[float, float, float, float]] = [
        (
            float(info["bbox"][0]),
            float(info["bbox"][1]),
            float(info["bbox"][2]),
            float(info["bbox"][3]),
        )
        for info in infos
        if "bbox" in info
    ]
    clustered = _cluster_boxes(boxes, gap=12.0)
    return [
        _Region(page=page_no, bbox=[round(v, 2) for v in b])
        for b in clustered
        if _area(b) >= 1600.0
    ]


def _h_overlap_ratio(a: list[float], b: list[float]) -> float:
    left = max(a[0], b[0])
    right = min(a[2], b[2])
    inter = max(0.0, right - left)
    narrower = min(a[2] - a[0], b[2] - b[0])
    return inter / narrower if narrower > 0 else 0.0


# ============================ §6.7 表 ============================


def _rows_to_html(rows: list[list[Any]]) -> str:
    def esc(v: Any) -> str:
        s = "" if v is None else str(v)
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts = ["<table>"]
    for row in rows:
        cells = "".join(f"<td>{esc(c)}</td>" for c in row)
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</table>")
    return "".join(parts)


def _detect_table_candidates(
    page: fitz.Page, page_no: int, pdf_bytes: bytes
) -> list[_TableCandidate]:
    out: list[_TableCandidate] = []
    try:
        finder = page.find_tables()
        for t in finder.tables:
            try:
                rows = t.extract()
            except (RuntimeError, ValueError):
                rows = None
            if rows:
                out.append(
                    _TableCandidate(page=page_no, bbox=[round(v, 2) for v in t.bbox], rows=rows)
                )
    except (RuntimeError, ValueError):
        pass
    if out:
        return out
    try:
        import pdfplumber

        with pdfplumber.open(BytesIO(pdf_bytes)) as pl:
            pp_page = pl.pages[page_no - 1]
            for t in pp_page.find_tables():
                rows = t.extract()
                if rows:
                    out.append(
                        _TableCandidate(page=page_no, bbox=[round(v, 2) for v in t.bbox], rows=rows)
                    )
    except (ImportError, IndexError, ValueError, RuntimeError):
        pass
    return out


def _line_inside_any(line: _Line, candidates: list[_TableCandidate]) -> bool:
    """行の中心点が表候補領域の内側にあるか(セル文字列の重複読み流し防止用)。"""
    cx, cy = line.cx, (line.y0 + line.y1) / 2.0
    for c in candidates:
        if c.page == line.page and c.bbox[0] <= cx <= c.bbox[2] and c.bbox[1] <= cy <= c.bbox[3]:
            return True
    return False


# ============================ §6.8 数式 ============================


def _is_equation_line(line: _Line, body_size: float, col_center: float) -> bool:
    text = line.text.strip()
    if not text or abs(line.cx - col_center) > 6.0:
        return False
    core = text
    m = _EQ_NUM_RE.search(core)
    if m:
        core = core[: m.start()].rstrip()
    if not core:
        return False
    symbol_count = sum(1 for c in core if c in _MATH_CHARS)
    return (symbol_count / len(core)) >= 0.25


# ============================ §6.9 参考文献 ============================


def _structure_reference(raw: str) -> dict[str, str] | None:
    out: dict[str, str] = {}
    am = _ARXIV_RE.search(raw)
    if am:
        out["arxiv_id"] = am.group(1)
    ym = _YEAR_RE.search(raw)
    if ym:
        out["year"] = ym.group()
    tm = _TITLE_QUOTE_RE.search(raw)
    if tm:
        out["title"] = tm.group(1).strip()
    dm = _DOI_RE.search(raw)
    if dm:
        out["doi"] = dm.group(1).rstrip(".")
    return out or None


def _split_references(lines: list[_Line]) -> list[Block]:
    if not lines:
        return []
    groups: list[list[_Line]] = []
    if any(_BIB_MARKER_RE.match(ln.text.strip()) for ln in lines):
        current: list[_Line] = []
        for ln in lines:
            if _BIB_MARKER_RE.match(ln.text.strip()):
                if current:
                    groups.append(current)
                current = [ln]
            else:
                current.append(ln)
        if current:
            groups.append(current)
    else:
        # ぶら下げインデント分割(先頭行より 10pt 以上右の継続行でグルーピング)。
        base_x = min(ln.x0 for ln in lines)
        current = []
        for ln in lines:
            if current and ln.x0 <= base_x + 10.0:
                groups.append(current)
                current = [ln]
            else:
                current.append(ln)
        if current:
            groups.append(current)
    blocks: list[Block] = []
    for idx, grp in enumerate(groups, start=1):
        raw = _merge_line_texts(grp)
        m = _BIB_MARKER_RE.match(raw)
        if m:
            label = f"bib-{m.group(1)}"
            raw_text = raw[m.end() :].strip()
        else:
            label = f"bib-{idx}"
            raw_text = raw
        blocks.append(
            Block(
                id="",
                type="reference_entry",
                raw=raw_text,
                label=label,
                page=grp[0].page,
                bbox=_union_bbox(grp),
                structured=_structure_reference(raw_text),
            )
        )
    return blocks


def _collect_caption_run(
    ordered: list[_Line], start: int, line_h: float, body_size: float
) -> tuple[list[_Line], int]:
    run = [ordered[start]]
    base_x = ordered[start].x0
    j = start + 1
    while j < len(ordered):
        nxt = ordered[j]
        text = nxt.text.strip()
        if not text:
            j += 1
            continue
        if _CAPTION_RE.match(text) or _heading_info(nxt, body_size) is not None:
            break
        prev = run[-1]
        gap = nxt.y0 - prev.y1
        indent = nxt.x0 - base_x
        if nxt.page == prev.page and 0 <= gap <= 0.9 * line_h and indent <= 8.0:
            run.append(nxt)
            j += 1
        else:
            break
    return run, j


# ============================ 本体パーサ ============================


class _PdfParser:
    """1 回のパースの状態を保持する(見出しスタック・段落バッファ・警告)。"""

    def __init__(self, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes
        self.warnings: list[str] = []
        self.body_size = 10.0
        self.line_h = 12.0
        self.intro = Section(id="sec-s0", heading=SectionHeading())
        self.top_sections: list[Section] = []
        self.stack: list[tuple[int, Section]] = []
        self.current: Section = self.intro
        self._para_lines: list[_Line] = []
        self._eq_lines: list[_Line] = []
        self._ref_buffer: list[_Line] = []
        self._in_references = False
        self._pending_cross_page = False
        self._last_flushed_paragraph: Block | None = None
        self._last_flushed_lines: list[_Line] | None = None
        self._pending_images: list[tuple[Block, bytes]] = []
        self._figure_captions_total = 0
        self._figure_caption_matches = 0
        self._orphan_figures = 0
        self._table_captions_total = 0
        self._table_caption_matches = 0

    # ---- 段落 ----
    def _accumulate_paragraph_line(self, line: _Line) -> None:
        if not self._para_lines:
            self._para_lines = [line]
            return
        prev = self._para_lines[-1]
        first_x = self._para_lines[0].x0
        gap = line.y0 - prev.y1
        indent = line.x0 - first_x
        if line.page == prev.page and 0 <= gap <= 0.9 * self.line_h and indent <= 8.0:
            self._para_lines.append(line)
        else:
            self._flush_paragraph()
            self._para_lines = [line]

    def _flush_paragraph(self) -> None:
        if not self._para_lines:
            return
        first_page = self._para_lines[0].page
        same_page_lines = [ln for ln in self._para_lines if ln.page == first_page]
        text = _merge_line_texts(self._para_lines)
        block = Block(
            id="",
            type="paragraph",
            inlines=[Inline(t="text", v=text)],
            page=first_page,
            bbox=_union_bbox(same_page_lines),
        )
        self.current.blocks.append(block)
        self._last_flushed_paragraph = block
        self._last_flushed_lines = list(self._para_lines)
        self._para_lines = []

    def _maybe_continue_paragraph(self, line: _Line) -> bool:
        block = self._last_flushed_paragraph
        lines = self._last_flushed_lines
        if block is None or lines is None or not self.current.blocks:
            return False
        if self.current.blocks[-1] is not block:
            return False
        text = block.inlines[0].v if block.inlines else ""
        first_char = line.text.strip()[:1]
        if text and text[-1] not in _SENTENCE_END and first_char.islower():
            self.current.blocks.pop()
            self._para_lines = [*lines, line]
            self._last_flushed_paragraph = None
            self._last_flushed_lines = None
            return True
        return False

    # ---- 数式 ----
    def _flush_equation(self) -> None:
        if not self._eq_lines:
            return
        number: str | None = None
        for ln in reversed(self._eq_lines):
            m = _EQ_NUM_RE.search(ln.text)
            if m:
                number = m.group(1)
                break
        block = Block(
            id="",
            type="equation",
            latex=None,
            number=number,
            page=self._eq_lines[0].page,
            bbox=_union_bbox(self._eq_lines),
        )
        png = self._crop(self._page_obj, block.bbox or [])
        self._pending_images.append((block, png))
        self.current.blocks.append(block)
        self._eq_lines = []

    # ---- 見出し ----
    def _finalize_references_if_needed(self) -> None:
        if self._in_references and self._ref_buffer:
            self.current.blocks.extend(_split_references(self._ref_buffer))
        self._ref_buffer = []
        self._in_references = False

    def _make_path(self, number: str, title: str, siblings: list[Section]) -> str:
        fixed_paths = {
            "abstract": "abstract",
            "references": "refs",
            "acknowledgments": "ack",
            "acknowledgements": "ack",
            "appendix": "appendix",
        }
        if not number and title.lower() in fixed_paths:
            base = fixed_paths[title.lower()]
        elif number:
            base = re.sub(r"[^0-9A-Za-z-]", "", number.replace(".", "-"))
        else:
            base = f"s{len(siblings) + 1}"
        existing = {s.id for s in siblings}
        path = base
        n = 2
        while f"sec-{path}" in existing:
            path = f"{base}-{n}"
            n += 1
        return path

    def _open_heading(self, number: str, title: str, page_no: int, bbox: list[float]) -> None:
        self._flush_paragraph()
        self._flush_equation()
        self._finalize_references_if_needed()
        level = (number.count(".") + 1) if number else 1
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        parent_list = self.stack[-1][1].sections if self.stack else self.top_sections
        path = self._make_path(number, title, parent_list)
        sec = Section(id=f"sec-{path}", heading=SectionHeading(number=number, title=title))
        sec.blocks.append(
            Block(
                id="",
                type="heading",
                level=level,
                number=number or None,
                title=title,
                page=page_no,
                bbox=bbox,
            )
        )
        parent_list.append(sec)
        self.stack.append((level, sec))
        self.current = sec
        self._in_references = title.strip().lower() in ("references", "bibliography")

    # ---- 図表 ----
    def _crop(self, page: fitz.Page, bbox: list[float]) -> bytes:
        rect = fitz.Rect(*bbox)
        pix = page.get_pixmap(clip=rect, dpi=200)
        return bytes(pix.tobytes("png"))

    def _nearest_unclaimed_figure(
        self, regions: list[_Region], page_no: int, cap_bbox: list[float]
    ) -> _Region | None:
        best: _Region | None = None
        best_dist = 0.0
        for r in regions:
            if r.claimed or r.page != page_no:
                continue
            dist = cap_bbox[1] - r.bbox[3]
            if dist < -2.0 or dist > 90.0:
                continue
            if _h_overlap_ratio(cap_bbox, r.bbox) < 0.5:
                continue
            if best is None or dist < best_dist:
                best, best_dist = r, dist
        return best

    def _nearest_table_candidate(
        self, candidates: list[_TableCandidate], page_no: int, cap_bbox: list[float]
    ) -> _TableCandidate | None:
        best: _TableCandidate | None = None
        best_dist = 0.0
        for c in candidates:
            if c.claimed or c.page != page_no:
                continue
            if cap_bbox[1] >= c.bbox[3]:
                dist = cap_bbox[1] - c.bbox[3]
            elif cap_bbox[3] <= c.bbox[1]:
                dist = c.bbox[1] - cap_bbox[3]
            else:
                dist = 0.0
            if dist > 90.0:
                continue
            if _h_overlap_ratio(cap_bbox, c.bbox) < 0.5:
                continue
            if best is None or dist < best_dist:
                best, best_dist = c, dist
        return best

    def _handle_caption(
        self,
        page: fitz.Page,
        page_no: int,
        run: list[_Line],
        cap_m: re.Match[str],
        figure_regions: list[_Region],
        table_candidates: list[_TableCandidate],
    ) -> None:
        kind = "figure" if cap_m.group(1) in ("Figure", "Fig.") else "table"
        number = cap_m.group(2)
        merged = _merge_line_texts(run)
        m2 = _CAPTION_RE.match(merged)
        body = merged[m2.end() :].lstrip(" .:–—-") if m2 else merged  # noqa: RUF001
        caption_inlines = [Inline(t="text", v=body)] if body else []
        cap_bbox = _union_bbox(run)
        if kind == "figure":
            self._figure_captions_total += 1
            region = self._nearest_unclaimed_figure(figure_regions, page_no, cap_bbox)
            if region is not None:
                region.claimed = True
                block = Block(
                    id="",
                    type="figure",
                    asset_key=None,
                    caption=caption_inlines,
                    number=number,
                    page=page_no,
                    bbox=region.bbox,
                )
                self._pending_images.append((block, self._crop(page, region.bbox)))
                self.current.blocks.append(block)
                self._figure_caption_matches += 1
            else:
                self.warnings.append(f"Figure {number} の図領域が見つかりません(キャプションのみ)")
                self.current.blocks.append(
                    Block(
                        id="",
                        type="figure",
                        asset_key=None,
                        caption=caption_inlines,
                        number=number,
                        page=page_no,
                        bbox=cap_bbox,
                    )
                )
            return
        self._table_captions_total += 1
        candidate = self._nearest_table_candidate(table_candidates, page_no, cap_bbox)
        if candidate is not None:
            candidate.claimed = True
            if candidate.rows:
                block = Block(
                    id="",
                    type="table",
                    raw=_rows_to_html(candidate.rows),
                    caption=caption_inlines,
                    number=number,
                    page=page_no,
                    bbox=candidate.bbox,
                )
            else:
                block = Block(
                    id="",
                    type="table",
                    asset_key=None,
                    caption=caption_inlines,
                    number=number,
                    page=page_no,
                    bbox=candidate.bbox,
                )
                self._pending_images.append((block, self._crop(page, candidate.bbox)))
            self.current.blocks.append(block)
            self._table_caption_matches += 1
        else:
            self.warnings.append(f"Table {number} の表領域が見つかりません(キャプションのみ)")
            self.current.blocks.append(
                Block(
                    id="",
                    type="table",
                    caption=caption_inlines,
                    number=number,
                    page=page_no,
                    bbox=cap_bbox,
                )
            )

    def _attach_orphan_regions(self, page_no: int, figure_regions: list[_Region]) -> None:
        for r in figure_regions:
            if r.claimed or r.page != page_no:
                continue
            block = Block(
                id="",
                type="figure",
                asset_key=None,
                caption=[],
                number=None,
                page=page_no,
                bbox=r.bbox,
            )
            self._pending_images.append((block, self._crop(self._page_obj, r.bbox)))
            self.current.blocks.append(block)
            self._orphan_figures += 1
            self.warnings.append("キャプション無しの図領域を検出しました(番号なし)")

    # ---- ページ駆動 ----
    def _process_page(
        self,
        page: fitz.Page,
        page_no: int,
        ordered: list[_Line],
        width: float,
        figure_regions: list[_Region],
        table_candidates: list[_TableCandidate],
    ) -> None:
        self._page_obj = page
        # 表領域内側の行はセル文字列そのもの(§6.7 で HTML 化済み)なので、通常の
        # 段落読み流しには乗せない(重複を避ける)。
        ordered = [ln for ln in ordered if not _line_inside_any(ln, table_candidates)]
        xc = width / 2.0
        mid_lo = xc - 0.12 * width
        mid_hi = xc + 0.12 * width
        columns = _detect_columns(ordered, width, self.body_size)
        left_c, right_c = _column_centers(ordered, width, columns, self.body_size)

        pending_continue = self._pending_cross_page
        self._pending_cross_page = False

        i = 0
        n = len(ordered)
        while i < n:
            line = ordered[i]
            text = line.text.strip()
            if not text:
                i += 1
                continue

            heading = _heading_info(line, self.body_size)
            if heading is not None:
                number, title = heading
                self._open_heading(number, title, page_no, [line.x0, line.y0, line.x1, line.y1])
                pending_continue = False
                i += 1
                continue

            cap_m = _CAPTION_RE.match(text)
            if cap_m is not None:
                self._flush_paragraph()
                self._flush_equation()
                run, j = _collect_caption_run(ordered, i, self.line_h, self.body_size)
                self._handle_caption(page, page_no, run, cap_m, figure_regions, table_candidates)
                pending_continue = False
                i = j
                continue

            if self._in_references:
                self._ref_buffer.append(line)
                pending_continue = False
                i += 1
                continue

            if mid_lo <= line.cx <= mid_hi:
                center = xc  # 中央帯の行はページ中央基準(全幅の中央寄せ数式。§6.8)。
            else:
                center = left_c if line.cx < xc else right_c
            if _is_equation_line(line, self.body_size, center):
                self._flush_paragraph()
                self._eq_lines.append(line)
                pending_continue = False
                i += 1
                continue
            self._flush_equation()

            consumed = False
            if pending_continue:
                consumed = self._maybe_continue_paragraph(line)
                pending_continue = False
            if not consumed:
                self._accumulate_paragraph_line(line)
            i += 1

        self._flush_paragraph()
        self._flush_equation()
        self._pending_cross_page = True
        self._attach_orphan_regions(page_no, figure_regions)

    def parse(self, doc: fitz.Document) -> ParsedPdfDocument:
        n_pages = doc.page_count
        if n_pages == 0 or _count_extractable_chars(doc) < 40 * n_pages:
            raise PdfParseError("no_text_layer", "テキストが抽出できません")

        pages_lines: list[list[_Line]] = []
        page_sizes: list[tuple[float, float]] = []
        for i in range(n_pages):
            page = doc[i]
            pages_lines.append(_extract_page_lines(page, i + 1))
            page_sizes.append((page.rect.width, page.rect.height))

        self.body_size, self.line_h = _compute_body_metrics(pages_lines)
        _remove_headers_footers(pages_lines, [h for _, h in page_sizes])

        column_counts: list[int] = []
        for i in range(n_pages):
            page = doc[i]
            page_no = i + 1
            width, _height = page_sizes[i]
            ordered, columns = _reading_order(pages_lines[i], width, self.body_size)
            column_counts.append(columns)
            figure_regions = _detect_figure_regions(page, page_no)
            table_candidates = _detect_table_candidates(page, page_no, self._pdf_bytes)
            self._process_page(page, page_no, ordered, width, figure_regions, table_candidates)

        self._flush_paragraph()
        self._flush_equation()
        self._finalize_references_if_needed()

        sections: list[Section] = []
        if self.intro.blocks or self.intro.sections:
            sections.append(self.intro)
        sections.extend(self.top_sections)
        assign_block_ids(sections)

        figure_images = {blk.id: png for blk, png in self._pending_images}
        blocks = _iter_blocks(sections)
        stats = {
            "pages": n_pages,
            "figures": self._figure_captions_total + self._orphan_figures,
            "tables": self._table_captions_total,
            "blocks": len(blocks),
            "columns": Counter(column_counts).most_common(1)[0][0] if column_counts else 1,
            "pdf_sync_rate": None,
            "figure_caption_match_rate": (
                self._figure_caption_matches / self._figure_captions_total
                if self._figure_captions_total
                else 1.0
            ),
            "equation_latex_rate": 0.0,
        }
        return ParsedPdfDocument(
            sections=sections, warnings=self.warnings, stats=stats, figure_images=figure_images
        )


def parse_pdf(data: bytes) -> ParsedPdfDocument:
    """PDF バイト列を構造化ドキュメントへパースする(品質 B。plans/05 §6)。"""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return _PdfParser(data).parse(doc)
    finally:
        doc.close()
