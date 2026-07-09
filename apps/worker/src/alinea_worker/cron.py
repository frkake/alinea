"""B→A 昇格提案の検出 cron(plans/05 §12.3・M1-22 (c)。判定規則は M2-01/M2-02 でも不変)。

``check_quality_promotions``: 品質 B かつ ``arxiv_id`` を持つ Paper のうち、LaTeX ソース
または公式 HTML が新たに取得可能になったものを検出し、``status_suggestion``
(``reason=promotion_b_to_a``・``action=promote_revision``)通知を挿入する。判定規則自体は
plans/05 §12.3 で「確定」済みのため M2-01(LaTeX パーサ実装)後も変更しない
(``probe_latex_available`` の HEAD 判定 + 24h キャッシュのままで十分。存在確認のみで
実パースまでは検証しない — 誤検知時は reingest 後にパイプライン側が HTML へ可視的に
フォールバックするため P3 を満たす)。ユーザーが通知の「変更する」から
``POST /api/papers/{paper_id}/reingest`` を叩くと、``alinea_worker.pipeline`` の
取得優先順位(LaTeX > HTML > PDF。M2-01)が実際に LaTeX ソースをパースして品質 A の
リビジョンへ昇格させる(本 cron は検出+通知のみを担い、実運用の「接続」はパイプライン側で完結する)。

**自動適用しない**(P6)。適用(新リビジョン作成→切替+リアンカー)はユーザー操作
(通知→「変更する」→ reingest → ``POST /api/library-items/{id}/adopt-revision``。
plans/03 §6.8)によってのみ行われる。本 cron は検出+通知のみを担う。

apps/worker は apps/api を import できない(Global Constraints)ため、通知 INSERT + SSE
publish の形式は ``apps/api/src/alinea_api/services/notifications.py`` の
``fire_status_suggestion``(plans/05 §12.3 の実装)と一致させて複製する(同モジュールの
deviations 注記が本 cron からの呼び出し経路を指す)。

再確認間隔は Redis ``promo:checked:{paper_id}``(TTL 7 日)で間引く(§12.3)。
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import redis.asyncio as redis
import structlog
from alinea_core.arxiv.fetch import RedisLike, Throttle, arxiv_throttle, probe_latex_available
from alinea_core.arxiv.ids import normalize_arxiv_id
from alinea_core.db.models import (
    Collection,
    CollectionEntry,
    DocumentRevision,
    LibraryItem,
    Notification,
    Paper,
    User,
)
from alinea_core.settings import CoreSettings, get_settings
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alinea_worker.bootstrap import _publish_event
from alinea_worker.notify import _notifications_enabled

log = structlog.get_logger("alinea.worker.cron")

# 再確認間隔(§12.3。7 日)。
_PROMO_CHECK_TTL_S = 7 * 24 * 3600
_PROMO_CHECK_KEY_PREFIX = "promo:checked:"

# send_deadline_reminders(plans/01 §4.3。毎日 08:00 JST)の同日重複抑制(TTL 20 時間。
# 次回 08:00 JST cron 実行までの間に同じコレクションへ 2 通送らないための間引き)。
_DEADLINE_SENT_TTL_S = 20 * 3600
_DEADLINE_SENT_KEY_PREFIX = "deadline:sent:"
_JST = ZoneInfo("Asia/Tokyo")


async def _candidate_papers(session: AsyncSession) -> list[Paper]:
    """最新リビジョンが quality_level='B' かつ arxiv_id を持つ Paper 一覧(§12.3)。"""
    rows = (
        await session.execute(
            select(Paper)
            .join(DocumentRevision, DocumentRevision.id == Paper.latest_revision_id)
            .where(Paper.arxiv_id.is_not(None), DocumentRevision.quality_level == "B")
        )
    ).scalars()
    return list(rows.all())


async def _official_html_available(
    arxiv_id: str,
    version: int | None,
    *,
    http: httpx.AsyncClient,
    settings: CoreSettings,
    redis_client: RedisLike,
    throttle: Throttle,
) -> bool:
    """``HEAD https://arxiv.org/html/{id}{v}`` が 200(公式 HTML あり。§12.3 の判定②)。"""
    base = (settings.alinea_arxiv_base_url or "https://arxiv.org").rstrip("/")
    versioned = f"{arxiv_id}v{version}" if version is not None else arxiv_id
    await throttle(redis_client)
    try:
        resp = await http.head(f"{base}/html/{versioned}", follow_redirects=True, timeout=6.0)
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


