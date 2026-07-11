"""PDF アップロード取り込みの worker 配線(M1-22 (a)。plans/05 §6・§9・§12.3 前段)。

PY-ING-04 の worker 部: `POST /api/ingest/pdf` が積む `source='pdf_upload'` の ingest ジョブを
`ingest_paper` が品質 B(pdf_parser)で完走させる(queued→…→complete)。arXiv 系の
fetching(HTML/PDF 取得・レート制限)は一切経由しない(§9.2「ローカル資産の存在確認のみ」)。

フィクスチャは `packages/py-core/tests/fixtures/pdf_*.pdf`(pymupdf で自作した最小 PDF)を
そのまま再利用する(外部ネットワーク通信なし)。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import fitz
import pytest
from _summary_contract import assert_summary_lines_contract
from alinea_core.arxiv.fetch import FetchError
from alinea_core.db.models import DocumentRevision, LibraryItem, Paper, TranslationSet, User
from alinea_core.document.blocks import DocumentContent
from alinea_core.ingest import build_timeline
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_core.translation.pipeline import compute_translation_scope
from alinea_worker import pipeline as worker_pipeline
from alinea_worker.source_candidates import parse_pdf_candidate
from alinea_worker.tasks.ingest import ingest_paper
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "py-core" / "tests" / "fixtures"


def _load_pdf(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _single_paragraph_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(72, 72, 520, 300),
        "This is one deliberately long paragraph with enough extractable characters for "
        "the PDF parser but no second paragraph or heading.",
        fontsize=11,
    )
    data = bytes(doc.tobytes())
    doc.close()
    return data


class _RaisingStorage:
    sources_bucket = "sources"
    assets_bucket = "assets"

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def get(self, bucket: str, key: str) -> bytes:
        del bucket, key
        raise AssertionError("retained source reads must be bounded")

    async def get_bounded(self, bucket: str, key: str, *, max_bytes: int) -> bytes:
        del bucket, key, max_bytes
        raise self.error


def _client_error(code: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "provider-secret-detail"},
            "ResponseMetadata": {"HTTPStatusCode": 503},
        },
        "GetObject",
    )


async def _seed_pdf_ingest_job(db: AsyncSession, *, pdf_bytes: bytes) -> dict[str, str]:
    """private Paper + LibraryItem + `source='pdf_upload'` ingest ジョブを作る。

    `POST /api/ingest/pdf`(apps/api)が実際に行う最小セットアップを模す: 原本 PDF は
    S3 に先行 PUT 済み(worker は再取得しない。§9.2)。
    """
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()

    paper = Paper(
        id=str(uuid.uuid4()),
        title="無題の PDF",
        visibility="private",
        owner_user_id=user.id,
        pdf_sha256=uuid.uuid4().hex,
        license="unknown",
    )
    db.add(paper)
    await db.flush()

    li = LibraryItem(id=str(uuid.uuid4()), user_id=user.id, paper_id=paper.id, status="planned")
    db.add(li)
    await db.flush()
    await db.commit()

    storage = S3Storage()
    await storage.put(
        storage.sources_bucket,
        StorageKeys.original_pdf(str(paper.id), "v1"),
        pdf_bytes,
        content_type="application/pdf",
    )

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="ingest",
        payload={"mode": "initial", "source": "pdf_upload", "library_item_id": str(li.id)},
        priority="bulk",
        user_id=str(user.id),
        paper_id=str(paper.id),
        library_item_id=str(li.id),
    )
    return {
        "job_id": job_id,
        "paper_id": str(paper.id),
        "library_item_id": str(li.id),
        "user_id": str(user.id),
    }


async def _revision(db: AsyncSession, paper_id: str) -> DocumentRevision:
    return (
        (await db.execute(select(DocumentRevision).where(DocumentRevision.paper_id == paper_id)))
        .scalars()
        .one()
    )


async def _personal_set(db: AsyncSession, revision_id: str, user_id: str) -> TranslationSet:
    return (
        (
            await db.execute(
                select(TranslationSet).where(
                    TranslationSet.revision_id == revision_id,
                    TranslationSet.scope == "personal",
                    TranslationSet.user_id == user_id,
                )
            )
        )
        .scalars()
        .one()
    )


# ===========================================================================
# PY-ING-04(worker 部): pdf_upload が品質 B で queued→complete まで完走する
# ===========================================================================


async def test_pdf_upload_ingest_reaches_complete_quality_b(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized_sources: list[str] = []
    original_materialize = worker_pipeline._materialize_figure_payload

    async def counting_materialize(
        data: bytes,
        source_name: str,
        content_type: str | None = None,
        **kwargs: Any,
    ) -> Any:
        materialized_sources.append(source_name)
        return await original_materialize(data, source_name, content_type, **kwargs)

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", counting_materialize)
    pdf_bytes = _load_pdf("pdf_quality_b_sample.pdf")
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=pdf_bytes)
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)  # arq プール無し → 本文をその場駆動

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.stage == "complete"
    assert job.status == "succeeded"
    assert job.progress == 100

    rev = await _revision(db_session, ids["paper_id"])
    assert rev.quality_level == "B"
    assert rev.source_format == "pdf"
    assert rev.parser_version == "pdf-1.2.0"
    assert rev.source_version == "v1"
    source_key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    assert rev.stats["selected_source"] == {
        "storage_key": source_key,
        "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
    }
    assert rev.stats["revision_content_sha256"] == (
        worker_pipeline._canonical_content_sha256(rev.content)
    )
    parsed_candidate = parse_pdf_candidate(pdf_bytes, pdf_text="")
    assert rev.stats["parsed_content_sha256"] == worker_pipeline._canonical_content_sha256(
        parsed_candidate.content.model_dump()
    )
    content = DocumentContent.model_validate(rev.content)
    assert content.sections

    # 図(Figure 1)は S3 保存後に asset_key が確定し、そのままサムネイルに使われる。
    figures = [blk for _sec, blk in content.iter_blocks() if blk.type == "figure"]
    assert figures and all(f.asset_key for f in figures)
    assert len(materialized_sources) == 2
    assert len(set(materialized_sources)) == 2
    assert all(source.endswith(".png") for source in materialized_sources)
    manifest = rev.stats["figure_asset_manifest"]
    assert rev.stats["figure_materialization_version"] == (
        worker_pipeline.FIGURE_MATERIALIZATION_VERSION
    )
    assert len(manifest) == 2
    asset_blocks = {
        block.id: block
        for _section, block in content.iter_blocks()
        if block.id in {entry["block_id"] for entry in manifest}
    }
    assert {block.type for block in asset_blocks.values()} == {"equation", "figure"}
    storage = S3Storage()
    for entry in manifest:
        block = asset_blocks[entry["block_id"]]
        assert block.asset_key == entry["key"]
        stored = await storage.get(storage.assets_bucket, entry["key"])
        assert len(stored) == entry["byte_size"]
        assert hashlib.sha256(stored).hexdigest() == entry["sha256"]

    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert paper.thumbnail_key  # Figure 1 からサムネイル生成(§8)
    assert paper.abstract  # Abstract セクションから抽出(§6.5 固定見出し)
    assert paper.abstract_ja
    assert_summary_lines_contract(paper.summary_lines)

    # private 論文の翻訳セットは personal スコープ(plans/06 §9.2)。
    tset = await _personal_set(db_session, str(rev.id), ids["user_id"])
    assert tset.style == "natural"
    assert tset.status == "complete"

    scope = compute_translation_scope(content)
    assert scope.in_scope_block_ids  # 自動翻訳対象ブロックが存在する

    # タイムライン 1 段目は「PDF 取得(拡張から直接送信)」(§10.2)。
    timeline = build_timeline(job.log)
    assert len(timeline) == 3
    assert timeline[0]["label"] == "PDF 取得(拡張から直接送信)"
    assert "構造化" in timeline[1]["label"]
    assert "全文翻訳 完了" in timeline[2]["label"]


async def test_pdf_upload_uses_ocr_after_no_text_layer_and_persists_identity(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanned_pdf = _load_pdf("pdf_no_text_layer.pdf")
    readable_pdf = _load_pdf("pdf_quality_b_sample.pdf")
    original_parse_candidate = parse_pdf_candidate
    text_calls: list[bytes] = []
    ocr_calls: list[bytes] = []

    async def no_text_candidate(data: bytes, *, pdf_text: str, **_kwargs: Any) -> Any:
        del pdf_text
        text_calls.append(data)
        raise worker_pipeline.CandidateUnavailable(
            "pdf", "no_text_layer", "synthetic uploaded PDF has no text layer"
        )

    async def ocr_candidate(
        data: bytes,
        *,
        pdf_text: str,
        ocr_language: str = "eng",
        **_kwargs: Any,
    ) -> Any:
        ocr_calls.append(data)
        candidate = original_parse_candidate(readable_pdf, pdf_text=pdf_text)
        candidate.source_bytes = data
        candidate.parsed.stats["ocr"] = True
        candidate.diagnostics = [
            {
                "kind": "pdf_ocr",
                "version": worker_pipeline.PDF_OCR_CANDIDATE_VERSION,
                "language": ocr_language,
            }
        ]
        return candidate

    monkeypatch.setattr(worker_pipeline, "parse_pdf_candidate_async", no_text_candidate)
    monkeypatch.setattr(worker_pipeline, "parse_pdf_ocr_candidate", ocr_candidate)
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=scanned_pdf)
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None

    await ingest_paper(worker_ctx, store, job)

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "succeeded", completed.error
    assert text_calls == [scanned_pdf]
    assert ocr_calls == [scanned_pdf]
    identity = {
        "kind": "pdf_ocr",
        "version": worker_pipeline.PDF_OCR_CANDIDATE_VERSION,
        "language": "eng",
    }
    checkpoint = JobStore.get_checkpoint(completed)["parsing"]
    assert checkpoint["candidate_identity"] == identity
    revision = await _revision(db_session, ids["paper_id"])
    assert revision.stats["ocr"] is True
    assert revision.stats["candidate_identity"] == identity
    assert revision.stats["selected_source"]["sha256"] == hashlib.sha256(scanned_pdf).hexdigest()

    completed.status = "queued"
    completed.stage = "structuring"
    completed.error = None
    completed.finished_at = None
    await db_session.commit()
    resumed = await store.claim(ids["job_id"])
    assert resumed is not None
    await ingest_paper(worker_ctx, store, resumed)

    assert text_calls == [scanned_pdf]
    assert ocr_calls == [scanned_pdf, scanned_pdf]
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert [str(item.id) for item in revisions.all()] == [str(revision.id)]


@pytest.mark.parametrize("failure_code", ["pdf_timeout", "pdf_crashed", "pdf_lifecycle"])
async def test_normal_pdf_checkpoint_resume_preserves_retryable_parser_failure(
    failure_code: str,
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _seed_pdf_ingest_job(
        db_session,
        pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf"),
    )
    store = JobStore(db_session)
    first = await store.claim(ids["job_id"])
    assert first is not None
    await ingest_paper(worker_ctx, store, first)

    async def fail_resume(*_args: Any, **_kwargs: Any) -> Any:
        raise worker_pipeline.CandidateUnavailable(
            "pdf",
            failure_code,
            "synthetic resume parser failure",
        )

    monkeypatch.setattr(worker_pipeline, "parse_pdf_candidate_async", fail_resume)
    completed = await store.get(ids["job_id"])
    assert completed is not None
    completed.status = "queued"
    completed.stage = "structuring"
    completed.error = None
    completed.finished_at = None
    await db_session.commit()
    resumed = await store.claim(ids["job_id"])
    assert resumed is not None

    with pytest.raises(FetchError) as exc_info:
        await worker_pipeline.run_ingest(worker_ctx, store, resumed)

    assert exc_info.value.kind == failure_code


async def test_pdf_upload_rejects_candidate_when_extracted_asset_cannot_validate(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject_asset(*_args: Any, **_kwargs: Any) -> Any:
        raise worker_pipeline.FigureAssetError("image_invalid", "synthetic invalid image")

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", reject_asset)
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf"))
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "failed"
    error = json.loads(completed.error or "{}")
    assert error["code"] == "figure_asset_unresolved"
    diagnostics = json.loads(error["message"])
    assert diagnostics["candidates"][0]["figure_asset_failures"] == [
        {
            "code": "image_invalid",
            "figure_id": "blk-2-eq1-1e91",
            "source": "pdf",
        },
        {
            "code": "image_invalid",
            "figure_id": "blk-2-fig1-15cb",
            "source": "pdf",
        },
    ]
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []


@pytest.mark.parametrize(
    ("raised_code", "expected_code"),
    [
        ("conversion_crashed", "conversion_crashed"),
        ("conversion_lifecycle", "conversion_lifecycle"),
        ("conversion_timeout", "conversion_timeout"),
        ("materialization_timeout", "materialization_timeout"),
        (None, "figure_asset_error"),
    ],
    ids=[
        "conversion-crashed",
        "conversion-lifecycle",
        "conversion-timeout",
        "materialization-timeout",
        "generic-child-error",
    ],
)
async def test_pdf_upload_operational_figure_failure_is_left_for_job_retry(
    raised_code: str | None,
    expected_code: str,
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_materialization(*_args: Any, **_kwargs: Any) -> Any:
        if raised_code is None:
            raise RuntimeError("synthetic child worker failure")
        raise worker_pipeline.FigureAssetError(raised_code, "synthetic operational failure")

    monkeypatch.setattr(worker_pipeline, "_materialize_figure_payload", fail_materialization)
    ids = await _seed_pdf_ingest_job(
        db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf")
    )
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None

    with pytest.raises(FetchError) as error:
        await ingest_paper(worker_ctx, store, job)

    assert error.value.kind == expected_code
    persisted_job = await store.get(ids["job_id"])
    assert persisted_job is not None
    assert persisted_job.status != "failed"
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []


async def test_pdf_validated_cache_persistence_failure_rolls_back_revision(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LiveDeadline:
        def remaining(self, operation_limit_s: float | None = None) -> float:
            return 30.0 if operation_limit_s is None else min(30.0, operation_limit_s)

    class ExpireOnSecondAsset:
        def __init__(self) -> None:
            self.calls = 0

        def remaining(self, operation_limit_s: float | None = None) -> float:
            self.calls += 1
            if self.calls == 2:
                raise worker_pipeline.FigureAssetError(
                    "materialization_timeout", "synthetic PDF persistence deadline expired"
                )
            return 30.0 if operation_limit_s is None else min(30.0, operation_limit_s)

    starts = 0

    def start_deadline(
        _cls: type[worker_pipeline.MaterializationDeadline],
        **_kwargs: Any,
    ) -> Any:
        nonlocal starts
        starts += 1
        return LiveDeadline() if starts == 1 else ExpireOnSecondAsset()

    monkeypatch.setattr(
        worker_pipeline.MaterializationDeadline,
        "start",
        classmethod(start_deadline),
    )
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf"))
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    with pytest.raises(worker_pipeline.FigureAssetError, match="deadline expired"):
        await worker_pipeline.run_ingest(worker_ctx, store, job)

    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []


@pytest.mark.parametrize(
    "commit_error",
    [ConnectionError("commit acknowledgement lost"), asyncio.CancelledError("commit cancelled")],
    ids=["connection-lost-after-commit", "cancelled-after-commit"],
)
async def test_ambiguous_commit_preserves_committed_revision_assets_and_retry_succeeds(
    commit_error: BaseException,
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _seed_pdf_ingest_job(
        db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf")
    )
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False, class_=AsyncSession)
    ctx = {**worker_ctx, "sessionmaker": maker}
    store = JobStore(db_session)
    original_commit = db_session.commit
    injected = False

    async def commit_then_lose_acknowledgement() -> None:
        nonlocal injected
        ready_revision = next(
            (
                value
                for value in db_session.identity_map.values()
                if isinstance(value, DocumentRevision)
                and "revision_content_sha256" in (value.stats or {})
            ),
            None,
        )
        await original_commit()
        if ready_revision is not None and not injected:
            injected = True
            raise commit_error

    monkeypatch.setattr(db_session, "commit", commit_then_lose_acknowledgement)
    job = await store.claim(ids["job_id"])
    assert job is not None

    attempt = asyncio.create_task(worker_pipeline.run_ingest(ctx, store, job))
    with pytest.raises(type(commit_error)) as caught:
        await attempt
    assert caught.value is commit_error
    assert injected is True

    db_session.expire_all()
    revision = await _revision(db_session, ids["paper_id"])
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None and paper.thumbnail_key is not None
    committed_keys = [entry["key"] for entry in revision.stats["figure_asset_manifest"]]
    committed_keys.extend(
        [
            paper.thumbnail_key,
            StorageKeys.thumbnail_retina_sibling(
                paper.thumbnail_key, paper_id=ids["paper_id"]
            ),
        ]
    )
    assert all(isinstance(key, str) for key in committed_keys)
    storage = S3Storage()
    for key in committed_keys:
        assert isinstance(key, str)
        assert await storage.get(storage.assets_bucket, key)

    retry_job = await store.get(ids["job_id"])
    assert retry_job is not None
    retry_job.status = "queued"
    retry_job.error = None
    retry_job.finished_at = None
    await db_session.commit()
    claimed_retry = await store.claim(ids["job_id"])
    assert claimed_retry is not None
    await ingest_paper(ctx, store, claimed_retry)

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "succeeded", completed.error
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert [str(item.id) for item in revisions.all()] == [str(revision.id)]


async def test_pdf_upload_existing_revision_repairs_missing_manifest_asset(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf"))
    store = JobStore(db_session)
    first_job = await store.claim(ids["job_id"])
    assert first_job is not None
    await ingest_paper(worker_ctx, store, first_job)
    revision = await _revision(db_session, ids["paper_id"])
    manifest = revision.stats["figure_asset_manifest"]
    assert manifest
    missing = manifest[0]
    storage = S3Storage()
    await storage.delete_many(storage.assets_bucket, [missing["key"]])

    reingest_job_id = await store.enqueue(
        kind="ingest",
        payload={
            "mode": "reingest",
            "source": "pdf_upload",
            "library_item_id": ids["library_item_id"],
        },
        priority="bulk",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
        library_item_id=ids["library_item_id"],
    )
    reingest_job = await store.claim(reingest_job_id)
    assert reingest_job is not None
    await ingest_paper(worker_ctx, store, reingest_job)

    completed = await store.get(reingest_job_id)
    assert completed is not None
    assert completed.status == "succeeded", completed.error
    repaired = await storage.get(storage.assets_bucket, missing["key"])
    assert len(repaired) == missing["byte_size"]
    assert hashlib.sha256(repaired).hexdigest() == missing["sha256"]

    await storage.put(
        storage.assets_bucket,
        missing["key"],
        b"corrupt",
        content_type="application/octet-stream",
    )
    corrupt_repair_job_id = await store.enqueue(
        kind="ingest",
        payload={
            "mode": "reingest",
            "source": "pdf_upload",
            "library_item_id": ids["library_item_id"],
        },
        priority="bulk",
        user_id=ids["user_id"],
        paper_id=ids["paper_id"],
        library_item_id=ids["library_item_id"],
    )
    corrupt_repair_job = await store.claim(corrupt_repair_job_id)
    assert corrupt_repair_job is not None
    await ingest_paper(worker_ctx, store, corrupt_repair_job)
    repaired_corruption = await storage.get(storage.assets_bucket, missing["key"])
    assert hashlib.sha256(repaired_corruption).hexdigest() == missing["sha256"]


async def test_pdf_upload_resume_backfills_diagnostics_from_retained_pdf(
    db_session: AsyncSession, worker_ctx: dict[str, Any]
) -> None:
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf"))
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    revision = await _revision(db_session, ids["paper_id"])
    revision.stats = {
        key: value
        for key, value in revision.stats.items()
        if key not in {"candidate_failures", "completeness"}
    }
    job = await store.get(ids["job_id"])
    assert job is not None
    payload = json.loads(json.dumps(job.payload))
    parsing_checkpoint = payload["_checkpoint"]["parsing"]
    parsing_checkpoint.pop("candidate_identity", None)
    parsing_checkpoint.pop("candidate_diagnostics", None)
    job.payload = payload
    job.status = "queued"
    job.finished_at = None
    await db_session.commit()

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "succeeded"
    await db_session.refresh(revision)
    assert revision.stats["completeness"]["accepted"] is True
    assert revision.stats["candidate_failures"] == []


async def test_pdf_upload_stale_parser_checkpoint_reparses_with_current_version(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    ids = await _seed_pdf_ingest_job(
        db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf")
    )
    store = JobStore(db_session)
    first_job = await store.claim(ids["job_id"])
    assert first_job is not None
    await ingest_paper(worker_ctx, store, first_job)

    legacy = await _revision(db_session, ids["paper_id"])
    legacy_id = str(legacy.id)
    legacy.parser_version = "pdf-1.0.0"
    job = await store.get(ids["job_id"])
    assert job is not None
    payload = json.loads(json.dumps(job.payload))
    checkpoints = payload["_checkpoint"]
    checkpoints.pop("structuring", None)
    checkpoints["parsing"]["parser_version"] = "pdf-1.0.0"
    job.payload = payload
    job.status = "queued"
    job.stage = "parsing"
    job.error = None
    job.finished_at = None
    await db_session.commit()

    resumed = await store.claim(ids["job_id"])
    assert resumed is not None
    await ingest_paper(worker_ctx, store, resumed)

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "succeeded", completed.error
    revisions = (
        (
            await db_session.execute(
                select(DocumentRevision)
                .where(DocumentRevision.paper_id == ids["paper_id"])
                .order_by(DocumentRevision.created_at, DocumentRevision.id)
            )
        )
        .scalars()
        .all()
    )
    assert [str(item.id) for item in revisions if item.parser_version == "pdf-1.0.0"] == [
        legacy_id
    ]
    current = [item for item in revisions if item.parser_version == "pdf-1.2.0"]
    assert len(current) == 1
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert str(paper.latest_revision_id) == str(current[0].id)


@pytest.mark.parametrize("resume", [False, True], ids=["initial", "fetching-checkpoint"])
@pytest.mark.parametrize("error_code", ["SlowDown", "NoSuchBucket"])
async def test_pdf_upload_client_error_is_sanitized_retryable_storage_error(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    resume: bool,
    error_code: str,
) -> None:
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf"))
    store = JobStore(db_session)
    if resume:
        await store.checkpoint(
            ids["job_id"],
            "fetching",
            {"source_version": "v1", "source_format": "pdf_upload"},
            progress=10,
        )
    ctx = {**worker_ctx, "s3": _RaisingStorage(_client_error(error_code))}

    job = await store.claim(ids["job_id"])
    assert job is not None
    with pytest.raises(FetchError) as error:
        await ingest_paper(ctx, store, job)

    assert error.value.kind == "storage_error"
    assert error_code not in str(error.value)
    assert "provider-secret-detail" not in str(error.value)
    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "running"
    assert job.error is None


async def test_pdf_upload_unexpected_storage_exception_is_sanitized_retryable(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf"))
    store = JobStore(db_session)
    ctx = {**worker_ctx, "s3": _RaisingStorage(RuntimeError("backend-token-detail"))}

    job = await store.claim(ids["job_id"])
    assert job is not None
    with pytest.raises(FetchError) as error:
        await ingest_paper(ctx, store, job)

    assert error.value.kind == "storage_error"
    assert "backend-token-detail" not in str(error.value)
    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "running"


async def test_pdf_upload_missing_object_remains_sanitized_source_not_found(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf"))
    store = JobStore(db_session)
    ctx = {**worker_ctx, "s3": _RaisingStorage(_client_error("NoSuchKey"))}

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "failed"
    error = json.loads(job.error or "{}")
    assert error["code"] == "source_not_found"
    assert "NoSuchKey" not in error["message"]
    assert "provider-secret-detail" not in error["message"]


async def test_pdf_upload_retained_source_uses_bounded_s3_read_and_fails_terminally(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = _load_pdf("pdf_quality_b_sample.pdf")
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=pdf)
    monkeypatch.setattr(worker_pipeline, "MAX_ARXIV_PDF_BYTES", len(pdf) - 1)
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None

    await ingest_paper(worker_ctx, store, job)

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "failed"
    assert json.loads(completed.error or "{}")["code"] == "source_too_large"
    revisions = (
        await db_session.execute(
            select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
        )
    ).scalars()
    assert revisions.all() == []


async def test_pdf_upload_non_object_fetching_checkpoint_fails_with_parse_error(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=_load_pdf("pdf_quality_b_sample.pdf"))
    store = JobStore(db_session)
    job = await store.get(ids["job_id"])
    assert job is not None
    job.payload = {**job.payload, "_checkpoint": {"fetching": "not-an-object"}}
    await db_session.commit()

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "failed"
    assert json.loads(job.error or "{}")["code"] == "parse_error"


async def test_pdf_upload_parsing_checkpoint_rejects_changed_source_bytes(
    db_session: AsyncSession, worker_ctx: dict[str, Any]
) -> None:
    original = _load_pdf("pdf_quality_b_sample.pdf")
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=original)
    key = StorageKeys.original_pdf(ids["paper_id"], "v1")
    store = JobStore(db_session)
    await store.checkpoint(
        ids["job_id"],
        "fetching",
        {"source_version": "v1", "source_format": "pdf_upload"},
        progress=10,
    )
    await store.checkpoint(
        ids["job_id"],
        "parsing",
        {
            "source_format": "pdf_upload",
            "parser_version": "pdf-1.2.0",
            "candidate_failures": [],
            "completeness": {"accepted": True},
            "adopt_from_revision_id": None,
            "source_storage_key": key,
            "source_sha256": hashlib.sha256(original).hexdigest(),
        },
        progress=20,
    )
    storage = S3Storage()
    await storage.put(
        storage.sources_bucket,
        key,
        _load_pdf("pdf_table_sample.pdf"),
        content_type="application/pdf",
    )

    job = await store.claim(ids["job_id"])
    assert job is not None
    with pytest.raises(FetchError) as error:
        await ingest_paper(worker_ctx, store, job)

    assert error.value.kind == "storage_error"
    revisions = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert revisions == []


async def test_corrupt_pdf_upload_preserves_stable_open_error(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=b"%PDF- corrupt")
    store = JobStore(db_session)
    job = await store.claim(ids["job_id"])
    assert job is not None

    await ingest_paper(worker_ctx, store, job)

    completed = await store.get(ids["job_id"])
    assert completed is not None
    assert completed.status == "failed"
    assert json.loads(completed.error or "{}")["code"] == "pdf_open_error"


# ===========================================================================
# テキストレイヤ無し PDF: 段階名(parsing)+理由+再試行なしで failed(§2.4・§6.1)
# ===========================================================================


async def test_pdf_upload_ingest_reports_unavailable_ocr_for_no_text_layer(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable_ocr(*_args: Any, **_kwargs: Any) -> Any:
        raise worker_pipeline.CandidateUnavailable(
            "pdf_ocr",
            "ocr_engine_unavailable",
            "PDF OCR engine is unavailable",
        )

    monkeypatch.setattr(worker_pipeline, "parse_pdf_ocr_candidate", unavailable_ocr)
    pdf_bytes = _load_pdf("pdf_no_text_layer.pdf")
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=pdf_bytes)
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "failed"
    assert job.stage == "parsing"
    error = json.loads(job.error or "{}")
    assert error["code"] == "ocr_engine_unavailable"
    assert "ocr_engine_unavailable" in error["message"]

    # structuring には到達しない(DocumentRevision が作られない)。
    revs = (
        (
            await db_session.execute(
                select(DocumentRevision.id).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert revs == []


async def test_pdf_upload_incomplete_document_fails_before_revision_promotion(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def still_incomplete(*_args: Any, **_kwargs: Any) -> Any:
        raise worker_pipeline.CandidateUnavailable(
            "pdf_ocr",
            "no_text_layer",
            "OCR did not recover enough visible text",
        )

    monkeypatch.setattr(worker_pipeline, "parse_pdf_ocr_candidate", still_incomplete)
    ids = await _seed_pdf_ingest_job(db_session, pdf_bytes=_single_paragraph_pdf())
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.status == "failed"
    assert json.loads(job.error or "{}")["code"] == "document_incomplete"
    paper = await db_session.get(Paper, ids["paper_id"])
    assert paper is not None
    assert paper.latest_revision_id is None
    revisions = (
        (
            await db_session.execute(
                select(DocumentRevision).where(DocumentRevision.paper_id == ids["paper_id"])
            )
        )
        .scalars()
        .all()
    )
    assert revisions == []
