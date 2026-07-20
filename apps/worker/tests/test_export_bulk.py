"""``jobs.kind='export'`` ハンドラのテスト(PY-EXP-04。plans/03 §18・M2-15)。

全量 JSON(``export_user_data.run_export_full_job``)がライブラリ・注釈・メモ・チャット・
語彙(SRS 含む)・リソース・記事・コレクション・設定の全キーを持ち、対象データを反映した
zip を S3(assets バケット)へアップロードして ``jobs.result.download_url`` を確定させる
ことを検証する。DB は実 PostgreSQL、S3 は実 MinIO(worker conftest の規約と同じ)。
"""

from __future__ import annotations

import json
import uuid
import zipfile
from io import BytesIO
from typing import Any

from alinea_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    ChatMessage,
    ChatThread,
    Collection,
    CollectionEntry,
    DocumentRevision,
    Glossary,
    GlossaryTerm,
    LibraryItem,
    Note,
    Notification,
    Paper,
    ReadingSession,
    ResourceLink,
    SavedFilter,
    SourceAsset,
    TranslationSet,
    TranslationUnit,
    User,
    VocabEntry,
)
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_worker.tasks.export_user_data import (
    build_export_archive,
    build_export_payload,
    run_export_full_job,
)
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_user_data(db: AsyncSession) -> dict[str, str]:
    user = User(
        id=str(uuid.uuid4()),
        email=f"{uuid.uuid4().hex}@t.test",
        settings={"translation": {"default_style": "literal"}},
    )
    db.add(user)
    await db.flush()

    paper_id = str(uuid.uuid4())
    thumbnail_key = f"thumbnails/{paper_id}/card.webp"
    paper = Paper(
        id=paper_id,
        title="Flow Straight and Fast",
        authors=[{"name": "Xingchang Liu"}],
        arxiv_id=f"2209.{uuid.uuid4().int % 100000:05d}",
        abstract="We propose rectified flow.",
        abstract_ja="整流フローを提案する。",
        summary_lines=["整流フロー", "直線軌道"],
        arxiv_categories=["cs.LG", "cs.CV"],
        license="arxiv-nonexclusive",
        bib_estimated=False,
        visibility="private",
        latest_version="v2",
        official_repo_url="https://github.com/gnobitab/RectifiedFlow",
        extracted_terms=[{"term": "rectified flow"}],
        thumbnail_key=thumbnail_key,
        owner_user_id=user.id,
    )
    db.add(paper)
    await db.flush()

    item_thumbnail_key = f"thumbnails/{paper_id}/item-card.webp"
    item = LibraryItem(
        id=str(uuid.uuid4()),
        user_id=user.id,
        paper_id=paper.id,
        status="reading",
        tags=["flow"],
        suggested_tags=["ml", "ot"],
        reading_position={"block_id": "blk-42", "offset": 10},
        queue_order=3,
        thumbnail_key=item_thumbnail_key,
    )
    db.add(item)
    await db.flush()

    db.add(Note(id=str(uuid.uuid4()), library_item_id=item.id, title="要点", body_md="整流フロー"))
    db.add(
        Annotation(
            id=str(uuid.uuid4()),
            library_item_id=item.id,
            kind="highlight",
            color="important",
            anchor={
                "revision_id": str(uuid.uuid4()),
                "block_id": "blk-1",
                "start": 0,
                "end": 5,
                "quote": "flow",
                "side": "source",
            },
        )
    )

    thread = ChatThread(id=str(uuid.uuid4()), library_item_id=item.id, title="メイン", is_main=True)
    db.add(thread)
    await db.flush()
    db.add(
        ChatMessage(
            thread_id=thread.id,
            role="user",
            content={"segments": [{"type": "text", "text": "質問"}]},
            text_plain="質問",
        )
    )

    db.add(
        VocabEntry(
            id=str(uuid.uuid4()),
            user_id=user.id,
            library_item_id=item.id,
            term="rectified flow",
            context_anchor={
                "revision_id": str(uuid.uuid4()),
                "block_id": "blk-1",
                "start": 0,
                "end": 5,
                "quote": "flow",
                "side": "source",
            },
            context_sentence="Rectified flow learns a straight map.",
        )
    )

    db.add(
        ResourceLink(
            id=str(uuid.uuid4()),
            library_item_id=item.id,
            kind="github",
            url="https://github.com/gnobitab/RectifiedFlow",
            url_normalized="https://github.com/gnobitab/rectifiedflow",
        )
    )

    article = Article(id=str(uuid.uuid4()), library_item_id=item.id, title="やさしい解説")
    db.add(article)
    await db.flush()
    db.add(
        ArticleBlock(
            article_id=article.id,
            position=0,
            type="heading",
            content={"heading": {"level": 2, "text": "はじめに"}},
            text_plain="はじめに",
        )
    )

    collection = Collection(id=str(uuid.uuid4()), user_id=user.id, name="輪読会")
    db.add(collection)
    await db.flush()
    db.add(
        CollectionEntry(
            id=str(uuid.uuid4()), collection_id=collection.id, library_item_id=item.id, position=0
        )
    )

    # DocumentRevision
    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        source_version="v1",
        parser_version="p1",
        quality_level="A",
        source_format="latex",
        content={"quality_level": "A", "sections": [{"id": "s1", "blocks": []}]},
        stats={"block_count": 1},
    )
    db.add(revision)
    await db.flush()

    # TranslationSet + TranslationUnit (scope=personal requires user_id)
    tset = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision.id,
        style="natural",
        scope="personal",
        user_id=user.id,
        status="complete",
    )
    db.add(tset)
    await db.flush()
    db.add(
        TranslationUnit(
            set_id=tset.id,
            block_id="blk-1",
            source_hash="abc123",
            content_ja=[{"type": "text", "text": "フロー"}],
            text_ja="フロー",
            state="machine",
        )
    )

    # Glossary + GlossaryTerm
    glossary = Glossary(
        id=str(uuid.uuid4()),
        scope="user",
        user_id=user.id,
        name="マイ用語集",
    )
    db.add(glossary)
    await db.flush()
    db.add(
        GlossaryTerm(
            id=str(uuid.uuid4()),
            glossary_id=glossary.id,
            source_term="rectified flow",
            target_term="整流フロー",
            pos_label="noun",
        )
    )

    # SavedFilter
    db.add(
        SavedFilter(
            id=str(uuid.uuid4()),
            user_id=user.id,
            name="未読",
            conditions={"status": "planned"},
        )
    )

    # ReadingSession
    db.add(
        ReadingSession(
            library_item_id=item.id,
            active_seconds=300,
            view_mode="translation",
        )
    )

    # Notification
    db.add(
        Notification(
            user_id=user.id,
            kind="translation_complete",
            payload={"message": "テスト通知"},
        )
    )

    # SourceAsset
    db.add(
        SourceAsset(
            id=str(uuid.uuid4()),
            paper_id=paper.id,
            kind="pdf",
            storage_key=f"assets/papers/{paper.id}/paper.pdf",
            content_type="application/pdf",
            byte_size=12345,
            sha256="abc123def456",
        )
    )

    await db.commit()
    return {
        "user_id": str(user.id),
        "paper_id": str(paper.id),
        "library_item_id": str(item.id),
        "asset_key": f"assets/papers/{paper.id}/paper.pdf",
        "paper_thumbnail_key": thumbnail_key,
        "item_thumbnail_key": item_thumbnail_key,
    }


