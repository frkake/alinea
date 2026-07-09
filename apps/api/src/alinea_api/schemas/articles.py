"""articles スキーマ(plans/03 §19、記事ビュー)。

``ArticleOut.blocks`` の ``content`` は plans/03 §19.1 の逐語ネスト形。DB 保存形(フラット)
からの変換は :mod:`alinea_core.article.wire` に委譲する(worker の ``jobs.result`` と同じ
変換ロジックを再利用し、二重実装を避ける)。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

Preset = Literal["beginner", "implementer", "researcher", "reading_group"]
ArticleBlockType = Literal[
    "heading",
    "paragraph",
    "quote_source",
    "figure_embed",
    "explainer_figure",
    "discussion",
    "attribution",
]


class AnchorRefOut(BaseModel):
    revision_id: str
    block_id: str
    start: int | None = None
    end: int | None = None
    quote: str | None = None
    side: str = "source"
    display: str


class EvidenceItemOut(BaseModel):
    ref: int
    display: str
    anchor: AnchorRefOut


class HeadingContentOut(BaseModel):
    level: int
    text: str


class QuoteContentOut(BaseModel):
    text_en: str
    anchor: AnchorRefOut


class FigureContentOut(BaseModel):
    figure_block_id: str
    image_url: str
    caption_ja: str
    credit: str
    license_badge: str
    # docs/09 §5.2: CC BY-ND はキャプション分離、CC BY-SA は SA 表示(article/wire.py が供給)。
    caption_separated: bool = False
    share_alike: bool = False


class FigureLinkCardOut(BaseModel):
    figure_display: str
    message: str


class ExplainerContentOut(BaseModel):
    figure_id: str
    image_url: str
    caption: str


class DiscussionItemOut(BaseModel):
    text: str
    origin: Literal["ai", "user_highlight"]


class DiscussionContentOut(BaseModel):
    items: list[DiscussionItemOut]


class AttributionContentOut(BaseModel):
    text: str


class ArticleBlockContentOut(BaseModel):
    heading: HeadingContentOut | None = None
    markdown: str | None = None
    quote: QuoteContentOut | None = None
    figure: FigureContentOut | None = None
    figure_link_card: FigureLinkCardOut | None = None
    explainer: ExplainerContentOut | None = None
    discussion: DiscussionContentOut | None = None
    attribution: AttributionContentOut | None = None


class ArticleBlockOut(BaseModel):
    id: str
    type: ArticleBlockType
    content: ArticleBlockContentOut
    evidence: list[EvidenceItemOut]
    origin: Literal["ai", "user_highlight"]
    locked: bool


class ArticleOut(BaseModel):
    id: str
    library_item_id: str
    title: str
    preset: Preset
    include_math: bool
    version: int
    generated_at: str
    disclaimer: str
    # M2-05 の担当(全体概要図)。未生成の間は常に null(§4.5 step8 は本レーンの対象外)。
    overview_figure: dict[str, Any] | None = None
    blocks: list[ArticleBlockOut]


class ArticleGenerateRequest(BaseModel):
    preset: Preset
    include_math: bool | None = None


class ArticleRegenerateRequest(BaseModel):
    instruction: str | None = None
    preset: Preset | None = None
    include_math: bool | None = None


class ArticleBlockRewriteRequest(BaseModel):
    instruction: str | None = None


class ArticleJobResponse(BaseModel):
    job_id: str


class ArticleVersionItemOut(BaseModel):
    version: int
    generated_at: str
    preset: Preset | None = None
    instruction: str | None = None


class ArticleVersionsResponse(BaseModel):
    items: list[ArticleVersionItemOut]


__all__ = [
    "AnchorRefOut",
    "ArticleBlockContentOut",
    "ArticleBlockOut",
    "ArticleBlockRewriteRequest",
    "ArticleBlockType",
    "ArticleGenerateRequest",
    "ArticleJobResponse",
    "ArticleOut",
    "ArticleRegenerateRequest",
    "ArticleVersionItemOut",
    "ArticleVersionsResponse",
    "AttributionContentOut",
    "DiscussionContentOut",
    "DiscussionItemOut",
    "EvidenceItemOut",
    "ExplainerContentOut",
    "FigureContentOut",
    "FigureLinkCardOut",
    "HeadingContentOut",
    "Preset",
    "QuoteContentOut",
]
