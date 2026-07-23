"""papers ルータ — 論文実体(plans/03 §4・§6.8)。

- ``POST /api/papers/{paper_id}/reingest``            再取り込み(202・実行中は 409 conflict)。
- ``GET  /api/papers/{paper_id}/ingest-log``          処理ログ(at 昇順・ページングなし)。
- ``GET  /api/papers/{paper_id}/pdf``                 原本 PDF(同一オリジンで bytes 配信)。
- ``POST /api/library-items/{id}/adopt-revision``     新リビジョンへの切替+リアンカー(§6.8。
  M1-22。新しいバージョンのバナー・B→A 昇格提案の適用の両方から使う共通経路)。自動切替はしない
  (P6)。本エンドポイントがユーザー操作の唯一の適用経路。

共有の依存(``S3Storage`` / 所有チェック)を提供し、assets ルータからも import する。
"""

from __future__ import annotations

from typing import Annotated, Any

from alinea_core.db.models import (
    DocumentRevision,
    Job,
    LibraryItem,
    Paper,
    PaperExternalId,
    SourceAsset,
)
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.ingest.joblog import project_ingest_log
from alinea_core.ingest.reanchor import ReanchorStats, reanchor_paper
from alinea_core.jobs.store import JobStore
from alinea_core.parsing.source_candidates import site_source_candidates
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_core.translation import find_effective_set
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from alinea_api.deps import CurrentUser, DbDep
from alinea_api.errors import ProblemException
from alinea_api.routers.ingest import JobWakeupDep, _active_ingest_job
from alinea_api.routers.library_items import _summary_for
from alinea_api.routers.viewer import resolve_owned_library_item
from alinea_api.schemas.common import LibraryItemSummary
from alinea_api.schemas.papers import (
    FigureMaterializeBatchRequest,
    FigureMaterializeResponse,
    PapersIngestLogEntry,
    PapersIngestLogResponse,
    PapersReingestResponse,
)

router = APIRouter(tags=["papers"])

# 原本 PDF とみなす source_assets.kind(§4.4)。"extension_capture" は POST /api/ingest/pdf
# (拡張/一般 PDF 直接送信。M1-18)が書き込む実際の kind(ck_source_assets_kind の許容値)。
_PDF_KINDS = ("pdf", "arxiv_pdf", "pdf_upload", "extension_capture")
_PDF_VARIANT_KINDS = {
    "source": _PDF_KINDS,
    "translated": ("translated_pdf",),
}


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


# --- reingest 用の origin-aware payload 構築 ----------------------------------------


