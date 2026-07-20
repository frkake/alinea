"""resources エンドポイントの DTO(plans/03 §12・docs/12・plans/09-screens/5a)。

``ResourceLink.meta`` は種類別の形状を持つ JSON(§12.1)。pydantic では素の ``dict`` として
やり取りし、形状の妥当性はルータ側(`_gather_metadata` 系)で保証する。

Task 18: Hugging Face 由来の関連ソース候補を扱うため、``kind`` に ``huggingface`` / ``project``
を追加し、``ResourceListResponse`` は単数 ``suggestion``(互換)に加えて複数 ``suggestions`` を返す。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ResKind = Literal["github", "youtube", "slides", "article", "huggingface", "project"]


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
    """関連ソース候補(docs/12 §5・設計 §3)。件数バッジには数えない。

    - arXiv 公式実装の動的候補(``papers.official_repo_url`` 由来)は ``resource_id=None``。
    - Hugging Face Paper API 由来の永続候補(``resource_links.status='suggested'``)は
      ``resource_id`` を持ち、ID 指定の accept/dismiss で個別に採用・却下できる。
    """

    url: str
    detected_from: Literal["arxiv_page", "huggingface_paper"] = "arxiv_page"
    resource_id: str | None = None
    kind: ResKind | None = None
    relation: str | None = None
    title: str | None = None
    official_candidate: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


class ResourceListResponse(BaseModel):
    """GET /api/library-items/{id}/resources(plans/03 §12.1)。

    ``suggestions``(複数)が正典。``suggestion``(単数)は互換期間中のみ先頭候補を返す。
    """

    items: list[ResourceLink]
    suggestion: ResourceSuggestion | None = None
    suggestions: list[ResourceSuggestion] = Field(default_factory=list)
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
