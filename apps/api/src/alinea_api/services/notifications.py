"""notifications の発火関数(plans/05 §12・plans/03 §16)。

- ``fire_translation_complete``: 取り込み完了時(§12.1)。同一 ``job_id`` では 1 回だけ発火
  (§2.3 の「1 回限り保証」)。
- ``fire_status_suggestion``: ステータス変更提案(3 分ルール/読了間近)・B→A 昇格提案の
  いずれも ``kind='status_suggestion'`` で発火する(plans/02 §3.7)。前者は同一論文への
  同種提案を再度出さない(docs/06 §2「1 回だけ出す」)。後者は plans/05 §12.3 のとおり
  「未読が既にあれば挿入しない」(既読後・7 日後の再検知では再度出せる)。

いずれも session(``AsyncSession``)+ 発火に必要なペイロード値のみを受け取る関数として実装し、
呼び出し側は ``user_id`` だけ知っていればよい(``User`` 行の事前ロードを要求しない)。

- 通知作成後、``services.events.publish_event`` で ``events:user:{user_id}`` に
  ``notification.created {notification_id, kind, payload}``(plans/01 §5・plans/05 §12.1)を
  発行し、SSE 経由でベル UI に即時反映させる(M1-08 が購読)。
- ``users.settings.notifications.<kind>`` が明示 ``false`` のときは発火しない(plans/05 §12.1)。

注(deviations): 呼び出し元は本来 2 つある——(a) 読書時間計測のハートビート(M1-05。
reason=read_3min/reached_end)、(b) B→A 昇格検知の arq cron ``check_quality_promotions``
(M1-22。worker 側)。worker(apps/worker)は apps/api を import できない(apps 間 import 禁止)
ため、worker から本モジュールを直接呼べない。理想は packages/py-core に純ロジックを置くことだが、
本タスクでは py-core/db/models.py 等が読み取り専用のため、API 側 service として実装した。
worker からの利用経路(py-core への切り出し、または worker→API 内部呼び出し)は M1-22 の
followup とする。
"""

from __future__ import annotations

from typing import Any, Literal

import redis.asyncio as redis
from alinea_core.db.models import Notification, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_api.schemas.notifications import notification_to_out
from alinea_api.schemas.settings import DEFAULTS, deep_merge
from alinea_api.services.events import publish_event

StatusSuggestionReason = Literal["read_3min", "reached_end", "promotion_b_to_a"]


def _notifications_enabled(user: User, key: str) -> bool:
    """``users.settings.notifications.<key>`` が明示 false でないこと(plans/05 §12.1)。"""
    merged = deep_merge(DEFAULTS, user.settings or {})
    notifications = merged.get("notifications")
    if not isinstance(notifications, dict):
        return True
    return notifications.get(key, True) is not False


async def _publish_created(r: redis.Redis, note: Notification) -> None:
    out = notification_to_out(note)
    await publish_event(
        r,
        str(note.user_id),
        "notification.created",
        {"notification_id": out.id, "kind": out.kind, "payload": out.payload},
    )


async def fire_translation_complete(
    db: AsyncSession,
    r: redis.Redis,
    *,
    user_id: str,
    library_item_id: str,
    paper_title: str,
    job_id: str,
) -> Notification | None:
    """§12.1: ingest ジョブ complete 時に発火(既訳流用で即完了した場合も同様)。

    ``job_id`` 単位で 1 回だけ挿入する(同一 job に対する重複呼び出しは no-op)。
    """
    user = await db.get(User, user_id)
    if user is None or not _notifications_enabled(user, "translation_complete"):
        return None

    existing = await db.execute(
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
    db.add(note)
    await db.flush()
    await db.commit()
    await db.refresh(note)
    await _publish_created(r, note)
    return note


async def fire_status_suggestion(
    db: AsyncSession,
    r: redis.Redis,
    *,
    user_id: str,
    library_item_id: str,
    paper_title: str,
    reason: StatusSuggestionReason,
    suggested_status: Literal["reading", "done"] | None = None,
    revision_id: str | None = None,
) -> Notification | None:
    """§2(3 分ルール/読了間近の提案)・plans/05 §12.3(B→A 昇格提案)を発火する。

    - ``reason in {"read_3min", "reached_end"}``: ``suggested_status`` 必須。同一論文への
      同種提案は(既読・resolved 済みでも)二度と出さない(docs/06 §2「1 回だけ出す」)。
    - ``reason == "promotion_b_to_a"``: ``revision_id``(現行 B リビジョン)必須。
      同一 library_item への同種提案の**未読**が既にあれば挿入しない(plans/05 §12.3)。
    """
    user = await db.get(User, user_id)
    if user is None or not _notifications_enabled(user, "status_suggestion"):
        return None

    payload: dict[str, Any] = {
        "library_item_id": library_item_id,
        "paper_title": paper_title,
        "reason": reason,
        "resolved": None,
    }
    dedupe_conditions = [
        Notification.user_id == user_id,
        Notification.kind == "status_suggestion",
        Notification.payload["library_item_id"].astext == library_item_id,
        Notification.payload["reason"].astext == reason,
    ]
    if reason == "promotion_b_to_a":
        if not revision_id:
            raise ValueError("promotion_b_to_a には revision_id が必須です")
        payload["action"] = "promote_revision"
        payload["revision_id"] = revision_id
        dedupe_conditions.append(Notification.read.is_(False))
    else:
        if not suggested_status:
            raise ValueError("read_3min/reached_end には suggested_status が必須です")
        payload["suggested_status"] = suggested_status

    existing = await db.execute(select(Notification.id).where(*dedupe_conditions))
    if existing.first() is not None:
        return None

    note = Notification(user_id=user_id, kind="status_suggestion", payload=payload)
    db.add(note)
    await db.flush()
    await db.commit()
    await db.refresh(note)
    await _publish_created(r, note)
    return note
