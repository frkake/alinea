"""PY-ANN-02: リアンカー(M1-22 (c)。plans/02 §5.3・plans/05 §4.5)。

新リビジョンで (a) block_id 引き継ぎ分は追従 (b) quote 探索一致分は移動 (c) 失敗分のみ
``annotations.orphaned=true``(消えない)。``reading_position``・vocab・記事アンカーも
同時に書き換わることを検証する。
"""

from __future__ import annotations

import uuid

from alinea_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    DocumentRevision,
    LibraryItem,
    Note,
    Paper,
    User,
    VocabEntry,
)
from alinea_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from alinea_core.document.inlines import Inline
from alinea_core.ingest.reanchor import reanchor_paper
from alinea_core.search.rebuild import rebuild_block_search_index
from sqlalchemy.ext.asyncio import AsyncSession


def _content(blocks: list[tuple[str, str]]) -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(id=bid, type="paragraph", inlines=[Inline(t="text", v=text)])
                    for bid, text in blocks
                ],
            )
        ],
    )


def _anchor(revision_id: str, block_id: str, quote: str) -> dict[str, object]:
    return {
        "revision_id": revision_id,
        "block_id": block_id,
        "start": 0,
        "end": len(quote),
        "quote": quote,
        "side": "source",
    }


async def test_reanchor_paper_moves_carries_over_and_marks_unplaced(
    db_session: AsyncSession,
) -> None:
    db = db_session
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(id=str(uuid.uuid4()), title="Reanchor Paper", visibility="public")
    db.add(paper)
    await db.flush()

    old_content = _content(
        [
            ("blk-keep", "Keep me across revisions"),
            ("blk-move", "Move me via quote search"),
            ("blk-lost", "Will be lost forever"),
        ]
    )
    old_rev = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        source_version="v1",
        parser_version="html-1.0.0",
        quality_level="A",
        source_format="arxiv_html",
        content=old_content.model_dump(),
    )
    db.add(old_rev)
    await db.flush()
    await rebuild_block_search_index(db, str(old_rev.id), old_content)

    # 新リビジョン: blk-keep は carryover で同一 id を維持、blk-move は id が変わるが
    # 同一テキストを保持(quote 探索で解決)、blk-lost 相当の内容は消滅(未配置になる)。
    new_content = _content(
        [
            ("blk-keep", "Keep me across revisions"),
            ("blk-moved-new", "Move me via quote search"),
        ]
    )
    new_rev = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        source_version="v1",
        parser_version="latex-1.0.0",
        quality_level="A",
        source_format="latex",
        content=new_content.model_dump(),
    )
    db.add(new_rev)
    await db.flush()
    await rebuild_block_search_index(db, str(new_rev.id), new_content)
    paper.latest_revision_id = new_rev.id

    li = LibraryItem(
        id=str(uuid.uuid4()),
        user_id=user.id,
        paper_id=paper.id,
        status="reading",
        reading_position={
            "revision_id": str(old_rev.id),
            "block_id": "blk-keep",
            "view_mode": "translation",
        },
    )
    db.add(li)
    await db.flush()

    ann_keep = Annotation(
        library_item_id=li.id,
        kind="highlight",
        color="important",
        anchor=_anchor(str(old_rev.id), "blk-keep", "Keep me"),
    )
    ann_moved = Annotation(
        library_item_id=li.id,
        kind="highlight",
        color="question",
        anchor=_anchor(str(old_rev.id), "blk-move", "Move me via quote search"),
    )
    ann_lost = Annotation(
        library_item_id=li.id,
        kind="highlight",
        color="idea",
        anchor=_anchor(str(old_rev.id), "blk-lost", "Will be lost forever"),
    )
    db.add_all([ann_keep, ann_moved, ann_lost])

    note = Note(
        library_item_id=li.id,
        body_md="メモ",
        anchors=[_anchor(str(old_rev.id), "blk-keep", "Keep me across revisions")],
    )
    db.add(note)

    vocab = VocabEntry(
        id=str(uuid.uuid4()),
        user_id=user.id,
        library_item_id=li.id,
        term="rectified",
        context_anchor=_anchor(str(old_rev.id), "blk-move", "Move me via quote search"),
        context_sentence="Move me via quote search",
    )
    db.add(vocab)

    article = Article(id=str(uuid.uuid4()), library_item_id=li.id, title="記事")
    db.add(article)
    await db.flush()
    article_block = ArticleBlock(
        article_id=article.id,
        position=0,
        type="paragraph",
        content={"text": "本文"},
        evidence_anchors=[_anchor(str(old_rev.id), "blk-lost", "Will be lost forever")],
    )
    db.add(article_block)
    await db.commit()

    stats = await reanchor_paper(
        db, paper_id=str(paper.id), old_revision_id=str(old_rev.id), new_revision_id=str(new_rev.id)
    )
    await db.commit()

    # moved: ann_keep / ann_moved / note / vocab の 4 件。unplaced: ann_lost / article_block の 2 件。
    assert stats.moved == 4
    assert stats.unplaced == 2

    await db.refresh(ann_keep)
    assert ann_keep.anchor["revision_id"] == str(new_rev.id)
    assert ann_keep.anchor["block_id"] == "blk-keep"
    assert ann_keep.orphaned is False

    await db.refresh(ann_moved)
    assert ann_moved.anchor["revision_id"] == str(new_rev.id)
    assert ann_moved.anchor["block_id"] == "blk-moved-new"
    assert ann_moved.orphaned is False

    await db.refresh(ann_lost)
    assert ann_lost.orphaned is True  # 消えない(P3)
    assert ann_lost.anchor["revision_id"] == str(old_rev.id)  # アンカー自体は保全

    await db.refresh(note)
    assert note.anchors[0]["revision_id"] == str(new_rev.id)
    assert note.anchors[0]["block_id"] == "blk-keep"

    await db.refresh(vocab)
    assert vocab.context_anchor["revision_id"] == str(new_rev.id)
    assert vocab.context_anchor["block_id"] == "blk-moved-new"

    await db.refresh(article_block)
    assert article_block.evidence_anchors[0]["revision_id"] == str(old_rev.id)  # 未配置は保全

    await db.refresh(li)
    assert li.reading_position is not None
    assert li.reading_position["revision_id"] == str(new_rev.id)
    assert li.reading_position["block_id"] == "blk-keep"


async def test_reanchor_paper_is_noop_without_library_items(db_session: AsyncSession) -> None:
    db = db_session
    paper = Paper(id=str(uuid.uuid4()), title="No Readers", visibility="public")
    db.add(paper)
    await db.flush()
    content = _content([("blk-a", "solo block")])
    rev = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        parser_version="html-1.0.0",
        quality_level="A",
        source_format="arxiv_html",
        content=content.model_dump(),
    )
    db.add(rev)
    await db.commit()

    stats = await reanchor_paper(
        db, paper_id=str(paper.id), old_revision_id=str(uuid.uuid4()), new_revision_id=str(rev.id)
    )
    assert stats.moved == 0
    assert stats.unplaced == 0
