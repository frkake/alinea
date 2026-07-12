"""articles ルータ — 記事ビュー(plans/03 §19)。

- 生成・再生成・ブロック書き直しは ``jobs(kind='article')`` へ委譲する(実行は
  :mod:`alinea_worker.tasks.generate_article`。202 を返し、クライアントは
  ``GET /api/jobs/{job_id}`` をポーリングする)。
- LLM 呼び出しは worker 側の ``ctx['router']`` 経由(本ルータは呼ばない)。クォータ判定
  (``check_quota(task='article')``)のみ enqueue 前に行う。
- ``content`` の wire 形変換(DB のフラット保存形 → plans/03 §19.1 のネスト形)は
  :mod:`alinea_core.article.wire` に委譲する(worker の ``jobs.result`` と同じ変換)。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import structlog
from alinea_core.article import (
    PRESET_INCLUDE_MATH_DEFAULT,
    EvidenceDisplayResolver,
    ExplainerRef,
    build_disclaimer,
    parse_article_block_pk,
)
from alinea_core.article.storage_keys import article_snapshot_key, article_versions_cache_key
from alinea_core.article.wire import build_article_block_wire
from alinea_core.db.models import (
    Article,
    ArticleBlock,
    DocumentRevision,
    ExplainerFigure,
    LibraryItem,
    OverviewFigure,
    Paper,
    User,
)
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.document.blocks import DocumentContent
from alinea_core.document.plaintext import article_block_to_plain
from alinea_core.jobs.store import JobStore
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends
from selectolax.parser import HTMLParser
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_api.deps import CurrentUser, DbDep, RedisDep, SettingsDep
from alinea_api.errors import ProblemException
from alinea_api.llm.deps import check_quota
from alinea_api.routers.papers import StorageDep
from alinea_api.routers.viewer import resolve_owned_library_item
from alinea_api.schemas.articles import (
    ArticleBlockOut,
    ArticleBlockRewriteRequest,
    ArticleGenerateRequest,
    ArticleJobResponse,
    ArticleOut,
    ArticleRegenerateRequest,
    ArticleVersionItemOut,
    ArticleVersionsResponse,
    Preset,
)
from alinea_api.schemas.viewer import asset_url

router = APIRouter(tags=["articles"])
log = structlog.get_logger("alinea.api.articles")

# plans/01 §4.3(apps/worker/settings.INTERACTIVE_QUEUE と同値。apps 間 import 禁止のため
# 定数で持つ)。
_INTERACTIVE_QUEUE = "alinea:interactive"


# ---------------------------------------------------------------------------
# 起床通知(テストで差し替え可能。apps/api/routers/vocab.py の get_vocab_job_wakeup と同方針)
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


def get_articles_job_wakeup(settings: SettingsDep) -> JobWakeup:
    """arq への起床通知を返す。失敗しても enqueue 自体は成功させる(DB が真実)。"""

    async def wakeup(job_id: str) -> None:
        try:
            await _default_wakeup(settings.redis_url, job_id)
        except Exception:
            await log.awarning("article_wakeup_failed", job_id=job_id)

    return wakeup


ArticlesJobWakeupDep = Annotated[JobWakeup, Depends(get_articles_job_wakeup)]


# ---------------------------------------------------------------------------
# 所有チェック・コンテキスト読み込み
# ---------------------------------------------------------------------------
def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


async def _articles_for_item(db: AsyncSession, library_item_id: str) -> list[Article]:
    rows = (
        (
            await db.execute(
                select(Article)
                .where(Article.library_item_id == library_item_id)
                .order_by(Article.generated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _article_for_item(
    db: AsyncSession, library_item_id: str, preset: Preset | None = None
) -> Article | None:
    stmt = select(Article).where(Article.library_item_id == library_item_id)
    if preset is not None:
        stmt = stmt.where(Article.preset == preset)
    else:
        stmt = stmt.order_by(Article.generated_at.desc()).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


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


async def _paper_and_revision(
    db: AsyncSession, item: LibraryItem
) -> tuple[Paper, DocumentRevision]:
    paper = await db.get(Paper, item.paper_id)
    if paper is None or paper.latest_revision_id is None:
        raise ProblemException("not_found")
    revision = await get_latest_paper_revision(db, paper)
    if revision is None:
        raise ProblemException("not_found")
    return paper, revision


async def _current_blocks(db: AsyncSession, article_id: str) -> list[ArticleBlock]:
    rows = (
        (
            await db.execute(
                select(ArticleBlock)
                .where(ArticleBlock.article_id == article_id)
                .order_by(ArticleBlock.position.asc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _explainer_lookup(db: AsyncSession, article_id: str) -> dict[int, ExplainerRef]:
    rows = (
        (
            await db.execute(
                select(ExplainerFigure).where(
                    ExplainerFigure.article_id == article_id,
                    ExplainerFigure.is_current.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        row.slot: ExplainerRef(
            figure_id=str(row.id),
            image_url=asset_url(row.image_storage_key) or "",
            caption=row.caption,
        )
        for row in rows
    }


async def _overview_figure_ref(db: AsyncSession, article_id: str) -> dict[str, Any] | None:
    """全体概要図(M2-05 の担当)。未生成の間は常に null。"""
    row = (
        await db.execute(
            select(OverviewFigure).where(
                OverviewFigure.article_id == article_id, OverviewFigure.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "id": str(row.id),
        "version": row.version,
        "generated_at": row.generated_at.isoformat(),
        "svg_url": f"/api/overview-figures/{row.id}/versions/{row.version}/svg",
        "raster_url": asset_url(row.image_storage_key) if row.render_mode == "raster" else None,
        "evidence": row.evidence_anchors or [],
        "dsl": row.dsl,
    }


async def _build_article_out(
    db: AsyncSession, article: Article, revision: DocumentRevision
) -> ArticleOut:
    blocks = await _current_blocks(db, str(article.id))
    document = DocumentContent.model_validate(revision.content)
    resolver = EvidenceDisplayResolver(document)
    source_blocks = {block.id: block for _section, block in document.iter_blocks()}
    explainer_lookup = await _explainer_lookup(db, str(article.id))
    out_blocks = [
        ArticleBlockOut.model_validate(
            build_article_block_wire(
                pk=b.id,
                type_=b.type,
                content=_enrich_figure_content(b.content or {}, source_blocks)
                if b.type == "figure_embed"
                else (b.content or {}),
                evidence_anchors=b.evidence_anchors or [],
                origin=b.origin,
                resolver=resolver,
                explainer_lookup=explainer_lookup,
            )
        )
        for b in blocks
    ]
    overview = await _overview_figure_ref(db, str(article.id))
    variants = await _articles_for_item(db, str(article.library_item_id))
    return ArticleOut(
        id=str(article.id),
        library_item_id=str(article.library_item_id),
        title=article.title,
        preset=article.preset,
        include_math=article.include_math,
        version=article.version,
        generated_at=article.generated_at.isoformat(),
        disclaimer=build_disclaimer(article.generated_at),
        available_presets=[row.preset for row in variants],
        overview_figure=overview,
        blocks=out_blocks,
    )


def _enrich_figure_content(
    content: dict[str, Any], source_blocks: dict[str, Any]
) -> dict[str, Any]:
    block_id = str(content.get("figure_block_id", ""))
    source = source_blocks.get(block_id)
    if source is None:
        return content
    enriched = {**content, "kind": source.type}
    if source.type != "table" or content.get("table_rows") or not source.raw:
        return enriched
    rows: list[list[str]] = []
    for row in HTMLParser(source.raw).css("tr"):
        cells = [
            re.sub(
                r"\\[A-Za-z]+",
                "",
                " ".join(cell.text(separator=" ", strip=True).split()),
            ).strip()
            for cell in row.css("th, td")
        ]
        if cells:
            rows.append(cells)
    return {**enriched, "table_rows": rows or None}


# ---------------------------------------------------------------------------
# §19.1 GET(記事取得)
# ---------------------------------------------------------------------------
@router.get(
    "/api/library-items/{item_id}/article",
    response_model=ArticleOut,
    operation_id="articles_get",
)
async def get_article(
    item_id: str, user: CurrentUser, db: DbDep, preset: Preset | None = None
) -> ArticleOut:
    item = await resolve_owned_library_item(db, item_id, user)
    article = await _article_for_item(db, str(item.id), preset)
    if article is None:
        raise ProblemException("not_found")
    _paper, revision = await _paper_and_revision(db, item)
    return await _build_article_out(db, article, revision)


# ---------------------------------------------------------------------------
# §19.2 POST(初回生成)
# ---------------------------------------------------------------------------
@router.post(
    "/api/library-items/{item_id}/article",
    response_model=ArticleJobResponse,
    status_code=202,
    operation_id="articles_generate",
)
async def generate_article(
    item_id: str,
    body: ArticleGenerateRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    wakeup: ArticlesJobWakeupDep,
) -> ArticleJobResponse:
    item = await resolve_owned_library_item(db, item_id, user)
    existing = await _article_for_item(db, str(item.id), body.preset)
    if existing is not None:
        raise ProblemException("conflict", detail="記事は既に生成されています")
    # 論文に取り込み済みリビジョンが無ければ素材収集できない(§4.2)。
    await _paper_and_revision(db, item)

    await check_quota(db, str(user.id), "article", settings=settings, cache=r)

    include_math = (
        body.include_math
        if body.include_math is not None
        else PRESET_INCLUDE_MATH_DEFAULT[body.preset]
    )
    store = JobStore(db)
    job_id = await store.enqueue(
        kind="article",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=str(item.id),
        payload={
            "op": "generate",
            "library_item_id": str(item.id),
            "preset": body.preset,
            "include_math": include_math,
        },
    )
    await wakeup(job_id)
    return ArticleJobResponse(job_id=job_id)


# ---------------------------------------------------------------------------
# §19.3 POST(✦ 指示つき再生成)
# ---------------------------------------------------------------------------
@router.post(
    "/api/articles/{article_id}/regenerate",
    response_model=ArticleJobResponse,
    status_code=202,
    operation_id="articles_regenerate",
)
async def regenerate_article(
    article_id: str,
    body: ArticleRegenerateRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    wakeup: ArticlesJobWakeupDep,
) -> ArticleJobResponse:
    article, item = await _owned_article(db, user, article_id)
    await check_quota(db, str(user.id), "article", settings=settings, cache=r)

    if body.preset is not None and body.preset != article.preset:
        existing_variant = await _article_for_item(db, str(item.id), body.preset)
        if existing_variant is not None:
            raise ProblemException("conflict", detail="この読者タイプの記事は既にあります")

    payload: dict[str, Any] = {"op": "regenerate", "article_id": str(article.id)}
    if body.instruction:
        payload["instruction"] = body.instruction
    if body.preset is not None:
        payload["preset"] = body.preset
    if body.include_math is not None:
        payload["include_math"] = body.include_math

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="article",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=str(item.id),
        article_id=str(article.id),
        payload=payload,
    )
    await wakeup(job_id)
    return ArticleJobResponse(job_id=job_id)


# ---------------------------------------------------------------------------
# §19.4 版管理
# ---------------------------------------------------------------------------
@router.get(
    "/api/articles/{article_id}/versions",
    response_model=ArticleVersionsResponse,
    operation_id="articles_list_versions",
)
async def list_versions(
    article_id: str, user: CurrentUser, db: DbDep, r: RedisDep
) -> ArticleVersionsResponse:
    article, _item = await _owned_article(db, user, article_id)
    raw = await r.lrange(  # type: ignore[misc]  # redis-py: sync/async union
        article_versions_cache_key(str(article.id)), 0, -1
    )
    items: list[ArticleVersionItemOut] = []
    for entry in raw:
        try:
            data = json.loads(entry)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            items.append(ArticleVersionItemOut(**data))
    items.sort(key=lambda i: i.version, reverse=True)
    return ArticleVersionsResponse(items=items)


@router.post(
    "/api/articles/{article_id}/versions/{version}/restore",
    response_model=ArticleOut,
    operation_id="articles_restore_version",
)
async def restore_version(
    article_id: str,
    version: int,
    user: CurrentUser,
    db: DbDep,
    r: RedisDep,
    storage: StorageDep,
) -> ArticleOut:
    article, item = await _owned_article(db, user, article_id)
    key = article_snapshot_key(str(article.id), version)
    try:
        raw = await storage.get(storage.assets_bucket, key)
    except ClientError as exc:  # S3 が版を持たない(不存在)場合(§4.6)
        raise ProblemException("not_found", detail="指定の版が見つかりません") from exc
    snapshot = json.loads(raw.decode("utf-8"))

    await db.execute(delete(ArticleBlock).where(ArticleBlock.article_id == article.id))
    await db.flush()
    rows = []
    for blk in snapshot.get("blocks", []):
        content = blk.get("content", {})
        rows.append(
            ArticleBlock(
                article_id=str(article.id),
                position=blk.get("position", 0),
                type=blk.get("type", "paragraph"),
                content=content,
                text_plain=article_block_to_plain(blk.get("type", "paragraph"), content),
                evidence_anchors=blk.get("evidence_anchors", []),
                origin=blk.get("origin", "ai"),
            )
        )
    db.add_all(rows)

    article.title = snapshot.get("title", article.title)
    article.preset = snapshot.get("preset", article.preset)
    article.include_math = bool(snapshot.get("include_math", article.include_math))
    article.version = article.version + 1
    await db.flush()
    await db.refresh(article)

    new_snapshot = {
        "version": article.version,
        "generated_at": article.generated_at.isoformat(),
        "preset": article.preset,
        "include_math": article.include_math,
        "instruction": snapshot.get("instruction"),
        "title": article.title,
        "blocks": snapshot.get("blocks", []),
    }
    await storage.put(
        storage.assets_bucket,
        article_snapshot_key(str(article.id), article.version),
        json.dumps(new_snapshot, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )
    try:
        await r.rpush(  # type: ignore[misc]  # redis-py: sync/async union
            article_versions_cache_key(str(article.id)),
            json.dumps(
                {
                    "version": article.version,
                    "generated_at": new_snapshot["generated_at"],
                    "preset": article.preset,
                    "instruction": new_snapshot["instruction"],
                },
                ensure_ascii=False,
            ),
        )
    except Exception as exc:  # ベストエフォートキャッシュ(§4.6)。失敗しても restore は成立させる。
        await log.awarning("article_version_cache_append_failed", error=str(exc))

    await db.commit()
    _paper, revision = await _paper_and_revision(db, item)
    return await _build_article_out(db, article, revision)


# ---------------------------------------------------------------------------
# §19.5 POST(ブロック書き直し・再生成)
# ---------------------------------------------------------------------------
@router.post(
    "/api/articles/{article_id}/blocks/{block_id}/rewrite",
    response_model=ArticleJobResponse,
    status_code=202,
    operation_id="articles_block_rewrite",
)
async def rewrite_block(
    article_id: str,
    block_id: str,
    body: ArticleBlockRewriteRequest,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    r: RedisDep,
    wakeup: ArticlesJobWakeupDep,
) -> ArticleJobResponse:
    article, item = await _owned_article(db, user, article_id)
    pk = parse_article_block_pk(block_id)
    if pk is None:
        raise ProblemException("not_found")
    block = await db.get(ArticleBlock, pk)
    if block is None or str(block.article_id) != str(article.id):
        raise ProblemException("not_found")
    if block.type == "attribution":
        # 出典ブロックは削除・書き直し不可(plans/03 §19.5)。
        raise ProblemException("forbidden")

    await check_quota(db, str(user.id), "article", settings=settings, cache=r)

    store = JobStore(db)
    job_id = await store.enqueue(
        kind="article",
        priority="interactive",
        user_id=str(user.id),
        library_item_id=str(item.id),
        article_id=str(article.id),
        payload={
            "op": "block_rewrite",
            "article_id": str(article.id),
            "block_pk": pk,
            "instruction": body.instruction,
        },
    )
    await wakeup(job_id)
    return ArticleJobResponse(job_id=job_id)


__all__ = ["router"]