async def _promotion_available(
    paper: Paper,
    *,
    http: httpx.AsyncClient,
    settings: CoreSettings,
    redis_client: RedisLike,
    throttle: Throttle,
) -> bool:
    """「A 化可能」判定(§12.3。LaTeX 有無 **または** 公式 HTML 200)。"""
    assert paper.arxiv_id is not None
    ref = normalize_arxiv_id(paper.arxiv_id)
    if await probe_latex_available(
        ref, redis=redis_client, http=http, settings=settings, throttle=throttle
    ):
        return True
    return await _official_html_available(
        ref.id,
        ref.version,
        http=http,
        settings=settings,
        redis_client=redis_client,
        throttle=throttle,
    )


async def _notification_recipients(session: AsyncSession, paper_id: str) -> list[str]:
    """当該 Paper を持つ全ユーザー(library_items.user_id)。plans/05 §12.3 の「全ユーザー」。"""
    from alinea_core.db.models import LibraryItem

    rows = (
        await session.execute(select(LibraryItem.user_id).where(LibraryItem.paper_id == paper_id))
    ).scalars()
    return [str(uid) for uid in rows.all()]


async def _insert_promotion_notification(
    session: AsyncSession, r: redis.Redis, *, user_id: str, paper: Paper, current_revision_id: str
) -> Notification | None:
    """``fire_status_suggestion``(promotion_b_to_a)と同一形式で通知を INSERT する。

    同一 library_item への同種提案の未読が既にあれば挿入しない(plans/05 §12.3)。
    """
    from alinea_core.db.models import LibraryItem

    library_item_id = await session.scalar(
        select(LibraryItem.id).where(
            LibraryItem.user_id == user_id, LibraryItem.paper_id == paper.id
        )
    )
    if library_item_id is None:
        return None
    library_item_id = str(library_item_id)

    payload: dict[str, Any] = {
        "library_item_id": library_item_id,
        "paper_title": paper.title,
        "reason": "promotion_b_to_a",
        "resolved": None,
        "action": "promote_revision",
        "revision_id": current_revision_id,
    }
    existing = await session.execute(
        select(Notification.id).where(
            Notification.user_id == user_id,
            Notification.kind == "status_suggestion",
            Notification.payload["library_item_id"].astext == library_item_id,
            Notification.payload["reason"].astext == "promotion_b_to_a",
            Notification.read.is_(False),
        )
    )
    if existing.first() is not None:
        return None

    note = Notification(user_id=user_id, kind="status_suggestion", payload=payload)
    session.add(note)
    await session.flush()
    await session.commit()
    await session.refresh(note)
    await _publish_notification_created(r, note)
    return note


async def _publish_notification_created(r: redis.Redis, note: Notification) -> None:
    """SSE ``notification.created``(``services/notifications.py._publish_created`` と同形式)。"""
    out_payload = {"kind": note.kind, **dict(note.payload or {})}
    try:
        await _publish_event(
            r,
            str(note.user_id),
            "notification.created",
            {"notification_id": str(note.id), "kind": note.kind, "payload": out_payload},
        )
    except Exception as exc:
        await log.awarning("promotion_notification_publish_failed", error=str(exc))


async def check_quality_promotions(ctx: dict[str, Any]) -> None:
    """arq cron 本体(worker-bulk、毎日 07:30 JST)。plans/05 §12.3。"""
    settings: CoreSettings = ctx.get("settings") or get_settings()
    maker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    # redis.Redis は RedisLike と構造的に厳密一致しないため型注釈を付けない
    # (ctx は dict[str, Any] なので代入自体は Any 経由で安全。pipeline.deps_from_ctx と同方針)。
    redis_client = ctx["redis"]
    http: httpx.AsyncClient | None = ctx.get("arxiv_http")
    throttle: Throttle = ctx.get("throttle", arxiv_throttle)

    owns_http = http is None

    async def _run(client: httpx.AsyncClient) -> None:
        async with maker() as session:
            candidates = await _candidate_papers(session)
            for paper in candidates:
                check_key = f"{_PROMO_CHECK_KEY_PREFIX}{paper.id}"
                if await redis_client.get(check_key) is not None:
                    continue  # 7 日以内に確認済み(§12.3)
                await redis_client.set(check_key, b"1", ex=_PROMO_CHECK_TTL_S)

                try:
                    available = await _promotion_available(
                        paper,
                        http=client,
                        settings=settings,
                        redis_client=redis_client,
                        throttle=throttle,
                    )
                except Exception as exc:
                    await log.awarning(
                        "promotion_probe_failed", paper_id=str(paper.id), error=str(exc)
                    )
                    continue
                if not available:
                    continue

                assert paper.latest_revision_id is not None
                current_revision_id = str(paper.latest_revision_id)
                for user_id in await _notification_recipients(session, str(paper.id)):
                    await _insert_promotion_notification(
                        session,
                        redis_client,
                        user_id=user_id,
                        paper=paper,
                        current_revision_id=current_revision_id,
                    )

    if owns_http:
        from alinea_core.arxiv.fetch import make_arxiv_client

        async with make_arxiv_client(settings) as client:
            await _run(client)
    else:
        assert http is not None
        await _run(http)


