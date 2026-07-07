"""dashboard API テスト(M1-09 / plans/03 §5.12・§5.7・docs/06 §6)。

- PY-LIB-04: ``GET /api/dashboard`` の 5 区画(続きを読む・すぐ読むキュー・締切・最近追加・統計)
  と ``PUT /api/library-items/queue-order`` の往復・422 バリデーションを検証する。

DB は実 PostgreSQL。テストデータは私有 Paper(owner=テストユーザー)として作り、teardown の
purge_user でカスケード削除する。認証はセッション直発行 + cookie(test_library_api.py と同じ
方式)。他タスクの WIP ルータを巻き込まないよう、本タスク所有のルータのみをマウントした
専用アプリで検証する。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.routers.dashboard import _week_start
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email
from yakudoku_core.db.models import LibraryItem, User


def _build_app() -> FastAPI:
    """本タスク所有ルータ(dashboard・library_items)のみをマウントしたアプリ。

    並行タスクの WIP ルータ(annotations 等)に import を巻き込まれず、本タスクを
    独立に検証する(test_library_api.py と同じ方針)。
    """
    from yakudoku_api.errors import register_exception_handlers
    from yakudoku_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from yakudoku_api.ratelimit import RateLimitMiddleware
    from yakudoku_api.redis_client import get_redis
    from yakudoku_api.routers import dashboard, library_items
    from yakudoku_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(dashboard.router)
    app.include_router(library_items.router)
    return app


@pytest_asyncio.fixture
async def auth(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str]]:
    email = f"dash-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)  # rollback 後に ORM 属性へ触れないよう先に確定させる
    token = await create_session(redis_client, user.id)
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield ac, uid
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


async def _mk_reading_item(
    db: AsyncSession, factories: Any, user: User, *, title: str, block_id: str = "blk-p3"
) -> LibraryItem:
    paper = await factories.make_paper(db, owner=user, visibility="private", title=title)
    rev = await factories.make_revision(db, paper=paper)
    item: LibraryItem = await factories.make_library_item(
        db,
        user=user,
        paper=paper,
        status="reading",
        reading_position={"revision_id": str(rev.id), "block_id": block_id},
    )
    await db.commit()  # 各行の updated_at/added_at を別トランザクションで確定させ、順序を分離する
    return item


# ---------------------------------------------------------------------------
# continue_reading(§5.12「続きを読む」)
# ---------------------------------------------------------------------------
async def test_dashboard_continue_reading_orders_by_recency_max3(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    for i in range(1, 5):
        await _mk_reading_item(db_session, factories, user, title=f"Reading {i}")

    resp = await client.get("/api/dashboard")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    continue_reading = body["continue_reading"]
    assert len(continue_reading) == 3
    got_titles = [it["paper"]["title"] for it in continue_reading]
    # 直近更新順(新しい順)。最古の "Reading 1" は除外される。
    assert got_titles == ["Reading 4", "Reading 3", "Reading 2"]
    # progress_pct は既存ヘルパ(_summary/_progress)の再利用を経由して算出される(5 ブロック中 4 番目)。
    assert continue_reading[0]["progress_pct"] == 80

    # 締切(§5.12)は M2-09 までレスポンス形のみ。
    assert body["deadlines"] == {"collections": [], "items": []}


# ---------------------------------------------------------------------------
# up_next_queue(§5.12「すぐ読むキュー」・§5.7 の手動順)
# ---------------------------------------------------------------------------
async def test_dashboard_up_next_queue_orders_by_queue_order_nulls_last(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    async def _mk_up_next(title: str, queue_order: int | None) -> LibraryItem:
        paper = await factories.make_paper(
            db_session, owner=user, visibility="private", title=title
        )
        item: LibraryItem = await factories.make_library_item(
            db_session, user=user, paper=paper, status="up_next", queue_order=queue_order
        )
        await db_session.commit()
        return item

    await _mk_up_next("U-queue0", 0)
    await _mk_up_next("U-queue2", 2)
    await _mk_up_next("U-null-first", None)
    await _mk_up_next("U-null-second", None)

    resp = await client.get("/api/dashboard")
    assert resp.status_code == 200, resp.text
    titles = [it["paper"]["title"] for it in resp.json()["up_next_queue"]]
    assert titles == ["U-queue0", "U-queue2", "U-null-first", "U-null-second"]


# ---------------------------------------------------------------------------
# recent(§5.12「最近追加」・docs/06 §6.4)
# ---------------------------------------------------------------------------
async def test_dashboard_recent_week_count_max6_and_pipeline(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    recent_items: list[LibraryItem] = []
    for i in range(1, 8):  # 7 件(今週追加。上位 6 件のみ items に載る)
        paper = await factories.make_paper(
            db_session, owner=user, visibility="private", title=f"Recent {i}"
        )
        item = await factories.make_library_item(
            db_session, user=user, paper=paper, status="planned"
        )
        await db_session.commit()
        recent_items.append(item)

    # 今週追加だが 3 週前に取り込んだことにする(集計・items から除外される)。
    old_paper = await factories.make_paper(
        db_session, owner=user, visibility="private", title="Old Item"
    )
    old_item = await factories.make_library_item(
        db_session, user=user, paper=old_paper, status="planned"
    )
    await db_session.commit()
    old_item.added_at = dt.datetime.now(dt.UTC) - dt.timedelta(weeks=3)
    await db_session.commit()

    # 最新(Recent 7)に取り込みジョブを紐付け、パイプライン進捗が反映されることを確認する。
    newest = recent_items[-1]
    await factories.make_job(
        db_session,
        kind="ingest",
        stage="translate_body",
        status="running",
        progress=68,
        user=user,
        library_item=newest,
    )
    await db_session.commit()

    resp = await client.get("/api/dashboard")
    assert resp.status_code == 200, resp.text
    recent = resp.json()["recent"]

    assert recent["week_count"] == 7  # Old Item は数えない
    assert len(recent["items"]) == 6
    titles = [it["paper"]["title"] for it in recent["items"]]
    assert titles[0] == "Recent 7"
    assert "Recent 1" not in titles  # 最古は上位 6 件から外れる
    assert "Old Item" not in titles

    pipeline = recent["items"][0]["pipeline"]
    assert pipeline is not None
    assert pipeline["stage"] == "translate_body"
    assert pipeline["status"] == "running"
    assert pipeline["progress_pct"] == 68

    # ジョブが無いアイテム(Recent 6)は pipeline=None。
    assert titles[1] == "Recent 6"
    assert recent["items"][1]["pipeline"] is None


# ---------------------------------------------------------------------------
# stats(§5.12「統計」・docs/06 §6.5)
# ---------------------------------------------------------------------------
async def test_dashboard_stats_weekly_hours_and_finished_count(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    now = dt.datetime.now(dt.UTC)
    week_start = _week_start(now)

    async def _mk_finished(title: str, finished_at: dt.datetime) -> None:
        paper = await factories.make_paper(
            db_session, owner=user, visibility="private", title=title
        )
        item = await factories.make_library_item(db_session, user=user, paper=paper, status="done")
        await db_session.commit()
        item.finished_at = finished_at
        await db_session.commit()

    await _mk_finished("Finished A", week_start + dt.timedelta(hours=1))
    await _mk_finished("Finished B", week_start + dt.timedelta(hours=2))
    await _mk_finished("Finished Old", week_start - dt.timedelta(days=20))

    reading_item = await factories.make_library_item(db_session, user=user, status="on_hold")
    await db_session.commit()

    # 今週(バケット index 11): 10000s + 5120s = 15120s = 4.2h
    await factories.make_reading_session(
        db_session,
        library_item=reading_item,
        active_seconds=10000,
        started_at=week_start + dt.timedelta(hours=3),
    )
    await factories.make_reading_session(
        db_session,
        library_item=reading_item,
        active_seconds=5120,
        started_at=week_start + dt.timedelta(hours=4),
    )
    # 11 週前(バケット index 0): 3600s = 1.0h
    oldest_bucket_start = week_start - dt.timedelta(weeks=11)
    await factories.make_reading_session(
        db_session,
        library_item=reading_item,
        active_seconds=3600,
        started_at=oldest_bucket_start + dt.timedelta(hours=1),
    )
    # 集計対象範囲(直近 12 週)より前: バケットに現れないこと。
    await factories.make_reading_session(
        db_session,
        library_item=reading_item,
        active_seconds=999_999,
        started_at=week_start - dt.timedelta(weeks=20),
    )
    await db_session.commit()

    resp = await client.get("/api/dashboard")
    assert resp.status_code == 200, resp.text
    stats = resp.json()["stats"]

    assert len(stats["weekly_hours"]) == 12
    assert stats["weekly_hours"][0] == 1.0
    assert stats["weekly_hours"][-1] == 4.2
    assert sum(stats["weekly_hours"]) == pytest.approx(5.2)
    assert stats["week"]["reading_hours"] == stats["weekly_hours"][-1]
    assert stats["week"]["finished_count"] == 2  # Finished Old は数えない


# ---------------------------------------------------------------------------
# PUT /api/library-items/queue-order(§5.7)
# ---------------------------------------------------------------------------
async def test_queue_order_reorders_up_next_items(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    u1 = await factories.make_library_item(db_session, user=user, status="up_next")
    u2 = await factories.make_library_item(db_session, user=user, status="up_next")
    u3 = await factories.make_library_item(db_session, user=user, status="up_next")
    await db_session.commit()

    resp = await client.put(
        "/api/library-items/queue-order",
        json={"library_item_ids": [u3.id, u1.id, u2.id]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}

    rows = (
        await db_session.execute(
            select(LibraryItem.id, LibraryItem.queue_order).where(
                LibraryItem.id.in_([u1.id, u2.id, u3.id])
            )
        )
    ).all()
    order_by_id = {str(rid): qo for rid, qo in rows}
    assert order_by_id[str(u3.id)] == 0
    assert order_by_id[str(u1.id)] == 1
    assert order_by_id[str(u2.id)] == 2


async def test_queue_order_rejects_missing_id_422(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    u1 = await factories.make_library_item(db_session, user=user, status="up_next")
    await factories.make_library_item(db_session, user=user, status="up_next")
    await db_session.commit()

    resp = await client.put("/api/library-items/queue-order", json={"library_item_ids": [u1.id]})
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "validation_error"


async def test_queue_order_rejects_non_up_next_id_422(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    u1 = await factories.make_library_item(db_session, user=user, status="up_next")
    other = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    resp = await client.put(
        "/api/library-items/queue-order",
        json={"library_item_ids": [u1.id, other.id]},
    )
    assert resp.status_code == 422, resp.text


async def test_queue_order_rejects_duplicate_id_422(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    u1 = await factories.make_library_item(db_session, user=user, status="up_next")
    await db_session.commit()

    resp = await client.put(
        "/api/library-items/queue-order",
        json={"library_item_ids": [u1.id, u1.id]},
    )
    assert resp.status_code == 422, resp.text


async def test_queue_order_rejects_other_users_id_422(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    u1 = await factories.make_library_item(db_session, user=user, status="up_next")
    other_user = await factories.make_user(db_session)
    other_item = await factories.make_library_item(db_session, user=other_user, status="up_next")
    await db_session.commit()

    try:
        resp = await client.put(
            "/api/library-items/queue-order",
            json={"library_item_ids": [u1.id, other_item.id]},
        )
        assert resp.status_code == 422, resp.text
    finally:
        await purge_user(db_session, str(other_user.id))
        await db_session.commit()
