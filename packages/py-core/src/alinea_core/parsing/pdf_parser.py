"""PDF パイプライン(品質 B。plans/05 §6)。

PyMuPDF(fitz)を主、表セル抽出のみ pdfplumber を併用する(spec-decisions C7)。
`parser_version='pdf-1.2.4'` / `source_format='pdf'` / `quality_level='B'`。
数値はすべて pt(1/72 インチ、PyMuPDF の既定単位のまま)。

処理順は §6 の節番号のとおり: 6.1 抽出 → 6.2 ヘッダ/フッタ除去 → 6.3 段組み判定・
読み順復元 → 6.4 段落組み立て → 6.5 見出し検出 → 6.6 図 → 6.7 表 → 6.8 数式 →
6.9 参考文献 → 6.10 stats。

出力の中間表現は既存の `alinea_core.document`(Block/Section/Inline)を再利用し、
ブロック安定 ID も `parsing.block_ids.assign_block_ids` を再利用する(重複定義しない)。
"""

from __future__ import annotations

import functools
import math
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import fitz  # PyMuPDF

from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.parsing.block_ids import assign_block_ids
from alinea_core.text_safety import sanitize_untrusted_text

PARSER_VERSION = "pdf-1.2.4"
MAX_PDF_PAGES = 2_000
MAX_PDF_EXTRACTED_CHARS = 20_000_000
MAX_PDF_LAYOUT_BLOCKS = 200_000
MAX_PDF_LAYOUT_LINES = 500_000
MAX_PDF_LAYOUT_SPANS = 1_000_000
MAX_PDF_STRUCTURED_BLOCKS = 200_000
MAX_PDF_SECTIONS = 20_000
MAX_PDF_PAGE_DIMENSION = 20_000.0
MAX_PDF_PAGE_AREA = 100_000_000.0
MAX_PDF_FIGURE_IMAGES = 200
MAX_PDF_SINGLE_FIGURE_BYTES = 32 * 1024 * 1024
MAX_PDF_FIGURE_BYTES = 128 * 1024 * 1024
MAX_PDF_VECTOR_DRAWINGS_PER_PAGE = 10_000
MAX_PDF_GRAPHICS_STREAM_BYTES_PER_PAGE = 512 * 1024
MAX_PDF_GRAPHICS_STREAM_REFS_PER_PAGE = 4_096

_WS = re.compile(r"\s+")
_OCR_LANGUAGE_RE = re.compile(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*\Z")

# --- 例外 -----------------------------------------------------------------------


class PdfParseError(Exception):
    """PDF パース失敗(§6.1 の non-retryable 分類。plans/05 §2.4 の kind と揃える)。"""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class PdfTextEvidence:
    """Bounded visible-text evidence used by candidate completeness checks."""

    text: str
    pages: int
    extracted_chars: int


@dataclass(frozen=True)
class PdfTextEvidenceCounts:
    """Count-only PDF text evidence safe to transfer across process boundaries."""

    pages: int
    extracted_chars: int


@dataclass(frozen=True)
class PdfOcrReadiness:
    """Non-fatal availability report for the optional PDF OCR capability."""

    available: bool
    code: str
    language: str

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "available": self.available,
            "code": self.code,
            "language": self.language,
        }


@functools.lru_cache(maxsize=8)
def check_pdf_ocr_readiness(
    *,
    language: str = "eng",
    timeout_s: float = 2.0,
) -> PdfOcrReadiness:
    """Probe the Tesseract executable and requested traineddata without raising."""

    try:
        if not sys.platform.startswith("linux"):
            return PdfOcrReadiness(False, "ocr_platform_unsupported", language)
        binary = shutil.which("tesseract")
        if binary is None:
            return PdfOcrReadiness(False, "ocr_engine_unavailable", language)
        completed = subprocess.run(  # noqa: S603 - resolved executable, fixed argument
            [binary, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            shell=False,
        )
        if not isinstance(completed.returncode, int):
            return PdfOcrReadiness(False, "ocr_readiness_failed", language)
        if completed.returncode != 0:
            return PdfOcrReadiness(False, "ocr_readiness_failed", language)
        if not isinstance(completed.stdout, str) or not isinstance(completed.stderr, str):
            return PdfOcrReadiness(False, "ocr_readiness_failed", language)
        available_languages = {
            line.strip()
            for line in f"{completed.stdout}\n{completed.stderr}".splitlines()
            if line.strip()
        }
        required_languages = {item for item in language.split("+") if item}
        if not required_languages or not required_languages.issubset(available_languages):
            return PdfOcrReadiness(False, "ocr_language_unavailable", language)
        return PdfOcrReadiness(True, "ready", language)
    except subprocess.TimeoutExpired:
        return PdfOcrReadiness(False, "ocr_readiness_timeout", language)
    except OSError:
        return PdfOcrReadiness(False, "ocr_engine_unavailable", language)
    except Exception:
        return PdfOcrReadiness(False, "ocr_readiness_failed", language)


def clear_pdf_ocr_readiness_cache() -> None:
    """Reset the process-local readiness cache (tests and controlled reloads)."""

    check_pdf_ocr_readiness.cache_clear()


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
    """図候補領域(ラスター画像またはベクター描画の連結成分。§6.6.1)。"""

    page: int
    bbox: list[float]
    claimed: bool = False
    from_scan_background: bool = False
    from_vector_graphics: bool = False


@dataclass
class _ScanBackground:
    """OCR ページを覆う走査ラスター。意味的な図候補には直接しない。"""

    page: int
    bbox: list[float]


@dataclass
class _TableCandidate:
    """表候補(§6.7。`find_tables()` 由来のセル構造つき領域)。"""

    page: int
    bbox: list[float]
    rows: list[list[Any]] | None
    claimed: bool = False
    from_scan_background: bool = False


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


def _open_pdf(data: bytes) -> fitz.Document:
    try:
        return fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise PdfParseError("pdf_open_error", "PDFを開けません") from exc


def _validate_pdf_page_count(doc: fitz.Document) -> int:
    try:
        pages = int(doc.page_count)
    except Exception as exc:
        raise PdfParseError("pdf_page_limit", "PDFページ数を検証できません") from exc
    if pages > MAX_PDF_PAGES:
        raise PdfParseError("pdf_page_limit", "PDFページ数が上限を超えています")
    return pages


def _validate_pdf_page_geometry(page: fitz.Page) -> tuple[float, float]:
    try:
        width = float(page.rect.width)
        height = float(page.rect.height)
    except Exception as exc:
        raise PdfParseError("pdf_geometry_limit", "PDFページ寸法を検証できません") from exc
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0
        or height <= 0
        or width > MAX_PDF_PAGE_DIMENSION
        or height > MAX_PDF_PAGE_DIMENSION
        or width * height > MAX_PDF_PAGE_AREA
    ):
        raise PdfParseError("pdf_geometry_limit", "PDFページ寸法が上限を超えています")
    return width, height


def _extract_bounded_page_text(page: fitz.Page, *, sort: bool = False) -> str:
    _validate_pdf_page_geometry(page)
    try:
        return str(page.get_text("text", sort=sort))
    except (RuntimeError, TypeError, ValueError) as exc:
        raise PdfParseError("pdf_text_error", "PDFテキストを抽出できません") from exc


