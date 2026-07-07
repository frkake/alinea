"""share API テスト(M2-10 / plans/03 §14・docs/09 §4・§5.2)。

PY-SHR-01: 匿名で `GET /api/share/collections/{token}` が 200 になり、revoked/不正/不存在
token は区別なく 404 になる。エントリ順序はコレクションの並び順(position)に一致する。
PY-SHR-02: 応答が書誌+✦要約+許可メモのみを含む(include_notes=false で shared_note が
全件 null、true では one_line_note 非空のエントリのみ非 null)。
PY-SHR-03: 応答に個人資産(進捗・注釈・チャット・リソース・記事・語彙・読書統計)が一切
含まれない。ライセンス不明の論文は summary_3line が null に縮退する(docs/09 §5.2)。

DB は実 PostgreSQL。認証不要のエンドポイントのため、本タスク所有のルータ(share)のみを
マウントした専用アプリ・無認証クライアントで検証する(main.py への登録は article レーンの
担当。followups 参照)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.services.user_service import purge_user
from yakudoku_core.db.models import Collection, CollectionShareToken


def _build_app() -> FastAPI:
    """本タスク所有ルータ(share)のみをマウントしたアプリ。"""
    from yakudoku_api.errors import register_exception_handlers
    from yakudoku_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from yakudoku_api.ratelimit import RateLimitMiddleware
    from yakudoku_api.redis_client import get_redis
    from yakudoku_api.routers import share
    from yakudoku_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(share.router)
    return app


@pytest_asyncio.fixture
async def anon(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """クッキーを持たない匿名クライアント(share は認証不要)。"""
    transport = ASGITransport(app=_build_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        try:
            yield ac
        finally:
            await db_session.rollback()


async def _token_of(db_session: AsyncSession, collection: Collection) -> str:
    """factory が発行した share token 文字列を取得する(commit 前に flush 済みで読める)。"""
    row = (
        await db_session.execute(
            select(CollectionShareToken).where(CollectionShareToken.collection_id == collection.id)
        )
    ).scalar_one()
    return str(row.token)


# ---------------------------------------------------------------------------
# PY-SHR-01: 匿名取得・順序・404
# ---------------------------------------------------------------------------
async def test_anonymous_get_returns_collection_in_order(
    anon: AsyncClient, factories: Any, db_session: AsyncSession
) -> None:
    user = await factories.make_user(db_session, display_name="YK")
    suffix = uuid.uuid4().hex[:6]
    paper1 = await factories.make_paper(
        db_session, title="Consistency Models", license="cc-by-4.0", arxiv_id=f"2303.{suffix}"
    )
    paper1.venue = "ICML"
    paper2 = await factories.make_paper(
        db_session, title="Rectified Flow", license="cc-by-4.0", arxiv_id=f"2209.{suffix}"
    )
    item1 = await factories.make_library_item(db_session, user=user, paper=paper1)
    item2 = await factories.make_library_item(db_session, user=user, paper=paper2)
    coll = await factories.make_collection(
        db_session,
        user=user,
        name="輪読会 2026-07",
        entries_of=[item1, item2],
        with_share_token=True,
    )
    token = await _token_of(db_session, coll)
    await db_session.commit()

    resp = await anon.get(f"/api/share/collections/{token}")
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("X-Robots-Tag") == "noindex"
    body = resp.json()
    assert body["collection"]["name"] == "輪読会 2026-07"
    assert body["collection"]["shared_by"] == "YK"
    assert body["collection"]["item_count"] == 2
    assert body["include_notes"] is False
    assert [it["order"] for it in body["items"]] == [1, 2]
    assert body["items"][0]["title"] == "Consistency Models"
    assert body["items"][0]["venue_year"] == "ICML 2022"  # make_paper 既定 published_on=2022
    assert body["items"][0]["arxiv_url"] == f"https://arxiv.org/abs/2303.{suffix}"
    assert body["items"][1]["title"] == "Rectified Flow"

    await purge_user(db_session, str(user.id))
    await db_session.commit()


async def test_revoked_and_unknown_and_malformed_token_all_404(
    anon: AsyncClient, factories: Any, db_session: AsyncSession
) -> None:
    user = await factories.make_user(db_session)
    item = await factories.make_library_item(db_session, user=user)
    coll = await factories.make_collection(
        db_session, user=user, entries_of=[item], with_share_token=True
    )
    revoked_token = await _token_of(db_session, coll)
    row = (
        await db_session.execute(
            select(CollectionShareToken).where(CollectionShareToken.collection_id == coll.id)
        )
    ).scalar_one()
    row.status = "revoked"
    await db_session.commit()

    resp_revoked = await anon.get(f"/api/share/collections/{revoked_token}")
    resp_missing = await anon.get(f"/api/share/collections/{uuid.uuid4().hex[:8]}")
    resp_malformed = await anon.get("/api/share/collections/not-8-chars")

    for resp in (resp_revoked, resp_missing, resp_malformed):
        assert resp.status_code == 404, resp.text
        assert resp.json()["code"] == "not_found"

    # revoked と不存在で応答本文が区別できない(plans/03 §14.1。instance は要求 URL 由来のため除く)。
    revoked_body = {k: v for k, v in resp_revoked.json().items() if k != "instance"}
    missing_body = {k: v for k, v in resp_missing.json().items() if k != "instance"}
    assert revoked_body == missing_body

    await purge_user(db_session, str(user.id))
    await db_session.commit()


# ---------------------------------------------------------------------------
# PY-SHR-02: include_notes・許可メモのみ
# ---------------------------------------------------------------------------
async def test_include_notes_false_hides_all_notes(
    anon: AsyncClient, factories: Any, db_session: AsyncSession
) -> None:
    user = await factories.make_user(db_session)
    item = await factories.make_library_item(db_session, user=user)
    item.one_line_note = "議論したい点がある"
    coll = await factories.make_collection(
        db_session, user=user, entries_of=[item], with_share_token=True, include_notes=False
    )
    token = await _token_of(db_session, coll)
    await db_session.commit()

    resp = await anon.get(f"/api/share/collections/{token}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["include_notes"] is False
    assert body["items"][0]["shared_note"] is None

    await purge_user(db_session, str(user.id))
    await db_session.commit()


async def test_include_notes_true_shows_only_nonempty_one_line_notes(
    anon: AsyncClient, factories: Any, db_session: AsyncSession
) -> None:
    user = await factories.make_user(db_session)
    with_note = await factories.make_library_item(db_session, user=user)
    with_note.one_line_note = "§2.2 と図2 を中心に議論したい。"
    without_note = await factories.make_library_item(db_session, user=user)
    coll = await factories.make_collection(
        db_session,
        user=user,
        entries_of=[with_note, without_note],
        with_share_token=True,
        include_notes=True,
    )
    token = await _token_of(db_session, coll)
    await db_session.commit()

    resp = await anon.get(f"/api/share/collections/{token}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["include_notes"] is True
    assert body["items"][0]["shared_note"] == "§2.2 と図2 を中心に議論したい。"
    assert body["items"][1]["shared_note"] is None

    await purge_user(db_session, str(user.id))
    await db_session.commit()


# ---------------------------------------------------------------------------
# PY-SHR-03: 個人資産の非包含・ライセンス縮退
# ---------------------------------------------------------------------------
async def test_response_contains_no_personal_assets(
    anon: AsyncClient, factories: Any, db_session: AsyncSession
) -> None:
    user = await factories.make_user(db_session)
    item = await factories.make_library_item(db_session, user=user, status="reading")
    coll = await factories.make_collection(
        db_session, user=user, entries_of=[item], with_share_token=True
    )
    token = await _token_of(db_session, coll)
    await db_session.commit()

    resp = await anon.get(f"/api/share/collections/{token}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"collection", "include_notes", "items"}
    assert set(body["collection"].keys()) == {
        "name",
        "description",
        "shared_by",
        "updated_at",
        "deadline",
        "item_count",
    }
    item_keys = set(body["items"][0].keys())
    assert item_keys == {
        "order",
        "title",
        "authors_short",
        "venue_year",
        "arxiv_url",
        "summary_3line",
        "shared_note",
    }
    # 進捗・注釈・担当・発表時間・読書統計・リソース・記事・語彙のいずれのキーも含まれない。
    forbidden = {
        "progress",
        "status",
        "assignee",
        "presentation_minutes",
        "reading_seconds_total",
        "annotations",
        "resources",
        "article",
        "vocab",
    }
    assert forbidden.isdisjoint(body["items"][0].keys())
    assert forbidden.isdisjoint(body["collection"].keys())

    await purge_user(db_session, str(user.id))
    await db_session.commit()


async def test_unknown_license_degrades_to_bibliography_only(
    anon: AsyncClient, factories: Any, db_session: AsyncSession
) -> None:
    user = await factories.make_user(db_session)
    known = await factories.make_paper(db_session, title="CC BY 論文", license="cc-by-4.0")
    known.summary_lines = ["要約1。", "要約2。", "要約3。"]
    unknown = await factories.make_paper(db_session, title="ライセンス不明論文", license="unknown")
    unknown.summary_lines = ["非表示になるはずの要約。"]
    item_known = await factories.make_library_item(db_session, user=user, paper=known)
    item_unknown = await factories.make_library_item(db_session, user=user, paper=unknown)
    coll = await factories.make_collection(
        db_session,
        user=user,
        entries_of=[item_known, item_unknown],
        with_share_token=True,
    )
    token = await _token_of(db_session, coll)
    await db_session.commit()

    resp = await anon.get(f"/api/share/collections/{token}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    known_item, unknown_item = body["items"]
    assert known_item["title"] == "CC BY 論文"
    assert known_item["summary_3line"] == ["要約1。", "要約2。", "要約3。"]
    assert unknown_item["title"] == "ライセンス不明論文"
    assert unknown_item["summary_3line"] is None  # 書誌のみに縮退(docs/09 §5.2)

    await purge_user(db_session, str(user.id))
    await db_session.commit()