async def _run_export_job(db: AsyncSession, user_id: str) -> Any:
    store = JobStore(db)
    job_id = await store.enqueue(kind="export", user_id=user_id, payload={})
    job = await store.claim(job_id)
    assert job is not None
    await run_export_full_job({}, store, job)
    return await store.get(job_id)


async def test_build_export_payload_has_all_category_keys(db_session: AsyncSession) -> None:
    ids = await _seed_user_data(db_session)
    payload = await build_export_payload(db_session, ids["user_id"])

    for key in (
        "exported_at",
        "user",
        "library",
        "notes",
        "annotations",
        "chat_threads",
        "vocab",
        "resources",
        "articles",
        "collections",
        "settings",
    ):
        assert key in payload, f"missing key: {key}"

    assert len(payload["library"]) == 1
    assert payload["library"][0]["library_item_id"] == ids["library_item_id"]
    assert len(payload["notes"]) == 1
    assert payload["notes"][0]["body_md"] == "整流フロー"
    assert len(payload["annotations"]) == 1
    assert len(payload["chat_threads"]) == 1
    assert payload["chat_threads"][0]["messages"][0]["text"] == "質問"
    assert len(payload["vocab"]) == 1
    assert payload["vocab"][0]["srs"]["stage"] == 1  # 既定ステージ(plans/02)
    assert len(payload["resources"]) == 1
    assert len(payload["articles"]) == 1
    assert payload["articles"][0]["blocks"][0]["content"]["heading"]["text"] == "はじめに"
    assert len(payload["collections"]) == 1
    assert payload["collections"][0]["library_item_ids"] == [ids["library_item_id"]]
    assert payload["settings"]["translation"]["default_style"] == "literal"


async def test_run_export_full_job_uploads_zip_and_sets_download_url(
    db_session: AsyncSession,
) -> None:
    ids = await _seed_user_data(db_session)
    job = await _run_export_job(db_session, ids["user_id"])

    assert job is not None
    assert job.status == "succeeded"
    download_url = job.result.get("download_url")
    assert isinstance(download_url, str) and download_url

    storage = S3Storage()
    key = StorageKeys.export(ids["user_id"], str(job.id))
    archive = await storage.get(storage.assets_bucket, key)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "data.json" in names
        payload = json.loads(zf.read("data.json"))
    assert payload["user"]["id"] == ids["user_id"]
    assert len(payload["library"]) == 1


async def test_export_job_for_user_with_no_data_still_succeeds(db_session: AsyncSession) -> None:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db_session.add(user)
    await db_session.commit()

    job = await _run_export_job(db_session, str(user.id))
    assert job is not None
    assert job.status == "succeeded"
    assert isinstance(job.result.get("download_url"), str)


