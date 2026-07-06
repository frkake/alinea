"""ユーザー単位イベントの発行と再送(plans/01 §5)。

- Redis Pub/Sub `events:user:{user_id}` に発行(リアルタイム配信)。
- Redis Stream `events:log:{user_id}`(MAXLEN ~1000)にも書き、`Last-Event-ID` 再送に使う。
- SSE イベント `id:` は Stream の単調増加 ID。
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

STREAM_MAXLEN = 1000


def channel_key(user_id: str) -> str:
    return f"events:user:{user_id}"


def stream_key(user_id: str) -> str:
    return f"events:log:{user_id}"


async def publish_event(r: redis.Redis, user_id: str, event_type: str, data: dict[str, Any]) -> str:
    """イベントを Stream に追記し Pub/Sub に発行。付与した Stream ID(= SSE id)を返す。"""
    fields: dict[Any, Any] = {"event": event_type, "data": json.dumps(data, ensure_ascii=False)}
    event_id = await r.xadd(stream_key(user_id), fields, maxlen=STREAM_MAXLEN, approximate=True)
    envelope = json.dumps({"id": event_id, "event": event_type, "data": data}, ensure_ascii=False)
    await r.publish(channel_key(user_id), envelope)
    return str(event_id)


async def read_events_since(
    r: redis.Redis, user_id: str, last_event_id: str
) -> list[tuple[str, str, dict[str, Any]]]:
    """`Last-Event-ID` 以降(排他)のイベントを Stream から取り出す。(id, event, data) のリスト。"""
    if not last_event_id:
        return []
    entries = await r.xrange(stream_key(user_id), min=f"({last_event_id}", max="+")
    result: list[tuple[str, str, dict[str, Any]]] = []
    for entry_id, fields in entries:
        raw = fields.get("data", "{}")
        try:
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            data = {}
        result.append((str(entry_id), fields.get("event", ""), data))
    return result
