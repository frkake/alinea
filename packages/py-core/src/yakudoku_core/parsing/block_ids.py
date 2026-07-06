"""ブロック安定 ID 生成(docs/01 §4.3・plans/05 §4.4)。

document/stable_id.py の `derive_block_id` / `content_hash` を再利用し(重複定義しない)、
セクションツリー全体へ決定的な `blk-` ID を付与する。内容ハッシュ(xxhash64 16 桁)は
translation_units.source_hash と同一関数で、リビジョン間 carryover の一致判定に使う。
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from yakudoku_core.document.blocks import Block, Section
from yakudoku_core.document.plaintext import block_to_plain
from yakudoku_core.document.stable_id import content_hash, derive_block_id

_WS = re.compile(r"\s+")
_INT = re.compile(r"^\d+$")


def normalize_for_hash(text: str) -> str:
    """ハッシュ入力の正規化(NFKC + 連続空白の圧縮 + 前後空白除去)。"""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def content_basis(block: Block) -> str:
    """内容ハッシュ・ID 生成の基底テキスト(種別ごと。plans/05 §4.4)。

    平文導出 `block_to_plain` を再利用する。図表は同キャプションでも
    アセットが異なれば別ブロックとするため asset_key を付す。
    """
    base = block_to_plain(block)
    if block.type in ("figure", "table") and block.asset_key:
        base = f"{base}|{block.asset_key}"
    return base


def block_source_hash(block: Block) -> str:
    """ブロック内容の安定ハッシュ(xxhash64, 16 桁 hex)。

    translation_units.source_hash と同一値であり carryover の一次キーになる。
    """
    return content_hash(normalize_for_hash(content_basis(block)))


def _section_path(section: Section) -> str:
    """Section.id(`sec-<path>`)から番号パスを取り出す。"""
    return section.id.removeprefix("sec-")


def _ordinal(block: Block, counters: dict[str, int], code: str) -> int:
    """ID 末尾序数。番号付き equation/figure/table は番号、他はセクション内出現順。"""
    if block.type in ("equation", "figure", "table") and block.number and _INT.match(block.number):
        return int(block.number)
    counters[code] += 1
    return counters[code]


def assign_block_ids(sections: list[Section]) -> None:
    """セクションツリー全体へ決定的なブロック安定 ID を付与する(破壊的)。

    docs/01 §4.4 の例(`blk-3-p2-a1f9` / `blk-3-eq5-77c2`)と同形式。
    同一 revision 内で衝突した場合のみ末尾に `-2`, `-3` … を付す(§4.4 の一意性検査)。
    """
    for section in sections:
        path = _section_path(section)
        counters: dict[str, int] = defaultdict(int)
        seen: set[str] = set()
        for block in section.blocks:
            basis = content_basis(block)
            # derive_block_id 内の _TYPE_CODE で種別コードが決まる(重複定義しない)
            base_id = derive_block_id(
                section_idx=path,
                para_idx=_ordinal(block, counters, block.type),
                content=basis,
                block_type=block.type,
            )
            block_id = base_id
            suffix = 2
            while block_id in seen:
                block_id = f"{base_id}-{suffix}"
                suffix += 1
            seen.add(block_id)
            block.id = block_id
        # 入れ子セクションへ再帰
        assign_block_ids(section.sections)
