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


# --------------------------------------------------------------------------- #
# OpenReview site ingest pipeline (Task 16)
# --------------------------------------------------------------------------- #


async def _seed_openreview_ingest_job(
    db: AsyncSession,
    *,
    pdf_bytes: bytes,
    external_id: str = "abc123XYZ",
) -> dict[str, str]:
    """OpenReview Paper + LibraryItem + `source='site'` ingest ジョブを作る。

    `POST /api/ingest/site`(apps/api)が実際に行う最小セットアップを模す。
    """
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()

    paper = Paper(
        id=str(uuid.uuid4()),
        title=f"OpenReview paper {external_id}",
        visibility="private",
        owner_user_id=user.id,
        pdf_sha256=uuid.uuid4().hex,
        license="cc-by-4.0",
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
            "site": "openreview",
            "external_id": external_id,
            "landing_url": f"https://openreview.net/forum?id={external_id}",
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


async def test_openreview_site_ingest_reaches_complete_quality_b(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    """OpenReview の `source='site'` ジョブが品質 B で complete まで完走する。"""
    pdf_bytes = _load_pdf("pdf_quality_b_sample.pdf")
    ids = await _seed_openreview_ingest_job(db_session, pdf_bytes=pdf_bytes)
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.stage == "complete"
    assert job.status == "succeeded"
    assert job.progress == 100

    rev = await _revision(db_session, ids["paper_id"])
    assert rev.quality_level == "B"
    assert rev.source_format == "pdf"
    assert rev.source_version == "v1"


# --------------------------------------------------------------------------- #
# PMC JATS 品質 A 取り込み(Task 17)
# --------------------------------------------------------------------------- #

_JATS_FIXTURE = _FIXTURES / "pmc_article.xml"


async def _seed_pmc_jats_ingest_job(db: AsyncSession, *, jats_bytes: bytes) -> dict[str, str]:
    """PMC の `source='site'` + `source_format='jats'` ジョブを作る。

    API(POST /api/ingest/site の PMC 経路)が行う最小セットアップを模す: NCBI から取得済みの
    JATS XML を S3 に先行 PUT 済みで、worker は再取得せずローカル資産を構造化する。
    """
    # 本文 fixture の DOI は固定なので、共有 DB での再実行時に uq_papers_doi と衝突しないよう
    # 先行実行が残した同 DOI の Paper を掃除する(本番は API が DOI で冪等化して衝突を避ける)。
    from sqlalchemy import delete as _sa_delete

    await db.execute(_sa_delete(Paper).where(Paper.doi == "10.1234/jdt.2019.42"))
    await db.commit()

    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex}@t.test")
    db.add(user)
    await db.flush()

    paper = Paper(
        id=str(uuid.uuid4()),
        title="PMC article PMC6543210",
        visibility="private",
        owner_user_id=user.id,
        license="cc-by-4.0",
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
        StorageKeys.jats_xml(str(paper.id), "v1"),
        jats_bytes,
        content_type="application/xml",
    )

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="ingest",
        payload={
            "mode": "initial",
            "source": "site",
            "site": "pmc",
            "source_format": "jats",
            "external_id": "PMC6543210",
            "landing_url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6543210/",
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


async def test_site_ingest_pmc_jats_reaches_complete_quality_a(
    db_session: AsyncSession,
    worker_ctx: dict[str, Any],
) -> None:
    jats_bytes = _JATS_FIXTURE.read_bytes()
    ids = await _seed_pmc_jats_ingest_job(db_session, jats_bytes=jats_bytes)
    store = JobStore(db_session)

    job = await store.claim(ids["job_id"])
    assert job is not None
    await ingest_paper(worker_ctx, store, job)

    job = await store.get(ids["job_id"])
    assert job is not None
    assert job.stage == "complete"
    assert job.status == "succeeded"
    assert job.progress == 100

    rev = await _revision(db_session, ids["paper_id"])
    assert rev.quality_level == "A"
    assert rev.source_format == "jats"
    assert rev.source_version == "v1"

    # 図ブロックは content に残り(黙って消えない)、実体未取得は deferred placeholder として
    # stats.figure_asset_failures に記録される(Step 5: 失敗時は deferred placeholder)。
    from alinea_core.document.blocks import DocumentContent

    content = DocumentContent.model_validate(rev.content)
    figure_blocks = [b for _sec, b in content.iter_blocks() if b.type == "figure"]
    assert figure_blocks, "JATS figure block must survive structuring"
    deferred = [
        f
        for f in (rev.stats.get("figure_asset_failures") or [])
        if f.get("code") == "figure_deferred"
    ]
    assert deferred, "JATS figure must be recorded as a deferred placeholder, not silently dropped"
    assert all(b.asset_key is None for b in figure_blocks)
