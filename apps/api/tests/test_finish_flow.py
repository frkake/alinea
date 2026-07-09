"""読了フロー(1g)の API 契約テスト(M1-06 / plans/09-screens/1g・docs/06 §3)。

- PY-LIB-06: ``PATCH /api/library-items/{id}`` で ``status: "done"`` に変更した時点で
  ``finished_at`` が自動記録され、以後(再読 ``reread`` → 再 ``done`` を含む)は不変であること。
  併せて理解度(``comprehension``)・重要度(``importance``)・ひとことメモ(``one_line_note``)が
  同エンドポイントで保存・null 解除できることを確認する(1g §5.2・§4.7)。

他タスクの WIP ルータを巻き込まないよう、本タスク所有の ``library_items.router`` のみを
マウントした専用アプリで検証する(test_reading_sessions.py と同方針)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app() -> FastAPI:
    """本タスク所有ルータ(library_items)のみをマウントしたアプリ。"""
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import library_items
    from alinea_api.settings import get_api_settings

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
    email = f"fin-{uuid.uuid4().hex}@example.com"
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


async def _mk_item(db: AsyncSession, uid: str, *, status: str = "reading") -> str:
    import factories as f
    from alinea_core.db.models import User

    owner = await db.get(User, uid)
    assert owner is not None
    paper = await f.make_paper(db, owner=owner, visibility="private")
    item = await f.make_library_item(db, user=owner, paper=paper, status=status)
    await db.commit()
    return str(item.id)


async def test_finished_at_recorded_once_and_immutable_across_reread(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    """PY-LIB-06: 初回 done で finished_at 記録・再読→再 done でも不変(§5.1・§4.7)。"""
    client, uid = auth
    item_id = await _mk_item(db_session, uid, status="reading")

    r1 = await client.patch(f"/api/library-items/{item_id}", json={"status": "done"})
    assert r1.status_code == 200
    finished_at = r1.json()["finished_at"]
    assert finished_at is not None

    # 再読に戻しても finished_at はそのまま(§5.1 の「再度 done」表と同じ規約)。
    r2 = await client.patch(f"/api/library-items/{item_id}", json={"status": "reread"})
    assert r2.status_code == 200
    assert r2.json()["finished_at"] == finished_at
    assert r2.json()["status"] == "reread"

    # 再び done にしても finished_at は初回値のまま上書きされない。
    r3 = await client.patch(f"/api/library-items/{item_id}", json={"status": "done"})
    assert r3.status_code == 200
    assert r3.json()["finished_at"] == finished_at
    assert r3.json()["status"] == "done"


async def test_comprehension_importance_and_note_are_saved(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    """1g 保存ボタン: comprehension/importance/one_line_note の 3 フィールドが保存される(§5.2・§4.7)。"""
    client, uid = auth
    item_id = await _mk_item(db_session, uid, status="reading")

    done = await client.patch(f"/api/library-items/{item_id}", json={"status": "done"})
    assert done.status_code == 200

    r = await client.patch(
        f"/api/library-items/{item_id}",
        json={
            "comprehension": 4,
            "importance": "high",
            "one_line_note": "reflow は蒸留の前処理として有効。",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["comprehension"] == 4
    assert body["importance"] == "high"
    assert body["one_line_note"] == "reflow は蒸留の前処理として有効。"

    # GET で永続化を再確認(ダイアログ再オープン時のプリフィル元データ)。
    got = await client.get(f"/api/library-items/{item_id}")
    assert got.status_code == 200
    assert got.json()["comprehension"] == 4
    assert got.json()["importance"] == "high"


async def test_comprehension_importance_dot_reclick_clears_to_null(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    """理解度・重要度は明示 null 送信で解除できる(§5.3・§5.4 の再クリック取り消し規則の API 側前提)。"""
    client, uid = auth
    item_id = await _mk_item(db_session, uid, status="reading")
    await client.patch(f"/api/library-items/{item_id}", json={"status": "done"})
    await client.patch(
        f"/api/library-items/{item_id}", json={"comprehension": 3, "importance": "mid"}
    )

    r = await client.patch(
        f"/api/library-items/{item_id}", json={"comprehension": None, "importance": None}
    )
    assert r.status_code == 200
    assert r.json()["comprehension"] is None
    assert r.json()["importance"] is None


async def test_comprehension_out_of_range_is_422(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid, status="reading")
    r = await client.patch(f"/api/library-items/{item_id}", json={"comprehension": 6})
    assert r.status_code == 422
