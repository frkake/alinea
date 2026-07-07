"""根拠実在検証と表示表記の導出(plans/07 §2.5、docs/05 §5)。

- ``verify_evidence``: 根拠アンカー列を、実在する block_id のものだけに絞る(P1 忠実性)。
- ``EvidenceValidator``: リビジョン単位で ``block_search_index`` を一括ロードし、SSE 中は辞書引き
  のみで ``resolve()`` / ``display_for()`` を返す(DB を叩かない)。
- ``derive_display``: ``AnchorRef.display`` を ``block_search_index`` から決定的に導出(§2.5.2)。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# §2.5.2: paragraph_ordinal を持ちうる(¶ 表記の対象)ブロック種別。
_PARAGRAPH_LIKE = frozenset({"paragraph", "list", "quote", "theorem", "algorithm", "footnote"})
# 1 メッセージの根拠上限(§2.5.1。プロンプトは 20、検証側は +4 の余裕)。
MAX_EVIDENCE = 24


async def verify_evidence(
    anchors: Sequence[Mapping[str, Any]],
    existing_block_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """根拠アンカーのうち実在する block_id のものだけを残す(docs/05 §5・P1)。

    実在しない参照はチップごと除去する(黙って壊れた根拠を表示しない)。
    """
    existing = set(existing_block_ids)
    return [dict(a) for a in anchors if a.get("block_id") in existing]


@dataclass(frozen=True)
class BlockRow:
    block_id: str
    block_type: str
    section_path: str
    section_label: str
    paragraph_ordinal: int | None
    element_label: str | None


def derive_display(row: BlockRow, *, context_chip: bool = False) -> str:
    """block_search_index の 1 行から短縮表記を決定的に導出する(§2.5.2)。

    - equation/figure/table: element_label(例 `式(5)` / `図2` / `表1`)
    - paragraph 系(paragraph_ordinal あり): `{section_label} ¶{paragraph_ordinal}`
    - heading / セクション参照: section_label
    - context_chip=True かつ element_label あり: `{element_label} · {section_label}`(1a)
    """
    if context_chip and row.element_label:
        return f"{row.element_label} · {row.section_label}"
    if row.block_type == "equation" and row.element_label:
        return row.element_label
    if row.block_type in ("figure", "table") and row.element_label:
        return row.element_label
    if row.block_type in _PARAGRAPH_LIKE and row.paragraph_ordinal is not None:
        return f"{row.section_label} ¶{row.paragraph_ordinal}"
    return row.section_label


class EvidenceValidator:
    """リビジョン単位で block_search_index をロードし、根拠を辞書引きで検証する。"""

    def __init__(self, revision_id: str, rows: Sequence[BlockRow]) -> None:
        self.revision_id = revision_id
        self._by_block: dict[str, BlockRow] = {r.block_id: r for r in rows}
        # sec-… 参照検証用: section_path の各階層 ID → 直属ブロックの section_label。
        self._section_label: dict[str, str] = {}
        for r in rows:
            parts = [p for p in r.section_path.split("/") if p]
            if parts:
                # 葉セクション(このブロックが直属する節)のラベルを登録。
                self._section_label.setdefault(parts[-1], r.section_label)
            for part in parts:
                self._section_label.setdefault(part, r.section_label)

    @property
    def block_ids(self) -> set[str]:
        return set(self._by_block)

    def display_for(self, block_id: str, *, context_chip: bool = False) -> str | None:
        row = self._by_block.get(block_id)
        if row is not None:
            return derive_display(row, context_chip=context_chip)
        if block_id in self._section_label:
            return self._section_label[block_id]
        return None

    def resolve(self, block_id: str) -> dict[str, Any] | None:
        """モデル出力の ``[[evidence:ID]]`` を AnchorRef(dict)へ解決する(§2.4 の validator)。

        実在しない ID は None(呼び出し側がトークンごと除去する)。
        """
        row = self._by_block.get(block_id)
        if row is not None:
            return {
                "revision_id": self.revision_id,
                "block_id": block_id,
                "start": None,
                "end": None,
                "quote": None,
                "side": "source",
                "display": derive_display(row),
            }
        # sec-… はセクション見出しブロックへの参照(start/end/quote = null)。
        if block_id in self._section_label:
            return {
                "revision_id": self.revision_id,
                "block_id": block_id,
                "start": None,
                "end": None,
                "quote": None,
                "side": "source",
                "display": self._section_label[block_id],
            }
        return None

    def with_display(
        self, anchor: Mapping[str, Any], *, context_chip: bool = False
    ) -> dict[str, Any]:
        """保存済み AnchorJson に display を付けて AnchorRef(dict)にする(GET 用)。"""
        block_id = str(anchor.get("block_id", ""))
        display = self.display_for(block_id, context_chip=context_chip) or ""
        return {
            "revision_id": anchor.get("revision_id", self.revision_id),
            "block_id": block_id,
            "start": anchor.get("start"),
            "end": anchor.get("end"),
            "quote": anchor.get("quote"),
            "side": anchor.get("side", "source"),
            "display": display,
        }


_LOAD_SQL = text(
    "SELECT block_id, block_type, section_path, section_label, paragraph_ordinal, element_label "
    "FROM block_search_index WHERE revision_id = CAST(:rid AS uuid) ORDER BY position"
)


async def load_validator(session: AsyncSession, revision_id: str) -> EvidenceValidator:
    """block_search_index を revision 単位で一括ロードし Validator を作る(§2.5.1)。"""
    result = await session.execute(_LOAD_SQL, {"rid": revision_id})
    rows = [
        BlockRow(
            block_id=r.block_id,
            block_type=r.block_type,
            section_path=r.section_path,
            section_label=r.section_label,
            paragraph_ordinal=r.paragraph_ordinal,
            element_label=r.element_label,
        )
        for r in result
    ]
    return EvidenceValidator(revision_id, rows)


__all__ = [
    "MAX_EVIDENCE",
    "BlockRow",
    "EvidenceValidator",
    "derive_display",
    "load_validator",
    "verify_evidence",
]
