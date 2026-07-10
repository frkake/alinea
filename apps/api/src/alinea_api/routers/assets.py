"""assets ルータ — 画像・ファイル配信(plans/03 §22.1)。

``GET /api/assets/{asset_id}`` → assets バケットのオブジェクトを同一オリジンで直接配信する。
図画像・サムネイル・生成ラスター・SVG 原データの配信を一元化する。``asset_id`` は
assets バケットのキーを base64url エンコードした不透明トークン
(:mod:`alinea_api.schemas.assets`)。所有チェックは
原則キー先頭の paper_id で行う(``figures/{pid}/…`` / ``thumbnails/{pid}/…``)。旧 seed/旧
取り込みデータの paper_id 無しキーは revision JSON の ``asset_key`` 参照から逆引きする。
"""

from __future__ import annotations

import posixpath

from alinea_core.db.models import (
    Article,
    DocumentRevision,
    ExplainerFigure,
    LibraryItem,
    OverviewFigure,
    Paper,
)
from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import String, cast, select

from alinea_api.deps import CurrentUser, DbDep
from alinea_api.errors import ProblemException
from alinea_api.routers.papers import StorageDep, assert_paper_access
from alinea_api.schemas.assets import decode_asset_id, paper_id_from_key

router = APIRouter(tags=["assets"])

_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}


def _content_type_for_key(key: str) -> str:
    return _CONTENT_TYPES.get(posixpath.splitext(key)[1].lower(), "application/octet-stream")


def _content_disposition(key: str, download: bool) -> str:
    disposition = "attachment" if download else "inline"
    filename = posixpath.basename(key).replace('"', "")
    return f'{disposition}; filename="{filename or "asset"}"'


async def _authorize_asset_key(key: str, user: CurrentUser, db: DbDep) -> None:
    paper_id = paper_id_from_key(key)
    if paper_id is not None:
        paper = await db.get(Paper, paper_id)
        if paper is None:
            raise ProblemException("not_found")
        await assert_paper_access(db, paper, str(user.id))
        return

    article_owner = await db.scalar(
        select(LibraryItem.user_id)
        .join(Article, Article.library_item_id == LibraryItem.id)
        .join(ExplainerFigure, ExplainerFigure.article_id == Article.id)
        .where(ExplainerFigure.image_storage_key == key)
        .limit(1)
    )
    if article_owner is None:
        article_owner = await db.scalar(
            select(LibraryItem.user_id)
            .join(Article, Article.library_item_id == LibraryItem.id)
            .join(OverviewFigure, OverviewFigure.article_id == Article.id)
            .where(
                (OverviewFigure.image_storage_key == key)
                | (OverviewFigure.svg_storage_key == key)
            )
            .limit(1)
        )
    if article_owner is not None:
        if str(article_owner) != str(user.id):
            raise ProblemException("not_found")
        return

    # 旧データ互換: `figures/fig-1.png` や `fig-1.png` のような paper_id を含まない
    # asset_key は、revision content にその key を持つ論文に限って配信する。
    paper = await db.scalar(
        select(Paper)
        .join(DocumentRevision, DocumentRevision.paper_id == Paper.id)
        .where(cast(DocumentRevision.content, String).contains(f'"asset_key": "{key}"'))
        .limit(1)
    )
    if paper is None:
        raise ProblemException("not_found")
    await assert_paper_access(db, paper, str(user.id))


@router.get("/api/assets/{asset_id}", operation_id="assets_get")
async def get_asset(
    asset_id: str,
    user: CurrentUser,
    db: DbDep,
    storage: StorageDep,
    download: bool = Query(default=False),
) -> Response:
    key = decode_asset_id(asset_id)
    if key is None:
        raise ProblemException("not_found")
    await _authorize_asset_key(key, user, db)

    try:
        body = await storage.get(storage.assets_bucket, key)
    except Exception as exc:
        raise ProblemException("not_found") from exc
    return Response(
        content=body,
        media_type=_content_type_for_key(key),
        headers={
            "Cache-Control": "private, max-age=600",
            "Content-Disposition": _content_disposition(key, download),
        },
    )
