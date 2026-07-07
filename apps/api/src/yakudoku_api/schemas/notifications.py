"""notifications エンドポイントの DTO(plans/03 §16・plans/02 §3.7 NotificationPayloadJson)。

- DB(``notifications.payload``)は plans/02 §3.7 の素の形(``kind`` を含まない)で持つ。
- API レスポンスの ``payload`` は plans/03 §16.1 のとおり ``kind`` を埋め込んだ形(FE の
  discriminated union 用)。``notification_to_out`` がこの写像を担う。
- ``status_suggestion`` の 2 バリアント(3 分ルール/読了間近の提案 と B→A 昇格提案)は
  フィールド集合が異なるため、緩い ``dict[str, Any]`` で表現する(jobs スキーマの
  ``JobOut.result`` と同じ方針)。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel
from yakudoku_core.db.models import Notification

NtfKind = Literal["translation_complete", "status_suggestion", "deadline_reminder"]
ResolvedState = Literal["applied", "dismissed"]


class NotificationOut(BaseModel):
    """plans/03 §16.1 Notification。"""

    id: str
    kind: NtfKind
    read: bool
    created_at: str
    payload: dict[str, Any]


class NotificationListResponse(BaseModel):
    items: list[NotificationOut]
    next_cursor: str | None = None
    unread: int


class NotificationPatch(BaseModel):
    """PATCH /api/notifications/{id}(plans/03 §16.2)。既読化のみ。"""

    read: Literal[True]


class ReadAllResponse(BaseModel):
    updated: int


class NotificationActionBody(BaseModel):
    """POST /api/notifications/{id}/action(plans/03 §16.4)。提案の 2 択。"""

    action: Literal["apply", "dismiss"]


class NotificationActionResponse(BaseModel):
    notification: NotificationOut
    library_item: Any | None = None  # LibraryItemSummary | None(schemas.common の型を使用)


def notification_to_out(note: Notification) -> NotificationOut:
    """DB 行 → API 表現。payload に ``kind`` を埋め込む(§16.1)。"""
    raw = dict(note.payload) if isinstance(note.payload, dict) else {}
    payload = {"kind": note.kind, **raw}
    return NotificationOut(
        id=str(note.id),
        kind=note.kind,  # DB は TEXT+CHECK、API は Literal(値は一致するため実行時に検証される)
        read=note.read,
        created_at=note.created_at.isoformat(),
        payload=payload,
    )
