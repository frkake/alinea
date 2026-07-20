"""公開記事コメント + モデレーション API テスト(Task 25。remaining-features-completion)。

Task 24 で公開されたスナップショットに対し、モデレーション付きコメントを検証する:
- 閲覧: 匿名可(GET /api/p/{slug}/comments)。
- 投稿: 認証必須(未認証は 401)。10 件/分/ユーザーの Redis レート制限。
- 本文: plain text のみ(HTML タグは保存されない)。1〜4000 文字。
- block_id: 公開スナップショットに存在するものだけ許可。
- parent: 同じ publication の 1 階層だけ(返信への返信は拒否)。
- 編集/削除: 投稿者本人のみ。削除は soft delete でスレッド構造を保つ。
- hide/restore: 記事公開者(publisher)のみ。

DB は実 PostgreSQL、Redis は実 Redis(conftest がウィンドウを掃除する)。他タスクの WIP
ルータを巻き込まないよう本タスク所有の ``publications.router`` のみをマウントする。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import factories
import pytest_asyncio
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import Article, ArticleBlock, User
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _build_app() -> FastAPI:
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import publications
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(publications.router)
    return app


@pytest_asyncio.fixture
async def auth(db_session: AsyncSession, redis_client: Any) -> AsyncIterator[SimpleNamespace]:
    email = f"pc-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)
    token = await create_session(redis_client, user.id)

    app = _build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield SimpleNamespace(client=ac, user_id=uid, app=app)
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


async def _seed_article(db: AsyncSession, user: User) -> str:
    """公開可能な記事(heading + paragraph)を組み、article_id を返す。"""
    paper = await factories.make_paper(
        db,
        owner=user,
        visibility="public",
        license="cc-by-4.0",
        arxiv_id=f"2209.{uuid.uuid4().int % 100000:05d}",
        title="Flow Straight and Fast",
    )
    await factories.make_revision(db, paper=paper)
    item = await factories.make_library_item(db, user=user, paper=paper, status="reading")
    article = Article(
        id=str(uuid.uuid4()),
        library_item_id=str(item.id),
        title="やさしい整流フロー入門",
        preset="beginner",
        version=1,
    )
    db.add(article)
    await db.flush()
    db.add_all(
        [
            ArticleBlock(
                article_id=str(article.id),
                position=0,
                type="heading",
                content={"level": 2, "text": "導入"},
                text_plain="導入",
                origin="ai",
            ),
            ArticleBlock(
                article_id=str(article.id),
                position=1,
                type="paragraph",
                content={"md": "これはAIによる解説文"},
                text_plain="これはAIによる解説文",
                origin="ai",
            ),
        ]
    )
    await db.commit()
    return str(article.id)


async def _publish(auth: SimpleNamespace, article_id: str) -> str:
    resp = await auth.client.post(
        f"/api/articles/{article_id}/publication", json={"visibility": "public"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["slug"]


async def _snapshot_block_id(auth: SimpleNamespace, slug: str) -> str:
    """公開スナップショットに実在するブロックの block_id を返す(コメント可能な対象)。"""
    async with _anon_client(auth) as anon:
        pub = await anon.get(f"/api/p/{slug}")
    assert pub.status_code == 200, pub.text
    blocks = pub.json()["blocks"]
    assert blocks, "publication should have at least one block"
    return blocks[0]["block_id"]


async def _seed_and_publish(auth: SimpleNamespace, db: AsyncSession) -> tuple[str, str]:
    """記事を公開し (slug, コメント可能な block_id) を返す。"""
    user = await db.get(User, auth.user_id)
    assert user is not None
    article_id = await _seed_article(db, user)
    slug = await _publish(auth, article_id)
    block_id = await _snapshot_block_id(auth, slug)
    return slug, block_id


def _anon_client(auth: SimpleNamespace) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=auth.app),
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    )


# ===========================================================================
# 閲覧(匿名可) / 投稿(認証必須)
# ===========================================================================
async def test_anon_can_view_comments(auth: SimpleNamespace, db_session: AsyncSession) -> None:
    slug, _block_id = await _seed_and_publish(auth, db_session)
    async with _anon_client(auth) as anon:
        resp = await anon.get(f"/api/p/{slug}/comments")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


async def test_authed_can_post_and_appears(auth: SimpleNamespace, db_session: AsyncSession) -> None:
    slug, block_id = await _seed_and_publish(auth, db_session)
    resp = await auth.client.post(
        f"/api/p/{slug}/comments", json={"block_id": block_id, "body": "はじめまして"}
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["body"] == "はじめまして"
    assert created["status"] == "visible"
    assert created["block_id"] == block_id

    async with _anon_client(auth) as anon:
        listing = await anon.get(f"/api/p/{slug}/comments")
    assert listing.status_code == 200
    bodies = [c["body"] for c in listing.json()]
    assert "はじめまして" in bodies


async def test_anon_cannot_post(auth: SimpleNamespace, db_session: AsyncSession) -> None:
    slug, block_id = await _seed_and_publish(auth, db_session)
    async with _anon_client(auth) as anon:
        resp = await anon.post(
            f"/api/p/{slug}/comments", json={"block_id": block_id, "body": "spam"}
        )
    assert resp.status_code == 401, resp.text


# ===========================================================================
# バリデーション(block_id / 本文長 / HTML / parent)
# ===========================================================================
async def test_rejects_block_id_not_in_snapshot(
    auth: SimpleNamespace, db_session: AsyncSession
) -> None:
    slug, _block_id = await _seed_and_publish(auth, db_session)
    resp = await auth.client.post(
        f"/api/p/{slug}/comments",
        json={"block_id": "nonexistent-block-zzz", "body": "存在しないブロック"},
    )
    assert resp.status_code == 400, resp.text


async def test_rejects_empty_and_too_long_body(
    auth: SimpleNamespace, db_session: AsyncSession
) -> None:
    slug, block_id = await _seed_and_publish(auth, db_session)
    empty = await auth.client.post(
        f"/api/p/{slug}/comments", json={"block_id": block_id, "body": ""}
    )
    assert empty.status_code == 422, empty.text
    too_long = await auth.client.post(
        f"/api/p/{slug}/comments", json={"block_id": block_id, "body": "x" * 4001}
    )
    assert too_long.status_code == 422, too_long.text


async def test_strips_html(auth: SimpleNamespace, db_session: AsyncSession) -> None:
    slug, block_id = await _seed_and_publish(auth, db_session)
    resp = await auth.client.post(
        f"/api/p/{slug}/comments",
        json={
            "block_id": block_id,
            "body": "<script>alert(1)</script>こんにちは<b>太字</b>",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()["body"]
    assert "<script>" not in body
    assert "<b>" not in body
    assert "こんにちは" in body
    assert "太字" in body


async def test_reply_one_level_only(auth: SimpleNamespace, db_session: AsyncSession) -> None:
    slug, block_id = await _seed_and_publish(auth, db_session)
    root = await auth.client.post(
        f"/api/p/{slug}/comments", json={"block_id": block_id, "body": "親"}
    )
    root_id = root.json()["id"]
    reply = await auth.client.post(
        f"/api/p/{slug}/comments",
        json={"block_id": block_id, "body": "返信", "parent_id": root_id},
    )
    assert reply.status_code == 201, reply.text
    reply_id = reply.json()["id"]
    # 返信への返信(2 階層目)は拒否する。
    deep = await auth.client.post(
        f"/api/p/{slug}/comments",
        json={"block_id": block_id, "body": "深い返信", "parent_id": reply_id},
    )
    assert deep.status_code == 400, deep.text


async def test_reply_must_be_same_publication(
    auth: SimpleNamespace, db_session: AsyncSession
) -> None:
    slug_a, block_a = await _seed_and_publish(auth, db_session)
    slug_b, block_b = await _seed_and_publish(auth, db_session)
    root_b = await auth.client.post(
        f"/api/p/{slug_b}/comments", json={"block_id": block_b, "body": "B の親"}
    )
    root_b_id = root_b.json()["id"]
    cross = await auth.client.post(
        f"/api/p/{slug_a}/comments",
        json={"block_id": block_a, "body": "別 pub への返信", "parent_id": root_b_id},
    )
    assert cross.status_code == 400, cross.text


# ===========================================================================
# 編集 / 削除(投稿者本人のみ)
# ===========================================================================
async def test_author_can_edit_others_cannot(
    auth: SimpleNamespace, db_session: AsyncSession, redis_client: Any
) -> None:
    slug, block_id = await _seed_and_publish(auth, db_session)
    created = await auth.client.post(
        f"/api/p/{slug}/comments", json={"block_id": block_id, "body": "初稿"}
    )
    comment_id = created.json()["id"]

    edit = await auth.client.patch(
        f"/api/p/{slug}/comments/{comment_id}", json={"body": "修正しました"}
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["body"] == "修正しました"

    # 別ユーザーは編集できない(403)。
    other = await upsert_user_by_email(
        db_session, f"other-{uuid.uuid4().hex}@example.com", provider="email"
    )
    other_uid = str(other.id)
    token = await create_session(redis_client, other.id)
    try:
        async with _anon_client(auth) as oc:
            oc.cookies.set("yk_session", token)
            forbidden = await oc.patch(
                f"/api/p/{slug}/comments/{comment_id}", json={"body": "乗っ取り"}
            )
            assert forbidden.status_code == 403, forbidden.text
    finally:
        await db_session.rollback()
        await purge_user(db_session, other_uid)


async def test_soft_delete_preserves_thread(
    auth: SimpleNamespace, db_session: AsyncSession
) -> None:
    slug, block_id = await _seed_and_publish(auth, db_session)
    root = await auth.client.post(
        f"/api/p/{slug}/comments", json={"block_id": block_id, "body": "親コメント"}
    )
    root_id = root.json()["id"]
    reply = await auth.client.post(
        f"/api/p/{slug}/comments",
        json={"block_id": block_id, "body": "返信コメント", "parent_id": root_id},
    )
    reply_id = reply.json()["id"]

    dele = await auth.client.delete(f"/api/p/{slug}/comments/{root_id}")
    assert dele.status_code == 204, dele.text

    async with _anon_client(auth) as anon:
        listing = await anon.get(f"/api/p/{slug}/comments")
    rows = {c["id"]: c for c in listing.json()}
    # 返信があるためスレッド構造(親の行)は保持される。
    assert root_id in rows
    assert rows[root_id]["status"] == "deleted"
    assert rows[root_id]["body"] == ""  # 削除本文は返さない
    # 返信はそのまま見える。
    assert reply_id in rows
    assert rows[reply_id]["body"] == "返信コメント"


# ===========================================================================
# モデレーション(publisher の hide / restore)
# ===========================================================================
async def test_publisher_can_hide_and_restore(
    auth: SimpleNamespace, db_session: AsyncSession, redis_client: Any
) -> None:
    slug, block_id = await _seed_and_publish(auth, db_session)

    # 別ユーザーがコメントを投稿する。
    commenter = await upsert_user_by_email(
        db_session, f"commenter-{uuid.uuid4().hex}@example.com", provider="email"
    )
    commenter_uid = str(commenter.id)
    ctoken = await create_session(redis_client, commenter.id)
    try:
        async with _anon_client(auth) as cc:
            cc.cookies.set("yk_session", ctoken)
            posted = await cc.post(
                f"/api/p/{slug}/comments", json={"block_id": block_id, "body": "議論します"}
            )
            assert posted.status_code == 201, posted.text
            comment_id = posted.json()["id"]

            # コメント投稿者は hide できない(publisher ではない)。
            forbidden = await cc.post(f"/api/p/{slug}/comments/{comment_id}/hide")
            assert forbidden.status_code == 403, forbidden.text

        # 記事公開者(auth)は hide できる。
        hidden = await auth.client.post(f"/api/p/{slug}/comments/{comment_id}/hide")
        assert hidden.status_code == 200, hidden.text
        assert hidden.json()["status"] == "hidden"

        async with _anon_client(auth) as anon:
            listing = await anon.get(f"/api/p/{slug}/comments")
        row = next(c for c in listing.json() if c["id"] == comment_id)
        assert row["status"] == "hidden"
        assert row["body"] == ""  # 非表示本文は返さない

        # restore で再表示。
        restored = await auth.client.post(f"/api/p/{slug}/comments/{comment_id}/restore")
        assert restored.status_code == 200, restored.text
        assert restored.json()["status"] == "visible"

        async with _anon_client(auth) as anon2:
            listing2 = await anon2.get(f"/api/p/{slug}/comments")
        row2 = next(c for c in listing2.json() if c["id"] == comment_id)
        assert row2["status"] == "visible"
        assert row2["body"] == "議論します"
    finally:
        await db_session.rollback()
        await purge_user(db_session, commenter_uid)


# ===========================================================================
# レート制限(10 件/分/ユーザー)
# ===========================================================================
async def test_rate_limit_10_per_minute(auth: SimpleNamespace, db_session: AsyncSession) -> None:
    slug, block_id = await _seed_and_publish(auth, db_session)
    statuses = []
    for i in range(11):
        r = await auth.client.post(
            f"/api/p/{slug}/comments", json={"block_id": block_id, "body": f"コメント {i}"}
        )
        statuses.append(r.status_code)
    assert statuses[:10] == [201] * 10, statuses
    assert statuses[10] == 429, statuses
