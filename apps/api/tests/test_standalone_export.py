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
from alinea_core.db.models import ArticleBlock, Job, SourceAsset, User
from alinea_core.storage.s3 import StorageKeys
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
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


# ---------------------------------------------------------------------------
# 非同期エクスポート API(Task 12: POST .../export/standalone → paper_export job)
# ---------------------------------------------------------------------------
# 単一 HTML(source/translation/bilingual/article)だけの選択は同期 HTML エンドポイントへ
# 誘導し、複数/PDF を含む選択は paper_export job を enqueue する。所有権・artifact 値域・
# availability を enqueue 前にサーバ側で再検証する(worker ハンドラの契約を写す)。
_START = "/api/library-items/{}/export/standalone"
_STATUS = "/api/library-items/{}/export/standalone/{}"


async def _seed_full_item(
    db_session: AsyncSession, factories: Any, user: User
) -> Any:
    """原文/訳文/記事/原文 PDF/訳文 PDF がすべて available な library item を作る。"""
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    rev = await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    tset = await factories.make_translation_set(
        db_session, revision=rev, style="natural", scope="shared", status="complete"
    )
    await factories.make_translation_unit(
        db_session, translation_set=tset, block_id="blk-p1", text_ja="整流フロー"
    )
    await factories.make_article(db_session, library_item=item)
    # 原文 PDF アセット。
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
    # 訳文 PDF アセット(有効 natural セット由来。scope=shared なので translation_set_id=None)。
    db_session.add(
        SourceAsset(
            paper_id=str(paper.id),
            kind="translated_pdf",
            source_url="https://arxiv.org/pdf/x-ja",
            source_version=rev.source_version,
            storage_key=StorageKeys.translated_pdf(
                str(paper.id), rev.source_version, "natural", translation_set_id=None
            ),
            content_type="application/pdf",
            byte_size=10,
            sha256="b" * 64,
        )
    )
    await db_session.commit()
    return item


