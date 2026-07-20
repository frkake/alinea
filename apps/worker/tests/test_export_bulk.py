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
    ArticlePublication,
    ChatMessage,
    ChatThread,
    CodeAnalysisRun,
    CodeCorrespondence,
    Collection,
    CollectionEntry,
    DocumentRevision,
    Glossary,
    GlossaryTerm,
    LibraryItem,
    Note,
    Notification,
    Paper,
    PaperExternalId,
    PresentationArtifact,
    PublicationComment,
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

    resource_link_id = str(uuid.uuid4())
    db.add(
        ResourceLink(
            id=resource_link_id,
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

    # 記事公開スナップショット(Task 24)。公開 URL は public のまま保存され、
    # 復元時には必ず unlisted に落ちる(import 側で検証)。
    publication_id = str(uuid.uuid4())
    publication_slug = f"yasashii-{uuid.uuid4().hex[:8]}"
    db.add(
        ArticlePublication(
            id=publication_id,
            article_id=article.id,
            user_id=user.id,
            slug=publication_slug,
            visibility="public",
            snapshot_version=2,
            title="やさしい解説",
            paper_meta={"title": "Flow Straight and Fast"},
            blocks=[{"type": "heading", "content": {"heading": {"level": 2, "text": "はじめに"}}}],
        )
    )

    # 記事公開コメント(Task 25)。本人 own コメントはバックアップに含まれ、第三者コメントは
    # 本人所有データではないため含まれない(export 側の絞り込みを検証する)。
    own_comment_id = str(uuid.uuid4())
    db.add(
        PublicationComment(
            id=own_comment_id,
            publication_id=publication_id,
            user_id=user.id,
            block_id="0",
            body="自分のコメント",
            status="visible",
        )
    )
    # 第三者(別ユーザー)が本人の公開記事に残したコメント。
    other_user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(other_user)
    await db.flush()
    third_party_comment_id = str(uuid.uuid4())
    db.add(
        PublicationComment(
            id=third_party_comment_id,
            publication_id=publication_id,
            user_id=other_user.id,
            block_id="0",
            body="第三者のコメント",
            status="visible",
        )
    )

    # プレゼンテーション成果物(Task 28)。最新版のみ(library_item ごと unique)。PPTX は
    # assets バケットの job 別 key を指す(上書きしない no-overwrite key)。
    presentation_job_id = str(uuid.uuid4())
    presentation_artifact_id = str(uuid.uuid4())
    presentation_pptx_key = StorageKeys.presentation_pptx(str(item.id), presentation_job_id)

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

    # Shared TranslationSet + TranslationUnit (scope=shared requires user_id IS NULL)。
    # 完全バックアップはこの共有翻訳をリビジョン到達で拾い、移行先へ無損失で復元する。
    shared_set = TranslationSet(
        id=str(uuid.uuid4()),
        revision_id=revision.id,
        style="literal",
        scope="shared",
        user_id=None,
        status="complete",
    )
    db.add(shared_set)
    await db.flush()
    db.add(
        TranslationUnit(
            set_id=shared_set.id,
            block_id="blk-1",
            source_hash="shared123",
            content_ja=[{"type": "text", "text": "共有フロー"}],
            text_ja="共有フロー",
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

    # Paper-scoped Glossary + GlossaryTerm (scope=paper requires user_id IS NULL,
    # library_item_id set)。論文単位の用語集も完全バックアップの復元対象に含める。
    paper_glossary = Glossary(
        id=str(uuid.uuid4()),
        scope="paper",
        user_id=None,
        library_item_id=item.id,
        name="論文用語集",
    )
    db.add(paper_glossary)
    await db.flush()
    db.add(
        GlossaryTerm(
            id=str(uuid.uuid4()),
            glossary_id=paper_glossary.id,
            source_term="straight map",
            target_term="直線写像",
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

    # PaperExternalId(サイト取り込み由来の名寄せ識別子。完全バックアップに含める)。
    external_id = f"2023.acl-long.{uuid.uuid4().int % 1000}"
    db.add(
        PaperExternalId(
            id=str(uuid.uuid4()),
            paper_id=paper.id,
            site="acl_anthology",
            external_id=external_id,
            canonical_url=f"https://aclanthology.org/{external_id}/",
        )
    )

    # プレゼンテーション成果物(Task 28)。revision 作成後に紐づける。
    db.add(
        PresentationArtifact(
            id=presentation_artifact_id,
            library_item_id=item.id,
            source_revision_id=revision.id,
            generation_job_id=presentation_job_id,
            preset="reading_group",
            audience="students",
            instruction="要点だけスライドに",
            model_provider="openai",
            model_id="gpt-5.5",
            ppt_master_revision="0c0bdaf0dd953afc2c00322e92f26dc02fc1c51f",
            pptx_storage_key=presentation_pptx_key,
        )
    )

    # コード対応解析の run + correspondence(Task 21)。完全バックアップに含める。
    code_run_id = str(uuid.uuid4())
    code_corr_id = str(uuid.uuid4())
    db.add(
        CodeAnalysisRun(
            id=code_run_id,
            user_id=user.id,
            library_item_id=item.id,
            resource_id=resource_link_id,
            revision_id=revision.id,
            commit_sha="c0ffee0000000000000000000000000000000000",
            analysis_version="ca-2026-07-17.1",
            trigger="on_demand",
            status="succeeded",
            estimated_cost_usd="0.12",
            actual_cost_usd="0.09876543",
        )
    )
    await db.flush()
    db.add(
        CodeCorrespondence(
            id=code_corr_id,
            run_id=code_run_id,
            position=0,
            paper_anchor={"revision_id": str(revision.id), "block_id": "blk-1"},
            claim_text="We train with the rectified flow loss.",
            path="model.py",
            symbol="train",
            start_line=1,
            end_line=4,
            code_excerpt="loss = compute_loss(model, data)",
            explanation_ja="学習ループの損失計算に対応。",
            confidence="high",
        )
    )

    await db.commit()
    return {
        "user_id": str(user.id),
        "paper_id": str(paper.id),
        "library_item_id": str(item.id),
        "paper_id": str(paper.id),
        "presentation_artifact_id": presentation_artifact_id,
        "presentation_job_id": presentation_job_id,
        "presentation_pptx_key": presentation_pptx_key,
        "asset_key": f"assets/papers/{paper.id}/paper.pdf",
        "paper_thumbnail_key": thumbnail_key,
        "item_thumbnail_key": item_thumbnail_key,
        "revision_id": str(revision.id),
        "shared_set_id": str(shared_set.id),
        "paper_glossary_id": str(paper_glossary.id),
        "external_id": external_id,
        "article_id": str(article.id),
        "publication_id": publication_id,
        "publication_slug": publication_slug,
        "own_comment_id": own_comment_id,
        "third_party_comment_id": third_party_comment_id,
        "other_user_id": str(other_user.id),
        "resource_link_id": resource_link_id,
        "code_run_id": code_run_id,
        "code_corr_id": code_corr_id,
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


async def test_export_includes_shared_translation_and_paper_glossary(
    db_session: AsyncSession,
) -> None:
    """完全バックアップは共有翻訳(user_id IS NULL)と論文用用語集を復元対象に含める。"""
    ids = await _seed_user_data(db_session)
    payload = await build_export_payload(db_session, ids["user_id"])

    # 共有翻訳セット(user_id IS NULL / scope='shared')がエクスポートに含まれる。
    shared_sets = [ts for ts in payload["translation_sets"] if ts["scope"] == "shared"]
    assert len(shared_sets) == 1, payload["translation_sets"]
    assert shared_sets[0]["id"] == ids["shared_set_id"]
    assert shared_sets[0]["user_id"] is None
    assert shared_sets[0]["revision_id"] == ids["revision_id"]

    # 共有セットの翻訳単位も追随する。
    shared_units = [
        u for u in payload["translation_units"] if u["set_id"] == ids["shared_set_id"]
    ]
    assert len(shared_units) == 1
    assert shared_units[0]["text_ja"] == "共有フロー"

    # 論文用用語集(user_id IS NULL / scope='paper')がエクスポートに含まれる。
    paper_glossaries = [g for g in payload["glossaries"] if g["scope"] == "paper"]
    assert len(paper_glossaries) == 1, payload["glossaries"]
    assert paper_glossaries[0]["id"] == ids["paper_glossary_id"]
    assert paper_glossaries[0]["user_id"] is None
    assert paper_glossaries[0]["library_item_id"] == ids["library_item_id"]

    # 論文用用語集の用語も追随する。
    paper_terms = [
        t for t in payload["glossary_terms"] if t["glossary_id"] == ids["paper_glossary_id"]
    ]
    assert len(paper_terms) == 1
    assert paper_terms[0]["source_term"] == "straight map"


async def test_export_includes_paper_external_id(db_session: AsyncSession) -> None:
    """完全バックアップはサイト取り込み由来の外部識別子(名寄せ用)を含める。"""
    ids = await _seed_user_data(db_session)
    payload = await build_export_payload(db_session, ids["user_id"])

    assert "paper_external_ids" in payload
    rows = payload["paper_external_ids"]
    assert len(rows) == 1
    row = rows[0]
    assert row["paper_id"] == ids["paper_id"]
    assert row["site"] == "acl_anthology"
    assert row["external_id"] == ids["external_id"]
    assert row["canonical_url"] == f"https://aclanthology.org/{ids['external_id']}/"


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
    # Paper フィールド(paper_thumbnail_key = Paper.thumbnail_key のエクスポートキー)
    for field in (
        "arxiv_id", "doi", "pdf_sha256", "title", "authors", "abstract", "abstract_ja",
        "summary_lines", "published_on", "venue", "arxiv_categories", "license",
        "bib_estimated", "visibility", "latest_version", "official_repo_url",
        "extracted_terms", "paper_thumbnail_key",
    ):
        assert field in lib, f"library entry missing paper field: {field}"

    # LibraryItem フィールド(thumbnail_key = LibraryItem.thumbnail_key)
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
    assert lib["thumbnail_key"] == ids["item_thumbnail_key"]      # LibraryItem thumbnail
    assert lib["paper_thumbnail_key"] == ids["paper_thumbnail_key"]  # Paper thumbnail
    assert lib["pdf_sha256"] is None  # not set in seed
    assert lib["suggested_tags"] == ["ml", "ot"]
    assert lib["reading_position"] == {"block_id": "blk-42", "offset": 10}
    assert lib["queue_order"] == 3


async def test_export_includes_publication_snapshot(db_session: AsyncSession) -> None:
    """記事公開スナップショット(Task 24)がエクスポートに含まれる。"""
    ids = await _seed_user_data(db_session)
    payload = await build_export_payload(db_session, ids["user_id"])

    assert "publications" in payload
    pubs = payload["publications"]
    assert len(pubs) == 1
    pub = pubs[0]
    assert pub["id"] == ids["publication_id"]
    assert pub["article_id"] == ids["article_id"]
    assert pub["slug"] == ids["publication_slug"]
    # エクスポートは可視性を保持する(復元時に unlisted へ落とすのは import 側の責務)。
    assert pub["visibility"] == "public"
    assert pub["snapshot_version"] == 2
    assert pub["blocks"][0]["type"] == "heading"


async def test_export_includes_only_own_publication_comment(db_session: AsyncSession) -> None:
    """記事公開コメント(Task 25)は本人 own のものだけをバックアップに含める。

    第三者が本人の公開記事へ残したコメントは本人所有データではないため複製しない。
    """
    ids = await _seed_user_data(db_session)
    payload = await build_export_payload(db_session, ids["user_id"])

    assert "publication_comments" in payload
    comments = payload["publication_comments"]
    # 本人 own の 1 件だけが含まれる(第三者コメントは含まない)。
    assert len(comments) == 1, comments
    row = comments[0]
    assert row["id"] == ids["own_comment_id"]
    assert row["publication_id"] == ids["publication_id"]
    assert row["body"] == "自分のコメント"
    assert row["block_id"] == "0"
    # 第三者コメントの id は決してエクスポートに現れない。
    exported_ids = {c["id"] for c in comments}
    assert ids["third_party_comment_id"] not in exported_ids


async def test_export_includes_presentation_artifact(db_session: AsyncSession) -> None:
    """プレゼンテーション成果物(Task 28)の metadata がエクスポートに含まれる。"""
    ids = await _seed_user_data(db_session)
    payload = await build_export_payload(db_session, ids["user_id"])

    assert "presentations" in payload
    rows = payload["presentations"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == ids["presentation_artifact_id"]
    assert row["library_item_id"] == ids["library_item_id"]
    assert row["source_revision_id"] == ids["revision_id"]
    assert row["generation_job_id"] == ids["presentation_job_id"]
    assert row["preset"] == "reading_group"
    assert row["audience"] == "students"
    assert row["model_provider"] == "openai"
    assert row["model_id"] == "gpt-5.5"
    assert row["pptx_storage_key"] == ids["presentation_pptx_key"]


async def test_export_archive_bundles_presentation_pptx(db_session: AsyncSession) -> None:
    """PPTX バイトが manifest + assets/ に含まれる(assets バケット)。"""
    ids = await _seed_user_data(db_session)
    storage = S3Storage()
    await storage.put(
        storage.assets_bucket,
        ids["presentation_pptx_key"],
        b"PK\x03\x04 fake pptx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
    )
    archive = await build_export_archive(db_session, ids["user_id"], storage)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        names = set(zf.namelist())
        assert f"assets/{ids['presentation_pptx_key']}" in names
        assert zf.read(f"assets/{ids['presentation_pptx_key']}") == b"PK\x03\x04 fake pptx"
        manifest = json.loads(zf.read("manifest.json"))
        entry = next(
            a for a in manifest["assets"] if a["storage_key"] == ids["presentation_pptx_key"]
        )
        assert entry["sha256"]


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
        manifest = json.loads(zf.read("manifest.json"))
        # manifest には paper thumbnail の sha256/byte_size が記録される
        thumb_entry = next(
            (a for a in manifest["assets"] if a["storage_key"] == ids["paper_thumbnail_key"]),
            None,
        )
        assert thumb_entry is not None, "paper thumbnail not in manifest assets"
        assert thumb_entry["sha256"], "manifest sha256 must be non-empty"
        assert thumb_entry["byte_size"] == len(b"WEBP thumb")


async def test_export_payload_includes_code_analysis(db_session: AsyncSession) -> None:
    """コード対応解析の run + correspondence が完全バックアップに含まれる(Task 21)。"""
    ids = await _seed_user_data(db_session)
    payload = await build_export_payload(db_session, ids["user_id"])

    assert "code_analysis_runs" in payload
    assert "code_correspondences" in payload
    runs = payload["code_analysis_runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["id"] == ids["code_run_id"]
    assert run["commit_sha"] == "c0ffee0000000000000000000000000000000000"
    assert run["resource_id"] == ids["resource_link_id"]
    assert run["revision_id"] == ids["revision_id"]
    assert run["status"] == "succeeded"

    corrs = payload["code_correspondences"]
    assert len(corrs) == 1
    assert corrs[0]["id"] == ids["code_corr_id"]
    assert corrs[0]["run_id"] == ids["code_run_id"]
    assert corrs[0]["path"] == "model.py"
    assert corrs[0]["confidence"] == "high"
    # 固定 commit URL を再利用できる(commit_sha が保持される)。
    assert len(run["commit_sha"]) == 40
