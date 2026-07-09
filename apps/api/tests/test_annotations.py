"""注釈 API テスト(M1-01 / plans/03 §8・docs/04 §9)。

- PY-ANN-01: CRUD の kind/color/body 形状制約(highlight は color 必須・comment は body で
  表現・bookmark は color/body 両 NULL)、counts 集計。
- PY-ANN-03(API 部): 一覧フィルタ(color / has_comment / placed=false / kind)と
  文書内出現順+未配置末尾。

DB は実 PostgreSQL。テストデータは私有 Paper(owner=テストユーザー)として作り、
teardown の purge_user でカスケード削除する。認証はセッション直発行 + cookie。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import Annotation, DocumentRevision, LibraryItem, Paper
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app() -> FastAPI:
    """本タスク所有ルータのみをマウントしたアプリ(共通基盤は create_app と同一)。"""
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import annotations
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(annotations.router)
    return app


def _content() -> dict[str, Any]:
    """sec-1(blk-p1, blk-p2, blk-eq1)+ sec-2(blk-p3, blk-fig1)の quality A 文書。"""
    return {
        "quality_level": "A",
        "sections": [
            {
                "id": "sec-1",
                "heading": {"number": "1", "title": "Introduction"},
                "blocks": [
                    {
                        "id": "blk-p1",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "Rectified flow learns a straight map."}],
                    },
                    {
                        "id": "blk-p2",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "We use an EMA teacher."}],
                    },
                    {
                        "id": "blk-eq1",
                        "type": "equation",
                        "number": "1",
                        "latex": r"\frac{d}{dt} z_t = v(z_t, t)",
                    },
                ],
            },
            {
                "id": "sec-2",
                "heading": {"number": "2", "title": "Method"},
                "blocks": [
                    {
                        "id": "blk-p3",
                        "type": "paragraph",
                        "inlines": [{"t": "text", "v": "The reflow procedure straightens paths."}],
                    },
                    {
                        "id": "blk-fig1",
                        "type": "figure",
                        "number": "1",
                        "caption": [{"t": "text", "v": "Straightened trajectories."}],
                    },
                ],
            },
        ],
    }


@pytest_asyncio.fixture
async def env(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str, str, str]]:
    """(client, item_id, revision_id, uid) を返す。私有 Paper+Revision+LibraryItem を用意。"""
    email = f"ann-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)
    paper = Paper(
        arxiv_id=None,
        title="Flow Straight and Fast",
        authors=[{"name": "Xingchang Liu"}, {"name": "Qiang Liu"}],
        abstract="abstract",
        license="cc-by-4.0",
        visibility="private",
        owner_user_id=uid,
        published_on=dt.date(2022, 9, 7),
    )
    db_session.add(paper)
    await db_session.flush()
    rev = DocumentRevision(
        paper_id=paper.id,
        source_version="v1",
        parser_version="test",
        quality_level="A",
        source_format="arxiv_html",
        content=_content(),
        stats={},
    )
    db_session.add(rev)
    await db_session.flush()
    paper.latest_revision_id = rev.id
    item = LibraryItem(user_id=uid, paper_id=paper.id, status="reading", tags=[], suggested_tags=[])
    db_session.add(item)
    await db_session.flush()
    item_id, rev_id = str(item.id), str(rev.id)
    await db_session.commit()

    transport = ASGITransport(app=_build_app())
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        token = await create_session(redis_client, uid)
        ac.cookies.set("yk_session", token)
        try:
            yield ac, item_id, rev_id, uid
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


def _anchor(
    rev_id: str,
    block_id: str,
    *,
    start: int | None = None,
    end: int | None = None,
    quote: str | None = None,
    side: str = "source",
) -> dict[str, Any]:
    return {
        "revision_id": rev_id,
        "block_id": block_id,
        "start": start,
        "end": end,
        "quote": quote,
        "side": side,
    }


# ===========================================================================
# PY-ANN-01: CRUD の kind/color/body 形状制約
# ===========================================================================
async def test_create_highlight_requires_color(env: tuple[AsyncClient, str, str, str]) -> None:
    client, item_id, rev_id, _uid = env
    r = await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={"kind": "highlight", "anchor": _anchor(rev_id, "blk-p1", start=0, end=14)},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


async def test_create_highlight_ok(env: tuple[AsyncClient, str, str, str]) -> None:
    client, item_id, rev_id, _uid = env
    r = await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={
            "kind": "highlight",
            "color": "important",
            "anchor": _anchor(rev_id, "blk-p1", start=0, end=14, quote="Rectified flow"),
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "highlight"
    assert body["color"] == "important"
    assert body["comment"] is None
    assert body["placed"] is True
    # display は section_label + ¶(段落)。
    assert body["anchor"]["display"] == "§1 ¶1"
    assert body["anchor"]["quote"] == "Rectified flow"
    assert "id" in body and body["created_at"]


async def test_create_comment_maps_to_highlight_with_comment(
    env: tuple[AsyncClient, str, str, str],
) -> None:
    client, item_id, rev_id, _uid = env
    r = await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={
            "kind": "highlight",
            "color": "question",
            "comment": "ここ重要",
            "anchor": _anchor(rev_id, "blk-p2", start=0, end=2),
        },
    )
    assert r.status_code == 201
    body = r.json()
    # 公開 API の kind は highlight のまま。comment を持つ(§8.1)。
    assert body["kind"] == "highlight"
    assert body["color"] == "question"
    assert body["comment"] == "ここ重要"


async def test_create_bookmark_nulls_color_and_comment(
    env: tuple[AsyncClient, str, str, str],
) -> None:
    client, item_id, rev_id, _uid = env
    r = await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={
            "kind": "bookmark",
            "color": "important",  # bookmark では無視される
            "comment": "しおり",  # 同上
            "anchor": _anchor(rev_id, "sec-2"),
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "bookmark"
    assert body["color"] is None
    assert body["comment"] is None
    assert body["anchor"]["start"] is None
    assert body["anchor"]["end"] is None
    # bookmark はセクション参照表記。
    assert body["anchor"]["display"] == "§2"


async def test_create_rejects_nonexistent_block(env: tuple[AsyncClient, str, str, str]) -> None:
    client, item_id, rev_id, _uid = env
    r = await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={
            "kind": "highlight",
            "color": "idea",
            "anchor": _anchor(rev_id, "blk-does-not-exist", start=0, end=3),
        },
    )
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


async def test_patch_color_and_comment(env: tuple[AsyncClient, str, str, str]) -> None:
    client, item_id, rev_id, _uid = env
    created = (
        await client.post(
            f"/api/library-items/{item_id}/annotations",
            json={
                "kind": "highlight",
                "color": "important",
                "anchor": _anchor(rev_id, "blk-p1", start=0, end=14),
            },
        )
    ).json()
    ann_id = created["id"]

    # コメントを付与 → comment 付き highlight。
    r = await client.patch(
        f"/api/annotations/{ann_id}", json={"color": "idea", "comment": "メモ追加"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["color"] == "idea"
    assert body["comment"] == "メモ追加"
    assert body["kind"] == "highlight"

    # コメントを null にすると純ハイライトへ戻す(§8.3)。
    r2 = await client.patch(f"/api/annotations/{ann_id}", json={"comment": None})
    assert r2.status_code == 200
    assert r2.json()["comment"] is None
    assert r2.json()["color"] == "idea"


async def test_delete_returns_204(env: tuple[AsyncClient, str, str, str]) -> None:
    client, item_id, rev_id, _uid = env
    created = (
        await client.post(
            f"/api/library-items/{item_id}/annotations",
            json={
                "kind": "bookmark",
                "anchor": _anchor(rev_id, "sec-1"),
            },
        )
    ).json()
    r = await client.delete(f"/api/annotations/{created['id']}")
    assert r.status_code == 204
    # 削除後は一覧に出ない。
    listing = (await client.get(f"/api/library-items/{item_id}/annotations")).json()
    assert all(a["id"] != created["id"] for a in listing["items"])


async def test_counts_aggregation(env: tuple[AsyncClient, str, str, str]) -> None:
    client, item_id, rev_id, _uid = env

    async def _post(payload: dict[str, Any]) -> None:
        r = await client.post(f"/api/library-items/{item_id}/annotations", json=payload)
        assert r.status_code == 201

    await _post({"kind": "highlight", "color": "important", "anchor": _anchor(rev_id, "blk-p1")})
    await _post({"kind": "highlight", "color": "important", "anchor": _anchor(rev_id, "blk-p2")})
    await _post({"kind": "highlight", "color": "question", "anchor": _anchor(rev_id, "blk-p3")})
    await _post(
        {
            "kind": "highlight",
            "color": "idea",
            "comment": "コメント付き",
            "anchor": _anchor(rev_id, "blk-eq1"),
        }
    )
    await _post({"kind": "bookmark", "anchor": _anchor(rev_id, "sec-2")})

    counts = (await client.get(f"/api/library-items/{item_id}/annotations")).json()["counts"]
    assert counts["all"] == 5
    assert counts["important"] == 2
    assert counts["question"] == 1
    assert counts["idea"] == 1
    assert counts["term"] == 0
    assert counts["with_comment"] == 1
    assert counts["unplaced"] == 0


# ===========================================================================
# PY-ANN-03(API 部): 一覧フィルタ・出現順・未配置末尾
# ===========================================================================
async def test_filter_by_color(env: tuple[AsyncClient, str, str, str]) -> None:
    client, item_id, rev_id, _uid = env
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={"kind": "highlight", "color": "important", "anchor": _anchor(rev_id, "blk-p1")},
    )
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={"kind": "highlight", "color": "question", "anchor": _anchor(rev_id, "blk-p2")},
    )
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={"kind": "highlight", "color": "idea", "anchor": _anchor(rev_id, "blk-p3")},
    )

    r = await client.get(
        f"/api/library-items/{item_id}/annotations",
        params=[("color", "important"), ("color", "idea")],
    )
    assert r.status_code == 200
    got = {a["color"] for a in r.json()["items"]}
    assert got == {"important", "idea"}
    # counts はフィルタに影響されず全体総数。
    assert r.json()["counts"]["all"] == 3


async def test_filter_has_comment(env: tuple[AsyncClient, str, str, str]) -> None:
    client, item_id, rev_id, _uid = env
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={"kind": "highlight", "color": "important", "anchor": _anchor(rev_id, "blk-p1")},
    )
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={
            "kind": "highlight",
            "color": "question",
            "comment": "コメント",
            "anchor": _anchor(rev_id, "blk-p2"),
        },
    )

    only_comment = (
        await client.get(
            f"/api/library-items/{item_id}/annotations", params={"has_comment": "true"}
        )
    ).json()["items"]
    assert len(only_comment) == 1
    assert only_comment[0]["comment"] == "コメント"

    no_comment = (
        await client.get(
            f"/api/library-items/{item_id}/annotations", params={"has_comment": "false"}
        )
    ).json()["items"]
    assert len(no_comment) == 1
    assert no_comment[0]["comment"] is None


async def test_filter_kind_bookmark(env: tuple[AsyncClient, str, str, str]) -> None:
    client, item_id, rev_id, _uid = env
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={"kind": "highlight", "color": "important", "anchor": _anchor(rev_id, "blk-p1")},
    )
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={"kind": "bookmark", "anchor": _anchor(rev_id, "sec-1")},
    )

    bms = (
        await client.get(f"/api/library-items/{item_id}/annotations", params={"kind": "bookmark"})
    ).json()["items"]
    assert len(bms) == 1
    assert bms[0]["kind"] == "bookmark"

    # kind=highlight は comment 付き(DB comment)も含む。
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={
            "kind": "highlight",
            "color": "idea",
            "comment": "c",
            "anchor": _anchor(rev_id, "blk-p2"),
        },
    )
    hls = (
        await client.get(f"/api/library-items/{item_id}/annotations", params={"kind": "highlight"})
    ).json()["items"]
    assert len(hls) == 2
    assert all(a["kind"] == "highlight" for a in hls)


async def test_filter_placed_false_and_unplaced_at_end(
    env: tuple[AsyncClient, str, str, str], db_session: AsyncSession
) -> None:
    client, item_id, rev_id, _uid = env
    # 配置済み(document order: blk-p1 < blk-p3)。
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={"kind": "highlight", "color": "important", "anchor": _anchor(rev_id, "blk-p3")},
    )
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={"kind": "highlight", "color": "important", "anchor": _anchor(rev_id, "blk-p1")},
    )
    # 未配置(orphaned=true)を DB 直挿入(リアンカー失敗の再現。API では作れない)。
    # quote は GENERATED 列のため Core insert で設定列のみ渡す(anchor 側に畳み込む)。
    await db_session.execute(
        insert(Annotation).values(
            id=str(uuid.uuid4()),
            library_item_id=item_id,
            kind="highlight",
            color="term",
            body=None,
            anchor=_anchor(rev_id, "blk-removed-0000", start=0, end=5, quote="orphan"),
            orphaned=True,
        )
    )
    await db_session.commit()

    body = (await client.get(f"/api/library-items/{item_id}/annotations")).json()
    items = body["items"]
    assert [a["placed"] for a in items] == [True, True, False]
    # 出現順: blk-p1 (§1 ¶1) が blk-p3 (§2 ¶1) より先。
    assert items[0]["anchor"]["block_id"] == "blk-p1"
    assert items[1]["anchor"]["block_id"] == "blk-p3"
    assert items[2]["placed"] is False
    assert body["counts"]["unplaced"] == 1

    # placed=false は未配置のみ。
    unplaced = (
        await client.get(f"/api/library-items/{item_id}/annotations", params={"placed": "false"})
    ).json()["items"]
    assert len(unplaced) == 1
    assert unplaced[0]["placed"] is False


async def test_document_order_within_block_by_start(env: tuple[AsyncClient, str, str, str]) -> None:
    client, item_id, rev_id, _uid = env
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={
            "kind": "highlight",
            "color": "important",
            "anchor": _anchor(rev_id, "blk-p1", start=10, end=14),
        },
    )
    await client.post(
        f"/api/library-items/{item_id}/annotations",
        json={
            "kind": "highlight",
            "color": "question",
            "anchor": _anchor(rev_id, "blk-p1", start=0, end=9),
        },
    )
    items = (await client.get(f"/api/library-items/{item_id}/annotations")).json()["items"]
    starts = [a["anchor"]["start"] for a in items]
    assert starts == [0, 10]


# ===========================================================================
# 所有チェック・認証
# ===========================================================================
async def test_other_users_item_is_404(
    env: tuple[AsyncClient, str, str, str], db_session: AsyncSession, redis_client: Any
) -> None:
    client, item_id, rev_id, _uid = env
    # 別ユーザーの item は 404。
    other = await upsert_user_by_email(
        db_session, f"o-{uuid.uuid4().hex}@example.com", provider="email"
    )
    other_id = str(other.id)
    other_paper = Paper(
        title="X",
        authors=[{"name": "A"}],
        abstract="",
        license="cc-by-4.0",
        visibility="private",
        owner_user_id=other_id,
        published_on=dt.date(2023, 1, 1),
    )
    db_session.add(other_paper)
    await db_session.flush()
    other_item = LibraryItem(
        user_id=other_id, paper_id=other_paper.id, status="planned", tags=[], suggested_tags=[]
    )
    db_session.add(other_item)
    await db_session.flush()
    other_item_id = str(other_item.id)
    await db_session.commit()
    try:
        r = await client.get(f"/api/library-items/{other_item_id}/annotations")
        assert r.status_code == 404
        r2 = await client.post(
            f"/api/library-items/{other_item_id}/annotations",
            json={"kind": "bookmark", "anchor": _anchor(rev_id, "sec-1")},
        )
        assert r2.status_code == 404
    finally:
        await db_session.rollback()
        await purge_user(db_session, other_id)


async def test_unknown_annotation_is_404(env: tuple[AsyncClient, str, str, str]) -> None:
    client, _item_id, _rev_id, _uid = env
    missing = str(uuid.uuid4())
    assert (
        await client.patch(f"/api/annotations/{missing}", json={"color": "idea"})
    ).status_code == 404
    assert (await client.delete(f"/api/annotations/{missing}")).status_code == 404
    # 不正 UUID も 404。
    assert (await client.delete("/api/annotations/not-a-uuid")).status_code == 404