async def test_start_multi_enqueues_paper_export_job(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    """複数成果物(HTML + PDF)の選択は paper_export job を enqueue し 202 を返す。"""
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    item = await _seed_full_item(db_session, factories, user)

    res = await client.post(
        _START.format(item.id),
        json={"artifacts": ["source_html", "pdf_original"]},
    )
    assert res.status_code == 202, res.text
    body = res.json()
    job_id = body["job_id"]
    assert body["mode"] == "job"
    assert body["download_url"] is None

    job = await db_session.get(Job, job_id)
    assert job is not None
    assert job.kind == "paper_export"
    assert str(job.user_id) == uid
    assert str(job.library_item_id) == str(item.id)
    assert set(job.payload["artifacts"]) == {"source_html", "pdf_original"}


async def test_start_single_html_uses_sync_endpoint(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    """単一 HTML の選択は job を作らず、同期 HTML エンドポイントへ誘導する。"""
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    item = await _seed_full_item(db_session, factories, user)

    res = await client.post(
        _START.format(item.id),
        json={"artifacts": ["source_html"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mode"] == "sync"
    assert body["job_id"] is None
    # 同期 HTML エンドポイントへの相対 URL を返す。
    assert body["download_url"] == _SRC.format(item.id)

    # job は作られていない。
    jobs = (
        (
            await db_session.execute(
                select(Job).where(
                    Job.user_id == uid, Job.kind == "paper_export"
                )
            )
        )
        .scalars()
        .all()
    )
    assert jobs == []


async def test_start_rejects_foreign_item(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    """他人の library item は 404(所有権をサーバ側で再検証)。"""
    client, _uid, _app, _storage = ctx
    other = await factories.make_user(db_session)
    item = await _seed_full_item(db_session, factories, other)

    try:
        res = await client.post(
            _START.format(item.id),
            json={"artifacts": ["source_html", "pdf_original"]},
        )
        assert res.status_code == 404, res.text
    finally:
        await purge_user(db_session, str(other.id))
        await db_session.commit()


async def test_start_rejects_unknown_artifact_value(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    """artifact 値域外(Literal 外)は 422(スキーマ検証)。"""
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    item = await _seed_full_item(db_session, factories, user)

    res = await client.post(
        _START.format(item.id),
        json={"artifacts": ["source_html", "nonsense_pdf"]},
    )
    assert res.status_code == 422, res.text


async def test_start_rejects_empty_artifacts(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    """空選択は 400(生成すべき成果物がない)。"""
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    item = await _seed_full_item(db_session, factories, user)

    res = await client.post(_START.format(item.id), json={"artifacts": []})
    assert res.status_code == 400, res.text


async def test_start_rejects_unavailable_artifact_before_enqueue(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    """availability=false の artifact を含む選択は enqueue 前に 409 で弾く。"""
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    # 原文のみ(訳文・PDF は未生成)。
    paper = await factories.make_paper(db_session, owner=user, visibility="private")
    await factories.make_revision(db_session, paper=paper)
    item = await factories.make_library_item(db_session, user=user, paper=paper)
    await db_session.commit()

    res = await client.post(
        _START.format(item.id),
        json={"artifacts": ["source_html", "pdf_original"]},
    )
    assert res.status_code == 409, res.text

    # job は作られていない(生成を始める前に弾く)。
    jobs = (
        (
            await db_session.execute(
                select(Job).where(Job.user_id == uid, Job.kind == "paper_export")
            )
        )
        .scalars()
        .all()
    )
    assert jobs == []


async def test_start_is_idempotent_with_key(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    """同一 Idempotency-Key の再投入は同じ job を返す(重複生成しない)。"""
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    item = await _seed_full_item(db_session, factories, user)

    key = f"paper-export-{uuid.uuid4().hex}"
    first = await client.post(
        _START.format(item.id),
        json={"artifacts": ["source_html", "pdf_original"]},
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 202, first.text
    second = await client.post(
        _START.format(item.id),
        json={"artifacts": ["source_html", "pdf_original"]},
        headers={"Idempotency-Key": key},
    )
    assert second.status_code == 202, second.text
    assert first.json()["job_id"] == second.json()["job_id"]

    jobs = (
        (
            await db_session.execute(
                select(Job).where(Job.user_id == uid, Job.kind == "paper_export")
            )
        )
        .scalars()
        .all()
    )
    assert len(jobs) == 1


async def test_status_polls_job_and_returns_download_url(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    """status エンドポイントは job 状態を返し、完了後は download_url を露出する。"""
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    item = await _seed_full_item(db_session, factories, user)

    start = await client.post(
        _START.format(item.id),
        json={"artifacts": ["source_html", "pdf_original"]},
    )
    assert start.status_code == 202, start.text
    job_id = start.json()["job_id"]

    pending = await client.get(_STATUS.format(item.id, job_id))
    assert pending.status_code == 200, pending.text
    pending_body = pending.json()
    assert pending_body["download_url"] is None
    assert pending_body["job"]["id"] == job_id
    assert pending_body["job"]["status"] == "queued"

    # worker の完了を模す(zip 化・S3 は worker の責務)。
    job = await db_session.get(Job, job_id)
    assert job is not None
    job.status = "succeeded"
    job.result = {"download_url": "https://example.test/exports/paper.zip"}
    await db_session.commit()

    done = await client.get(_STATUS.format(item.id, job_id))
    assert done.status_code == 200, done.text
    done_body = done.json()
    assert done_body["download_url"] == "https://example.test/exports/paper.zip"
    assert done_body["job"]["status"] == "succeeded"


async def test_status_other_users_job_is_404(
    ctx: tuple[AsyncClient, str, FastAPI, _FakeStorage],
    db_session: AsyncSession,
    factories: Any,
) -> None:
    """他人の job の status は 404。"""
    client, uid, _app, _storage = ctx
    user = await db_session.get(User, uid)
    assert user is not None
    item = await _seed_full_item(db_session, factories, user)

    other = await factories.make_user(db_session)
    other_job = await factories.make_job(
        db_session, kind="paper_export", user=other, library_item=None
    )
    await db_session.commit()
    try:
        res = await client.get(_STATUS.format(item.id, other_job.id))
        assert res.status_code == 404, res.text
    finally:
        await purge_user(db_session, str(other.id))
        await db_session.commit()
