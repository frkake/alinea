"""notifications — 一覧・既読化・read-all・提案 2 択 action(plans/03 §16)。

- 一覧: ``cursor``(不透明・created_at 基準)+ ``limit``(既定 20・最大 50)。並びは
  ``created_at`` 降順。未読件数はベル UI 用に ``count_unread_notifications``
  (``/api/auth/me`` の ``unread_notifications`` と同一計算)を再利用する。
- action: ``status_suggestion`` の 2 択のみ有効(それ以外 422)。resolved 済みの再操作は
  409 ``conflict``。``apply``(suggested_status ありの提案)は §5.4 と同一の内部処理
  (ステータス更新+初回 ``done`` の ``finished_at`` 自動記録)をこのルータ内で行う
  (``library_items`` ルータは他タスクが並行編集中のため、更新系はそちら側の関数を直接
  呼ばず本ルータ内に複製し、読み出し専用の ``_summary_for`` のみ再利用する)。
  ``promote_revision`` バリアント(B→A 昇格)の ``apply`` は §6.8 adopt-revision と
  同一の内部処理とされているが、adopt-revision・reanchor は M1-22 の所有物のため未接続
  (followup)。本タスクでは resolved の消化のみ行う。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import and_, or_, select, update
from yakudoku_core.db.models import LibraryItem, Notification

from yakudoku_api.deps import CurrentUser, DbDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.routers.library_items import _summary_for
from yakudoku_api.schemas.common import LibraryItemSummary, decode_cursor, encode_cursor
from yakudoku_api.schemas.notifications import (
    NotificationActionBody,
    NotificationActionResponse,
    NotificationListResponse,
    NotificationOut,
    NotificationPatch,
    ReadAllResponse,
    notification_to_out,
)
from yakudoku_api.services.user_service import count_unread_notifications

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
    notification_id: str, body: NotificationActionBody, user: CurrentUser, db: DbDep
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

    if body.action == "dismiss":
        payload["resolved"] = "dismissed"
        summary = await _safe_summary(db, user.id, library_item_id)
    else:
        payload["resolved"] = "applied"
        if payload.get("action") == "promote_revision":
            # §6.8 adopt-revision と同一の内部処理(M1-22 の所有物。reanchor 込みで未接続)。
            # ここでは提案の resolved 消化のみ行う(followup: M1-22 で adopt-revision に接続)。
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

    return NotificationActionResponse(notification=notification_to_out(note), library_item=summary)
