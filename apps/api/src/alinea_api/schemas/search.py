"""search — 横断検索・論文内検索の DTO(plans/03 §15・§6.7、plans/11 §11 R-2/R-3)。

- ``SearchHit.target`` は plans/03 §15.1 の判別共用体の逐語。R-2 により
  ``kind: "viewer"`` の ``anchor`` は nullable(書誌ヒット= null =「論文の先頭を開く」)。
- ``SearchGroup`` は R-2 で追加された ``hit_count`` / ``article`` を含む。
- ID は既存実装(library.py 等)に合わせ生 UUID 文字列で返す。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_serializer
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema
from pydantic_core.core_schema import SerializerFunctionWrapHandler

from alinea_api.schemas.chat import AnchorRef
from alinea_api.schemas.common import LibraryItemSummary

SearchSource = Literal["body", "note", "annotation", "chat", "article"]
SearchSourceFilter = Literal["all", "body", "notes", "chat", "article"]
SearchSort = Literal["relevance", "recency"]
MatchedInValue = Literal["source", "translation"]
# 一致種別(S12 セマンティック検索。docs/10 §5)。lexical=全文のみ / semantic=意味のみ /
# both=両方。フラグ off のときは常に None(= JSON に一切現れない。§4 flag-off byte-identical)。
MatchType = Literal["lexical", "semantic", "both"]


class _DropNullMatchType(BaseModel):
    """``match_type is None`` のとき JSON からキーごと落とすミックスイン(§4 byte-identical)。

    フラグ off・lexical のみ・縮退時は ``match_type=None`` になり、その場合は今日と完全一致の
    レスポンスを返す(キーが一切現れない)。``model_serializer(mode="wrap")`` は OpenAPI の
    プロパティを消してしまうため、``__get_pydantic_json_schema__`` で serialization 側の
    上書きを外した素の core schema からスキーマを復元し、SDK 型は ``match_type?`` を保つ。
    """

    match_type: MatchType | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("match_type") is None:
            data.pop("match_type", None)
        return data

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: CoreSchema, handler: Any) -> JsonSchemaValue:
        # serialization 側の上書き(match_type を落とす wrap serializer)を外した素の core schema
        # から JSON スキーマを起こす。これで OpenAPI/SDK 型は全プロパティを保つ。
        schema_without_ser = dict(core_schema)
        schema_without_ser.pop("serialization", None)
        result: JsonSchemaValue = handler(schema_without_ser)
        return result


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


class SearchGroup(_DropNullMatchType):
    """論文単位グループ化(plans/03 §15.1 + plans/11 R-2)。

    ``match_type``(``_DropNullMatchType`` 由来)は S12 セマンティック検索の一致種別(全文=
    lexical / 意味=semantic / 両方=both)。フラグ off のときは常に ``None`` で、その場合は JSON
    に **一切現れない**(flag-off byte-identical の保証。§4)。
    """

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


class SimilarPaper(BaseModel):
    """「似た論文」の 1 件(S12 セマンティック検索。docs/10 §5・spec §6.3)。

    ``similarity`` は 0〜1 のコサイン類似度(``1 - cosine_distance``)。自分のライブラリ内の
    論文だけを対象にし、``library_item_id`` で情報パネルからリンクする。
    """

    library_item_id: str
    title: str
    authors: list[str]
    similarity: float


class SimilarPapersResponse(BaseModel):
    """``GET /api/library-items/{id}/similar`` レスポンス(spec §6.3)。

    ``indexing`` は対象論文の埋め込みが未生成のとき ``true``(空配列を返す)。フラグ off や
    埋め込み未整備でも 200 で空配列を返し、検索導線を壊さない(P3)。
    """

    items: list[SimilarPaper]
    indexing: bool = False


__all__ = [
    "InPaperSearchItem",
    "InPaperSearchResponse",
    "MatchType",
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
    "SimilarPaper",
    "SimilarPapersResponse",
]
