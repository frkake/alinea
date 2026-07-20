"""記事公開 API テスト(Task 24。remaining-features-completion)。

サニタイズ済みスナップショットの公開(private/unlisted/public)を検証する:
- 可視性: unlisted = URL 保持者のみ + robots noindex、public = 検索インデックス許可、
  private(= 公開解除後の予約状態)は slug 読み取り不可(404)。
- 所有権: 作成/更新/公開解除は所有者のみ。slug 読み取りは認証不要。
- slug 重複: 明示 slug の衝突は 409。公開解除しても slug は予約され、乗っ取り不可。
- 再公開: 公開解除後に再公開すると同一 slug を再利用する。
- private 論文の記事は公開拒否。
- 情報漏えい防止(critical): スナップショットは heading/paragraph/attribution +
  ライセンス確認済み overview/explainer のみを含み、source quote 本文・訳文・メモ・
  チャット・discussion・figure_embed(原図)を一切含まない(sentinel 文字列で検証)。

DB は実 PostgreSQL。他タスクの WIP ルータを巻き込まないよう本タスク所有の
``publications.router`` のみをマウントした専用アプリで検証する(test_articles.py と同方針)。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import factories
import pytest
import pytest_asyncio
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import (
    Article,
    ArticleBlock,
    ArticlePublication,
    ChatMessage,
    ChatThread,
    ExplainerFigure,
    Note,
    OverviewFigure,
    TranslationSet,
    TranslationUnit,
    User,
)
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# --- sentinel 文字列(スナップショットに現れてはならない/現れるべき) -----------------
SOURCE_QUOTE_SENTINEL = "SOURCEQUOTELEAK_zzz"
TRANSLATION_SENTINEL = "TRANSLATIONLEAK_zzz"
NOTE_SENTINEL = "NOTELEAK_zzz"
CHAT_SENTINEL = "CHATLEAK_zzz"
DISCUSSION_SENTINEL = "DISCUSSIONLEAK_zzz"
FIGURE_EMBED_SENTINEL = "FIGUREEMBEDLEAK_zzz"

HEADING_SENTINEL = "HEADINGKEEP_zzz"
PARAGRAPH_SENTINEL = "PARAGRAPHKEEP_zzz"
ATTRIBUTION_SENTINEL = "ATTRIBUTIONKEEP_zzz"
EXPLAINER_SENTINEL = "EXPLAINERKEEP_zzz"
OVERVIEW_SENTINEL = "OVERVIEWKEEP_zzz"


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
    email = f"pub-{uuid.uuid4().hex}@example.com"
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


async def _seed_article(
    db: AsyncSession,
    user: User,
    *,
    paper_visibility: str = "public",
) -> SimpleNamespace:
    """公開可能な記事 + 全種別ブロック(漏えい検査用の sentinel 入り)を組む。"""
    paper = await factories.make_paper(
        db,
        owner=user,
        visibility=paper_visibility,
        license="cc-by-4.0",
        arxiv_id=f"2209.{uuid.uuid4().int % 100000:05d}",
        title="Flow Straight and Fast",
    )
    revision = await factories.make_revision(db, paper=paper)
    item = await factories.make_library_item(db, user=user, paper=paper, status="reading")

    article = Article(
        id=str(uuid.uuid4()),
        library_item_id=str(item.id),
        title="やさしい整流フロー入門",
        preset="beginner",
        version=3,
    )
    db.add(article)
    await db.flush()
    revision_id = str(revision.id)

    blocks = [
        ArticleBlock(
            article_id=str(article.id),
            position=0,
            type="heading",
            content={"level": 2, "text": f"導入 {HEADING_SENTINEL}"},
            text_plain="導入",
            origin="ai",
        ),
        ArticleBlock(
            article_id=str(article.id),
            position=1,
            type="paragraph",
            content={"md": f"これはAIによる解説文 {PARAGRAPH_SENTINEL}"},
            text_plain="これはAIによる解説文",
            evidence_anchors=[
                {
                    "revision_id": revision_id,
                    "block_id": "blk-p1",
                    "start": None,
                    "end": None,
                    "quote": SOURCE_QUOTE_SENTINEL,
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
                "text_en": f"Verbatim source sentence {SOURCE_QUOTE_SENTINEL}.",
                "block_id": "blk-p1",
                "revision_id": revision_id,
            },
            text_plain=f"Verbatim source sentence {SOURCE_QUOTE_SENTINEL}.",
            origin="ai",
        ),
        ArticleBlock(
            article_id=str(article.id),
            position=3,
            type="figure_embed",
            content={
                "variant": "figure",
                "caption_ja": f"原図キャプション {FIGURE_EMBED_SENTINEL}",
                "figure_block_id": "blk-fig1",
                "revision_id": revision_id,
                "asset_key": "fig-1.png",
                "credit": "出典: Liu, Liu",
                "license_badge": "CC BY 4.0",
            },
            text_plain="原図キャプション",
            origin="ai",
        ),
        ArticleBlock(
            article_id=str(article.id),
            position=4,
            type="explainer_figure",
            content={"slot": 0, "caption_ja": "fallback"},
            text_plain="",
            origin="ai",
        ),
        ArticleBlock(
            article_id=str(article.id),
            position=5,
            type="discussion",
            content={
                "items": [
                    {"md": f"議論メモ {DISCUSSION_SENTINEL}", "origin": "user_highlight"},
                ]
            },
            text_plain="",
            origin="ai",
        ),
        ArticleBlock(
            article_id=str(article.id),
            position=6,
            type="attribution",
            content={"text": f"出典: Liu, Liu. Flow Straight and Fast. {ATTRIBUTION_SENTINEL}"},
            text_plain="",
            origin="ai",
        ),
    ]
    db.add_all(blocks)

    # explainer figure(AI 生成・ライセンス確認済み)。slot 0 に対応。
    db.add(
        ExplainerFigure(
            id=str(uuid.uuid4()),
            article_id=str(article.id),
            slot=0,
            version=1,
            is_current=True,
            provider="openai",
            model="gpt-image",
            prompt="p",
            image_storage_key="explainers/x.png",
            caption=f"図の説明 {EXPLAINER_SENTINEL}",
        )
    )
    # overview figure(AI 生成 DSL 図)。
    db.add(
        OverviewFigure(
            id=str(uuid.uuid4()),
            article_id=str(article.id),
            version=1,
            is_current=True,
            render_mode="svg",
            dsl={"cards": [{"heading": f"全体像 {OVERVIEW_SENTINEL}", "body": "直線輸送"}]},
        )
    )

    # 漏えい元となる読書資産(スナップショットに現れてはならない)。
    db.add(Note(id=str(uuid.uuid4()), library_item_id=str(item.id), body_md=NOTE_SENTINEL))
    thread = ChatThread(id=str(uuid.uuid4()), library_item_id=str(item.id), is_main=True)
    db.add(thread)
    await db.flush()
    db.add(
        ChatMessage(
            thread_id=str(thread.id),
            role="assistant",
            content={"segments": [{"type": "text", "text": CHAT_SENTINEL}]},
            text_plain=CHAT_SENTINEL,
        )
    )
    tset = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision_id,
        style="natural",
        scope="shared",
        user_id=None,
        status="complete",
    )
    db.add(tset)
    await db.flush()
    db.add(
        TranslationUnit(
            set_id=str(tset.id),
            block_id="blk-p1",
            source_hash="h1",
            content_ja=[{"t": "text", "v": TRANSLATION_SENTINEL}],
            text_ja=TRANSLATION_SENTINEL,
            state="machine",
        )
    )
    await db.commit()
    return SimpleNamespace(article_id=str(article.id), paper=paper, item_id=str(item.id))


# ===========================================================================
# 公開(create)と slug 読み取り
# ===========================================================================
async def test_publish_unlisted_readable_by_slug_and_noindex(auth: SimpleNamespace,
                                                              db_session: AsyncSession) -> None:
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    seed = await _seed_article(db_session, user)

    resp = await auth.client.post(
        f"/api/articles/{seed.article_id}/publication", json={"visibility": "unlisted"}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    slug = body["slug"]
    assert body["visibility"] == "unlisted"
    assert body["snapshot_version"] == 3

    # slug 読み取りは認証不要(クッキー無しクライアント)。
    async with AsyncClient(
        transport=ASGITransport(app=auth.app),
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as anon:
        pub = await anon.get(f"/api/p/{slug}")
    assert pub.status_code == 200, pub.text
    data = pub.json()
    assert data["title"] == "やさしい整流フロー入門"
    assert data["noindex"] is True  # unlisted は検索非索引
    assert pub.headers.get("X-Robots-Tag") == "noindex"


async def test_publish_public_is_search_indexable(auth: SimpleNamespace,
                                                   db_session: AsyncSession) -> None:
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    seed = await _seed_article(db_session, user)

    resp = await auth.client.post(
        f"/api/articles/{seed.article_id}/publication", json={"visibility": "public"}
    )
    assert resp.status_code == 201, resp.text
    slug = resp.json()["slug"]

    pub = await auth.client.get(f"/api/p/{slug}")
    assert pub.status_code == 200
    assert pub.json()["noindex"] is False
    assert "noindex" not in pub.headers.get("X-Robots-Tag", "")


async def test_publish_rejects_private_paper(auth: SimpleNamespace,
                                             db_session: AsyncSession) -> None:
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    seed = await _seed_article(db_session, user, paper_visibility="private")

    resp = await auth.client.post(
        f"/api/articles/{seed.article_id}/publication", json={"visibility": "public"}
    )
    assert resp.status_code in (403, 409), resp.text
    # 公開行は作られない。
    row = (
        await db_session.execute(
            select(ArticlePublication).where(ArticlePublication.article_id == seed.article_id)
        )
    ).scalar_one_or_none()
    assert row is None


# ===========================================================================
# slug 重複・予約
# ===========================================================================
async def test_duplicate_slug_conflicts(auth: SimpleNamespace, db_session: AsyncSession) -> None:
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    a = await _seed_article(db_session, user)
    b = await _seed_article(db_session, user)

    r1 = await auth.client.post(
        f"/api/articles/{a.article_id}/publication",
        json={"visibility": "unlisted", "slug": "my-shared-slug"},
    )
    assert r1.status_code == 201, r1.text

    r2 = await auth.client.post(
        f"/api/articles/{b.article_id}/publication",
        json={"visibility": "unlisted", "slug": "my-shared-slug"},
    )
    assert r2.status_code == 409, r2.text


async def test_unpublish_reserves_slug_and_blocks_read(auth: SimpleNamespace,
                                                        db_session: AsyncSession) -> None:
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    a = await _seed_article(db_session, user)
    b = await _seed_article(db_session, user)

    r1 = await auth.client.post(
        f"/api/articles/{a.article_id}/publication",
        json={"visibility": "public", "slug": "reserved-slug"},
    )
    assert r1.status_code == 201, r1.text

    # 公開解除。
    dele = await auth.client.delete(f"/api/articles/{a.article_id}/publication")
    assert dele.status_code == 204, dele.text

    # slug 読み取りは 404(予約されているが読めない)。
    read = await auth.client.get("/api/p/reserved-slug")
    assert read.status_code == 404

    # 別記事は予約済み slug を乗っ取れない。
    r2 = await auth.client.post(
        f"/api/articles/{b.article_id}/publication",
        json={"visibility": "unlisted", "slug": "reserved-slug"},
    )
    assert r2.status_code == 409, r2.text


async def test_republish_reuses_slug(auth: SimpleNamespace, db_session: AsyncSession) -> None:
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    a = await _seed_article(db_session, user)

    r1 = await auth.client.post(
        f"/api/articles/{a.article_id}/publication",
        json={"visibility": "public", "slug": "stable-slug"},
    )
    assert r1.status_code == 201
    await auth.client.delete(f"/api/articles/{a.article_id}/publication")

    r2 = await auth.client.post(
        f"/api/articles/{a.article_id}/publication", json={"visibility": "unlisted"}
    )
    assert r2.status_code in (200, 201), r2.text
    assert r2.json()["slug"] == "stable-slug"  # 予約済み slug を再利用
    read = await auth.client.get("/api/p/stable-slug")
    assert read.status_code == 200


# ===========================================================================
# 所有権
# ===========================================================================
async def test_ownership_enforced(auth: SimpleNamespace, db_session: AsyncSession,
                                  redis_client: Any) -> None:
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    a = await _seed_article(db_session, user)

    # 別ユーザーのクライアント。
    other = await upsert_user_by_email(db_session, f"other-{uuid.uuid4().hex}@example.com",
                                       provider="email")
    other_uid = str(other.id)
    token = await create_session(redis_client, other.id)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=auth.app),
            base_url="http://testserver",
            headers={"Origin": "http://localhost:3000"},
            trust_env=False,
        ) as oc:
            oc.cookies.set("yk_session", token)
            create = await oc.post(
                f"/api/articles/{a.article_id}/publication", json={"visibility": "public"}
            )
            assert create.status_code == 404, create.text
            dele = await oc.delete(f"/api/articles/{a.article_id}/publication")
            assert dele.status_code == 404
    finally:
        await db_session.rollback()
        await purge_user(db_session, other_uid)


async def test_update_changes_visibility(auth: SimpleNamespace, db_session: AsyncSession) -> None:
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    a = await _seed_article(db_session, user)

    await auth.client.post(
        f"/api/articles/{a.article_id}/publication", json={"visibility": "unlisted"}
    )
    upd = await auth.client.patch(
        f"/api/articles/{a.article_id}/publication", json={"visibility": "public"}
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["visibility"] == "public"
    slug = upd.json()["slug"]
    read = await auth.client.get(f"/api/p/{slug}")
    assert read.json()["noindex"] is False


# ===========================================================================
# 情報漏えい防止(critical)
# ===========================================================================
async def test_snapshot_excludes_all_private_content(auth: SimpleNamespace,
                                                     db_session: AsyncSession) -> None:
    user = await db_session.get(User, auth.user_id)
    assert user is not None
    seed = await _seed_article(db_session, user)

    resp = await auth.client.post(
        f"/api/articles/{seed.article_id}/publication", json={"visibility": "public"}
    )
    assert resp.status_code == 201, resp.text
    slug = resp.json()["slug"]

    pub = await auth.client.get(f"/api/p/{slug}")
    assert pub.status_code == 200
    raw = json.dumps(pub.json(), ensure_ascii=False)

    # 漏えいしてはならない。
    for leak in (
        SOURCE_QUOTE_SENTINEL,
        TRANSLATION_SENTINEL,
        NOTE_SENTINEL,
        CHAT_SENTINEL,
        DISCUSSION_SENTINEL,
        FIGURE_EMBED_SENTINEL,
    ):
        assert leak not in raw, f"LEAK: {leak} present in published snapshot"

    # 許可された内容は含まれる。
    for keep in (HEADING_SENTINEL, PARAGRAPH_SENTINEL, ATTRIBUTION_SENTINEL, EXPLAINER_SENTINEL):
        assert keep in raw, f"MISSING allowed content: {keep}"

    # ブロック種別は許可リストのみ。
    data = pub.json()
    allowed_types = {"heading", "paragraph", "attribution", "explainer_figure", "overview_figure"}
    for block in data["blocks"]:
        assert block["type"] in allowed_types, f"disallowed block type: {block['type']}"

    # evidence は paper title + section label のみ(quote 本文なし)。
    para = next(b for b in data["blocks"] if b["type"] == "paragraph")
    for ev in para.get("evidence", []):
        assert "quote" not in ev
        assert ev.get("paper_title") == "Flow Straight and Fast"
        assert isinstance(ev.get("section"), str)

    # paper_meta は書誌のみ。
    assert data["paper_meta"]["title"] == "Flow Straight and Fast"


@pytest.mark.parametrize("bad_type", ["quote_source", "figure_embed", "discussion"])
def test_sanitizer_drops_disallowed_block_types(bad_type: str) -> None:
    """ユニット: サニタイザは許可外ブロックを落とす。"""
    from alinea_core.article.publication import sanitize_article_blocks

    blocks = [
        {"type": "heading", "content": {"level": 2, "text": "H"}, "evidence_anchors": []},
        {"type": bad_type, "content": {"text_en": "leak", "items": [{"md": "leak"}]},
         "evidence_anchors": []},
    ]
    out = sanitize_article_blocks(blocks, resolver=None, explainer_lookup={},
                                  paper_title="T")
    types = [b["type"] for b in out]
    assert "heading" in types
    assert bad_type not in types
