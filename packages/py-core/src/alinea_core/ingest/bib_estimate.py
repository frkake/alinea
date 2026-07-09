"""アップロード PDF の書誌推定(plans/05 §9.3)。structuring 段(worker)で実行し、
``papers`` を UPDATE する材料(:class:`BibEstimate`)を返す。

手順: ① PyMuPDF `doc.metadata` の title/author → ② 1 ページ目上部 40% のフォント
最大行群をタイトル候補、その直下〜Abstract までを著者候補 → ③ DOI/arXiv ID 検出 →
④ DOI があれば Crossref で補完(``bib_estimated=false``)。Crossref 呼び出しは注入可能
(既定は httpx。テストは決定的なフェイクに差し替える。外部実通信はテストで発生させない)。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF
import httpx

from alinea_core.arxiv.ids import parse_arxiv_url

_WS = re.compile(r"\s+")
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
_ARXIV_HINT_RE = re.compile(r"arXiv:\s*([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.IGNORECASE)
_ABSTRACT_RE = re.compile(r"^\s*abstract\b", re.IGNORECASE)
_UNTITLED_RE = re.compile(r"^\s*(untitled)\s*$", re.IGNORECASE)
_MAX_AUTHOR_LINES = 3
_MAX_AUTHOR_NAME_LEN = 60

# Crossref User-Agent(plans/05 §9.3 の逐語)。
CROSSREF_USER_AGENT = "alinea/1.0 (mailto:contact@alinea.app)"

CrossrefFetch = Callable[[str], Awaitable[dict[str, Any] | None]]


@dataclass
class BibEstimate:
    """``papers`` UPDATE 用の推定書誌(§9.3)。"""

    title: str | None = None
    authors: list[dict[str, str]] = field(default_factory=list)
    doi: str | None = None
    arxiv_id: str | None = None
    published_on: str | None = None  # ISO 8601 date (YYYY-MM-DD)
    venue: str | None = None
    bib_estimated: bool = True


@dataclass
class _PageLine:
    y0: float
    y1: float
    size: float
    text: str


def _clean_metadata_str(value: str | None) -> str | None:
    if not value:
        return None
    v = _WS.sub(" ", value).strip()
    if not v or _UNTITLED_RE.match(v):
        return None
    return v


def _extract_first_page_lines(page: fitz.Page) -> list[_PageLine]:
    raw = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
    out: list[_PageLine] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = _WS.sub(" ", "".join(str(s.get("text", "")) for s in spans)).strip()
            if not text:
                continue
            y0 = min(float(s["bbox"][1]) for s in spans)
            y1 = max(float(s["bbox"][3]) for s in spans)
            size = max(float(s.get("size", 0.0)) for s in spans)
            out.append(_PageLine(y0=y0, y1=y1, size=size, text=text))
    return out


def _split_author_names(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for name in re.split(r",| and | & ", text):
        cleaned = name.strip(" .")
        if cleaned and len(cleaned) <= _MAX_AUTHOR_NAME_LEN:
            out.append({"name": cleaned})
    return out


def _guess_title_authors(
    lines: list[_PageLine], page_height: float
) -> tuple[str | None, list[dict[str, str]]]:
    """1 ページ目上部 40% のフォント最大行群 = タイトル候補、その下 = 著者候補(§9.3-2)。"""
    band = sorted((ln for ln in lines if ln.y0 <= page_height * 0.4), key=lambda ln: ln.y0)
    if not band:
        return None, []
    max_size = max(ln.size for ln in band)
    title_lines = [ln for ln in band if ln.size >= max_size - 0.5]
    title = _WS.sub(" ", " ".join(ln.text for ln in title_lines)).strip() or None

    title_bottom = title_lines[-1].y1 if title_lines else -1.0
    author_lines: list[_PageLine] = []
    for ln in band:
        if ln in title_lines or ln.y0 < title_bottom:
            continue
        if _ABSTRACT_RE.match(ln.text):
            break
        author_lines.append(ln)
        if len(author_lines) >= _MAX_AUTHOR_LINES:
            break
    authors: list[dict[str, str]] = []
    for ln in author_lines:
        authors.extend(_split_author_names(ln.text))
    return title, authors


def _detect_doi_and_arxiv(text: str) -> tuple[str | None, str | None]:
    doi: str | None = None
    m = _DOI_RE.search(text)
    if m:
        doi = m.group().rstrip(".,;)")
    arxiv_id: str | None = None
    am = _ARXIV_HINT_RE.search(text)
    if am:
        ref = parse_arxiv_url(f"arXiv:{am.group(1)}")
        if ref is not None:
            arxiv_id = ref.id
    return doi, arxiv_id


async def _default_crossref_fetch(doi: str) -> dict[str, Any] | None:
    url = f"https://api.crossref.org/works/{doi}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": CROSSREF_USER_AGENT})
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        payload = resp.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        return message if isinstance(message, dict) else None


def _authors_from_crossref(message: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for a in message.get("author", []) or []:
        given = str(a.get("given", "")).strip()
        family = str(a.get("family", "")).strip()
        name = f"{given} {family}".strip()
        if name:
            out.append({"name": name})
    return out


def _published_on_from_crossref(message: dict[str, Any]) -> str | None:
    issued = message.get("issued") or {}
    parts = issued.get("date-parts") or []
    if not parts or not parts[0]:
        return None
    p = parts[0]
    if not p:
        return None
    year = int(p[0])
    month = int(p[1]) if len(p) > 1 else 1
    day = int(p[2]) if len(p) > 2 else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def _title_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    base = filename.rsplit("/", 1)[-1]
    if base.lower().endswith(".pdf"):
        base = base[: -len(".pdf")]
    return base or None


async def estimate_bibliography(
    data: bytes,
    *,
    filename: str | None = None,
    crossref_fetch: CrossrefFetch | None = None,
) -> BibEstimate:
    """PDF から書誌を推定する(§9.3)。structuring 段で ``papers`` UPDATE に使う材料を返す。"""
    fetch = crossref_fetch or _default_crossref_fetch
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        meta = doc.metadata or {}
        result = BibEstimate(
            title=_clean_metadata_str(meta.get("title")),
        )
        authors_meta = _clean_metadata_str(meta.get("author"))
        if authors_meta:
            result.authors = _split_author_names(authors_meta)

        first_page_text = ""
        if doc.page_count:
            first_lines = _extract_first_page_lines(doc[0])
            first_page_text = "\n".join(ln.text for ln in first_lines)
            if not result.title:
                guess_title, guess_authors = _guess_title_authors(first_lines, doc[0].rect.height)
                result.title = guess_title
                if not result.authors:
                    result.authors = guess_authors
            if doc.page_count > 1:
                more = "\n".join(ln.text for ln in _extract_first_page_lines(doc[1]))
                first_page_text = f"{first_page_text}\n{more}"

        if not result.title:
            result.title = _title_from_filename(filename)

        doi, arxiv_id = _detect_doi_and_arxiv(first_page_text)
        result.doi = doi
        result.arxiv_id = arxiv_id

        if doi:
            message = await fetch(doi)
            if message:
                result.bib_estimated = False
                crossref_title = message.get("title") or []
                if crossref_title:
                    result.title = str(crossref_title[0])
                crossref_authors = _authors_from_crossref(message)
                if crossref_authors:
                    result.authors = crossref_authors
                published = _published_on_from_crossref(message)
                if published:
                    result.published_on = published
                venue = message.get("container-title") or []
                if venue:
                    result.venue = str(venue[0])
                result.doi = str(message.get("DOI") or doi)
        return result
    finally:
        doc.close()
