"""PDF アップロード取り込みの worker 配線(M1-22 (a)。plans/05 §6・§9・§12.3 前段)。

PY-ING-04 の worker 部: `POST /api/ingest/pdf` が積む `source='pdf_upload'` の ingest ジョブを
`ingest_paper` が品質 B(pdf_parser)で完走させる(queued→…→complete)。arXiv 系の
fetching(HTML/PDF 取得・レート制限)は一切経由しない(§9.2「ローカル資産の存在確認のみ」)。

フィクスチャは `packages/py-core/tests/fixtures/pdf_*.pdf`(pymupdf で自作した最小 PDF)を
そのまま再利用する(外部ネットワーク通信なし)。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from _summary_contract import assert_summary_lines_contract
from alinea_core.db.models import DocumentRevision, LibraryItem, Paper, TranslationSet, User
from alinea_core.document.blocks import DocumentContent
from alinea_core.ingest import build_timeline
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_core.translation.pipeline import compute_translation_scope
from alinea_worker.tasks.ingest import ingest_paper
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "py-core" / "tests" / "fixtures"


def _load_pdf(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


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
    db_session: AsyncSession, worker_ctx: dict[str, Any]
) -> None:
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
    assert rev.parser_version == "pdf-1.0.0"
    assert rev.source_version == "v1"
    content = DocumentContent.model_validate(rev.content)
    assert content.sections

    # 図(Figure 1)は S3 保存後に asset_key が確定し、そのままサムネイルに使われる。
    figures = [blk for _sec, blk in content.iter_blocks() if blk.type == "figure"]
    assert figures and all(f.asset_key for f in figures)

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


# ===========================================================================
# テキストレイヤ無し PDF: 段階名(parsing)+理由+再試行なしで failed(§2.4・§6.1)
# ===========================================================================


async def test_pdf_upload_ingest_no_text_layer_fails_parsing(
    db_session: AsyncSession, worker_ctx: dict[str, Any]
) -> None:
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
    assert error["code"] == "no_text_layer"
    assert "テキストが抽出できません" in error["message"]

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
