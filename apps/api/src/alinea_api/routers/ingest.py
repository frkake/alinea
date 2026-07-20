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

import asyncio
import datetime as dt
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from urllib.parse import urlsplit

import structlog
from alinea_core.adapters import SiteAdapter, SiteMeta, SiteRef, resolve_adapter
from alinea_core.adapters.fetch import (
    SiteFetchError,
    adapter_allowed_hosts,
    fetch_html,
    fetch_note,
)
from alinea_core.adapters.fetch import (
    fetch_pdf as fetch_site_pdf,
)
from alinea_core.arxiv.fetch import FetchError, fetch_pdf, probe_latex_available
from alinea_core.arxiv.ids import ArxivId, parse_arxiv_url, pdf_url
from alinea_core.arxiv.limits import MAX_ARXIV_PDF_BYTES
from alinea_core.arxiv.metadata import ArxivMeta, fetch_metadata
from alinea_core.db.models import (
    BlockSearchIndex,
    Collection,
    CollectionEntry,
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    PaperExternalId,
    SourceAsset,
)
from alinea_core.db.revisions import get_latest_paper_revision, get_paper_revision
from alinea_core.document.blocks import DocumentContent
from alinea_core.ingest.dedupe import detect_duplicate
from alinea_core.jobs.store import JobStore
from alinea_core.licenses import is_public_shareable_license
from alinea_core.parsing.source_candidates import site_source_candidates
from alinea_core.settings import CoreSettings
from alinea_core.storage.s3 import S3Storage, StorageKeys
from fastapi import APIRouter, Depends, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from alinea_api.deps import CurrentUserOrExt, DbDep, SettingsDep
from alinea_api.errors import PROBLEM_CONTENT_TYPE, ProblemError, ProblemException, build_problem
from alinea_api.schemas.ingest import (
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
    SiteIngestRequest,
    SiteIngestResponse,
    authors_short,
    build_pipeline_state,
)

router = APIRouter(tags=["ingest"])
log = structlog.get_logger("alinea.api.ingest")

# plans/01 §4.3 のキュー名(識別子)。apps/worker への import は禁止のため定数で持つ。
BULK_QUEUE = "alinea:bulk"

# 取り込み時に受け付ける Status(§1.6)。拡張 UI は planned|up_next|reading の 3 択(§3.2)。
_VALID_STATUSES = frozenset({"planned", "up_next", "reading", "done", "reread", "on_hold"})
_ACTIVE_JOB_STATUSES = ("queued", "running", "waiting_quota", "waiting_input")

_MAX_PDF_BYTES = 50 * 1024 * 1024  # 50MB(plans/03 §3.3・plans/05 §9.1-1)
_PDF_MAGIC = b"%PDF-"
_READ_CHUNK_BYTES = 1024 * 1024
_PDF_PLACEHOLDER_PARSER_VERSION = "pdf-placeholder-1.0.0"
_ARXIV_PDF_PREFETCH_TIMEOUT_SECONDS = 8.0


def get_pdf_storage() -> S3Storage:
    return S3Storage()


PdfStorageDep = Annotated[S3Storage, Depends(get_pdf_storage)]


# --- 依存(テストで差し替え可能) ---------------------------------------------------


class ArxivGateway:
    """arXiv メタデータ・LaTeX 判定の薄いラッパ(``alinea_core.arxiv`` 経由)。"""

    async def fetch_metadata(self, ref: ArxivId) -> ArxivMeta:
        return await fetch_metadata(ref)

    async def probe_latex_available(self, ref: ArxivId) -> bool:
        return await probe_latex_available(ref)

    async def fetch_pdf(self, ref: ArxivId, settings: CoreSettings) -> bytes:
        return await fetch_pdf(ref, settings=settings, max_bytes=_MAX_PDF_BYTES)


def get_arxiv_gateway() -> ArxivGateway:
    return ArxivGateway()


ArxivGatewayDep = Annotated[ArxivGateway, Depends(get_arxiv_gateway)]

_MAX_SITE_PDF_BYTES = MAX_ARXIV_PDF_BYTES


