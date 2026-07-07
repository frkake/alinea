"""library-items エンドポイントの DTO(plans/03 §5・§1.7)。

- 主要型(``LibraryItemSummary`` / ``PaperBib`` / ``LastPosition`` 等)は plans/03 §1.7 準拠。
- ID は既存実装(jobs ルータ)に合わせ **生 UUID 文字列**で返す(``li_``/``pap_`` 接頭辞は
  付けない。DB は UUID PK・``db.get`` で直参照するため実装全体で一貫させる)。
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from yakudoku_api.schemas.common import (
    LibraryItemSummary,
    PaperBib,
)

_STATUS_LITERAL = Literal["planned", "up_next", "reading", "done", "reread", "on_hold"]
_SORT_KEY_LITERAL = Literal[
    "updated_at", "added_at", "title", "deadline", "reading_time", "comprehension", "priority"
]


class TagCount(BaseModel):
    tag: str
    count: int


class CollectionFacet(BaseModel):
    id: str
    name: str
    count: int


class YearFacet(BaseModel):
    year: int
    count: int


class QuickFacet(BaseModel):
    all: int
    unread: int
    in_progress: int
    done: int
    recheck: int


class QualityFacet(BaseModel):
    A: int
    B: int


class FacetsResponse(BaseModel):
    quick: QuickFacet
    status: dict[str, int]
    tags: list[TagCount]
    collections: list[CollectionFacet]
    quality: QualityFacet
    years: list[YearFacet]


class TagsResponse(BaseModel):
    items: list[TagCount]


class LibraryItemPatch(BaseModel):
    """PATCH /api/library-items/{id}(plans/03 §5.4)。全て任意・指定フィールドのみ更新。

    ``model_fields_set`` で「未指定」と「明示 null」を区別する(null は当該属性の解除)。
    不正な列挙値・値域は Pydantic の ValidationError → 422 ``validation_error``。
    """

    status: Literal["planned", "up_next", "reading", "done", "reread", "on_hold"] | None = None
    priority: Literal["high", "mid", "low"] | None = None
    deadline: str | None = None  # "YYYY-MM-DD"
    tags: list[str] | None = None
    one_line_note: str | None = None
    comprehension: int | None = None  # 1-5
    importance: Literal["low", "mid", "high"] | None = None

    @field_validator("deadline")
    @classmethod
    def _v_deadline(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            dt.date.fromisoformat(v)
        except ValueError as exc:
            raise ValueError("日付形式(YYYY-MM-DD)ではありません") from exc
        return v

    @field_validator("comprehension")
    @classmethod
    def _v_comprehension(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= 5):
            raise ValueError("理解度は 1-5")
        return v


class BulkOperationBody(BaseModel):
    """POST /api/library-items/bulk(一括操作バー。plans/03 §5.6)。"""

    ids: list[str] = Field(min_length=1, max_length=100)
    op: Literal["set_status", "add_tags", "add_to_collection"]
    status: _STATUS_LITERAL | None = None  # op=set_status
    tags: list[str] | None = None  # op=add_tags(既存タグに追加)
    collection_id: str | None = None  # op=add_to_collection(末尾に追加)


class BulkOperationResponse(BaseModel):
    updated: int


class SavedFilterConditions(BaseModel):
    """§5.14・plans/11 §8.3 の ``SavedFilterConditions``(API クエリ語彙と 1:1)。"""

    quick: Literal["all", "unread", "in_progress", "done", "recheck"] | None = None
    status: list[_STATUS_LITERAL] | None = None
    tags: list[str] | None = None
    collection_id: str | None = None
    quality: Literal["A", "B"] | None = None
    years: list[int] | None = None


class SavedFilterSort(BaseModel):
    key: _SORT_KEY_LITERAL
    order: Literal["asc", "desc"]


class SavedFilterBody(BaseModel):
    """POST/PATCH /api/saved-filters の共通リクエスト(§5.14。両者とも全項目送信)。"""

    name: str
    conditions: SavedFilterConditions
    sort: SavedFilterSort

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("フィルタ名を入力してください")
        return trimmed


class SavedFilterOut(BaseModel):
    id: str
    name: str
    conditions: SavedFilterConditions
    sort: SavedFilterSort
    count: int  # クエリ実行時の導出値(保存しない。§5.14)


class SavedFiltersListResponse(BaseModel):
    items: list[SavedFilterOut]


class DuplicateResolutionBody(BaseModel):
    action: str  # "merge" | "dismiss"
    other_paper_id: str | None = None  # merge 時必須


class DuplicateResolutionResponse(BaseModel):
    library_item: LibraryItemSummary


# --- 純関数ヘルパ(書誌の派生表記) ---------------------------------------------


def author_names(authors: list[Any] | None) -> list[str]:
    """authors JSON(``[{name, affiliation}]`` または文字列配列)→ 名前の配列。"""
    names: list[str] = []
    for a in authors or []:
        if isinstance(a, dict):
            name = a.get("name")
            if name:
                names.append(str(name))
        elif a:
            names.append(str(a))
    return names


def authors_short(names: list[str]) -> str:
    """先頭 3 名の姓(最終トークン)を ", " で連結。4 名以上は " et al." を付す(§1.7 例)。"""
    families = [n.split()[-1] for n in names[:3] if n.split()]
    if not families:
        return ""
    short = ", ".join(families)
    if len(names) > 3:
        short += " et al."
    return short


def build_paper_bib(paper: Any) -> PaperBib:
    names = author_names(paper.authors)
    return PaperBib(
        id=str(paper.id),
        title=paper.title,
        authors=names,
        authors_short=authors_short(names),
        venue=paper.venue,
        year=paper.published_on.year if paper.published_on else None,
        arxiv_id=paper.arxiv_id,
        arxiv_version=paper.latest_version,
        doi=paper.doi,
        license=paper.license,
        visibility=paper.visibility,
        abstract=paper.abstract,
    )
