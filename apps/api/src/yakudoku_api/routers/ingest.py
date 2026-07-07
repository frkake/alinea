"""ingest ルータ — 取り込み(拡張専用経路。plans/03 §3)。

- ``GET  /api/ingest/check``  取り込み前の状態判定(新規 / 既存 / 非対応)+ LaTeX 有無。
- ``POST /api/ingest/arxiv``  取り込み開始(202 + Idempotency-Key・重複は 409 duplicate)。
- ``POST /api/ingest/pdf``    拡張からの PDF 直接送信(202・private・50MB/415/テキストレイヤ無し)。
- ``GET  /api/ingest/recent`` 直近の取り込み(拡張フッタ)。

外部 arXiv 呼び出しは :class:`ArxivGateway`(DI)経由。ジョブは PostgreSQL ``jobs`` が真実で、
arq へは起床通知(``run_job``)を best-effort で投げる(plans/01 §4)。両者ともテストは
``app.dependency_overrides`` で差し替える。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from yakudoku_core.arxiv.fetch import FetchError, probe_latex_available
from yakudoku_core.arxiv.ids import ArxivId, parse_arxiv_url
from yakudoku_core.arxiv.metadata import ArxivMeta, fetch_metadata
from yakudoku_core.db.models import (
    BlockSearchIndex,
    Collection,
    CollectionEntry,
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    SourceAsset,
)
from yakudoku_core.ingest import joblog
from yakudoku_core.ingest.dedupe import detect_duplicate
from yakudoku_core.jobs.store import JobStore
from yakudoku_core.parsing.pdf_parser import PdfParseError, check_text_layer
from yakudoku_core.storage.s3 import S3Storage, StorageKeys

from yakudoku_api.deps import CurrentUserOrExt, DbDep, SettingsDep
from yakudoku_api.errors import PROBLEM_CONTENT_TYPE, ProblemError, ProblemException, build_problem
from yakudoku_api.schemas.ingest import (
    IngestArxivRequest,
    IngestArxivResponse,
    IngestCheckBib,
    IngestCheckResponse,
    IngestCheckSaved,
    IngestLastPosition,
    IngestPdfMeta,
    IngestPipelineState,
    IngestRecentItem,
    IngestRecentResponse,
    authors_short,
    build_pipeline_state,
)

router = APIRouter(tags=["ingest"])
log = structlog.get_logger("yakudoku.api.ingest")

# plans/01 §4.3 のキュー名(識別子)。apps/worker への import は禁止のため定数で持つ。
BULK_QUEUE = "yk:bulk"

# 取り込み時に受け付ける Status(§1.6)。拡張 UI は planned|up_next|reading の 3 択(§3.2)。
_VALID_STATUSES = frozenset({"planned", "up_next", "reading", "done", "reread", "on_hold"})
_ACTIVE_JOB_STATUSES = ("queued", "running", "waiting_quota")


# --- 依存(テストで差し替え可能) ---------------------------------------------------


class ArxivGateway:
    """arXiv メタデータ・LaTeX 判定の薄いラッパ(``yakudoku_core.arxiv`` 経由)。"""

    async def fetch_metadata(self, ref: ArxivId) -> ArxivMeta:
        return await fetch_metadata(ref)

    async def probe_latex_available(self, ref: ArxivId) -> bool:
        return await probe_latex_available(ref)


def get_arxiv_gateway() -> ArxivGateway:
    return ArxivGateway()


ArxivGatewayDep = Annotated[ArxivGateway, Depends(get_arxiv_gateway)]

JobWakeup = Callable[[str], Awaitable[None]]


async def _default_wakeup(redis_url: str, job_id: str) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        await pool.enqueue_job("run_job", job_id, _queue_name=BULK_QUEUE)
    finally:
        await pool.aclose()


def get_job_wakeup(settings: SettingsDep) -> JobWakeup:
    """arq への起床通知を返す。失敗しても取り込み要求は成功させる(DB が真実)。"""

    async def wakeup(job_id: str) -> None:
        try:
            await _default_wakeup(settings.redis_url, job_id)
        except Exception:
            log.warning("ingest_wakeup_failed", job_id=job_id)

    return wakeup


JobWakeupDep = Annotated[JobWakeup, Depends(get_job_wakeup)]


# --- GET /api/ingest/check ----------------------------------------------------------


@router.get("/api/ingest/check", response_model=IngestCheckResponse, operation_id="ingest_check")
async def ingest_check(
    user: CurrentUserOrExt,
    db: DbDep,
    gateway: ArxivGatewayDep,
    url: str = Query(..., description="現在タブの URL"),
) -> IngestCheckResponse:
    ref = parse_arxiv_url(url)
    if ref is None:
        # 一般ページ PDF(状態4。3a §6.5・plans/10 §11.2・lib/popup-state.ts の kind==="pdf"
        # 分岐が唯一の消費者)。拡張ポップアップはこの kind を見て GenericPdf 状態へ遷移する。
        clean_url = url.split("?", 1)[0].split("#", 1)[0]
        if clean_url.lower().endswith(".pdf"):
            return IngestCheckResponse(kind="pdf")
        # 到達不能・非対応 URL はエラーにせず unsupported を返す(§3.1)。
        return IngestCheckResponse(kind="unsupported")

    arxiv_id = ref.id
    arxiv_version = ref.version_suffix or None
    existing = await detect_duplicate(db, arxiv_id, user_id=str(user.id))
    if existing is not None:
        bib, latex, tags = await _saved_preview(db, existing)
        return IngestCheckResponse(
            kind="arxiv",
            arxiv_id=arxiv_id,
            arxiv_version=arxiv_version,
            bib=bib,
            latex_available=latex,
            suggested_tags=tags,
            saved=await _build_saved(db, existing),
        )

    bib, latex, tags = await _new_preview(gateway, ref)
    return IngestCheckResponse(
        kind="arxiv",
        arxiv_id=arxiv_id,
        arxiv_version=arxiv_version,
        bib=bib,
        latex_available=latex,
        suggested_tags=tags,
        saved=None,
    )


async def _new_preview(
    gateway: ArxivGateway, ref: ArxivId
) -> tuple[IngestCheckBib | None, bool | None, list[str]]:
    try:
        meta = await gateway.fetch_metadata(ref)
    except FetchError:
        return None, None, []
    bib = IngestCheckBib(
        title=meta.title,
        authors_short=authors_short(list(meta.authors)),
        venue=meta.venue,
        year=_year_of(meta.published_on),
    )
    try:
        latex: bool | None = await gateway.probe_latex_available(ref)
    except Exception:
        latex = None
    return bib, latex, list(meta.arxiv_categories)


async def _saved_preview(
    db: DbDep, item: LibraryItem
) -> tuple[IngestCheckBib | None, bool | None, list[str]]:
    paper = await db.get(Paper, item.paper_id)
    bib = None
    if paper is not None:
        bib = IngestCheckBib(
            title=paper.title,
            authors_short=authors_short(list(paper.authors)),
            venue=paper.venue,
            year=paper.published_on.year if paper.published_on else None,
        )
    latex = await _has_latex_source(db, str(item.paper_id))
    return bib, latex, list(item.suggested_tags)


def _year_of(published_on: str | None) -> int | None:
    if not published_on:
        return None
    try:
        return int(published_on[:4])
    except ValueError:
        return None


async def _has_latex_source(db: DbDep, paper_id: str) -> bool | None:
    row = (
        await db.execute(
            select(SourceAsset.id)
            .where(SourceAsset.paper_id == paper_id, SourceAsset.kind.in_(("arxiv_latex", "latex")))
            .limit(1)
        )
    ).first()
    if row is not None:
        return True
    quality = (
        await db.execute(
            select(DocumentRevision.quality_level)
            .where(DocumentRevision.paper_id == paper_id)
            .order_by(DocumentRevision.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if quality is None:
        return None
    return quality == "A"


async def _build_saved(db: DbDep, item: LibraryItem) -> IngestCheckSaved:
    job = await _latest_ingest_job(db, str(item.id))
    pipeline: IngestPipelineState | None = None
    if job is not None and job.status in _ACTIVE_JOB_STATUSES:
        pipeline = build_pipeline_state(job)
    return IngestCheckSaved(
        library_item_id=str(item.id),
        status=item.status,
        added_at=item.added_at.isoformat(),
        progress_pct=await _reading_progress(db, item),
        last_position=await _build_last_position(db, item),
        pipeline=pipeline,
    )


async def _latest_ingest_job(db: DbDep, library_item_id: str) -> Job | None:
    return (
        (
            await db.execute(
                select(Job)
                .where(Job.kind == "ingest", Job.library_item_id == library_item_id)
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _active_ingest_job(db: DbDep, paper_id: str) -> Job | None:
    return (
        (
            await db.execute(
                select(Job).where(
                    Job.kind == "ingest",
                    Job.paper_id == paper_id,
                    Job.status.in_(_ACTIVE_JOB_STATUSES),
                )
            )
        )
        .scalars()
        .first()
    )


async def _reading_progress(db: DbDep, item: LibraryItem) -> int:
    """読書位置(reading_position)からの粗い進捗(block_search_index の位置比)。"""
    rp = item.reading_position or {}
    revision_id = rp.get("revision_id")
    block_id = rp.get("block_id")
    if not revision_id or not block_id:
        return 0
    total = (
        await db.execute(
            select(func.count())
            .select_from(BlockSearchIndex)
            .where(BlockSearchIndex.revision_id == revision_id)
        )
    ).scalar_one()
    if total == 0:
        return 0
    position = (
        await db.execute(
            select(BlockSearchIndex.position).where(
                BlockSearchIndex.revision_id == revision_id,
                BlockSearchIndex.block_id == block_id,
            )
        )
    ).scalar_one_or_none()
    if position is None:
        return 0
    ahead = (
        await db.execute(
            select(func.count())
            .select_from(BlockSearchIndex)
            .where(
                BlockSearchIndex.revision_id == revision_id,
                BlockSearchIndex.position <= position,
            )
        )
    ).scalar_one()
    return min(100, (100 * ahead) // total)


async def _build_last_position(db: DbDep, item: LibraryItem) -> IngestLastPosition | None:
    rp = item.reading_position or {}
    revision_id = rp.get("revision_id")
    block_id = rp.get("block_id")
    if not revision_id or not block_id:
        return None
    section_display = (
        await db.execute(
            select(BlockSearchIndex.section_label).where(
                BlockSearchIndex.revision_id == revision_id,
                BlockSearchIndex.block_id == block_id,
            )
        )
    ).scalar_one_or_none() or ""
    mode = rp.get("view_mode") or rp.get("mode") or "translation"
    return IngestLastPosition(
        revision_id=str(revision_id),
        block_id=str(block_id),
        mode=str(mode),
        section_display=section_display,
        saved_at=item.updated_at.isoformat(),
    )


# --- POST /api/ingest/arxiv ---------------------------------------------------------


@router.post(
    "/api/ingest/arxiv",
    response_model=IngestArxivResponse,
    status_code=202,
    operation_id="ingest_arxiv",
)
async def ingest_arxiv(
    user: CurrentUserOrExt,
    db: DbDep,
    wakeup: JobWakeupDep,
    body: IngestArxivRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> IngestArxivResponse | JSONResponse:
    # 冪等: 同一キーの既存ジョブがあれば初回レスポンスを再生する(§3.2)。
    if idempotency_key:
        prior = (
            (await db.execute(select(Job).where(Job.idempotency_key == idempotency_key).limit(1)))
            .scalars()
            .first()
        )
        if prior is not None:
            return IngestArxivResponse(
                paper_id=str(prior.paper_id),
                library_item_id=str(prior.library_item_id),
                job_id=str(prior.id),
            )

    ref = parse_arxiv_url(body.url)
    if ref is None:
        raise ProblemException(
            "validation_error",
            detail="url is not an arXiv URL",
            errors=[ProblemError(field="url", message="arXiv の URL ではありません")],
        )

    status_value = body.status or "planned"
    if status_value not in _VALID_STATUSES:
        raise ProblemException(
            "validation_error",
            errors=[ProblemError(field="status", message="不正なステータスです")],
        )

    existing = await detect_duplicate(db, ref.id, user_id=str(user.id))
    if existing is not None:
        return await _duplicate_response(db, existing)

    # Paper を UPSERT(公開・共有)。既存 arXiv Paper があれば再利用(2 人目以降)。
    paper = (
        (
            await db.execute(
                select(Paper).where(Paper.arxiv_id == ref.id, Paper.visibility == "public")
            )
        )
        .scalars()
        .first()
    )
    if paper is None:
        paper = Paper(arxiv_id=ref.id, title=f"arXiv:{ref.versioned}", visibility="public")
        db.add(paper)
        await db.flush()
    paper_id = str(paper.id)

    item = LibraryItem(
        user_id=str(user.id),
        paper_id=paper_id,
        status=status_value,
        tags=list(body.tags or []),
        one_line_note=body.quick_note or "",
    )
    db.add(item)
    try:
        await db.flush()
    except IntegrityError:
        # 競合: 同一ユーザー・同一 Paper(uq_library_items_user_paper)→ duplicate。
        await db.rollback()
        again = await detect_duplicate(db, ref.id, user_id=str(user.id))
        if again is not None:
            return await _duplicate_response(db, again)
        raise
    library_item_id = str(item.id)

    if body.collection_id:
        await _add_to_collection(db, str(user.id), body.collection_id, library_item_id)

    await db.commit()

    # 稼働中 ingest があれば再利用(uq_jobs_ingest_active との競合回避)。
    active = await _active_ingest_job(db, paper_id)
    if active is not None:
        job_id = str(active.id)
    else:
        store = JobStore(db)
        job_id = await store.enqueue(
            kind="ingest",
            payload={
                "mode": "initial",
                "source": "arxiv",
                "arxiv_id": ref.id,
                "requested_version": ref.version_suffix or None,
                "url": body.url,
                "library_item_id": library_item_id,
            },
            idempotency_key=idempotency_key,
            priority="bulk",
            user_id=str(user.id),
            paper_id=paper_id,
            library_item_id=library_item_id,
        )
        await wakeup(job_id)

    return IngestArxivResponse(paper_id=paper_id, library_item_id=library_item_id, job_id=job_id)


async def _add_to_collection(
    db: DbDep, user_id: str, collection_id: str, library_item_id: str
) -> None:
    """有効(所有)なコレクションにのみ末尾追加する。無効時は黙ってスキップ(取り込みは継続)。"""
    collection = await db.get(Collection, collection_id)
    if collection is None or str(collection.user_id) != user_id:
        return
    exists = (
        await db.execute(
            select(CollectionEntry.id).where(
                CollectionEntry.collection_id == collection_id,
                CollectionEntry.library_item_id == library_item_id,
            )
        )
    ).first()
    if exists is not None:
        return
    next_pos = (
        await db.execute(
            select(func.coalesce(func.max(CollectionEntry.position), -1) + 1).where(
                CollectionEntry.collection_id == collection_id
            )
        )
    ).scalar_one()
    db.add(
        CollectionEntry(
            collection_id=collection_id,
            library_item_id=library_item_id,
            position=int(next_pos),
        )
    )


async def _duplicate_response(
    db: DbDep, existing: LibraryItem, *, instance: str = "/api/ingest/arxiv"
) -> JSONResponse:
    """§3.2 / §3.3 の 409 duplicate 本文(``existing`` 付き Problem Details)。

    arxiv・pdf の両取り込みエンドポイントが共有するため、呼び出し元の実パスを ``instance``
    で明示する(既定は後方互換で arxiv)。
    """
    last_position = await _build_last_position(db, existing)
    problem = build_problem(
        "duplicate",
        status=409,
        title="既にライブラリにあります",
        instance=instance,
    )
    content: dict[str, Any] = problem.model_dump(mode="json")
    content["existing"] = {
        "library_item_id": str(existing.id),
        "status": existing.status,
        "added_at": existing.added_at.isoformat(),
        "progress_pct": await _reading_progress(db, existing),
        "last_position": last_position.model_dump() if last_position is not None else None,
    }
    return JSONResponse(status_code=409, content=content, media_type=PROBLEM_CONTENT_TYPE)


# --- POST /api/ingest/pdf(§3.3) ------------------------------------------------------

_MAX_PDF_BYTES = 50 * 1024 * 1024  # 50MB(plans/03 §3.3・plans/05 §9.1-1)
_PDF_MAGIC = b"%PDF-"
_READ_CHUNK_BYTES = 1024 * 1024


def get_pdf_storage() -> S3Storage:
    return S3Storage()


PdfStorageDep = Annotated[S3Storage, Depends(get_pdf_storage)]


def _parse_pdf_meta(raw: str) -> IngestPdfMeta:
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ProblemException(
            "validation_error",
            detail="meta は JSON 文字列である必要があります",
            errors=[ProblemError(field="meta", message=str(exc))],
        ) from exc
    try:
        return IngestPdfMeta.model_validate(payload)
    except ValidationError as exc:
        raise ProblemException(
            "validation_error", errors=[ProblemError(field="meta", message=str(exc))]
        ) from exc


async def _read_limited_upload(file: UploadFile, limit: int) -> bytes:
    """ストリーム読取中の累積サイズ検査(§9.1-1。超過は 413)。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ProblemException("payload_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _title_from_filename(filename: str | None) -> str:
    """§9.1-4: title_guess が無ければファイル名(拡張子除去)、それも空なら既定文言。"""
    if not filename:
        return "無題の PDF"
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if base.lower().endswith(".pdf"):
        base = base[: -len(".pdf")]
    base = base.strip()
    return base or "無題の PDF"


async def _pdf_duplicate_for_user(db: DbDep, sha256: str, user_id: str) -> LibraryItem | None:
    """同一ユーザー・同一 SHA-256 の既存 Paper に紐づく LibraryItem を返す(§7.1 ③)。"""
    paper_id = (
        await db.execute(
            select(Paper.id).where(Paper.pdf_sha256 == sha256, Paper.owner_user_id == user_id)
        )
    ).scalar_one_or_none()
    if paper_id is None:
        return None
    return (
        (
            await db.execute(
                select(LibraryItem).where(
                    LibraryItem.paper_id == paper_id, LibraryItem.user_id == user_id
                )
            )
        )
        .scalars()
        .first()
    )


@router.post(
    "/api/ingest/pdf",
    response_model=IngestArxivResponse,
    status_code=202,
    operation_id="ingest_pdf",
)
async def ingest_pdf(
    user: CurrentUserOrExt,
    db: DbDep,
    wakeup: JobWakeupDep,
    storage: PdfStorageDep,
    request: Request,
    file: UploadFile,
    meta: Annotated[str, Form()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> IngestArxivResponse | JSONResponse:
    # 冪等: 同一キーの既存ジョブがあれば初回レスポンスを再生する(§3.3)。
    if idempotency_key:
        prior = (
            (await db.execute(select(Job).where(Job.idempotency_key == idempotency_key).limit(1)))
            .scalars()
            .first()
        )
        if prior is not None:
            return IngestArxivResponse(
                paper_id=str(prior.paper_id),
                library_item_id=str(prior.library_item_id),
                job_id=str(prior.id),
            )

    # Content-Length 事前拒否(§9.1-1)。ストリーム読取中の累積検査は後段で二重に行う。
    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit():
        if int(content_length) > _MAX_PDF_BYTES:
            raise ProblemException("payload_too_large")

    meta_obj = _parse_pdf_meta(meta)
    status_value = meta_obj.status or "planned"
    if status_value not in _VALID_STATUSES:
        raise ProblemException(
            "validation_error",
            errors=[ProblemError(field="meta.status", message="不正なステータスです")],
        )

    data = await _read_limited_upload(file, _MAX_PDF_BYTES)
    if not data.startswith(_PDF_MAGIC):
        raise ProblemException("unsupported_media_type", detail="PDF ファイルではありません")

    sha256 = hashlib.sha256(data).hexdigest()
    existing_item = await _pdf_duplicate_for_user(db, sha256, str(user.id))
    if existing_item is not None:
        return await _duplicate_response(db, existing_item, instance="/api/ingest/pdf")

    title = meta_obj.title_guess or _title_from_filename(file.filename)
    paper = Paper(
        title=title,
        visibility="private",
        owner_user_id=str(user.id),
        pdf_sha256=sha256,
        license="unknown",
    )
    db.add(paper)
    try:
        await db.flush()
    except IntegrityError:
        # 競合: 同一ユーザー・同一 SHA-256(uq_papers_owner_pdf_sha256)→ duplicate。
        await db.rollback()
        again = await _pdf_duplicate_for_user(db, sha256, str(user.id))
        if again is not None:
            return await _duplicate_response(db, again, instance="/api/ingest/pdf")
        raise
    paper_id = str(paper.id)

    storage_key = StorageKeys.original_pdf(paper_id, "v1")
    await storage.put(storage.sources_bucket, storage_key, data, content_type="application/pdf")
    db.add(
        SourceAsset(
            paper_id=paper_id,
            kind="extension_capture",
            source_url=meta_obj.source_url,
            source_version="v1",
            storage_key=storage_key,
            content_type="application/pdf",
            byte_size=len(data),
            sha256=sha256,
        )
    )

    item = LibraryItem(
        user_id=str(user.id),
        paper_id=paper_id,
        status=status_value,
        tags=list(meta_obj.tags or []),
        one_line_note=meta_obj.quick_note or "",
    )
    db.add(item)
    await db.flush()
    library_item_id = str(item.id)

    if meta_obj.collection_id:
        await _add_to_collection(db, str(user.id), meta_obj.collection_id, library_item_id)

    await db.commit()

    # テキストレイヤ判定(plans/05 §6.1・§9.2)。無ければジョブ側で即 failed(202 は維持)。
    user_id = str(user.id)
    try:
        check_text_layer(data)
        job_id = await _enqueue_pdf_ingest(
            db, wakeup, idempotency_key, user_id, paper_id, library_item_id
        )
    except PdfParseError as exc:
        job_id = await _fail_pdf_ingest(
            db, idempotency_key, user_id, paper_id, library_item_id, exc
        )

    return IngestArxivResponse(paper_id=paper_id, library_item_id=library_item_id, job_id=job_id)


async def _enqueue_pdf_ingest(
    db: DbDep,
    wakeup: JobWakeup,
    idempotency_key: str | None,
    user_id: str,
    paper_id: str,
    library_item_id: str,
) -> str:
    store = JobStore(db)
    job_id = await store.enqueue(
        kind="ingest",
        payload={"mode": "initial", "source": "pdf_upload", "library_item_id": library_item_id},
        idempotency_key=idempotency_key,
        priority="bulk",
        user_id=user_id,
        paper_id=paper_id,
        library_item_id=library_item_id,
    )
    await wakeup(job_id)
    return job_id


async def _fail_pdf_ingest(
    db: DbDep,
    idempotency_key: str | None,
    user_id: str,
    paper_id: str,
    library_item_id: str,
    exc: PdfParseError,
) -> str:
    """テキストレイヤ無し PDF は受け口の同期チェックで即 failed にする(§6.1・§9.2)。

    apps/worker の PDF パイプライン結線は本タスクの所有範囲外のため、軽量な
    ``check_text_layer`` のみを受け口で同期実行する(deviations 参照)。
    """
    job = Job(
        kind="ingest",
        stage="parsing",
        status="failed",
        progress=0,
        payload={"mode": "initial", "source": "pdf_upload", "library_item_id": library_item_id},
        idempotency_key=idempotency_key,
        user_id=user_id,
        paper_id=paper_id,
        library_item_id=library_item_id,
        error=json.dumps(
            {"stage": "parsing", "code": exc.kind, "message": exc.message}, ensure_ascii=False
        ),
        log=[joblog.log_entry("parsing", "error", exc.message, detail={"code": exc.kind})],
        finished_at=dt.datetime.now(dt.UTC),
    )
    db.add(job)
    await db.commit()
    return str(job.id)


# --- GET /api/ingest/recent ---------------------------------------------------------


@router.get("/api/ingest/recent", response_model=IngestRecentResponse, operation_id="ingest_recent")
async def ingest_recent(
    user: CurrentUserOrExt,
    db: DbDep,
    limit: int = Query(default=3, ge=1, le=10),
) -> IngestRecentResponse:
    jobs = (
        (
            await db.execute(
                select(Job)
                .where(
                    Job.kind == "ingest",
                    Job.user_id == str(user.id),
                    Job.library_item_id.is_not(None),
                )
                .order_by(Job.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    items: list[IngestRecentItem] = []
    seen: set[str] = set()
    for job in jobs:
        library_item_id = str(job.library_item_id)
        if library_item_id in seen:
            continue
        seen.add(library_item_id)
        item = await db.get(LibraryItem, library_item_id)
        if item is None:
            continue
        paper = await db.get(Paper, item.paper_id)
        items.append(
            IngestRecentItem(
                library_item_id=library_item_id,
                title=paper.title if paper is not None else "",
                pipeline=build_pipeline_state(job),
                completed_at=job.finished_at.isoformat() if job.finished_at else None,
                viewer_url=f"/papers/{library_item_id}",
            )
        )
        if len(items) >= limit:
            break
    return IngestRecentResponse(items=items)
