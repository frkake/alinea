"""記事公開ルータ(Task 24・記事公開のデータモデルと公開 API)。

生成記事のサニタイズ済みスナップショットを公開する。private 論文の記事は公開不可。
- 所有者用: ``POST/PATCH/DELETE /api/articles/{article_id}/publication``(作成・可視性更新・
  公開解除)。公開解除しても行は残し slug を予約する(visibility='private')。
- 認証不要: ``GET /api/p/{slug}``(公開スナップショット読み取り)。unlisted は robots noindex、
  public は検索索引許可。

スナップショットは :mod:`alinea_core.article.publication` のサニタイザで作る。source quote 本文・
訳文・メモ・チャット・discussion・原論文図は一切含めない(情報漏えい防止)。
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from alinea_core.article.publication import (
    build_paper_meta,
    sanitize_article_blocks,
    sanitize_overview_figure,
)
from alinea_core.article.wire import EvidenceDisplayResolver, ExplainerRef
from alinea_core.db.models import (
    Article,
    ArticleBlock,
    ArticlePublication,
    DocumentRevision,
    ExplainerFigure,
    LibraryItem,
    OverviewFigure,
    Paper,
    User,
)
from alinea_core.db.revisions import get_latest_paper_revision
from alinea_core.document.blocks import DocumentContent
from fastapi import APIRouter, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from alinea_api.deps import CurrentUser, DbDep
from alinea_api.errors import ProblemException
from alinea_api.schemas.publications import (
    PublicArticleOut,
    PublicationCreateRequest,
    PublicationOut,
    PublicationUpdateRequest,
)
from alinea_api.schemas.viewer import asset_url

router = APIRouter(tags=["publications"])


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _iso(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


async def _owned_article(db: AsyncSession, user: User, article_id: str) -> tuple[Article, Paper]:
    """自分の記事とその論文を返す。無ければ 404(所有者以外にも 404 で存在を隠す)。"""
    if not _valid_uuid(article_id):
        raise ProblemException("not_found")
    article = await db.get(Article, article_id)
    if article is None:
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, article.library_item_id)
    if item is None or str(item.user_id) != str(user.id):
        raise ProblemException("not_found")
    paper = await db.get(Paper, item.paper_id)
    if paper is None:
        raise ProblemException("not_found")
    return article, paper


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


async def _overview_snapshot(db: AsyncSession, article_id: str) -> dict[str, object] | None:
    row = (
        await db.execute(
            select(OverviewFigure).where(
                OverviewFigure.article_id == article_id, OverviewFigure.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return sanitize_overview_figure(
        {
            "dsl": row.dsl,
            "svg_url": f"/api/overview-figures/{row.id}/versions/{row.version}/svg"
            if row.render_mode == "svg"
            else None,
            "raster_url": asset_url(row.image_storage_key) if row.render_mode == "raster" else None,
        }
    )


async def _build_snapshot(
    db: AsyncSession, article: Article, paper: Paper
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """記事の現行状態からサニタイズ済み blocks + paper_meta を組む。"""
    revision: DocumentRevision | None = None
    if paper.latest_revision_id is not None:
        revision = await get_latest_paper_revision(db, paper)
    resolver: EvidenceDisplayResolver | None = None
    if revision is not None:
        resolver = EvidenceDisplayResolver(DocumentContent.model_validate(revision.content))

    explainer_lookup = await _explainer_lookup(db, str(article.id))
    raw_blocks = [
        {
            "type": b.type,
            "content": b.content or {},
            "evidence_anchors": b.evidence_anchors or [],
        }
        for b in await _current_blocks(db, str(article.id))
    ]
    blocks = sanitize_article_blocks(
        raw_blocks,
        resolver=resolver,
        explainer_lookup=explainer_lookup,
        paper_title=paper.title,
    )
    overview = await _overview_snapshot(db, str(article.id))
    if overview is not None:
        blocks.insert(0, overview)

    authors = [
        str(a.get("name", a)) if isinstance(a, dict) else str(a) for a in (paper.authors or [])
    ]
    paper_meta = build_paper_meta(
        title=paper.title,
        authors=authors,
        arxiv_id=paper.arxiv_id,
        doi=paper.doi,
        venue=paper.venue,
        published_on=_iso(paper.published_on),
        license=paper.license,
    )
    return blocks, paper_meta


def _slugify(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return base or "article"


async def _unique_slug(db: AsyncSession, base: str) -> str:
    """base slug に短いランダム接尾辞を付けて衝突しない slug を返す。"""
    for _ in range(6):
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
        exists = (
            await db.execute(
                select(ArticlePublication.id).where(ArticlePublication.slug == candidate)
            )
        ).scalar_one_or_none()
        if exists is None:
            return candidate
    return f"{base}-{uuid.uuid4().hex}"


def _publication_out(pub: ArticlePublication) -> PublicationOut:
    return PublicationOut(
        id=str(pub.id),
        article_id=str(pub.article_id),
        slug=pub.slug,
        visibility=pub.visibility,
        snapshot_version=pub.snapshot_version,
        title=pub.title,
        published_at=_iso(pub.published_at),
        updated_at=_iso(pub.updated_at),
    )


# ---------------------------------------------------------------------------
# 所有者用: 作成 / 更新 / 公開解除
# ---------------------------------------------------------------------------
@router.post(
    "/api/articles/{article_id}/publication",
    response_model=PublicationOut,
    operation_id="publications_create",
)
async def create_publication(
    article_id: str,
    body: PublicationCreateRequest,
    user: CurrentUser,
    db: DbDep,
    response: Response,
) -> PublicationOut:
    article, paper = await _owned_article(db, user, article_id)

    # private 論文の記事は公開できない(情報漏えい防止の第一の関門)。
    if paper.visibility != "public":
        raise ProblemException("forbidden", detail="非公開論文の記事は公開できません")

    blocks, paper_meta = await _build_snapshot(db, article, paper)

    existing = (
        await db.execute(
            select(ArticlePublication).where(ArticlePublication.article_id == str(article.id))
        )
    ).scalar_one_or_none()

    if existing is not None:
        # 既存(公開中 or 予約中)を再公開・更新する。slug は予約済みのものを再利用する。
        if body.slug is not None and body.slug != existing.slug:
            raise ProblemException("conflict", detail="この記事は既に slug を予約済みです")
        existing.visibility = body.visibility
        existing.snapshot_version = article.version
        existing.title = article.title
        existing.paper_meta = paper_meta
        existing.blocks = blocks
        if existing.published_at is None:
            existing.published_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(existing)
        response.status_code = 200
        return _publication_out(existing)

    # 新規公開。明示 slug は衝突時 409、省略時はサーバ採番。
    if body.slug is not None:
        taken = (
            await db.execute(
                select(ArticlePublication.id).where(ArticlePublication.slug == body.slug)
            )
        ).scalar_one_or_none()
        if taken is not None:
            raise ProblemException("conflict", detail="この slug は既に使われています")
        slug = body.slug
    else:
        slug = await _unique_slug(db, _slugify(article.title))

    pub = ArticlePublication(
        id=str(uuid.uuid4()),
        article_id=str(article.id),
        user_id=str(user.id),
        slug=slug,
        visibility=body.visibility,
        snapshot_version=article.version,
        title=article.title,
        paper_meta=paper_meta,
        blocks=blocks,
        published_at=datetime.now(UTC),
    )
    db.add(pub)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ProblemException("conflict", detail="公開に失敗しました(重複)") from exc
    await db.refresh(pub)
    response.status_code = 201
    return _publication_out(pub)


@router.patch(
    "/api/articles/{article_id}/publication",
    response_model=PublicationOut,
    operation_id="publications_update",
)
async def update_publication(
    article_id: str,
    body: PublicationUpdateRequest,
    user: CurrentUser,
    db: DbDep,
) -> PublicationOut:
    article, _paper = await _owned_article(db, user, article_id)
    pub = (
        await db.execute(
            select(ArticlePublication).where(ArticlePublication.article_id == str(article.id))
        )
    ).scalar_one_or_none()
    # 公開中(unlisted/public)でなければ更新対象なし。
    if pub is None or pub.visibility not in ("unlisted", "public"):
        raise ProblemException("not_found")
    pub.visibility = body.visibility
    await db.commit()
    await db.refresh(pub)
    return _publication_out(pub)


@router.delete(
    "/api/articles/{article_id}/publication",
    status_code=204,
    operation_id="publications_unpublish",
)
async def unpublish(article_id: str, user: CurrentUser, db: DbDep) -> Response:
    article, _paper = await _owned_article(db, user, article_id)
    pub = (
        await db.execute(
            select(ArticlePublication).where(ArticlePublication.article_id == str(article.id))
        )
    ).scalar_one_or_none()
    if pub is None or pub.visibility not in ("unlisted", "public"):
        raise ProblemException("not_found")
    # slug を予約したまま非公開化する(リンク乗っ取り防止)。行は残す。
    pub.visibility = "private"
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 認証不要: slug 読み取り
# ---------------------------------------------------------------------------
@router.get(
    "/api/p/{slug}",
    response_model=PublicArticleOut,
    operation_id="publications_read_by_slug",
)
async def read_by_slug(slug: str, db: DbDep, response: Response) -> PublicArticleOut:
    pub = (
        await db.execute(select(ArticlePublication).where(ArticlePublication.slug == slug))
    ).scalar_one_or_none()
    # private(公開解除済み・予約)や不在は 404 で内容も存在も晒さない。
    if pub is None or pub.visibility not in ("unlisted", "public"):
        raise ProblemException("not_found")

    noindex = pub.visibility != "public"
    if noindex:
        response.headers["X-Robots-Tag"] = "noindex"
    return PublicArticleOut(
        slug=pub.slug,
        title=pub.title,
        visibility=pub.visibility,
        snapshot_version=pub.snapshot_version,
        noindex=noindex,
        paper_meta=pub.paper_meta or {},
        blocks=pub.blocks or [],
        published_at=_iso(pub.published_at),
    )


__all__ = ["router"]