# ============================================================================
# send_deadline_reminders(M2-09。plans/01 §4.3・plans/03 §16.1・docs/06 §7)
# ============================================================================
async def _due_collections(session: AsyncSession, today: dt.date) -> list[Collection]:
    """締切設定済み・未超過(``deadline >= today``)のコレクション全件(全ユーザー)。

    「超過分は表示しない」(plans/09-screens/1d §4.7 の decision)と同じ扱いで、締切を過ぎた
    コレクションはリマインドしない(4b で全量を確認できる)。
    """
    rows = (
        await session.execute(
            select(Collection).where(Collection.deadline.is_not(None), Collection.deadline >= today)
        )
    ).scalars()
    return list(rows.all())


async def _unstarted_count(session: AsyncSession, collection_id: str) -> int:
    """未着手件数の近似値(payload の ``unstarted_count``。docs/06 §7 本文の「未着手 n 本」)。

    **決定**: apps/worker は apps/api の進捗計算(``library_items._progress``。DocumentRevision
    のブロック順走査が必要)を import できない(Global Constraints)ため、
    ``reading_position IS NULL AND status != 'done'`` を「未着手」の代理指標として使う
    (plans/09-screens/4b §3.3 の厳密な ``isUnstarted``(progress_pct===0 && status!=='done')
    の簡略版。ビューアを一度も開いていない大半のケースでは一致する)。
    """
    return (
        await session.execute(
            select(func.count())
            .select_from(CollectionEntry)
            .join(LibraryItem, LibraryItem.id == CollectionEntry.library_item_id)
            .where(
                CollectionEntry.collection_id == collection_id,
                LibraryItem.status != "done",
                LibraryItem.reading_position.is_(None),
            )
        )
    ).scalar_one()


async def _insert_deadline_reminder(
    session: AsyncSession,
    r: Any,
    *,
    collection: Collection,
    days_left: int,
    unstarted_count: int,
) -> Notification:
    """plans/02 §3.7 payload 形式で ``deadline_reminder`` 通知を INSERT する。"""
    payload: dict[str, Any] = {
        "collection_id": str(collection.id),
        "collection_name": collection.name,
        "days_left": days_left,
        "unstarted_count": unstarted_count,
    }
    note = Notification(user_id=str(collection.user_id), kind="deadline_reminder", payload=payload)
    session.add(note)
    await session.flush()
    await session.commit()
    await session.refresh(note)
    await _publish_notification_created(r, note)
    return note


async def send_deadline_reminders(ctx: dict[str, Any]) -> None:
    """arq cron 本体(worker-bulk、毎日 08:00 JST。plans/01 §4.3)。

    締切が設定され未超過のコレクションのうち、未着手エントリが 1 件以上あるものについて、
    オーナーへ ``deadline_reminder`` 通知を発火する。``notifications.deadline_reminder``
    (既定 true。4f §…)が明示 false のユーザーには送らない(``notify._notifications_enabled``
    と同一判定)。Redis TTL キー(20 時間)で同日内の重複実行(cron リトライ等)を間引く
    (**決定**: 締切が近づく間は毎日新しいリマインドを送る仕様のため、間引きは「同じ暦日に
    2 通送らない」ことのみを保証する。7 日間引きの check_quality_promotions とは意図が異なる)。
    """
    maker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    redis_client = ctx["redis"]
    today = dt.datetime.now(dt.UTC).astimezone(_JST).date()

    async with maker() as session:
        for collection in await _due_collections(session, today):
            assert collection.deadline is not None
            left = (collection.deadline - today).days

            check_key = f"{_DEADLINE_SENT_KEY_PREFIX}{collection.id}:{today.isoformat()}"
            if await redis_client.get(check_key) is not None:
                continue  # 同日内に送信済み(冪等)

            unstarted = await _unstarted_count(session, str(collection.id))
            if unstarted == 0:
                continue

            user = await session.get(User, collection.user_id)
            if user is None or not _notifications_enabled(user.settings, "deadline_reminder"):
                continue

            await redis_client.set(check_key, b"1", ex=_DEADLINE_SENT_TTL_S)
            await _insert_deadline_reminder(
                session,
                redis_client,
                collection=collection,
                days_left=left,
                unstarted_count=unstarted,
            )


__all__ = ["check_quality_promotions", "send_deadline_reminders"]
