"""resources エンドポイントの DTO(plans/03 §12・docs/12・plans/09-screens/5a)。

``ResourceLink.meta`` は種類別の形状を持つ JSON(§12.1)。pydantic では素の ``dict`` として
やり取りし、形状の妥当性はルータ側(`_gather_metadata` 系)で保証する。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ResKind = Literal["github", "youtube", "slides", "article"]


class ResourceLink(BaseModel):
    """plans/03 §12.1 ResourceLink(逐語)。"""

    id: str
    kind: ResKind
    url: str
    official: bool
    title: str
    source_label: str
    thumbnail_url: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    meta_fetched: bool
    note: str | None = None
    created_at: str


class ResourceSuggestion(BaseModel):
    """公式実装の自動検出提案(docs/12 §5)。件数バッジには数えない。"""

    url: str
    detected_from: Literal["arxiv_page"] = "arxiv_page"


class ResourceListResponse(BaseModel):
    """GET /api/library-items/{id}/resources(plans/03 §12.1)。"""

    items: list[ResourceLink]
    suggestion: ResourceSuggestion | None = None
    count: int


class ResourceCreateRequest(BaseModel):
    """POST /api/library-items/{id}/resources(plans/03 §12.2)。"""

    url: str
    note: str | None = None


class ResourcePatchRequest(BaseModel):
    """PATCH /api/resources/{resource_id}(plans/03 §12.3)。

    ``note`` は明示的に ``null`` を送るとメモを消せる。未送信(フィールド省略)は不変。
    区別のため呼び出し側は ``model_fields_set`` を見ること。
    """

    title: str | None = None
    kind: ResKind | None = None
    note: str | None = None


__all__ = [
    "ResKind",
    "ResourceCreateRequest",
    "ResourceLink",
    "ResourceListResponse",
    "ResourcePatchRequest",
    "ResourceSuggestion",
]
