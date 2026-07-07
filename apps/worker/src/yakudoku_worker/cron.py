"""B→A 昇格提案の検出 cron(plans/05 §12.3・M1-22 (c))。

``check_quality_promotions``: 品質 B かつ ``arxiv_id`` を持つ Paper のうち、LaTeX ソース
または公式 HTML が新たに取得可能になったものを検出し、``status_suggestion``
(``reason=promotion_b_to_a``・``action=promote_revision``)通知を挿入する。

**自動適用しない**(P6)。適用(新リビジョン作成→切替+リアンカー)はユーザー操作
(通知→「変更する」→ reingest → ``POST /api/library-items/{id}/adopt-revision``。
plans/03 §6.8)によってのみ行われる。本 cron は検出+通知のみを担う。

apps/worker は apps/api を import できない(Global Constraints)ため、通知 INSERT + SSE
publish の形式は ``apps/api/src/yakudoku_api/services/notifications.py`` の
``fire_status_suggestion``(plans/05 §12.3 の実装)と一致させて複製する(同モジュールの
deviations 注記が本 cron からの呼び出し経路を指す)。

再確認間隔は Redis ``promo:checked:{paper_id}``(TTL 7 日)で間引く(§12.3)。
"""

from __future__ import annotations

from typing import Any

import httpx
import redis.asyncio as redis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from yakudoku_core.arxiv.fetch import RedisLike, Throttle, arxiv_throttle, probe_latex_available
from yakudoku_core.arxiv.ids import normalize_arxiv_id
from yakudoku_core.db.models import DocumentRevision, Notification, Paper
from yakudoku_core.settings import CoreSettings, get_settings

from yakudoku_worker.bootstrap import _publish_event

log = structlog.get_logger("yakudoku.worker.cron")

# 再確認間隔(§12.3。7 日)。
_PROMO_CHECK_TTL_S = 7 * 24 * 3600
_PROMO_CHECK_KEY_PREFIX = "promo:checked:"


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
    base = (settings.yakudoku_arxiv_base_url or "https://arxiv.org").rstrip("/")
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
    from yakudoku_core.db.models import LibraryItem

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
    from yakudoku_core.db.models import LibraryItem

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
        from yakudoku_core.arxiv.fetch import make_arxiv_client

        async with make_arxiv_client(settings) as client:
            await _run(client)
    else:
        assert http is not None
        await _run(http)


__all__ = ["check_quality_promotions"]