async def test_export_payload_includes_generated_content(db_session: AsyncSession) -> None:
    ids = await _seed_user_data(db_session)
    payload = await build_export_payload(db_session, ids["user_id"])
    assert payload["schema_version"] == 2
    # 本文・翻訳が含まれる
    assert len(payload["document_revisions"]) >= 1
    assert payload["document_revisions"][0]["content"]  # 構造化本文 JSONB
    assert len(payload["translation_sets"]) >= 1
    assert len(payload["translation_units"]) >= 1
    # 用語集・保存フィルタ・読書セッション・通知・図メタ・アセットメタ
    for key in (
        "glossaries", "glossary_terms", "saved_filters", "reading_sessions",
        "notifications", "overview_figures", "explainer_figures", "source_assets",
    ):
        assert key in payload, key
    # source_asset メタは storage_key/sha256/byte_size を持つ
    assert payload["source_assets"][0]["storage_key"]


async def test_export_archive_bundles_assets_and_manifest(db_session: AsyncSession) -> None:
    ids = await _seed_user_data(db_session)
    storage = S3Storage()
    # source_asset が指す storage_key に実バイナリを置く
    await storage.put(storage.sources_bucket, ids["asset_key"], b"%PDF-1.7 fake",
                      content_type="application/pdf")
    archive = await build_export_archive(db_session, ids["user_id"], storage)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "data.json" in names
        assert f"assets/{ids['asset_key']}" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["schema_version"] == 2
        entry = next(a for a in manifest["assets"] if a["storage_key"] == ids["asset_key"])
        assert entry["sha256"]
        assert zf.read(f"assets/{ids['asset_key']}") == b"%PDF-1.7 fake"


async def test_export_all_columns_present(db_session: AsyncSession) -> None:
    """PAPER_EXPORT_FIELDS と LIBRARY_EXPORT_FIELDS の全列がペイロードに含まれることを検証する。"""
    ids = await _seed_user_data(db_session)
    payload = await build_export_payload(db_session, ids["user_id"])

    lib = payload["library"][0]
    # Paper フィールド
    for field in (
        "arxiv_id", "doi", "pdf_sha256", "title", "authors", "abstract", "abstract_ja",
        "summary_lines", "published_on", "venue", "arxiv_categories", "license",
        "bib_estimated", "visibility", "latest_version", "official_repo_url",
        "extracted_terms", "thumbnail_key",
    ):
        assert field in lib, f"library entry missing paper field: {field}"

    # LibraryItem フィールド
    for field in (
        "status", "priority", "deadline", "tags", "suggested_tags", "one_line_note",
        "understanding", "importance", "reading_position", "queue_order",
        "total_active_seconds", "thumbnail_key", "added_at", "finished_at",
    ):
        assert field in lib, f"library entry missing library_item field: {field}"

    # 値が実際に入っている
    assert lib["abstract_ja"] == "整流フローを提案する。"
    assert lib["summary_lines"] == ["整流フロー", "直線軌道"]
    assert lib["arxiv_categories"] == ["cs.LG", "cs.CV"]
    assert lib["license"] == "arxiv-nonexclusive"
    assert lib["official_repo_url"] == "https://github.com/gnobitab/RectifiedFlow"
    assert lib["thumbnail_key"] == ids["item_thumbnail_key"]
    assert lib["suggested_tags"] == ["ml", "ot"]
    assert lib["reading_position"] == {"block_id": "blk-42", "offset": 10}
    assert lib["queue_order"] == 3


async def test_export_document_asset_keys_collected(db_session: AsyncSession) -> None:
    """DocumentRevision の figure/table block の asset_key が manifest に収集されることを検証する。"""
    ids = await _seed_user_data(db_session)
    storage = S3Storage()

    # figure block が参照する asset_key を S3 に置く
    figure_asset_key = f"figures/{ids['paper_id']}/rev1/blk-fig.png"
    await storage.put(storage.assets_bucket, figure_asset_key, b"\x89PNG fake figure",
                      content_type="image/png")
    # paper thumbnail を S3 に置く
    await storage.put(storage.assets_bucket, ids["paper_thumbnail_key"], b"WEBP thumb",
                      content_type="image/webp")
    # item thumbnail を S3 に置く
    await storage.put(storage.assets_bucket, ids["item_thumbnail_key"], b"WEBP item thumb",
                      content_type="image/webp")

    # DocumentRevision の content に figure block を追加する
    from alinea_core.db.models import DocumentRevision as DR
    from sqlalchemy import select
    revs = (await db_session.execute(select(DR))).scalars().all()
    for rev in revs:
        rev.content = {
            "quality_level": "A",
            "sections": [{
                "id": "s1",
                "blocks": [
                    {"id": "blk-fig", "type": "figure", "asset_key": figure_asset_key},
                ],
            }],
        }
    await db_session.commit()

    archive = await build_export_archive(db_session, ids["user_id"], storage)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        names = set(zf.namelist())
        assert f"assets/{figure_asset_key}" in names, "figure asset_key missing from archive"
        assert f"assets/{ids['paper_thumbnail_key']}" in names, "paper thumbnail missing"
        assert f"assets/{ids['item_thumbnail_key']}" in names, "item thumbnail missing"