async def _reingest_source_payload(db: DbDep, paper: Paper) -> dict[str, Any]:
    """再取り込みジョブの payload から、取り込み元(origin)依存部分を復元する。

    初回取り込みは元 URL/kind を知っているが、reingest はそれを保存された事実
    (``papers.arxiv_id`` / ``paper_external_ids`` / ``source_assets``)から再構成する
    必要がある。arXiv 前提で固定していたため、PMC/ACL 等の site 論文や PDF アップロード
    論文の再取り込みが worker で ``not a recognizable arxiv id`` になり無限リトライして
    いた(P3: 黙って壊れない)。ここで origin ごとに worker が期待する payload 形状
    (ingest.py の初回取り込みと同一)へ分岐する。

    - arXiv (``papers.arxiv_id`` あり): ``source="arxiv"`` + ``arxiv_id``。
    - site (``paper_external_ids`` あり): ``source="site"`` + ``site``/``external_id``/
      ``landing_url``。PMC 等 JATS 候補を持つサイトは ``source_format="jats"`` を付す
      (worker の is_jats 経路が S3 の先行保存 JATS を品質 A で構造化する)。
    - PDF アップロード(上記いずれも無く原本 PDF 資産だけがある): ``source="pdf_upload"``。
    """

    if paper.arxiv_id:
        return {"source": "arxiv", "arxiv_id": paper.arxiv_id, "url": None}

    external = (
        (
            await db.execute(
                select(PaperExternalId).where(PaperExternalId.paper_id == str(paper.id))
            )
        )
        .scalars()
        .first()
    )
    if external is not None:
        payload: dict[str, Any] = {
            "source": "site",
            "site": external.site,
            "external_id": external.external_id,
            "landing_url": external.canonical_url or None,
        }
        # JATS 品質 A 経路を持つサイト(PMC)は worker の is_jats 経路を起動する
        # (初回取り込みで S3 へ先行保存済みの jats.xml をそのまま再構造化する)。
        if "jats" in site_source_candidates(external.site):
            payload["source_format"] = "jats"
        return payload

    # arxiv_id も外部識別子も無い → PDF アップロード由来。worker は先行保存の原本 PDF
    # だけを読む(source_candidates.py の pdf_upload 経路)。
    return {"source": "pdf_upload"}


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

    payload: dict[str, Any] = {
        "mode": "reingest",
        "library_item_id": library_item_id,
        **await _reingest_source_payload(db, paper),
    }
    store = JobStore(db)
    try:
        job_id = await store.enqueue(
            kind="ingest",
            payload=payload,
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


# --- 未読込図のオンデマンド素材化 (§figure-limit block degradation) --------------------


def _deferred_figure_ids(revision: DocumentRevision | None) -> list[str]:
    """図数上限を超えて縮退した未読込図の block_id を、本文順に返す。"""

    if revision is None or not isinstance(revision.stats, dict):
        return []
    failures = revision.stats.get("figure_asset_failures")
    if not isinstance(failures, list):
        return []
    return [
        str(item["figure_id"])
        for item in failures
        if isinstance(item, dict)
        and item.get("code") == "figure_deferred"
        and "figure_id" in item
    ]


def _materialized_figure_count(revision: DocumentRevision | None) -> int:
    """既に素材化済みの図数(figure_asset_manifest の要素数)。"""

    if revision is None or not isinstance(revision.stats, dict):
        return 0
    manifest = revision.stats.get("figure_asset_manifest")
    return len(manifest) if isinstance(manifest, list) else 0


async def _enqueue_figure_expansion(
    db: DbDep,
    wakeup: JobWakeupDep,
    *,
    paper: Paper,
    library_item_id: str | None,
    user_id: str,
    figure_limit: int,
) -> str:
    """図数上限を引き上げた再取り込みジョブを起こし job_id を返す。"""

    if await _active_ingest_job(db, str(paper.id)) is not None:
        raise ProblemException("conflict", detail="同一 Paper の取り込みが実行中です")
    payload: dict[str, Any] = {
        "mode": "reingest",
        "library_item_id": library_item_id,
        "figure_limit": figure_limit,
        **await _reingest_source_payload(db, paper),
    }
    store = JobStore(db)
    try:
        job_id = await store.enqueue(
            kind="ingest",
            payload=payload,
            priority="bulk",
            user_id=user_id,
            paper_id=str(paper.id),
            library_item_id=library_item_id,
        )
    except IntegrityError:
        await db.rollback()
        raise ProblemException("conflict", detail="同一 Paper の取り込みが実行中です") from None
    await wakeup(job_id)
    return job_id


@router.post(
    "/api/library-items/{library_item_id}/figures/{block_id}/materialize",
    response_model=FigureMaterializeResponse,
    status_code=202,
    operation_id="figures_materialize_deferred",
)
async def materialize_deferred_figure(
    library_item_id: str,
    block_id: str,
    user: CurrentUser,
    db: DbDep,
    wakeup: JobWakeupDep,
) -> FigureMaterializeResponse:
    """未読込(deferred)の1図をオンデマンドで素材化する(図数上限を必要分だけ拡張)。"""

    item = await resolve_owned_library_item(db, library_item_id, user)
    paper = await db.get(Paper, str(item.paper_id))
    if paper is None:
        raise ProblemException("not_found")
    revision = await get_latest_paper_revision(db, paper)
    deferred = _deferred_figure_ids(revision)
    if block_id not in deferred:
        # 既に素材化済み、または deferred でない block → 何もしない(冪等)。
        return FigureMaterializeResponse(job_id=None, already_materialized=True)
    # 対象図を含む位置まで上限を広げる。deferred は本文順なので index+1 分を追加する。
    include_through = deferred.index(block_id) + 1
    figure_limit = _materialized_figure_count(revision) + include_through
    job_id = await _enqueue_figure_expansion(
        db,
        wakeup,
        paper=paper,
        library_item_id=str(item.id),
        user_id=str(user.id),
        figure_limit=figure_limit,
    )
    return FigureMaterializeResponse(job_id=job_id, figure_limit=figure_limit)


@router.post(
    "/api/library-items/{library_item_id}/figures/materialize-batch",
    response_model=FigureMaterializeResponse,
    status_code=202,
    operation_id="figures_materialize_batch",
)
async def materialize_deferred_figures_batch(
    library_item_id: str,
    body: FigureMaterializeBatchRequest,
    user: CurrentUser,
    db: DbDep,
    wakeup: JobWakeupDep,
) -> FigureMaterializeResponse:
    """未読込図を本文順に ``count`` 件、まとめて素材化する(段階的な上限拡張)。"""

    item = await resolve_owned_library_item(db, library_item_id, user)
    paper = await db.get(Paper, str(item.paper_id))
    if paper is None:
        raise ProblemException("not_found")
    revision = await get_latest_paper_revision(db, paper)
    deferred = _deferred_figure_ids(revision)
    if not deferred:
        return FigureMaterializeResponse(job_id=None, already_materialized=True)
    count = max(1, min(body.count, len(deferred)))
    figure_limit = _materialized_figure_count(revision) + count
    job_id = await _enqueue_figure_expansion(
        db,
        wakeup,
        paper=paper,
        library_item_id=str(item.id),
        user_id=str(user.id),
        figure_limit=figure_limit,
    )
    return FigureMaterializeResponse(job_id=job_id, figure_limit=figure_limit)


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
    paper_id: str,
    user: CurrentUser,
    db: DbDep,
    storage: StorageDep,
    variant: str = Query(default="source", pattern="^(source|translated)$"),
    style: str = Query(default="natural", pattern="^(natural|literal)$"),
) -> Response:
    paper = await db.get(Paper, paper_id)
    if paper is None:
        raise ProblemException("not_found")
    await assert_paper_access(db, paper, str(user.id))

    kinds = _PDF_VARIANT_KINDS.get(variant, _PDF_KINDS)
    conditions = [SourceAsset.paper_id == paper_id, SourceAsset.kind.in_(kinds)]
    revision = await get_latest_paper_revision(db, paper)
    canonical_key: str | None = None
    if variant == "translated":
        if revision is not None:
            conditions.append(SourceAsset.source_version == revision.source_version)
            tset = await find_effective_set(db, str(revision.id), style, str(user.id))
            if tset is None:
                raise ProblemException("not_found")
            canonical_key = StorageKeys.translated_pdf(
                paper_id,
                revision.source_version,
                style,
                translation_set_id=(str(tset.id) if tset.scope == "personal" else None),
            )
            conditions.append(SourceAsset.storage_key == canonical_key)
        else:
            # Legacy rows without a current revision predate translation-set-scoped PDFs.
            conditions.append(SourceAsset.storage_key.endswith(f"/translated-{style}.pdf"))
    elif revision is not None:
        conditions.append(SourceAsset.source_version == revision.source_version)
        canonical_key = StorageKeys.original_pdf(paper_id, revision.source_version)
    ordering = (
        (
            (SourceAsset.storage_key == canonical_key).desc(),
            SourceAsset.created_at.desc(),
            SourceAsset.id.asc(),
        )
        if canonical_key is not None
        else (SourceAsset.created_at.desc(), SourceAsset.id.asc())
    )
    asset = (
        (await db.execute(select(SourceAsset).where(*conditions).order_by(*ordering).limit(1)))
        .scalars()
        .first()
    )
    if asset is None and variant == "source" and revision is not None:
        # 不整合フォールバック: プレースホルダ生成時点の source_version(要求時のエイリアス
        # 'latest' 等)と、その後 worker が確定させた実バージョンがずれている既存行の救済(§4.4)。
        # まず Paper.latest_version で解決し直し、それでも見つからなければ当該論文の最新の
        # PDF 資産へ後退する。所有チェックは上の assert_paper_access 済み、kind は
        # _PDF_KINDS のまま絞り込むので translated 資産は対象外(provenance は変えない)。
        fallback_conditions = [SourceAsset.paper_id == paper_id, SourceAsset.kind.in_(kinds)]
        if paper.latest_version and paper.latest_version != revision.source_version:
            asset = (
                (
                    await db.execute(
                        select(SourceAsset)
                        .where(
                            *fallback_conditions,
                            SourceAsset.source_version == paper.latest_version,
                        )
                        .order_by(SourceAsset.created_at.desc(), SourceAsset.id.asc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
        if asset is None:
            asset = (
                (
                    await db.execute(
                        select(SourceAsset)
                        .where(*fallback_conditions)
                        .order_by(SourceAsset.created_at.desc(), SourceAsset.id.asc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
    if asset is None:
        raise ProblemException("not_found")

    data = await storage.get(storage.sources_bucket, asset.storage_key)
    return Response(
        content=data,
        media_type=asset.content_type or "application/pdf",
        headers={
            "Cache-Control": "private, max-age=600",
            "Content-Disposition": f'inline; filename="paper-{variant}.pdf"',
        },
    )


# --- POST /api/library-items/{id}/adopt-revision(§6.8) -----------------------------


class AdoptRevisionRequest(BaseModel):
    revision_id: str


class ReanchorCounts(BaseModel):
    moved: int
    unplaced: int


class AdoptRevisionResponse(BaseModel):
    library_item: LibraryItemSummary
    reanchor: ReanchorCounts


@router.post(
    "/api/library-items/{item_id}/adopt-revision",
    response_model=AdoptRevisionResponse,
    operation_id="library_items_adopt_revision",
)
async def adopt_revision(
    item_id: str, body: AdoptRevisionRequest, user: CurrentUser, db: DbDep
) -> AdoptRevisionResponse:
    """新リビジョンへの切替+リアンカー(§6.8)。自動切替はしない(P6)。

    「新しいバージョンがあります」バナー(arXiv 新版取り込み。plans/05 §7.1)と、B→A 昇格提案
    の適用(通知「変更する」。plans/03 §16.4「adopt-revision と同一の内部処理」)の両方が本
    エンドポイントを唯一の適用経路として使う。``papers.latest_revision_id`` は Paper 単位
    (全ユーザー共通)のため、リアンカーは当該 Paper の全 ``library_items`` を対象にする
    (plans/02 §5.3)。
    """
    item = await resolve_owned_library_item(db, item_id, user)
    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        raise ProblemException("not_found")

    new_revision = await db.get(DocumentRevision, body.revision_id)
    if new_revision is None or str(new_revision.paper_id) != str(paper.id):
        raise ProblemException(
            "validation_error", detail="指定のリビジョンはこの論文のものではありません"
        )

    stats = ReanchorStats()
    current_revision = await get_latest_paper_revision(db, paper)
    if current_revision is None or str(current_revision.id) != str(new_revision.id):
        old_revision_id = str(current_revision.id) if current_revision is not None else None
        paper.latest_revision_id = new_revision.id
        await db.flush()
        if old_revision_id is not None:
            stats = await reanchor_paper(
                db,
                paper_id=str(paper.id),
                old_revision_id=old_revision_id,
                new_revision_id=str(new_revision.id),
            )
        await db.commit()

    summary = await _summary_for(db, item)
    return AdoptRevisionResponse(
        library_item=summary, reanchor=ReanchorCounts(moved=stats.moved, unplaced=stats.unplaced)
    )
