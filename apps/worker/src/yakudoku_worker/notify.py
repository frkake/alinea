"""worker 側の通知発火(plans/05 §12.1 translation_complete)。

apps/api/services/notifications.py と同じ意味論(opt-out・job_id 単位の 1 回限り・
SSE ``notification.created``)を、apps 間 import 禁止のため worker 側に再実装する。
形式の正は API 側実装と cron.py の既存パターン(_insert_promotion_notification)。
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_core.db.models import Notification, User

from yakudoku_worker.bootstrap import _publish_event

log = structlog.get_logger(__name__)


def _notifications_enabled(settings: dict[str, Any] | None, key: str) -> bool:
    """``users.settings.notifications.<key>`` が明示 false でないこと(既定 true)。"""
    notifications = (settings or {}).get("notifications")
    if not isinstance(notifications, dict):
        return True
    return notifications.get(key, True) is not False


async def fire_translation_complete(
    session: AsyncSession,
    r: Any,  # redis.asyncio.Redis(RedisLike と構造非一致のため Any。cron.py と同方針)
    *,
    user_id: str,
    library_item_id: str,
    paper_title: str,
    job_id: str,
) -> Notification | None:
    """§12.1: ingest ジョブ complete 時に発火。同一 ``job_id`` では 1 回だけ挿入する。"""
    user = await session.get(User, user_id)
    if user is None or not _notifications_enabled(user.settings, "translation_complete"):
        return None

    existing = await session.execute(
        select(Notification.id).where(
            Notification.user_id == user_id,
            Notification.kind == "translation_complete",
            Notification.payload["job_id"].astext == job_id,
        )
    )
    if existing.first() is not None:
        return None

    note = Notification(
        user_id=user_id,
        kind="translation_complete",
        payload={
            "library_item_id": library_item_id,
            "paper_title": paper_title,
            "job_id": job_id,
        },
    )
    session.add(note)
    await session.flush()
    await session.commit()
    await session.refresh(note)

    if r is not None:
        out_payload = {"kind": note.kind, **dict(note.payload or {})}
        try:
            await _publish_event(
                r,
                str(note.user_id),
                "notification.created",
                {"notification_id": str(note.id), "kind": note.kind, "payload": out_payload},
            )
        except Exception as exc:
            await log.awarning("translation_complete_publish_failed", error=str(exc))
    return note