def _count_extractable_chars(doc: fitz.Document) -> int:
    _validate_pdf_page_count(doc)
    total = 0
    for page in doc:
        total += len(_extract_bounded_page_text(page).strip())
        if total > MAX_PDF_EXTRACTED_CHARS:
            raise PdfParseError("pdf_text_limit", "PDF抽出テキストが上限を超えています")
    return total


def extract_pdf_text_evidence(data: bytes) -> PdfTextEvidence:
    """Extract bounded PDF text once, with counts shared by completeness and parsing."""

    doc = _open_pdf(data)
    try:
        pages = _validate_pdf_page_count(doc)
        text_pages: list[str] = []
        extracted_chars = 0
        retained_chars = 0
        for page in doc:
            text = _extract_bounded_page_text(page)
            extracted_chars += len(text.strip())
            retained_chars += len(text)
            if (
                extracted_chars > MAX_PDF_EXTRACTED_CHARS
                or retained_chars > MAX_PDF_EXTRACTED_CHARS
            ):
                raise PdfParseError("pdf_text_limit", "PDF抽出テキストが上限を超えています")
            text_pages.append(text)
        return PdfTextEvidence(
            text="\n".join(text_pages),
            pages=pages,
            extracted_chars=extracted_chars,
        )
    finally:
        doc.close()


def count_pdf_text_evidence(data: bytes) -> PdfTextEvidenceCounts:
    """Count bounded visible text without retaining or returning page contents."""

    doc = _open_pdf(data)
    try:
        pages = _validate_pdf_page_count(doc)
        return PdfTextEvidenceCounts(
            pages=pages,
            extracted_chars=_count_extractable_chars(doc),
        )
    finally:
        doc.close()


def check_text_layer(data: bytes) -> None:
    """テキストレイヤ判定(§6.1)。抽出文字数 < 40 x ページ数 なら :class:`PdfParseError`。

    worker の bounded evidence / parser 経路と同じページ・文字数上限を適用する。
    core の軽量呼び出し用であり、レイアウト dict は抽出しない。
    """
    doc = _open_pdf(data)
    try:
        n_pages = _validate_pdf_page_count(doc)
        if n_pages == 0 or _count_extractable_chars(doc) < 40 * n_pages:
            raise PdfParseError("no_text_layer", "テキストが抽出できません")
    finally:
        doc.close()


@dataclass
class _PdfLayoutBudget:
    blocks: int = 0
    lines: int = 0
    spans: int = 0

    def charge(self, *, blocks: int = 0, lines: int = 0, spans: int = 0) -> None:
        self.blocks += blocks
        self.lines += lines
        self.spans += spans
        if (
            self.blocks > MAX_PDF_LAYOUT_BLOCKS
            or self.lines > MAX_PDF_LAYOUT_LINES
            or self.spans > MAX_PDF_LAYOUT_SPANS
        ):
            raise PdfParseError("pdf_layout_limit", "PDFレイアウト要素が上限を超えています")


def _extract_page_lines(
    page: fitz.Page,
    page_no: int,
    *,
    textpage: fitz.TextPage | None = None,
    budget: _PdfLayoutBudget | None = None,
) -> list[_Line]:
    if textpage is None:
        raw = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    else:
        raw = page.get_text(
            "dict",
            flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES,
            textpage=textpage,
        )
    raw_blocks = raw.get("blocks", [])
    if not isinstance(raw_blocks, list):
        raise PdfParseError("pdf_layout_limit", "PDFレイアウトが不正です")
    if budget is not None:
        budget.charge(blocks=len(raw_blocks))
    lines: list[_Line] = []
    for block in raw_blocks:
        if block.get("type") != 0:
            continue
        raw_lines = block.get("lines", [])
        if not isinstance(raw_lines, list):
            raise PdfParseError("pdf_layout_limit", "PDFレイアウトが不正です")
        if budget is not None:
            budget.charge(lines=len(raw_lines))
        for line in raw_lines:
            spans = line.get("spans", [])
            if not isinstance(spans, list):
                raise PdfParseError("pdf_layout_limit", "PDFレイアウトが不正です")
            if budget is not None:
                budget.charge(spans=len(spans))
            if not spans:
                continue
            text = sanitize_untrusted_text("".join(str(s.get("text", "")) for s in spans))
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


def _extract_ocr_page(
    page: fitz.Page,
    page_no: int,
    *,
    language: str,
    budget: _PdfLayoutBudget | None = None,
) -> tuple[int, list[_Line]]:
    """Extract one OCR page and release its heavyweight TextPage before returning."""

    textpage = page.get_textpage_ocr(language=language, dpi=200, full=True)
    try:
        extractable_chars = len(page.get_text(textpage=textpage).strip())
        lines = _extract_page_lines(page, page_no, textpage=textpage, budget=budget)
        return extractable_chars, lines
    finally:
        del textpage


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
    if not text:
        return None
    key = re.sub(r"[.:\s]+$", "", text).strip().lower()
    fixed = _FIXED_HEADINGS.get(key)
    if fixed:
        return "", fixed
    if not _font_ok(line, body_size):
        return None
    m = _HEADING_SPLIT_RE.match(text)
    if m:
        number = m.group("num") or m.group("letter") or ""
        title = m.group("title").strip()
        return number, title
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


def _page_has_complex_graphics_stream(page: fitz.Page) -> bool:
    """Bound optional geometry walks using cheap compressed stream lengths."""

    try:
        refs = [int(xref) for xref in page.get_contents()]
        refs.extend(int(item[0]) for item in page.get_xobjects())
        unique_refs = set(refs)
        if len(unique_refs) > MAX_PDF_GRAPHICS_STREAM_REFS_PER_PAGE:
            return True
        total = 0
        for xref in unique_refs:
            kind, raw_length = page.parent.xref_get_key(xref, "Length")
            if kind != "int":
                continue
            length = int(raw_length)
            if length < 0:
                return True
            total += length
            if total > MAX_PDF_GRAPHICS_STREAM_BYTES_PER_PAGE:
                return True
    except Exception:
        # Figure/table geometry is optional. Unknown stream metadata must not
        # expose the parser to an unbounded third-party graphics walk.
        return True
    return False


def _detect_figure_regions(
    page: fitz.Page,
    page_no: int,
    *,
    inspect_graphics: bool = True,
) -> list[_Region]:
    if not inspect_graphics:
        return []
    try:
        # Only bounding boxes are consumed below. Resolving xrefs forces PyMuPDF
        # to materialize and hash every image pixmap, which can exhaust memory on
        # image-heavy papers without improving figure-region detection.
        infos = page.get_image_info()
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
    regions = [
        _Region(page=page_no, bbox=[round(v, 2) for v in b])
        for b in clustered
        if _area(b) >= 1600.0
    ]

    # TikZ / PGF and many charting tools emit only PDF drawing commands, so
    # ``get_image_info`` cannot see them.  Cluster their path bounds as
    # caption-match candidates.  They are deliberately not eligible for the
    # captionless-figure fallback: a vector table, rule, or page decoration is
    # otherwise indistinguishable from an uncaptioned figure.
    try:
        drawings = page.get_drawings()
    except (RuntimeError, TypeError, ValueError):
        drawings = []
    if len(drawings) > MAX_PDF_VECTOR_DRAWINGS_PER_PAGE:
        return regions

    vector_boxes: list[tuple[float, float, float, float]] = []
    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None:
            continue
        try:
            box = tuple(float(value) for value in rect)
        except (TypeError, ValueError):
            continue
        if len(box) != 4 or not all(math.isfinite(value) for value in box):
            continue
        if box[0] > box[2] or box[1] > box[3]:
            continue
        vector_boxes.append((box[0], box[1], box[2], box[3]))

    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    page_area = page_width * page_height
    for box in _cluster_boxes(vector_boxes, gap=12.0):
        width = box[2] - box[0]
        height = box[3] - box[1]
        area = _area(box)
        if (
            width < max(36.0, page_width * 0.12)
            or height < 24.0
            or area < 1600.0
            or area > page_area * 0.58
            or width > page_width * 0.96
            or height > page_height * 0.80
        ):
            continue
        rounded = [round(value, 2) for value in box]
        if any(_bbox_iou(rounded, region.bbox) >= 0.80 for region in regions):
            continue
        regions.append(
            _Region(
                page=page_no,
                bbox=rounded,
                from_vector_graphics=True,
            )
        )
    return regions


