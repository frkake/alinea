"""arXiv 解決(URL/ID 正規化・メタデータ・ライセンス・LaTeX 有無判定)。plans/05 §3。"""

from alinea_core.arxiv.fetch import (
    FetchError,
    RedisLike,
    arxiv_throttle,
    make_arxiv_client,
    probe_latex_available,
)
from alinea_core.arxiv.ids import (
    ArxivId,
    ArxivRef,
    api_query_url,
    eprint_url,
    normalize_arxiv_id,
    oai_url,
    parse_arxiv_url,
)
from alinea_core.arxiv.licenses import normalize_license_url
from alinea_core.arxiv.metadata import ArxivMeta, fetch_metadata

__all__ = [
    "ArxivId",
    "ArxivMeta",
    "ArxivRef",
    "FetchError",
    "RedisLike",
    "api_query_url",
    "arxiv_throttle",
    "eprint_url",
    "fetch_metadata",
    "make_arxiv_client",
    "normalize_arxiv_id",
    "normalize_license_url",
    "oai_url",
    "parse_arxiv_url",
    "probe_latex_available",
]
