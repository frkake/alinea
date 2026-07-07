"""共通スキーマ: cursor ページング封筒と SSE イベントの整形(plans/03 §1.5・§1.9)。"""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import BaseModel


class CursorPage[T](BaseModel):
    """cursor 方式の一覧レスポンス封筒。`next_cursor` は次ページが無ければ null。

    `total` は件数表示が仕様にある一覧(ライブラリ・検索・通知)でのみ返す。
    """

    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


def encode_cursor(sort_key: Any, tiebreaker_id: str) -> str:
    """base64url(JSON: {"k": <ソートキー値>, "id": "<tiebreaker ID>"})。"""
    raw = json.dumps({"k": sort_key, "id": tiebreaker_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """不透明カーソルを復号。壊れていれば ValueError。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
    if not isinstance(data, dict) or "id" not in data:
        raise ValueError("invalid cursor")
    return data


def sse_frame(
    *,
    data: str,
    event: str | None = None,
    event_id: str | None = None,
) -> str:
    """1 つの SSE フレーム文字列(末尾に空行)を組み立てる。data は 1 行 JSON を想定。"""
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    if event is not None:
        lines.append(f"event: {event}")
    for chunk in data.splitlines() or [""]:
        lines.append(f"data: {chunk}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def sse_json_frame(
    payload: dict[str, Any],
    *,
    event: str | None = None,
    event_id: str | None = None,
) -> str:
    return sse_frame(
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        event=event,
        event_id=event_id,
    )


SSE_HEADERS: dict[str, str] = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


# --- plans/03 §1.7 の共通レスポンス型(library / viewer 両ルータが共有) ---


class PaperBib(BaseModel):
    """plans/03 §1.7 PaperBib。"""

    id: str
    title: str
    authors: list[str]
    authors_short: str
    venue: str | None = None
    year: int | None = None
    arxiv_id: str | None = None
    arxiv_version: str | None = None
    doi: str | None = None
    license: str
    visibility: str
    abstract: str


class LastPosition(BaseModel):
    """plans/03 §1.7 LastPosition。mode: translation|parallel|source|pdf|article。"""

    revision_id: str
    block_id: str
    mode: str
    section_display: str
    saved_at: str


class PipelineState(BaseModel):
    """plans/03 §1.7 PipelineState(処理完了後は null)。"""

    job_id: str
    stage: str
    status: str
    progress_pct: int
    readable_upto: str | None = None
    failed_reason: str | None = None


class LibraryItemSummary(BaseModel):
    """plans/03 §1.7 LibraryItemSummary。"""

    id: str
    paper: PaperBib
    status: str
    priority: str | None = None
    deadline: str | None = None
    tags: list[str]
    suggested_tags: list[str]
    quality_level: str
    source: str  # "arxiv" | "upload"
    progress_pct: int
    comprehension: int | None = None
    importance: str | None = None
    reading_seconds_total: int
    one_line_note: str | None = None
    summary_3line: list[str] | None = None
    thumbnail_url: str | None = None
    pipeline: PipelineState | None = None
    last_position: LastPosition | None = None
    added_at: str
    updated_at: str
    finished_at: str | None = None
