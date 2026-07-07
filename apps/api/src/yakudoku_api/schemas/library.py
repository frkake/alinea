"""library-items エンドポイントの DTO(plans/03 §5・§1.7)。

- 主要型(``LibraryItemSummary`` / ``PaperBib`` / ``LastPosition`` 等)は plans/03 §1.7 準拠。
- ID は既存実装(jobs ルータ)に合わせ **生 UUID 文字列**で返す(``li_``/``pap_`` 接頭辞は
  付けない。DB は UUID PK・``db.get`` で直参照するため実装全体で一貫させる)。
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, field_validator

from yakudoku_api.schemas.common import (
    LibraryItemSummary,
    PaperBib,
)


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
