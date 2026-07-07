"""annotations — 注釈 CRUD・一覧フィルタ・counts(plans/03 §8・docs/04 §9)。

公開 API の ``kind`` は ``highlight | bookmark`` の 2 値。「コメント」は ``highlight`` +
``comment`` で表す。DB(0001)は ``kind IN ('highlight','comment','bookmark')`` の 3 値で
保持するため、本ルータで相互写像する:

- API ``highlight`` + comment あり → DB ``comment``(body=comment)。出力は再び highlight+comment。
- API ``highlight`` + comment なし → DB ``highlight``(body=NULL)。
- API ``bookmark`` → DB ``bookmark``(color/body 両 NULL)。

kind_shape CHECK(0001 §4.7): bookmark=両 NULL / highlight=color / comment=color+body。
``placed`` は DB ``orphaned`` の否定(リアンカー成否。placed=false=未配置)。
``anchor.display`` は block_search_index と同じ規則で ``DocumentRevision.content`` から
決定的に導出する(§1.7。保存しない)。
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import insert, select
from yakudoku_core.db.models import Annotation, DocumentRevision, LibraryItem
from yakudoku_core.document.blocks import DocumentContent, Section
from yakudoku_core.search.rebuild import compute_index_rows

from yakudoku_api.chat.evidence import BlockRow, derive_display
from yakudoku_api.deps import CurrentUser, DbDep
from yakudoku_api.errors import ProblemException
from yakudoku_api.schemas.annotations import (
    Annotation as AnnotationOut,
)
from yakudoku_api.schemas.annotations import (
    AnnotationCounts,
    AnnotationCreate,
    AnnotationListResponse,
    AnnotationPatch,
)
from yakudoku_api.schemas.chat import AnchorRef

router = APIRouter(tags=["annotations"])

_COLORS = ("important", "question", "idea", "term")
# ドキュメント順で見つからない注釈の順序キー(placed 群の末尾へ)。
_FAR = 1 << 30


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


# --- 所有チェック -------------------------------------------------------------------
async def _get_owned_item(db: DbDep, user_id: str, item_id: str) -> LibraryItem:
    if not _valid_uuid(item_id):
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return item


async def _get_owned_annotation(db: DbDep, user_id: str, ann_id: str) -> Annotation:
    if not _valid_uuid(ann_id):
        raise ProblemException("not_found")
    ann = await db.get(Annotation, ann_id)
    if ann is None:
        raise ProblemException("not_found")
    item = await db.get(LibraryItem, ann.library_item_id)
    if item is None or str(item.user_id) != str(user_id):
        raise ProblemException("not_found")
    return ann


# --- リビジョン索引(display 導出・出現順・ブロック実在検証) -------------------------
@dataclass
class _RevIndex:
    """1 リビジョンの block_search_index 相当(content から決定的に導出)。"""

    rows: dict[str, BlockRow] = field(default_factory=dict)  # block_id -> BlockRow
    order: dict[str, int] = field(default_factory=dict)  # block_id -> 文書内位置
    section_label: dict[str, str] = field(default_factory=dict)  # section_id -> 表記
    section_first_pos: dict[str, int] = field(default_factory=dict)  # section_id -> 先頭位置

    def block_or_section_exists(self, block_id: str) -> bool:
        return block_id in self.rows or block_id in self.section_label


def _section_label(sec: Section) -> str:
    """block_search_index と同じ規則("§2.2" / タイトル / id)。"""
    number = (sec.heading.number or "").strip()
    if number:
        return f"§{number}"
    return (sec.heading.title or "").strip() or sec.id


def _collect_section_labels(content: DocumentContent, index: _RevIndex) -> None:
    def walk(sec: Section) -> None:
        index.section_label.setdefault(sec.id, _section_label(sec))
        for sub in sec.sections:
            walk(sub)

    for top in content.sections:
        walk(top)


def _build_rev_index(content: DocumentContent) -> _RevIndex:
    index = _RevIndex()
    _collect_section_labels(content, index)
    for row in compute_index_rows(content):
        index.rows[row.block_id] = BlockRow(
            block_id=row.block_id,
            block_type=row.block_type,
            section_path=row.section_path,
            section_label=row.section_label,
            paragraph_ordinal=row.paragraph_ordinal,
            element_label=row.element_label,
        )
        index.order[row.block_id] = row.position
        leaf = row.section_path.split("/")[-1]
        index.section_first_pos.setdefault(leaf, row.position)
    return index


async def _load_rev_index(db: DbDep, revision_id: str, cache: dict[str, _RevIndex]) -> _RevIndex:
    if revision_id in cache:
        return cache[revision_id]
    index = _RevIndex()
    if _valid_uuid(revision_id):
        rev = await db.get(DocumentRevision, revision_id)
        if rev is not None:
            try:
                content = DocumentContent.model_validate(rev.content)
            except (ValueError, TypeError):
                content = DocumentContent(quality_level="A", sections=[])
            index = _build_rev_index(content)
    cache[revision_id] = index
    return index


def _display_for(index: _RevIndex, block_id: str, *, api_kind: str) -> str:
    row = index.rows.get(block_id)
    if row is not None:
        # bookmark はセクション参照(節見出しの表記のみ。§8.1)。
        if api_kind == "bookmark":
            return row.section_label
        return derive_display(row)
    # bookmark が節 ID を直接指す場合。
    if block_id in index.section_label:
        return index.section_label[block_id]
    return ""


def _order_key(index: _RevIndex, block_id: str) -> int:
    if block_id in index.order:
        return index.order[block_id]
    if block_id in index.section_first_pos:
        return index.section_first_pos[block_id]
    return _FAR


# --- 出力整形(DB Annotation → API Annotation) -------------------------------------
def _api_kind(db_kind: str) -> str:
    return "bookmark" if db_kind == "bookmark" else "highlight"


def _to_out(ann: Annotation, index: _RevIndex) -> AnnotationOut:
    anchor = ann.anchor if isinstance(ann.anchor, dict) else {}
    block_id = str(anchor.get("block_id", ""))
    api_kind = _api_kind(ann.kind)
    display = _display_for(index, block_id, api_kind=api_kind)
    if api_kind == "bookmark":
        # bookmark は start/end/quote を持たない(§8.1)。
        start: int | None = None
        end: int | None = None
        quote: str | None = None
    else:
        start = anchor.get("start")
        end = anchor.get("end")
        quote = anchor.get("quote")
    anchor_ref = AnchorRef(
        revision_id=str(anchor.get("revision_id", "")),
        block_id=block_id,
        start=start,
        end=end,
        quote=quote,
        side=anchor.get("side", "source"),
        display=display,
    )
    return AnnotationOut(
        id=str(ann.id),
        kind=api_kind,
        color=ann.color,
        anchor=anchor_ref,
        comment=ann.body if ann.kind == "comment" else None,
        placed=not ann.orphaned,
        created_at=ann.created_at.isoformat(),
        updated_at=ann.updated_at.isoformat(),
    )


async def _out_single(db: DbDep, ann: Annotation) -> AnnotationOut:
    anchor = ann.anchor if isinstance(ann.anchor, dict) else {}
    revision_id = str(anchor.get("revision_id", ""))
    cache: dict[str, _RevIndex] = {}
    index = await _load_rev_index(db, revision_id, cache)
    return _to_out(ann, index)


# ============================================================================
# 一覧 + counts(§8.1)
# ============================================================================
@router.get(
    "/api/library-items/{item_id}/annotations",
    response_model=AnnotationListResponse,
    operation_id="annotations_list",
)
async def list_annotations(
    item_id: str,
    user: CurrentUser,
    db: DbDep,
    color: Annotated[list[str] | None, Query()] = None,
    has_comment: Annotated[bool | None, Query()] = None,
    placed: Annotated[bool | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
) -> AnnotationListResponse:
    await _get_owned_item(db, user.id, item_id)

    if color:
        for c in color:
            if c not in _COLORS:
                raise ProblemException("validation_error", detail=f"color が不正です: {c}")
    if kind is not None and kind not in ("highlight", "bookmark"):
        raise ProblemException("validation_error", detail="kind は highlight|bookmark")

    all_rows = (
        (await db.execute(select(Annotation).where(Annotation.library_item_id == item_id)))
        .scalars()
        .all()
    )

    # counts はフィルタに関わらず論文全体の総数(docs/04 §9 のフィルタチップ件数)。
    counts = AnnotationCounts(
        all=len(all_rows),
        important=sum(1 for a in all_rows if a.color == "important"),
        question=sum(1 for a in all_rows if a.color == "question"),
        idea=sum(1 for a in all_rows if a.color == "idea"),
        term=sum(1 for a in all_rows if a.color == "term"),
        with_comment=sum(1 for a in all_rows if a.kind == "comment"),
        unplaced=sum(1 for a in all_rows if a.orphaned),
    )

    # フィルタ適用(同一属性内 OR・属性間 AND)。
    filtered: list[Annotation] = []
    for a in all_rows:
        if color and a.color not in color:
            continue
        if has_comment is not None and (a.kind == "comment") != has_comment:
            continue
        if placed is not None and (not a.orphaned) != placed:
            continue
        if kind is not None:
            if kind == "bookmark" and a.kind != "bookmark":
                continue
            if kind == "highlight" and a.kind not in ("highlight", "comment"):
                continue
        filtered.append(a)

    # display 導出・出現順のためリビジョン索引を一括ロード。
    cache: dict[str, _RevIndex] = {}
    rev_ids = {str(a.anchor.get("revision_id", "")) for a in filtered if isinstance(a.anchor, dict)}
    for rid in rev_ids:
        await _load_rev_index(db, rid, cache)

    def _index_of(a: Annotation) -> _RevIndex:
        rid = str(a.anchor.get("revision_id", "")) if isinstance(a.anchor, dict) else ""
        return cache.get(rid, _RevIndex())

    def _start_of(a: Annotation) -> int:
        anchor = a.anchor if isinstance(a.anchor, dict) else {}
        start = anchor.get("start")
        return start if isinstance(start, int) else -1

    def _block_of(a: Annotation) -> str:
        return str(a.anchor.get("block_id", "")) if isinstance(a.anchor, dict) else ""

    placed_rows = [a for a in filtered if not a.orphaned]
    unplaced_rows = [a for a in filtered if a.orphaned]

    # placed: 文書内出現順(セクション順→ブロック順→start 昇順)。
    placed_rows.sort(
        key=lambda a: (_order_key(_index_of(a), _block_of(a)), _start_of(a), a.created_at)
    )
    # unplaced: 末尾に作成降順(§8.1)。
    unplaced_rows.sort(key=lambda a: a.created_at, reverse=True)

    items = [_to_out(a, _index_of(a)) for a in [*placed_rows, *unplaced_rows]]
    return AnnotationListResponse(items=items, counts=counts)


# ============================================================================
# 作成(§8.2)
# ============================================================================
@router.post(
    "/api/library-items/{item_id}/annotations",
    response_model=AnnotationOut,
    status_code=201,
    operation_id="annotations_create",
)
async def create_annotation(
    item_id: str, body: AnnotationCreate, user: CurrentUser, db: DbDep
) -> AnnotationOut:
    await _get_owned_item(db, user.id, item_id)

    # highlight は color 必須(§8.2)。
    if body.kind == "highlight" and body.color is None:
        raise ProblemException("validation_error", detail="highlight には color が必須です")

    # anchor のブロック実在検証(§8.2。不存在は 422)。
    revision_id = body.anchor.revision_id
    cache: dict[str, _RevIndex] = {}
    index = await _load_rev_index(db, revision_id, cache)
    if not index.block_or_section_exists(body.anchor.block_id):
        raise ProblemException("validation_error", detail="anchor のブロックが存在しません")

    anchor_json = body.anchor.model_dump(mode="json")

    # API kind → DB kind への写像(kind_shape CHECK を満たす形)。
    comment = (body.comment or "").strip() or None
    if body.kind == "bookmark":
        db_kind, db_color, db_body = "bookmark", None, None
        # bookmark は start/end/quote を持たない(§8.1)。
        anchor_json["start"] = None
        anchor_json["end"] = None
        anchor_json["quote"] = None
    elif comment is not None:
        db_kind, db_color, db_body = "comment", body.color, comment
    else:
        db_kind, db_color, db_body = "highlight", body.color, None

    # annotations.quote は GENERATED ALWAYS 列(0001 §4.7)。models.py は plain 列に写像する
    # ため ORM の add() は quote を INSERT に含めて失敗する。Core insert で設定列のみ渡し、
    # quote は DB に計算させる(anchor->>'quote')。
    ann_id = str(uuid.uuid4())
    await db.execute(
        insert(Annotation).values(
            id=ann_id,
            library_item_id=item_id,
            kind=db_kind,
            color=db_color,
            body=db_body,
            anchor=anchor_json,
        )
    )
    await db.commit()
    ann = await db.get(Annotation, ann_id)
    assert ann is not None
    return _to_out(ann, index)


# ============================================================================
# 更新(§8.3)
# ============================================================================
@router.patch(
    "/api/annotations/{annotation_id}",
    response_model=AnnotationOut,
    operation_id="annotations_update",
)
async def patch_annotation(
    annotation_id: str, body: AnnotationPatch, user: CurrentUser, db: DbDep
) -> AnnotationOut:
    ann = await _get_owned_annotation(db, user.id, annotation_id)
    provided = body.model_fields_set

    # bookmark は color/comment を持たない(§8.1)。当該フィールドは無視する。
    if ann.kind != "bookmark":
        if "color" in provided and body.color is not None:
            ann.color = body.color
        if "comment" in provided:
            new_comment = (body.comment or "").strip() or None
            if new_comment is None:
                # コメント解除 → 純ハイライトに戻す(comment→highlight)。
                ann.body = None
                ann.kind = "highlight"
            else:
                ann.body = new_comment
                ann.kind = "comment"

    # annotations には updated_at トリガが無いため明示更新する(0001)。
    ann.updated_at = dt.datetime.now(dt.UTC)
    await db.commit()
    await db.refresh(ann)
    return await _out_single(db, ann)


# ============================================================================
# 削除(§8.4)
# ============================================================================
@router.delete(
    "/api/annotations/{annotation_id}",
    status_code=204,
    operation_id="annotations_delete",
)
async def delete_annotation(annotation_id: str, user: CurrentUser, db: DbDep) -> Response:
    ann = await _get_owned_annotation(db, user.id, annotation_id)
    await db.delete(ann)
    await db.commit()
    return Response(status_code=204)