def _clip_bbox_to_page(bbox: list[float], page_width: float, page_height: float) -> list[float]:
    return [
        min(max(0.0, bbox[0]), page_width),
        min(max(0.0, bbox[1]), page_height),
        min(max(0.0, bbox[2]), page_width),
        min(max(0.0, bbox[3]), page_height),
    ]


def _grid_coverage_ratio(regions: list[_Region], *, page_width: float, page_height: float) -> float:
    """Approximate union coverage with a fixed grid, keeping tiled detection bounded."""

    columns = 30
    rows = 40
    cell_width = page_width / columns
    cell_height = page_height / rows
    covered: set[int] = set()
    for region in regions:
        x0, y0, x1, y1 = _clip_bbox_to_page(region.bbox, page_width, page_height)
        first_column = max(0, math.ceil(x0 / cell_width - 0.5))
        last_column = min(columns - 1, math.floor(x1 / cell_width - 0.5))
        first_row = max(0, math.ceil(y0 / cell_height - 0.5))
        last_row = min(rows - 1, math.floor(y1 / cell_height - 0.5))
        for row in range(first_row, last_row + 1):
            offset = row * columns
            for column in range(first_column, last_column + 1):
                covered.add(offset + column)
    return len(covered) / (columns * rows)


def _contains_page_ocr(bbox: list[float], lines: list[_Line]) -> bool:
    visible_lines = [line for line in lines if line.text.strip()]
    total_chars = sum(len(line.text.strip()) for line in visible_lines)
    contained_lines = [
        line
        for line in visible_lines
        if bbox[0] <= line.cx <= bbox[2] and bbox[1] <= (line.y0 + line.y1) / 2.0 <= bbox[3]
    ]
    contained_chars = sum(len(line.text.strip()) for line in contained_lines)
    return (
        total_chars > 0
        and contained_chars * 100 >= total_chars * 80
        and (len(contained_lines) >= 2 or contained_chars >= 40)
    )


def _has_inset_scan_geometry(bbox: list[float], *, page_width: float, page_height: float) -> bool:
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    page_area = page_width * page_height
    return (
        width / page_width >= 0.65
        and height / page_height >= 0.85
        and width * height / page_area >= 0.65
    )


def _partition_ocr_scan_background_regions(
    regions: list[_Region],
    *,
    page_width: float,
    page_height: float,
    lines: list[_Line],
) -> tuple[list[_Region], list[_ScanBackground]]:
    """Separate page-covering scan geometry from semantic image regions."""

    page_area = page_width * page_height
    semantic_regions: list[_Region] = []
    backgrounds: list[_ScanBackground] = []
    for region in regions:
        bbox = _clip_bbox_to_page(region.bbox, page_width, page_height)
        width = max(0.0, bbox[2] - bbox[0])
        height = max(0.0, bbox[3] - bbox[1])
        dominant_geometry = (
            width / page_width >= 0.78
            and height / page_height >= 0.68
            and width * height / page_area >= 0.55
        )
        if dominant_geometry or (
            _has_inset_scan_geometry(
                bbox,
                page_width=page_width,
                page_height=page_height,
            )
            and _contains_page_ocr(bbox, lines)
        ):
            backgrounds.append(_ScanBackground(page=region.page, bbox=bbox))
        else:
            semantic_regions.append(region)

    if len(semantic_regions) < 2:
        return semantic_regions, backgrounds

    first_bbox = semantic_regions[0].bbox
    union = (first_bbox[0], first_bbox[1], first_bbox[2], first_bbox[3])
    for region in semantic_regions[1:]:
        bbox = region.bbox
        union = _union_box(union, (bbox[0], bbox[1], bbox[2], bbox[3]))
    union_bbox = _clip_bbox_to_page(list(union), page_width, page_height)
    union_width = union_bbox[2] - union_bbox[0]
    union_height = union_bbox[3] - union_bbox[1]
    coverage = _grid_coverage_ratio(
        semantic_regions,
        page_width=page_width,
        page_height=page_height,
    )
    if union_width / page_width >= 0.78 and union_height / page_height >= 0.68 and coverage >= 0.52:
        backgrounds.append(_ScanBackground(page=semantic_regions[0].page, bbox=union_bbox))
        semantic_regions = []
    return semantic_regions, backgrounds


