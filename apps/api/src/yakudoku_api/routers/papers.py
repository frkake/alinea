"""papers ルータ — 論文実体(plans/03 §4)。

- ``POST /api/papers/{paper_id}/reingest``    再取り込み(202・実行中は 409 conflict)。
- ``GET  /api/papers/{paper_id}/ingest-log``  処理ログ(at 昇順・ページングなし)。
- ``GET  /api/papers/{paper_id}/pdf``         原本 PDF(302 署名付き URL、10 分)。

共有の依存(``S3Storage`` / 所有チェック)を提供し、assets ルータからも import する。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from yakudoku_core.db.models import Job, LibraryItem, Paper, SourceAsset
from yakudoku_core.ingest.joblog import project_ingest_log
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.storage.s3 import S3Storage

from yakudoku_api.deps import CurrentUser, DbDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.routers.ingest import JobWakeupDep, _active_ingest_job
from yakudoku_api.schemas.papers import (
    PapersIngestLogEntry,
    PapersIngestLogResponse,
    PapersReingestResponse,
)

router = APIRouter(tags=["papers"])

# 原本 PDF とみなす source_assets.kind(§4.4)。
_PDF_KINDS = ("pdf", "arxiv_pdf", "pdf_upload")


def get_storage() -> S3Storage:
    return S3Storage()


StorageDep = Annotated[S3Storage, Depends(get_storage)]


async def assert_paper_access(db: DbDep, paper: Paper, user_id: str) -> None:
    """論文アクセス権を検証する。無ければ 404(存在自体を隠す。§4.1)。

    - private: 所有者のみ。
    - public: 所有者、または当該論文の LibraryItem を持つユーザーのみ(plans/01 §7.3)。
    """
    if paper.visibility == "private":
        if str(paper.owner_user_id) != user_id:
            raise ProblemException("not_found")
        return
    if str(paper.owner_user_id or "") == user_id:
        return
    has = (
        await db.execute(
            select(LibraryItem.id)
            .where(LibraryItem.user_id == user_id, LibraryItem.paper_id == str(paper.id))
            .limit(1)
        )
    ).first()
    if has is None:
        raise ProblemException("not_found")


# --- POST /api/papers/{paper_id}/reingest ------------------------------------------


@router.post(
    "/api/papers/{paper_id}/reingest",
    response_model=PapersReingestResponse,
    status_code=202,
    operation_id="papers_reingest",
)
async def reingest(
    paper_id: str, user: CurrentUser, db: DbDep, wakeup: JobWakeupDep
) -> PapersReingestResponse:
    paper = await db.get(Paper, paper_id)
    if paper is None:
        raise ProblemException("not_found")
    await assert_paper_access(db, paper, str(user.id))

    if await _active_ingest_job(db, paper_id) is not None:
        raise ProblemException("conflict", detail="同一 Paper の取り込みが実行中です")

    item = (
        (
            await db.execute(
                select(LibraryItem).where(
                    LibraryItem.user_id == str(user.id), LibraryItem.paper_id == paper_id
                )
            )
        )
        .scalars()
        .first()
    )
    library_item_id = str(item.id) if item is not None else None

    store = JobStore(db)
    try:
        job_id = await store.enqueue(
            kind="ingest",
            payload={
                "mode": "reingest",
                "source": "arxiv",
                "arxiv_id": paper.arxiv_id,
                "url": None,
                "library_item_id": library_item_id,
            },
            priority="bulk",
            user_id=str(user.id),
            paper_id=paper_id,
            library_item_id=library_item_id,
        )
    except IntegrityError:
        # uq_jobs_ingest_active: 競合で稼働中 ingest が挿入済み → 409。
        await db.rollback()
        raise ProblemException("conflict", detail="同一 Paper の取り込みが実行中です") from None

    await wakeup(job_id)
    return PapersReingestResponse(job_id=job_id)


# --- GET /api/papers/{paper_id}/ingest-log -----------------------------------------


@router.get(
    "/api/papers/{paper_id}/ingest-log",
    response_model=PapersIngestLogResponse,
    operation_id="papers_ingest_log",
)
async def ingest_log(paper_id: str, user: CurrentUser, db: DbDep) -> PapersIngestLogResponse:
    paper = await db.get(Paper, paper_id)
    if paper is None:
        raise ProblemException("not_found")
    await assert_paper_access(db, paper, str(user.id))

    jobs = (
        (
            await db.execute(
                select(Job)
                .where(Job.kind == "ingest", Job.paper_id == paper_id)
                .order_by(Job.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    rows: list[object] = []
    for job in jobs:
        rows.extend(job.log or [])
    projected = project_ingest_log(rows)
    projected.sort(key=lambda r: str(r.get("at") or ""))
    entries = [PapersIngestLogEntry.model_validate(r) for r in projected]
    return PapersIngestLogResponse(entries=entries)


# --- GET /api/papers/{paper_id}/pdf ------------------------------------------------


@router.get("/api/papers/{paper_id}/pdf", operation_id="papers_pdf")
async def paper_pdf(
    paper_id: str, user: CurrentUser, db: DbDep, storage: StorageDep
) -> RedirectResponse:
    paper = await db.get(Paper, paper_id)
    if paper is None:
        raise ProblemException("not_found")
    await assert_paper_access(db, paper, str(user.id))

    asset = (
        (
            await db.execute(
                select(SourceAsset)
                .where(SourceAsset.paper_id == paper_id, SourceAsset.kind.in_(_PDF_KINDS))
                .order_by(SourceAsset.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if asset is None:
        raise ProblemException("not_found")

    url = await storage.presign_get(storage.sources_bucket, asset.storage_key, expires_in=600)
    return RedirectResponse(url, status_code=302)
