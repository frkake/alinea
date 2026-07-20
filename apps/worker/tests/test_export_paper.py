"""``jobs.kind='paper_export'`` ハンドラのテスト(Feature S3・Task 11)。

論文単位のスタンドアロンエクスポート zip を検証する:

- 選択した成果物(原文/訳文/対訳/記事 HTML・原文/訳文/対訳 PDF)が zip 内に正しいファイル名で入る。
- 原文 PDF に block_search_index の page/bbox を使ったハイライト矩形 + コメント(popup)注釈が
  埋め込まれ、``fitz`` で読み戻した注釈数が一致する。
- bbox を持たないブロックに紐づく注釈は ``skipped_annotations`` に数え、黙って成功扱いにしない。
- 対訳 PDF のページ順が ``source-1, translated-1, source-2, translated-2, ...`` になる。
- 選択された成果物が未生成なら、生成を始める前にジョブが失敗する。

外部ネットワーク禁止のため S3 は in-memory フェイクを注入し、PDF はフィクスチャで生成する。
DB は worker conftest の規約どおり実 PostgreSQL(``db_session``)を使う。
"""

from __future__ import annotations

import uuid
import zipfile
from io import BytesIO
from typing import Any

import fitz
import pytest
from alinea_core.db.models import (
    Annotation,
    Article,
    ArticleBlock,
    BlockSearchIndex,
    DocumentRevision,
    LibraryItem,
    Paper,
    SourceAsset,
    TranslationSet,
    TranslationUnit,
    User,
)
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import StorageKeys
from alinea_worker.tasks.export_paper import ArtifactUnavailableError, run_export_paper_job
from sqlalchemy.ext.asyncio import AsyncSession


