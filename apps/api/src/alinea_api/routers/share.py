"""share — 匿名共有ページ API(plans/03 §14。docs/09 §4・§5.2 のライセンス縮退)。

- ``GET /api/share/collections/{token}``: 認証不要(``anonymous``)。revoked・不存在の
  token は区別せず 404 ``not_found`` を返す(plans/03 §14.1)。
- 応答ヘッダ ``X-Robots-Tag: noindex``(§14.1。HTML 側の ``<meta>`` は apps/web が担う)。
- ライセンス縮退(docs/09 §5.2): ``alinea_core.licenses.classify_license`` の
  ``share_page_bibliography_only`` が真の論文(ライセンス不明/出版社 PDF/アップロード PDF)は
  ``summary_3line`` を null にして書誌のみへ縮退する(書誌自体は常に返す)。
- ``include_notes=False`` の共有では全アイテムで ``shared_note`` を null にする(§13.3 決定:
  共有されるメモは ``LibraryItem.one_line_note`` のみ)。
- 個人資産(進捗・注釈・チャット・リソース・記事・語彙・読書統計)は一切含めない(docs/09 §4)。

レート制限(§1.8 `GET /api/share/collections/{token}` 120 回/分/IP)は
``alinea_api.ratelimit.match_rule`` への専用ルール追加が必要(本タスクの所有外の
共有ファイル。followups に記載)。現状は既定ルール(600 回/分。匿名は IP スコープに
フォールバック)が適用される。

main.py への ``app.include_router(share.router)`` 登録は本タスクの所有外
(main.py は article レーンが編集する取り決め)。followups に登録依頼を明記する。
"""

from __future__ import annotations

from alinea_core.db.models import (
    Collection,
    CollectionEntry,
    CollectionShareToken,
    LibraryItem,
    Paper,
    User,
)
from alinea_core.licenses import classify_license
from fastapi import APIRouter, Response
from sqlalchemy import select

from alinea_api.deps import DbDep
from alinea_api.errors import ProblemException
from alinea_api.schemas.library import author_names, authors_short
from alinea_api.schemas.share import (
    ShareCollectionInfo,
    ShareCollectionItem,
    ShareCollectionResponse,
)

router = APIRouter(tags=["share"])


def _venue_year(paper: Paper) -> str | None:
    """``PaperBib`` の venue/year から表示用の 1 文字列を組み立てる(viewer.py の参考文献の方針)。"""
    year = paper.published_on.year if paper.published_on else None
    venue = paper.venue
    if venue and year:
        return f"{venue} {year}"
    if year:
        return str(year)
    if venue:
        return str(venue)
    return None


def _arxiv_url(paper: Paper) -> str | None:
    return f"https://arxiv.org/abs/{paper.arxiv_id}" if paper.arxiv_id else None


@router.get(
    "/api/share/collections/{token}",
    response_model=ShareCollectionResponse,
    operation_id="share_collections_get",
)
async def get_share_collection(
    token: str, db: DbDep, response: Response
) -> ShareCollectionResponse:
    response.headers["X-Robots-Tag"] = "noindex"

    share = (
        await db.execute(
            select(CollectionShareToken).where(
                CollectionShareToken.token == token,
                CollectionShareToken.status == "active",
            )
        )
    ).scalar_one_or_none()
    if share is None:
        raise ProblemException("not_found")

    collection = await db.get(Collection, share.collection_id)
    if collection is None:
        raise ProblemException("not_found")
    owner = await db.get(User, collection.user_id)

    entries = (
        (
            await db.execute(
                select(CollectionEntry)
                .where(CollectionEntry.collection_id == collection.id)
                .order_by(CollectionEntry.position.asc(), CollectionEntry.id.asc())
            )
        )
        .scalars()
        .all()
    )

    items: list[ShareCollectionItem] = []
    for idx, entry in enumerate(entries, start=1):
        item = await db.get(LibraryItem, entry.library_item_id)
        assert item is not None
        paper = await db.get(Paper, item.paper_id)
        assert paper is not None
        names = author_names(paper.authors)
        policy = classify_license(paper.license)
        summary = None if policy.share_page_bibliography_only else paper.summary_lines
        shared_note = (item.one_line_note or None) if share.include_notes else None
        items.append(
            ShareCollectionItem(
                order=idx,
                title=paper.title,
                authors_short=authors_short(names),
                venue_year=_venue_year(paper),
                arxiv_url=_arxiv_url(paper),
                summary_3line=summary,
                shared_note=shared_note,
            )
        )

    return ShareCollectionResponse(
        collection=ShareCollectionInfo(
            name=collection.name,
            description=collection.description or None,
            shared_by=owner.display_name if owner is not None else "",
            updated_at=collection.updated_at.isoformat(),
            deadline=collection.deadline.isoformat() if collection.deadline else None,
            item_count=len(items),
        ),
        include_notes=share.include_notes,
        items=items,
    )
