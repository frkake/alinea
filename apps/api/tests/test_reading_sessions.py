"""reading-sessions API テスト(M1-05 / plans/03 §5.9・plans/07 §8・docs/06 §2)。

- PY-LIB-05: ハートビート 3 分ルールで ``status_suggestion`` 通知(自動変更なし)、設定
  ``reading.status_transition`` = auto/suggest/off の 3 分岐(auto のみ即適用+通知なし、
  off は通知なし)。読了間近(§8.2)の提案も同モジュールが担うため併せて検証する。

DB は実 PostgreSQL・Redis も実インスタンス(``fire_status_suggestion`` が ``publish_event`` で
Redis に書くため)。他タスクの WIP ルータを巻き込まないよう、本タスク所有の
``library_items.router`` のみをマウントした専用アプリで検証する(test_dashboard.py と同方針)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email
from yakudoku_core.db.models import Notification, ReadingSession, User


def _build_app() -> FastAPI:
    """本タスク所有ルータ(library_items)のみをマウントしたアプリ。"""
    from yakudoku_api.errors import register_exception_handlers
    from yakudoku_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from yakudoku_api.ratelimit import RateLimitMiddleware
    from yakudoku_api.redis_client import get_redis
    from yakudoku_api.routers import library_items
    from yakudoku_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(library_items.router)
    return app


@pytest_asyncio.fixture
async def auth(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str]]:
    email = f"rs-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)
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


def _iso(t: dt.datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


async def _set_reading_settings(db: AsyncSession, user: User, patch: dict[str, Any]) -> None:
    settings = dict(user.settings or {})
    reading = dict(settings.get("reading", {}))
    reading.update(patch)
    settings["reading"] = reading
    user.settings = settings
    await db.commit()


# ---------------------------------------------------------------------------
# ハートビートの累計・冪等性(§5.9)
# ---------------------------------------------------------------------------
async def test_heartbeat_accumulates_active_seconds_across_calls(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    started = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=60)
    session_id = str(uuid.uuid4())

    resp1 = await client.post(
        f"/api/library-items/{item.id}/reading-sessions",
        json={
            "client_session_id": session_id,
            "started_at": _iso(started),
            "last_activity_at": _iso(started + dt.timedelta(seconds=30)),
            "active_seconds": 30,
        },
    )
    assert resp1.status_code == 200, resp1.text
    assert resp1.json()["reading_seconds_total"] == 30

    # 同一 client_session_id の 2 回目のハートビート(累計値を送る)。
    resp2 = await client.post(
        f"/api/library-items/{item.id}/reading-sessions",
        json={
            "client_session_id": session_id,
            "started_at": _iso(started),
            "last_activity_at": _iso(started + dt.timedelta(seconds=60)),
            "active_seconds": 60,
        },
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["reading_seconds_total"] == 60

    rows = (
        (
            await db_session.execute(
                select(ReadingSession).where(ReadingSession.library_item_id == item.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1  # (library_item_id, started_at) に upsert される(冪等)。
    assert rows[0].active_seconds == 60


async def test_heartbeat_retry_same_value_does_not_double_count(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    payload = {
        "client_session_id": str(uuid.uuid4()),
        "started_at": _iso(started),
        "last_activity_at": _iso(started),
        "active_seconds": 45,
    }
    resp1 = await client.post(f"/api/library-items/{item.id}/reading-sessions", json=payload)
    assert resp1.json()["reading_seconds_total"] == 45

    # ネットワーク再送を模して同一値を再送 → 二重加算しない。
    resp2 = await client.post(f"/api/library-items/{item.id}/reading-sessions", json=payload)
    assert resp2.json()["reading_seconds_total"] == 45


async def test_heartbeat_track_reading_time_false_skips_recording(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    await _set_reading_settings(db_session, user, {"track_reading_time": False})
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    resp = await client.post(
        f"/api/library-items/{item.id}/reading-sessions",
        json={
            "client_session_id": str(uuid.uuid4()),
            "started_at": _iso(started),
            "last_activity_at": _iso(started),
            "active_seconds": 999,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reading_seconds_total"] == 0

    rows = (
        (
            await db_session.execute(
                select(ReadingSession).where(ReadingSession.library_item_id == item.id)
            )
        )
        .scalars()
        .all()
    )
    assert rows == []  # 記録もしない(§5.9 決定)。


async def test_heartbeat_rejects_invalid_timestamp_422(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    resp = await client.post(
        f"/api/library-items/{item.id}/reading-sessions",
        json={
            "client_session_id": str(uuid.uuid4()),
            "started_at": "not-a-timestamp",
            "last_activity_at": "not-a-timestamp",
            "active_seconds": 10,
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "validation_error"


async def test_heartbeat_other_users_item_404(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, _uid = auth
    other_user = await factories.make_user(db_session)
    other_item = await factories.make_library_item(db_session, user=other_user, status="planned")
    await db_session.commit()

    try:
        started = dt.datetime.now(dt.UTC)
        resp = await client.post(
            f"/api/library-items/{other_item.id}/reading-sessions",
            json={
                "client_session_id": str(uuid.uuid4()),
                "started_at": _iso(started),
                "last_activity_at": _iso(started),
                "active_seconds": 10,
            },
        )
        assert resp.status_code == 404, resp.text
    finally:
        await purge_user(db_session, str(other_user.id))
        await db_session.commit()


# ---------------------------------------------------------------------------
# 3 分ルール(§8.1)・設定 3 分岐(auto/suggest/off)
# ---------------------------------------------------------------------------
async def test_read_3min_rule_suggest_mode_creates_notification_without_changing_status(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(
        db_session, owner=user, visibility="private", title="InstaFlow"
    )
    item = await factories.make_library_item(db_session, user=user, paper=paper, status="up_next")
    item.total_active_seconds = 170  # 既存の累計(3 分ルールに 10 秒残す)。
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    resp = await client.post(
        f"/api/library-items/{item.id}/reading-sessions",
        json={
            "client_session_id": str(uuid.uuid4()),
            "started_at": _iso(started),
            "last_activity_at": _iso(started),
            "active_seconds": 10,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["reading_seconds_total"] == 180

    await db_session.refresh(item)
    assert item.status == "up_next"  # 自動変更しない(P6)。

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == uid, Notification.kind == "status_suggestion"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1
    assert notes[0].payload["reason"] == "read_3min"
    assert notes[0].payload["suggested_status"] == "reading"
    assert notes[0].payload["library_item_id"] == str(item.id)
    assert notes[0].payload["paper_title"] == "InstaFlow"


async def test_read_3min_rule_auto_mode_applies_status_without_notification(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    await _set_reading_settings(db_session, user, {"status_transition": "auto"})
    item = await factories.make_library_item(db_session, user=user, status="planned")
    item.total_active_seconds = 180
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    resp = await client.post(
        f"/api/library-items/{item.id}/reading-sessions",
        json={
            "client_session_id": str(uuid.uuid4()),
            "started_at": _iso(started),
            "last_activity_at": _iso(started),
            "active_seconds": 1,
        },
    )
    assert resp.status_code == 200, resp.text

    await db_session.refresh(item)
    assert item.status == "reading"

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == uid, Notification.kind == "status_suggestion"
                )
            )
        )
        .scalars()
        .all()
    )
    assert notes == []  # auto は通知を作らない(plans/07 §8.1)。


async def test_read_3min_rule_off_mode_does_nothing(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    await _set_reading_settings(db_session, user, {"status_transition": "off"})
    item = await factories.make_library_item(db_session, user=user, status="planned")
    item.total_active_seconds = 200
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    resp = await client.post(
        f"/api/library-items/{item.id}/reading-sessions",
        json={
            "client_session_id": str(uuid.uuid4()),
            "started_at": _iso(started),
            "last_activity_at": _iso(started),
            "active_seconds": 1,
        },
    )
    assert resp.status_code == 200, resp.text

    await db_session.refresh(item)
    assert item.status == "planned"

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == uid, Notification.kind == "status_suggestion"
                )
            )
        )
        .scalars()
        .all()
    )
    assert notes == []


async def test_read_3min_rule_suggested_only_once(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    """§8.1: 同一提案は(既読を問わず)二度と出さない。"""
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    item = await factories.make_library_item(db_session, user=user, status="planned")
    item.total_active_seconds = 500  # 既に閾値超過。
    await db_session.commit()

    for _ in range(2):
        started = dt.datetime.now(dt.UTC)
        resp = await client.post(
            f"/api/library-items/{item.id}/reading-sessions",
            json={
                "client_session_id": str(uuid.uuid4()),
                "started_at": _iso(started),
                "last_activity_at": _iso(started),
                "active_seconds": 1,
            },
        )
        assert resp.status_code == 200, resp.text

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == uid, Notification.kind == "status_suggestion"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1


# ---------------------------------------------------------------------------
# 読了間近の提案(§8.2)
# ---------------------------------------------------------------------------
async def test_reached_end_suggest_mode_creates_done_suggestion(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    # 既定ドキュメント: sec-1(blk-p1,blk-p2)+sec-2(blk-p3,blk-fig1)。
    # blk-fig1 は本文ブロック 4 件中 4 番目(100%)かつ最終セクション(sec-2)内。
    item = await factories.make_library_item(
        db_session,
        user=user,
        paper=paper,
        status="reading",
        reading_position={"revision_id": str(rev.id), "block_id": "blk-fig1"},
    )
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    resp = await client.post(
        f"/api/library-items/{item.id}/reading-sessions",
        json={
            "client_session_id": str(uuid.uuid4()),
            "started_at": _iso(started),
            "last_activity_at": _iso(started),
            "active_seconds": 1,
        },
    )
    assert resp.status_code == 200, resp.text

    await db_session.refresh(item)
    assert item.status == "reading"  # 自動変更しない。

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == uid, Notification.kind == "status_suggestion"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1
    assert notes[0].payload["reason"] == "reached_end"
    assert notes[0].payload["suggested_status"] == "done"


async def test_reached_end_auto_mode_marks_done_and_sets_finished_at(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    await _set_reading_settings(db_session, user, {"status_transition": "auto"})
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(
        db_session,
        user=user,
        paper=paper,
        status="reading",
        reading_position={"revision_id": str(rev.id), "block_id": "blk-fig1"},
    )
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    resp = await client.post(
        f"/api/library-items/{item.id}/reading-sessions",
        json={
            "client_session_id": str(uuid.uuid4()),
            "started_at": _iso(started),
            "last_activity_at": _iso(started),
            "active_seconds": 1,
        },
    )
    assert resp.status_code == 200, resp.text

    await db_session.refresh(item)
    assert item.status == "done"
    assert item.finished_at is not None

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == uid, Notification.kind == "status_suggestion"
                )
            )
        )
        .scalars()
        .all()
    )
    assert notes == []


async def test_reached_end_not_triggered_before_90_percent(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    # blk-p3 は本文ブロック 4 件中 3 番目(75%)。閾値未満のため提案なし。
    item = await factories.make_library_item(
        db_session,
        user=user,
        paper=paper,
        status="reading",
        reading_position={"revision_id": str(rev.id), "block_id": "blk-p3"},
    )
    await db_session.commit()

    started = dt.datetime.now(dt.UTC)
    resp = await client.post(
        f"/api/library-items/{item.id}/reading-sessions",
        json={
            "client_session_id": str(uuid.uuid4()),
            "started_at": _iso(started),
            "last_activity_at": _iso(started),
            "active_seconds": 1,
        },
    )
    assert resp.status_code == 200, resp.text

    notes = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.user_id == uid, Notification.kind == "status_suggestion"
                )
            )
        )
        .scalars()
        .all()
    )
    assert notes == []
