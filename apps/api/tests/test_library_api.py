"""ライブラリ API テスト(M0-22 / plans/03 §5・plans/11 §8)。

- PY-LIB-01: 一覧フィルタ(同一属性内 OR・属性間 AND・quick と status の積集合)・quality/tag。
- PY-LIB-02: keyset cursor ページング(全件を重複なく走査・total 一致)。
- 単体 GET/PATCH/DELETE・facets・tags・提案タグ却下・duplicate-resolution のスモーク。

DB は実 PostgreSQL。テストデータは私有 Paper(owner=テストユーザー)として作り、
teardown の purge_user でカスケード削除する。認証はセッション直発行 + cookie。
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from alinea_api.services.session_service import create_extension_token, create_session
from alinea_api.services.user_service import purge_user, upsert_user_by_email
from alinea_core.article.storage_keys import article_versions_cache_key
from alinea_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    ChatMessage,
    ChatThread,
    CollectionEntry,
    DocumentRevision,
    ExplainerFigure,
    Glossary,
    GlossaryTerm,
    Job,
    LibraryItem,
    Note,
    Notification,
    OverviewFigure,
    Paper,
    ReadingSession,
    ResourceLink,
    SourceAsset,
    UsageRecord,
    User,
    VocabEntry,
)
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


class _RecordingStorage:
    sources_bucket = "sources"
    assets_bucket = "assets"

    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.deleted_prefixes: list[tuple[str, str]] = []

    async def delete_many(self, bucket: str, keys: Any) -> None:
        key_list = list(keys)
        if key_list and _storage_failure is not None:
            raise _storage_failure
        self.deleted.extend((bucket, key) for key in key_list)

    async def delete_prefixes(self, bucket: str, prefixes: Any) -> None:
        prefix_list = list(prefixes)
        if prefix_list and _storage_failure is not None:
            raise _storage_failure
        self.deleted_prefixes.extend((bucket, prefix) for prefix in prefix_list)


_last_storage: _RecordingStorage | None = None
_storage_failure: Exception | None = None


def _storage_override() -> _RecordingStorage:
    global _last_storage
    _last_storage = _RecordingStorage()
    return _last_storage


def _storage_deleted() -> set[tuple[str, str]]:
    assert _last_storage is not None
    return set(_last_storage.deleted)


def _storage_deleted_prefixes() -> set[tuple[str, str]]:
    assert _last_storage is not None
    return set(_last_storage.deleted_prefixes)


def _build_app() -> FastAPI:
    """本タスク所有ルータのみをマウントしたアプリ(main.create_app と同じ共通基盤を使用)。

    並行タスクの WIP ルータ(chat 等)に import を巻き込まれず、本タスクを独立に検証する。
    """
    from alinea_api.errors import register_exception_handlers
    from alinea_api.middleware import OriginCsrfMiddleware, RequestIdMiddleware
    from alinea_api.ratelimit import RateLimitMiddleware
    from alinea_api.redis_client import get_redis
    from alinea_api.routers import library_items, llm_settings
    from alinea_api.routers import settings as settings_router
    from alinea_api.settings import get_api_settings

    s = get_api_settings()
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(OriginCsrfMiddleware, settings=s)
    app.add_middleware(RateLimitMiddleware, redis_factory=get_redis)
    app.add_middleware(RequestIdMiddleware)
    app.dependency_overrides[library_items.get_storage] = _storage_override
    app.include_router(library_items.router)
    app.include_router(settings_router.router)
    app.include_router(llm_settings.router)
    return app


@pytest_asyncio.fixture
async def auth(
    db_session: AsyncSession, redis_client: Any
) -> AsyncIterator[tuple[AsyncClient, str]]:
    email = f"lib-{uuid.uuid4().hex}@example.com"
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


async def _mk_item(
    db: AsyncSession,
    user_id: str,
    *,
    status: str = "planned",
    tags: list[str] | None = None,
    suggested: list[str] | None = None,
    title: str = "Paper",
    priority: str | None = None,
    quality: str = "A",
    year: int = 2023,
    arxiv_id: str | None = None,
    deadline: dt.date | None = None,
    understanding: int | None = None,
) -> str:
    paper = Paper(
        arxiv_id=arxiv_id,
        title=title,
        authors=[{"name": "Xingchang Liu"}, {"name": "Qiang Liu"}],
        abstract="abstract",
        license="unknown",
        visibility="private",
        owner_user_id=user_id,
        published_on=dt.date(year, 1, 1),
    )
    db.add(paper)
    await db.flush()
    rev = DocumentRevision(
        paper_id=paper.id,
        source_version="v1",
        parser_version="test",
        quality_level=quality,
        source_format="arxiv_html",
        content={"quality_level": quality, "sections": []},
        stats={},
    )
    db.add(rev)
    await db.flush()
    paper.latest_revision_id = rev.id
    item = LibraryItem(
        user_id=user_id,
        paper_id=paper.id,
        status=status,
        tags=tags or [],
        suggested_tags=suggested or [],
        priority=priority,
        deadline=deadline,
        understanding=understanding,
    )
    db.add(item)
    await db.flush()
    return str(item.id)


async def _count(db: AsyncSession, model: Any, where: Any) -> int:
    value = await db.scalar(select(func.count()).select_from(model).where(where))
    return int(value or 0)


# ---------------------------------------------------------------------------
# PY-LIB-01: 一覧フィルタ
# ---------------------------------------------------------------------------
async def test_library_list_filters_by_status(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    await _mk_item(db_session, uid, status="up_next", tags=["cs.CV"], quality="A")
    await _mk_item(db_session, uid, status="up_next", tags=["cs.LG"], quality="B")
    await _mk_item(db_session, uid, status="reading", tags=["cs.CV"])
    await _mk_item(db_session, uid, status="done")
    await db_session.commit()

    r = await client.get("/api/library-items", params={"status": "up_next"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert all(i["status"] == "up_next" for i in body["items"])
    assert "next_cursor" in body


async def test_status_or_within_attribute(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    await _mk_item(db_session, uid, status="up_next")
    await _mk_item(db_session, uid, status="reading")
    await _mk_item(db_session, uid, status="done")
    await db_session.commit()

    r = await client.get(
        "/api/library-items", params=[("status", "up_next"), ("status", "reading")]
    )
    assert r.status_code == 200
    got = {i["status"] for i in r.json()["items"]}
    assert got == {"up_next", "reading"}


async def test_tag_and_status_are_anded(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    keep = await _mk_item(db_session, uid, status="up_next", tags=["cs.CV"])
    await _mk_item(db_session, uid, status="up_next", tags=["cs.LG"])  # wrong tag
    await _mk_item(db_session, uid, status="reading", tags=["cs.CV"])  # wrong status
    await db_session.commit()

    r = await client.get("/api/library-items", params={"status": "up_next", "tag": "cs.CV"})
    assert r.status_code == 200
    ids = [i["id"] for i in r.json()["items"]]
    assert ids == [keep]


async def test_quick_and_status_intersection(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    await _mk_item(db_session, uid, status="up_next")  # unread
    await _mk_item(db_session, uid, status="reading")  # in_progress
    await db_session.commit()

    # quick=unread(planned+up_next)と status=reading の積集合は空(§5.1)。
    r = await client.get("/api/library-items", params={"quick": "unread", "status": "reading"})
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["total"] == 0

    # quick=unread 単独なら up_next が拾える。
    r2 = await client.get("/api/library-items", params={"quick": "unread"})
    assert {i["status"] for i in r2.json()["items"]} == {"up_next"}


async def test_quality_filter_and_bib_fields(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    # arxiv_id は papers に UNIQUE 制約があるためシード等と衝突しない一意値を使う。
    axid = f"2099.{uuid.uuid4().hex[:8]}"
    await _mk_item(db_session, uid, quality="A", arxiv_id=axid, title="RF")
    await _mk_item(db_session, uid, quality="B", title="Other")
    await db_session.commit()

    r = await client.get("/api/library-items", params={"quality": "A"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    it = items[0]
    assert it["quality_level"] == "A"
    assert it["source"] == "arxiv"
    assert it["paper"]["arxiv_id"] == axid
    assert it["paper"]["authors_short"] == "Liu, Liu"
    assert it["paper"]["year"] == 2023


async def test_invalid_status_is_422(auth: tuple[AsyncClient, str]) -> None:
    client, _uid = auth
    r = await client.get("/api/library-items", params={"status": "bogus"})
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


# ---------------------------------------------------------------------------
# PY-LIB-02: cursor ページング
# ---------------------------------------------------------------------------
async def test_cursor_pagination_covers_all(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    created = set()
    for k in range(5):
        created.add(await _mk_item(db_session, uid, status="planned", title=f"P{k}"))
    await db_session.commit()

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        r = await client.get("/api/library-items", params=params)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 5
        seen.extend(i["id"] for i in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages <= 10  # 無限ループ保護

    assert len(seen) == 5
    assert set(seen) == created
    assert len(set(seen)) == 5  # 重複なし


async def test_bad_cursor_is_422(auth: tuple[AsyncClient, str]) -> None:
    client, _uid = auth
    r = await client.get("/api/library-items", params={"cursor": "!!!not-base64!!!"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# facets(§5.2)
# ---------------------------------------------------------------------------
async def test_facets_quick_counts_sum_to_all(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    await _mk_item(db_session, uid, status="planned", tags=["a"])
    await _mk_item(db_session, uid, status="up_next", tags=["a", "b"])
    await _mk_item(db_session, uid, status="reading")
    await _mk_item(db_session, uid, status="done")
    await _mk_item(db_session, uid, status="reread")
    await db_session.commit()

    r = await client.get("/api/library-items/facets")
    assert r.status_code == 200
    f = r.json()
    q = f["quick"]
    assert q["all"] == 5
    assert q["unread"] + q["in_progress"] + q["done"] + q["recheck"] == q["all"]
    assert q["unread"] == 2
    tag_map = {t["tag"]: t["count"] for t in f["tags"]}
    assert tag_map["a"] == 2
    assert tag_map["b"] == 1


# ---------------------------------------------------------------------------
# 単体 GET / PATCH / DELETE(§5.3-5.5)
# ---------------------------------------------------------------------------
async def test_get_single_and_not_found(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid, status="reading")
    await db_session.commit()

    r = await client.get(f"/api/library-items/{item_id}")
    assert r.status_code == 200
    assert r.json()["id"] == item_id

    missing = await client.get(f"/api/library-items/{uuid.uuid4()}")
    assert missing.status_code == 404
    bad = await client.get("/api/library-items/not-a-uuid")
    assert bad.status_code == 404


async def test_patch_status_done_sets_finished_at_once(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid, status="reading", suggested=["cs.CV", "cs.LG"])
    await db_session.commit()

    r = await client.patch(f"/api/library-items/{item_id}", json={"status": "done"})
    assert r.status_code == 200
    finished = r.json()["finished_at"]
    assert finished is not None

    # 再度ステータスを変えても finished_at は消えない・上書きされない(§5.4)。
    r2 = await client.patch(f"/api/library-items/{item_id}", json={"status": "reading"})
    assert r2.json()["finished_at"] == finished


async def test_patch_tags_consumes_matching_suggestions(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid, suggested=["cs.CV", "cs.LG"])
    await db_session.commit()

    # 提案 cs.CV を承認(tags に含める)。含めなかった cs.LG は残る。
    r = await client.patch(f"/api/library-items/{item_id}", json={"tags": ["cs.CV", "自作"]})
    assert r.status_code == 200
    body = r.json()
    assert set(body["tags"]) == {"cs.CV", "自作"}
    assert body["suggested_tags"] == ["cs.LG"]


async def test_patch_invalid_value_is_422(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid)
    await db_session.commit()
    r = await client.patch(f"/api/library-items/{item_id}", json={"comprehension": 9})
    assert r.status_code == 422
    r2 = await client.patch(f"/api/library-items/{item_id}", json={"deadline": "07/16"})
    assert r2.status_code == 422


async def test_delete_removes_item_and_private_paper(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid)
    await db_session.commit()

    r = await client.delete(f"/api/library-items/{item_id}")
    assert r.status_code == 204
    gone = await client.get(f"/api/library-items/{item_id}")
    assert gone.status_code == 404


async def test_delete_removes_unreferenced_public_paper_and_storage_objects(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid)
    item = await db_session.get(LibraryItem, item_id)
    assert item is not None
    paper = await db_session.get(Paper, item.paper_id)
    assert paper is not None
    revision = await db_session.get(DocumentRevision, paper.latest_revision_id)
    assert revision is not None

    paper.visibility = "public"
    paper.owner_user_id = None
    paper_id = str(paper.id)
    revision_id = str(revision.id)
    paper_thumbnail_key = f"thumbnails/{paper_id}/card.webp"
    retina_thumbnail_key = f"thumbnails/{paper_id}/card@2x.webp"
    item_thumbnail_key = f"thumbnails/{paper_id}/item.webp"
    figure_key = f"figures/{paper_id}/{revision_id}/fig-1.png"
    source_key = f"sources/{paper_id}/v1/original.pdf"
    overview_svg_key = f"renders/overview/{item_id}/v1.svg"
    overview_image_key = f"renders/overview/{item_id}/v1.png"
    explainer_image_key = f"renders/explainer/{item_id}/v1.png"

    paper.thumbnail_key = paper_thumbnail_key
    item.thumbnail_key = item_thumbnail_key
    revision.content = {
        "quality_level": "A",
        "sections": [
            {
                "id": "sec-1",
                "title": "Section",
                "blocks": [{"id": "fig-1", "type": "figure", "asset_key": figure_key}],
            }
        ],
    }
    db_session.add(
        SourceAsset(
            paper_id=paper_id,
            kind="pdf",
            source_version="v1",
            storage_key=source_key,
            content_type="application/pdf",
            byte_size=10,
        )
    )
    article = await factories.make_article(
        db_session, library_item=item, with_overview_figure=False
    )
    db_session.add(
        OverviewFigure(
            id=str(uuid.uuid4()),
            article_id=str(article.id),
            version=1,
            is_current=True,
            render_mode="svg",
            dsl={"cards": [], "connectors": [], "footer": {"summary": ""}},
            svg_storage_key=overview_svg_key,
            image_storage_key=overview_image_key,
        )
    )
    db_session.add(
        ExplainerFigure(
            id=str(uuid.uuid4()),
            article_id=str(article.id),
            slot=0,
            version=1,
            is_current=True,
            provider="google",
            model="gemini-3.1-flash-image",
            prompt="",
            image_storage_key=explainer_image_key,
        )
    )
    await db_session.commit()

    r = await client.delete(f"/api/library-items/{item_id}")

    assert r.status_code == 204
    assert await _count(db_session, Paper, Paper.id == paper_id) == 0
    assert await _count(db_session, SourceAsset, SourceAsset.paper_id == paper_id) == 0
    assert {
        ("sources", source_key),
        ("assets", paper_thumbnail_key),
        ("assets", retina_thumbnail_key),
        ("assets", item_thumbnail_key),
        ("assets", figure_key),
        ("assets", overview_svg_key),
        ("assets", overview_image_key),
        ("assets", explainer_image_key),
    } <= _storage_deleted()
    assert {
        ("sources", f"sources/{paper_id}/"),
        ("assets", f"figures/{paper_id}/"),
        ("assets", f"thumbnails/{paper_id}/"),
        ("assets", f"renders/articles/{article.id}/"),
        ("assets", f"renders/overview/{article.id}/"),
    } <= _storage_deleted_prefixes()


async def test_delete_removes_public_paper_and_all_referencing_items(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid)
    item = await db_session.get(LibraryItem, item_id)
    assert item is not None
    paper = await db_session.get(Paper, item.paper_id)
    assert paper is not None
    paper.visibility = "public"
    paper.owner_user_id = None
    other = await upsert_user_by_email(
        db_session, f"shared-{uuid.uuid4().hex}@example.com", provider="email"
    )
    other_item = LibraryItem(user_id=other.id, paper_id=paper.id, status="planned")
    db_session.add(other_item)
    await db_session.flush()
    other_item_id = str(other_item.id)
    paper_id = str(paper.id)
    await db_session.commit()

    r = await client.delete(f"/api/library-items/{item_id}")

    assert r.status_code == 204
    assert await _count(db_session, LibraryItem, LibraryItem.id == item_id) == 0
    assert await _count(db_session, LibraryItem, LibraryItem.id == other_item_id) == 0
    assert await _count(db_session, Paper, Paper.id == paper_id) == 0


async def test_delete_rolls_back_when_storage_delete_fails(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid)
    item = await db_session.get(LibraryItem, item_id)
    assert item is not None
    paper = await db_session.get(Paper, item.paper_id)
    assert paper is not None
    paper_id = str(paper.id)
    paper.thumbnail_key = f"thumbnails/{paper_id}/card.webp"
    await db_session.commit()

    global _storage_failure
    _storage_failure = RuntimeError("s3 down")
    try:
        with pytest.raises(RuntimeError, match="s3 down"):
            await client.delete(f"/api/library-items/{item_id}")
    finally:
        _storage_failure = None

    await db_session.rollback()
    assert await _count(db_session, LibraryItem, LibraryItem.id == item_id) == 1
    assert await _count(db_session, Paper, Paper.id == paper_id) == 1


async def test_delete_cascades_related_personal_data(
    auth: tuple[AsyncClient, str], db_session: AsyncSession, factories: Any, redis_client: Any
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid)
    user = await db_session.get(User, uid)
    item = await db_session.get(LibraryItem, item_id)
    assert user is not None
    assert item is not None
    paper = await db_session.get(Paper, item.paper_id)
    assert paper is not None
    revision = await db_session.get(DocumentRevision, paper.latest_revision_id)
    assert revision is not None

    anchor = {
        "revision_id": str(revision.id),
        "block_id": "blk-p1",
        "start": 0,
        "end": 4,
        "quote": "flow",
        "side": "source",
    }
    thread = await factories.make_chat_thread(db_session, library_item=item)
    await factories.make_chat_message(db_session, thread=thread)
    await factories.make_note(db_session, library_item=item)
    await factories.make_annotation(db_session, library_item=item, anchor=anchor)
    await factories.make_vocab_entry(db_session, user=user, library_item=item, context_anchor=anchor)
    await factories.make_resource_link(db_session, library_item=item)
    await factories.make_collection(db_session, user=user, entries_of=[item])
    article = await factories.make_article(db_session, library_item=item, with_blocks=True)
    await factories.make_reading_session(db_session, library_item=item)
    job = await factories.make_job(db_session, user=user, paper=paper, library_item=item)
    usage = UsageRecord(
        user_id=uid,
        library_item_id=item_id,
        job_id=str(job.id),
        task="translation",
        provider="openai",
        model="test",
        key_source="operator",
        status="ok",
    )
    db_session.add(usage)
    glossary = Glossary(id=str(uuid.uuid4()), scope="paper", library_item_id=item_id)
    db_session.add(glossary)
    await db_session.flush()
    db_session.add(
        GlossaryTerm(
            id=str(uuid.uuid4()),
            glossary_id=str(glossary.id),
            source_term="flow",
            target_term="流れ",
        )
    )
    paper_id = str(paper.id)
    revision_id = str(revision.id)
    thread_id = str(thread.id)
    article_id = str(article.id)
    usage_id = usage.id
    glossary_id = str(glossary.id)
    db_session.add(
        Notification(
            user_id=uid,
            kind="translation_complete",
            payload={"library_item_id": item_id, "paper_title": paper.title},
        )
    )
    await db_session.commit()
    await redis_client.set(f"promo:checked:{paper_id}", "1")
    await redis_client.rpush(article_versions_cache_key(article_id), "cached-version")

    r = await client.delete(f"/api/library-items/{item_id}")
    assert r.status_code == 204

    assert await _count(db_session, LibraryItem, LibraryItem.id == item_id) == 0
    assert await _count(db_session, Paper, Paper.id == paper_id) == 0
    assert await _count(db_session, DocumentRevision, DocumentRevision.id == revision_id) == 0
    assert await _count(db_session, ChatThread, ChatThread.id == thread_id) == 0
    assert await _count(db_session, ChatMessage, ChatMessage.thread_id == thread_id) == 0
    assert await _count(db_session, Note, Note.library_item_id == item_id) == 0
    assert await _count(db_session, Annotation, Annotation.library_item_id == item_id) == 0
    assert await _count(db_session, VocabEntry, VocabEntry.library_item_id == item_id) == 0
    assert await _count(db_session, ResourceLink, ResourceLink.library_item_id == item_id) == 0
    assert await _count(db_session, CollectionEntry, CollectionEntry.library_item_id == item_id) == 0
    assert await _count(db_session, Article, Article.id == article_id) == 0
    assert await _count(db_session, ArticleBlock, ArticleBlock.article_id == article_id) == 0
    assert await _count(db_session, ReadingSession, ReadingSession.library_item_id == item_id) == 0
    assert await _count(db_session, Job, Job.library_item_id == item_id) == 0
    assert await _count(db_session, UsageRecord, UsageRecord.id == usage_id) == 0
    assert await _count(db_session, Glossary, Glossary.id == glossary_id) == 0
    assert await _count(db_session, GlossaryTerm, GlossaryTerm.glossary_id == glossary_id) == 0
    assert (
        await _count(
            db_session,
            Notification,
            Notification.payload["library_item_id"].astext == item_id,
        )
        == 0
    )
    assert await redis_client.exists(f"promo:checked:{paper_id}") == 0
    assert await redis_client.exists(article_versions_cache_key(article_id)) == 0
    assert {
        ("assets", f"renders/articles/{article_id}/"),
        ("assets", f"renders/overview/{article_id}/"),
    } <= _storage_deleted_prefixes()


async def test_delete_allows_extension_token(
    auth: tuple[AsyncClient, str],
    bare_client: AsyncClient,
    db_session: AsyncSession,
    redis_client: Any,
) -> None:
    """取り込みキャンセル(docs/08)は拡張ポップアップから呼ぶため拡張トークンで通す。"""
    _client, uid = auth
    item_id = await _mk_item(db_session, uid)
    await db_session.commit()
    token, _expires = await create_extension_token(redis_client, uid)

    r = await bare_client.delete(
        f"/api/library-items/{item_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 204


async def test_reject_tag_suggestion(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid, suggested=["cs.CV", "cs.LG"])
    await db_session.commit()

    r = await client.delete(f"/api/library-items/{item_id}/tag-suggestions/cs.CV")
    assert r.status_code == 204
    got = await client.get(f"/api/library-items/{item_id}")
    assert got.json()["suggested_tags"] == ["cs.LG"]


# ---------------------------------------------------------------------------
# tags(§5.13)・duplicate-resolution(§5.11)
# ---------------------------------------------------------------------------
async def test_tags_aggregation(auth: tuple[AsyncClient, str], db_session: AsyncSession) -> None:
    client, uid = auth
    await _mk_item(db_session, uid, tags=["distill", "cs.CV"])
    await _mk_item(db_session, uid, tags=["distill"])
    await db_session.commit()

    r = await client.get("/api/tags")
    assert r.status_code == 200
    items = r.json()["items"]
    counts = {t["tag"]: t["count"] for t in items}
    assert counts["distill"] == 2
    assert counts["cs.CV"] == 1
    # 件数降順(distill が先頭)。
    assert items[0]["tag"] == "distill"

    # 前方一致補完。
    r2 = await client.get("/api/tags", params={"q": "cs"})
    assert [t["tag"] for t in r2.json()["items"]] == ["cs.CV"]


async def test_duplicate_resolution_dismiss(
    auth: tuple[AsyncClient, str], db_session: AsyncSession
) -> None:
    client, uid = auth
    item_id = await _mk_item(db_session, uid)
    await db_session.commit()

    r = await client.post(
        f"/api/library-items/{item_id}/duplicate-resolution", json={"action": "dismiss"}
    )
    assert r.status_code == 200
    assert r.json()["library_item"]["id"] == item_id

    # merge は other_paper_id 必須。
    bad = await client.post(
        f"/api/library-items/{item_id}/duplicate-resolution", json={"action": "merge"}
    )
    assert bad.status_code == 422
