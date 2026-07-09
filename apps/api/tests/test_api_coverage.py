"""API 経路のカバレッジ補完(library-items / jobs / papers の実装済み M0 経路)。

実 PostgreSQL + セッションクッキー認証で、単一取得・部分更新・削除・重複統合・
ジョブ取得/一覧・取り込みログの分岐を通す。テストデータは factories で私有ユーザーに
ぶら下げ、teardown の purge_user(users カスケード)で全消去する。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import factories
import pytest_asyncio
from alinea_core.db.models import User
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def api(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str]]:
    from alinea_api.main import app
    from alinea_api.services.session_service import COOKIE_NAME, create_session
    from alinea_api.services.user_service import purge_user

    user = await factories.make_user(db_session)
    await db_session.commit()
    uid = str(user.id)
    token = await create_session(redis_client, uid)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        cookies={COOKIE_NAME: token},
        trust_env=False,
    ) as ac:
        try:
            yield ac, uid
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


# ---------------------------------------------------------------------------
# library-items: get / patch(全項目)/ delete(private Paper カスケード)
# ---------------------------------------------------------------------------
async def test_library_item_get_patch_delete(
    api: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = api
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(
        db_session, owner=user, visibility="private", license="unknown"
    )
    await factories.make_revision(db_session, paper=paper, quality_level="B", source_format="pdf")
    item = await factories.make_library_item(
        db_session, user=user, paper=paper, status="reading", suggested_tags=["cs.CV", "cs.LG"]
    )
    item_id = str(item.id)
    await db_session.commit()

    # GET single(_summary_for / _quality_of を通す)。
    r = await client.get(f"/api/library-items/{item_id}")
    assert r.status_code == 200, r.text
    assert r.json()["quality_level"] == "B"

    # PATCH 全項目(status→done で finished_at 自動記録・提案タグ消化)。
    r = await client.patch(
        f"/api/library-items/{item_id}",
        json={
            "status": "done",
            "priority": "high",
            "deadline": "2026-08-01",
            "tags": ["cs.CV"],
            "one_line_note": "重要",
            "comprehension": 4,
            "importance": "high",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done"
    assert body["comprehension"] == 4
    assert "cs.LG" in body["suggested_tags"] and "cs.CV" not in body["suggested_tags"]

    # 提案タグ却下(残りの cs.LG)。
    r = await client.delete(f"/api/library-items/{item_id}/tag-suggestions/cs.LG")
    assert r.status_code == 204

    # DELETE(private Paper は参照ゼロで一緒に消える)。
    r = await client.delete(f"/api/library-items/{item_id}")
    assert r.status_code == 204
    r = await client.get(f"/api/library-items/{item_id}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# library-items: 一覧のソート/フィルタ + 翻訳セットありの進捗サマリ
# ---------------------------------------------------------------------------
async def test_library_list_sort_and_progress(
    api: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = api
    user = await db_session.get(User, uid)
    assert user is not None

    # 翻訳セット + 一部訳済ユニットを持つ item(進捗計算経路)。
    # arxiv_id はユニーク制約(uq_papers_arxiv_id)持ち — シードの正典 ID を使うと
    # rectified-flow 投入済み DB で衝突するため、実行ごとのユニーク値にする。
    paper = await factories.make_paper(
        db_session, owner=user, title="Rectified", arxiv_id=f"9901.{uuid.uuid4().int % 100000:05d}"
    )
    rev = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper, status="reading")
    tset = await factories.make_translation_set(db_session, revision=rev, status="partial")
    await factories.make_translation_unit(db_session, translation_set=tset, block_id="blk-p1")
    # 別の item(ソート確認用)。
    await factories.make_library_item(
        db_session,
        user=user,
        paper=await factories.make_paper(
            db_session, owner=user, title="Other", published_on=dt.date(2020, 1, 1)
        ),
        status="planned",
        deadline=dt.date(2026, 9, 1),
    )
    await db_session.commit()

    for sort, order in [
        ("added_at", "desc"),
        ("title", "asc"),
        ("deadline", "asc"),
        ("priority", "asc"),
    ]:
        r = await client.get(
            "/api/library-items", params={"sort": sort, "order": order, "limit": 10}
        )
        assert r.status_code == 200, r.text
        assert r.json()["total"] >= 2

    # クイックフィルタ(in_progress)+ facets。
    r = await client.get("/api/library-items", params={"quick": "in_progress"})
    assert r.status_code == 200
    ids = [it["id"] for it in r.json()["items"]]
    assert str(item.id) in ids

    r = await client.get("/api/library-items/facets")
    assert r.status_code == 200
    q = r.json()["quick"]
    assert q["all"] == q["unread"] + q["in_progress"] + q["done"] + q["recheck"]


# ---------------------------------------------------------------------------
# library-items: 重複統合(dismiss / merge)
# ---------------------------------------------------------------------------
async def test_resolve_duplicate(api: tuple[AsyncClient, str], db_session: AsyncSession) -> None:
    client, uid = api
    user = await db_session.get(User, uid)
    assert user is not None
    # private(arxiv なし)を現行、arXiv 版を merge 先にする。
    private_paper = await factories.make_paper(
        db_session, owner=user, visibility="private", license="unknown"
    )
    item = await factories.make_library_item(
        db_session, user=user, paper=private_paper, status="planned"
    )
    arxiv_paper = await factories.make_paper(db_session, owner=user, arxiv_id="2301.00001")
    await db_session.commit()

    # dismiss。
    r = await client.post(
        f"/api/library-items/{item.id}/duplicate-resolution", json={"action": "dismiss"}
    )
    assert r.status_code == 200, r.text

    # merge(arXiv 側 survivor に付け替え)。
    r = await client.post(
        f"/api/library-items/{item.id}/duplicate-resolution",
        json={"action": "merge", "other_paper_id": str(arxiv_paper.id)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["library_item"]["paper"]["arxiv_id"] == "2301.00001"

    # 不正 action。
    r = await client.post(
        f"/api/library-items/{item.id}/duplicate-resolution", json={"action": "bogus"}
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# jobs: 取得 / 一覧(active フィルタ)/ アクセス制御
# ---------------------------------------------------------------------------
async def test_jobs_get_and_list(api: tuple[AsyncClient, str], db_session: AsyncSession) -> None:
    client, uid = api
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    running = await factories.make_job(
        db_session,
        kind="ingest",
        status="running",
        stage="translating_body",
        progress=68,
        user=user,
        paper=paper,
        library_item=item,
    )
    await factories.make_job(
        db_session,
        kind="translation",
        status="succeeded",
        progress=100,
        user=user,
        paper=paper,
        library_item=item,
    )
    await db_session.commit()

    r = await client.get(f"/api/jobs/{running.id}")
    assert r.status_code == 200, r.text
    assert r.json()["progress_pct"] == 68
    assert r.json()["stage"] == "translating_body"

    r = await client.get(f"/api/library-items/{item.id}/jobs")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2

    r = await client.get(f"/api/library-items/{item.id}/jobs", params={"active": True})
    assert r.status_code == 200
    assert {j["status"] for j in r.json()["items"]} == {"running"}

    # 他ユーザーのジョブ(user_id 一致せず)は 404。
    orphan = await factories.make_job(db_session, kind="ingest", status="queued")
    await db_session.commit()
    r = await client.get(f"/api/jobs/{orphan.id}")
    assert r.status_code == 200  # user_id=None は誰でも取得可(共有 ingest)
    other_user = await factories.make_user(db_session)
    other_job = await factories.make_job(db_session, kind="ingest", user=other_user)
    await db_session.commit()
    r = await client.get(f"/api/jobs/{other_job.id}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# papers: 取り込みログ(processing log の投影)
# ---------------------------------------------------------------------------
async def test_papers_ingest_log(api: tuple[AsyncClient, str], db_session: AsyncSession) -> None:
    client, uid = api
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user)
    await factories.make_library_item(db_session, user=user, paper=paper)
    await factories.make_job(
        db_session,
        kind="ingest",
        status="succeeded",
        stage="complete",
        user=user,
        paper=paper,
        log=[
            {"at": "2026-07-06T00:00:00Z", "stage": "fetching", "level": "info", "message": "取得"},
            {"at": "2026-07-06T00:01:00Z", "stage": "complete", "level": "info", "message": "完了"},
        ],
    )
    await db_session.commit()

    r = await client.get(f"/api/papers/{paper.id}/ingest-log")
    assert r.status_code == 200, r.text
    entries = r.json()["entries"]
    assert len(entries) >= 1
