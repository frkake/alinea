"""arXiv HTML パーサと関連ユーティリティ(plans/05 §4)。

DOM → docs/01 §4 構造化ドキュメント中間表現。ブロック/インラインの Pydantic モデルと
安定 ID は `yakudoku_core.document` を再利用する(重複定義しない)。
"""

from yakudoku_core.parsing.block_ids import (
    assign_block_ids,
    block_source_hash,
    content_basis,
    normalize_for_hash,
)
from yakudoku_core.parsing.carryover import CarryOverStats, carry_over_ids, flatten_blocks
from yakudoku_core.parsing.html_parser import (
    PARSER_VERSION,
    ParsedDocument,
    parse_arxiv_html,
)
from yakudoku_core.parsing.pdf_sync import (
    BlockPosition,
    PdfSyncResult,
    PdfWord,
    sync_block_positions,
)

__all__ = [
    "PARSER_VERSION",
    "BlockPosition",
    "CarryOverStats",
    "ParsedDocument",
    "PdfSyncResult",
    "PdfWord",
    "assign_block_ids",
    "block_source_hash",
    "carry_over_ids",
    "content_basis",
    "flatten_blocks",
    "normalize_for_hash",
    "parse_arxiv_html",
    "sync_block_positions",
]