def _bbox_iou(a: list[float], b: list[float]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    a_area = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    b_area = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = a_area + b_area - intersection
    return intersection / union if union > 0 else 0.0


def _normalized_bbox(bbox: list[float], *, page_width: float, page_height: float) -> list[float]:
    return [
        bbox[0] / page_width,
        bbox[1] / page_height,
        bbox[2] / page_width,
        bbox[3] / page_height,
    ]


def _scan_profiles_match(candidate: list[float], confirmed: list[float]) -> bool:
    edge_delta = max(abs(left - right) for left, right in zip(candidate, confirmed, strict=True))
    return edge_delta <= 0.035 and _bbox_iou(candidate, confirmed) >= 0.90


def _partition_ocr_document_scan_regions(
    regions_by_page: list[list[_Region]],
    *,
    page_sizes: list[tuple[float, float]],
    pages_lines: list[list[_Line]],
) -> list[tuple[list[_Region], list[_ScanBackground]]]:
    """Infer OCR-confirmed scan profiles before inheriting them across all pages."""

    partitions: list[tuple[list[_Region], list[_ScanBackground]]] = []
    confirmed_profiles: list[list[float]] = []
    for regions, (page_width, page_height), lines in zip(
        regions_by_page,
        page_sizes,
        pages_lines,
        strict=True,
    ):
        partitions.append(
            _partition_ocr_scan_background_regions(
                regions,
                page_width=page_width,
                page_height=page_height,
                lines=lines,
            )
        )
        for region in regions:
            bbox = _clip_bbox_to_page(region.bbox, page_width, page_height)
            if _has_inset_scan_geometry(
                bbox,
                page_width=page_width,
                page_height=page_height,
            ) and _contains_page_ocr(bbox, lines):
                confirmed_profiles.append(
                    _normalized_bbox(
                        bbox,
                        page_width=page_width,
                        page_height=page_height,
                    )
                )

    if not confirmed_profiles:
        return partitions

    inherited_partitions: list[tuple[list[_Region], list[_ScanBackground]]] = []
    for (semantic_regions, backgrounds), (page_width, page_height) in zip(
        partitions,
        page_sizes,
        strict=True,
    ):
        retained: list[_Region] = []
        for region in semantic_regions:
            bbox = _clip_bbox_to_page(region.bbox, page_width, page_height)
            profile = _normalized_bbox(
                bbox,
                page_width=page_width,
                page_height=page_height,
            )
            if _has_inset_scan_geometry(
                bbox,
                page_width=page_width,
                page_height=page_height,
            ) and any(_scan_profiles_match(profile, confirmed) for confirmed in confirmed_profiles):
                backgrounds.append(_ScanBackground(page=region.page, bbox=bbox))
            else:
                retained.append(region)
        inherited_partitions.append((retained, backgrounds))
    return inherited_partitions


def _scan_crop_x_bounds(
    caption_bbox: list[float],
    background_bbox: list[float],
    lines: list[_Line],
    *,
    page_width: float,
    columns: int,
    body_size: float,
) -> tuple[float, float]:
    """Return the usable page or inferred column width around a caption."""

    page_center = page_width / 2.0
    caption_center = (caption_bbox[0] + caption_bbox[2]) / 2.0
    center_band = 0.12 * page_width
    crosses_center = (
        caption_bbox[0] <= page_center <= caption_bbox[2]
        or abs(caption_center - page_center) <= center_band
    )
    if columns != 2 or crosses_center:
        return background_bbox[0], background_bbox[2]

    left_lines, right_lines = _body_column_lines(lines, page_width, body_size)
    split = page_center
    if left_lines and right_lines:
        left_edge = max(line.x1 for line in left_lines)
        right_edge = min(line.x0 for line in right_lines)
        if left_edge < right_edge:
            split = (left_edge + right_edge) / 2.0
    if caption_center < page_center:
        return background_bbox[0], min(background_bbox[2], split)
    return max(background_bbox[0], split), background_bbox[2]


def _is_scan_gap_boundary(line: _Line, *, body_size: float) -> bool:
    text = _WS.sub(" ", line.text).strip()
    if not text:
        return False
    return _CAPTION_RE.match(text) is not None or _heading_info(line, body_size) is not None


def _scan_gap_candidates(
    caption_run: list[_Line],
    lines: list[_Line],
    background: _ScanBackground,
    *,
    page_width: float,
    line_height: float,
    columns: int,
    body_size: float,
    force_full_width: bool = False,
) -> list[tuple[str, list[float]]]:
    caption_bbox = _union_bbox(caption_run)
    if force_full_width:
        x0, x1 = background.bbox[0], background.bbox[2]
    else:
        x0, x1 = _scan_crop_x_bounds(
            caption_bbox,
            background.bbox,
            lines,
            page_width=page_width,
            columns=columns,
            body_size=body_size,
        )
    horizontal_bbox = [x0, background.bbox[1], x1, background.bbox[3]]
    excluded = {id(line) for line in caption_run}
    padding = max(2.0, min(6.0, line_height * 0.25))
    above_y0 = background.bbox[1] + padding
    below_y1 = background.bbox[3] - padding
    for line in lines:
        if id(line) in excluded:
            continue
        line_bbox = [line.x0, line.y0, line.x1, line.y1]
        if _h_overlap_ratio(horizontal_bbox, line_bbox) < 0.15:
            continue
        if not _is_scan_gap_boundary(line, body_size=body_size):
            continue
        if line.y1 <= caption_bbox[1]:
            above_y0 = max(above_y0, line.y1 + padding)
        if line.y0 >= caption_bbox[3]:
            below_y1 = min(below_y1, line.y0 - padding)

    minimum_height = max(24.0, line_height * 2.0)
    candidates: list[tuple[str, list[float]]] = []
    above_y1 = min(background.bbox[3], caption_bbox[1] - padding)
    if above_y1 - above_y0 >= minimum_height:
        candidates.append(("above", [x0, above_y0, x1, above_y1]))
    below_y0 = max(background.bbox[1], caption_bbox[3] + padding)
    if below_y1 - below_y0 >= minimum_height:
        candidates.append(("below", [x0, below_y0, x1, below_y1]))
    return candidates


def _projection_clusters(indices: list[int], *, maximum_gap: int) -> list[tuple[int, int]]:
    if not indices:
        return []
    clusters: list[tuple[int, int]] = []
    start = indices[0]
    previous = start
    for value in indices[1:]:
        if value - previous > maximum_gap:
            clusters.append((start, previous))
            start = value
        previous = value
    clusters.append((start, previous))
    return clusters


def _visual_content_crop(
    page: fitz.Page,
    candidate_bbox: list[float],
    *,
    page_width: float,
    page_height: float,
    target_x: float,
    target_y: float,
    masked_text_bboxes: list[list[float]],
    allow_component_union: bool,
) -> tuple[float, list[float]] | None:
    """Select a connected non-paper extent nearest the caption target."""

    rect = fitz.Rect(*candidate_bbox)
    if rect.width <= 0 or rect.height <= 0:
        return None
    scale = min(1.0, math.sqrt(160_000.0 / max(1.0, rect.get_area())))
    try:
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            clip=rect,
            colorspace=fitz.csGRAY,
            alpha=False,
        )
        width = int(pixmap.width)
        height = int(pixmap.height)
        stride = int(pixmap.stride)
        samples = pixmap.samples
    except Exception:
        return None
    if width < 2 or height < 2 or not samples:
        return None

    masked_pixels = bytearray(width * height)
    for text_bbox in masked_text_bboxes:
        intersection = fitz.Rect(*text_bbox) & rect
        if intersection.is_empty:
            continue
        first_column = max(
            0,
            math.floor((intersection.x0 - rect.x0) / rect.width * width) - 1,
        )
        last_column = min(
            width,
            math.ceil((intersection.x1 - rect.x0) / rect.width * width) + 1,
        )
        first_row = max(
            0,
            math.floor((intersection.y0 - rect.y0) / rect.height * height) - 1,
        )
        last_row = min(
            height,
            math.ceil((intersection.y1 - rect.y0) / rect.height * height) + 1,
        )
        masked_width = last_column - first_column
        if masked_width <= 0:
            continue
        masked_run = b"\x01" * masked_width
        for row in range(first_row, last_row):
            offset = row * width + first_column
            masked_pixels[offset : offset + masked_width] = masked_run

    values = [samples[row * stride + column] for row in range(height) for column in range(width)]
    ordered_values = sorted(values)
    paper_level = ordered_values[round(0.90 * (len(ordered_values) - 1))]
    contrast = paper_level - ordered_values[0]
    if contrast < 24:
        return None
    dark_threshold = paper_level - max(18, round(contrast * 0.15))
    dark_by_row = [0] * height
    dark_by_column = [0] * width
    dark_pixels = 0
    dark_points: list[tuple[int, int]] = []
    for row in range(height):
        row_offset = row * stride
        for column in range(width):
            if (
                not masked_pixels[row * width + column]
                and samples[row_offset + column] <= dark_threshold
            ):
                dark_pixels += 1
                dark_by_row[row] += 1
                dark_by_column[column] += 1
                dark_points.append((column, row))
    if dark_pixels < max(16, round(width * height * 0.002)):
        return None

    active_rows = [row for row, count in enumerate(dark_by_row) if count >= max(2, width // 250)]
    active_columns = [
        column for column, count in enumerate(dark_by_column) if count >= max(2, height // 250)
    ]
    if not active_rows or not active_columns:
        return None

    x_clusters = _projection_clusters(
        active_columns,
        maximum_gap=max(6, round(width * 0.04)),
    )
    y_clusters = _projection_clusters(
        active_rows,
        maximum_gap=max(4, round(height * 0.025)),
    )
    x_membership = [-1] * width
    y_membership = [-1] * height
    for cluster_index, (start, end) in enumerate(x_clusters):
        for column in range(start, end + 1):
            x_membership[column] = cluster_index
    for cluster_index, (start, end) in enumerate(y_clusters):
        for row in range(start, end + 1):
            y_membership[row] = cluster_index

    component_points: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for column, row in dark_points:
        x_cluster = x_membership[column]
        y_cluster = y_membership[row]
        if x_cluster >= 0 and y_cluster >= 0:
            component_points.setdefault((x_cluster, y_cluster), []).append((column, row))

    page_area = page_width * page_height
    components: list[tuple[float, list[float], float]] = []
    padding = 4.0
    for points in component_points.values():
        minimum_points = max(16, round(width * height * 0.002))
        if len(points) < minimum_points:
            continue
        min_column = min(point[0] for point in points)
        max_column = max(point[0] for point in points)
        min_row = min(point[1] for point in points)
        max_row = max(point[1] for point in points)
        content_bbox = [
            max(rect.x0, rect.x0 + min_column / width * rect.width - padding),
            max(rect.y0, rect.y0 + min_row / height * rect.height - padding),
            min(
                rect.x1,
                rect.x0 + (max_column + 1) / width * rect.width + padding,
            ),
            min(
                rect.y1,
                rect.y0 + (max_row + 1) / height * rect.height + padding,
            ),
        ]
        content_width = content_bbox[2] - content_bbox[0]
        content_height = content_bbox[3] - content_bbox[1]
        content_area = content_width * content_height
        if (
            content_width < max(36.0, page_width * 0.12)
            or content_height < 24.0
            or content_area < 1600.0
            or content_area > page_area * 0.58
            or content_width > page_width * 0.96
            or content_height > page_height * 0.80
        ):
            continue
        if target_x < content_bbox[0]:
            horizontal_distance = content_bbox[0] - target_x
        elif target_x > content_bbox[2]:
            horizontal_distance = target_x - content_bbox[2]
        else:
            horizontal_distance = 0.0
        if target_y < content_bbox[1]:
            vertical_distance = content_bbox[1] - target_y
        elif target_y > content_bbox[3]:
            vertical_distance = target_y - content_bbox[3]
        else:
            vertical_distance = 0.0
        distance_penalty = (
            1.0 + 4.0 * horizontal_distance / page_width + 1.5 * vertical_distance / page_height
        )
        score = contrast * (len(points) + content_area * 0.04) / distance_penalty
        rounded_bbox = [round(value, 2) for value in content_bbox]
        components.append((score, rounded_bbox, content_area))
    if not components:
        return None

    primary = max(components, key=lambda item: item[0])
    if not allow_component_union:
        return primary[0], primary[1]

    grouped_score = primary[0]
    grouped_bbox = list(primary[1])
    primary_area = primary[2]
    search_width = candidate_bbox[2] - candidate_bbox[0]

    def overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
        overlap = max(0.0, min(a1, b1) - max(a0, b0))
        narrower = min(a1 - a0, b1 - b0)
        return overlap / narrower if narrower > 0 else 0.0

    def axis_gap(a0: float, a1: float, b0: float, b1: float) -> float:
        return max(0.0, max(a0, b0) - min(a1, b1))

    for score, bbox, area in sorted(components, key=lambda item: item[0], reverse=True):
        if bbox == primary[1]:
            continue
        if score < primary[0] * 0.12 or area < primary_area * 0.12:
            continue
        horizontal_gap = axis_gap(grouped_bbox[0], grouped_bbox[2], bbox[0], bbox[2])
        vertical_gap = axis_gap(grouped_bbox[1], grouped_bbox[3], bbox[1], bbox[3])
        aligned_side_by_side = overlap_ratio(
            grouped_bbox[1], grouped_bbox[3], bbox[1], bbox[3]
        ) >= 0.55 and horizontal_gap <= max(18.0, search_width * 0.18)
        aligned_stacked = overlap_ratio(
            grouped_bbox[0], grouped_bbox[2], bbox[0], bbox[2]
        ) >= 0.55 and vertical_gap <= max(18.0, page_height * 0.10)
        if not aligned_side_by_side and not aligned_stacked:
            continue
        union_bbox = [
            min(grouped_bbox[0], bbox[0]),
            min(grouped_bbox[1], bbox[1]),
            max(grouped_bbox[2], bbox[2]),
            max(grouped_bbox[3], bbox[3]),
        ]
        union_width = union_bbox[2] - union_bbox[0]
        union_height = union_bbox[3] - union_bbox[1]
        union_area = union_width * union_height
        if (
            union_area > page_area * 0.58
            or union_width > page_width * 0.96
            or union_height > page_height * 0.80
        ):
            continue
        grouped_bbox = union_bbox
        grouped_score += score
    return grouped_score, [round(value, 2) for value in grouped_bbox]


def _has_competing_display_caption(
    lines: list[_Line],
    caption_run: list[_Line],
    caption_bbox: list[float],
    *,
    line_height: float,
) -> bool:
    current_ids = {id(line) for line in caption_run}
    caption_center_y = (caption_bbox[1] + caption_bbox[3]) / 2.0
    maximum_distance = max(24.0, line_height * 4.0)
    for line in lines:
        if id(line) in current_ids:
            continue
        match = _CAPTION_RE.match(line.text.strip())
        if match is None:
            continue
        line_center_y = (line.y0 + line.y1) / 2.0
        if abs(line_center_y - caption_center_y) <= maximum_distance:
            return True
    return False


def _derive_ocr_scan_display_regions(
    page: fitz.Page,
    page_no: int,
    ordered: list[_Line],
    backgrounds: list[_ScanBackground],
    *,
    page_width: float,
    page_height: float,
    line_height: float,
    body_size: float,
    columns: int,
) -> tuple[list[_Region], list[_TableCandidate]]:
    """Derive non-overlapping semantic crops without exposing scan backgrounds."""

    figure_regions: list[_Region] = []
    table_candidates: list[_TableCandidate] = []
    derived_bboxes: list[list[float]] = []
    index = 0
    while index < len(ordered):
        line = ordered[index]
        match = _CAPTION_RE.match(line.text.strip())
        if match is None:
            index += 1
            continue
        is_figure = match.group(1) in ("Figure", "Fig.")
        caption_run, next_index = _collect_caption_run(ordered, index, line_height, body_size)
        caption_bbox = _union_bbox(caption_run)
        caption_center = (caption_bbox[0] + caption_bbox[2]) / 2.0
        caption_line_ids = {id(item) for item in caption_run}
        masked_text_bboxes = [
            [item.x0, item.y0, item.x1, item.y1]
            for item in ordered
            if id(item) not in caption_line_ids
        ]
        best_above: tuple[float, list[float]] | None = None
        best_below: tuple[float, list[float]] | None = None
        for background in backgrounds:
            if background.page != page_no:
                continue
            if not (
                background.bbox[0]
                <= (caption_bbox[0] + caption_bbox[2]) / 2.0
                <= background.bbox[2]
                and background.bbox[1]
                <= (caption_bbox[1] + caption_bbox[3]) / 2.0
                <= background.bbox[3]
            ):
                continue
            for direction, gap_bbox in _scan_gap_candidates(
                caption_run,
                ordered,
                background,
                page_width=page_width,
                line_height=line_height,
                columns=columns,
                body_size=body_size,
            ):
                visual = _visual_content_crop(
                    page,
                    gap_bbox,
                    page_width=page_width,
                    page_height=page_height,
                    target_x=caption_center,
                    target_y=caption_bbox[1] if direction == "above" else caption_bbox[3],
                    masked_text_bboxes=masked_text_bboxes,
                    allow_component_union=(gap_bbox[2] - gap_bbox[0]) >= page_width * 0.75,
                )
                if visual is None:
                    continue
                score, crop_bbox = visual
                column_scoped = (gap_bbox[2] - gap_bbox[0]) < page_width * 0.75
                if column_scoped and not _has_competing_display_caption(
                    ordered,
                    caption_run,
                    caption_bbox,
                    line_height=line_height,
                ):
                    full_gap = next(
                        (
                            candidate
                            for candidate_direction, candidate in _scan_gap_candidates(
                                caption_run,
                                ordered,
                                background,
                                page_width=page_width,
                                line_height=line_height,
                                columns=columns,
                                body_size=body_size,
                                force_full_width=True,
                            )
                            if candidate_direction == direction
                        ),
                        None,
                    )
                    if full_gap is not None:
                        full_visual = _visual_content_crop(
                            page,
                            full_gap,
                            page_width=page_width,
                            page_height=page_height,
                            target_x=caption_center,
                            target_y=(caption_bbox[1] if direction == "above" else caption_bbox[3]),
                            masked_text_bboxes=masked_text_bboxes,
                            allow_component_union=True,
                        )
                        if full_visual is not None:
                            full_score, full_bbox = full_visual
                            split = (
                                gap_bbox[2] if caption_center < page_width / 2.0 else gap_bbox[0]
                            )
                            minimum_crossing = page_width * 0.08
                            crosses_split = (
                                full_bbox[0] <= split - minimum_crossing
                                and full_bbox[2] >= split + minimum_crossing
                            )
                            vertical_overlap = max(
                                0.0,
                                min(crop_bbox[3], full_bbox[3]) - max(crop_bbox[1], full_bbox[1]),
                            )
                            original_height = crop_bbox[3] - crop_bbox[1]
                            vertically_related = (
                                original_height > 0 and vertical_overlap / original_height >= 0.55
                            )
                            if crosses_split and vertically_related and full_score >= score * 1.10:
                                score, crop_bbox = full_score, full_bbox
                if any(_bbox_iou(crop_bbox, bbox) >= 0.35 for bbox in derived_bboxes):
                    continue
                selected_visual = (score, crop_bbox)
                current = best_above if direction == "above" else best_below
                if current is None or score > current[0]:
                    if direction == "above":
                        best_above = selected_visual
                    else:
                        best_below = selected_visual
        selected = best_above
        if best_below is not None and (selected is None or best_below[0] > selected[0] * 1.5):
            selected = best_below
        if selected is not None:
            derived_bboxes.append(selected[1])
            if is_figure:
                figure_regions.append(
                    _Region(
                        page=page_no,
                        bbox=selected[1],
                        from_scan_background=True,
                    )
                )
            else:
                table_candidates.append(
                    _TableCandidate(
                        page=page_no,
                        bbox=selected[1],
                        rows=None,
                        from_scan_background=True,
                    )
                )
        index = max(index + 1, next_index)
    return figure_regions, table_candidates


def _exclude_scan_display_labels(lines: list[_Line], scan_bboxes: list[list[float]]) -> list[_Line]:
    """Keep crop-internal OCR labels in the image, not structured paragraphs."""

    retained: list[_Line] = []
    for line in lines:
        if _CAPTION_RE.match(line.text.strip()) is not None:
            retained.append(line)
            continue
        center_y = (line.y0 + line.y1) / 2.0
        inside_crop = any(
            bbox[0] <= line.cx <= bbox[2] and bbox[1] <= center_y <= bbox[3] for bbox in scan_bboxes
        )
        if not inside_crop:
            retained.append(line)
    return retained


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


class _PdfPlumberTableFallback:
    """Lazily reuse one pdfplumber document and release every page layout cache."""

    def __init__(self, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes
        self._document: Any | None = None
        self._unavailable = False

    def _open(self) -> Any | None:
        if self._unavailable:
            return None
        if self._document is not None:
            return self._document
        try:
            import pdfplumber

            self._document = pdfplumber.open(BytesIO(self._pdf_bytes))
        except Exception:
            self._unavailable = True
            return None
        return self._document

    def find(self, page_no: int) -> list[_TableCandidate]:
        document = self._open()
        if document is None:
            return []
        page: Any | None = None
        out: list[_TableCandidate] = []
        try:
            page = document.pages[page_no - 1]
            for table in page.find_tables():
                rows: list[list[Any]] | None
                bbox: list[float] | None
                try:
                    rows = table.extract()
                    bbox = [round(float(value), 2) for value in table.bbox]
                except Exception:
                    rows = None
                    bbox = None
                if rows and bbox is not None:
                    out.append(_TableCandidate(page=page_no, bbox=bbox, rows=rows))
        except Exception:
            return []
        finally:
            close_page = getattr(page, "close", None)
            if callable(close_page):
                try:
                    close_page()
                except Exception:
                    self._unavailable = True
        return out

    def close(self) -> None:
        document, self._document = self._document, None
        if document is None:
            return
        try:
            document.close()
        except Exception:
            self._unavailable = True


def _detect_table_candidates(
    page: fitz.Page, page_no: int, fallback: _PdfPlumberTableFallback
) -> list[_TableCandidate]:
    out: list[_TableCandidate] = []
    finder: Any | None
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
    except Exception:
        finder = None
    if out:
        return out
    return fallback.find(page_no)


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
        same_visual_line = (
            abs(nxt.y0 - prev.y0) <= 0.35 * line_h
            and nxt.x0 >= prev.x1 - 2.0
            and nxt.x0 - prev.x1 <= max(24.0, 3.0 * line_h)
        )
        continued_line = 0 <= gap <= 0.9 * line_h and indent <= 8.0
        if nxt.page == prev.page and (same_visual_line or continued_line):
            run.append(nxt)
            j += 1
        else:
            break
    return run, j


def _infer_text_figure_region(
    caption_run: list[_Line],
    ordered: list[_Line],
    *,
    page_no: int,
    page_width: float,
    line_height: float,
) -> _Region | None:
    """Infer a caption-adjacent diagram made entirely from positioned text glyphs."""

    caption_bbox = _union_bbox(caption_run)
    page_center = page_width / 2.0
    caption_center = (caption_bbox[0] + caption_bbox[2]) / 2.0
    crosses_center = caption_bbox[0] <= page_center <= caption_bbox[2]
    if crosses_center or caption_bbox[2] - caption_bbox[0] >= page_width * 0.55:
        column_x0, column_x1 = 0.0, page_width
    elif caption_center < page_center:
        column_x0, column_x1 = 0.0, page_center
    else:
        column_x0, column_x1 = page_center, page_width

    caption_ids = {id(line) for line in caption_run}
    maximum_distance = max(120.0, line_height * 18.0)
    candidates = sorted(
        (
            line
            for line in ordered
            if id(line) not in caption_ids
            and line.page == page_no
            and column_x0 <= line.cx <= column_x1
            and line.y1 <= caption_bbox[1] - 1.0
            and caption_bbox[1] - line.y1 <= maximum_distance
        ),
        key=lambda line: (line.y1, line.x0),
        reverse=True,
    )
    selected: list[_Line] = []
    cursor_y = caption_bbox[1]
    maximum_gap = max(24.0, line_height * 2.4)
    for line in candidates:
        gap = cursor_y - line.y1
        if gap > maximum_gap:
            if selected:
                break
            continue
        selected.append(line)
        cursor_y = min(cursor_y, line.y0)
    if len(selected) < 2:
        return None

    widths = sorted(line.x1 - line.x0 for line in selected)
    median_width = widths[len(widths) // 2]
    column_width = column_x1 - column_x0
    if median_width > column_width * 0.68:
        return None
    bbox = _union_bbox(selected)
    padding = 4.0
    inferred = [
        max(column_x0, bbox[0] - padding),
        max(0.0, bbox[1] - padding),
        min(column_x1, bbox[2] + padding),
        min(caption_bbox[1] - 1.0, bbox[3] + padding),
    ]
    if (
        inferred[2] - inferred[0] < max(36.0, page_width * 0.08)
        or inferred[3] - inferred[1] < 24.0
        or _area((inferred[0], inferred[1], inferred[2], inferred[3])) < 1600.0
    ):
        return None
    return _Region(
        page=page_no,
        bbox=[round(value, 2) for value in inferred],
        from_vector_graphics=True,
    )


# ============================ 本体パーサ ============================


class _PdfParser:
    """1 回のパースの状態を保持する(見出しスタック・段落バッファ・警告)。"""

    def __init__(self, pdf_bytes: bytes, *, use_ocr: bool = False) -> None:
        self._pdf_bytes = pdf_bytes
        self._use_ocr = use_ocr
        self.warnings: list[str] = []
        self.body_size = 10.0
        self.line_h = 12.0
        self.intro = Section(id="sec-s0", heading=SectionHeading())
        self._section_ids = {self.intro.id}
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
        self._structured_blocks = 0
        self._section_count = 1
        self._pending_image_bytes = 0

    def _append_block(self, block: Block, *, section: Section | None = None) -> None:
        if self._structured_blocks >= MAX_PDF_STRUCTURED_BLOCKS:
            raise PdfParseError("pdf_block_limit", "PDF構造化ブロック数が上限を超えています")
        (section or self.current).blocks.append(block)
        self._structured_blocks += 1

    def _extend_blocks(self, blocks: list[Block], *, section: Section | None = None) -> None:
        if self._structured_blocks + len(blocks) > MAX_PDF_STRUCTURED_BLOCKS:
            raise PdfParseError("pdf_block_limit", "PDF構造化ブロック数が上限を超えています")
        (section or self.current).blocks.extend(blocks)
        self._structured_blocks += len(blocks)

    def _append_section(self, sections: list[Section], section: Section) -> None:
        if self._section_count >= MAX_PDF_SECTIONS:
            raise PdfParseError("pdf_section_limit", "PDFセクション数が上限を超えています")
        sections.append(section)
        self._section_count += 1

    def _append_pending_image(self, block: Block, payload: bytes) -> None:
        if len(self._pending_images) >= MAX_PDF_FIGURE_IMAGES:
            raise PdfParseError("pdf_figure_limit", "PDF図表数が上限を超えています")
        if (
            len(payload) > MAX_PDF_SINGLE_FIGURE_BYTES
            or self._pending_image_bytes + len(payload) > MAX_PDF_FIGURE_BYTES
        ):
            raise PdfParseError("pdf_figure_bytes_limit", "PDF図表バイト数が上限を超えています")
        self._pending_images.append((block, payload))
        self._pending_image_bytes += len(payload)

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
        self._append_block(block)
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
            self._structured_blocks -= 1
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
        self._append_pending_image(block, png)
        self._append_block(block)
        self._eq_lines = []

    # ---- 見出し ----
    def _finalize_references_if_needed(self) -> None:
        if self._in_references and self._ref_buffer:
            self._extend_blocks(_split_references(self._ref_buffer))
        self._ref_buffer = []
        self._in_references = False

    def _make_path(self, number: str, title: str, _siblings: list[Section]) -> str:
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
            base = f"s{len(self._section_ids)}"
        path = base
        n = 2
        while f"sec-{path}" in self._section_ids:
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
        self._append_block(
            Block(
                id="",
                type="heading",
                level=level,
                number=number or None,
                title=title,
                page=page_no,
                bbox=bbox,
            ),
            section=sec,
        )
        self._append_section(parent_list, sec)
        self._section_ids.add(sec.id)
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
            if not r.from_scan_background:
                dist = cap_bbox[1] - r.bbox[3]
                if dist < -2.0:
                    continue
            elif cap_bbox[1] >= r.bbox[3]:
                dist = cap_bbox[1] - r.bbox[3]
            elif cap_bbox[3] <= r.bbox[1]:
                dist = r.bbox[1] - cap_bbox[3]
            else:
                dist = 0.0
            maximum_distance = 120.0 if r.from_scan_background else 90.0
            if dist > maximum_distance:
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
            maximum_distance = 120.0 if c.from_scan_background else 90.0
            if dist > maximum_distance:
                continue
            if _h_overlap_ratio(cap_bbox, c.bbox) < 0.5:
                continue
            if best is None or dist < best_dist:
                best, best_dist = c, dist
        return best

    def _nearest_unclaimed_table_image(
        self, regions: list[_Region], page_no: int, cap_bbox: list[float]
    ) -> _Region | None:
        """Match a raster/vector table body on either side of its caption."""

        best: _Region | None = None
        best_dist = 0.0
        for region in regions:
            if region.claimed or region.page != page_no:
                continue
            if cap_bbox[3] <= region.bbox[1]:
                dist = region.bbox[1] - cap_bbox[3]
            elif region.bbox[3] <= cap_bbox[1]:
                dist = cap_bbox[1] - region.bbox[3]
            else:
                dist = 0.0
            maximum_distance = 120.0 if region.from_scan_background else 90.0
            if dist > maximum_distance or _h_overlap_ratio(cap_bbox, region.bbox) < 0.5:
                continue
            if best is None or dist < best_dist:
                best, best_dist = region, dist
        return best

    def _handle_caption(
        self,
        page: fitz.Page,
        page_no: int,
        run: list[_Line],
        cap_m: re.Match[str],
        figure_regions: list[_Region],
        table_candidates: list[_TableCandidate],
        ordered: list[_Line],
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
            if region is None:
                region = _infer_text_figure_region(
                    run,
                    ordered,
                    page_no=page_no,
                    page_width=float(page.rect.width),
                    line_height=self.line_h,
                )
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
                self._append_pending_image(block, self._crop(page, region.bbox))
                self._append_block(block)
                self._figure_caption_matches += 1
            else:
                self.warnings.append(f"Figure {number} の図領域が見つかりません(キャプションのみ)")
                self._append_block(
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
                self._append_pending_image(block, self._crop(page, candidate.bbox))
            self._append_block(block)
            self._table_caption_matches += 1
        else:
            image_region = self._nearest_unclaimed_table_image(
                figure_regions,
                page_no,
                cap_bbox,
            )
            if image_region is not None:
                image_region.claimed = True
                block = Block(
                    id="",
                    type="table",
                    asset_key=None,
                    caption=caption_inlines,
                    number=number,
                    page=page_no,
                    bbox=image_region.bbox,
                )
                self._append_pending_image(block, self._crop(page, image_region.bbox))
                self._append_block(block)
                self._table_caption_matches += 1
            else:
                self.warnings.append(f"Table {number} の表領域が見つかりません(キャプションのみ)")
                self._append_block(
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
            if r.claimed or r.page != page_no or r.from_scan_background or r.from_vector_graphics:
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
            self._append_pending_image(block, self._crop(self._page_obj, r.bbox))
            self._append_block(block)
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
                self._handle_caption(
                    page,
                    page_no,
                    run,
                    cap_m,
                    figure_regions,
                    table_candidates,
                    ordered,
                )
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

    def parse(
        self,
        doc: fitz.Document,
        *,
        ocr_language: str = "eng",
    ) -> ParsedPdfDocument:
        n_pages = _validate_pdf_page_count(doc)
        pages_lines: list[list[_Line]] = []
        page_sizes: list[tuple[float, float]] = []
        layout_budget = _PdfLayoutBudget()
        if self._use_ocr:
            extractable_chars = 0
            for i in range(n_pages):
                page = doc[i]
                page_size = _validate_pdf_page_geometry(page)
                page_chars, page_lines = _extract_ocr_page(
                    page,
                    i + 1,
                    language=ocr_language,
                    budget=layout_budget,
                )
                extractable_chars += page_chars
                if extractable_chars > MAX_PDF_EXTRACTED_CHARS:
                    raise PdfParseError("pdf_text_limit", "PDF抽出テキストが上限を超えています")
                pages_lines.append(page_lines)
                page_sizes.append(page_size)
                del page
            if n_pages == 0 or extractable_chars < 40 * n_pages:
                raise PdfParseError("no_text_layer", "テキストが抽出できません")
        else:
            extractable_chars = _count_extractable_chars(doc)
            if n_pages == 0 or extractable_chars < 40 * n_pages:
                raise PdfParseError("no_text_layer", "テキストが抽出できません")
            for i in range(n_pages):
                page = doc[i]
                page_size = _validate_pdf_page_geometry(page)
                pages_lines.append(_extract_page_lines(page, i + 1, budget=layout_budget))
                page_sizes.append(page_size)

        self.body_size, self.line_h = _compute_body_metrics(pages_lines)
        _remove_headers_footers(pages_lines, [h for _, h in page_sizes])

        complex_graphics_pages = [_page_has_complex_graphics_stream(doc[i]) for i in range(n_pages)]
        ocr_region_partitions: list[tuple[list[_Region], list[_ScanBackground]]] = []
        if self._use_ocr:
            regions_by_page = [
                _detect_figure_regions(
                    doc[i],
                    i + 1,
                    inspect_graphics=not complex_graphics_pages[i],
                )
                for i in range(n_pages)
            ]
            ocr_region_partitions = _partition_ocr_document_scan_regions(
                regions_by_page,
                page_sizes=page_sizes,
                pages_lines=pages_lines,
            )

        column_counts: list[int] = []
        table_fallback = _PdfPlumberTableFallback(self._pdf_bytes)
        try:
            for i in range(n_pages):
                page = doc[i]
                page_no = i + 1
                width, _height = page_sizes[i]
                inspect_graphics = not complex_graphics_pages[i]
                ordered, columns = _reading_order(pages_lines[i], width, self.body_size)
                column_counts.append(columns)
                scan_table_candidates: list[_TableCandidate] = []
                if self._use_ocr:
                    figure_regions, scan_backgrounds = ocr_region_partitions[i]
                    scan_figure_regions, scan_table_candidates = _derive_ocr_scan_display_regions(
                        page,
                        page_no,
                        ordered,
                        scan_backgrounds,
                        page_width=width,
                        page_height=page_sizes[i][1],
                        line_height=self.line_h,
                        body_size=self.body_size,
                        columns=columns,
                    )
                    figure_regions.extend(scan_figure_regions)
                    ordered = _exclude_scan_display_labels(
                        ordered,
                        [region.bbox for region in scan_figure_regions]
                        + [candidate.bbox for candidate in scan_table_candidates],
                    )
                else:
                    figure_regions = _detect_figure_regions(
                        page,
                        page_no,
                        inspect_graphics=inspect_graphics,
                    )
                table_candidates = (
                    _detect_table_candidates(page, page_no, table_fallback)
                    if inspect_graphics
                    else []
                )
                table_candidates.extend(scan_table_candidates)
                self._process_page(page, page_no, ordered, width, figure_regions, table_candidates)
        finally:
            table_fallback.close()

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
            "ocr": self._use_ocr,
            "extracted_chars": extractable_chars,
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


def _ocr_error(exc: Exception, *, language: str) -> PdfParseError:
    message = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timed out" in message or "timeout" in message:
        return PdfParseError("ocr_timeout", "PDF OCR timed out")
    if "tesseract" in message and (
        "not installed" in message or "not found" in message or "no tessdata" in message
    ):
        return PdfParseError("ocr_engine_unavailable", "PDF OCR engine is unavailable")
    if (
        "traineddata" in message
        or "failed loading language" in message
        or "couldn't load any languages" in message
    ):
        return PdfParseError(
            "ocr_language_unavailable",
            "PDF OCR language data is unavailable",
        )
    readiness = check_pdf_ocr_readiness(language=language)
    if readiness.code == "ocr_engine_unavailable":
        return PdfParseError("ocr_engine_unavailable", "PDF OCR engine is unavailable")
    if readiness.code == "ocr_language_unavailable":
        return PdfParseError(
            "ocr_language_unavailable",
            "PDF OCR language data is unavailable",
        )
    if readiness.code == "ocr_readiness_timeout":
        return PdfParseError("ocr_readiness_timeout", "PDF OCR readiness check timed out")
    return PdfParseError("ocr_failed", "PDF OCR failed")


def parse_pdf(
    data: bytes,
    *,
    use_ocr: bool = False,
    ocr_language: str = "eng",
) -> ParsedPdfDocument:
    """PDF バイト列を構造化ドキュメントへパースする(品質 B。plans/05 §6)。"""
    if use_ocr and not sys.platform.startswith("linux"):
        raise PdfParseError("ocr_platform_unsupported", "PDF OCR is unsupported on this platform")
    if use_ocr and (len(ocr_language) > 64 or _OCR_LANGUAGE_RE.fullmatch(ocr_language) is None):
        raise PdfParseError("ocr_language_invalid", "PDF OCR language is invalid")
    doc = _open_pdf(data)
    try:
        if not use_ocr:
            return _PdfParser(data).parse(doc)
        try:
            return _PdfParser(data, use_ocr=True).parse(
                doc,
                ocr_language=ocr_language,
            )
        except PdfParseError:
            raise
        except Exception as exc:
            raise _ocr_error(exc, language=ocr_language) from exc
    finally:
        doc.close()
