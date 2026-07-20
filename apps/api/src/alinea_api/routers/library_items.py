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
import logging
import uuid
from collections.abc import Iterable, Sequence
from typing import Annotated, Any

from alinea_core.article.storage_keys import article_versions_cache_key
from alinea_core.db.models import (
    Article,
    Collection,
    CollectionEntry,
    DocumentRevision,
    ExplainerFigure,
    Glossary,
    Job,
    LibraryItem,
    Notification,
    OverviewFigure,
    Paper,
    SavedFilter,
    SourceAsset,
    UsageRecord,
)
from alinea_core.db.revisions import (
    get_paper_revisions,
    get_preferred_item_revision,
    reading_position_revision_id,
)
from alinea_core.document.blocks import DocumentContent
from alinea_core.storage.s3 import S3Storage, StorageKeys
from alinea_llm.providers.openai_embeddings import DEFAULT_EMBEDDING_MODEL
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import Integer, and_, case, cast, delete, extract, func, or_, select, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from alinea_api.deps import CurrentUser, CurrentUserOrExt, DbDep, RedisDep, SettingsDep
from alinea_api.errors import ProblemException
from alinea_api.schemas.common import (
    CursorPage,
    LastPosition,
    LibraryItemSummary,
    decode_cursor,
    encode_cursor,
)
from alinea_api.schemas.dashboard import QueueOrderRequest, QueueOrderResponse
from alinea_api.schemas.library import (
    BulkOperationBody,
    BulkOperationResponse,
    CollectionFacet,
    DuplicateResolutionBody,
    DuplicateResolutionResponse,
    FacetsResponse,
    LibraryItemPatch,
    QualityFacet,
    QuickFacet,
    SavedFilterBody,
    SavedFilterConditions,
    SavedFilterOut,
    SavedFiltersListResponse,
    SavedFilterSort,
    TagCount,
    TagsResponse,
    YearFacet,
    author_names,
    build_paper_bib,
)
from alinea_api.schemas.search import SimilarPaper, SimilarPapersResponse
from alinea_api.search_semantic import (
    SIMILAR_TOP_K,
    SemanticIndexFactory,
    default_semantic_index_factory,
)
from alinea_api.services.reading_sessions import (
    ReadingHeartbeatBody,
    ReadingHeartbeatResponse,
    record_heartbeat,
)
from alinea_api.settings import get_api_settings

router = APIRouter(tags=["library-items"])
logger = logging.getLogger(__name__)


def get_storage() -> S3Storage:
    return S3Storage(get_api_settings())


StorageDep = Annotated[S3Storage, Depends(get_storage)]


# ---------------------------------------------------------------------------
# 似た論文(S12)のテスト注入点。既定は pgvector ANN(クエリ埋め込みは不要 = 種ベクトル利用)。
# ---------------------------------------------------------------------------
def get_semantic_index_factory() -> SemanticIndexFactory:
    return default_semantic_index_factory()


SemanticIndexFactoryDep = Annotated[SemanticIndexFactory, Depends(get_semantic_index_factory)]

_ASSET_STORAGE_PREFIXES = ("figures/", "renders/", "thumbnails/")
_ASSET_KEY_FIELDS = {"asset_key", "storage_key", "image_storage_key", "svg_storage_key"}


def _add_storage_key(keys: set[str], key: str | None) -> None:
    if key:
        keys.add(key)


def _asset_keys_from_json(value: Any) -> set[str]:
    keys: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for name, child in node.items():
                if (
                    name in _ASSET_KEY_FIELDS
                    and isinstance(child, str)
                    and child.startswith(_ASSET_STORAGE_PREFIXES)
                ):
                    keys.add(child)
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return keys


async def _article_storage_data(
    db: DbDep, library_item_ids: Sequence[str]
) -> tuple[list[str], list[str], set[str]]:
    if not library_item_ids:
        return [], [], set()
    rows = await db.execute(select(Article.id).where(Article.library_item_id.in_(library_item_ids)))
    article_ids = list(rows.scalars())
    if not article_ids:
        return [], [], set()

    keys: set[str] = set()
    overview_rows = await db.execute(
        select(OverviewFigure.svg_storage_key, OverviewFigure.image_storage_key).where(
            OverviewFigure.article_id.in_(article_ids)
        )
    )
    for svg_key, image_key in overview_rows.all():
        _add_storage_key(keys, svg_key)
        _add_storage_key(keys, image_key)

    explainer_rows = await db.execute(
        select(ExplainerFigure.id, ExplainerFigure.image_storage_key).where(
            ExplainerFigure.article_id.in_(article_ids)
        )
    )
    explainer_ids: list[str] = []
    for explainer_id, image_key in explainer_rows.all():
        explainer_ids.append(str(explainer_id))
        _add_storage_key(keys, image_key)
    return [str(article_id) for article_id in article_ids], explainer_ids, keys


