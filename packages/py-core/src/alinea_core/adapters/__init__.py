"""他サイトアダプタ(OpenReview / ACL Anthology / PubMed。S8・docs/02-ingest.md §8)。

「検出 + メタデータ写像 + 最良ソース解決」をサイトごとに差し込む純粋コア。arXiv は既存の
:mod:`alinea_core.arxiv` パッケージが担い、本パッケージは対象外(URL 空間が重ならない)。
"""

from alinea_core.adapters.acl_anthology import AclAnthologyAdapter
from alinea_core.adapters.base import SiteAdapter, SiteMeta, SiteRef
from alinea_core.adapters.citation_meta import (
    CitationMeta,
    citation_date_to_iso,
    extract_citation_meta,
    normalize_scholar_author,
)
from alinea_core.adapters.fetch import (
    MAX_SITE_HTML_BYTES,
    MAX_SITE_PDF_BYTES,
    SiteFetchError,
    adapter_allowed_hosts,
    fetch_html,
    fetch_pdf,
    make_site_client,
)
from alinea_core.adapters.registry import (
    registered_adapters,
    resolve_adapter,
)

__all__ = [
    "MAX_SITE_HTML_BYTES",
    "MAX_SITE_PDF_BYTES",
    "AclAnthologyAdapter",
    "CitationMeta",
    "SiteAdapter",
    "SiteFetchError",
    "SiteMeta",
    "SiteRef",
    "adapter_allowed_hosts",
    "citation_date_to_iso",
    "extract_citation_meta",
    "fetch_html",
    "fetch_pdf",
    "make_site_client",
    "normalize_scholar_author",
    "registered_adapters",
    "resolve_adapter",
]
