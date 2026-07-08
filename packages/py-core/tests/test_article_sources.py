"""``yakudoku_core.article.sources`` の素材収集テスト(plans/07 §4.2)。

``collect_article_sources`` はメモ・注釈・チャット履歴を DB から読み取り 1 つの素材集合
(:class:`ArticleSources`)にまとめる。実 PostgreSQL(``db_session``)に対して実行する。
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.article.sources import collect_article_sources
from yakudoku_core.db.models import (
    Annotation,
    ChatMessage,
    ChatThread,
    DocumentRevision,
    LibraryItem,
    Note,
    Paper,
    User,
)
from yakudoku_core.document.blocks import Block, DocumentContent, Section, SectionHeading
from yakudoku_core.document.inlines import Inline


@dataclass
class _Seed:
    user: User
    paper: Paper
    revision: DocumentRevision
    library_item: LibraryItem


def _uid() -> str:
    return str(uuid.uuid4())


def _content() -> DocumentContent:
    return DocumentContent(
        quality_level="A",
        sections=[
            Section(
                id="sec-1",
                heading=SectionHeading(number="1", title="Introduction"),
                blocks=[
                    Block(
                        id="blk-p1",
                        type="paragraph",
                        inlines=[Inline(t="text", v="Rectified flow learns a straight map.")],
                    )
                ],
            )
        ],
    )


async def _seed(db: AsyncSession) -> _Seed:
    user = User(id=_uid(), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=_uid(),
        title="Rectified Flow",
        authors=[{"name": "Xingchao Liu"}],
        arxiv_id=f"2209.{uuid.uuid4().hex[:5]}",
        venue="ICLR 2023",
        published_on=dt.date(2022, 9, 7),
        license="cc-by-4.0",
        visibility="private",
        owner_user_id=user.id,
    )
    db.add(paper)
    await db.flush()
    revision = DocumentRevision(
        id=_uid(),
        paper_id=paper.id,
        parser_version="test-1",
        quality_level="A",
        source_format="latex",
        content=_content().model_dump(),
    )
    db.add(revision)
    await db.flush()
    item = LibraryItem(id=_uid(), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(item)
    await db.flush()
    await db.commit()
    return _Seed(user=user, paper=paper, revision=revision, library_item=item)


async def test_collect_article_sources_with_no_notes_annotations_or_chat(
    db_session: AsyncSession,
) -> None:
    """素材が 1 つも無い場合は各テキストが空文字列になる(§4.2 の既定形)。"""
    seed = await _seed(db_session)
    sources = await collect_article_sources(
        db_session,
        library_item=seed.library_item,
        paper=seed.paper,
        revision=seed.revision,
        user=seed.user,
        include_math=False,
    )
    assert sources.notes_text == ""
    assert sources.annotations_text == ""
    assert sources.chat_text == ""


async def test_collect_article_sources_includes_notes_annotations_and_chat(
    db_session: AsyncSession,
) -> None:
    seed = await _seed(db_session)
    item = seed.library_item

    db_session.add(
        Note(id=_uid(), library_item_id=item.id, title="要点", body_md="reflow の反復回数を確認")
    )
    db_session.add(
        Annotation(
            id=_uid(),
            library_item_id=item.id,
            kind="comment",
            color="question",
            body="拡散モデルとの違いはここ",
            anchor={
                "revision_id": str(seed.revision.id),
                "block_id": "blk-p1",
                "start": 0,
                "end": 10,
                "quote": "straight",
                "side": "source",
            },
        )
    )
    thread = ChatThread(id=_uid(), library_item_id=item.id, title="メイン", is_main=True)
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        ChatMessage(
            thread_id=thread.id,
            role="user",
            content={"segments": [{"type": "text", "text": "reflow とは?"}]},
            text_plain="reflow とは?",
        )
    )
    db_session.add(
        ChatMessage(
            thread_id=thread.id,
            role="assistant",
            content={"segments": [{"type": "text", "text": "反復適用による再直線化です。"}]},
            text_plain="反復適用による再直線化です。",
        )
    )
    await db_session.commit()

    sources = await collect_article_sources(
        db_session,
        library_item=item,
        paper=seed.paper,
        revision=seed.revision,
        user=seed.user,
        include_math=False,
    )

    assert "# メモ" in sources.notes_text
    assert "reflow の反復回数を確認" in sources.notes_text

    assert "# 注釈" in sources.annotations_text
    assert "★疑問" in sources.annotations_text  # is_question=True(color=question)の印
    assert "拡散モデルとの違いはここ" in sources.annotations_text  # コメント本文
    assert len(sources.annotation_refs) == 1
    assert sources.annotation_refs[0].is_question is True

    assert "# チャット履歴" in sources.chat_text
    assert "あなた: reflow とは?" in sources.chat_text
    assert "アシスタント: 反復適用による再直線化です。" in sources.chat_text
