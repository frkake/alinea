"""読書時間計測 API のサービス層(M1-05。plans/03 §5.9・plans/07 §8・docs/06 §2/§6.5)。

- ``record_heartbeat``: ``POST /api/library-items/{id}/reading-sessions`` 本体。
  開始・30 秒間隔ハートビート・(タブを閉じる直前の)実質的な終了はすべて同一契約(§5.9)で
  クライアントから呼ばれる。``reading_sessions`` テーブル(0001 DDL・読み取り専用)には
  ``client_session_id`` 列が無いため、``(library_item_id, started_at)`` の複合キー
  (0001 の ``ix_reading_sessions_item_started`` が示す想定アクセスパターン)で upsert する。
  クライアントは 1 ブラウジングセッション内で ``started_at`` を固定して送る(§8.1 手順1)ため、
  同一 ``client_session_id`` のハートビート再送は自然にこのキーへ収束し、Redis 等の追加の
  一時状態を必要としない(冪等)。
- ``total_active_seconds`` への加算は「今回の active_seconds(セッション内累計)- 直前に記録
  した値」の差分のみ(リトライで同一値を再送しても二重加算しない)。
- 3 分ルール(§8.1)・読了間近提案(§8.2)を同一トランザクション内で判定する(LLM 不使用・
  ジョブ化しない)。読了間近の判定は仕様上 ``PUT .../position``(translations.py。他タスク
  所有)内の同期処理だが、本タスクの所有ファイルはそこに含まれないため、本モジュールの
  ハートビート呼び出しのたびに(= 実質的な「終了」を含む毎呼び出しで)判定する
  (deviations 参照。位置自体は position 保存 API が書き込んだ ``item.reading_position`` を
  そのまま読む)。
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

import redis.asyncio as redis
from alinea_core.db.models import DocumentRevision, LibraryItem, Paper, ReadingSession, User
from alinea_core.document.blocks import DocumentContent, Section
from alinea_core.translation.pipeline import compute_translation_scope
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_api.schemas.settings import DEFAULTS, deep_merge
from alinea_api.services.notifications import fire_status_suggestion

# §8.1: 「読んでいる」提案の閾値(3 分ちょうどを含む)。
ACTIVE_RULE_SECONDS = 180
# §8.2: 本文内位置(本文ブロック中の序数 / 本文ブロック総数)の閾値。
REACHED_END_RATIO = 0.90


# ============================================================================
# リクエスト/レスポンス DTO(plans/03 §5.9)
# ============================================================================
class ReadingHeartbeatBody(BaseModel):
    client_session_id: str
    started_at: str
    last_activity_at: str
    active_seconds: int

    @field_validator("active_seconds")
    @classmethod
    def _v_active(cls, v: int) -> int:
        if v < 0:
            raise ValueError("active_seconds は 0 以上である必要があります")
        return v

    @field_validator("started_at", "last_activity_at")
    @classmethod
    def _v_ts(cls, v: str) -> str:
        _parse_ts(v)  # 形式検証のみ(不正なら ValueError → 422 validation_error)。
        return v


class ReadingHeartbeatResponse(BaseModel):
    reading_seconds_total: int
    today_reading_minutes: int


def _parse_ts(value: str) -> dt.datetime:
    v = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = dt.datetime.fromisoformat(v)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


# ============================================================================
# 設定の読み取り(users.settings.reading.*)
# ============================================================================
def _reading_settings(user: User) -> dict[str, object]:
    merged = deep_merge(DEFAULTS, user.settings or {})
    reading = merged.get("reading")
    return reading if isinstance(reading, dict) else {}


# ============================================================================
# reading_sessions の upsert(§8.1 手順 3)
# ============================================================================
async def _load_or_create_session_row(
    db: AsyncSession, *, library_item_id: str, started_at: dt.datetime
) -> ReadingSession:
    row = (
        await db.execute(
            select(ReadingSession)
            .where(
                ReadingSession.library_item_id == library_item_id,
                ReadingSession.started_at == started_at,
            )
            .order_by(ReadingSession.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = ReadingSession(library_item_id=library_item_id, started_at=started_at, active_seconds=0)
    db.add(row)
    await db.flush()
    return row


async def _today_reading_minutes(db: AsyncSession, user_id: str) -> int:
    """当日(UTC)の ReadingSession active_seconds 合計を分に(§5.9 today_reading_minutes)。"""
    today = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    total = await db.scalar(
        select(func.coalesce(func.sum(ReadingSession.active_seconds), 0))
        .join(LibraryItem, LibraryItem.id == ReadingSession.library_item_id)
        .where(LibraryItem.user_id == user_id, ReadingSession.started_at >= today)
    )
    return int(total or 0) // 60


# ============================================================================
# §8.2: 読了間近の判定(本文ブロックの序数 / 総数 ≥ 0.90 かつ本文最終セクション内)
# ============================================================================
def _top_level_ancestors(content: DocumentContent) -> dict[str, str]:
    """section_id → 属するトップレベルセクションの id。"""
    mapping: dict[str, str] = {}

    def walk(sec: Section, top_id: str) -> None:
        mapping[sec.id] = top_id
        for sub in sec.sections:
            walk(sub, top_id)

    for top in content.sections:
        walk(top, top.id)
    return mapping


def _leaf_section_of(content: DocumentContent, block_id: str) -> str | None:
    for sec, blk in content.iter_blocks():
        if blk.id == block_id:
            return sec.id
    return None


async def _reached_end(db: AsyncSession, item: LibraryItem) -> bool:
    rp = item.reading_position
    if not isinstance(rp, dict) or not rp.get("revision_id") or not rp.get("block_id"):
        return False
    revision = await db.get(DocumentRevision, str(rp["revision_id"]))
    if revision is None:
        return False
    try:
        content = DocumentContent.model_validate(revision.content)
    except (ValueError, TypeError):
        return False

    scope = compute_translation_scope(content)
    if not scope.sections:
        return False
    body_order: list[str] = [bid for sec in scope.sections for bid in sec["block_ids"]]
    total = len(body_order)
    block_id = str(rp["block_id"])
    if total == 0 or block_id not in body_order:
        return False  # 本文ブロック外(参考文献・付録)→ 対象外(§8.2)
    pos = body_order.index(block_id) + 1
    if pos / total < REACHED_END_RATIO:
        return False

    top_of = _top_level_ancestors(content)
    section_id = _leaf_section_of(content, block_id)
    last_section_id = scope.sections[-1]["section_id"]
    return section_id is not None and top_of.get(section_id) == top_of.get(last_section_id)


# ============================================================================
# ステータス自動遷移(§5.4 規則)・提案(§8.1/§8.2)
# ============================================================================
def _apply_status_auto(item: LibraryItem, status: Literal["reading", "done"]) -> None:
    item.status = status
    # 初めて done になった時点で finished_at を自動記録(以後上書きしない。§5.4)。
    if status == "done" and item.finished_at is None:
        item.finished_at = dt.datetime.now(dt.UTC)


async def _maybe_suggest_status(
    db: AsyncSession, r: redis.Redis, *, user: User, item: LibraryItem
) -> None:
    transition = _reading_settings(user).get("status_transition", "suggest")
    if transition == "off":
        return

    paper = await db.get(Paper, item.paper_id)
    paper_title = paper.title if paper is not None else ""

    # --- 「読んでいる」提案(3 分ルール。§8.1) ---
    if item.status in ("planned", "up_next") and item.total_active_seconds >= ACTIVE_RULE_SECONDS:
        if transition == "auto":
            _apply_status_auto(item, "reading")
        else:
            await fire_status_suggestion(
                db,
                r,
                user_id=str(user.id),
                library_item_id=str(item.id),
                paper_title=paper_title,
                reason="read_3min",
                suggested_status="reading",
            )

    # --- 読了間近の提案(§8.2) ---
    if item.status == "reading" and await _reached_end(db, item):
        if transition == "auto":
            _apply_status_auto(item, "done")
        else:
            await fire_status_suggestion(
                db,
                r,
                user_id=str(user.id),
                library_item_id=str(item.id),
                paper_title=paper_title,
                reason="reached_end",
                suggested_status="done",
            )


# ============================================================================
# エントリポイント
# ============================================================================
async def record_heartbeat(
    db: AsyncSession,
    r: redis.Redis,
    *,
    user: User,
    item: LibraryItem,
    body: ReadingHeartbeatBody,
) -> ReadingHeartbeatResponse:
    """POST /api/library-items/{id}/reading-sessions(§5.9)。

    ``reading.track_reading_time=false`` のときは記録せず現在値をそのまま返す(§5.9 決定)。
    """
    if _reading_settings(user).get("track_reading_time", True) is False:
        return ReadingHeartbeatResponse(
            reading_seconds_total=item.total_active_seconds,
            today_reading_minutes=await _today_reading_minutes(db, str(user.id)),
        )

    started_at = _parse_ts(body.started_at)
    last_activity_at = _parse_ts(body.last_activity_at)

    row = await _load_or_create_session_row(db, library_item_id=str(item.id), started_at=started_at)
    delta = max(0, body.active_seconds - row.active_seconds)
    row.active_seconds = body.active_seconds
    row.ended_at = last_activity_at
    item.total_active_seconds = (item.total_active_seconds or 0) + delta
    await db.flush()

    await _maybe_suggest_status(db, r, user=user, item=item)

    await db.commit()
    await db.refresh(item)
    return ReadingHeartbeatResponse(
        reading_seconds_total=item.total_active_seconds,
        today_reading_minutes=await _today_reading_minutes(db, str(user.id)),
    )
