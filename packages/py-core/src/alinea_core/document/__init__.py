"""構造化ドキュメント中間表現(docs/01 §4)。全パーサの出力・全機能の入力仕様。"""

from alinea_core.document.anchor import AnchorJson
from alinea_core.document.blocks import (
    BLOCK_TYPES,
    Block,
    DocumentContent,
    Section,
)
from alinea_core.document.inlines import INLINE_TYPES, Inline
from alinea_core.document.stable_id import derive_block_id

__all__ = [
    "BLOCK_TYPES",
    "INLINE_TYPES",
    "AnchorJson",
    "Block",
    "DocumentContent",
    "Inline",
    "Section",
    "derive_block_id",
]
