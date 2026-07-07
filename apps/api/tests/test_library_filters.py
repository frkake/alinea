"""ライブラリ強化 API テスト(M2-14 / plans/03 §5.6・§5.14、plans/11 §8.3)。

- PY-LIB-07: 保存フィルタ CRUD(名前+条件+ソートの往復・``count`` が導出値で保存されない・
  重複名は 409・他ユーザーからは 404)+ ``filter_id`` 適用(明示クエリが同名項目を上書き)。
- 一括操作(§5.6): set_status / add_tags / add_to_collection の 3 op・全 ID 事前検証で
  不存在・他ユーザー ID が 1 件でもあれば 404 で全体を失敗させる(部分適用しない)・
  add_to_collection は既にあるエントリをスキップし ``updated`` に数えない。

DB は実 PostgreSQL。テストデータは private Paper(owner=テストユーザー)として作り、
teardown の purge_user でカスケード削除する。認証はセッション直発行 + cookie
(test_library_api.py と同方式。並行タスクのルータを巻き込まないよう本タスク所有ルータのみ
マウントする独立アプリを使う)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import factories
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user
from yakudoku_core.db.models import CollectionEntry, SavedFilter, User


def _build_app() -> FastAPI:
    """本タスク所有ルータのみをマウントしたアプリ(test_library_api.py と同方式)。"""
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


class _Auth:
    """クライアント+認証済みユーザー(factories がそのまま使えるよう ``User`` を保持)。"""

    def __init__(self, client: AsyncClient, user: User) -> None:
        self.client = client
        self.user = user
        self.uid = str(user.id)


@pytest_asyncio.fixture
async def auth(db_session: AsyncSession, redis_client: Any) -> AsyncIterator[_Auth]:
    user = await factories.make_user(
        db_session, email=f"lib-filters-{uuid.uuid4().hex}@example.com"
    )
    await db_session.commit()
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
            yield _Auth(ac, user)
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


_SORT_DEFAULT = {"key": "updated_at", "order": "desc"}


# ============================================================================
# PY-LIB-07: 保存フィルタ CRUD(§5.14)
# ============================================================================
async def test_saved_filter_create_round_trips_and_derives_count(
    auth: _Auth, db_session: AsyncSession
) -> None:
    client = auth.client
    await factories.make_library_item(db_session, user=auth.user, status="up_next", tags=["cs.CV"])
    await factories.make_library_item(db_session, user=auth.user, status="planned", tags=["cs.LG"])
    await factories.make_library_item(db_session, user=auth.user, status="reading", tags=["cs.CV"])
    await db_session.commit()

    body = {
        "name": "cs.CV の未読",
        "conditions": {"quick": "unread", "tags": ["cs.CV"]},
        "sort": {"key": "title", "order": "asc"},
    }
    r = await client.post("/api/saved-filters", json=body)
    assert r.status_code == 201
    created = r.json()
    assert created["name"] == "cs.CV の未読"
    assert created["conditions"] == {
        "quick": "unread",
        "tags": ["cs.CV"],
        "status": None,
        "collection_id": None,
        "quality": None,
        "years": None,
    }
    assert created["sort"] == {"key": "title", "order": "asc"}
    # quick=unread(planned+up_next)AND tag=cs.CV に一致するのは 1 件目のみ。
    assert created["count"] == 1

    # count はリクエスト時の導出値であり DB には保存されない(§5.14)。
    row = (
        await db_session.execute(select(SavedFilter).where(SavedFilter.id == created["id"]))
    ).scalar_one()
    assert "count" not in row.conditions
    assert "count" not in row.sort

    # GET 一覧にも同じ導出値で現れる。
    listed = await client.get("/api/saved-filters")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == created["id"]
    assert items[0]["count"] == 1

    # 一致件数が変化すれば count も追随する(保存値ではなく導出値である証明)。
    await factories.make_library_item(db_session, user=auth.user, status="planned", tags=["cs.CV"])
    await db_session.commit()
    listed2 = await client.get("/api/saved-filters")
    assert listed2.json()["items"][0]["count"] == 2


async def test_saved_filter_patch_replaces_conditions_and_sort(
    auth: _Auth, db_session: AsyncSession
) -> None:
    client = auth.client
    r = await client.post(
        "/api/saved-filters",
        json={"name": "締切あり", "conditions": {}, "sort": _SORT_DEFAULT},
    )
    assert r.status_code == 201
    fid = r.json()["id"]
    assert r.json()["conditions"]["quick"] is None

    r2 = await client.patch(
        f"/api/saved-filters/{fid}",
        json={
            "name": "締切あり(更新)",
            "conditions": {"quality": "A", "years": [2023, 2024]},
            "sort": {"key": "deadline", "order": "asc"},
        },
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["name"] == "締切あり(更新)"
    assert body["conditions"]["quality"] == "A"
    assert body["conditions"]["years"] == [2023, 2024]
    assert body["sort"] == {"key": "deadline", "order": "asc"}


async def test_saved_filter_delete_removes_from_list(auth: _Auth) -> None:
    client = auth.client
    r = await client.post(
        "/api/saved-filters", json={"name": "一時フィルタ", "conditions": {}, "sort": _SORT_DEFAULT}
    )
    fid = r.json()["id"]

    d = await client.delete(f"/api/saved-filters/{fid}")
    assert d.status_code == 204

    listed = await client.get("/api/saved-filters")
    assert listed.json()["items"] == []

    # GET 単体は仕様に無い(一覧のみ)。削除済み ID への PATCH は 404 を確認する。
    patch_missing = await client.patch(
        f"/api/saved-filters/{fid}", json={"name": "x", "conditions": {}, "sort": _SORT_DEFAULT}
    )
    assert patch_missing.status_code == 404


async def test_saved_filter_duplicate_name_conflicts(auth: _Auth) -> None:
    client = auth.client
    r1 = await client.post(
        "/api/saved-filters", json={"name": "A", "conditions": {}, "sort": _SORT_DEFAULT}
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/saved-filters", json={"name": "A", "conditions": {}, "sort": _SORT_DEFAULT}
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "duplicate"

    r3 = await client.post(
        "/api/saved-filters", json={"name": "B", "conditions": {}, "sort": _SORT_DEFAULT}
    )
    fid_b = r3.json()["id"]
    # 既存の別フィルタと同名へのリネームも 409。
    r4 = await client.patch(
        f"/api/saved-filters/{fid_b}",
        json={"name": "A", "conditions": {}, "sort": _SORT_DEFAULT},
    )
    assert r4.status_code == 409

    # 自分自身の現在名への PATCH(実質無変更)は衝突と見なさない。
    r5 = await client.patch(
        f"/api/saved-filters/{fid_b}",
        json={"name": "B", "conditions": {"quality": "A"}, "sort": _SORT_DEFAULT},
    )
    assert r5.status_code == 200


async def test_saved_filter_blank_name_is_422(auth: _Auth) -> None:
    client = auth.client
    r = await client.post(
        "/api/saved-filters", json={"name": "   ", "conditions": {}, "sort": _SORT_DEFAULT}
    )
    assert r.status_code == 422


async def test_saved_filter_not_visible_to_other_user(
    auth: _Auth, db_session: AsyncSession, redis_client: Any
) -> None:
    client = auth.client
    r = await client.post(
        "/api/saved-filters", json={"name": "私のフィルタ", "conditions": {}, "sort": _SORT_DEFAULT}
    )
    fid = r.json()["id"]

    other = await factories.make_user(db_session)
    await db_session.commit()
    other_token = await create_session(redis_client, other.id)
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as other_client:
        other_client.cookies.set("yk_session", other_token)
        # 別ユーザーからは一覧にも現れず、PATCH/DELETE は 404(所有チェック。§5.14)。
        listed = await other_client.get("/api/saved-filters")
        assert listed.json()["items"] == []
        patch = await other_client.patch(
            f"/api/saved-filters/{fid}",
            json={"name": "乗っ取り", "conditions": {}, "sort": _SORT_DEFAULT},
        )
        assert patch.status_code == 404
        delete = await other_client.delete(f"/api/saved-filters/{fid}")
        assert delete.status_code == 404

    await purge_user(db_session, str(other.id))


# ============================================================================
# 一括操作(§5.6)
# ============================================================================
async def test_bulk_set_status_updates_all_and_sets_finished_at(
    auth: _Auth, db_session: AsyncSession
) -> None:
    client = auth.client
    it1 = await factories.make_library_item(db_session, user=auth.user, status="reading")
    it2 = await factories.make_library_item(db_session, user=auth.user, status="up_next")
    await db_session.commit()

    r = await client.post(
        "/api/library-items/bulk",
        json={"ids": [str(it1.id), str(it2.id)], "op": "set_status", "status": "done"},
    )
    assert r.status_code == 200
    assert r.json() == {"updated": 2}

    g1 = await client.get(f"/api/library-items/{it1.id}")
    g2 = await client.get(f"/api/library-items/{it2.id}")
    assert g1.json()["status"] == "done"
    assert g1.json()["finished_at"] is not None
    assert g2.json()["status"] == "done"
    assert g2.json()["finished_at"] is not None


async def test_bulk_add_tags_merges_and_dedupes(auth: _Auth, db_session: AsyncSession) -> None:
    client = auth.client
    it1 = await factories.make_library_item(db_session, user=auth.user, tags=["a"])
    await db_session.commit()

    r = await client.post(
        "/api/library-items/bulk",
        json={"ids": [str(it1.id)], "op": "add_tags", "tags": ["a", "b"]},
    )
    assert r.status_code == 200
    assert r.json() == {"updated": 1}

    g1 = await client.get(f"/api/library-items/{it1.id}")
    assert g1.json()["tags"] == ["a", "b"]


async def test_bulk_add_to_collection_skips_existing_and_appends(
    auth: _Auth, db_session: AsyncSession
) -> None:
    client = auth.client
    it1 = await factories.make_library_item(db_session, user=auth.user)
    it2 = await factories.make_library_item(db_session, user=auth.user)
    coll = await factories.make_collection(db_session, user=auth.user, entries_of=[it1])
    await db_session.commit()

    r = await client.post(
        "/api/library-items/bulk",
        json={
            "ids": [str(it1.id), str(it2.id)],
            "op": "add_to_collection",
            "collection_id": str(coll.id),
        },
    )
    assert r.status_code == 200
    # it1 は既にコレクションにあるためスキップ(updated に数えない)。
    assert r.json() == {"updated": 1}

    rows = (
        (
            await db_session.execute(
                select(CollectionEntry)
                .where(CollectionEntry.collection_id == coll.id)
                .order_by(CollectionEntry.position.asc())
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {str(row.library_item_id) for row in rows} == {str(it1.id), str(it2.id)}
    # it2 が末尾(最大 position)に追加されている。
    assert str(rows[-1].library_item_id) == str(it2.id)


async def test_bulk_fails_whole_batch_when_id_missing_or_other_user(
    auth: _Auth, db_session: AsyncSession
) -> None:
    client = auth.client
    it1 = await factories.make_library_item(db_session, user=auth.user, status="reading")
    other_user = await factories.make_user(db_session)
    other_item = await factories.make_library_item(db_session, user=other_user, status="reading")
    await db_session.commit()

    # 不存在 ID を含む。
    r1 = await client.post(
        "/api/library-items/bulk",
        json={"ids": [str(it1.id), str(uuid.uuid4())], "op": "set_status", "status": "done"},
    )
    assert r1.status_code == 404

    # 他ユーザー所有の ID を含む。
    r2 = await client.post(
        "/api/library-items/bulk",
        json={"ids": [str(it1.id), str(other_item.id)], "op": "set_status", "status": "done"},
    )
    assert r2.status_code == 404

    # 部分適用されていないことを確認(it1 は planned→reading のまま未変更)。
    g1 = await client.get(f"/api/library-items/{it1.id}")
    assert g1.json()["status"] == "reading"


async def test_bulk_ids_length_and_op_validation(auth: _Auth) -> None:
    client = auth.client
    too_many = [str(uuid.uuid4()) for _ in range(101)]
    r1 = await client.post(
        "/api/library-items/bulk", json={"ids": too_many, "op": "set_status", "status": "done"}
    )
    assert r1.status_code == 422

    r2 = await client.post("/api/library-items/bulk", json={"ids": [], "op": "set_status"})
    assert r2.status_code == 422

    r3 = await client.post(
        "/api/library-items/bulk", json={"ids": [str(uuid.uuid4())], "op": "bogus"}
    )
    assert r3.status_code == 422


# ============================================================================
# filter_id 適用(§5.1・plans/11 §8.3): 明示クエリが同名項目を上書き
# ============================================================================
async def test_filter_id_applies_conditions_and_explicit_query_overrides(
    auth: _Auth, db_session: AsyncSession
) -> None:
    client = auth.client
    keep = await factories.make_library_item(db_session, user=auth.user, tags=["cs.CV"])
    other = await factories.make_library_item(db_session, user=auth.user, tags=["other"])
    await db_session.commit()

    r = await client.post(
        "/api/saved-filters",
        json={"name": "cs.CV", "conditions": {"tags": ["cs.CV"]}, "sort": _SORT_DEFAULT},
    )
    fid = r.json()["id"]
    assert r.json()["count"] == 1

    applied = await client.get("/api/library-items", params={"filter_id": fid})
    assert applied.status_code == 200
    assert [i["id"] for i in applied.json()["items"]] == [str(keep.id)]

    overridden = await client.get("/api/library-items", params={"filter_id": fid, "tag": "other"})
    assert overridden.status_code == 200
    assert [i["id"] for i in overridden.json()["items"]] == [str(other.id)]
