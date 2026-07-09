"""search — 横断検索・論文内検索の DTO(plans/03 §15・§6.7、plans/11 §11 R-2/R-3)。

- ``SearchHit.target`` は plans/03 §15.1 の判別共用体の逐語。R-2 により
  ``kind: "viewer"`` の ``anchor`` は nullable(書誌ヒット= null =「論文の先頭を開く」)。
- ``SearchGroup`` は R-2 で追加された ``hit_count`` / ``article`` を含む。
- ID は既存実装(library.py 等)に合わせ生 UUID 文字列で返す。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from alinea_api.schemas.chat import AnchorRef
from alinea_api.schemas.common import LibraryItemSummary

SearchSource = Literal["body", "note", "annotation", "chat", "article"]
SearchSourceFilter = Literal["all", "body", "notes", "chat", "article"]
SearchSort = Literal["relevance", "recency"]
MatchedInValue = Literal["source", "translation"]


class SearchHitTargetViewer(BaseModel):
    """「該当位置へ →」(plans/11 §7)。書誌ヒットは ``anchor: null``(論文の先頭を開く)。"""

    kind: Literal["viewer"] = "viewer"
    library_item_id: str
    anchor: AnchorRef | None = None


class SearchHitTargetNote(BaseModel):
    """「メモを開く →」。"""

    kind: Literal["note"] = "note"
    library_item_id: str
    note_id: str


class SearchHitTargetChat(BaseModel):
    """「スレッドを開く →」。"""

    kind: Literal["chat"] = "chat"
    library_item_id: str
    thread_id: str
    message_id: str


class SearchHitTargetArticle(BaseModel):
    """「記事モードで開く →」(article 源。M2-15 で実装。型のみ先行定義)。"""

    kind: Literal["article"] = "article"
    library_item_id: str
    article_block_id: str


SearchHitTarget = Annotated[
    SearchHitTargetViewer | SearchHitTargetNote | SearchHitTargetChat | SearchHitTargetArticle,
    Field(discriminator="kind"),
]


class SearchHit(BaseModel):
    """plans/03 §15.1 SearchHit。"""

    source: SearchSource
    matched_in: list[MatchedInValue] | None = None
    display: str
    snippet: str
    snippet_lang: Literal["en", "ja"]
    target: SearchHitTarget


class SearchHitWithPaper(SearchHit):
    """plans/03 §15.2 ``SearchHit_with_paper``(プレビューの各 item)。"""

    library_item: SearchPreviewPaper


class SearchPreviewPaper(BaseModel):
    id: str
    title: str


SearchHitWithPaper.model_rebuild()


class SearchFacetSource(BaseModel):
    all: int
    body: int
    notes: int
    chat: int
    article: int


class SearchFacetPaper(BaseModel):
    library_item_id: str
    title: str
    count: int


class SearchFacets(BaseModel):
    source: SearchFacetSource
    papers: list[SearchFacetPaper]


class SearchGroupArticle(BaseModel):
    """記事ヒットを含む場合のみ(plans/11 R-2)。"""

    article_id: str
    title: str
    generated_at: str


class SearchGroup(BaseModel):
    """論文単位グループ化(plans/03 §15.1 + plans/11 R-2)。"""

    library_item: LibraryItemSummary
    hit_count: int
    article: SearchGroupArticle | None = None
    hits: list[SearchHit]


class SearchResponse(BaseModel):
    """``GET /api/search`` レスポンス(plans/03 §15.1)。"""

    query: str
    total: int
    paper_count: int
    facets: SearchFacets
    groups: list[SearchGroup]
    next_cursor: str | None = None


class SearchPreviewResponse(BaseModel):
    """``GET /api/search/preview`` レスポンス(plans/03 §15.2)。上位3件+total。"""

    total: int
    items: list[SearchHitWithPaper]


class InPaperSearchItem(BaseModel):
    """plans/03 §6.7 論文内検索の 1 件。"""

    block_id: str
    section_id: str
    display: str
    matched_in: list[MatchedInValue]
    snippet: str


class InPaperSearchResponse(BaseModel):
    items: list[InPaperSearchItem]


__all__ = [
    "InPaperSearchItem",
    "InPaperSearchResponse",
    "MatchedInValue",
    "SearchFacetPaper",
    "SearchFacetSource",
    "SearchFacets",
    "SearchGroup",
    "SearchGroupArticle",
    "SearchHit",
    "SearchHitTarget",
    "SearchHitTargetArticle",
    "SearchHitTargetChat",
    "SearchHitTargetNote",
    "SearchHitTargetViewer",
    "SearchHitWithPaper",
    "SearchPreviewPaper",
    "SearchPreviewResponse",
    "SearchResponse",
    "SearchSort",
    "SearchSource",
    "SearchSourceFilter",
]