# --------------------------------------------------------------------------- #
# in-memory S3 フェイク(S3Storage のサブセットを実装)
# --------------------------------------------------------------------------- #
class FakeS3Storage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.sources_bucket = "sources"
        self.assets_bucket = "assets"

    async def put(
        self,
        bucket: str,
        key: str,
        body: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> None:
        self.objects[(bucket, key)] = bytes(body)

    async def get(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]

    async def presign_get(self, bucket: str, key: str, expires_in: int = 600) -> str:
        return f"https://fake-s3.local/{bucket}/{key}?sig=test"


# --------------------------------------------------------------------------- #
# フィクスチャ PDF 生成
# --------------------------------------------------------------------------- #
def _make_pdf(labels: list[str]) -> bytes:
    doc = fitz.open()
    for label in labels:
        page = doc.new_page()
        page.insert_text((72, 100), label)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _read_pdf_page_texts(data: bytes) -> list[str]:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return [page.get_text().strip() for page in doc]
    finally:
        doc.close()


def _count_annotations(data: bytes) -> dict[str, int]:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        counts: dict[str, int] = {}
        for page in doc:
            for annot in page.annots() or []:
                name = annot.type[1]
                counts[name] = counts.get(name, 0) + 1
        return counts
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# シード
# --------------------------------------------------------------------------- #
async def _seed(db: AsyncSession, storage: FakeS3Storage) -> dict[str, str]:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()

    paper_id = str(uuid.uuid4())
    source_version = "v1"
    arxiv_id = f"2209.{uuid.uuid4().int % 100000:05d}"
    paper = Paper(
        id=paper_id,
        arxiv_id=arxiv_id,
        title="Flow Straight and Fast",
        authors=[{"name": "Xingchao Liu"}],
        abstract="We propose rectified flow.",
        visibility="private",
        latest_version=source_version,
        owner_user_id=user.id,
    )
    db.add(paper)
    await db.flush()

    revision = DocumentRevision(
        id=str(uuid.uuid4()),
        paper_id=paper.id,
        source_version=source_version,
        parser_version="p1",
        quality_level="B",
        source_format="pdf",
        content={
            "quality_level": "B",
            "sections": [
                {
                    "id": "s1",
                    "heading": {"number": "1", "title": "Introduction"},
                    "blocks": [
                        {
                            "id": "blk-1",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "Rectified flow is a method."}],
                        },
                        {
                            "id": "blk-2",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "It learns straight maps."}],
                        },
                        {
                            "id": "blk-3",
                            "type": "paragraph",
                            "inlines": [{"t": "text", "v": "No position for this block."}],
                        },
                    ],
                }
            ],
        },
        stats={},
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id
    await db.flush()

    # block_search_index: blk-1/blk-2 は page/bbox あり、blk-3 は bbox なし。
    db.add(
        BlockSearchIndex(
            revision_id=revision.id,
            block_id="blk-1",
            block_type="paragraph",
            section_path="s1",
            section_label="1 Introduction",
            position=0,
            source_text="Rectified flow is a method.",
            page=1,
            bbox=[72.0, 90.0, 320.0, 110.0],
        )
    )
    db.add(
        BlockSearchIndex(
            revision_id=revision.id,
            block_id="blk-2",
            block_type="paragraph",
            section_path="s1",
            section_label="1 Introduction",
            position=1,
            source_text="It learns straight maps.",
            page=1,
            bbox=[72.0, 120.0, 320.0, 140.0],
        )
    )
    db.add(
        BlockSearchIndex(
            revision_id=revision.id,
            block_id="blk-3",
            block_type="paragraph",
            section_path="s1",
            section_label="1 Introduction",
            position=2,
            source_text="No position for this block.",
            page=None,
            bbox=None,
        )
    )

    item = LibraryItem(
        id=str(uuid.uuid4()),
        user_id=user.id,
        paper_id=paper.id,
        status="reading",
    )
    db.add(item)
    await db.flush()

    # 注釈(ck_annotations_kind_shape: highlight は color あり body なし、comment は color+body)。
    # blk-1: comment(矩形 + popup)・blk-2: highlight(矩形のみ)・blk-3: comment だが bbox なし
    # → skipped に数える。
    db.add(
        Annotation(
            id=str(uuid.uuid4()),
            library_item_id=item.id,
            kind="comment",
            color="important",
            body="This is the key idea.",
            anchor={"revision_id": revision.id, "block_id": "blk-1", "side": "source"},
        )
    )
    db.add(
        Annotation(
            id=str(uuid.uuid4()),
            library_item_id=item.id,
            kind="highlight",
            color="question",
            body=None,
            anchor={"revision_id": revision.id, "block_id": "blk-2", "side": "source"},
        )
    )
    db.add(
        Annotation(
            id=str(uuid.uuid4()),
            library_item_id=item.id,
            kind="comment",
            color="idea",
            body="Comment on an unplaced block.",
            anchor={"revision_id": revision.id, "block_id": "blk-3", "side": "source"},
        )
    )

    # 翻訳(natural・complete)+ ユニット。
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
            source_hash="h1",
            content_ja=[{"t": "text", "v": "整流フローは手法である。"}],
            text_ja="整流フローは手法である。",
            state="machine",
        )
    )
    db.add(
        TranslationUnit(
            set_id=tset.id,
            block_id="blk-2",
            source_hash="h2",
            content_ja=[{"t": "text", "v": "直線写像を学習する。"}],
            text_ja="直線写像を学習する。",
            state="machine",
        )
    )

    # 記事。
    article = Article(id=str(uuid.uuid4()), library_item_id=item.id, title="やさしい解説")
    db.add(article)
    await db.flush()
    db.add(
        ArticleBlock(
            article_id=article.id,
            position=0,
            type="heading",
            content={"level": 2, "text": "はじめに"},
            text_plain="はじめに",
        )
    )
    db.add(
        ArticleBlock(
            article_id=article.id,
            position=1,
            type="paragraph",
            content={"markdown": "これは **記事** の本文です。"},
            text_plain="これは 記事 の本文です。",
        )
    )

    # 原本 PDF / 訳文 PDF のアセット + バイト列。
    original_key = StorageKeys.original_pdf(paper_id, source_version)
    db.add(
        SourceAsset(
            id=str(uuid.uuid4()),
            paper_id=paper.id,
            kind="pdf",
            source_version=source_version,
            storage_key=original_key,
            content_type="application/pdf",
            byte_size=1,
        )
    )
    translated_key = StorageKeys.translated_pdf(
        paper_id, source_version, "natural", translation_set_id=tset.id
    )
    db.add(
        SourceAsset(
            id=str(uuid.uuid4()),
            paper_id=paper.id,
            kind="translated_pdf",
            source_version=source_version,
            storage_key=translated_key,
            content_type="application/pdf",
            byte_size=1,
        )
    )
    await db.commit()

    await storage.put(
        storage.sources_bucket,
        original_key,
        _make_pdf(["SOURCE PAGE 1", "SOURCE PAGE 2"]),
        content_type="application/pdf",
    )
    await storage.put(
        storage.sources_bucket,
        translated_key,
        _make_pdf(["TRANSLATED PAGE 1", "TRANSLATED PAGE 2"]),
        content_type="application/pdf",
    )

    return {
        "user_id": str(user.id),
        "paper_id": str(paper.id),
        "library_item_id": str(item.id),
        "revision_id": str(revision.id),
        "arxiv_id": arxiv_id,
        "original_key": original_key,
        "translated_key": translated_key,
    }