class SiteGateway:
    """他サイト取り込みの HTTP 副作用ラッパ(SSRF 対策付き。テストで差し替え可能)。

    アダプタ(純粋)が宣言するホストだけを許可し、landing HTML → SiteMeta 写像、本文 PDF の
    取得を :mod:`alinea_core.adapters.fetch` の境界付きクライアント経由で行う。

    OpenReview は API2 note を優先取得し、note 不在なら citation_* フォールバックを使う。
    ACL Anthology 等の他アダプタは従来通り landing HTML → parse_metadata。
    """

    async def fetch_metadata(self, adapter: SiteAdapter, ref: SiteRef) -> SiteMeta:
        from alinea_core.adapters.openreview import OpenReviewAdapter

        allowed = adapter_allowed_hosts(adapter, ref)
        html = await fetch_html(adapter.landing_url(ref), allowed_hosts=allowed)

        if isinstance(adapter, OpenReviewAdapter):
            # OpenReview: API2 note を試み、取れたら note 経由の高品質メタを使う。
            # note が None/403/empty の場合は citation_* フォールバックへ。
            note = await fetch_note(adapter, ref)
            return adapter.parse_metadata_from_note_and_citation(
                note=note, citation_html=html, ref=ref
            )

        return adapter.parse_metadata(html, ref)

    async def fetch_pdf(
        self, adapter: SiteAdapter, ref: SiteRef, meta: SiteMeta, settings: CoreSettings
    ) -> bytes:
        if not meta.pdf_url:
            raise SiteFetchError("source_not_found", "site metadata has no pdf url")
        # SSRF: allow-list は「アダプタが宣言したホスト」だけから作る。meta.pdf_url は
        # 取得済み landing HTML(citation_pdf_url)由来で攻撃者が操作し得るため、その
        # ホストが adapter の allow-list に入っていることをフェッチ前に検証する
        # (自己参照 allow-list を作らない)。リダイレクトも fetch_pdf 内で同一 allow-list
        # に照らして再検証される。
        allowed = adapter_allowed_hosts(adapter, ref)
        pdf_host = urlsplit(meta.pdf_url).hostname
        if pdf_host is None or pdf_host.lower() not in allowed:
            raise SiteFetchError(
                "source_not_found", "pdf url host is not in the adapter allow-list"
            )
        return await fetch_site_pdf(
            meta.pdf_url,
            allowed_hosts=allowed,
            settings=settings,
            max_bytes=_MAX_SITE_PDF_BYTES,
        )


def get_site_gateway() -> SiteGateway:
    return SiteGateway()


SiteGatewayDep = Annotated[SiteGateway, Depends(get_site_gateway)]

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
    site_gateway: SiteGatewayDep,
    url: str = Query(..., description="現在タブの URL"),
) -> IngestCheckResponse:
    ref = parse_arxiv_url(url)
    if ref is None:
        # 他サイトアダプタ(ACL Anthology 等)の論文ページなら kind="site" を返す。
        resolved = resolve_adapter(url)
        if resolved is not None:
            adapter, site_ref = resolved
            return await _site_check(
                db, site_gateway, adapter, site_ref, user_id=str(user.id)
            )
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


async def _find_paper_by_external_id(db: DbDep, site: str, external_id: str) -> Paper | None:
    """(site, external_id) の外部識別子から既存 Paper を返す(冪等化の第 2 判定)。"""
    paper_id = (
        await db.execute(
            select(PaperExternalId.paper_id).where(
                PaperExternalId.site == site, PaperExternalId.external_id == external_id
            )
        )
    ).scalar_one_or_none()
    if paper_id is None:
        return None
    return await db.get(Paper, str(paper_id))


async def _library_item_for_paper(db: DbDep, paper_id: str, user_id: str) -> LibraryItem | None:
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


def _site_body_unavailable(adapter: SiteAdapter, ref: SiteRef) -> bool:
    """本文取得経路が無いサイト参照か(PubMed 単体など)を判定する。

    候補順(source_candidates)が JATS を含めば OK(PMC)。PDF のみのサイトは、アダプタが
    PDF 直リンク(または landing の citation_pdf_url)を出せる限り OK(ACL Anthology 等)。
    PubMed は JATS 本文が無く pdf_url も None のため、この経路では本文へ到達できない。
    JATS/NCBI 経路が API に配線されるまでは終端シグナル(415)を返すためのゲート。
    """
    candidates = site_source_candidates(adapter.site)
    if "jats" in candidates:
        # JATS を第一候補にするサイト(PMC)。JATS/PDF いずれかで本文へ到達可能。
        return False
    # PDF のみのサイト。PDF 直リンクを予測できないアダプタ(PubMed: pdf_url=None)は本文取得不可。
    return adapter.pdf_url(ref) is None


