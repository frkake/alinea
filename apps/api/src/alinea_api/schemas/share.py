"""share エンドポイントの DTO(plans/03 §14.1)。

匿名共有ページ ``GET /api/share/collections/{token}`` の応答形。個人資産(進捗・注釈・
リソース・読書統計等)を表すフィールドは一切持たない(docs/09 §4)。
"""

from __future__ import annotations

from pydantic import BaseModel


class ShareCollectionInfo(BaseModel):
    """§14.1 ``collection``。"""

    name: str
    description: str | None = None
    shared_by: str  # 表示名(users.display_name)。クライアント側で「{shared_by} さんが共有」に整形
    updated_at: str
    deadline: str | None = None
    item_count: int


class ShareCollectionItem(BaseModel):
    """§14.1 ``items[]``。書誌+✦要約+許可メモのみ(個人資産を含まない)。"""

    order: int
    title: str
    authors_short: str
    venue_year: str | None = None
    arxiv_url: str | None = None
    summary_3line: list[str] | None = None  # ライセンス縮退(docs/09 §5.2)時は null
    shared_note: str | None = None  # include_notes=true かつ one_line_note 非空のときのみ非 null


class ShareCollectionResponse(BaseModel):
    """§14.1 ``GET /api/share/collections/{token}`` 200。"""

    collection: ShareCollectionInfo
    include_notes: bool
    items: list[ShareCollectionItem]
