"""collections エンドポイントの DTO(plans/03 §13)。

- ``GET /api/collections``(一覧)・``GET/POST/PATCH/DELETE /api/collections/{id}``・
  entries(§13.2)・share(§13.3)の入出力をまとめる。
- ``CollectionEntryOut.library_item`` は ``LibraryItemSummary``(schemas/common.py。読み出し専用の
  ``library_items._summary_for`` を再利用して組み立てる)をそのまま使う。
- PATCH 系は ``model_fields_set`` で「未指定」と「明示 null」を区別する(library.py の
  ``LibraryItemPatch`` と同方針)。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from yakudoku_api.schemas.common import LibraryItemSummary


class CollectionListItem(BaseModel):
    """§13.1 ``GET /api/collections`` の items 要素。"""

    id: str
    name: str
    deadline: str | None = None
    days_left: int | None = None
    item_count: int
    done_count: int


class CollectionListResponse(BaseModel):
    items: list[CollectionListItem]


class CollectionCreateBody(BaseModel):
    """§13.1 ``POST /api/collections``。"""

    name: str = Field(min_length=1)
    description: str | None = None
    deadline: str | None = None  # "YYYY-MM-DD"


class CollectionPatchBody(BaseModel):
    """§13.1 ``PATCH /api/collections/{id}``。全て任意・指定フィールドのみ更新。"""

    name: str | None = None
    description: str | None = None
    deadline: str | None = None  # "YYYY-MM-DD" | null


class ShareInfo(BaseModel):
    """§13.1 詳細レスポンスの ``share`` / §13.3 の応答。"""

    status: str  # "none" | "active" | "revoked"
    token: str | None = None
    url: str | None = None
    include_notes: bool
    included_note_count: int


class CollectionProgress(BaseModel):
    done: int
    total: int


class CollectionEntryOut(BaseModel):
    """§13.1 ``CollectionEntry``。"""

    id: str
    order: int
    library_item: LibraryItemSummary
    assignee: str | None = None
    assignee_is_self: bool
    presentation_minutes: int | None = None
    note: str | None = None


class CollectionDetailResponse(BaseModel):
    """§13.1 ``GET /api/collections/{id}``。"""

    id: str
    name: str
    description: str | None = None
    deadline: str | None = None
    days_left: int | None = None
    progress: CollectionProgress
    share: ShareInfo
    entries: list[CollectionEntryOut]


class EntryCreateBody(BaseModel):
    """§13.2 ``POST /api/collections/{id}/entries``。"""

    library_item_id: str


class EntryPatchBody(BaseModel):
    """§13.2 ``PATCH /api/collection-entries/{id}``。全て任意・指定フィールドのみ更新。"""

    assignee: str | None = None
    assignee_is_self: bool | None = None
    presentation_minutes: int | None = Field(default=None, ge=1, le=999)
    note: str | None = None


class EntriesOrderBody(BaseModel):
    """§13.2 ``PUT /api/collections/{id}/entries/order``。"""

    entry_ids: list[str]


class OkResponse(BaseModel):
    ok: bool = True


class SharePatchBody(BaseModel):
    """§13.3 ``PATCH /api/collections/{id}/share``。"""

    include_notes: bool