async def _run(
    db: AsyncSession, storage: FakeS3Storage, ids: dict[str, str], artifacts: list[str]
) -> Any:
    store = JobStore(db)
    job_id = await store.enqueue(
        kind="paper_export",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
        library_item_id=ids["library_item_id"],
        payload={"artifacts": artifacts},
    )
    job = await store.claim(job_id)
    assert job is not None
    await run_export_paper_job({"s3": storage}, store, job)
    return await store.get(job_id)


ALL_ARTIFACTS = [
    "source_html",
    "translation_html",
    "bilingual_html",
    "article_html",
    "pdf_original",
    "pdf_translated",
    "pdf_bilingual",
]


async def test_zip_contains_all_selected_artifacts(db_session: AsyncSession) -> None:
    storage = FakeS3Storage()
    ids = await _seed(db_session, storage)
    job = await _run(db_session, storage, ids, ALL_ARTIFACTS)

    assert job is not None
    assert job.status == "succeeded", job.error
    download_url = job.result.get("download_url")
    assert isinstance(download_url, str) and download_url

    key = StorageKeys.export(ids["user_id"], str(job.id))
    archive = await storage.get(storage.assets_bucket, key)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        names = set(zf.namelist())

    base = ids["arxiv_id"]
    assert f"{base}-source.html" in names
    assert f"{base}-translation.html" in names
    assert f"{base}-bilingual.html" in names
    assert f"{base}-article.html" in names
    assert f"{base}-original.pdf" in names
    assert f"{base}-translated.pdf" in names
    assert f"{base}-bilingual.pdf" in names


async def test_original_pdf_has_block_annotations(db_session: AsyncSession) -> None:
    storage = FakeS3Storage()
    ids = await _seed(db_session, storage)
    job = await _run(db_session, storage, ids, ["pdf_original"])
    assert job is not None
    assert job.status == "succeeded", job.error

    # bbox なしの blk-3 注釈は skipped に数える(黙って落とさない)。
    assert job.result.get("skipped_annotations") == 1

    key = StorageKeys.export(ids["user_id"], str(job.id))
    archive = await storage.get(storage.assets_bucket, key)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        pdf_bytes = zf.read(f"{ids['arxiv_id']}-original.pdf")

    counts = _count_annotations(pdf_bytes)
    # blk-1 / blk-2 の 2 件がハイライト、blk-1 のコメント 1 件が text(popup)。
    assert counts.get("Highlight") == 2
    assert counts.get("Text") == 1


async def test_bilingual_pdf_interleaves_source_and_translated(db_session: AsyncSession) -> None:
    storage = FakeS3Storage()
    ids = await _seed(db_session, storage)
    job = await _run(db_session, storage, ids, ["pdf_bilingual"])
    assert job is not None
    assert job.status == "succeeded", job.error

    key = StorageKeys.export(ids["user_id"], str(job.id))
    archive = await storage.get(storage.assets_bucket, key)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        pdf_bytes = zf.read(f"{ids['arxiv_id']}-bilingual.pdf")

    texts = _read_pdf_page_texts(pdf_bytes)
    assert len(texts) == 4
    assert "SOURCE PAGE 1" in texts[0]
    assert "TRANSLATED PAGE 1" in texts[1]
    assert "SOURCE PAGE 2" in texts[2]
    assert "TRANSLATED PAGE 2" in texts[3]


