"""collections API テスト(M2-09 / plans/03 §13・docs/06 §4)。

PY-COL-01: コレクション CRUD・entries の position 並べ替え・締切残日数・進捗集計
(done/total)・担当(assignee/assignee_is_self)・発表時間・予備注記・共有リンクの発行/
無効化/再発行・重複追加 409・並べ替え不足 422 を検証する。

DB は実 PostgreSQL。認証はセッション直発行 + cookie(test_library_api.py と同じ方式)。
他タスクの WIP ルータを巻き込まないよう、本タスク所有のルータ(collections)のみを
マウントした専用アプリで検証する(main.py への登録は article レーンの担当。followups 参照)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.services.deadlines import today_jst
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email
from yakudoku_core.db.models import User


def _build_app() -> FastAPI:
    """本タスク所有ルータ(collections)のみをマウントしたアプリ。"""
    from yakudoku_api.errors import register_exception_handlers
    from yakudoku_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from yakudoku_api.ratelimit import RateLimitMiddleware
    from yakudoku_api.redis_client import get_redis
    from yakudoku_api.routers import collections
    from yakudoku_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(collections.router)
    return app


@pytest_asyncio.fixture
async def auth(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str]]:
    email = f"col-{uuid.uuid4().hex}@example.com"
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


# ---------------------------------------------------------------------------
# CRUD(§13.1)
# ---------------------------------------------------------------------------
async def test_create_get_patch_delete_collection(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, _uid = auth

    resp = await client.post(
        "/api/collections", json={"name": " 輪読会 2026-07 ", "description": "初回"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "輪読会 2026-07"  # trim される
    assert body["description"] == "初回"
    assert body["deadline"] is None
    assert body["days_left"] is None
    assert body["progress"] == {"done": 0, "total": 0}
    assert body["share"] == {
        "status": "none",
        "token": None,
        "url": None,
        "include_notes": False,
        "included_note_count": 0,
    }
    assert body["entries"] == []
    collection_id = body["id"]

    resp = await client.get(f"/api/collections/{collection_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "輪読会 2026-07"

    deadline = (today_jst() + dt.timedelta(days=10)).isoformat()
    resp = await client.patch(
        f"/api/collections/{collection_id}", json={"deadline": deadline, "description": None}
    )
    assert resp.status_code == 200, resp.text
    patched = resp.json()
    assert patched["deadline"] == deadline
    assert patched["days_left"] == 10
    assert patched["description"] is None

    resp = await client.delete(f"/api/collections/{collection_id}")
    assert resp.status_code == 204, resp.text

    resp = await client.get(f"/api/collections/{collection_id}")
    assert resp.status_code == 404, resp.text


async def test_get_collection_404_for_other_users(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, _uid = auth
    other_user = await factories.make_user(db_session)
    other_collection = await factories.make_collection(db_session, user=other_user)
    await db_session.commit()

    try:
        resp = await client.get(f"/api/collections/{other_collection.id}")
        assert resp.status_code == 404, resp.text
    finally:
        await purge_user(db_session, str(other_user.id))
        await db_session.commit()


async def test_create_collection_rejects_blank_name(auth: tuple[AsyncClient, str]) -> None:
    client, _uid = auth
    resp = await client.post("/api/collections", json={"name": "   "})
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "validation_error"


async def test_patch_collection_rejects_invalid_deadline_format(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    coll = await factories.make_collection(db_session, user=user)
    await db_session.commit()

    resp = await client.patch(f"/api/collections/{coll.id}", json={"deadline": "07/16/2026"})
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 一覧(§13.1)・進捗集計・締切残日数
# ---------------------------------------------------------------------------
async def test_list_collections_ordered_by_created_at_with_progress_and_days_left(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    done_item = await factories.make_library_item(db_session, user=user, status="done")
    todo_item = await factories.make_library_item(db_session, user=user, status="planned")
    await db_session.commit()

    deadline = today_jst() + dt.timedelta(days=5)
    coll1 = await factories.make_collection(
        db_session, user=user, name="First", entries_of=[done_item, todo_item], deadline=deadline
    )
    # Postgres の now() はトランザクション開始時刻のため、同一トランザクションで作ると
    # created_at が同値になり並びが id(UUID)次第で不定になる。コミットで時刻を分離する。
    await db_session.commit()
    coll2 = await factories.make_collection(db_session, user=user, name="Second")
    await db_session.commit()

    resp = await client.get("/api/collections")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [it["id"] for it in items] == [str(coll1.id), str(coll2.id)]
    assert items[0]["item_count"] == 2
    assert items[0]["done_count"] == 1
    assert items[0]["days_left"] == 5
    assert items[0]["deadline"] == deadline.isoformat()
    assert items[1]["item_count"] == 0
    assert items[1]["deadline"] is None
    assert items[1]["days_left"] is None


async def test_collection_detail_progress_and_entry_fields(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None

    item_a = await factories.make_library_item(db_session, user=user, status="done")
    item_b = await factories.make_library_item(db_session, user=user, status="reading")
    coll = await factories.make_collection(db_session, user=user, entries_of=[item_a, item_b])
    await db_session.commit()

    resp = await client.get(f"/api/collections/{coll.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["progress"] == {"done": 1, "total": 2}
    entries = body["entries"]
    assert [e["order"] for e in entries] == [1, 2]
    assert entries[0]["library_item"]["id"] == str(item_a.id)
    assert entries[0]["assignee"] is None
    assert entries[0]["assignee_is_self"] is False
    assert entries[0]["presentation_minutes"] is None
    assert entries[0]["note"] is None


# ---------------------------------------------------------------------------
# entries(§13.2): 追加・重複 409・担当/発表時間/注記・削除・並べ替え
# ---------------------------------------------------------------------------
async def test_add_entry_appends_and_rejects_duplicate(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    existing = await factories.make_library_item(db_session, user=user)
    coll = await factories.make_collection(db_session, user=user, entries_of=[existing])
    new_item = await factories.make_library_item(db_session, user=user)
    await db_session.commit()

    resp = await client.post(
        f"/api/collections/{coll.id}/entries", json={"library_item_id": str(new_item.id)}
    )
    assert resp.status_code == 201, resp.text
    added = resp.json()
    assert added["order"] == 2
    assert added["library_item"]["id"] == str(new_item.id)

    resp = await client.post(
        f"/api/collections/{coll.id}/entries", json={"library_item_id": str(new_item.id)}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "duplicate"


async def test_add_entry_rejects_other_users_library_item(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    coll = await factories.make_collection(db_session, user=user)
    other_user = await factories.make_user(db_session)
    other_item = await factories.make_library_item(db_session, user=other_user)
    await db_session.commit()

    try:
        resp = await client.post(
            f"/api/collections/{coll.id}/entries", json={"library_item_id": str(other_item.id)}
        )
        assert resp.status_code == 404, resp.text
    finally:
        await purge_user(db_session, str(other_user.id))
        await db_session.commit()


async def test_patch_entry_updates_assignee_and_presentation_fields(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    item = await factories.make_library_item(db_session, user=user)
    coll = await factories.make_collection(db_session, user=user, entries_of=[item])
    await db_session.commit()
    resp = await client.get(f"/api/collections/{coll.id}")
    entry_id = resp.json()["entries"][0]["id"]

    resp = await client.patch(
        f"/api/collection-entries/{entry_id}",
        json={
            "assignee": None,
            "assignee_is_self": True,
            "presentation_minutes": 25,
            "note": "予備(時間があれば)",
        },
    )
    assert resp.status_code == 200, resp.text
    patched = resp.json()
    assert patched["assignee"] is None
    assert patched["assignee_is_self"] is True
    assert patched["presentation_minutes"] == 25
    assert patched["note"] == "予備(時間があれば)"

    resp = await client.patch(
        f"/api/collection-entries/{entry_id}", json={"assignee": "佐藤", "assignee_is_self": False}
    )
    assert resp.status_code == 200, resp.text
    patched2 = resp.json()
    assert patched2["assignee"] == "佐藤"
    assert patched2["assignee_is_self"] is False
    # 未指定フィールドは変化しない(部分更新)。
    assert patched2["presentation_minutes"] == 25
    assert patched2["note"] == "予備(時間があれば)"


async def test_delete_entry_removes_from_collection_but_keeps_library_item(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    item = await factories.make_library_item(db_session, user=user)
    coll = await factories.make_collection(db_session, user=user, entries_of=[item])
    await db_session.commit()
    entry_id = (await client.get(f"/api/collections/{coll.id}")).json()["entries"][0]["id"]

    resp = await client.delete(f"/api/collection-entries/{entry_id}")
    assert resp.status_code == 204, resp.text

    detail = (await client.get(f"/api/collections/{coll.id}")).json()
    assert detail["entries"] == []

    library_item = await db_session.get(type(item), item.id)
    assert library_item is not None  # LibraryItem 自体は削除されない(§13.1)


async def test_reorder_entries_updates_order_and_rejects_incomplete_ids(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    item1 = await factories.make_library_item(db_session, user=user)
    item2 = await factories.make_library_item(db_session, user=user)
    item3 = await factories.make_library_item(db_session, user=user)
    coll = await factories.make_collection(db_session, user=user, entries_of=[item1, item2, item3])
    await db_session.commit()

    detail = (await client.get(f"/api/collections/{coll.id}")).json()
    entries = detail["entries"]
    e1, e2, e3 = (e["id"] for e in entries)

    resp = await client.put(
        f"/api/collections/{coll.id}/entries/order", json={"entry_ids": [e3, e1, e2]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}

    reordered = (await client.get(f"/api/collections/{coll.id}")).json()["entries"]
    assert [e["id"] for e in reordered] == [e3, e1, e2]
    assert [e["order"] for e in reordered] == [1, 2, 3]

    resp = await client.put(
        f"/api/collections/{coll.id}/entries/order", json={"entry_ids": [e1, e2]}
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 共有リンク(§13.3)
# ---------------------------------------------------------------------------
async def test_share_issue_patch_revoke_and_reissue_gets_new_token(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    item = await factories.make_library_item(db_session, user=user)
    coll = await factories.make_collection(db_session, user=user, entries_of=[item])
    await db_session.commit()

    resp = await client.post(f"/api/collections/{coll.id}/share")
    assert resp.status_code == 201, resp.text
    share1 = resp.json()
    assert share1["status"] == "active"
    assert share1["token"] is not None
    assert len(share1["token"]) == 8
    assert share1["token"].isalnum()
    assert share1["url"] is not None and share1["url"].endswith(f"/c/{share1['token']}")
    assert share1["include_notes"] is False
    assert share1["included_note_count"] == 0

    # 発行済みでの再発行は 409。
    resp = await client.post(f"/api/collections/{coll.id}/share")
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "conflict"

    resp = await client.patch(f"/api/collections/{coll.id}/share", json={"include_notes": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["include_notes"] is True

    resp = await client.delete(f"/api/collections/{coll.id}/share")
    assert resp.status_code == 204, resp.text

    detail = (await client.get(f"/api/collections/{coll.id}")).json()
    assert detail["share"]["status"] == "revoked"
    assert detail["share"]["token"] is None
    assert detail["share"]["url"] is None

    resp = await client.post(f"/api/collections/{coll.id}/share")
    assert resp.status_code == 201, resp.text
    share2 = resp.json()
    assert share2["token"] != share1["token"]  # 再発行は新しい token(§13.3)


async def test_share_included_note_count_counts_nonempty_one_line_notes(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    with_note = await factories.make_library_item(db_session, user=user)
    with_note.one_line_note = "共有用のひとことメモ"
    without_note = await factories.make_library_item(db_session, user=user)
    coll = await factories.make_collection(
        db_session, user=user, entries_of=[with_note, without_note]
    )
    await db_session.commit()

    resp = await client.post(f"/api/collections/{coll.id}/share")
    assert resp.status_code == 201, resp.text
    assert resp.json()["included_note_count"] == 1


async def test_share_patch_and_revoke_without_active_share_404(
    auth: tuple[AsyncClient, str], factories: Any, db_session: AsyncSession
) -> None:
    client, uid = auth
    user = await db_session.get(User, uid)
    assert user is not None
    coll = await factories.make_collection(db_session, user=user)
    await db_session.commit()

    resp = await client.patch(f"/api/collections/{coll.id}/share", json={"include_notes": True})
    assert resp.status_code == 404, resp.text

    resp = await client.delete(f"/api/collections/{coll.id}/share")
    assert resp.status_code == 404, resp.text
