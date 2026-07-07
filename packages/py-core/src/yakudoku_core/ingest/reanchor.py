"""リアンカー(リビジョン昇格。plans/02 §5.3・plans/05 §4.5・§12.3)。

新リビジョン作成時(再取り込み・B→A 昇格・arXiv 新版)、旧リビジョンを指す各種アンカーを
新リビジョンへ追従させる。対応は次の 2 パス:

1. ``block_id`` 引き継ぎ分: carryover(``parsing/carryover.py``)により新リビジョンに同一
   ``block_id`` が存在すればそのまま ``revision_id`` だけ書き換える。
2. quote 文字列探索分: (1) で解決できなければ、アンカーの ``quote``(引用スナップショット)を
   新リビジョンの ``block_search_index.source_text`` に対して部分文字列探索し、一致した
   ブロックへ移動する。

いずれにも失敗したものは **消さない**。``annotations`` は ``orphaned=true``(未配置)にし、
他のアンカー保持テーブル(notes / vocab_entries / chat_messages / article_blocks /
resource_links)は ``orphaned`` 列を持たないため、旧アンカーをそのまま保持する(P3: 黙って
消さない)。対象は当該 Paper の全 ``library_items``(``papers.latest_revision_id`` は
Paper 単位のため、全ユーザーの個人資産が対象になる)。

``library_items.reading_position`` は ``quote`` を持たない({revision_id, block_id,
view_mode})ため、パス 1(block_id 引き継ぎ)のみ適用する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yakudoku_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    BlockSearchIndex,
    ChatMessage,
    ChatThread,
    LibraryItem,
    Note,
    ResourceLink,
    VocabEntry,
)


@dataclass
class ReanchorStats:
    """adopt-revision レスポンス(plans/03 §6.8)の ``{moved, unplaced}``。"""

    moved: int = 0
    unplaced: int = 0


def _find_by_quote(quote: str, texts: list[tuple[str, str]]) -> str | None:
    """新リビジョンの block_search_index から quote を含む最初のブロックを探す。"""
    q = (quote or "").strip()
    if not q:
        return None
    for block_id, text in texts:
        if q in text:
            return block_id
    return None


def _resolve_anchor(
    anchor: Any,
    *,
    old_revision_id: str,
    new_revision_id: str,
    valid_block_ids: set[str],
    texts: list[tuple[str, str]],
) -> tuple[dict[str, Any] | None, str]:
    """1 アンカーを解決する。戻り値 = (更新後アンカー or None, outcome)。

    outcome: ``"skip"``(対象外。旧リビジョンを指していない)/ ``"moved"`` / ``"unplaced"``。
    """
    if not isinstance(anchor, dict) or anchor.get("revision_id") != old_revision_id:
        return None, "skip"
    block_id = anchor.get("block_id")
    if block_id in valid_block_ids:
        return {**anchor, "revision_id": new_revision_id}, "moved"
    matched = _find_by_quote(str(anchor.get("quote", "")), texts)
    if matched is not None:
        return {**anchor, "revision_id": new_revision_id, "block_id": matched}, "moved"
    return None, "unplaced"


def _resolve_anchor_list(
    anchors: list[Any] | None,
    *,
    old_revision_id: str,
    new_revision_id: str,
    valid_block_ids: set[str],
    texts: list[tuple[str, str]],
    stats: ReanchorStats,
) -> tuple[list[Any] | None, bool]:
    """アンカー配列(notes.anchors 等)を解決する。戻り値 = (更新後配列 or None, changed)。"""
    if not anchors:
        return None, False
    out: list[Any] = []
    changed = False
    for anchor in anchors:
        updated, outcome = _resolve_anchor(
            anchor,
            old_revision_id=old_revision_id,
            new_revision_id=new_revision_id,
            valid_block_ids=valid_block_ids,
            texts=texts,
        )
        if outcome == "moved" and updated is not None:
            out.append(updated)
            changed = True
            stats.moved += 1
        else:
            out.append(anchor)
            if outcome == "unplaced":
                stats.unplaced += 1
    return (out if changed else None), changed


async def _new_revision_index(
    session: AsyncSession, new_revision_id: str
) -> tuple[set[str], list[tuple[str, str]]]:
    rows = (
        await session.execute(
            select(BlockSearchIndex.block_id, BlockSearchIndex.source_text).where(
                BlockSearchIndex.revision_id == new_revision_id
            )
        )
    ).all()
    valid_ids = {str(r[0]) for r in rows}
    texts = [(str(r[0]), str(r[1] or "")) for r in rows]
    return valid_ids, texts


async def _reanchor_annotations(
    session: AsyncSession,
    library_item_ids: list[str],
    *,
    old_revision_id: str,
    new_revision_id: str,
    valid_block_ids: set[str],
    texts: list[tuple[str, str]],
    stats: ReanchorStats,
) -> None:
    rows = (
        (
            await session.execute(
                select(Annotation).where(Annotation.library_item_id.in_(library_item_ids))
            )
        )
        .scalars()
        .all()
    )
    for ann in rows:
        updated, outcome = _resolve_anchor(
            dict(ann.anchor or {}),
            old_revision_id=old_revision_id,
            new_revision_id=new_revision_id,
            valid_block_ids=valid_block_ids,
            texts=texts,
        )
        if outcome == "moved" and updated is not None:
            ann.anchor = updated
            ann.orphaned = False
            stats.moved += 1
        elif outcome == "unplaced":
            ann.orphaned = True  # 黙って消さない(P3。docs/01 §4.3)
            stats.unplaced += 1


async def _reanchor_notes(session: AsyncSession, library_item_ids: list[str], **kw: Any) -> None:
    rows = (
        (await session.execute(select(Note).where(Note.library_item_id.in_(library_item_ids))))
        .scalars()
        .all()
    )
    for note in rows:
        updated, changed = _resolve_anchor_list(note.anchors, **kw)
        if changed and updated is not None:
            note.anchors = updated


async def _reanchor_vocab(
    session: AsyncSession,
    library_item_ids: list[str],
    *,
    old_revision_id: str,
    new_revision_id: str,
    valid_block_ids: set[str],
    texts: list[tuple[str, str]],
    stats: ReanchorStats,
) -> None:
    rows = (
        (
            await session.execute(
                select(VocabEntry).where(VocabEntry.library_item_id.in_(library_item_ids))
            )
        )
        .scalars()
        .all()
    )
    for entry in rows:
        updated, outcome = _resolve_anchor(
            dict(entry.context_anchor or {}),
            old_revision_id=old_revision_id,
            new_revision_id=new_revision_id,
            valid_block_ids=valid_block_ids,
            texts=texts,
        )
        if outcome == "moved" and updated is not None:
            entry.context_anchor = updated
            stats.moved += 1
        elif outcome == "unplaced":
            stats.unplaced += 1


async def _reanchor_chat(session: AsyncSession, library_item_ids: list[str], **kw: Any) -> None:
    rows = (
        (
            await session.execute(
                select(ChatMessage)
                .join(ChatThread, ChatThread.id == ChatMessage.thread_id)
                .where(ChatThread.library_item_id.in_(library_item_ids))
            )
        )
        .scalars()
        .all()
    )
    for msg in rows:
        updated_ctx, changed_ctx = _resolve_anchor_list(msg.context_anchors, **kw)
        if changed_ctx and updated_ctx is not None:
            msg.context_anchors = updated_ctx
        updated_ev, changed_ev = _resolve_anchor_list(msg.evidence_anchors, **kw)
        if changed_ev and updated_ev is not None:
            msg.evidence_anchors = updated_ev


async def _reanchor_articles(session: AsyncSession, library_item_ids: list[str], **kw: Any) -> None:
    rows = (
        (
            await session.execute(
                select(ArticleBlock)
                .join(Article, Article.id == ArticleBlock.article_id)
                .where(Article.library_item_id.in_(library_item_ids))
            )
        )
        .scalars()
        .all()
    )
    for blk in rows:
        updated, changed = _resolve_anchor_list(blk.evidence_anchors, **kw)
        if changed and updated is not None:
            blk.evidence_anchors = updated


async def _reanchor_resources(
    session: AsyncSession, library_item_ids: list[str], **kw: Any
) -> None:
    rows = (
        (
            await session.execute(
                select(ResourceLink).where(ResourceLink.library_item_id.in_(library_item_ids))
            )
        )
        .scalars()
        .all()
    )
    for link in rows:
        updated, changed = _resolve_anchor_list(link.note_anchors, **kw)
        if changed and updated is not None:
            link.note_anchors = updated


async def _reanchor_reading_positions(
    session: AsyncSession,
    library_item_ids: list[str],
    *,
    old_revision_id: str,
    new_revision_id: str,
    valid_block_ids: set[str],
    **_kw: Any,
) -> None:
    """読書位置は quote を持たないため block_id 引き継ぎ分のみ追従させる。"""
    rows = (
        (await session.execute(select(LibraryItem).where(LibraryItem.id.in_(library_item_ids))))
        .scalars()
        .all()
    )
    for item in rows:
        rp = item.reading_position
        if not isinstance(rp, dict) or rp.get("revision_id") != old_revision_id:
            continue
        if rp.get("block_id") in valid_block_ids:
            item.reading_position = {**rp, "revision_id": new_revision_id}


async def reanchor_paper(
    session: AsyncSession, *, paper_id: str, old_revision_id: str, new_revision_id: str
) -> ReanchorStats:
    """Paper 単位でリアンカーを実行する(adopt-revision・B→A 昇格適用の共通経路)。

    ``papers.latest_revision_id`` は Paper 単位で全ユーザー共通のため、当該 Paper を持つ
    全 ``library_items``(全ユーザー)の個人資産を対象にする。
    """
    stats = ReanchorStats()
    valid_block_ids, texts = await _new_revision_index(session, new_revision_id)
    library_item_ids = [
        str(row)
        for row in (
            await session.execute(select(LibraryItem.id).where(LibraryItem.paper_id == paper_id))
        )
        .scalars()
        .all()
    ]
    if not library_item_ids:
        return stats

    kw: dict[str, Any] = {
        "old_revision_id": old_revision_id,
        "new_revision_id": new_revision_id,
        "valid_block_ids": valid_block_ids,
        "texts": texts,
        "stats": stats,
    }
    await _reanchor_annotations(session, library_item_ids, **kw)
    await _reanchor_notes(session, library_item_ids, **kw)
    await _reanchor_vocab(session, library_item_ids, **kw)
    await _reanchor_chat(session, library_item_ids, **kw)
    await _reanchor_articles(session, library_item_ids, **kw)
    await _reanchor_resources(session, library_item_ids, **kw)
    await _reanchor_reading_positions(session, library_item_ids, **kw)
    return stats
