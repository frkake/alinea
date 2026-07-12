"""dashboard ルータ(plans/03 §5.12・docs/06 §6)。

``GET /api/dashboard``: ホーム画面(1d)の 5 区画。

- ``continue_reading``: ``status=reading``、位置保存の新しい順(``updated_at`` 降順。§5.8 の
  ``save_position`` は明示的な保存時刻を ``reading_position`` に持たないため、更新トリガで
  自動更新される ``updated_at`` を代用する)、最大 3 件。
- ``up_next_queue``: ``status=up_next``、``queue_order`` 昇順(未設定は末尾。§5.7)、
  同順位は ``added_at`` 昇順。
- ``deadlines``: ``services/deadlines.dashboard_deadlines``(M2-09)に委譲。締切設定済みの
  コレクション(最大 2・締切昇順)と、締切コレクションに属す未読了エントリ(最大 3。
  plans/09-screens/1d §4.7 の抽出規則。超過分は含めない)。
- ``recent``: 今週(月曜 00:00 UTC 起点)追加、最大 6 件+取り込みパイプライン進捗。
- ``stats``: 直近 12 週(古→新)の読書時間棒グラフ+今週の読了本数。

``LibraryItemSummary`` の組み立ては library_items ルータの既存ヘルパ
(``_summary`` / ``_reading_maps`` / ``_quality_of``)を再利用する(重複実装しない)。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from alinea_core.db.models import Job, LibraryItem, Paper, ReadingSession
from fastapi import APIRouter
from sqlalchemy import Row, func, select

from alinea_api.deps import CurrentUser, DbDep
from alinea_api.routers.library_items import _quality_of, _reading_maps, _summary
from alinea_api.schemas.common import LibraryItemSummary, PipelineState
from alinea_api.schemas.dashboard import (
    DashboardResponse,
    RecentSection,
    StatsSection,
    StatsWeek,
)
from alinea_api.schemas.ingest import build_pipeline_state
from alinea_api.services.deadlines import dashboard_deadlines, today_jst

router = APIRouter(tags=["dashboard"])

_WEEKS = 12
_ItemRow = Row[tuple[LibraryItem, Paper]]


def _week_start(now: dt.datetime) -> dt.datetime:
    """今週の開始(月曜 00:00 UTC)。docs/06 §6.5 の「今週」の基準。"""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - dt.timedelta(days=midnight.weekday())


async def _summaries(db: DbDep, rows: Sequence[_ItemRow]) -> list[LibraryItemSummary]:
    """``_reading_maps`` を一括ロードしつつ行ごとに ``_summary`` を組み立てる。"""
    maps = await _reading_maps(db, [(row[0], row[1]) for row in rows])
    out: list[LibraryItemSummary] = []
    for row in rows:
        item, paper = row[0], row[1]
        quality = await _quality_of(db, item, paper)
        out.append(_summary(item, paper, quality, maps))
    return out


async def _continue_reading(db: DbDep, user_id: str) -> list[LibraryItemSummary]:
    rows = (
        await db.execute(
            select(LibraryItem, Paper)
            .join(Paper, Paper.id == LibraryItem.paper_id)
            .where(LibraryItem.user_id == user_id, LibraryItem.status == "reading")
            .order_by(LibraryItem.updated_at.desc())
            .limit(3)
        )
    ).all()
    return await _summaries(db, rows)


async def _up_next_queue(db: DbDep, user_id: str) -> list[LibraryItemSummary]:
    rows = (
        await db.execute(
            select(LibraryItem, Paper)
            .join(Paper, Paper.id == LibraryItem.paper_id)
            .where(LibraryItem.user_id == user_id, LibraryItem.status == "up_next")
            .order_by(
                LibraryItem.queue_order.is_(None),
                LibraryItem.queue_order.asc(),
                LibraryItem.added_at.asc(),
            )
        )
    ).all()
    return await _summaries(db, rows)


async def _latest_ingest_job(db: DbDep, library_item_id: str) -> Job | None:
    return (
        (
            await db.execute(
                select(Job)
                .where(Job.kind == "ingest", Job.library_item_id == library_item_id)
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


def _to_pipeline_state(job: Job) -> PipelineState:
    """schemas/ingest.py の ``build_pipeline_state`` を再利用し §1.7 の共通型に写す。"""
    ingest_state = build_pipeline_state(job)
    return PipelineState(**ingest_state.model_dump())


async def _recent(db: DbDep, user_id: str, week_start: dt.datetime) -> RecentSection:
    rows = (
        await db.execute(
            select(LibraryItem, Paper)
            .join(Paper, Paper.id == LibraryItem.paper_id)
            .where(LibraryItem.user_id == user_id, LibraryItem.added_at >= week_start)
            .order_by(LibraryItem.added_at.desc())
        )
    ).all()
    week_count = len(rows)
    top = rows[:6]
    summaries = await _summaries(db, top)

    items: list[LibraryItemSummary] = []
    for row, summary in zip(top, summaries, strict=True):
        job = await _latest_ingest_job(db, str(row[0].id))
        if job is not None:
            summary = summary.model_copy(update={"pipeline": _to_pipeline_state(job)})
        items.append(summary)
    return RecentSection(week_count=week_count, items=items)


async def _weekly_hours(db: DbDep, user_id: str, week_start: dt.datetime) -> list[float]:
    """直近 12 週(古→新)の active_seconds を時間へ変換(小数第 1 位)。"""
    range_start = week_start - dt.timedelta(weeks=_WEEKS - 1)
    rows = (
        await db.execute(
            select(ReadingSession.started_at, ReadingSession.active_seconds)
            .join(LibraryItem, LibraryItem.id == ReadingSession.library_item_id)
            .where(LibraryItem.user_id == user_id, ReadingSession.started_at >= range_start)
        )
    ).all()
    buckets = [0] * _WEEKS
    for started_at, active_seconds in rows:
        idx = int((started_at - range_start) // dt.timedelta(weeks=1))
        if 0 <= idx < _WEEKS:
            buckets[idx] += int(active_seconds)
    return [round(seconds / 3600, 1) for seconds in buckets]


async def _stats(db: DbDep, user_id: str, week_start: dt.datetime) -> StatsSection:
    weekly_hours = await _weekly_hours(db, user_id, week_start)
    week_end = week_start + dt.timedelta(weeks=1)
    finished_count = (
        await db.execute(
            select(func.count())
            .select_from(LibraryItem)
            .where(
                LibraryItem.user_id == user_id,
                LibraryItem.finished_at.is_not(None),
                LibraryItem.finished_at >= week_start,
                LibraryItem.finished_at < week_end,
            )
        )
    ).scalar_one()
    return StatsSection(
        week=StatsWeek(finished_count=int(finished_count), reading_hours=weekly_hours[-1]),
        weekly_hours=weekly_hours,
    )


@router.get("/api/dashboard", response_model=DashboardResponse, operation_id="dashboard_get")
async def get_dashboard(user: CurrentUser, db: DbDep) -> DashboardResponse:
    now = dt.datetime.now(dt.UTC)
    week_start = _week_start(now)
    return DashboardResponse(
        continue_reading=await _continue_reading(db, user.id),
        up_next_queue=await _up_next_queue(db, user.id),
        deadlines=await dashboard_deadlines(db, user.id, today_jst(now)),
        recent=await _recent(db, user.id, week_start),
        stats=await _stats(db, user.id, week_start),
    )
