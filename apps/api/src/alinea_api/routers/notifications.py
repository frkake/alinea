"""notifications — 一覧・既読化・read-all・提案 2 択 action(plans/03 §16)。

- 一覧: ``cursor``(不透明・created_at 基準)+ ``limit``(既定 20・最大 50)。並びは
  ``created_at`` 降順。未読件数はベル UI 用に ``count_unread_notifications``
  (``/api/auth/me`` の ``unread_notifications`` と同一計算)を再利用する。
- action: ``status_suggestion`` の 2 択のみ有効(それ以外 422)。resolved 済みの再操作は
  409 ``conflict``。``apply``(suggested_status ありの提案)は §5.4 と同一の内部処理
  (ステータス更新+初回 ``done`` の ``finished_at`` 自動記録)をこのルータ内で行う
  (``library_items`` ルータは他タスクが並行編集中のため、更新系はそちら側の関数を直接
  呼ばず本ルータ内に複製し、読み出し専用の ``_summary_for`` のみ再利用する)。
  ``promote_revision`` バリアント(B→A 昇格)の ``apply`` は plans/05 §12.3 の入口
  (「通知→『変更する』→ ``POST /api/papers/{paper_id}/reingest``」)と同一の内部処理を行う:
  ``mode='reingest'`` の ingest ジョブを ``adopt_on_complete=true`` で enqueue する。
  reingest 完了時に worker(``pipeline.py``。M1-22/M1-07 followup)が adopt-revision と
  同一の処理(``papers.latest_revision_id`` 切替+``reanchor_paper``)を自動実行する
  (通常の reingest では立てないフラグのため自動適用にはならない。P6)。arxiv_id 未設定・
  稼働中 ingest が既にある場合は best-effort でスキップし、通知の resolved 消化自体は
  失敗させない。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from alinea_core.db.models import LibraryItem, Notification, Paper
from alinea_core.jobs.store import JobStore
from fastapi import APIRouter, Query
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from alinea_api.deps import CurrentUser, DbDep
from alinea_api.errors import ProblemException
from alinea_api.routers.ingest import JobWakeupDep, _active_ingest_job
from alinea_api.routers.library_items import _summary_for
from alinea_api.schemas.common import LibraryItemSummary, decode_cursor, encode_cursor
from alinea_api.schemas.notifications import (
    NotificationActionBody,
    NotificationActionResponse,
    NotificationListResponse,
    NotificationOut,
    NotificationPatch,
    ReadAllResponse,
    notification_to_out,
)
from alinea_api.services.user_service import count_unread_notifications

router = APIRouter(tags=["notifications"])


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _get_owned_notification(db: DbDep, user_id: str, notification_id: str) -> Notification:
    try:
        nid = int(notification_id)
    except (ValueError, TypeError):
        raise ProblemException("not_found") from None
    note = await db.get(Notification, nid)
    if note is None or str(note.user_id) != str(user_id):
        raise ProblemException("not_found")
    return note


async def _owned_item(db: DbDep, user_id: str, item_id: str) -> LibraryItem | None:
    if not _valid_uuid(item_id):
        return None
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user_id):
        return None
    return item


async def _safe_summary(db: DbDep, user_id: str, item_id: str) -> LibraryItemSummary | None:
    item = await _owned_item(db, user_id, item_id)
    if item is None:
        return None
    # library_items ルータの読み出し専用ヘルパを再利用(progress/last_position 計算の重複を避ける)。
    return await _summary_for(db, item)


async def _apply_suggested_status(
    db: DbDep, user_id: str, item_id: str, suggested_status: str
) -> LibraryItemSummary | None:
    """§5.4 と同一の内部処理(ステータス更新+初回 done の finished_at 自動記録)。"""
    item = await _owned_item(db, user_id, item_id)
    if item is None:
        return None
    item.status = suggested_status
    if suggested_status == "done" and item.finished_at is None:
        item.finished_at = dt.datetime.now(dt.UTC)
    await db.flush()
    return await _summary_for(db, item)


async def _enqueue_promotion_reingest(
    db: DbDep, wakeup: JobWakeupDep, user_id: str, item_id: str
) -> str | None:
    """B→A 昇格提案の apply: plans/05 §12.3 の入口(``POST /api/papers/{paper_id}/reingest``)と
    同一の ingest ジョブを ``adopt_on_complete=true`` で enqueue する。

    reingest 完了時、worker(pipeline.py)が structuring 段の最終処理として adopt-revision
    と同一の内部処理(``papers.latest_revision_id`` 切替+``reanchor_paper``)を自動実行する
    (通常の再取り込みボタンでは ``adopt_on_complete`` を立てないため自動適用にはならない。P6)。
    論文が見つからない・arxiv_id 未設定・既に稼働中の ingest がある場合は best-effort で
    None を返す(通知の resolved 消化自体は失敗させない)。
    """
    item = await _owned_item(db, user_id, item_id)
    if item is None:
        return None
    paper = await db.get(Paper, item.paper_id)
    if paper is None or not paper.arxiv_id:
        return None
    if await _active_ingest_job(db, str(paper.id)) is not None:
        return None

    store = JobStore(db)
    try:
        job_id = await store.enqueue(
            kind="ingest",
            payload={
                "mode": "reingest",
                "source": "arxiv",
                "arxiv_id": paper.arxiv_id,
                "url": None,
                "library_item_id": item_id,
                "adopt_on_complete": True,
            },
            priority="bulk",
            user_id=user_id,
            paper_id=str(paper.id),
            library_item_id=item_id,
        )
    except IntegrityError:
        # uq_jobs_ingest_active: 競合で稼働中 ingest が挿入済み → best-effort でスキップ。
        await db.rollback()
        return None
    await wakeup(job_id)
    return job_id


# ============================================================================
# 一覧(§16.1)
# ============================================================================
@router.get(
    "/api/notifications",
    response_model=NotificationListResponse,
    operation_id="notifications_list",
)
async def list_notifications(
    user: CurrentUser,
    db: DbDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> NotificationListResponse:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if cursor:
        try:
            data = decode_cursor(cursor)
            created_at = dt.datetime.fromisoformat(str(data["k"]))
            last_id = int(data["id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ProblemException("validation_error", detail="カーソルが不正です") from exc
        stmt = stmt.where(
            or_(
                Notification.created_at < created_at,
                and_(Notification.created_at == created_at, Notification.id < last_id),
            )
        )
    stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1)
    rows = (await db.execute(stmt)).scalars().all()
    has_next = len(rows) > limit
    kept = rows[:limit]

    next_cursor: str | None = None
    if has_next:
        last = kept[-1]
        next_cursor = encode_cursor(last.created_at.isoformat(), str(last.id))

    unread = await count_unread_notifications(db, user.id)
    return NotificationListResponse(
        items=[notification_to_out(n) for n in kept],
        next_cursor=next_cursor,
        unread=unread,
    )


# ============================================================================
# 既読化(§16.2)
# ============================================================================
@router.patch(
    "/api/notifications/{notification_id}",
    response_model=NotificationOut,
    operation_id="notifications_update",
)
async def patch_notification(
    notification_id: str, body: NotificationPatch, user: CurrentUser, db: DbDep
) -> NotificationOut:
    note = await _get_owned_notification(db, user.id, notification_id)
    note.read = body.read
    await db.commit()
    await db.refresh(note)
    return notification_to_out(note)


# ============================================================================
# すべて既読(§16.3)
# ============================================================================
@router.post(
    "/api/notifications/read-all",
    response_model=ReadAllResponse,
    operation_id="notifications_read_all",
)
async def read_all(user: CurrentUser, db: DbDep) -> ReadAllResponse:
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read.is_(False))
        .values(read=True)
    )
    await db.commit()
    return ReadAllResponse(updated=int(result.rowcount or 0))


# ============================================================================
# 提案の 2 択(§16.4)
# ============================================================================
@router.post(
    "/api/notifications/{notification_id}/action",
    response_model=NotificationActionResponse,
    operation_id="notifications_action",
)
async def notification_action(
    notification_id: str,
    body: NotificationActionBody,
    user: CurrentUser,
    db: DbDep,
    wakeup: JobWakeupDep,
) -> NotificationActionResponse:
    note = await _get_owned_notification(db, user.id, notification_id)

    if note.kind != "status_suggestion":
        raise ProblemException(
            "validation_error", detail="action は status_suggestion のみ有効です"
        )

    payload = dict(note.payload) if isinstance(note.payload, dict) else {}
    if payload.get("resolved") is not None:
        raise ProblemException("conflict", detail="この提案はすでに処理済みです")

    library_item_id = str(payload.get("library_item_id", ""))
    summary: LibraryItemSummary | None = None
    job_id: str | None = None

    if body.action == "dismiss":
        payload["resolved"] = "dismissed"
        summary = await _safe_summary(db, user.id, library_item_id)
    else:
        payload["resolved"] = "applied"
        if payload.get("action") == "promote_revision":
            # plans/05 §12.3 の入口と同一の ingest ジョブを adopt_on_complete=true で
            # enqueue する(§6.8 adopt-revision と同一の内部処理は worker 側で完了時に実行。
            # M1-22/M1-07 followup)。自動適用ではない(P6): このジョブは本 apply クリックが
            # 唯一の発生源。
            job_id = await _enqueue_promotion_reingest(db, wakeup, user.id, library_item_id)
            summary = await _safe_summary(db, user.id, library_item_id)
        else:
            suggested_status = payload.get("suggested_status")
            if suggested_status:
                summary = await _apply_suggested_status(
                    db, user.id, library_item_id, str(suggested_status)
                )
            else:
                summary = await _safe_summary(db, user.id, library_item_id)

    note.payload = payload
    note.read = True
    await db.commit()
    await db.refresh(note)

    return NotificationActionResponse(
        notification=notification_to_out(note), library_item=summary, job_id=job_id
    )
