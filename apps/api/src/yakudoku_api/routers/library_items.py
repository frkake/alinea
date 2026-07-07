"""library-items — ライブラリ一覧・facets・単体 CRUD・タグ(plans/03 §5、plans/11 §8)。

- ``GET /api/library-items``: フィルタ(同一属性内 OR・属性間 AND、quick と status は積集合)・
  ソート(§8.2)・keyset cursor ページング(§1.5)。
- ``GET /api/library-items/facets``: クイックフィルタ件数と属性選択肢+件数(quick は無視。§5.2)。
- ``GET/PATCH/DELETE /api/library-items/{id}``: 単体取得・部分更新(P6: 明示変更のみ)・削除。
- ``DELETE …/tag-suggestions/{tag}``(§5.10)、``POST …/duplicate-resolution``(§5.11)、tags(§5.13)。

DB のステータス列挙は 0001 初期スキーマの CHECK(``planned/up_next/reading/done/reread/on_hold``)で
API 列挙と一致するため、ルータ層での変換は不要(plans/11 §8.1 の to_read 系写像は解消済み)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from sqlalchemy import Integer, and_, case, cast, extract, func, or_, select, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.sql.elements import ColumnElement
from yakudoku_core.db.models import (
    Collection,
    CollectionEntry,
    DocumentRevision,
    LibraryItem,
    Paper,
    SavedFilter,
)
from yakudoku_core.document.blocks import DocumentContent

from yakudoku_api.deps import CurrentUser, CurrentUserOrExt, DbDep, RedisDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.schemas.common import (
    CursorPage,
    LastPosition,
    LibraryItemSummary,
    decode_cursor,
    encode_cursor,
)
from yakudoku_api.schemas.dashboard import QueueOrderRequest, QueueOrderResponse
from yakudoku_api.schemas.library import (
    CollectionFacet,
    DuplicateResolutionBody,
    DuplicateResolutionResponse,
    FacetsResponse,
    LibraryItemPatch,
    QualityFacet,
    QuickFacet,
    TagCount,
    TagsResponse,
    YearFacet,
    build_paper_bib,
)
from yakudoku_api.services.reading_sessions import (
    ReadingHeartbeatBody,
    ReadingHeartbeatResponse,
    record_heartbeat,
)

router = APIRouter(tags=["library-items"])

# --- 列挙・クイックフィルタ合成(docs/06 §1・plans/03 §1.6) --------------------------
STATUSES = ("planned", "up_next", "reading", "done", "reread", "on_hold")
_QUICK: dict[str, list[str]] = {
    "unread": ["planned", "up_next"],
    "in_progress": ["reading", "on_hold"],
    "done": ["done"],
    "recheck": ["reread"],
}
_QUICKS = ("all", *_QUICK.keys())
_PRIORITY_RANK = {"high": 0, "mid": 1, "low": 2}


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


# --- クエリ組み立て -----------------------------------------------------------------
def _rev_id() -> ColumnElement[Any]:
    """読書位置の revision → 無ければ最新 revision(plans/11 §8.1 の COALESCE)。"""
    return func.coalesce(
        cast(LibraryItem.reading_position["revision_id"].astext, PGUUID),
        Paper.latest_revision_id,
    )


def _scoped(*cols: Any) -> Any:
    return (
        select(*cols)
        .select_from(LibraryItem)
        .join(Paper, Paper.id == LibraryItem.paper_id)
        .outerjoin(DocumentRevision, DocumentRevision.id == _rev_id())
    )


def _author_match(q: str) -> Any:
    return text(
        "EXISTS (SELECT 1 FROM jsonb_array_elements(papers.authors) AS _a "
        "WHERE _a->>'name' ILIKE :q_pat)"
    ).bindparams(q_pat=f"%{q}%")


def _conditions(
    user_id: str,
    *,
    quick: str,
    statuses: list[str] | None,
    tags: list[str] | None,
    collection_id: str | None,
    quality: str | None,
    years: list[int] | None,
    q: str | None,
    include_quick: bool,
) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [LibraryItem.user_id == user_id]
    if include_quick and quick != "all":
        conds.append(LibraryItem.status.in_(_QUICK[quick]))
    if statuses:
        conds.append(LibraryItem.status.in_(statuses))
    if tags:
        conds.append(LibraryItem.tags.overlap(tags))
    if collection_id:
        conds.append(
            select(CollectionEntry.id)
            .where(
                CollectionEntry.library_item_id == LibraryItem.id,
                CollectionEntry.collection_id == collection_id,
            )
            .exists()
        )
    if quality:
        conds.append(DocumentRevision.quality_level == quality)
    if years:
        conds.append(cast(extract("year", Paper.published_on), Integer).in_(years))
    if q:
        conds.append(or_(Paper.title.ilike(f"%{q}%"), _author_match(q)))
    return conds


# --- ソート仕様(plans/03 §5.1・plans/11 §8.2) ------------------------------------
def _priority_rank() -> ColumnElement[Any]:
    return case(
        (LibraryItem.priority == "high", 0),
        (LibraryItem.priority == "mid", 1),
        (LibraryItem.priority == "low", 2),
        else_=None,
    )


# (kind, nullable, 列式ファクトリ, カーソル値抽出)。nullable=True は NULLS LAST(§8.2)。
_SORTS: dict[str, tuple[str, bool, Any, Any]] = {
    "updated_at": ("dt", False, lambda: LibraryItem.updated_at, lambda it, p: it.updated_at),
    "added_at": ("dt", False, lambda: LibraryItem.added_at, lambda it, p: it.added_at),
    "title": ("str", False, lambda: Paper.title.collate("C"), lambda it, p: p.title),
    "deadline": ("date", True, lambda: LibraryItem.deadline, lambda it, p: it.deadline),
    "priority": ("int", True, _priority_rank, lambda it, p: _PRIORITY_RANK.get(it.priority)),
    "reading_time": (
        "int",
        False,
        lambda: LibraryItem.total_active_seconds,
        lambda it, p: it.total_active_seconds,
    ),
    "comprehension": (
        "int",
        True,
        lambda: LibraryItem.understanding,
        lambda it, p: it.understanding,
    ),
}


def _deserialize(kind: str, k: Any) -> Any:
    if kind == "dt":
        return dt.datetime.fromisoformat(k)
    if kind == "date":
        return dt.date.fromisoformat(k)
    if kind == "int":
        return int(k)
    return k


def _serialize(kind: str, value: Any) -> Any:
    if value is None:
        return None
    if kind in ("dt", "date"):
        return value.isoformat()
    if kind == "int":
        return int(value)
    return value


def _keyset(
    col: ColumnElement[Any], kind: str, nullable: bool, asc: bool, k: Any, last_id: str
) -> ColumnElement[bool]:
    id_cmp = LibraryItem.id > last_id if asc else LibraryItem.id < last_id
    if k is None:
        return and_(col.is_(None), id_cmp) if nullable else id_cmp
    bind = _deserialize(kind, k)
    op = col > bind if asc else col < bind
    eq = and_(col == bind, id_cmp)
    if nullable:
        return or_(op, eq, col.is_(None))
    return or_(op, eq)


# --- サマリ構築 ---------------------------------------------------------------------
# revision ごとの (block_id→順序, 総ブロック数, block_id→所属 Section)。
_RevMap = tuple[dict[str, int], int, dict[str, Any]]


async def _reading_maps(db: DbDep, items: list[LibraryItem]) -> dict[str, _RevMap]:
    """reading_position を持つ item の revision を一括ロードし block 順序と所属節を引く。"""
    rev_ids = {
        str(it.reading_position["revision_id"])
        for it in items
        if it.reading_position and it.reading_position.get("revision_id")
    }
    if not rev_ids:
        return {}
    rows = await db.execute(
        select(DocumentRevision.id, DocumentRevision.content).where(
            DocumentRevision.id.in_(rev_ids)
        )
    )
    maps: dict[str, _RevMap] = {}
    for rid, content in rows.all():
        try:
            doc = DocumentContent.model_validate(content)
        except (ValueError, TypeError):
            continue
        pairs = doc.iter_blocks()
        order = {blk.id: idx for idx, (_sec, blk) in enumerate(pairs)}
        sections = {blk.id: sec for sec, blk in pairs}
        maps[str(rid)] = (order, len(pairs), sections)
    return maps


def _progress(item: LibraryItem, maps: dict[str, _RevMap]) -> int:
    if item.status == "done":
        return 100
    rp = item.reading_position
    if rp and rp.get("revision_id") and rp.get("block_id"):
        m = maps.get(str(rp["revision_id"]))
        if m is not None:
            order, total, _sections = m
            bid = rp["block_id"]
            if total > 0 and bid in order:
                return min(100, (100 * (order[bid] + 1)) // total)
    return 0


def _last_position(item: LibraryItem, maps: dict[str, _RevMap]) -> LastPosition | None:
    rp = item.reading_position
    if not rp or not rp.get("revision_id") or not rp.get("block_id"):
        return None
    rid = str(rp["revision_id"])
    bid = str(rp["block_id"])
    mode = rp.get("mode") or rp.get("view_mode") or "translation"
    section_display = ""
    m = maps.get(rid)
    if m is not None and bid in m[2]:
        sec = m[2][bid]
        num = (sec.heading.number or "").strip()
        title = (sec.heading.title or "").strip()
        label = f"§{num} {title}".strip() if (num or title) else ""
        section_display = label
    saved_at = rp.get("saved_at") or (item.updated_at.isoformat() if item.updated_at else "")
    return LastPosition(
        revision_id=rid, block_id=bid, mode=mode, section_display=section_display, saved_at=saved_at
    )


def _summary(
    item: LibraryItem, paper: Paper, quality: str | None, maps: dict[str, _RevMap]
) -> LibraryItemSummary:
    return LibraryItemSummary(
        id=str(item.id),
        paper=build_paper_bib(paper),
        status=item.status,
        priority=item.priority,
        deadline=item.deadline.isoformat() if item.deadline else None,
        tags=list(item.tags or []),
        suggested_tags=list(item.suggested_tags or []),
        quality_level=quality or "B",
        source="arxiv" if paper.arxiv_id else "upload",
        progress_pct=_progress(item, maps),
        comprehension=item.understanding,
        importance=item.importance,
        reading_seconds_total=item.total_active_seconds,
        one_line_note=item.one_line_note or None,
        summary_3line=paper.summary_lines,
        thumbnail_url=None,
        pipeline=None,
        last_position=_last_position(item, maps),
        added_at=item.added_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
        finished_at=item.finished_at.isoformat() if item.finished_at else None,
    )


async def _quality_of(db: DbDep, item: LibraryItem, paper: Paper) -> str | None:
    rev_id: str | None = None
    if item.reading_position and item.reading_position.get("revision_id"):
        rev_id = str(item.reading_position["revision_id"])
    elif paper.latest_revision_id:
        rev_id = str(paper.latest_revision_id)
    if rev_id is None:
        return None
    return (
        await db.execute(
            select(DocumentRevision.quality_level).where(DocumentRevision.id == rev_id)
        )
    ).scalar_one_or_none()


async def _summary_for(db: DbDep, item: LibraryItem) -> LibraryItemSummary:
    paper = await db.get(Paper, item.paper_id)
    assert paper is not None
    quality = await _quality_of(db, item, paper)
    maps = await _reading_maps(db, [item])
    return _summary(item, paper, quality, maps)


# --- 所有チェック -------------------------------------------------------------------
async def _get_owned(db: DbDep, user_id: str, item_id: str) -> LibraryItem:
    if not _valid_uuid(item_id):
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return item


# ============================================================================
# 一覧(§5.1)
# ============================================================================
@router.get(
    "/api/library-items",
    response_model=CursorPage[LibraryItemSummary],
    operation_id="libraryItems_list",
)
async def list_items(
    user: CurrentUser,
    db: DbDep,
    quick: Annotated[str, Query()] = "all",
    status: Annotated[list[str] | None, Query()] = None,
    tag: Annotated[list[str] | None, Query()] = None,
    collection_id: Annotated[str | None, Query()] = None,
    quality: Annotated[str | None, Query()] = None,
    year: Annotated[list[int] | None, Query()] = None,
    filter_id: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query()] = "updated_at",
    order: Annotated[str, Query()] = "desc",
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CursorPage[LibraryItemSummary]:
    if filter_id:
        quick, status, tag, collection_id, quality, year, sort, order = await _apply_saved_filter(
            db, user.id, filter_id, quick, status, tag, collection_id, quality, year, sort, order
        )
    _validate_query(quick, sort, order, quality, status, collection_id)

    conds = _conditions(
        user.id,
        quick=quick,
        statuses=status,
        tags=tag,
        collection_id=collection_id,
        quality=quality,
        years=year,
        q=q,
        include_quick=True,
    )

    total = (
        await db.execute(
            select(func.count()).select_from(_scoped(LibraryItem.id).where(*conds).subquery())
        )
    ).scalar_one()

    kind, nullable, col_fn, key_fn = _SORTS[sort]
    col = col_fn()
    asc = order == "asc"

    stmt = _scoped(LibraryItem, Paper, DocumentRevision.quality_level).where(*conds)
    if cursor:
        try:
            data = decode_cursor(cursor)
        except ValueError as exc:
            raise ProblemException("validation_error", detail="カーソルが不正です") from exc
        stmt = stmt.where(_keyset(col, kind, nullable, asc, data.get("k"), data["id"]))

    primary = col.asc() if asc else col.desc()
    if nullable:
        primary = primary.nulls_last()
    id_order = LibraryItem.id.asc() if asc else LibraryItem.id.desc()
    stmt = stmt.order_by(primary, id_order).limit(limit + 1)

    rows = (await db.execute(stmt)).all()
    has_next = len(rows) > limit
    kept = rows[:limit]
    maps = await _reading_maps(db, [r[0] for r in kept])
    items = [_summary(r[0], r[1], r[2], maps) for r in kept]

    next_cursor: str | None = None
    if has_next:
        last_it, last_p, _q = kept[-1]
        next_cursor = encode_cursor(_serialize(kind, key_fn(last_it, last_p)), str(last_it.id))
    return CursorPage(items=items, next_cursor=next_cursor, total=int(total))


def _validate_query(
    quick: str,
    sort: str,
    order: str,
    quality: str | None,
    statuses: list[str] | None,
    collection_id: str | None,
) -> None:
    if quick not in _QUICKS:
        raise ProblemException("validation_error", detail=f"quick は {_QUICKS}")
    if sort not in _SORTS:
        raise ProblemException("validation_error", detail="sort が不正です")
    if order not in ("asc", "desc"):
        raise ProblemException("validation_error", detail="order は asc|desc")
    if quality is not None and quality not in ("A", "B"):
        raise ProblemException("validation_error", detail="quality は A|B")
    for s in statuses or []:
        if s not in STATUSES:
            raise ProblemException("validation_error", detail=f"status が不正です: {s}")
    if collection_id is not None and not _valid_uuid(collection_id):
        raise ProblemException("validation_error", detail="collection_id が不正です")


async def _apply_saved_filter(
    db: DbDep,
    user_id: str,
    filter_id: str,
    quick: str,
    status: list[str] | None,
    tag: list[str] | None,
    collection_id: str | None,
    quality: str | None,
    year: list[int] | None,
    sort: str,
    order: str,
) -> tuple[
    str, list[str] | None, list[str] | None, str | None, str | None, list[int] | None, str, str
]:
    """保存フィルタの条件を既定として適用(明示クエリが同項目を上書き。§5.1・plans/11 §8.3)。"""
    if not _valid_uuid(filter_id):
        raise ProblemException("not_found")
    sf = await db.get(SavedFilter, filter_id)
    if sf is None or str(sf.user_id) != str(user_id):
        raise ProblemException("not_found")
    c = sf.conditions or {}
    s = sf.sort or {}
    if quick == "all":
        quick = c.get("quick", "all")
    if status is None:
        status = c.get("status")
    if tag is None:
        tag = c.get("tags")
    if collection_id is None:
        collection_id = c.get("collection_id")
    if quality is None:
        quality = c.get("quality")
    if year is None:
        year = c.get("years")
    if sort == "updated_at":
        sort = s.get("key", "updated_at")
    if order == "desc":
        order = s.get("order", "desc")
    return quick, status, tag, collection_id, quality, year, sort, order


# ============================================================================
# facets(§5.2)
# ============================================================================
@router.get(
    "/api/library-items/facets", response_model=FacetsResponse, operation_id="libraryItems_facets"
)
async def facets(
    user: CurrentUser,
    db: DbDep,
    status: Annotated[list[str] | None, Query()] = None,
    tag: Annotated[list[str] | None, Query()] = None,
    collection_id: Annotated[str | None, Query()] = None,
    quality: Annotated[str | None, Query()] = None,
    year: Annotated[list[int] | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
) -> FacetsResponse:
    _validate_query("all", "updated_at", "desc", quality, status, collection_id)
    # §5.2: quick は無視。属性フィルタのみを適用した集合で件数を出す。
    conds = _conditions(
        user.id,
        quick="all",
        statuses=status,
        tags=tag,
        collection_id=collection_id,
        quality=quality,
        years=year,
        q=q,
        include_quick=False,
    )

    status_rows = (
        await db.execute(
            _scoped(LibraryItem.status, func.count(LibraryItem.id))
            .where(*conds)
            .group_by(LibraryItem.status)
        )
    ).all()
    sc = {s: int(c) for s, c in status_rows}
    status_all = {s: sc.get(s, 0) for s in STATUSES}
    quick = QuickFacet(
        all=sum(status_all.values()),
        unread=status_all["planned"] + status_all["up_next"],
        in_progress=status_all["reading"] + status_all["on_hold"],
        done=status_all["done"],
        recheck=status_all["reread"],
    )

    tag_sub = _scoped(func.unnest(LibraryItem.tags).label("tag")).where(*conds).subquery()
    tag_rows = (
        await db.execute(
            select(tag_sub.c.tag, func.count().label("c"))
            .group_by(tag_sub.c.tag)
            .order_by(func.count().desc(), tag_sub.c.tag.asc())
            .limit(100)
        )
    ).all()
    tags = [TagCount(tag=t, count=int(c)) for t, c in tag_rows]

    ids_sub = _scoped(LibraryItem.id.label("lid")).where(*conds).subquery()
    coll_rows = (
        await db.execute(
            select(Collection.id, Collection.name, func.count())
            .select_from(CollectionEntry)
            .join(Collection, Collection.id == CollectionEntry.collection_id)
            .where(CollectionEntry.library_item_id.in_(select(ids_sub.c.lid)))
            .group_by(Collection.id, Collection.name)
            .order_by(func.count().desc())
        )
    ).all()
    collections = [
        CollectionFacet(id=str(cid), name=name, count=int(c)) for cid, name, c in coll_rows
    ]

    q_rows = (
        await db.execute(
            _scoped(DocumentRevision.quality_level, func.count(LibraryItem.id))
            .where(*conds)
            .group_by(DocumentRevision.quality_level)
        )
    ).all()
    qd = {ql: int(c) for ql, c in q_rows}
    quality_facet = QualityFacet(A=qd.get("A", 0), B=qd.get("B", 0))

    year_expr = cast(extract("year", Paper.published_on), Integer)
    year_rows = (
        await db.execute(
            _scoped(year_expr.label("y"), func.count(LibraryItem.id))
            .where(*conds, Paper.published_on.isnot(None))
            .group_by(year_expr)
            .order_by(year_expr.desc())
        )
    ).all()
    years = [YearFacet(year=int(y), count=int(c)) for y, c in year_rows]

    return FacetsResponse(
        quick=quick,
        status=status_all,
        tags=tags,
        collections=collections,
        quality=quality_facet,
        years=years,
    )


# ============================================================================
# タグ集計(§5.13)
# ============================================================================
@router.get("/api/tags", response_model=TagsResponse, operation_id="tags_list")
async def list_tags(
    user: CurrentUser,
    db: DbDep,
    q: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> TagsResponse:
    sub = (
        select(func.unnest(LibraryItem.tags).label("tag"))
        .where(LibraryItem.user_id == user.id)
        .subquery()
    )
    stmt = select(sub.c.tag, func.count().label("c"))
    if q:
        stmt = stmt.where(sub.c.tag.ilike(f"{q}%"))  # 前方一致補完
    stmt = stmt.group_by(sub.c.tag).order_by(func.count().desc(), sub.c.tag.asc()).limit(limit)
    rows = (await db.execute(stmt)).all()
    return TagsResponse(items=[TagCount(tag=t, count=int(c)) for t, c in rows])


# ============================================================================
# 単体取得(§5.3)
# ============================================================================
@router.get(
    "/api/library-items/{item_id}",
    response_model=LibraryItemSummary,
    operation_id="libraryItems_get",
)
async def get_item(item_id: str, user: CurrentUser, db: DbDep) -> LibraryItemSummary:
    item = await _get_owned(db, user.id, item_id)
    return await _summary_for(db, item)


# ============================================================================
# 部分更新(§5.4)
# ============================================================================
@router.patch(
    "/api/library-items/{item_id}",
    response_model=LibraryItemSummary,
    operation_id="libraryItems_update",
)
async def patch_item(
    item_id: str, body: LibraryItemPatch, user: CurrentUserOrExt, db: DbDep
) -> LibraryItemSummary:
    item = await _get_owned(db, user.id, item_id)
    provided = body.model_fields_set

    if "status" in provided and body.status is not None:
        item.status = body.status
        # 初めて done になった時点で finished_at を自動記録(以後上書きしない。§5.4)。
        if body.status == "done" and item.finished_at is None:
            item.finished_at = dt.datetime.now(dt.UTC)
    if "priority" in provided:
        item.priority = body.priority  # None = 解除
    if "deadline" in provided:
        item.deadline = dt.date.fromisoformat(body.deadline) if body.deadline else None
    if "tags" in provided and body.tags is not None:
        new_tags = body.tags
        item.tags = new_tags
        # tags に含まれた提案は消化、含まれない提案は残す(§5.4)。
        item.suggested_tags = [s for s in (item.suggested_tags or []) if s not in new_tags]
    if "one_line_note" in provided:
        item.one_line_note = body.one_line_note or ""
    if "comprehension" in provided:
        item.understanding = body.comprehension  # None = 解除
    if "importance" in provided:
        item.importance = body.importance  # None = 解除

    await db.commit()
    await db.refresh(item)  # updated_at(トリガ)を反映
    return await _summary_for(db, item)


# ============================================================================
# 削除(§5.5)
# ============================================================================
@router.delete("/api/library-items/{item_id}", status_code=204, operation_id="libraryItems_delete")
async def delete_item(item_id: str, user: CurrentUser, db: DbDep) -> Response:
    item = await _get_owned(db, user.id, item_id)
    paper_id = item.paper_id
    await db.delete(item)  # 配下は FK ON DELETE CASCADE で消える
    await db.flush()
    # private Paper で他参照が無ければ Paper ごと削除(§5.5・docs/01 §13)。
    paper = await db.get(Paper, paper_id)
    if paper is not None and paper.visibility == "private":
        remaining = (
            await db.execute(
                select(func.count())
                .select_from(LibraryItem)
                .where(LibraryItem.paper_id == paper_id)
            )
        ).scalar_one()
        if remaining == 0:
            await db.delete(paper)
    await db.commit()
    return Response(status_code=204)


# ============================================================================
# 提案タグの却下(§5.10)
# ============================================================================
@router.delete(
    "/api/library-items/{item_id}/tag-suggestions/{tag}",
    status_code=204,
    operation_id="libraryItems_reject_tag_suggestion",
)
async def reject_tag_suggestion(item_id: str, tag: str, user: CurrentUser, db: DbDep) -> Response:
    item = await _get_owned(db, user.id, item_id)
    suggested = list(item.suggested_tags or [])
    if tag in suggested:
        item.suggested_tags = [t for t in suggested if t != tag]
        await db.commit()
    return Response(status_code=204)


# ============================================================================
# ファジー重複の統合確認(§5.11)
# ============================================================================
@router.post(
    "/api/library-items/{item_id}/duplicate-resolution",
    response_model=DuplicateResolutionResponse,
    operation_id="libraryItems_resolve_duplicate",
)
async def resolve_duplicate(
    item_id: str, body: DuplicateResolutionBody, user: CurrentUser, db: DbDep
) -> DuplicateResolutionResponse:
    item = await _get_owned(db, user.id, item_id)
    if body.action not in ("merge", "dismiss"):
        raise ProblemException("validation_error", detail="action は merge|dismiss")

    if body.action == "dismiss":
        # 「同一の可能性」カードの非表示はクライアント側(M0 は再提示抑止の永続化なし)。
        return DuplicateResolutionResponse(library_item=await _summary_for(db, item))

    # merge: arXiv 側 Paper を残す(§5.11・docs/02 §6 の B→A 昇格の入口)。
    if not body.other_paper_id:
        raise ProblemException("validation_error", detail="merge には other_paper_id が必須です")
    other = await _accessible_paper(db, user.id, body.other_paper_id)
    current = await db.get(Paper, item.paper_id)
    assert current is not None

    survivor = current if current.arxiv_id else (other if other.arxiv_id else current)
    result_item = item
    if str(survivor.id) != str(item.paper_id):
        existing = (
            (
                await db.execute(
                    select(LibraryItem).where(
                        LibraryItem.user_id == user.id, LibraryItem.paper_id == survivor.id
                    )
                )
            )
            .scalars()
            .first()
        )
        old_paper_id = item.paper_id
        if existing is not None and str(existing.id) != str(item.id):
            # 既に survivor 側の項目がある: そちらを残して重複項目を削除。
            await db.delete(item)
            result_item = existing
        else:
            item.paper_id = survivor.id
        await db.flush()
        # 旧 private Paper が参照されなくなったら削除。
        old = await db.get(Paper, old_paper_id)
        if old is not None and old.visibility == "private":
            remaining = (
                await db.execute(
                    select(func.count())
                    .select_from(LibraryItem)
                    .where(LibraryItem.paper_id == old_paper_id)
                )
            ).scalar_one()
            if remaining == 0:
                await db.delete(old)
    await db.commit()
    return DuplicateResolutionResponse(library_item=await _summary_for(db, result_item))


async def _accessible_paper(db: DbDep, user_id: str, paper_id: str) -> Paper:
    if not _valid_uuid(paper_id):
        raise ProblemException("not_found")
    paper = await db.get(Paper, paper_id)
    if paper is None:
        raise ProblemException("not_found")
    if paper.visibility == "public":
        return paper
    if paper.owner_user_id and str(paper.owner_user_id) == str(user_id):
        return paper
    owns = (
        await db.execute(
            select(LibraryItem.id).where(
                LibraryItem.user_id == user_id, LibraryItem.paper_id == paper_id
            )
        )
    ).first()
    if owns is not None:
        return paper
    raise ProblemException("not_found")


# ============================================================================
# すぐ読むキューの並び替え(§5.7)
# ============================================================================
@router.put(
    "/api/library-items/queue-order",
    response_model=QueueOrderResponse,
    operation_id="libraryItems_setQueueOrder",
)
async def set_queue_order(
    body: QueueOrderRequest, user: CurrentUser, db: DbDep
) -> QueueOrderResponse:
    ids = body.library_item_ids
    if len(ids) != len(set(ids)):
        raise ProblemException("validation_error", detail="library_item_ids に重複があります")

    current_ids = (
        (
            await db.execute(
                select(LibraryItem.id).where(
                    LibraryItem.user_id == user.id, LibraryItem.status == "up_next"
                )
            )
        )
        .scalars()
        .all()
    )
    current_set = {str(cid) for cid in current_ids}
    if set(ids) != current_set:
        # up_next でない ID が混在/不足(他ユーザー・不存在・重複含む)は 422(§5.7)。
        raise ProblemException(
            "validation_error",
            detail="status=up_next の全 ID を過不足なく指定してください",
        )

    for idx, item_id in enumerate(ids):
        item = await db.get(LibraryItem, item_id)
        assert item is not None  # 直前の集合一致チェックで存在保証済み
        item.queue_order = idx
    await db.commit()
    return QueueOrderResponse(ok=True)


# ============================================================================
# 読書時間計測(M1-05。§5.9・plans/07 §8)
# ============================================================================
@router.post(
    "/api/library-items/{item_id}/reading-sessions",
    response_model=ReadingHeartbeatResponse,
    operation_id="libraryItems_readingSessionHeartbeat",
)
async def reading_session_heartbeat(
    item_id: str, body: ReadingHeartbeatBody, user: CurrentUser, db: DbDep, r: RedisDep
) -> ReadingHeartbeatResponse:
    item = await _get_owned(db, user.id, item_id)
    return await record_heartbeat(db, r, user=user, item=item, body=body)