async def _paper_storage_keys(db: DbDep, paper: Paper) -> tuple[set[str], set[str]]:
    source_keys: set[str] = set()
    asset_keys: set[str] = set()

    source_rows = await db.execute(
        select(SourceAsset.storage_key).where(SourceAsset.paper_id == paper.id)
    )
    for key in source_rows.scalars():
        _add_storage_key(source_keys, key)

    revision_rows = await db.execute(
        select(DocumentRevision.content).where(DocumentRevision.paper_id == paper.id)
    )
    for content in revision_rows.scalars():
        asset_keys.update(_asset_keys_from_json(content))

    if paper.thumbnail_key:
        _add_storage_key(asset_keys, paper.thumbnail_key)
        _add_storage_key(
            asset_keys,
            StorageKeys.thumbnail_retina_sibling(
                paper.thumbnail_key,
                paper_id=str(paper.id),
            ),
        )
    return source_keys, asset_keys


async def _delete_storage_objects(
    storage: S3Storage,
    *,
    source_keys: Iterable[str],
    asset_keys: Iterable[str],
    source_prefixes: Iterable[str] = (),
    asset_prefixes: Iterable[str] = (),
) -> None:
    try:
        await storage.delete_many(storage.sources_bucket, source_keys)
        await storage.delete_many(storage.assets_bucket, asset_keys)
        await storage.delete_prefixes(storage.sources_bucket, source_prefixes)
        await storage.delete_prefixes(storage.assets_bucket, asset_prefixes)
    except Exception:
        logger.warning("failed to delete library item storage objects", exc_info=True)
        raise


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
_UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_ReadingRevision = aliased(DocumentRevision, name="reading_revision")


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


# --- クエリ組み立て -----------------------------------------------------------------
def _rev_id() -> ColumnElement[Any]:
    """同一論文の読書位置 revision → 無ければ latest revision。"""
    return func.coalesce(_ReadingRevision.id, Paper.latest_revision_id)


def _safe_reading_revision_id() -> ColumnElement[Any]:
    raw = LibraryItem.reading_position["revision_id"].astext
    return case(
        (raw.op("~*")(_UUID_RE), cast(raw, PGUUID)),
        else_=None,
    )


def _scoped(*cols: Any) -> Any:
    return (
        select(*cols)
        .select_from(LibraryItem)
        .join(Paper, Paper.id == LibraryItem.paper_id)
        .outerjoin(
            _ReadingRevision,
            and_(
                _ReadingRevision.id == _safe_reading_revision_id(),
                _ReadingRevision.paper_id == Paper.id,
            ),
        )
        .outerjoin(
            DocumentRevision,
            and_(DocumentRevision.id == _rev_id(), DocumentRevision.paper_id == Paper.id),
        )
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
    statuses: Sequence[str] | None,
    tags: Sequence[str] | None,
    collection_id: str | None,
    quality: str | None,
    years: Sequence[int] | None,
    q: str | None,
    include_quick: bool,
) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [LibraryItem.user_id == user_id]
    if include_quick and quick != "all":
        conds.append(LibraryItem.status.in_(_QUICK[quick]))
    if statuses:
        conds.append(LibraryItem.status.in_(statuses))
    if tags:
        conds.append(LibraryItem.tags.overlap(list(tags)))
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
# item ごとの (revision_id, block_id→順序, 総ブロック数, block_id→所属 Section)。
_RevMap = tuple[str, dict[str, int], int, dict[str, Any]]


