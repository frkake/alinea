"""assets ルータ — 画像・ファイル配信(plans/03 §22.1)。

``GET /api/assets/{asset_id}`` → 302 署名付き GET URL(有効 10 分)。図画像・サムネイル・
生成ラスター・SVG 原データの配信を一元化する。``asset_id`` は assets バケットのキーを
base64url エンコードした不透明トークン(:mod:`yakudoku_api.schemas.assets`)。所有チェックは
キー先頭の paper_id で行う(``figures/{pid}/…`` / ``thumbnails/{pid}/…``)。
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from yakudoku_core.db.models import Paper

from yakudoku_api.deps import CurrentUser, DbDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.routers.papers import StorageDep, assert_paper_access
from yakudoku_api.schemas.assets import decode_asset_id, paper_id_from_key

router = APIRouter(tags=["assets"])


@router.get("/api/assets/{asset_id}", operation_id="assets_get")
async def get_asset(
    asset_id: str,
    user: CurrentUser,
    db: DbDep,
    storage: StorageDep,
    download: bool = Query(default=False),
) -> RedirectResponse:
    key = decode_asset_id(asset_id)
    if key is None:
        raise ProblemException("not_found")
    paper_id = paper_id_from_key(key)
    if paper_id is None:
        # M0 は figures / thumbnails(paper_id 由来)のみ配信する。
        raise ProblemException("not_found")

    paper = await db.get(Paper, paper_id)
    if paper is None:
        raise ProblemException("not_found")
    await assert_paper_access(db, paper, str(user.id))

    # download=true の Content-Disposition 付与は s3.presign_get 未対応のため M0 では未実装。
    url = await storage.presign_get(storage.assets_bucket, key, expires_in=600)
    return RedirectResponse(url, status_code=302)
