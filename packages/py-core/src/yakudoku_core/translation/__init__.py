"""翻訳パイプラインのコアロジック(plans/06)。

M0-16 ではプレースホルダプロトコル(:mod:`yakudoku_core.translation.placeholder`)のみを
公開する。プロンプト・用語集・品質検査・パイプライン駆動は後続タスク(M0-17 以降)で追加する。
"""

from yakudoku_core.translation.placeholder import (
    BRACKET_RE,
    TOKEN_RE,
    EncodedBlock,
    PlaceholderMismatchError,
    TokenEntry,
    VerifyResult,
    compute_source_hash,
    decode_translation,
    encode_block,
    protect,
    restore,
    validate,
    verify_tokens,
)

__all__ = [
    "BRACKET_RE",
    "TOKEN_RE",
    "EncodedBlock",
    "PlaceholderMismatchError",
    "TokenEntry",
    "VerifyResult",
    "compute_source_hash",
    "decode_translation",
    "encode_block",
    "protect",
    "restore",
    "validate",
    "verify_tokens",
]
