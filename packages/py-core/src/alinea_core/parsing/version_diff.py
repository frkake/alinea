"""リビジョン間ブロック差分(version diff。docs/02 §6・docs/10 M3・S10)。

同一 Paper の 2 リビジョン(v1→v2 等)を **ブロック単位**で比較し、各ブロックを
added / removed / changed / unchanged に **決定的に**分類する(LLM 不使用)。

carryover(``parsing/carryover.py``)が生成したブロック ID の一致関係を再利用する:
新版取り込み時に ``carry_over_ids`` が存続ブロックへ旧 ID を引き継ぐため、版間で残った
ブロックは同一 ``blk-...`` ID を共有する。本モジュールは両版のブロック ID 列を
``difflib.SequenceMatcher``(carryover / LaTeX↔PDF 整列と同じ stdlib)で整列し、

- 同一 ID(opcode ``equal``)→ ``block_source_hash`` を比較して unchanged / changed
- 旧側のみ(``delete`` / ``replace`` 旧側)→ removed
- 新側のみ(``insert`` / ``replace`` 新側)→ added

と分類する。carryover 未適用の版どうしを渡すと ID が総入れ替えになり全ブロックが
added+removed になる(整列不能を捏造しない安全側の縮退。P3)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from alinea_core.document.blocks import Block, DocumentContent, Section
from alinea_core.document.plaintext import block_to_plain
from alinea_core.parsing.block_ids import block_source_hash

ChangeStatus = Literal["added", "removed", "changed", "unchanged"]


@dataclass
class BlockChange:
    """1 ブロックの差分。added は old_text=None、removed は new_text=None。"""

    status: ChangeStatus
    block_id: str
    block_type: str
    section_id: str
    old_text: str | None = None
    new_text: str | None = None


@dataclass
class RevisionDiffStats:
    """差分サマリ(情報パネル「変更点」チップ用)。"""

    added: int = 0
    removed: int = 0
    changed: int = 0
    unchanged: int = 0


@dataclass
class RevisionDiff:
    """リビジョン間差分。``changes`` は added/removed/changed のみ(unchanged は件数だけ)。"""

    changes: list[BlockChange] = field(default_factory=list)
    stats: RevisionDiffStats = field(default_factory=RevisionDiffStats)


@dataclass
class _Located:
    """ブロックと所属セクション ID。"""

    block: Block
    section_id: str


def _locate_blocks(content: DocumentContent) -> list[_Located]:
    """全ブロックを (所属セクション ID つき) の文書順で平坦化する(入れ子対応)。"""
    out: list[_Located] = []

    def walk(section: Section) -> None:
        for block in section.blocks:
            out.append(_Located(block=block, section_id=section.id))
        for sub in section.sections:
            walk(sub)

    for section in content.sections:
        walk(section)
    return out


def diff_revisions(old: DocumentContent, new: DocumentContent) -> RevisionDiff:
    """2 リビジョンのブロック差分を決定的に計算する。

    carryover 済みの版では存続ブロックが同一 ID を共有する前提で、ブロック ID 列を
    ``SequenceMatcher`` で整列し opcode ごとに分類する。出力は opcode 順(新版文書順)。
    """
    old_located = _locate_blocks(old)
    new_located = _locate_blocks(new)
    old_ids = [item.block.id for item in old_located]
    new_ids = [item.block.id for item in new_located]

    diff = RevisionDiff()
    matcher = SequenceMatcher(a=old_ids, b=new_ids, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for oi, nj in zip(range(i1, i2), range(j1, j2), strict=True):
                old_item = old_located[oi]
                new_item = new_located[nj]
                if block_source_hash(old_item.block) == block_source_hash(new_item.block):
                    diff.stats.unchanged += 1
                else:
                    diff.stats.changed += 1
                    diff.changes.append(
                        BlockChange(
                            status="changed",
                            block_id=new_item.block.id,
                            block_type=str(new_item.block.type),
                            section_id=new_item.section_id,
                            old_text=block_to_plain(old_item.block),
                            new_text=block_to_plain(new_item.block),
                        )
                    )
            continue
        # replace / delete の旧側は removed、replace / insert の新側は added。
        for oi in range(i1, i2):
            old_item = old_located[oi]
            diff.stats.removed += 1
            diff.changes.append(
                BlockChange(
                    status="removed",
                    block_id=old_item.block.id,
                    block_type=str(old_item.block.type),
                    section_id=old_item.section_id,
                    old_text=block_to_plain(old_item.block),
                    new_text=None,
                )
            )
        for nj in range(j1, j2):
            new_item = new_located[nj]
            diff.stats.added += 1
            diff.changes.append(
                BlockChange(
                    status="added",
                    block_id=new_item.block.id,
                    block_type=str(new_item.block.type),
                    section_id=new_item.section_id,
                    old_text=None,
                    new_text=block_to_plain(new_item.block),
                )
            )
    return diff