async def _reading_maps(
    db: DbDep, item_papers: list[tuple[LibraryItem, Paper]]
) -> dict[str, _RevMap]:
    """reading_position を持つ item の revision を一括ロードし block 順序と所属節を引く。"""
    requested = [
        (paper.id, revision_id)
        for item, paper in item_papers
        if (revision_id := reading_position_revision_id(item.reading_position)) is not None
    ]
    revisions = await get_paper_revisions(db, requested)
    maps: dict[str, _RevMap] = {}
    for item, paper in item_papers:
        revision_id = reading_position_revision_id(item.reading_position)
        if revision_id is None:
            continue
        revision = revisions.get((str(paper.id), revision_id))
        if revision is None:
            continue
        try:
            doc = DocumentContent.model_validate(revision.content)
        except (ValueError, TypeError):
            continue
        blocks = doc.iter_blocks()
        order = {blk.id: idx for idx, (_sec, blk) in enumerate(blocks)}
        sections = {blk.id: sec for sec, blk in blocks}
        maps[str(item.id)] = (str(revision.id), order, len(blocks), sections)
    return maps


def _progress(item: LibraryItem, maps: dict[str, _RevMap]) -> int:
    if item.status == "done":
        return 100
    rp = item.reading_position
    if rp and rp.get("revision_id") and rp.get("block_id"):
        m = maps.get(str(item.id))
        if m is not None:
            _revision_id, order, total, _sections = m
            bid = rp["block_id"]
            if total > 0 and bid in order:
                return min(100, (100 * (order[bid] + 1)) // total)
    return 0


def _last_position(item: LibraryItem, maps: dict[str, _RevMap]) -> LastPosition | None:
    rp = item.reading_position
    if not rp or not rp.get("revision_id") or not rp.get("block_id"):
        return None
    bid = str(rp["block_id"])
    mode = rp.get("mode") or rp.get("view_mode") or "translation"
    m = maps.get(str(item.id))
    if m is None:
        return None
    revision_id, order, _total, sections = m
    if bid not in order:
        return None
    sec = sections[bid]
    num = (sec.heading.number or "").strip()
    title = (sec.heading.title or "").strip()
    section_display = f"§{num} {title}".strip() if (num or title) else ""
    saved_at = rp.get("saved_at") or (item.updated_at.isoformat() if item.updated_at else "")
    return LastPosition(
        revision_id=revision_id,
        block_id=bid,
        mode=mode,
        section_display=section_display,
        saved_at=saved_at,
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
    revision = await get_preferred_item_revision(db, item=item, paper=paper)
    return revision.quality_level if revision is not None else None


async def _summary_for(db: DbDep, item: LibraryItem) -> LibraryItemSummary:
    paper = await db.get(Paper, item.paper_id)
    assert paper is not None
    quality = await _quality_of(db, item, paper)
    maps = await _reading_maps(db, [(item, paper)])
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
    maps = await _reading_maps(db, [(r[0], r[1]) for r in kept])
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
# 似た論文(S12 セマンティック検索。docs/10 §5・spec §6.3)
# ============================================================================
@router.get(
    "/api/library-items/{item_id}/similar",
    response_model=SimilarPapersResponse,
    operation_id="libraryItems_similar",
)
async def similar_items(
    item_id: str,
    user: CurrentUser,
    db: DbDep,
    settings: SettingsDep,
    index_factory: SemanticIndexFactoryDep,
) -> SimilarPapersResponse:
    """対象論文に意味的に近い自分のライブラリ内の他論文(上位 10)。

    - フラグ off / 空 index は 200・空配列・``indexing=false``(セマンティック経路に入らない)。
    - 対象論文に埋め込みが無いときは ``indexing=false`` + 空配列(202 で index job を enqueue
      しない・spec §6.3)。※将来 on-demand indexing を足すなら indexing=true を返す余地を残す。
    - 自分自身を除外し、他ユーザーの論文は決して返さない(ANN が user_id で絞る)。
    """
    item = await _get_owned(db, user.id, item_id)
    if not settings.semantic_search_enabled:
        return SimilarPapersResponse(items=[], indexing=False)

    index = index_factory(db)
    neighbors = await index.paper_neighbors(
        paper_id=str(item.paper_id),
        user_id=str(user.id),
        top_k=SIMILAR_TOP_K,
        model=DEFAULT_EMBEDDING_MODEL,
        exclude_library_item_id=str(item.id),
    )
    if neighbors is None:
        # 対象論文の埋め込みが未生成 = indexing 待ち。enqueue も 202 もしない。
        return SimilarPapersResponse(items=[], indexing=False)
    if not neighbors:
        return SimilarPapersResponse(items=[], indexing=False)

    # 近傍 library_item の書誌をまとめて引く(論文タイトル・著者)。
    rows = (
        await db.execute(
            select(LibraryItem.id, Paper.title, Paper.authors)
            .join(Paper, Paper.id == LibraryItem.paper_id)
            .where(LibraryItem.id.in_([n.library_item_id for n in neighbors]))
        )
    ).all()
    meta = {str(lid): (title, authors) for lid, title, authors in rows}
    items: list[SimilarPaper] = []
    for n in neighbors:
        info = meta.get(n.library_item_id)
        if info is None:
            continue  # 直前に削除された等(安全網)。
        title, authors = info
        items.append(
            SimilarPaper(
                library_item_id=n.library_item_id,
                title=title,
                authors=author_names(authors),
                similarity=round(n.similarity, 4),
            )
        )
    return SimilarPapersResponse(items=items, indexing=False)


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
# 削除(§5.5)。論文を再取り込みした際に旧翻訳・生成物を再利用しないよう、同じ Paper を
# 参照する項目を含めて論文単位で完全削除する。取り込み中
# (queued/waiting_quota/waiting_input/running)の
# 項目に対する呼び出しは取り込みキャンセルとして扱う(docs/08 §2.2)。
# 未着手・入力待ち(queued/waiting_quota/waiting_input)のジョブは行ごと消えるため
# claim() が確実に空振りする
# (main.py run_job の claim=None 経路)。running は次回 DB 書き込みで LookupError となり
# ベストエフォートで中断(main.py の job_gone_after_cancel 経路)。
# ============================================================================
@router.delete("/api/library-items/{item_id}", status_code=204, operation_id="libraryItems_delete")
async def delete_item(
    item_id: str, user: CurrentUserOrExt, db: DbDep, storage: StorageDep, r: RedisDep
) -> Response:
    item = await _get_owned(db, user.id, item_id)
    paper_id = str(item.paper_id)
    paper = await db.get(Paper, paper_id)
    library_item_ids = [
        str(value)
        for value in (
            await db.execute(select(LibraryItem.id).where(LibraryItem.paper_id == paper_id))
        ).scalars()
    ]
    article_ids, explainer_ids, article_asset_keys = await _article_storage_data(
        db, library_item_ids
    )

    source_keys: set[str] = set()
    asset_keys = set(article_asset_keys)
    if paper is not None:
        paper_source_keys, paper_asset_keys = await _paper_storage_keys(db, paper)
        source_keys.update(paper_source_keys)
        asset_keys.update(paper_asset_keys)
    item_thumbnail_rows = await db.execute(
        select(LibraryItem.thumbnail_key).where(LibraryItem.id.in_(library_item_ids))
    )
    for thumbnail_key in item_thumbnail_rows.scalars():
        _add_storage_key(asset_keys, thumbnail_key)

    job_ids = [
        str(value)
        for value in (
            await db.execute(
                select(Job.id).where(
                    or_(Job.paper_id == paper_id, Job.library_item_id.in_(library_item_ids))
                )
            )
        ).scalars()
    ]
    usage_condition: ColumnElement[bool] = UsageRecord.library_item_id.in_(library_item_ids)
    if job_ids:
        usage_condition = or_(usage_condition, UsageRecord.job_id.in_(job_ids))
    await db.execute(delete(UsageRecord).where(usage_condition))
    await db.execute(delete(Glossary).where(Glossary.library_item_id.in_(library_item_ids)))
    notification_conditions = [
        Notification.payload["library_item_id"].astext.in_(library_item_ids),
        Notification.payload["paper_id"].astext == paper_id,
    ]
    if job_ids:
        notification_conditions.append(Notification.payload["job_id"].astext.in_(job_ids))
    await db.execute(delete(Notification).where(or_(*notification_conditions)))
    if paper is not None:
        await db.delete(paper)
    else:
        await db.delete(item)
    await db.flush()
    await _delete_storage_objects(
        storage,
        source_keys=source_keys,
        asset_keys=asset_keys,
        source_prefixes=[f"sources/{paper_id}/"],
        asset_prefixes=[
            f"figures/{paper_id}/",
            f"thumbnails/{paper_id}/",
            # 論文→PPTX 成果物(Task 29)。key は presentations/{library_item_id}/{job_id}.pptx。
            # PresentationArtifact 行は library_item CASCADE で消えるが、S3 実体は明示削除する。
            *(f"presentations/{library_item_id}/" for library_item_id in library_item_ids),
            *(f"renders/articles/{article_id}/" for article_id in article_ids),
            *(f"renders/overview/{article_id}/" for article_id in article_ids),
            *(f"renders/explainer/{explainer_id}/" for explainer_id in explainer_ids),
        ],
    )
    redis_keys = [f"promo:checked:{paper_id}"]
    redis_keys.extend(article_versions_cache_key(article_id) for article_id in article_ids)
    await r.delete(*redis_keys)
    await db.commit()
    return Response(status_code=204)


# ============================================================================
# 一括操作(§5.6・plans/09-screens/1e §4.8・§5.5)
# ============================================================================
@router.post(
    "/api/library-items/bulk",
    response_model=BulkOperationResponse,
    operation_id="libraryItems_bulk",
)
async def bulk_update(
    body: BulkOperationBody, user: CurrentUser, db: DbDep
) -> BulkOperationResponse:
    unique_ids = list(dict.fromkeys(body.ids))
    if any(not _valid_uuid(i) for i in unique_ids):
        raise ProblemException("not_found")
    rows = (
        (await db.execute(select(LibraryItem).where(LibraryItem.id.in_(unique_ids))))
        .scalars()
        .all()
    )
    by_id = {str(r.id): r for r in rows}
    # 不存在・他ユーザー所有の ID が 1 件でもあれば全体を 404 で失敗させる(部分適用しない。§5.6)。
    if len(by_id) != len(unique_ids) or any(
        str(item.user_id) != str(user.id) for item in by_id.values()
    ):
        raise ProblemException("not_found")
    items = [by_id[i] for i in unique_ids]

    updated = 0
    if body.op == "set_status":
        if body.status is None:
            raise ProblemException("validation_error", detail="status が必要です")
        now = dt.datetime.now(dt.UTC)
        for item in items:
            item.status = body.status
            if body.status == "done" and item.finished_at is None:
                item.finished_at = now
            updated += 1
    elif body.op == "add_tags":
        if not body.tags:
            raise ProblemException("validation_error", detail="tags が必要です")
        for item in items:
            existing = list(item.tags or [])
            item.tags = existing + [t for t in body.tags if t not in existing]
            updated += 1
    elif body.op == "add_to_collection":
        if not body.collection_id:
            raise ProblemException("validation_error", detail="collection_id が必要です")
        collection = (
            await db.get(Collection, body.collection_id)
            if _valid_uuid(body.collection_id)
            else None
        )
        if collection is None or str(collection.user_id) != str(user.id):
            raise ProblemException("not_found", detail="コレクションが見つかりません")
        already = set(
            (
                await db.execute(
                    select(CollectionEntry.library_item_id).where(
                        CollectionEntry.collection_id == collection.id,
                        CollectionEntry.library_item_id.in_(unique_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        already = {str(i) for i in already}
        max_position = (
            await db.execute(
                select(func.max(CollectionEntry.position)).where(
                    CollectionEntry.collection_id == collection.id
                )
            )
        ).scalar_one()
        next_pos = (max_position + 1) if max_position is not None else 0
        for item in items:
            if str(item.id) in already:  # 既にコレクションにある項目はスキップ(§5.6)
                continue
            db.add(
                CollectionEntry(
                    id=str(uuid.uuid4()),
                    collection_id=str(collection.id),
                    library_item_id=str(item.id),
                    position=next_pos,
                )
            )
            next_pos += 1
            updated += 1

    await db.commit()
    return BulkOperationResponse(updated=updated)


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


# ============================================================================
# 保存フィルタ(§5.14・plans/11 §8.3)
# ============================================================================
async def _count_for_conditions(db: DbDep, user_id: str, conditions: SavedFilterConditions) -> int:
    """§5.14 の ``count``: 保存済みの conditions を §5.1 の WHERE に展開した導出値(保存しない)。"""
    conds = _conditions(
        user_id,
        quick=conditions.quick or "all",
        statuses=conditions.status,
        tags=conditions.tags,
        collection_id=conditions.collection_id,
        quality=conditions.quality,
        years=conditions.years,
        q=None,
        include_quick=True,
    )
    return int(
        (
            await db.execute(
                select(func.count()).select_from(_scoped(LibraryItem.id).where(*conds).subquery())
            )
        ).scalar_one()
    )


def _saved_filter_out(sf: SavedFilter, count: int) -> SavedFilterOut:
    return SavedFilterOut(
        id=str(sf.id),
        name=sf.name,
        conditions=SavedFilterConditions.model_validate(sf.conditions or {}),
        sort=SavedFilterSort.model_validate(sf.sort or {"key": "updated_at", "order": "desc"}),
        count=count,
    )


async def _get_owned_saved_filter(db: DbDep, user_id: str, filter_id: str) -> SavedFilter:
    if not _valid_uuid(filter_id):
        raise ProblemException("not_found")
    sf = await db.get(SavedFilter, filter_id)
    if sf is None or str(sf.user_id) != str(user_id):
        raise ProblemException("not_found")
    return sf


async def _assert_name_available(
    db: DbDep, user_id: str, name: str, *, exclude_id: str | None = None
) -> None:
    stmt = select(SavedFilter.id).where(SavedFilter.user_id == user_id, SavedFilter.name == name)
    if exclude_id is not None:
        stmt = stmt.where(SavedFilter.id != exclude_id)
    if (await db.execute(stmt)).first() is not None:
        raise ProblemException("duplicate", detail="同名の保存フィルタが既にあります")


@router.get(
    "/api/saved-filters",
    response_model=SavedFiltersListResponse,
    operation_id="savedFilters_list",
)
async def list_saved_filters(user: CurrentUser, db: DbDep) -> SavedFiltersListResponse:
    rows = (
        (
            await db.execute(
                select(SavedFilter)
                .where(SavedFilter.user_id == user.id)
                .order_by(SavedFilter.position.asc(), SavedFilter.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    items: list[SavedFilterOut] = []
    for sf in rows:
        conditions = SavedFilterConditions.model_validate(sf.conditions or {})
        count = await _count_for_conditions(db, user.id, conditions)
        items.append(_saved_filter_out(sf, count))
    return SavedFiltersListResponse(items=items)


@router.post(
    "/api/saved-filters",
    response_model=SavedFilterOut,
    status_code=201,
    operation_id="savedFilters_create",
)
async def create_saved_filter(
    body: SavedFilterBody, user: CurrentUser, db: DbDep
) -> SavedFilterOut:
    await _assert_name_available(db, user.id, body.name)
    max_position = (
        await db.execute(
            select(func.max(SavedFilter.position)).where(SavedFilter.user_id == user.id)
        )
    ).scalar_one()
    sf = SavedFilter(
        id=str(uuid.uuid4()),
        user_id=user.id,
        name=body.name,
        conditions=body.conditions.model_dump(exclude_none=True),
        sort=body.sort.model_dump(),
        position=(max_position + 1) if max_position is not None else 0,
    )
    db.add(sf)
    await db.commit()
    count = await _count_for_conditions(db, user.id, body.conditions)
    return _saved_filter_out(sf, count)


@router.patch(
    "/api/saved-filters/{filter_id}",
    response_model=SavedFilterOut,
    operation_id="savedFilters_update",
)
async def update_saved_filter(
    filter_id: str, body: SavedFilterBody, user: CurrentUser, db: DbDep
) -> SavedFilterOut:
    sf = await _get_owned_saved_filter(db, user.id, filter_id)
    if body.name != sf.name:
        await _assert_name_available(db, user.id, body.name, exclude_id=str(sf.id))
    sf.name = body.name
    sf.conditions = body.conditions.model_dump(exclude_none=True)
    sf.sort = body.sort.model_dump()
    await db.commit()
    count = await _count_for_conditions(db, user.id, body.conditions)
    return _saved_filter_out(sf, count)


@router.delete(
    "/api/saved-filters/{filter_id}", status_code=204, operation_id="savedFilters_delete"
)
async def delete_saved_filter(filter_id: str, user: CurrentUser, db: DbDep) -> Response:
    sf = await _get_owned_saved_filter(db, user.id, filter_id)
    await db.delete(sf)
    await db.commit()
    return Response(status_code=204)