async def _site_check(
    db: DbDep,
    site_gateway: SiteGateway,
    adapter: SiteAdapter,
    ref: SiteRef,
    *,
    user_id: str,
) -> IngestCheckResponse:
    """kind="site" のプレビュー(既存なら saved、無ければ landing メタから書誌プレビュー)。"""
    existing = await _find_paper_by_external_id(db, adapter.site, ref.external_id)
    if existing is not None:
        item = await _library_item_for_paper(db, str(existing.id), user_id)
        saved_bib = IngestCheckBib(
            title=existing.title,
            authors_short=authors_short(list(existing.authors)),
            venue=existing.venue,
            year=existing.published_on.year if existing.published_on else None,
        )
        return IngestCheckResponse(
            kind="site",
            site=adapter.site,
            external_id=ref.external_id,
            bib=saved_bib,
            saved=(await _build_saved(db, item)) if item is not None else None,
        )

    try:
        meta = await site_gateway.fetch_metadata(adapter, ref)
    except Exception:
        # プレビュー取得失敗は unsupported にせず、書誌なしの site として返す(§3.1 と同方針)。
        return IngestCheckResponse(kind="site", site=adapter.site, external_id=ref.external_id)
    bib = IngestCheckBib(
        title=meta.title,
        authors_short=authors_short(list(meta.authors)),
        venue=meta.venue,
        year=_year_of(meta.published_on),
    )
    return IngestCheckResponse(
        kind="site",
        site=adapter.site,
        external_id=ref.external_id,
        bib=bib,
        suggested_tags=list(meta.categories),
        saved=None,
    )


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


async def _active_ingest_job(db: DbDep, paper_id: str, *, user_id: str | None = None) -> Job | None:
    """Return an active ingest, optionally constrained to its owner.

    A caller that returns or reuses the job ID must supply ``user_id``.  The unscoped form remains
    for shared-paper reingest paths that only perform an existence check and never expose the job.
    """
    conditions = [
        Job.kind == "ingest",
        Job.paper_id == paper_id,
        Job.status.in_(_ACTIVE_JOB_STATUSES),
    ]
    if user_id is not None:
        conditions.append(Job.user_id == user_id)
    return (await db.execute(select(Job).where(*conditions))).scalars().first()


def _scoped_ingest_idempotency_key(user_id: str, request_key: str) -> str:
    digest = hashlib.sha256(f"{user_id}\0{request_key}".encode()).hexdigest()
    return f"ingest:v1:{digest}"


