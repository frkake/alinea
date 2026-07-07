"""articles API テスト(M2-04。plans/03 §19)。

- PY-ART-01: 初回生成の enqueue(既定 include_math・preset)・既存記事への 409・
  ✦指示つき再生成の enqueue(payload に instruction/article_id)。
- PY-ART-04: 出典ブロック(attribution)は削除・書き直し対象外 → rewrite で 403 forbidden。
  GET は常に末尾に出典ブロック(locked=true)を返す。

記事生成そのもの(LLM 呼び出し・検証・正規化)は worker タスクを直接呼ぶ
:mod:`apps/worker/tests/test_generate_article.py` で検証する(本ファイルは API 契約のみ)。

DB は実 PostgreSQL。他タスクの WIP ルータを巻き込まないよう、本タスク所有の
``articles.router`` のみをマウントした専用アプリで検証する(test_vocab.py と同方針)。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import factories
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from yakudoku_api.services.session_service import create_session
from yakudoku_api.services.user_service import purge_user, upsert_user_by_email
from yakudoku_core.db.models import Article, ArticleBlock, Job, User


def _build_app() -> FastAPI:
    """本タスク所有ルータ(articles)のみをマウントしたアプリ(test_vocab.py と同方針)。"""
    from yakudoku_api.errors import register_exception_handlers
    from yakudoku_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from yakudoku_api.ratelimit import RateLimitMiddleware
    from yakudoku_api.redis_client import get_redis
    from yakudoku_api.routers import articles
    from yakudoku_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(articles.router)
    return app


@pytest_asyncio.fixture
async def auth(db_session: AsyncSession, redis_client: Any) -> AsyncIterator[SimpleNamespace]:
    from yakudoku_api.routers.articles import get_articles_job_wakeup

    email = f"art-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)  # rollback 後に ORM 属性へ触れないよう先に確定させる
    token = await create_session(redis_client, user.id)

    app = _build_app()
    wakeups: list[str] = []

    async def _noop_wakeup(job_id: str) -> None:
        wakeups.append(job_id)

    app.dependency_overrides[get_articles_job_wakeup] = lambda: _noop_wakeup

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield SimpleNamespace(client=ac, user_id=uid, wakeups=wakeups)
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


@pytest_asyncio.fixture
async def article_ctx(
    auth: SimpleNamespace, db_session: AsyncSession
) -> AsyncIterator[SimpleNamespace]:
    """記事テスト用の私有論文 + リビジョン + 読書中エントリ。"""
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    paper = await factories.make_paper(
        db_session, owner=user, visibility="private", license="cc-by-4.0"
    )
    revision = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper, status="reading")
    await db_session.commit()
    yield SimpleNamespace(
        client=auth.client,
        user_id=auth.user_id,
        wakeups=auth.wakeups,
        db=db_session,
        user=user,
        paper=paper,
        revision=revision,
        item_id=str(item.id),
    )


async def _job_for(db: AsyncSession, job_id: str) -> Job:
    job = await db.get(Job, job_id)
    assert job is not None
    return job


async def _blocks_for(db: AsyncSession, article_id: str) -> list[ArticleBlock]:
    rows = (
        (
            await db.execute(
                select(ArticleBlock)
                .where(ArticleBlock.article_id == article_id)
                .order_by(ArticleBlock.position.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _seed_full_article(db: AsyncSession, ctx: SimpleNamespace) -> Article:
    """GET のワイヤ形検証用に、DB 保存形(フラット)で全種別のブロックを組む。"""
    article = Article(
        id=str(uuid.uuid4()),
        library_item_id=ctx.item_id,
        title="やさしい整流フロー入門",
        preset="beginner",
        version=1,
    )
    db.add(article)
    await db.flush()
    revision_id = str(ctx.revision.id)
    rows = [
        ArticleBlock(
            article_id=str(article.id),
            position=0,
            type="heading",
            content={"level": 2, "text": "背景"},
            text_plain="背景",
            origin="ai",
        ),
        ArticleBlock(
            article_id=str(article.id),
            position=1,
            type="paragraph",
            content={"md": "整流フローは直線輸送を学習する。"},
            text_plain="整流フローは直線輸送を学習する。",
            evidence_anchors=[
                {
                    "revision_id": revision_id,
                    "block_id": "blk-p1",
                    "start": None,
                    "end": None,
                    "quote": None,
                    "side": "source",
                }
            ],
            origin="ai",
        ),
        ArticleBlock(
            article_id=str(article.id),
            position=2,
            type="quote_source",
            content={
                "text_en": "Rectified flow learns a straight transport map "
                "between two distributions.",
                "block_id": "blk-p1",
                "revision_id": revision_id,
            },
            text_plain="Rectified flow learns a straight transport map between two distributions.",
            origin="ai",
        ),
        ArticleBlock(
            article_id=str(article.id),
            position=3,
            type="figure_embed",
            content={
                "variant": "figure",
                "caption_ja": "軌道の直線化。",
                "figure_block_id": "blk-fig1",
                "revision_id": revision_id,
                "asset_key": "fig-1.png",
                "credit": "出典: Liu, Liu, *Flow Straight and Fast* (arXiv:None)",
                "license_badge": "CC BY 4.0 — 転載可",
                "caption_separated": False,
            },
            text_plain="軌道の直線化。",
            origin="ai",
        ),
        ArticleBlock(
            article_id=str(article.id),
            position=4,
            type="discussion",
            content={
                "items": [
                    {"md": "reflow を重ねると誤差は蓄積しないか", "origin": "user_highlight"},
                    {"md": "ベースラインは妥当か", "origin": "ai"},
                ]
            },
            text_plain="",
            origin="ai",
        ),
        ArticleBlock(
            article_id=str(article.id),
            position=5,
            type="attribution",
            content={"text": '出典: Liu, Liu. "Flow Straight and Fast."'},
            text_plain="",
            origin="ai",
        ),
    ]
    db.add_all(rows)
    await db.commit()
    return article


# ===========================================================================
# PY-ART-01: 初回生成・既存記事への 409・✦指示つき再生成の enqueue
# ===========================================================================
async def test_generate_article_enqueues_job_with_preset_defaults(
    article_ctx: SimpleNamespace,
) -> None:
    resp = await article_ctx.client.post(
        f"/api/library-items/{article_ctx.item_id}/article",
        json={"preset": "implementer"},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    assert job_id in article_ctx.wakeups

    job = await _job_for(article_ctx.db, job_id)
    assert job.kind == "article"
    assert job.payload["op"] == "generate"
    assert job.payload["preset"] == "implementer"
    assert job.payload["include_math"] is True  # implementer 既定(plans/07 §4.1)


async def test_generate_article_conflicts_when_article_exists(
    article_ctx: SimpleNamespace,
) -> None:
    from yakudoku_core.db.models import LibraryItem

    item = await article_ctx.db.get(LibraryItem, article_ctx.item_id)
    assert item is not None
    await factories.make_article(article_ctx.db, library_item=item)
    await article_ctx.db.commit()

    resp = await article_ctx.client.post(
        f"/api/library-items/{article_ctx.item_id}/article", json={"preset": "beginner"}
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "conflict"


async def test_regenerate_enqueues_job_with_instruction_and_bumps_none_yet(
    article_ctx: SimpleNamespace,
) -> None:
    from yakudoku_core.db.models import LibraryItem

    item = await article_ctx.db.get(LibraryItem, article_ctx.item_id)
    assert item is not None
    article = await factories.make_article(article_ctx.db, library_item=item)
    await article_ctx.db.commit()

    resp = await article_ctx.client.post(
        f"/api/articles/{article.id}/regenerate",
        json={"instruction": "もっと簡単に", "preset": "reading_group"},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    job = await _job_for(article_ctx.db, job_id)
    assert job.payload["op"] == "regenerate"
    assert job.payload["article_id"] == str(article.id)
    assert job.payload["instruction"] == "もっと簡単に"
    assert job.payload["preset"] == "reading_group"


async def test_regenerate_other_users_article_is_404(article_ctx: SimpleNamespace) -> None:
    other_item = await factories.make_library_item(article_ctx.db, status="reading")
    other_article = await factories.make_article(article_ctx.db, library_item=other_item)
    await article_ctx.db.commit()

    resp = await article_ctx.client.post(f"/api/articles/{other_article.id}/regenerate", json={})
    assert resp.status_code == 404


# ===========================================================================
# GET /api/library-items/{id}/article
# ===========================================================================
async def test_get_article_404_before_generation(article_ctx: SimpleNamespace) -> None:
    resp = await article_ctx.client.get(f"/api/library-items/{article_ctx.item_id}/article")
    assert resp.status_code == 404


async def test_get_article_returns_wire_shape(article_ctx: SimpleNamespace) -> None:
    article = await _seed_full_article(article_ctx.db, article_ctx)

    resp = await article_ctx.client.get(f"/api/library-items/{article_ctx.item_id}/article")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(article.id)
    assert body["preset"] == "beginner"
    assert body["version"] == 1
    assert "自動構成" in body["disclaimer"]
    assert body["overview_figure"] is None  # M2-05 の担当。未生成の間は null

    blocks = body["blocks"]
    assert len(blocks) == 6
    assert all(b["id"].startswith("ablk_") for b in blocks)

    heading = blocks[0]
    assert heading["type"] == "heading"
    assert heading["content"]["heading"] == {"level": 2, "text": "背景"}
    assert heading["locked"] is False

    paragraph = blocks[1]
    assert paragraph["content"]["markdown"] == "整流フローは直線輸送を学習する。"
    assert len(paragraph["evidence"]) == 1
    assert paragraph["evidence"][0]["ref"] == 1
    assert paragraph["evidence"][0]["anchor"]["block_id"] == "blk-p1"
    assert paragraph["evidence"][0]["display"]  # block_search_index から導出された表記

    quote = blocks[2]
    assert quote["content"]["quote"]["text_en"] == (
        "Rectified flow learns a straight transport map between two distributions."
    )
    assert quote["content"]["quote"]["anchor"]["block_id"] == "blk-p1"

    figure = blocks[3]
    assert figure["content"]["figure"]["image_url"]
    assert "出典" in figure["content"]["figure"]["credit"]
    assert "CC BY 4.0" in figure["content"]["figure"]["license_badge"]

    discussion = blocks[4]
    assert discussion["content"]["discussion"]["items"] == [
        {"text": "reflow を重ねると誤差は蓄積しないか", "origin": "user_highlight"},
        {"text": "ベースラインは妥当か", "origin": "ai"},
    ]

    attribution = blocks[5]
    assert attribution["type"] == "attribution"
    assert attribution["locked"] is True
    assert attribution["content"]["attribution"]["text"]


async def test_get_article_ownership_is_enforced(
    article_ctx: SimpleNamespace, db_session: AsyncSession
) -> None:
    other_item = await factories.make_library_item(db_session, status="reading")
    await factories.make_article(db_session, library_item=other_item)
    await db_session.commit()

    resp = await article_ctx.client.get(f"/api/library-items/{other_item.id}/article")
    assert resp.status_code == 404


# ===========================================================================
# PY-ART-04: 出典ブロック(attribution)は削除・書き直し対象外
# ===========================================================================
async def test_rewrite_attribution_block_is_forbidden(article_ctx: SimpleNamespace) -> None:
    from yakudoku_core.db.models import LibraryItem

    item = await article_ctx.db.get(LibraryItem, article_ctx.item_id)
    assert item is not None
    article = await factories.make_article(article_ctx.db, library_item=item)
    await article_ctx.db.commit()

    blocks = await _blocks_for(article_ctx.db, str(article.id))
    attribution = next(b for b in blocks if b.type == "attribution")

    resp = await article_ctx.client.post(
        f"/api/articles/{article.id}/blocks/ablk_{attribution.id}/rewrite",
        json={},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden"
    assert not article_ctx.wakeups  # ジョブは enqueue されない


async def test_rewrite_normal_block_enqueues_job(article_ctx: SimpleNamespace) -> None:
    from yakudoku_core.db.models import LibraryItem

    item = await article_ctx.db.get(LibraryItem, article_ctx.item_id)
    assert item is not None
    article = await factories.make_article(article_ctx.db, library_item=item)
    await article_ctx.db.commit()

    blocks = await _blocks_for(article_ctx.db, str(article.id))
    paragraph = next(b for b in blocks if b.type == "paragraph")

    resp = await article_ctx.client.post(
        f"/api/articles/{article.id}/blocks/ablk_{paragraph.id}/rewrite",
        json={"instruction": "もっと短く"},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    job = await _job_for(article_ctx.db, job_id)
    assert job.payload["op"] == "block_rewrite"
    assert job.payload["block_pk"] == paragraph.id
    assert job.payload["instruction"] == "もっと短く"


async def test_rewrite_invalid_block_id_is_404(article_ctx: SimpleNamespace) -> None:
    from yakudoku_core.db.models import LibraryItem

    item = await article_ctx.db.get(LibraryItem, article_ctx.item_id)
    assert item is not None
    article = await factories.make_article(article_ctx.db, library_item=item)
    await article_ctx.db.commit()

    resp = await article_ctx.client.post(
        f"/api/articles/{article.id}/blocks/not-a-real-id/rewrite", json={}
    )
    assert resp.status_code == 404


# ===========================================================================
# 版一覧(§19.4。Redis キャッシュ由来)
# ===========================================================================
async def test_list_versions_reads_redis_cache_newest_first(
    article_ctx: SimpleNamespace, redis_client: Any
) -> None:
    from yakudoku_core.db.models import LibraryItem

    item = await article_ctx.db.get(LibraryItem, article_ctx.item_id)
    assert item is not None
    article = await factories.make_article(article_ctx.db, library_item=item, version=2)
    await article_ctx.db.commit()

    key = f"article:versions:{article.id}"
    await redis_client.rpush(
        key,
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-07-01T00:00:00+00:00",
                "preset": "beginner",
                "instruction": None,
            }
        ),
    )
    await redis_client.rpush(
        key,
        json.dumps(
            {
                "version": 2,
                "generated_at": "2026-07-02T00:00:00+00:00",
                "preset": "beginner",
                "instruction": "もっと簡単に",
            }
        ),
    )

    resp = await article_ctx.client.get(f"/api/articles/{article.id}/versions")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert [i["version"] for i in items] == [2, 1]  # 新しい順
    assert items[0]["instruction"] == "もっと簡単に"
