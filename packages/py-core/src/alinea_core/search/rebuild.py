"""block_search_index の再構築(plans/11 §9 フック#1)。

document_revisions.content(JSONB)から派生テーブル block_search_index を
revision 単位で DELETE→INSERT する。DB トリガは使わず、呼び出し側の Tx 内で実行する。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_core.document.blocks import Block, DocumentContent, Section
from alinea_core.document.plaintext import block_to_plain

# 段落系(paragraph_ordinal を振るブロック種別)
_PARAGRAPH_LIKE = {"paragraph", "quote", "theorem", "algorithm", "footnote", "list"}
# 自動翻訳スコープに入る種別(plans/06 §2.1。equation/code/reference_entry は対象外)
_TRANSLATABLE = {
    "paragraph",
    "heading",
    "figure",
    "table",
    "list",
    "quote",
    "theorem",
    "algorithm",
    "footnote",
}


@dataclass
class BlockIndexRow:
    block_id: str
    block_type: str
    section_path: str
    section_label: str
    paragraph_ordinal: int | None
    element_label: str | None
    position: int
    source_text: str
    in_translation_scope: bool
    page: int | None
    bbox: list[float] | None


def _element_label(block: Block) -> str | None:
    """式(5)・図2・表1 等の表示ラベルを導出する。"""
    if block.type == "equation" and block.number:
        return f"式({block.number})"
    if block.type == "figure" and block.number:
        return f"図{block.number}"
    if block.type == "table" and block.number:
        return f"表{block.number}"
    return None


def compute_index_rows(content: DocumentContent) -> list[BlockIndexRow]:
    """DocumentContent から block_search_index の行を決定的に構築する。"""
    rows: list[BlockIndexRow] = []
    position = 0

    def section_label(sec: Section) -> str:
        num = sec.heading.number
        return f"§{num}" if num else (sec.heading.title or sec.id)

    def walk(sec: Section, path_parts: list[str]) -> None:
        nonlocal position
        path = "/".join([*path_parts, sec.id])
        label = section_label(sec)
        para_ordinal = 0
        for blk in sec.blocks:
            is_para = blk.type in _PARAGRAPH_LIKE
            ordinal: int | None = None
            if is_para:
                para_ordinal += 1
                ordinal = para_ordinal
            rows.append(
                BlockIndexRow(
                    block_id=blk.id,
                    block_type=blk.type,
                    section_path=path,
                    section_label=label,
                    paragraph_ordinal=ordinal,
                    element_label=_element_label(blk),
                    position=position,
                    source_text=block_to_plain(blk),
                    in_translation_scope=blk.type in _TRANSLATABLE,
                    page=blk.page,
                    bbox=blk.bbox,
                )
            )
            position += 1
        for sub in sec.sections:
            walk(sub, [*path_parts, sec.id])

    for s in content.sections:
        walk(s, [])
    return rows


async def rebuild_block_search_index(
    session: AsyncSession, revision_id: str, content: DocumentContent
) -> int:
    """block_search_index を revision 単位で DELETE→INSERT する。挿入行数を返す。

    呼び出し側の Tx 内で実行し、document_revisions INSERT と同一トランザクションにする
    (plans/11 §9 フック#1)。
    """
    await session.execute(
        text("DELETE FROM block_search_index WHERE revision_id = :rid"),
        {"rid": revision_id},
    )
    rows = compute_index_rows(content)
    if rows:
        await session.execute(
            text(
                """
                INSERT INTO block_search_index
                  (revision_id, block_id, block_type, section_path, section_label,
                   paragraph_ordinal, element_label, position, source_text,
                   in_translation_scope, page, bbox)
                VALUES
                  (:revision_id, :block_id, :block_type, :section_path, :section_label,
                   :paragraph_ordinal, :element_label, :position, :source_text,
                   :in_translation_scope, :page, :bbox)
                """
            ),
            [
                {
                    "revision_id": revision_id,
                    "block_id": r.block_id,
                    "block_type": r.block_type,
                    "section_path": r.section_path,
                    "section_label": r.section_label,
                    "paragraph_ordinal": r.paragraph_ordinal,
                    "element_label": r.element_label,
                    "position": r.position,
                    "source_text": r.source_text,
                    "in_translation_scope": r.in_translation_scope,
                    "page": r.page,
                    "bbox": r.bbox,
                }
                for r in rows
            ],
        )
    return len(rows)
