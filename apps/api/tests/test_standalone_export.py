"""論文単位スタンドアロンエクスポート API テスト(Feature S3)。

- availability: 生成済み/未生成の成果物を最新リビジョン基準で正しく返す。
- 単一 HTML(source/translation/bilingual/article): サーバ非依存で開ける単一 HTML を返し、
  図は data URI で埋め込まれる。未生成成果物は 404。他人の項目は 404。

``test_export.py`` と同型(専用アプリに export+annotations ルータをマウント)。外部 S3 は
``StorageDep`` の dependency_overrides で決定的フェイクに差し替える(実通信なし)。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from alinea_api.services.session_service import create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.db.models import ArticleBlock, SourceAsset, User
from alinea_core.storage.s3 import StorageKeys
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


class _FakeStorage:
    """S3Storage の決定的フェイク(assets バケットの図バイトを返すだけ)。"""

    sources_bucket = "sources"
    assets_bucket = "assets"

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}

    async def get(self, bucket: str, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc


def _build_app() -> FastAPI:
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import annotations, export
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(export.router)
    app.include_router(annotations.router)
    return app


@pytest_asyncio.fixture
async def ctx(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str, FastAPI, _FakeStorage]]:
    from alinea_api.routers.papers import get_storage

    email = f"s3exp-{uuid.uuid4().hex}@example.com"
    user = await upsert_user_by_email(db_session, email, provider="email")
    uid = str(user.id)
    token = await create_session(redis_client, user.id)
    app = _build_app()
    storage = _FakeStorage()
    app.dependency_overrides[get_storage] = lambda: storage
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
        trust_env=False,
    ) as ac:
        ac.cookies.set("yk_session", token)
        try:
            yield ac, uid, app, storage
        finally:
            await db_session.rollback()
            await purge_user(db_session, uid)


_AVAIL = "/api/library-items/{}/export/standalone/availability"
_SRC = "/api/library-items/{}/export/standalone/source.html"
_TR = "/api/library-items/{}/export/standalone/translation.html"
_BI = "/api/library-items/{}/export/standalone/bilingual.html"
_ART = "/api/library-items/{}/export/standalone/article.html"


# ---------------------------------------------------------------------------
# availability
# ---------------------------------------------------------------------------
async def test_availability_source_only(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    await db_session.commit()

    res = await client.get(_AVAIL.format(item.id))
    assert res.status_code == 200
    body = res.json()
    assert body["source_html"] is True
    assert body["translation_html"] is False
    assert body["bilingual_html"] is False
    assert body["article_html"] is False
    assert body["pdf_original"] is False
    assert body["pdf_translated"] is False


async def test_availability_translation_complete(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    tset = await factories.make_translation_set(
        db_session, revision=rev, style="natural", scope="shared", status="complete"
    )
    await factories.make_translation_unit(
        db_session, translation_set=tset, block_id="blk-p1", text_ja="整流フロー"
    )
    await db_session.commit()

    body = (await client.get(_AVAIL.format(item.id))).json()
    assert body["translation_html"] is True
    assert body["bilingual_html"] is True


async def test_availability_article_and_pdf(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    await factories.make_article(db_session, library_item=item)
    db_session.add(
        SourceAsset(
            paper_id=str(paper.id),
            kind="pdf",
            source_url="https://arxiv.org/pdf/x",
            source_version=rev.source_version,
            storage_key=StorageKeys.original_pdf(str(paper.id), rev.source_version),
            content_type="application/pdf",
            byte_size=10,
            sha256="a" * 64,
        )
    )
    await db_session.commit()

    body = (await client.get(_AVAIL.format(item.id))).json()
    assert body["article_html"] is True
    assert body["pdf_original"] is True


async def test_availability_other_user_is_404(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    client, _uid, _app, _storage = ctx
    other = await factories.make_user(db_session)
    paper = await factories.make_paper(db_session, owner=other, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=other, paper=paper)
    await db_session.commit()

    assert (await client.get(_AVAIL.format(item.id))).status_code == 404


async def test_availability_translation_html_requires_source_ready(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    """translation_html / bilingual_html は source_ready も必要(空コンテンツでは False)。

    回帰テスト: DocumentRevision.content が空ブロックの場合に translation_complete でも
    translation_html/bilingual_html が False になることを確認する。
    """
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    # 空ブロックリスト → source_ready = False
    rev = await factories.make_revision(
        db_session,
        paper=paper,
        content={"quality_level": "A", "sections": []},
    )
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    tset = await factories.make_translation_set(
        db_session, revision=rev, style="natural", scope="shared", status="complete"
    )
    await factories.make_translation_unit(
        db_session, translation_set=tset, block_id="blk-p1", text_ja="テスト"
    )
    await db_session.commit()

    body = (await client.get(_AVAIL.format(item.id))).json()
    assert body["source_html"] is False
    assert body["translation_html"] is False
    assert body["bilingual_html"] is False


# ---------------------------------------------------------------------------
# source.html
# ---------------------------------------------------------------------------
async def test_source_html_is_self_contained_with_image(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    client, uid, _app, storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)  # 既定 content は fig-1.png を持つ
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    await db_session.commit()
    storage.objects["fig-1.png"] = b"\x89PNG\r\n\x1a\nFAKE"

    res = await client.get(_SRC.format(item.id))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    text = res.text
    assert text.lstrip().lower().startswith("<!doctype html>")
    assert "Rectified flow learns a straight transport map" in text
    assert "data:image/png;base64," in text  # 図が data URI で埋め込まれている


async def test_translation_html_prefers_translation_and_falls_back(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    tset = await factories.make_translation_set(
        db_session, revision=rev, style="natural", scope="shared", status="complete"
    )
    await factories.make_translation_unit(
        db_session, translation_set=tset, block_id="blk-p1", text_ja="整流フローは直線的です。"
    )
    await db_session.commit()

    res = await client.get(_TR.format(item.id))
    assert res.status_code == 200
    assert "整流フローは直線的です。" in res.text
    # 未訳ブロック(blk-p2)は原文フォールバック。
    assert "We use an EMA teacher for distillation." in res.text


async def test_bilingual_html_has_both_languages(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    tset = await factories.make_translation_set(
        db_session, revision=rev, style="natural", scope="shared", status="complete"
    )
    await factories.make_translation_unit(
        db_session, translation_set=tset, block_id="blk-p1", text_ja="整流フローは直線的です。"
    )
    await db_session.commit()

    res = await client.get(_BI.format(item.id))
    assert res.status_code == 200
    assert "Rectified flow learns a straight transport map" in res.text
    assert "整流フローは直線的です。" in res.text


async def test_translation_html_404_when_no_translation(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    await db_session.commit()

    assert (await client.get(_TR.format(item.id))).status_code == 404


async def test_article_html(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    article = await factories.make_article(db_session, library_item=item, with_blocks=False)
    db_session.add_all(
        [
            ArticleBlock(
                article_id=str(article.id),
                position=0,
                type="heading",
                content={"level": 2, "text": "はじめに"},
                text_plain="はじめに",
                origin="ai",
            ),
            ArticleBlock(
                article_id=str(article.id),
                position=1,
                type="paragraph",
                content={"md": "整流フローの **概要** です。"},
                text_plain="整流フローの概要です。",
                origin="ai",
            ),
            ArticleBlock(
                article_id=str(article.id),
                position=99,
                type="attribution",
                content={"text": "元の論文とは別物です。"},
                text_plain="元の論文とは別物です。",
                origin="ai",
            ),
        ]
    )
    await db_session.commit()

    res = await client.get(_ART.format(item.id))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert res.text.lstrip().lower().startswith("<!doctype html>")
    assert "はじめに" in res.text  # 記事の見出しブロック
    assert "<b>概要</b>" in res.text  # Markdown サブセット
    assert "元の論文とは別物です。" in res.text


async def test_article_html_404_when_no_article(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    await db_session.commit()

    assert (await client.get(_ART.format(item.id))).status_code == 404
