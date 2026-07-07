"""締切関連の共有ロジック(plans/03 §13.1 の ``days_left``・plans/09-screens/1d §4.7
「締切が近い」の抽出規則・docs/06 §6.3)。

``days_left`` は collections ルータ(一覧・詳細)とダッシュボードの両方から使う共通の日数計算。
``dashboard_deadlines`` は ``GET /api/dashboard`` の ``deadlines`` セクション(§5.12)を組み立てる
(M2-09 まではレスポンス形のみ・常に空だった箇所を実データ化する)。

**決定(デザイン未確定のため本モジュールで確定する抽出規則)**:
``deadlines.items``(plans/03 §5.12「論文単位」)は個々の ``LibraryItem`` が持つ独立した
締切フィールドではなく、締切が設定されたコレクションの各エントリのうち **まだ「読んだ」に
なっていないもの**(docs/06 §6.3「未着手」強調と同じ発想)を対象にする。1 つの LibraryItem が
複数コレクションに属す場合は最も近い締切のものを採用する(重複表示を避ける)。並び順は
締切昇順→自分担当(assignee_is_self)優先→collection_entries.position 昇順。
「超過分は表示しない」(1d §4.7)を collections・items の双方に適用する(``days_left < 0`` を除外)。
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import Collection, CollectionEntry, LibraryItem, Paper

from yakudoku_api.schemas.dashboard import (
    DeadlineCollectionEntry,
    DeadlineItemEntry,
    DeadlinesSection,
)

_MAX_COLLECTIONS = 2
_MAX_ITEMS = 3

# 締切の日境界は JST(cron `send_deadline_reminders` の毎日 08:00 JST・llm_settings.py の
# クォータ月境界と同方針。plans/01 §4.3・apps/api/src/.../llm/deps.py)。
_JST = ZoneInfo("Asia/Tokyo")


def today_jst(now: dt.datetime | None = None) -> dt.date:
    """締切の残日数計算に使う「今日」(JST 暦日)。"""
    moment = now if now is not None else dt.datetime.now(dt.UTC)
    return moment.astimezone(_JST).date()


def days_left(deadline: dt.date | None, today: dt.date) -> int | None:
    """締切までの残日数(超過は負値)。``deadline`` が null なら null。"""
    if deadline is None:
        return None
    return (deadline - today).days


async def _collections_with_deadline(
    db: AsyncSession, user_id: str
) -> list[tuple[Collection, int, int]]:
    """ユーザーの締切設定済みコレクション+ (total_count, done_count)。"""
    rows = (
        await db.execute(
            select(
                Collection,
                func.count(CollectionEntry.id),
                func.count(CollectionEntry.id).filter(LibraryItem.status == "done"),
            )
            .outerjoin(CollectionEntry, CollectionEntry.collection_id == Collection.id)
            .outerjoin(LibraryItem, LibraryItem.id == CollectionEntry.library_item_id)
            .where(Collection.user_id == user_id, Collection.deadline.is_not(None))
            .group_by(Collection.id)
        )
    ).all()
    return [(row[0], int(row[1]), int(row[2])) for row in rows]


async def dashboard_deadlines(db: AsyncSession, user_id: str, today: dt.date) -> DeadlinesSection:
    """§5.12 ``deadlines``(1d「締切が近い」)。超過分は含めない(1d §4.7)。"""
    collections = await _collections_with_deadline(db, user_id)

    collection_entries: list[DeadlineCollectionEntry] = []
    for collection, total_count, done_count in collections:
        left = days_left(collection.deadline, today)
        if left is None or left < 0:
            continue
        assert collection.deadline is not None
        collection_entries.append(
            DeadlineCollectionEntry(
                id=str(collection.id),
                name=collection.name,
                deadline=collection.deadline.isoformat(),
                days_left=left,
                done_count=done_count,
                total_count=total_count,
            )
        )
    collection_entries.sort(key=lambda c: c.days_left)
    collection_entries = collection_entries[:_MAX_COLLECTIONS]

    item_rows = (
        await db.execute(
            select(
                CollectionEntry.library_item_id,
                CollectionEntry.position,
                CollectionEntry.assignee_is_self,
                Collection.deadline,
                LibraryItem.status,
                Paper.title,
            )
            .join(Collection, Collection.id == CollectionEntry.collection_id)
            .join(LibraryItem, LibraryItem.id == CollectionEntry.library_item_id)
            .join(Paper, Paper.id == LibraryItem.paper_id)
            .where(
                Collection.user_id == user_id,
                Collection.deadline.is_not(None),
                LibraryItem.status != "done",
            )
        )
    ).all()

    # library_item_id ごとに最も近い締切のものだけ残す(1 論文が複数コレクションに入る場合)。
    best: dict[str, tuple[int, int, bool, dt.date, str, str]] = {}
    for library_item_id, position, assignee_is_self, deadline, status, title in item_rows:
        left = days_left(deadline, today)
        if left is None or left < 0:
            continue
        lid = str(library_item_id)
        prev = best.get(lid)
        if prev is None or left < prev[0]:
            best[lid] = (left, int(position), bool(assignee_is_self), deadline, status, title)

    ordered = sorted(
        best.items(),
        key=lambda kv: (kv[1][0], not kv[1][2], kv[1][1]),
    )
    item_entries = [
        DeadlineItemEntry(
            library_item_id=lid,
            title=data[5],
            deadline=data[3].isoformat(),
            assignee_self=data[2],
            status=data[4],
        )
        for lid, data in ordered[:_MAX_ITEMS]
    ]

    return DeadlinesSection(collections=collection_entries, items=item_entries)


__all__ = ["dashboard_deadlines", "days_left", "today_jst"]
