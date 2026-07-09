"""figures ルータ — 全体概要図・解説図(plans/03 §20)。

- 生成(書き直し・単体再生成)は ``jobs(kind='figure')`` へ委譲する(実行は
  :mod:`alinea_worker.tasks.generate_overview_figure` /
  :mod:`alinea_worker.tasks.generate_explainer_figure`。202 を返し、クライアントは
  ``GET /api/jobs/{job_id}`` をポーリングする)。
- LLM/ImageRouter 呼び出しは worker 側(本ルータは呼ばない)。クォータ判定
  (``check_quota(task='overview_figure_dsl'|'explainer_image')``)のみ enqueue 前に行う。
- 全体概要図は全版を行として保持する(削除しない)ため、版一覧は DB を直接問い合わせる
  (記事本体の版スナップショット(Redis+S3)とは異なる方式。plans/07 §5.3)。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import structlog
from alinea_core.db.models import (
    Article,
    ExplainerFigure,
    LibraryItem,
    OverviewFigure,
    Paper,
    User,
)
from alinea_core.jobs.store import JobStore
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_api.deps import CurrentUser, DbDep, SettingsDep
from alinea_api.errors import ProblemException
from alinea_api.llm.deps import check_quota
from alinea_api.routers.papers import StorageDep
from alinea_api.schemas.figures import (
    ExplainerFigureRegenerateRequest,
    FigureJobResponse,
    OverviewFigureGetOut,
    OverviewFigureRefOut,
    OverviewFigureRewriteRequest,
    OverviewFigureVersionItemOut,
)
from alinea_api.schemas.viewer import asset_url

router = APIRouter(tags=["figures"])
log = structlog.get_logger("alinea.api.figures")

# plans/01 §4.3(apps/worker/settings.INTERACTIVE_QUEUE と同値。
# apps 間 import 禁止のため定数で持つ)。
_INTERACTIVE_QUEUE = "alinea:interactive"


# ---------------------------------------------------------------------------
# 起床通知(テストで差し替え可能。apps/api/routers/articles.py と同方針)
# ---------------------------------------------------------------------------
JobWakeup = Callable[[str], Awaitable[None]]


async def _default_wakeup(redis_url: str, job_id: str) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        await pool.enqueue_job("run_job", job_id, _queue_name=_INTERACTIVE_QUEUE)
    finally:
        await pool.aclose()


def get_figures_job_wakeup(settings: SettingsDep) -> JobWakeup:
    """arq への起床通知を返す。失敗しても enqueue 自体は成功させる(DB が真実)。"""

    async def wakeup(job_id: str) -> None:
        try:
            await _default_wakeup(settings.redis_url, job_id)
        except Exception:
            await log.awarning("figures_wakeup_failed", job_id=job_id)

    return wakeup


FiguresJobWakeupDep = Annotated[JobWakeup, Depends(get_figures_job_wakeup)]


# ---------------------------------------------------------------------------
# 所有チェック・コンテキスト読み込み
# ---------------------------------------------------------------------------
def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _owned_article(db: DbDep, user: User, article_id: str) -> tuple[Article, LibraryItem]:
    if not _valid_uuid(article_id):
        raise ProblemException("not_found")
    article = await db.get(Article, article_id)
    if article is None:
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, article.library_item_id)
    if item is None or str(item.user_id) != str(user.id):
        raise ProblemException("not_found")
    return article, item


async def _owned_overview_figure(
    db: DbDep, user: User, figure_id: str
) -> tuple[OverviewFigure, Article, LibraryItem]:
    if not _valid_uuid(figure_id):
        raise ProblemException("not_found")
    figure = await db.get(OverviewFigure, figure_id)
    if figure is None:
        raise ProblemException("not_found")
    article, item = await _owned_article(db, user, str(figure.article_id))
    return figure, article, item


async def _owned_explainer_figure(
    db: DbDep, user: User, figure_id: str
) -> tuple[ExplainerFigure, Article, LibraryItem]:
    if not _valid_uuid(figure_id):
        raise ProblemException("not_found")
    figure = await db.get(ExplainerFigure, figure_id)
    if figure is None:
        raise ProblemException("not_found")
    article, item = await _owned_article(db, user, str(figure.article_id))
    return figure, article, item


async def _current_overview_figure(db: AsyncSession, article_id: str) -> OverviewFigure | None:
    return (
        await db.execute(
            select(OverviewFigure).where(
                OverviewFigure.article_id == article_id, OverviewFigure.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()


async def _all_versions(db: AsyncSession, article_id: str) -> list[OverviewFigureVersionItemOut]:
    rows = (
        (
            await db.execute(
                select(OverviewFigure)
                .where(OverviewFigure.article_id == article_id)
                .order_by(OverviewFigure.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        OverviewFigureVersionItemOut(version=r.version, generated_at=r.generated_at.isoformat())
        for r in rows
    ]


def _overview_figure_ref(row: OverviewFigure) -> OverviewFigureRefOut:
    evidence = [
        {"display": e.get("display", ""), "anchor": e.get("anchor", {})}
        for e in (row.evidence_anchors or [])
        if isinstance(e, dict)
    ]
    return OverviewFigureRefOut.model_validate(
        {
            "id": str(row.id),
            "version": row.version,
            "generated_at": row.generated_at.isoformat(),
            "svg_url": f"/api/overview-figures/{row.id}/versions/{row.version}/svg",
            "raster_url": asset_url(row.image_storage_key) if row.render_mode == "raster" else None,
            "evidence": evidence,
            "dsl": row.dsl,
        }
    )


async def _download_filename(db: AsyncSession, article: Article, version: int) -> str:
    item = await db.get(LibraryItem, article.library_item_id)
    slug = ""
    if item is not None:
        paper = await db.get(Paper, item.paper_id)
        if paper is not None:
            slug = paper.arxiv_id or str(paper.id)
    return f"alinea-overview-{slug or 'paper'}-v{version}.svg"


# ---------------------------------------------------------------------------
# §20.1 GET(全体概要図取得)
# ---------------------------------------------------------------------------
@router.get(
    "/api/articles/{article_id}/overview-figure",
    response_model=OverviewFigureGetOut,
    operation_id="figures_get_overview",
)
async def get_overview_figure(
    article_id: str, user: CurrentUser, db: DbDep
) -> OverviewFigureGetOut:
    article, _item = await _owned_article(db, user, article_id)
    current = await _current_overview_figure(db, str(article.id))
    if current is None:
        raise ProblemException("not_found")
    ref = _overview_figure_ref(current)
    versions = await _all_versions(db, str(article.id))
    return OverviewFigureGetOut(**ref.model_dump(), versions=versions)


# ---------------------------------------------------------------------------
# §20.1 POST(✦ 書き直し指示)
# ---------------------------------------------------------------------------
@router.post(
    "/api/articles/{article_id}/overview-figure/rewrite",
    response_model=FigureJobResponse,
    status_code=202,
    operation_id="figures_rewrite_overview",
)
async def rewrite_overview_figure(
    article_id: str,
    body: OverviewFigureRewriteRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    wakeup: FiguresJobWakeupDep,
) -> FigureJobResponse:
    article, item = await _owned_article(db, user, article_id)
    current = await _current_overview_figure(db, str(article.id))
    if current is None:
        raise ProblemException("not_found")

    await check_quota(db, str(user.id), "overview_figure_dsl", settings=settings)

    store = JobStore(db)
    payload: dict[str, Any] = {"figure_kind": "overview", "article_id": str(article.id)}
    if body.instruction:
        payload["instruction"] = body.instruction
    job_id = await store.enqueue(
        kind="figure",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=str(item.id),
        article_id=str(article.id),
        payload=payload,
    )
    await wakeup(job_id)
    return FigureJobResponse(job_id=job_id)


# ---------------------------------------------------------------------------
# §20.1 版復元(新行は作らない。is_current の付替えのみ)
# ---------------------------------------------------------------------------
@router.post(
    "/api/articles/{article_id}/overview-figure/versions/{version}/restore",
    response_model=OverviewFigureRefOut,
    operation_id="figures_restore_overview_version",
)
async def restore_overview_figure_version(
    article_id: str, version: int, user: CurrentUser, db: DbDep
) -> OverviewFigureRefOut:
    article, _item = await _owned_article(db, user, article_id)
    target = (
        await db.execute(
            select(OverviewFigure).where(
                OverviewFigure.article_id == article.id, OverviewFigure.version == version
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise ProblemException("not_found", detail="指定の版が見つかりません")

    if not target.is_current:
        current = await _current_overview_figure(db, str(article.id))
        if current is not None and current.id != target.id:
            current.is_current = False
            db.add(current)
            await db.flush()
        target.is_current = True
        db.add(target)
    await db.commit()
    await db.refresh(target)
    return _overview_figure_ref(target)


# ---------------------------------------------------------------------------
# §20.1 SVG 配信・ダウンロード
# ---------------------------------------------------------------------------
@router.get(
    "/api/overview-figures/{figure_id}/versions/{version}/svg",
    operation_id="figures_get_overview_svg",
)
async def get_overview_figure_svg(
    figure_id: str,
    version: int,
    user: CurrentUser,
    db: DbDep,
    storage: StorageDep,
    download: Annotated[bool, Query()] = False,
) -> Response:
    figure, article, _item = await _owned_overview_figure(db, user, figure_id)
    if figure.version != version or not figure.svg_storage_key:
        raise ProblemException("not_found")
    body = await storage.get(storage.assets_bucket, figure.svg_storage_key)
    headers: dict[str, str] = {}
    if download:
        filename = await _download_filename(db, article, version)
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content=body, media_type="image/svg+xml", headers=headers)


# ---------------------------------------------------------------------------
# §20.2 POST(解説図 単体再生成)
# ---------------------------------------------------------------------------
@router.post(
    "/api/explainer-figures/{figure_id}/regenerate",
    response_model=FigureJobResponse,
    status_code=202,
    operation_id="figures_regenerate_explainer",
)
async def regenerate_explainer_figure(
    figure_id: str,
    body: ExplainerFigureRegenerateRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    wakeup: FiguresJobWakeupDep,
) -> FigureJobResponse:
    figure, article, item = await _owned_explainer_figure(db, user, figure_id)
    if not figure.is_current:
        raise ProblemException("conflict", detail="現行版以外は再生成できません")

    await check_quota(db, str(user.id), "explainer_image", settings=settings)

    store = JobStore(db)
    payload: dict[str, Any] = {"figure_kind": "explainer", "figure_id": str(figure.id)}
    if body.instruction:
        payload["instruction"] = body.instruction
    job_id = await store.enqueue(
        kind="figure",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=str(item.id),
        article_id=str(article.id),
        payload=payload,
    )
    await wakeup(job_id)
    return FigureJobResponse(job_id=job_id)


__all__ = ["router"]
