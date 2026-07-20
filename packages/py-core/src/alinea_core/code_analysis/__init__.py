"""GitHub コード対応解析の共有ドメインコア(§5-§9)。

- :mod:`contracts` — 型・LLM スキーマ・費用見積り・サーバー検証(実バイト照合)。
- :mod:`archive` — 固定 commit tar の安全な境界付き抽出(実行しない)。
- :mod:`chunks` — tree-sitter による symbol 境界 chunk 化(行窓フォールバック)。
- :mod:`claims` — 論文からの主張抽出(block anchor 付き)。
- :mod:`retrieval` — lexical retrieval + 埋め込み再順位付け。
"""

from alinea_core.code_analysis.archive import (
    ArchiveError,
    ExtractedRepo,
    extract_repository,
    is_secret_file,
    is_target_code_file,
)
from alinea_core.code_analysis.chunks import (
    MAX_CHUNK_LINES,
    CodeChunk,
    chunk_repository,
    chunk_source,
    tree_sitter_available,
)
from alinea_core.code_analysis.claims import extract_claims
from alinea_core.code_analysis.contracts import (
    ANALYSIS_VERSION,
    CHUNKS_PER_CLAIM,
    CODE_CORRESPONDENCE_SCHEMA_NAME,
    CODE_CORRESPONDENCE_SCHEMA_SPEC,
    CONFIDENCE_VALUES,
    ESTIMATE_TTL_SECONDS,
    EXCERPT_MAX_CHARS,
    MAX_CLAIMS,
    AnalysisClaim,
    ClaimSet,
    Correspondence,
    CostEstimate,
    ModelPricing,
    estimate_tokens_and_cost,
    expires_at,
    idempotency_key,
    verify_correspondences,
)
from alinea_core.code_analysis.retrieval import (
    RankedChunk,
    lexical_candidates,
    rerank_with_embeddings,
)
from alinea_core.code_analysis.stale import (
    mark_runs_stale_for_new_commit,
    mark_runs_stale_for_new_revision,
)

__all__ = [
    "ANALYSIS_VERSION",
    "CHUNKS_PER_CLAIM",
    "CODE_CORRESPONDENCE_SCHEMA_NAME",
    "CODE_CORRESPONDENCE_SCHEMA_SPEC",
    "CONFIDENCE_VALUES",
    "ESTIMATE_TTL_SECONDS",
    "EXCERPT_MAX_CHARS",
    "MAX_CHUNK_LINES",
    "MAX_CLAIMS",
    "AnalysisClaim",
    "ArchiveError",
    "ClaimSet",
    "CodeChunk",
    "Correspondence",
    "CostEstimate",
    "ExtractedRepo",
    "ModelPricing",
    "RankedChunk",
    "chunk_repository",
    "chunk_source",
    "estimate_tokens_and_cost",
    "expires_at",
    "extract_claims",
    "extract_repository",
    "idempotency_key",
    "is_secret_file",
    "is_target_code_file",
    "lexical_candidates",
    "mark_runs_stale_for_new_commit",
    "mark_runs_stale_for_new_revision",
    "rerank_with_embeddings",
    "tree_sitter_available",
    "verify_correspondences",
]