async def _prior_ingest_job(db: DbDep, *, user_id: str, request_key: str) -> Job | None:
    """Find a user-owned retry, including raw keys written before user scoping."""
    scoped_key = _scoped_ingest_idempotency_key(user_id, request_key)
    return (
        (
            await db.execute(
                select(Job)
                .where(
                    Job.kind == "ingest",
                    Job.user_id == user_id,
                    Job.idempotency_key.in_((scoped_key, request_key)),
                )
                .order_by(Job.created_at.desc())
                .limit(1)
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
    paper = await db.get(Paper, item.paper_id)
    revision = (
        await get_paper_revision(db, paper_id=paper.id, revision_id=revision_id)
        if paper is not None
        else None
    )
    if revision is None:
        return 0
    total = (
        await db.execute(
            select(func.count())
            .select_from(BlockSearchIndex)
            .where(BlockSearchIndex.revision_id == revision.id)
        )
    ).scalar_one()
    if total == 0:
        return 0
    position = (
        await db.execute(
            select(BlockSearchIndex.position).where(
                BlockSearchIndex.revision_id == revision.id,
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
                BlockSearchIndex.revision_id == revision.id,
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
    paper = await db.get(Paper, item.paper_id)
    revision = (
        await get_paper_revision(db, paper_id=paper.id, revision_id=revision_id)
        if paper is not None
        else None
    )
    if revision is None:
        return None
    section_display = (
        await db.execute(
            select(BlockSearchIndex.section_label).where(
                BlockSearchIndex.revision_id == revision.id,
                BlockSearchIndex.block_id == block_id,
            )
        )
    ).scalar_one_or_none()
    if section_display is None:
        return None
    mode = rp.get("view_mode") or rp.get("mode") or "translation"
    return IngestLastPosition(
        revision_id=str(revision.id),
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
    gateway: ArxivGatewayDep,
    storage: PdfStorageDep,
    settings: SettingsDep,
    body: IngestArxivRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> IngestArxivResponse | JSONResponse:
    user_id = str(user.id)
    stored_idempotency_key = (
        _scoped_ingest_idempotency_key(user_id, idempotency_key) if idempotency_key else None
    )
    # 冪等: 同一キーの既存ジョブがあれば初回レスポンスを再生する(§3.2)。
    if idempotency_key:
        prior = await _prior_ingest_job(db, user_id=user_id, request_key=idempotency_key)
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

    existing = await detect_duplicate(db, ref.id, user_id=user_id)
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

    source_version = ref.version_suffix or "latest"
    await _ensure_arxiv_pdf_available(db, paper, ref, source_version, gateway, storage, settings)

    item = LibraryItem(
        user_id=user_id,
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
        again = await detect_duplicate(db, ref.id, user_id=user_id)
        if again is not None:
            return await _duplicate_response(db, again)
        raise
    library_item_id = str(item.id)

    if body.collection_id:
        await _add_to_collection(db, user_id, body.collection_id, library_item_id)

    await db.commit()

    # 稼働中 ingest があれば再利用(uq_jobs_ingest_active との競合回避)。
    active = await _active_ingest_job(db, paper_id, user_id=user_id)
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
            idempotency_key=stored_idempotency_key,
            priority="bulk",
            user_id=user_id,
            paper_id=paper_id,
            library_item_id=library_item_id,
        )
        await wakeup(job_id)

    return IngestArxivResponse(paper_id=paper_id, library_item_id=library_item_id, job_id=job_id)


async def _ensure_arxiv_pdf_available(
    db: DbDep,
    paper: Paper,
    ref: ArxivId,
    source_version: str,
    gateway: ArxivGateway,
    storage: S3Storage,
    settings: CoreSettings,
) -> None:
    """arXiv 取り込み開始時に原文 PDF を同期保存し、解析前の PDF 表示を可能にする。

    PDF の事前取得はビューアの PDF タブを早く有効にするための補助処理で、取り込み本体は
    worker の fetching 段で LaTeX/HTML/PDF を改めて取得する。arXiv 側の一時失敗で保存
    要求全体を 502 にしないよう、取得失敗は警告ログに留めて続行する。
    """

    paper_id = str(paper.id)
    existing_pdf = (
        await db.execute(
            select(SourceAsset.id, SourceAsset.source_version)
            .where(SourceAsset.paper_id == paper_id, SourceAsset.kind == "pdf")
            .order_by(SourceAsset.created_at.desc())
            .limit(1)
        )
    ).first()

    # プレースホルダは実際に保存された PDF 資産のバージョンに揃える。既存資産があれば
    # そちらが真実(worker がフェッチ段で 'latest' エイリアスを実バージョンへ解決し、
    # 資産行を書き換えている場合があるため)。新規保存時はこの要求時点の source_version
    # をそのまま資産にも刻むので、両者は一致する。
    placeholder_source_version = (
        existing_pdf.source_version if existing_pdf is not None else source_version
    )

    if existing_pdf is None:
        try:
            data = await asyncio.wait_for(
                gateway.fetch_pdf(ref, settings), timeout=_ARXIV_PDF_PREFETCH_TIMEOUT_SECONDS
            )
            storage_key = StorageKeys.original_pdf(paper_id, source_version)
            sha256 = hashlib.sha256(data).hexdigest()
            await storage.put(
                storage.sources_bucket,
                storage_key,
                data,
                content_type="application/pdf",
            )
        except FetchError as exc:
            log.warning(
                "arxiv_pdf_prefetch_failed",
                arxiv_id=ref.id,
                version=ref.version_suffix or "latest",
                kind=exc.kind,
                error=str(exc),
            )
        except TimeoutError:
            log.warning(
                "arxiv_pdf_prefetch_timeout",
                arxiv_id=ref.id,
                version=ref.version_suffix or "latest",
                timeout_seconds=_ARXIV_PDF_PREFETCH_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise ProblemException("provider_error", detail="PDF 原本の保存に失敗しました") from exc
        else:
            db.add(
                SourceAsset(
                    paper_id=paper_id,
                    kind="pdf",
                    source_url=pdf_url(ref, settings.alinea_arxiv_base_url or None),
                    source_version=source_version,
                    storage_key=storage_key,
                    content_type="application/pdf",
                    byte_size=len(data),
                    sha256=sha256,
                )
            )

    await _ensure_pdf_placeholder_revision(db, paper, placeholder_source_version)


async def _ensure_pdf_placeholder_revision(db: DbDep, paper: Paper, source_version: str) -> None:
    """構造化前でも PDF モードを開くための空リビジョンを用意する。"""

    if await get_latest_paper_revision(db, paper) is not None:
        return

    paper_id = str(paper.id)
    existing = (
        (
            await db.execute(
                select(DocumentRevision).where(
                    DocumentRevision.paper_id == paper_id,
                    DocumentRevision.source_version == source_version,
                    DocumentRevision.parser_version == _PDF_PLACEHOLDER_PARSER_VERSION,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        paper.latest_revision_id = existing.id
        return

    content = DocumentContent(quality_level="B", sections=[])
    revision = DocumentRevision(
        paper_id=paper_id,
        source_version=source_version,
        parser_version=_PDF_PLACEHOLDER_PARSER_VERSION,
        quality_level="B",
        source_format="pdf",
        content=content.model_dump(),
        stats={"pages": None, "figures": 0, "tables": 0, "blocks": 0, "translatable_blocks": 0},
    )
    db.add(revision)
    await db.flush()
    paper.latest_revision_id = revision.id


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


# --- POST /api/ingest/site(S8。他サイト取り込み) -------------------------------------


@router.post(
    "/api/ingest/site",
    response_model=SiteIngestResponse,
    status_code=202,
    operation_id="ingest_site",
)
async def ingest_site(
    user: CurrentUserOrExt,
    db: DbDep,
    wakeup: JobWakeupDep,
    site_gateway: SiteGatewayDep,
    storage: PdfStorageDep,
    settings: SettingsDep,
    body: SiteIngestRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> SiteIngestResponse | JSONResponse:
    """ACL Anthology 等のサイト論文ページを取り込む(landing→PDF を取得し品質 B で構造化)。

    冪等化順(§7.1 相当): DOI → (site, external_id) → PDF SHA-256 の順に既存 Paper を探し、
    どれにも一致しなければ新規作成する。互換ライセンスが機械判定できないときは
    ``visibility="private"`` + ``owner_user_id``、明示された CC/CC0 のみ ``public`` を許す。
    """
    user_id = str(user.id)
    stored_idempotency_key = (
        _scoped_ingest_idempotency_key(user_id, idempotency_key) if idempotency_key else None
    )
    if idempotency_key:
        prior = await _prior_ingest_job(db, user_id=user_id, request_key=idempotency_key)
        if prior is not None:
            return SiteIngestResponse(
                paper_id=str(prior.paper_id),
                library_item_id=str(prior.library_item_id),
                job_id=str(prior.id),
            )

    resolved = resolve_adapter(body.url)
    if resolved is None:
        raise ProblemException(
            "validation_error",
            detail="url is not a supported site URL",
            errors=[ProblemError(field="url", message="対応サイトの URL ではありません")],
        )
    adapter, ref = resolved

    status_value = body.status or "planned"
    if status_value not in _VALID_STATUSES:
        raise ProblemException(
            "validation_error",
            errors=[ProblemError(field="status", message="不正なステータスです")],
        )

    landing_url = adapter.landing_url(ref)

    # 本文取得経路が無いサイト(PubMed 単体: JATS 本文なし・PDF 直リンクなし)は、常に失敗する
    # provider_error(502=リトライ示唆)ではなく、明確な終端シグナル(415=未対応)を返す。
    # JATS/NCBI エンドツーエンド経路が API に配線されるまでの暫定ゲート(silent always-500 回避)。
    if _site_body_unavailable(adapter, ref):
        raise ProblemException(
            "unsupported_media_type",
            detail="このサイトの本文取得はまだ対応していません(書誌のみ取得可能)",
            errors=[ProblemError(field="url", message="本文取得は未対応です")],
        )

    # (site, external_id) の既存 Paper があり、かつユーザーが既に所有していれば duplicate。
    existing_paper = await _find_paper_by_external_id(db, adapter.site, ref.external_id)
    if existing_paper is not None:
        existing_item = await _library_item_for_paper(db, str(existing_paper.id), user_id)
        if existing_item is not None:
            return await _duplicate_response(db, existing_item, instance="/api/ingest/site")

    # landing メタと本文 PDF を SSRF 対策付きで取得する。
    try:
        meta = await site_gateway.fetch_metadata(adapter, ref)
        pdf_bytes = await site_gateway.fetch_pdf(adapter, ref, meta, settings)
    except SiteFetchError as exc:
        raise ProblemException(
            "provider_error", detail=f"サイトからの取得に失敗しました({exc.kind})"
        ) from exc
    if not pdf_bytes.startswith(_PDF_MAGIC):
        raise ProblemException("provider_error", detail="取得した本文が PDF ではありません")

    sha256 = hashlib.sha256(pdf_bytes).hexdigest()

    # 冪等化: DOI → (site, external_id) → PDF SHA-256 の順に既存 Paper を探す。
    # 再利用は「public か自分の所有」に限る(他人の private には寄せない。§7.1)。
    paper = await _resolve_site_paper(
        db, meta=meta, existing_by_external_id=existing_paper, sha256=sha256, user_id=user_id
    )
    created_new = paper is None
    is_public = is_public_shareable_license(meta.license)
    if paper is None:
        paper = Paper(
            title=meta.title or landing_url,
            authors=list(meta.authors),
            abstract=meta.abstract or "",
            published_on=_site_date(meta.published_on),
            venue=meta.venue,
            doi=meta.doi,
            license=meta.license or "unknown",
            pdf_sha256=sha256,
            visibility="public" if is_public else "private",
            owner_user_id=None if is_public else user_id,
        )
        db.add(paper)
        await db.flush()
    paper_id = str(paper.id)

    # 外部識別子を保存((site, external_id) unique。冪等: 既存があれば作らない)。
    await _ensure_paper_external_id(db, paper_id, adapter.site, ref.external_id, landing_url)

    # 既にこのユーザーが所有していれば duplicate(external-id 名寄せで再利用したケース)。
    existing_item = await _library_item_for_paper(db, paper_id, user_id)
    if existing_item is not None:
        await db.commit()
        return await _duplicate_response(db, existing_item, instance="/api/ingest/site")

    # 原本 PDF の保存は「新規作成した Paper」に対してのみ行う。再利用した(public または自分の)
    # Paper は最初の取り込みで既に原本 PDF・リビジョンを持つため、上書き保存しない
    # (他ユーザーの共有 Paper の原本 PDF を untrusted なサイト取得で破壊しない)。
    if created_new:
        storage_key = StorageKeys.original_pdf(paper_id, "v1")
        await storage.put(
            storage.sources_bucket, storage_key, pdf_bytes, content_type="application/pdf"
        )
        db.add(
            SourceAsset(
                paper_id=paper_id,
                kind="pdf",
                source_url=landing_url,
                source_version="v1",
                storage_key=storage_key,
                content_type="application/pdf",
                byte_size=len(pdf_bytes),
                sha256=sha256,
            )
        )
        await _ensure_pdf_placeholder_revision(db, paper, "v1")

    item = LibraryItem(
        user_id=user_id,
        paper_id=paper_id,
        status=status_value,
        tags=list(body.tags or []),
        one_line_note=body.quick_note or "",
    )
    db.add(item)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        again = await _library_item_for_paper(db, paper_id, user_id)
        if again is not None:
            return await _duplicate_response(db, again, instance="/api/ingest/site")
        raise
    library_item_id = str(item.id)

    if body.collection_id:
        await _add_to_collection(db, user_id, body.collection_id, library_item_id)

    await db.commit()

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="ingest",
        payload={
            "mode": "initial",
            "source": "site",
            "site": adapter.site,
            "external_id": ref.external_id,
            "landing_url": landing_url,
            "library_item_id": library_item_id,
        },
        idempotency_key=stored_idempotency_key,
        priority="bulk",
        user_id=user_id,
        paper_id=paper_id,
        library_item_id=library_item_id,
    )
    await wakeup(job_id)

    return SiteIngestResponse(paper_id=paper_id, library_item_id=library_item_id, job_id=job_id)


def _site_date(published_on: str | None) -> Any:
    if not published_on:
        return None
    try:
        return dt.date.fromisoformat(published_on)
    except ValueError:
        return None


def _reusable_for_user(paper: Paper | None, user_id: str) -> Paper | None:
    """他ユーザーの private Paper への name-match を禁止する(§7.1・arXiv 経路と同方針)。

    再利用してよいのは「public」または「自分が所有する Paper」だけ。他人の private Paper に
    寄せると、その論文へ LibraryItem を貼り原本 PDF を上書きしてしまう(情報漏えい・破壊)。
    """
    if paper is None:
        return None
    if paper.visibility == "public" or str(paper.owner_user_id) == user_id:
        return paper
    return None


async def _resolve_site_paper(
    db: DbDep,
    *,
    meta: SiteMeta,
    existing_by_external_id: Paper | None,
    sha256: str,
    user_id: str,
) -> Paper | None:
    """DOI → (site, external_id) → PDF SHA-256 の順に既存 Paper を探す(§7.1)。

    いずれの一致でも「public か自分の所有」に限って再利用する(他人の private には寄せない)。
    """
    if meta.doi:
        by_doi = (
            (await db.execute(select(Paper).where(Paper.doi == meta.doi))).scalars().first()
        )
        reusable = _reusable_for_user(by_doi, user_id)
        if reusable is not None:
            return reusable
    reusable = _reusable_for_user(existing_by_external_id, user_id)
    if reusable is not None:
        return reusable
    by_sha = (
        (
            await db.execute(
                select(Paper).where(Paper.pdf_sha256 == sha256, Paper.owner_user_id == user_id)
            )
        )
        .scalars()
        .first()
    )
    return by_sha


async def _ensure_paper_external_id(
    db: DbDep, paper_id: str, site: str, external_id: str, canonical_url: str
) -> None:
    """(site, external_id) 行を冪等に作る(既存があれば何もしない)。"""
    existing = (
        await db.execute(
            select(PaperExternalId.id).where(
                PaperExternalId.site == site, PaperExternalId.external_id == external_id
            )
        )
    ).first()
    if existing is not None:
        return
    db.add(
        PaperExternalId(
            paper_id=paper_id,
            site=site,
            external_id=external_id,
            canonical_url=canonical_url,
        )
    )
    await db.flush()


# --- POST /api/ingest/pdf(§3.3) ------------------------------------------------------


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
    user_id = str(user.id)
    stored_idempotency_key = (
        _scoped_ingest_idempotency_key(user_id, idempotency_key) if idempotency_key else None
    )
    # 冪等: 同一キーの既存ジョブがあれば初回レスポンスを再生する(§3.3)。
    if idempotency_key:
        prior = await _prior_ingest_job(db, user_id=user_id, request_key=idempotency_key)
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
    existing_item = await _pdf_duplicate_for_user(db, sha256, user_id)
    if existing_item is not None:
        return await _duplicate_response(db, existing_item, instance="/api/ingest/pdf")

    title = meta_obj.title_guess or _title_from_filename(file.filename)
    paper = Paper(
        title=title,
        visibility="private",
        owner_user_id=user_id,
        pdf_sha256=sha256,
        license="unknown",
    )
    db.add(paper)
    try:
        await db.flush()
    except IntegrityError:
        # 競合: 同一ユーザー・同一 SHA-256(uq_papers_owner_pdf_sha256)→ duplicate。
        await db.rollback()
        again = await _pdf_duplicate_for_user(db, sha256, user_id)
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
    await _ensure_pdf_placeholder_revision(db, paper, "v1")

    item = LibraryItem(
        user_id=user_id,
        paper_id=paper_id,
        status=status_value,
        tags=list(meta_obj.tags or []),
        one_line_note=meta_obj.quick_note or "",
    )
    db.add(item)
    await db.flush()
    library_item_id = str(item.id)

    if meta_obj.collection_id:
        await _add_to_collection(db, user_id, meta_obj.collection_id, library_item_id)

    await db.commit()

    # テキストレイヤ判定と最終 OCR fallback は bounded worker 側で行う。
    job_id = await _enqueue_pdf_ingest(
        db, wakeup, stored_idempotency_key, user_id, paper_id, library_item_id
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
