# mypy: disable-error-code="attr-defined"
"""Site (ACL Anthology) ingest の worker 配線(Task 15。plans/05 §6・§9.2 相当)。

`POST /api/ingest/site`(apps/api)が積む `source='site'` の ingest ジョブを `ingest_paper`
が品質 B(pdf_parser)で完走させる(queued→…→complete)。arXiv 系の fetching(HTML/PDF
取得・レート制限)は一切経由しない: サイト取り込みは API が landing→PDF を取得して S3 へ
先行保存済みで、worker は pdf_upload と同じくローカル資産の存在確認だけで構造化する。

フィクスチャは既存の最小 PDF(`packages/py-core/tests/fixtures/pdf_quality_b_sample.pdf`)を
そのまま再利用する(外部ネットワーク通信なし)。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from alinea_core.db.models import DocumentRevision, LibraryItem, Paper, User
from alinea_core.jobs.store import JobStore
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_worker.tasks.ingest import ingest_paper
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_FIXTURES = Path(__file__).resolve().parents[3] / "packages" / "py-core" / "tests" / "fixtures"


def _load_pdf(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


async def _seed_site_ingest_job(db: AsyncSession, *, pdf_bytes: bytes) -> dict[str, str]:
    """private Paper + LibraryItem + `source='site'` ingest ジョブを作る。

    `POST /api/ingest/site`(apps/api)が実際に行う最小セットアップを模す: landing→PDF は
    API が取得済みで、原本 PDF は S3 に先行 PUT 済み(worker は再取得しない)。
    """
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()

    paper = Paper(
        id=str(uuid.uuid4()),
        title="ACL paper 2023.acl-long.42",
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
        payload={
            "mode": "initial",
            "source": "site",
            "site": "acl_anthology",
            "external_id": "2023.acl-long.42",
            "landing_url": "https://aclanthology.org/2023.acl-long.42/",
            "library_item_id": str(li.id),
        },
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


async def test_site_ingest_reaches_complete_quality_b(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    pdf_bytes = _load_pdf("pdf_quality_b_sample.pdf")
    ids = await _seed_site_ingest_job(db_session, pdf_bytes=pdf_bytes)
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
    assert rev.source_version == "v1"
