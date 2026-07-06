"""ブロック安定 ID の決定的生成(docs/01 §4.3)。

生成規則: セクションパス + ブロック種別 + セクション内出現順 + 内容ハッシュ(先頭64bit)。
同一入力からは常に同一 ID を返す(決定的)。リビジョン間 carryover の一致判定に使う。
docs/01 §4.4 の例に合わせ、`blk-<section-order>-<typecode><ordinal>-<hash16>` 形。
"""

from __future__ import annotations

import xxhash

# 種別 → 短縮コード(ID を読みやすくするため。docs/01 例の p=paragraph, eq=equation に整合)
_TYPE_CODE = {
    "paragraph": "p",
    "heading": "h",
    "figure": "fig",
    "table": "tbl",
    "equation": "eq",
    "code": "code",
    "list": "li",
    "quote": "q",
    "theorem": "thm",
    "algorithm": "alg",
    "footnote": "fn",
    "reference_entry": "ref",
}


def content_hash(content: str) -> str:
    """内容ハッシュの先頭 64bit を 16 桁 hex で返す(xxhash64)。"""
    return f"{xxhash.xxh64(content.encode('utf-8')).intdigest():016x}"


def derive_block_id(
    *,
    section_idx: int | str,
    para_idx: int,
    content: str,
    block_type: str = "paragraph",
) -> str:
    """決定的なブロック安定 ID を生成する。

    Args:
        section_idx: セクション番号/パス(例 3 や "sec-2-2")。
        para_idx: セクション内でのそのブロックの出現順(0 起点)。
        content: ブロックの正規化テキスト(ハッシュ入力)。
        block_type: docs/01 §4.1 の種別。ID の種別コードに使う。

    Returns:
        `blk-<section>-<typecode><ordinal>-<hash16>` 形の安定 ID。
    """
    code = _TYPE_CODE.get(block_type, "b")
    h = content_hash(f"{section_idx}|{block_type}|{para_idx}|{content}")
    # hash の先頭 4 桁を短縮識別子に(docs/01 例: blk-3-p2-a1f9)
    return f"blk-{section_idx}-{code}{para_idx}-{h[:4]}"
