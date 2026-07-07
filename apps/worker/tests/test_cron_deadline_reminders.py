"""``send_deadline_reminders`` cron のテスト(M2-09。plans/01 §4.3・docs/06 §7・PY-NTF-01 の締切部)。

締切が設定され未超過のコレクションで未着手エントリがあるものについて ``deadline_reminder``
通知が 1 件だけ挿入されること(重複抑制)・未着手 0 件/超過/通知設定 OFF ではスキップされる
ことを検証する。

DB は worker conftest の ``db_session`` / ``maker`` フィクスチャ(実 PostgreSQL)、Redis は
``worker_ctx`` の ``FakeRedis`` を使う。実ネットワーク通信は発生しない。

NOTE(所有範囲の確認): 本ファイルは新規追加であり、他タスクが所有する既存ファイルは編集しない。
所有ファイルリストは ``apps/worker/src/yakudoku_worker/cron.py`` のみを明記しているが、
TDD(担当テスト ID 全 green)を満たすため本テストファイルを新規に追加する
(followups に記載)。
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from yakudoku_core.db.models import (
    Collection,
    CollectionEntry,
    LibraryItem,
    Notification,
    Paper,
    User,
)
from yakudoku_worker.cron import send_deadline_reminders

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://yakudoku:yakudoku@localhost:5432/yakudoku",
)


@pytest_asyncio.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(DATABASE_URL, poolclass=None)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


def _today_jst() -> dt.date:
    from zoneinfo import ZoneInfo

    return dt.datetime.now(dt.UTC).astimezone(ZoneInfo("Asia/Tokyo")).date()


async def _seed_collection(
    db: AsyncSession,
    *,
    deadline_offset_days: int,
    entry_statuses: list[str],
    notifications_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """締切付きコレクション+ entry_statuses の件数分の LibraryItem(reading_position 無し)。"""
    user = User(
        id=str(uuid.uuid4()),
        email=f"{uuid.uuid4().hex}@t.test",
        settings=(
            {"notifications": notifications_settings} if notifications_settings is not None else {}
        ),
    )
    db.add(user)
    await db.flush()

    coll = Collection(
        id=str(uuid.uuid4()),
        user_id=user.id,
        name="輪読会 2026-07",
        deadline=_today_jst() + dt.timedelta(days=deadline_offset_days),
    )
    db.add(coll)
    await db.flush()

    library_item_ids: list[str] = []
    for i, status in enumerate(entry_statuses):
        paper = Paper(id=str(uuid.uuid4()), title=f"Paper {i}", visibility="public")
        db.add(paper)
        await db.flush()
        li = LibraryItem(id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status=status)
        db.add(li)
        await db.flush()
        db.add(
            CollectionEntry(
                id=str(uuid.uuid4()), collection_id=coll.id, library_item_id=li.id, position=i
            )
        )
        library_item_ids.append(str(li.id))
    await db.commit()
    return {
        "user_id": str(user.id),
        "collection_id": str(coll.id),
        "library_item_ids": library_item_ids,
    }


async def test_send_deadline_reminders_fires_notification_with_unstarted_count(
    db_session: AsyncSession, maker: async_sessionmaker[AsyncSession], worker_ctx: dict[str, Any]
) -> None:
    seed = await _seed_collection(
        db_session, deadline_offset_days=10, entry_statuses=["planned", "done"]
    )
    ctx = {**worker_ctx, "sessionmaker": maker}

    await send_deadline_reminders(ctx)

    async with maker() as session:
        note = (
            (
                await session.execute(
                    select(Notification).where(
                        Notification.user_id == seed["user_id"],
                        Notification.kind == "deadline_reminder",
                    )
                )
            )
            .scalars()
            .one()
        )
        assert note.payload["collection_id"] == seed["collection_id"]
        assert note.payload["collection_name"] == "輪読会 2026-07"
        assert note.payload["days_left"] == 10
        assert note.payload["unstarted_count"] == 1  # "done" は数えない
        assert note.read is False


async def test_send_deadline_reminders_dedupes_within_same_day(
    db_session: AsyncSession, maker: async_sessionmaker[AsyncSession], worker_ctx: dict[str, Any]
) -> None:
    seed = await _seed_collection(db_session, deadline_offset_days=3, entry_statuses=["up_next"])
    ctx = {**worker_ctx, "sessionmaker": maker}

    await send_deadline_reminders(ctx)
    await send_deadline_reminders(ctx)  # 同日 2 回目は Redis の間引きでスキップ

    async with maker() as session:
        rows = (
            await session.execute(
                select(Notification.id).where(Notification.user_id == seed["user_id"])
            )
        ).all()
        assert len(rows) == 1


async def test_send_deadline_reminders_skips_when_no_unstarted_entries(
    db_session: AsyncSession, maker: async_sessionmaker[AsyncSession], worker_ctx: dict[str, Any]
) -> None:
    seed = await _seed_collection(db_session, deadline_offset_days=5, entry_statuses=["done"])
    ctx = {**worker_ctx, "sessionmaker": maker}

    await send_deadline_reminders(ctx)

    async with maker() as session:
        rows = (
            await session.execute(
                select(Notification.id).where(Notification.user_id == seed["user_id"])
            )
        ).all()
        assert rows == []


async def test_send_deadline_reminders_skips_overdue_collections(
    db_session: AsyncSession, maker: async_sessionmaker[AsyncSession], worker_ctx: dict[str, Any]
) -> None:
    seed = await _seed_collection(db_session, deadline_offset_days=-1, entry_statuses=["planned"])
    ctx = {**worker_ctx, "sessionmaker": maker}

    await send_deadline_reminders(ctx)

    async with maker() as session:
        rows = (
            await session.execute(
                select(Notification.id).where(Notification.user_id == seed["user_id"])
            )
        ).all()
        assert rows == []


async def test_send_deadline_reminders_respects_notifications_setting_off(
    db_session: AsyncSession, maker: async_sessionmaker[AsyncSession], worker_ctx: dict[str, Any]
) -> None:
    seed = await _seed_collection(
        db_session,
        deadline_offset_days=1,
        entry_statuses=["planned"],
        notifications_settings={"deadline_reminder": False},
    )
    ctx = {**worker_ctx, "sessionmaker": maker}

    await send_deadline_reminders(ctx)

    async with maker() as session:
        rows = (
            await session.execute(
                select(Notification.id).where(Notification.user_id == seed["user_id"])
            )
        ).all()
        assert rows == []
