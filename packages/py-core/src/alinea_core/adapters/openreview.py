"""OpenReview アダプタ(S8 Task 16)。

OpenReview は forum?id=X と /pdf?id=X の 2 形式のみを検出し、同一 ``SiteRef`` に正規化する。
メタデータは API2 の note JSON(``parse_note``)から取得し、note が取れなかった場合は
``citation_*`` メタ(``parse_metadata``)へフォールバックする。

本文 PDF は ``https://openreview.net/pdf?id={external_id}`` のみ(品質 B)。
403 / note 不在 → タブ内 PDF フォールバック(``pdf_url`` が ``None`` を返さず SiteMeta.pdf_url
に URL を格納する方針、実際のフォールバック判断は上位層が 403 を受けた時点で行う)。

SSRF allow-list: landing_url / pdf_url がともに openreview.net を宣言するため、
``adapter_allowed_hosts`` は ``{"openreview.net"}`` を返す。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import parse_qs, quote, urlsplit

from alinea_core.adapters.base import SiteMeta, SiteRef
from alinea_core.adapters.citation_meta import (
    citation_date_to_iso,
    extract_citation_meta,
    normalize_scholar_author,
)
from alinea_core.licenses import LicenseId

_SITE = "openreview"
_BASE = "https://openreview.net"

# /forum?id=X や /pdf?id=X を検出し、外のクエリ文字列ノイズを無視する。
_SCHEME = r"(?:https?://)?"
_HOST = r"(?:www\.)?openreview\.net"
_PATH_FORUM = r"/forum"
_PATH_PDF = r"/pdf"
_ALLOWED_PATHS = re.compile(rf"^{_SCHEME}{_HOST}(?:{_PATH_FORUM}|{_PATH_PDF})\?", re.IGNORECASE)


def _extract_id(url: str) -> str | None:
    """URL から id クエリパラメータを抽出して URL デコードする。空文字列は None 扱い。"""
    parts = urlsplit(url if "://" in url else "https://" + url)
    qs = parse_qs(parts.query, keep_blank_values=False)
    ids = qs.get("id", [])
    if not ids or not ids[0].strip():
        return None
    return ids[0]  # parse_qs が既に URL デコードするので quote 不要


# OpenReview のライセンス文字列 → LicenseId マッピング。
# API2 は "CC BY 4.0"・"CC BY-SA 4.0" 等の人間可読形式で返すことが多い。
_LICENSE_MAP: dict[str, LicenseId] = {
    "cc by 4.0": "cc-by-4.0",
    "cc-by 4.0": "cc-by-4.0",
    "cc by-sa 4.0": "cc-by-sa-4.0",
    "cc-by-sa 4.0": "cc-by-sa-4.0",
    "cc by-nc 4.0": "cc-by-nc-4.0",
    "cc-by-nc 4.0": "cc-by-nc-4.0",
    "cc by-nc-sa 4.0": "cc-by-nc-sa-4.0",
    "cc-by-nc-sa 4.0": "cc-by-nc-sa-4.0",
    "cc by-nd 4.0": "cc-by-nd-4.0",
    "cc-by-nd 4.0": "cc-by-nd-4.0",
    "cc by-nc-nd 4.0": "cc-by-nc-nd-4.0",
    "cc-by-nc-nd 4.0": "cc-by-nc-nd-4.0",
    "cc0": "cc0",
    "cc0 1.0": "cc0",
    "public domain": "cc0",
}


def _normalize_license(raw: str | None) -> LicenseId:
    """OpenReview のライセンス文字列を LicenseId に正規化する。不明は 'unknown'。"""
    if not raw:
        return "unknown"
    key = raw.strip().lower()
    # 完全一致で先に試みる。
    if key in _LICENSE_MAP:
        return _LICENSE_MAP[key]
    # 部分一致フォールバック(例: "Creative Commons Attribution 4.0" など)。
    for pattern, lid in _LICENSE_MAP.items():
        if pattern in key:
            return lid
    return "unknown"


def _ms_timestamp_to_iso(ms: int | float | None) -> str | None:
    """Unix ミリ秒タイムスタンプを ISO 日付文字列(YYYY-MM-DD)に変換する。"""
    if ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000, tz=UTC)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return None


def _get_content_value(content: Mapping[str, object], key: str) -> str | None:
    """API2 note.content[key] の値を取得する。

    API2 形式は ``{"value": "..."}`` ネストと生文字列の両方がある。
    """
    raw = content.get(key)
    if raw is None:
        return None
    if isinstance(raw, dict):
        val = raw.get("value")
        return str(val) if val is not None else None
    return str(raw)


def _get_content_list(content: Mapping[str, object], key: str) -> list[str]:
    """API2 note.content[key] のリスト値を取得する。"""
    raw = content.get(key)
    if raw is None:
        return []
    if isinstance(raw, dict):
        val = raw.get("value")
        if val is None:
            return []
        raw = val
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None]
    return []


class OpenReviewAdapter:
    """OpenReview の検出・メタ写像・URL ビルダ(:class:`SiteAdapter` 実装)。

    ``parse_note`` は API2 note JSON 直接を受け取る高品質経路。
    ``parse_metadata`` は forum landing の HTML を ``citation_*`` で読むフォールバック経路。
    ``parse_metadata_from_note_and_citation`` は両者を統合する。
    """

    site = _SITE

    # --------------------------------------------------------------------- #
    # SiteAdapter プロトコル必須メソッド
    # --------------------------------------------------------------------- #

    def match(self, url: str) -> SiteRef | None:
        """URL が OpenReview の forum/pdf ページなら ``SiteRef`` を返す。"""
        raw = url.strip()
        if not raw:
            return None
        # スキームが無い URL にも対応するため、正規化してから検査する。
        normalized = raw if "://" in raw else "https://" + raw
        if not _ALLOWED_PATHS.match(normalized):
            return None
        external_id = _extract_id(normalized)
        if not external_id:
            return None
        return SiteRef(site=_SITE, external_id=external_id)

    def landing_url(self, ref: SiteRef) -> str:
        return f"{_BASE}/forum?id={quote(ref.external_id, safe='')}"

    def pdf_url(self, ref: SiteRef) -> str | None:
        return f"{_BASE}/pdf?id={quote(ref.external_id, safe='')}"

    def parse_metadata(self, html: str, ref: SiteRef) -> SiteMeta:
        """landing HTML → ``citation_*`` フォールバック経路(API2 が使えない場合)。"""
        return self.parse_metadata_from_note_and_citation(
            note=None,
            citation_html=html,
            ref=ref,
        )

    # --------------------------------------------------------------------- #
    # OpenReview 固有の高品質経路
    # --------------------------------------------------------------------- #

    def parse_note(self, note: Mapping[str, object], ref: SiteRef) -> SiteMeta:
        """API2 note オブジェクト → :class:`SiteMeta` 写像(ネットワーク非依存)。

        ``note`` は ``GET /api2/notes?id=X`` のレスポンス ``notes[0]`` オブジェクト。
        """
        content = note.get("content") or {}
        if not isinstance(content, dict):
            content = {}

        title = _get_content_value(content, "title") or ""
        author_list = _get_content_list(content, "authors")
        abstract = _get_content_value(content, "abstract") or ""
        venue = _get_content_value(content, "venue") or _get_content_value(content, "venueid")
        license_raw = _get_content_value(content, "license")

        # pdate(公開日)を優先、なければ cdate(作成日)を使う。
        pdate = note.get("pdate")
        cdate = note.get("cdate")
        pdate_num = pdate if isinstance(pdate, int | float) else None
        cdate_num = cdate if isinstance(cdate, int | float) else None
        published_on = _ms_timestamp_to_iso(pdate_num) or _ms_timestamp_to_iso(cdate_num)

        return SiteMeta(
            site=_SITE,
            external_id=ref.external_id,
            title=title,
            authors=[{"name": a} for a in author_list if a],
            abstract=abstract,
            published_on=published_on,
            venue=venue,
            doi=None,
            pdf_url=self.pdf_url(ref),
            license=_normalize_license(license_raw),
            categories=[],
        )

    def parse_metadata_from_note_and_citation(
        self,
        *,
        note: Mapping[str, object] | None,
        citation_html: str,
        ref: SiteRef,
    ) -> SiteMeta:
        """API2 note と citation_* HTML の両方から最良のメタを合成する。

        ``note`` が ``None`` または title が空のときは ``citation_*`` HTML から読む。
        """
        if note is not None:
            note_meta = self.parse_note(note, ref)
            if note_meta.title:
                return note_meta

        # citation_* フォールバック。
        cite = extract_citation_meta(citation_html)
        return SiteMeta(
            site=_SITE,
            external_id=ref.external_id,
            title=cite.title or "",
            authors=[
                {"name": normalize_scholar_author(name)} for name in cite.authors
            ],
            abstract=cite.abstract or "",
            published_on=citation_date_to_iso(cite.publication_date),
            venue=cite.conference_title or cite.journal_title,
            doi=cite.doi,
            pdf_url=cite.pdf_url or self.pdf_url(ref),
            license="unknown",
            categories=[],
        )