async def test_html_artifacts_are_standalone(db_session: AsyncSession) -> None:
    storage = FakeS3Storage()
    ids = await _seed(db_session, storage)
    job = await _run(db_session, storage, ids, ["source_html", "bilingual_html", "article_html"])
    assert job is not None
    assert job.status == "succeeded", job.error

    key = StorageKeys.export(ids["user_id"], str(job.id))
    archive = await storage.get(storage.assets_bucket, key)
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        source_html = zf.read(f"{ids['arxiv_id']}-source.html").decode("utf-8")
        bilingual_html = zf.read(f"{ids['arxiv_id']}-bilingual.html").decode("utf-8")
        article_html = zf.read(f"{ids['arxiv_id']}-article.html").decode("utf-8")

    assert source_html.startswith("<!doctype html>")
    assert "Rectified flow is a method." in source_html
    # 対訳は原文と訳文の両方を含む。
    assert "It learns straight maps." in bilingual_html
    assert "直線写像を学習する。" in bilingual_html
    assert "はじめに" in article_html


async def test_fails_before_start_when_artifact_unavailable(db_session: AsyncSession) -> None:
    storage = FakeS3Storage()
    ids = await _seed(db_session, storage)

    # 訳文 PDF アセットを消し、pdf_translated を要求 → 生成前に失敗する。
    from sqlalchemy import delete

    await db_session.execute(
        delete(SourceAsset).where(SourceAsset.storage_key == ids["translated_key"])
    )
    await db_session.commit()

    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="paper_export",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
        library_item_id=ids["library_item_id"],
        payload={"artifacts": ["pdf_translated"]},
    )
    job = await store.claim(job_id)
    assert job is not None
    with pytest.raises(ArtifactUnavailableError):
        await run_export_paper_job({"s3": storage}, store, job)

    # 出力 zip は作られていない。
    key = StorageKeys.export(ids["user_id"], job_id)
    assert (storage.assets_bucket, key) not in storage.objects


async def test_rejects_foreign_library_item(db_session: AsyncSession) -> None:
    storage = FakeS3Storage()
    ids = await _seed(db_session, storage)

    other = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db_session.add(other)
    await db_session.commit()

    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="paper_export",
        user_id=str(other.id),
        paper_id=ids["paper_id"],
        library_item_id=ids["library_item_id"],
        payload={"artifacts": ["source_html"]},
    )
    job = await store.claim(job_id)
    assert job is not None
    with pytest.raises(ArtifactUnavailableError):
        await run_export_paper_job({"s3": storage}, store, job)


async def test_temp_workspace_is_cleaned_up(
    db_session: AsyncSession, tmp_path: Any, monkeypatch: Any
) -> None:
    """成功経路で作業用一時ディレクトリを残さない。"""
    import os
    import tempfile

    created: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def _tracking_mkdtemp(*args: Any, **kwargs: Any) -> str:
        kwargs["dir"] = str(tmp_path)
        path: str = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", _tracking_mkdtemp)

    storage = FakeS3Storage()
    ids = await _seed(db_session, storage)
    job = await _run(db_session, storage, ids, ["source_html", "pdf_original"])
    assert job is not None and job.status == "succeeded", job.error

    assert created, "expected the handler to create a temp workspace"
    for path in created:
        assert not os.path.exists(path), f"temp workspace left behind: {path}"


async def test_temp_workspace_cleaned_up_on_failure(
    db_session: AsyncSession, tmp_path: Any, monkeypatch: Any
) -> None:
    """生成中に失敗しても(可用性検証は通過)作業用一時ディレクトリを残さない。"""
    import os
    import tempfile

    created: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def _tracking_mkdtemp(*args: Any, **kwargs: Any) -> str:
        kwargs["dir"] = str(tmp_path)
        path: str = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", _tracking_mkdtemp)

    storage = FakeS3Storage()
    ids = await _seed(db_session, storage)
    # 原本 PDF のアセット行は残しつつ、実バイトを消す → 可用性検証は通過するが生成で失敗する。
    del storage.objects[(storage.sources_bucket, ids["original_key"])]

    store = JobStore(db_session)
    job_id = await store.enqueue(
        kind="paper_export",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
        library_item_id=ids["library_item_id"],
        payload={"artifacts": ["pdf_original"]},
    )
    job = await store.claim(job_id)
    assert job is not None
    with pytest.raises(Exception):  # noqa: B017 — 生成中の失敗自体を確認する
        await run_export_paper_job({"s3": storage}, store, job)

    assert created, "expected the handler to create a temp workspace before failing"
    for path in created:
        assert not os.path.exists(path), f"temp workspace left behind on failure: {path}"
