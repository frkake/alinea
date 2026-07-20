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
    fetch_note,
    fetch_pdf,
    make_site_client,
)
from alinea_core.adapters.huggingface import (
    DiscoveredResource,
    HuggingFaceAdapter,
    HuggingFaceClient,
    HuggingFaceConfig,
    HuggingFaceRef,
    arxiv_id_from_tags,
    discover_paper_resources,
    normalize_candidate_url,
    parse_huggingface_url,
)
from alinea_core.adapters.openreview import OpenReviewAdapter
from alinea_core.adapters.pubmed import (
    NcbiClient,
    NcbiConfig,
    PmcAdapter,
    PubMedAdapter,
    ncbi_throttle,
    ncbi_throttle_interval_ms,
    normalize_pmcid,
    normalize_pmid,
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
    "DiscoveredResource",
    "HuggingFaceAdapter",
    "HuggingFaceClient",
    "HuggingFaceConfig",
    "HuggingFaceRef",
    "NcbiClient",
    "NcbiConfig",
    "OpenReviewAdapter",
    "PmcAdapter",
    "PubMedAdapter",
    "SiteAdapter",
    "SiteFetchError",
    "SiteMeta",
    "SiteRef",
    "adapter_allowed_hosts",
    "arxiv_id_from_tags",
    "citation_date_to_iso",
    "discover_paper_resources",
    "extract_citation_meta",
    "fetch_html",
    "fetch_note",
    "fetch_pdf",
    "make_site_client",
    "ncbi_throttle",
    "ncbi_throttle_interval_ms",
    "normalize_candidate_url",
    "normalize_pmcid",
    "normalize_pmid",
    "normalize_scholar_author",
    "parse_huggingface_url",
    "registered_adapters",
    "resolve_adapter",
]
