"""ACL Anthology アダプタ(S8 の最初の縦スライス)。

静的サイト・認証なし・URL→PDF/bib が完全に予測可能。landing ページが Highwire/Scholar の
``citation_*`` メタを綺麗に出すため、共通の :mod:`.citation_meta` をそのまま写像すれば済む。
本文は PDF のみ(品質 B)。
"""

from __future__ import annotations

import re

from alinea_core.adapters.base import SiteMeta, SiteRef
from alinea_core.adapters.citation_meta import (
    citation_date_to_iso,
    extract_citation_meta,
    normalize_scholar_author,
)

_SITE = "acl_anthology"
_BASE = "https://aclanthology.org"

# 論文 ID: 現行 ``YYYY.venue-type.NNN`` と旧式 ``[A-Z]\d{2}-\d{4}``(例 P19-1001)。
_ID = r"(?P<id>\d{4}\.[a-z0-9]+-[a-z0-9]+\.\d+|[A-Z]\d{2}-\d{4})"
_SCHEME = r"(?:https?://)?"
_HOST = r"(?:www\.)?aclanthology\.org"
_TAIL = r"(?:[?#].*)?"

# 論文ページのみ検出する。/volumes/ /events/ などの集約ページは除外。
_PATTERN = re.compile(rf"^{_SCHEME}{_HOST}/{_ID}(?:\.pdf|/)?{_TAIL}$")


class AclAnthologyAdapter:
    """ACL Anthology の検出・メタ写像・URL ビルダ(:class:`SiteAdapter` 実装)。"""

    site = _SITE

    def match(self, url: str) -> SiteRef | None:
        raw = url.strip()
        if not raw:
            return None
        hit = _PATTERN.match(raw)
        if hit is None:
            return None
        return SiteRef(site=_SITE, external_id=hit.group("id"))

    def landing_url(self, ref: SiteRef) -> str:
        return f"{_BASE}/{ref.external_id}/"

    def pdf_url(self, ref: SiteRef) -> str | None:
        return f"{_BASE}/{ref.external_id}.pdf"

    def parse_metadata(self, html: str, ref: SiteRef) -> SiteMeta:
        cite = extract_citation_meta(html)
        return SiteMeta(
            site=_SITE,
            external_id=ref.external_id,
            title=cite.title or "",
            authors=[{"name": normalize_scholar_author(name)} for name in cite.authors],
            abstract=cite.abstract or "",
            published_on=citation_date_to_iso(cite.publication_date),
            venue=cite.conference_title or cite.journal_title,
            doi=cite.doi,
            # citation_pdf_url があればそれを優先し、無ければ規約 URL を組み立てる。
            pdf_url=cite.pdf_url or self.pdf_url(ref),
            license="unknown",
            categories=[],
        )
