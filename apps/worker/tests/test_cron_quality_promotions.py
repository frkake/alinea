"""B→A 昇格提案検出 cron のテスト(M1-22 (c)。plans/05 §12.3。PY-ING-07 の cron 部)。

``check_quality_promotions`` が LaTeX/公式 HTML の出現を検知して
``status_suggestion``(``reason=promotion_b_to_a``)通知を挿入し、**自動適用しない**
(``papers.latest_revision_id`` が変わらない)ことを検証する。7 日間引きと A 論文の
対象外化も確認する。

arXiv は worker conftest の ASGI スタブ(``worker_ctx``)を再利用する(LaTeX/HTML とも
200 を返すため両判定経路を経由する)。実ネットワーク通信は発生しない。
"""

from __future__ import annotations

import os
import random
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from alinea_core.db.models import DocumentRevision, LibraryItem, Notification, Paper, User
from alinea_worker.cron import check_quality_promotions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://alinea:alinea@localhost:5432/alinea",
)


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(DATABASE_URL, poolclass=None)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


def _arxiv_id() -> str:
    n = (int(time.time() * 1000) + random.randint(0, 9999)) % 100000
    return f"{random.randint(1001, 2912)}.{n:05d}"


async def _seed_quality_b_paper(db: AsyncSession) -> dict[str, str]:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()
    paper = Paper(
        id=str(uuid.uuid4()), arxiv_id=_arxiv_id(), title="B Quality Paper", visibility="public"
    )
    db.add(paper)
    await db.flush()
    rev = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        parser_version="pdf-1.0.0",
        quality_level="B",
        source_format="pdf",
        content={"quality_level": "B", "sections": []},
    )
    db.add(rev)
    await db.flush()
    paper.latest_revision_id = rev.id
    li = LibraryItem(id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status="reading")
    db.add(li)
    await db.commit()
    return {
        "user_id": str(user.id),
        "paper_id": str(paper.id),
        "revision_id": str(rev.id),
        "library_item_id": str(li.id),
    }


async def test_check_quality_promotions_fires_notification_without_auto_apply(
    db_session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    worker_ctx: dict[str, Any],
) -> None:
    seed = await _seed_quality_b_paper(db_session)
    ctx = {**worker_ctx, "sessionmaker": maker}

    await check_quality_promotions(ctx)

    async with maker() as session:
        note = (
            (
                await session.execute(
                    select(Notification).where(
                        Notification.user_id == seed["user_id"],
                        Notification.kind == "status_suggestion",
                    )
                )
            )
            .scalars()
            .one()
        )
        assert note.payload["reason"] == "promotion_b_to_a"
        assert note.payload["action"] == "promote_revision"
        assert note.payload["revision_id"] == seed["revision_id"]
        assert note.payload["library_item_id"] == seed["library_item_id"]
        assert note.payload["resolved"] is None
        assert note.read is False

        paper = await session.get(Paper, seed["paper_id"])
        assert paper is not None
        # 自動適用されない(P6): latest_revision_id は現行 B のまま。
        assert str(paper.latest_revision_id) == seed["revision_id"]


async def test_check_quality_promotions_dedupes_within_seven_days(
    db_session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    worker_ctx: dict[str, Any],
) -> None:
    seed = await _seed_quality_b_paper(db_session)
    ctx = {**worker_ctx, "sessionmaker": maker}

    await check_quality_promotions(ctx)
    await check_quality_promotions(ctx)  # 2 回目は Redis の 7 日間引きでスキップされる

    async with maker() as session:
        rows = (
            await session.execute(
                select(Notification.id).where(Notification.user_id == seed["user_id"])
            )
        ).all()
        assert len(rows) == 1


async def test_check_quality_promotions_skips_unread_existing_suggestion(
    db_session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    worker_ctx: dict[str, Any],
) -> None:
    """Redis の 7 日間引きを回避しても、未読の同種提案があれば二重挿入しない(§12.3)。"""
    seed = await _seed_quality_b_paper(db_session)
    ctx = {**worker_ctx, "sessionmaker": maker}
    await check_quality_promotions(ctx)

    # Redis キーを消して間引きを回避しても、未読提案がある限り再挿入しない。
    worker_ctx["redis"]._store.pop(f"promo:checked:{seed['paper_id']}", None)
    await check_quality_promotions(ctx)

    async with maker() as session:
        rows = (
            await session.execute(
                select(Notification.id).where(Notification.user_id == seed["user_id"])
            )
        ).all()
        assert len(rows) == 1


async def test_check_quality_promotions_ignores_quality_a_papers(
    db_session: AsyncSession,
    maker: async_sessionmaker[AsyncSession],
    worker_ctx: dict[str, Any],
) -> None:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db_session.add(user)
    await db_session.flush()
    paper = Paper(
        id=str(uuid.uuid4()), arxiv_id=_arxiv_id(), title="Already A", visibility="public"
    )
    db_session.add(paper)
    await db_session.flush()
    rev = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        parser_version="html-1.0.0",
        quality_level="A",
        source_format="arxiv_html",
        content={"quality_level": "A", "sections": []},
    )
    db_session.add(rev)
    await db_session.flush()
    paper.latest_revision_id = rev.id
    await db_session.commit()

    ctx = {**worker_ctx, "sessionmaker": maker}
    await check_quality_promotions(ctx)

    async with maker() as session:
        rows = (
            await session.execute(select(Notification.id).where(Notification.user_id == user.id))
        ).all()
        assert rows == []
